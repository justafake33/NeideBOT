import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from gmgn_client import GmgnClient, RateLimitError, get_sol_usd
import db
from scheduler import HeapScheduler
from telegram_bot import send_alert

logger = logging.getLogger(__name__)

# ── Fase 1 V2 — Micro absorption helpers ────────────────────────────────────
_MICRO_FLOW_EPS = 0.000001


def _build_window_features(window_data: dict, complete: bool = True) -> dict:
    """Deriva métricas padronizadas de get_absorption_window() para micro snapshots.

    complete=False → janela ainda não fechou (ex: rise_5s com bottom_age_sec < 5).
    Retorna todos os campos NULL e zero_type='incomplete' para não poluir análises.

    Apenas computação — sem efeitos colaterais, sem chamadas externas.
    Retorna dict com: buy, sell, net, events, largest_buy, largest_sell,
    buy_concentration, sell_concentration, zero_type.
    """
    if not complete:
        return {
            "buy": None, "sell": None, "net": None, "events": None,
            "largest_buy": None, "largest_sell": None,
            "buy_concentration": None, "sell_concentration": None,
            "zero_type": "incomplete",
        }
    if not window_data:
        return {
            "buy": None, "sell": None, "net": None, "events": None,
            "largest_buy": None, "largest_sell": None,
            "buy_concentration": None, "sell_concentration": None,
            "zero_type": "missing",
        }
    buy      = window_data.get("buy_vol_sol",  0) or 0
    sell     = window_data.get("sell_vol_sol", 0) or 0
    events   = window_data.get("total_events", 0) or 0
    lrg_buy  = window_data.get("largest_buy",  0) or 0
    lrg_sell = window_data.get("largest_sell", 0) or 0
    net      = buy - sell

    if events == 0:
        zero_type = "zero_no_events"
    elif abs(net) < _MICRO_FLOW_EPS:
        zero_type = "zero_balanced_flow"
    elif net > _MICRO_FLOW_EPS:
        zero_type = "positive"
    else:
        zero_type = "negative"

    return {
        "buy":               buy,
        "sell":              sell,
        "net":               round(net, 6),
        "events":            events,
        "largest_buy":       lrg_buy,
        "largest_sell":      lrg_sell,
        "buy_concentration":  round(lrg_buy  / buy,  4) if buy  > 0 else None,
        "sell_concentration": round(lrg_sell / sell, 4) if sell > 0 else None,
        "zero_type":         zero_type,
    }


POLL_INTERVAL      = int(os.getenv("POLL_INTERVAL_SEC",    "5"))
HOT_INTERVAL       = 30
HOT_PRECISE_SEC    = int(os.getenv("HOT_PRECISE_SEC",      "5"))    # alinhado com trenches poll — mc_min sempre fresco na detecção
HOT_WINDOW_MIN     = 60

# ------------------------------------------------------------------
# Filtros (configuráveis via env vars no Railway sem redeploy)
# Defaults baseados em análise de 1134 tokens (recovery rate por faixa)
# ------------------------------------------------------------------
F_RUG_MAX        = float(os.getenv("FILTER_RUG_MAX",        "0.50"))  # score interno GMGN (raramente >0.5)
F_BUNDLER_MAX    = float(os.getenv("FILTER_BUNDLER_MAX",    "0.60"))  # dados: >60% recovery cai para 14%
F_BOT_MAX        = float(os.getenv("FILTER_BOT_MAX",        "0.80"))  # dados: sweet spot 30-60%, só bloquear extremo
F_SNIPER_MAX     = float(os.getenv("FILTER_SNIPER_MAX",     "0.50"))  # 50% default — não validado com dados limpos; 10% anterior bloqueava quase tudo
F_RAT_MAX        = float(os.getenv("FILTER_RAT_MAX",        "0.20"))
F_TOP10_MAX      = float(os.getenv("FILTER_TOP10_MAX",      "0.80"))  # volta ao original: 0.50 era análise em recovery corrompido
F_SMART_MIN      = int(os.getenv(  "FILTER_SMART_MIN",      "2"))     # live: smart=1 → 0% WR (n=6). smart=2 → 20%, smart=3 → 57%
F_WASH_BLOCK     = os.getenv(      "FILTER_WASH_BLOCK",     "true").lower() == "true"
BC_MIN_WATCHLIST = float(os.getenv("BC_MIN_WATCHLIST",      "80"))
F_MC_MIN         = float(os.getenv("FILTER_MC_MIN",         "5000"))
F_MC_MAX         = float(os.getenv("FILTER_MC_MAX",         "200000"))
F_LIQ_MIN        = float(os.getenv("FILTER_LIQ_MIN",        "10000"))
F_FEE_MIN        = float(os.getenv("FILTER_FEE_MIN",        "1.0"))
MAX_MIG_AGE_SEC  = int(os.getenv(  "MAX_MIG_AGE_SEC",       "600"))
MIGRATION_SOL_UNITS = float(os.getenv("MIGRATION_SOL_UNITS", "410"))
# H22: só tokens no pump.fun pAMM têm dados de absorção via Helius.
# Raydium/Meteora/outros nunca terão absorption_events > 0.
F_PUMP_ONLY      = os.getenv("FILTER_PUMP_ONLY", "true").lower() == "true"


def _passes_filter(token: dict) -> tuple[bool, str]:
    if F_WASH_BLOCK and token.get("is_wash_trading"):
        return False, "wash_trading"

    rug = _float(token.get("rug_ratio"))
    if rug is not None and rug > F_RUG_MAX:
        return False, f"rug_ratio={rug:.0%}>{F_RUG_MAX:.0%}"

    bundler = _float(token.get("bundler_trader_amount_rate"))
    if bundler is not None and bundler > F_BUNDLER_MAX:
        return False, f"bundler={bundler:.0%}>{F_BUNDLER_MAX:.0%}"

    bot = _float(token.get("bot_degen_rate"))
    if bot is not None and bot > F_BOT_MAX:
        return False, f"bot_degen={bot:.0%}>{F_BOT_MAX:.0%}"

    sniper = _float(token.get("top70_sniper_hold_rate"))
    if sniper is not None and sniper > F_SNIPER_MAX:
        return False, f"sniper={sniper:.0%}>{F_SNIPER_MAX:.0%}"

    rat = _float(token.get("rat_trader_amount_rate"))
    if rat is not None and rat > F_RAT_MAX:
        return False, f"rat_trader={rat:.0%}>{F_RAT_MAX:.0%}"

    top10 = _float(token.get("top_10_holder_rate"))
    if top10 is not None and top10 > F_TOP10_MAX:
        return False, f"top10={top10:.0%}>{F_TOP10_MAX:.0%}"

    smart = _int(token.get("smart_degen_count")) or 0
    if smart < F_SMART_MIN:
        return False, f"smart={smart}<{F_SMART_MIN}"

    return True, ""


# ==============================================================================
# HotMonitor — tracking de fundo pós-migração
# ==============================================================================

class HotMonitor:
    def __init__(self, gmgn: GmgnClient, scheduler: HeapScheduler, executor=None):
        self.gmgn = gmgn
        self.scheduler = scheduler
        self._executor = executor
        self._tokens: dict = {}
        self._cancelled: set = set()
        self._mc_live: dict = {}  # mint → último MC do trenches, atualizado a cada poll
        self._lock = threading.Lock()
        self._tick_buffer: list = []
        self._tick_lock = threading.Lock()

        # Helius WebSocket — preço real-time (erpc global ou Helius por mint)
        self._helius = None
        if os.getenv("ERPC_API_KEY") or os.getenv("HELIUS_API_KEY"):
            try:
                from helius_ws import HeliusWS
                self._helius = HeliusWS(on_price=self.push_mc)
            except Exception as e:
                logger.warning("HeliusWS não iniciado: %s", e)

    def add(self, mint: str, mc_migration: float, ts_migration: datetime,
            symbol: str, total_supply: float = 1_000_000_000, exchange: str = None,
            time_to_migrate_sec: int = None, trending_at_migration: bool = None):
        added = False
        with self._lock:
            if mint not in self._tokens and mint not in self._cancelled:
                self._tokens[mint] = {
                    "mc_migration":           mc_migration,
                    "ts_mig_unix":            int(ts_migration.timestamp()),
                    "ts_migration":           ts_migration,
                    "symbol":                 symbol,
                    "exchange":               exchange,
                    "mc_min":                 mc_migration,
                    "time_min":               0.0,
                    "ts_min":                 ts_migration,
                    "mc_max":                 mc_migration,
                    "time_max":               0.0,
                    "mc_last":                mc_migration,
                    "total_supply":           total_supply,
                    "time_to_migrate_sec":    time_to_migrate_sec,
                    "trending_at_migration":  trending_at_migration,
                }
                added = True

        # Helius: subscrição imediata por mint — sem precisar do pool address
        if added and self._helius and mint not in self._cancelled:
            try:
                self._helius.subscribe(mint, total_supply)
            except Exception as e:
                logger.debug("helius.subscribe %s: %s", mint[:8], e)

        # erpc per-mint (transactionSubscribe filtrado pelo mint) é a fonte de mc_min
        # em tempo real — uma subscrição por token ativo, sem firehose. _precise_bottom_single
        # removido: kline não escreve mc_min. (ERPC_GLOBAL=1 volta ao firehose global legado.)

    def cancel(self, mint: str):
        with self._lock:
            self._cancelled.add(mint)
            self._tokens.pop(mint, None)
        if self._helius:
            self._helius.unsubscribe(mint)

    def push_mc(self, mint: str, mc: float, token_data: dict = None,
                event_ts: datetime = None):
        """
        Chamado pelo Scanner a cada poll, pelo HeliusWS (erpc_live) e pelo replay
        de learning window (erpc_replay).

        event_ts: timestamp real do evento on-chain. Quando fornecido, ts_min e
        elapsed_min refletem o momento do evento, não o de processamento.
        Usado pelo replay para que A.6 (freshness) calcule secs_since_bottom
        corretamente mesmo quando a pool demorou para ser confirmada.
        """
        with self._lock:
            self._mc_live[mint] = mc  # captura sempre, mesmo após graduação
            state = self._tokens.get(mint)
            if not state or mint in self._cancelled:
                return

        ref_ts = event_ts or datetime.now(timezone.utc)
        elapsed_sec = int((ref_ts - state["ts_migration"]).total_seconds())
        elapsed_min = elapsed_sec / 60

        state["mc_last"] = mc

        # Acumula tick no buffer para batch insert (flush a cada HOT_INTERVAL)
        if token_data and 0 <= elapsed_sec <= 3660:
            tick = {
                "mint":           mint,
                "elapsed_sec":    elapsed_sec,
                "mc":             mc,
                "smart_count":    _int(token_data.get("smart_degen_count")),
                "renowned_count": _int(token_data.get("renowned_count")),
                "volume_1m":      _float(token_data.get("volume_1m")),
                "volume_5m":      _float(token_data.get("volume_5m")),
                "volume_15m":     _float(token_data.get("volume_15m")),
                "holders_count":  _int(token_data.get("holder_count")),
                "liquidity":      _float(token_data.get("liquidity")),
            }
            with self._tick_lock:
                self._tick_buffer.append(tick)

        if mc < state["mc_migration"] * 0.20:
            logger.info("Rug %s: queda >=80%%", state["symbol"])
            self.cancel(mint)
            db.set_recovery_false(mint)
            return

        # erpc (token_data is None, PDA validado) escreve mc_min sem restrição de tempo.
        # A pool pAMM só existe após migração — não há contaminação de bonding curve.
        # kline (_precise_bottom_tick) complementa com candles fechados (elapsed >= 90s).
        # Trenches NÃO escreve mc_min — poll de 5s, não é tempo real.
        if token_data is None and mc < state["mc_min"]:
            mc_old  = state["mc_min"]
            source  = "erpc_replay" if event_ts else "erpc_live"
            state["mc_min"]   = mc
            state["time_min"] = elapsed_min
            state["ts_min"]   = ref_ts   # timestamp do evento real (não do processamento)
            db.update_bottom(mint, mc, elapsed_min)
            logger.info(
                "Fundo(%s) %s: mint=%s mc_old=$%.2f mc_new=$%.2f "
                "event_ts=%.3f elapsed=%.1fmin",
                source, state["symbol"], mint, mc_old, mc, ref_ts.timestamp(), elapsed_min,
            )

        if mc > state["mc_max"]:
            state["mc_max"] = mc
            state["time_max"] = elapsed_min
            db.update_peak(mint, mc, elapsed_min)
            logger.info("Pico(poll)  %s: $%.0f em %.1fmin (+%.0f%% vs mig)",
                        state["symbol"], mc, elapsed_min,
                        (mc / state["mc_migration"] - 1) * 100)

        self._check_bounce(mint, state, mc, elapsed_sec)

        # Fase 1 V2 — micro absorption snapshot (research-only, nunca afeta trading)
        if self._helius and state.get("ts_min") is not None:
            self._maybe_record_micro_snapshot(mint, state, mc, ref_ts)

        # Sell exhaustion — research-only data accumulation (nunca afeta trading)
        if self._helius and state.get("ts_min") is not None:
            self._maybe_record_sell_exhaustion(mint, state, mc, ref_ts)

    # ------------------------------------------------------------------
    # Bounce detector
    # ------------------------------------------------------------------

    BOUNCE_MIN_PCT       = float(os.getenv("BOUNCE_MIN_PCT",         "0.30"))  # A.7: bounce mínimo 30% (live WR 9.1% abaixo disso)
    BOUNCE_MAX_PCT       = float(os.getenv("BOUNCE_MAX_PCT",         "9.99"))  # desativado por padrão — sem validação GMGN para threshold máximo (nota 87 CLAUDE.md)
    BOUNCE_DROP_MIN      = float(os.getenv("BOUNCE_DROP_MIN",        "0.20"))  # queda mínima da migração (sessão 21: drop 10-30% tem rec75 38-46%, melhor quartil)
    BOUNCE_TIME_MAX      = float(os.getenv("BOUNCE_TIME_MAX",        "20.0"))  # A.5: janela 30→20min (100% TPs em ≤20min)
    BOTTOM_FRESHNESS_SEC = int(os.getenv(  "BOTTOM_FRESHNESS_SEC",  "600"))   # A.6: máx 10min desde o último mc_min (sessão 21: 180s era muito restritivo)

    def _check_bounce(self, mint: str, state: dict, mc: float, elapsed_sec: int):
        """Detecta bounce em tempo real. Fase A: filtros A.2, A.3, A.6.

        RACE CONDITION FIX: Atomic bounce_fired check-and-set com lock.
        Dois threads podem chamar _check_bounce simultaneamente. Sem lock,
        ambos leem bounce_fired=False/missing, ambos passam nos filtros,
        ambos setam bounce_fired=True e disparam sinal duplicado.
        """
        # ── Atomic check-and-set com lock ────────────────────────────────────
        # STALE MC_MIN FIX: Snapshot mc_min e ts_min DENTRO do lock.
        # Outro thread pode atualizar state["mc_min"] entre a leitura (fora lock) e o
        # cálculo de bounce_pct, causando inconsistência. Lê uma vez dentro do lock,
        # usa cópia local fora do lock para todos os cálculos.
        with self._lock:
            mint_state = self._tokens.get(mint)
            if mint_state is None:
                return
            if mint_state.get("bounce_fired"):
                return
            # Snapshot: lê dentro do lock, usa cópia local fora do lock
            mc_min       = mint_state["mc_min"]
            mc_migration = mint_state["mc_migration"]
            ts_min       = mint_state.get("ts_min")
            # Não set bounce_fired aqui — apenas checkpoint. Set só após passar todos os filtros.

        elapsed_min  = elapsed_sec / 60

        # ── Boundary checks ─────────────────────────────────────────────────────────
        if elapsed_sec < 0 or elapsed_min > self.BOUNCE_TIME_MAX:
            return

        if mc_min <= 0 or mc_migration <= 0:
            return


        drop_from_mig = (mc_migration - mc_min) / mc_migration
        if drop_from_mig < self.BOUNCE_DROP_MIN:
            return

        bounce_pct = (mc - mc_min) / mc_min
        if bounce_pct < self.BOUNCE_MIN_PCT:
            return
        if self.BOUNCE_MAX_PCT < 9.99 and bounce_pct > self.BOUNCE_MAX_PCT:
            logger.debug("BOUNCE skip %s — bounce tardio=%.1f%% (max=%.0f%%)",
                         mint[:8], bounce_pct * 100, self.BOUNCE_MAX_PCT * 100)
            return

        # Anti-decline: rejeita se o preço está caindo em relação a um pico REAL pós-migração.
        # Bug original: mc_max é inicializado com mc_migration → a condição mc_max > mc_min
        # era sempre True após o dump, bloqueando qualquer bounce abaixo de 85% da migração.
        # Exemplo: mc_migration=$27k, mc_bottom=$5k → anti-decline exigia mc >= $22.95k,
        # mas com BOUNCE_MAX_PCT=100% o token é bloqueado antes de chegar lá.
        # Fix: só aplica se mc_max superou 5% acima da migração (pico real pós-migração).
        mc_max = mint_state["mc_max"]
        if mc_max > mc_migration * 1.05 and mc < mc_max * 0.85:
            logger.debug("BOUNCE skip %s — declining: $%.0f < pico real $%.0f × 85%%",
                         mint[:8], mc, mc_max)
            return

        # A.6 — Bottom freshness: sinal deve vir logo após o fundo ser confirmado.
        # Padrão inválido: token pica em <3min, dump por 10-20min, sinal dispara em mini-rally tardio.
        # Dados: 92% dos inválidos têm gap (ts_min → sinal) > 2min. P50 gap = 6.7min.
        # Válidos: fundo recente → sinal em 1-3min. Invalidos: fundo em 2-5min → sinal 13-17min depois.
        # ts_min já foi snapshot dentro do lock acima.
        if ts_min is not None:
            secs_since_bottom = (datetime.now(timezone.utc) - ts_min).total_seconds()
            if secs_since_bottom > self.BOTTOM_FRESHNESS_SEC:
                logger.debug(
                    "BOUNCE skip %s — A.6 bottom stale %.0fs ago (max=%ds)",
                    mint[:8], secs_since_bottom, self.BOTTOM_FRESHNESS_SEC,
                )
                return

        # A.8 — Preço acima da migração: sinal é artifact de wick intra-candle, não bounce real.
        if mc >= mc_migration:
            logger.debug("BOUNCE skip %s — A.8 mc=$%.0f >= mig=$%.0f", mint[:8], mc, mc_migration)
            return

        # B.3 — Backfill retroativo: insere kline completo desde a migração para cobertura de ticks.
        # Não bloqueia o sinal — A.1 removido (H13 refutada com n=153: slope não discrimina recovery).
        if not state.get("slope_backfill_triggered"):
            state["slope_backfill_triggered"] = True
            threading.Thread(
                target=self._backfill_slope_ticks,
                args=(mint, dict(state)),
                daemon=True,
                name=f"backfill-{mint[:8]}",
            ).start()

        # H22-H26 — Coleta de métricas brutas de absorção (observe-only, NÃO bloqueia o sinal)
        # Fonte: Helius pAMM events (buy/sell) em múltiplas janelas.
        # Score/grade NÃO calculados aqui — fórmula não validada por dados reais.
        # Após n≥30 bounces com outcomes (TP/SL), analisar quais métricas brutas
        # discriminam e SÓ ENTÃO construir score baseado em correlações reais.

        # Três janelas de fluxo — visão completa do bounce:
        # PRÉ  [ts_min-60s → ts_min]   : absorção do dump (sellers exaustos?) — H22
        # RISE [ts_min    → ts_signal] : quem comprou durante a subida? — H26
        # PÓS  [ts_signal → ts_signal+60s]: coleta assíncrona (+60s via scheduler)
        now_ts     = datetime.now(timezone.utc).timestamp()
        # Usa o snapshot de ts_min tirado dentro do lock no início da função
        # (não state.get("ts_min") que pode ser cópia stale em _precise_bottom_tick).
        ts_min_obj = ts_min
        ts_min_ts  = ts_min_obj.timestamp() if ts_min_obj else None

        absorption_window = {}  # alias para PRÉ window
        pre_window  = {}  # absorção do dump
        rise_window = {}  # momentum da subida
        if self._helius:
            try:
                # PRÉ: [ts_min-60s → ts_min]
                pre_window = self._helius.get_absorption_window(
                    mint, window_sec=60, window_end_ts=ts_min_ts
                )
                # RISE: [ts_min → ts_signal]
                if ts_min_ts:
                    rise_sec = max(1, int(now_ts - ts_min_ts))
                    rise_window = self._helius.get_absorption_window(
                        mint, window_sec=rise_sec, window_end_ts=now_ts
                    )

                pre_ev  = pre_window.get("total_events",  0) or 0
                rise_ev = rise_window.get("total_events", 0) or 0
                pre_bv  = pre_window.get("buy_vol_sol",   0) or 0
                pre_sv  = pre_window.get("sell_vol_sol",  0) or 0
                rise_bv = rise_window.get("buy_vol_sol",  0) or 0
                rise_sv = rise_window.get("sell_vol_sol", 0) or 0
                logger.info(
                    "FLOW %s | PRÉ: %dev buy=%.2f sell=%.2f net=%+.2f"
                    " | RISE: %dev buy=%.2f sell=%.2f net=%+.2f",
                    state.get("symbol", mint[:8]),
                    pre_ev,  pre_bv,  pre_sv,  pre_bv  - pre_sv,
                    rise_ev, rise_bv, rise_sv, rise_bv - rise_sv,
                )
            except Exception as e:
                logger.debug("flow_windows %s: %s", mint[:8], e)

        # alias para compatibilidade com código de insert abaixo
        absorption_window = pre_window

        # H24 — concentração de sellers durante o dump [ts_migration → ts_min]
        # H25 — profundidade do pool no momento do sinal
        dump_concentration   = {}
        pool_quote_at_signal = None

        if self._helius:
            try:
                ts_mig_ts = state["ts_migration"].timestamp()
                dump_concentration = self._helius.get_dump_seller_concentration(
                    mint,
                    window_start_ts=ts_mig_ts,
                    window_end_ts=ts_min_ts or now_ts,
                )
                pool_quote_at_signal = self._helius.get_pool_quote(mint)
                logger.info(
                    "H24/H25 %s | sellers=%d HHI=%.3f top=%.1f%% | pool=%.1f SOL",
                    state.get("symbol", mint[:8]),
                    dump_concentration.get("seller_count", 0),
                    dump_concentration.get("seller_hhi") or 0,
                    (dump_concentration.get("top_seller_frac") or 0) * 100,
                    pool_quote_at_signal or 0,
                )
            except Exception as e:
                logger.debug("H24/H25 %s: %s", mint[:8], e)

        # H26 — concentração de buyers na RISE window (largest_buy / total_buy_vol)
        buy_concentration = None
        rise_bv = rise_window.get("buy_vol_sol") or 0
        rise_lb = rise_window.get("largest_buy") or 0
        if rise_bv > 0:
            buy_concentration = round(rise_lb / rise_bv, 4)

        # Piso anti-fake para pesquisa: pools extremamente rasas durante migração
        # poluem o dataset. Este piso só descarta fake-pools óbvias; o piso
        # rígido de COMPRA REAL fica no TradeExecutor via TRADE_MIN_POOL_SOL.
        bounce_record_min_pool = float(os.getenv("BOUNCE_RECORD_MIN_POOL_SOL", "5"))
        if bounce_record_min_pool > 0 and self._helius is not None and pool_quote_at_signal is not None:
            if pool_quote_at_signal < bounce_record_min_pool:
                logger.info(
                    "BOUNCE skip %s — fake/raso pool=%.1f SOL < record_min=%.0f SOL",
                    state.get("symbol", mint[:8]), pool_quote_at_signal, bounce_record_min_pool,
                )
                return

        net_sol_flow = (
            (absorption_window.get("buy_vol_sol") or 0) -
            (absorption_window.get("sell_vol_sol") or 0)
        ) if absorption_window.get("total_events") else None

        # H23 gate — bloqueia sinal se sellers dominam o fundo (net_sol_flow < H23_NET_MIN).
        # Se net_sol_flow is None (sem dados Helius), passa: melhor não bloquear do que perder.
        # Dado: negativo (< 0) → WR 20.2% (abaixo break-even 25%). Positivo → WR 67% (n=9).
        _h23_net_min = os.getenv("H23_NET_MIN")
        if _h23_net_min is not None and net_sol_flow is not None:
            _h23_threshold = float(_h23_net_min)
            if net_sol_flow < _h23_threshold:
                logger.info(
                    "BOUNCE skip %s — H23 gate: net_flow=%+.2f SOL < min=%.1f",
                    state.get("symbol", mint[:8]), net_sol_flow, _h23_threshold,
                )
                return

        rise_net_flow = (
            (rise_window.get("buy_vol_sol") or 0) -
            (rise_window.get("sell_vol_sol") or 0)
        ) if rise_window.get("total_events") else None

        # RISE gate — bloqueia sinal se o bounce foi criado com sellers dominando.
        # Dados (n=61 tokens, token_ticks outcome): RISE < 0 → rec75=9%, sl25=91%.
        # RISE ≥ 0 → rec75=22%, sl25=84%. Delta -13pp rec75, +7pp sl25.
        # Se rise_net_flow is None (sem dados Helius), passa: não bloquear sem dado.
        # Env var RISE_NET_MIN: default 0 (bloqueia qualquer negativo). -999 = desativado.
        _rise_net_min_str = os.getenv("RISE_NET_MIN")
        if _rise_net_min_str is not None and rise_net_flow is not None:
            _rise_threshold = float(_rise_net_min_str)
            if rise_net_flow < _rise_threshold:
                logger.info(
                    "BOUNCE skip %s — RISE gate: rise_net=%+.2f SOL < min=%.1f",
                    state.get("symbol", mint[:8]), rise_net_flow, _rise_threshold,
                )
                return

        helius_context = {
            "pool_quote_at_signal": pool_quote_at_signal,
            "absorption_events": absorption_window.get("total_events"),
            "net_sol_flow": net_sol_flow,
            "rise_events": rise_window.get("total_events"),
            "rise_net_flow": rise_net_flow,
            "dump_seller_count": dump_concentration.get("seller_count"),
            "dump_seller_hhi": dump_concentration.get("seller_hhi"),
            "dump_top_seller_frac": dump_concentration.get("top_seller_frac"),
            "buy_concentration": buy_concentration,
            "h23_window": "bottom",
        }

        # ── Atomic bounce_fired set: todos os filtros passaram, agora commita o sinal ────
        # Usar lock novamente para garantir que nenhuma outra thread setou bounce_fired
        # entre o snapshot inicial e aqui (improvável mas possível com timing muito específico).
        # Bug 3 fix: check-and-set atômico sob lock — evita double-fire por corrida
        # entre push_mc (trenches) e _precise_bottom_tick (kline) em threads concorrentes.
        with self._lock:
            mint_state = self._tokens.get(mint)
            if mint_state and not mint_state.get("bounce_fired"):
                mint_state["bounce_fired"] = True
            else:
                # Outra thread setou bounce_fired enquanto a gente passava nos filtros.
                # Cancela o sinal para evitar duplicata.
                logger.debug("BOUNCE %s: duplicate signal canceled (bounce_fired já setado por outra thread)", mint[:8])
                return

        symbol = mint_state.get("symbol", mint[:8])
        time_to_migrate_sec = mint_state.get("time_to_migrate_sec")
        # trending_at_bounce = valor coletado no momento da migração (pump.fun 5m trending)
        trending_at_bounce = mint_state.get("trending_at_migration")

        try:
            db.insert_bounce_signal({
                "mint":                mint,
                "symbol":              symbol,
                "exchange":            state.get("exchange"),
                "elapsed_sec":         elapsed_sec,
                "mc_migration":        mc_migration,
                "mc_min":              mc_min,
                "mc_at_signal":        mc,
                "drop_from_mig":       round(drop_from_mig, 4),
                "bounce_pct":          round(bounce_pct, 4),
                "absorption_score":    None,
                "absorption_grade":    None,
                "absorption_buy_vol":  absorption_window.get("buy_vol_sol"),
                "absorption_sell_vol": absorption_window.get("sell_vol_sol"),
                "absorption_buy_cnt":  absorption_window.get("buy_count"),
                "absorption_sell_cnt": absorption_window.get("sell_count"),
                "absorption_lrg_buy":  absorption_window.get("largest_buy"),
                "absorption_events":   absorption_window.get("total_events"),
                "net_sol_flow":        net_sol_flow,
                "h23_window":          "bottom",
                # Janela RISE: [ts_min → ts_signal]
                "rise_buy_vol":        rise_window.get("buy_vol_sol"),
                "rise_sell_vol":       rise_window.get("sell_vol_sol"),
                "rise_net_flow":       rise_net_flow,
                "rise_events":         rise_window.get("total_events"),
                "rise_lrg_buy":        rise_window.get("largest_buy"),
                # H25 — pool depth
                "pool_quote_at_signal":  pool_quote_at_signal,
                # H24 — seller concentration durante o dump
                "dump_seller_count":     dump_concentration.get("seller_count"),
                "dump_seller_hhi":       dump_concentration.get("seller_hhi"),
                "dump_top_seller_frac":  dump_concentration.get("top_seller_frac"),
                # H26 — buyer concentration na subida
                "buy_concentration":     buy_concentration,
                # H27 — trending no momento do bounce
                "trending_at_bounce":    trending_at_bounce,
                # H28 — velocidade de migração
                "time_to_migrate_sec":   time_to_migrate_sec,
            })
        except Exception as e:
            logger.warning("insert_bounce_signal %s: %s", mint[:8], e)

        # PÓS-sinal: coleta [ts_signal → ts_signal+30s] e [ts_signal → ts_signal+60s]
        # window_end_ts = ts_signal + delay → captura apenas o período pós-sinal
        if self._helius and self.scheduler:
            ts_signal_ts = now_ts

            def _snap30(m=mint, t0=ts_signal_ts):
                try:
                    w = self._helius.get_absorption_window(m, window_sec=30, window_end_ts=t0 + 30)
                    db.update_bounce_snapshots(
                        m, "30s",
                        buy_vol=w.get("buy_vol_sol", 0) or 0,
                        sell_vol=w.get("sell_vol_sol", 0) or 0,
                        events=w.get("total_events", 0) or 0,
                    )
                except Exception as ex:
                    logger.debug("snap30 %s: %s", m[:8], ex)

            def _snap60(m=mint, t0=ts_signal_ts):
                try:
                    w = self._helius.get_absorption_window(m, window_sec=60, window_end_ts=t0 + 60)
                    db.update_bounce_snapshots(
                        m, "60s",
                        buy_vol=w.get("buy_vol_sol", 0) or 0,
                        sell_vol=w.get("sell_vol_sol", 0) or 0,
                        events=w.get("total_events", 0) or 0,
                    )
                except Exception as ex:
                    logger.debug("snap60 %s: %s", m[:8], ex)

            self.scheduler.schedule(30, _snap30)
            self.scheduler.schedule(60, _snap60)

        msg = (
            f"🔔 BOUNCE {symbol}\n"
            f"  Migração: ${mc_migration:,.0f}\n"
            f"  Fundo:    ${mc_min:,.2f} (-{drop_from_mig:.0%})\n"
            f"  Agora:    ${mc:,.0f} (+{bounce_pct:.0%} do fundo)\n"
            f"  Tempo:    {elapsed_min:.1f}min\n"
            f"  Mint: {mint}"
        )
        try:
            send_alert(msg)
        except Exception as e:
            logger.warning("send_alert bounce %s: %s", mint[:8], e)

        logger.info("BOUNCE %s: fundo=$%.2f drop=%.0f%% bounce=%.0f%% em %.1fmin",
                    symbol, mc_min, drop_from_mig * 100, bounce_pct * 100, elapsed_min)

        if self._executor:
            try:
                self._executor.on_bounce(
                    mint=mint,
                    symbol=symbol,
                    mc_bottom=mc_min,
                    mc_migration=mc_migration,
                    elapsed_sec=elapsed_sec,
                    mc_at_signal=mc,
                    bounce_pct=bounce_pct,
                    drop_from_mig=drop_from_mig,
                    config_bounce_min_pct=self.BOUNCE_MIN_PCT,
                    config_bounce_max_pct=self.BOUNCE_MAX_PCT,
                    config_bounce_drop_min=self.BOUNCE_DROP_MIN,
                    helius_context=helius_context,
                )
            except Exception as e:
                logger.error("executor.on_bounce %s: %s", symbol, e)

    # ------------------------------------------------------------------
    # Fase 1 V2 — Micro absorption snapshot (research-only)
    # Nunca altera lógica de compra, _check_bounce, executor ou gmgn_client.
    # ------------------------------------------------------------------

    _MICRO_SNAP_WINDOW_SEC = 60   # grava até 60s após o fundo
    _MICRO_SNAP_RATE_SEC   = 2.0  # no máximo 1 snapshot a cada 2s por token

    def _maybe_record_micro_snapshot(self, mint: str, state: dict,
                                     mc: float, ref_ts: datetime) -> None:
        """Rate-limiter: checa se é hora de gravar um novo snapshot e lança thread."""
        now_ts      = ref_ts.timestamp()
        ts_min      = state.get("ts_min")
        if ts_min is None:
            return
        ts_min_ts   = ts_min.timestamp()
        bottom_age  = now_ts - ts_min_ts

        if bottom_age < 0 or bottom_age > self._MICRO_SNAP_WINDOW_SEC:
            return

        last = state.get("last_micro_snapshot_ts", 0)
        if now_ts - last < self._MICRO_SNAP_RATE_SEC:
            return

        # Atualiza rate limiter imediatamente (evita burst)
        state["last_micro_snapshot_ts"] = now_ts

        # Executa em thread daemon — push_mc não pode bloquear
        threading.Thread(
            target=self._record_micro_absorption_snapshot,
            args=(mint, dict(state), mc, now_ts, ts_min_ts, round(bottom_age, 2)),
            daemon=True,
            name=f"micro-{mint[:6]}",
        ).start()

    def _record_micro_absorption_snapshot(
        self, mint: str, state: dict, mc_current: float,
        now_ts: float, ts_min_ts: float, bottom_age_sec: float,
    ) -> None:
        """Coleta e persiste microdados de absorção ao redor do fundo.

        Research-only: nunca chama executor, nunca altera trading logic.
        Falha silenciosa para nunca quebrar o scanner.
        """
        try:
            ts_migration = state.get("ts_migration")
            ts_mig_ts    = ts_migration.timestamp() if ts_migration else None
            mc_migration = state.get("mc_migration") or 0
            mc_min       = state.get("mc_min") or 0

            elapsed_sec   = int(now_ts - ts_mig_ts) if ts_mig_ts else None
            drop_from_mig = (mc_migration - mc_min) / mc_migration if mc_migration > 0 else None
            bounce_pct_now = (mc_current - mc_min) / mc_min if mc_min > 0 else None

            pool_quote = self._helius.get_pool_quote(mint)

            def _w(sec: int, end_ts: float) -> dict:
                return self._helius.get_absorption_window(
                    mint, window_sec=max(1, sec), window_end_ts=end_ts
                )

            def _wf(sec: int, end_ts: float, complete: bool = True) -> dict:
                return _build_window_features(_w(sec, end_ts), complete=complete)

            # PRE: [ts_min - Xs, ts_min] — sempre completas (fundo já ocorreu)
            pre_5  = _wf(5,  ts_min_ts)
            pre_10 = _wf(10, ts_min_ts)
            pre_15 = _wf(15, ts_min_ts)
            pre_30 = _wf(30, ts_min_ts)

            # RISE: [ts_min, ts_min + Xs] — só preencher quando a janela tiver fechado.
            # Se bottom_age_sec < X, a janela ainda não encerrou → NULL + zero_type='incomplete'.
            rise_1  = _wf(1,  ts_min_ts + 1,  complete=(bottom_age_sec >= 1))
            rise_3  = _wf(3,  ts_min_ts + 3,  complete=(bottom_age_sec >= 3))
            rise_5  = _wf(5,  ts_min_ts + 5,  complete=(bottom_age_sec >= 5))
            rise_10 = _wf(10, ts_min_ts + 10, complete=(bottom_age_sec >= 10))
            # rise_now usa o elapsed real — sempre completo por definição
            rise_now = _wf(max(1, int(now_ts - ts_min_ts)), now_ts)

            # LIVE: [now - Xs, now] — sempre completas
            live_5  = _wf(5,  now_ts)
            live_10 = _wf(10, now_ts)
            live_30 = _wf(30, now_ts)
            live_60 = _wf(60, now_ts)

            def _wp(feat: dict, prefix: str) -> dict:
                return {
                    f"{prefix}_buy":          feat.get("buy"),
                    f"{prefix}_sell":         feat.get("sell"),
                    f"{prefix}_net":          feat.get("net"),
                    f"{prefix}_events":       feat.get("events"),
                    f"{prefix}_largest_buy":  feat.get("largest_buy"),
                    f"{prefix}_largest_sell": feat.get("largest_sell"),
                    f"{prefix}_zero_type":    feat.get("zero_type"),
                }

            pre_net  = pre_30.get("net") or 0
            rise_net = rise_now.get("net") or 0
            rise_ev  = rise_now.get("events") or 0
            pre_ev   = pre_30.get("events") or 0

            flags = {
                "idb_like_live":      pre_net >= 0 and rise_net <= 1,
                "idb2_like_live":     pre_net > 0 and rise_net <= 0,
                "silent_bottom_like": pre_ev == 0 and rise_net <= 1,
                "rise_hot_risk":      rise_net > 3,
                "hhi_missing":        True,   # H24 não calculado no micro path (custoso)
                "data_missing":       pre_ev == 0 and rise_ev == 0,
            }

            data = {
                "mint":           mint,
                "symbol":         state.get("symbol"),
                "ts_snapshot":    datetime.fromtimestamp(now_ts, tz=timezone.utc),
                "ts_migration":   ts_migration,
                "ts_min":         datetime.fromtimestamp(ts_min_ts, tz=timezone.utc),
                "mc_migration":   mc_migration,
                "mc_min":         mc_min,
                "mc_current":     mc_current,
                "elapsed_sec":    elapsed_sec,
                "bottom_age_sec": bottom_age_sec,
                "bounce_pct_now": round(bounce_pct_now, 4) if bounce_pct_now is not None else None,
                "drop_from_mig":  round(drop_from_mig, 4) if drop_from_mig is not None else None,
                "pool_quote":     pool_quote,
                **_wp(pre_5,  "pre_5s"),
                **_wp(pre_10, "pre_10s"),
                **_wp(pre_15, "pre_15s"),
                **_wp(pre_30, "pre_30s"),
                **_wp(rise_1,  "rise_1s"),
                **_wp(rise_3,  "rise_3s"),
                **_wp(rise_5,  "rise_5s"),
                **_wp(rise_10, "rise_10s"),
                **_wp(rise_now, "rise_now"),
                "live_5s_buy":    live_5.get("buy"),
                "live_5s_sell":   live_5.get("sell"),
                "live_5s_net":    live_5.get("net"),
                "live_5s_events": live_5.get("events"),
                "live_10s_buy":    live_10.get("buy"),
                "live_10s_sell":   live_10.get("sell"),
                "live_10s_net":    live_10.get("net"),
                "live_10s_events": live_10.get("events"),
                # LIVE 30s / 60s — janelas mais largas, permitem checar fluxo sustentado
                "live_30s_buy":    live_30.get("buy"),
                "live_30s_sell":   live_30.get("sell"),
                "live_30s_net":    live_30.get("net"),
                "live_30s_events": live_30.get("events"),
                "live_60s_buy":    live_60.get("buy"),
                "live_60s_sell":   live_60.get("sell"),
                "live_60s_net":    live_60.get("net"),
                "live_60s_events": live_60.get("events"),
                # Concentrações legacy (mantidas para retrocompat — mapeiam pre_5s/pre_10s)
                "buy_concentration_5s":   pre_5.get("buy_concentration"),
                "buy_concentration_10s":  pre_10.get("buy_concentration"),
                "sell_concentration_5s":  pre_5.get("sell_concentration"),
                "sell_concentration_10s": pre_10.get("sell_concentration"),
                # Concentrações nomeadas por janela (sem ambiguidade)
                "pre_5s_buy_concentration":   pre_5.get("buy_concentration"),
                "pre_10s_buy_concentration":  pre_10.get("buy_concentration"),
                "rise_5s_buy_concentration":  rise_5.get("buy_concentration"),   # NULL se incompleta
                "rise_10s_buy_concentration": rise_10.get("buy_concentration"),  # NULL se incompleta
                "live_5s_buy_concentration":  live_5.get("buy_concentration"),
                "live_10s_buy_concentration": live_10.get("buy_concentration"),
                "candidate_flags": flags,
            }

            db.insert_micro_absorption_snapshot(data)

        except Exception as e:
            logger.debug("micro_snapshot %s: %s", mint[:8], e)

    # ------------------------------------------------------------------
    # Sell exhaustion — research-only data accumulation
    # Registra candidatos de seller exhaustion para validar se a pausa de sells
    # antes do fundo é preditiva de recovery. Nunca afeta trading.
    # ------------------------------------------------------------------

    _SELL_EXH_RATE_SEC  = 5.0   # grava no máximo 1 candidato a cada 5s por token
    _SELL_EXH_WINDOW_SEC = 120  # grava até 120s após o fundo (fundo pode não ser detectado ainda)

    def _maybe_record_sell_exhaustion(self, mint: str, state: dict,
                                      mc: float, ref_ts: datetime) -> None:
        """Rate-limiter: captura snapshot de sell velocity perto do fundo."""
        ts_min = state.get("ts_min")
        if ts_min is None:
            return

        now_ts      = ref_ts.timestamp()
        ts_min_ts   = ts_min.timestamp()
        bottom_age  = now_ts - ts_min_ts

        # Só grava na janela [0, 120s] após o fundo mais recente
        if bottom_age < 0 or bottom_age > self._SELL_EXH_WINDOW_SEC:
            return

        # Rate limit por token
        last = state.get("last_sell_exh_ts", 0)
        if now_ts - last < self._SELL_EXH_RATE_SEC:
            return
        state["last_sell_exh_ts"] = now_ts

        # Executa em thread daemon para não bloquear push_mc
        threading.Thread(
            target=self._record_sell_exhaustion,
            args=(mint, dict(state), mc, now_ts, ts_min_ts, round(bottom_age, 2)),
            daemon=True,
            name=f"sexh-{mint[:6]}",
        ).start()

    def _record_sell_exhaustion(
        self, mint: str, state: dict, mc_current: float,
        now_ts: float, ts_min_ts: float, bottom_age_sec: float,
    ) -> None:
        """Persiste um snapshot de sell velocity para o mint. Research-only."""
        try:
            vel = self._helius.get_sell_velocity_snapshot(mint, ref_ts=ts_min_ts)

            mc_migration  = state.get("mc_migration") or 0
            mc_min        = state.get("mc_min") or 0
            drop_from_mig = (mc_migration - mc_min) / mc_migration if mc_migration > 0 else None
            ts_migration  = state.get("ts_migration")
            elapsed_sec   = int(now_ts - ts_migration.timestamp()) if ts_migration else None

            pool_quote = self._helius.get_pool_quote(mint)

            db.insert_sell_exhaustion_candidate({
                "mint":           mint,
                "symbol":         state.get("symbol"),
                "ts_signal":      datetime.fromtimestamp(now_ts, tz=timezone.utc),
                "mc_at_signal":   mc_current,
                "mc_migration":   mc_migration,
                "mc_min":         mc_min,
                "drop_from_mig":  round(drop_from_mig, 4) if drop_from_mig is not None else None,
                "elapsed_sec":    elapsed_sec,
                "pool_quote":     pool_quote,
                "sell_5s":        vel["sell_5s"],
                "sell_5_15s":     vel["sell_5_15s"],
                "sell_cnt_5s":    vel["sell_cnt_5s"],
                "sell_ratio":     vel["ratio"],
                "bottom_age_sec": bottom_age_sec,
            })

            if vel["exhausted"]:
                logger.info(
                    "SellExhausted %s: sell_5s=%.3f sell_5_15s=%.3f ratio=%s age=%.0fs",
                    state.get("symbol", mint[:8]),
                    vel["sell_5s"], vel["sell_5_15s"],
                    f"{vel['ratio']:.2f}" if vel["ratio"] is not None else "∞",
                    bottom_age_sec,
                )
        except Exception as e:
            logger.debug("sell_exhaustion %s: %s", mint[:8], e)

    def _backfill_slope_ticks(self, mint: str, state: dict):
        """
        B.3: Busca kline completo desde a migração e insere ticks históricos para cobertura.
        Chamado em thread própria na primeira detecção de bounce elegível.
        """
        try:
            now_ts      = int(datetime.now(timezone.utc).timestamp())
            ts_mig_unix = state["ts_mig_unix"]
            total_supply = state["total_supply"]

            candles = self.gmgn.get_token_kline(
                mint, "sol", "1m",
                from_ts=ts_mig_unix,
                to_ts=now_ts,
            )
            if not candles:
                logger.debug("backfill %s: kline vazio", mint[:8])
                return

            kline_ticks = []
            for c in candles:
                candle_ts   = int(c.get("time", 0)) / 1000
                elapsed_sec = int(candle_ts - ts_mig_unix)
                if elapsed_sec < 0 or elapsed_sec > 3660:
                    continue
                # Bug 6 fix: ignora o candle de transição bonding curve → pool.
                if elapsed_sec < 90:
                    continue
                close_p = _float(c.get("close"))
                open_p  = _float(c.get("open"))
                high_p  = _float(c.get("high"))
                low_p   = _float(c.get("low"))
                vol     = _float(c.get("volume"))
                if not close_p or close_p <= 0 or not total_supply:
                    continue
                kline_ticks.append({
                    "mint":        mint,
                    "elapsed_sec": elapsed_sec,
                    "mc":          close_p * total_supply,
                    "mc_open":     open_p  * total_supply if open_p  else None,
                    "mc_high":     high_p  * total_supply if high_p  else None,
                    "mc_low":      low_p   * total_supply if low_p   else None,
                    "volume_1m":   vol,
                })

            if kline_ticks:
                db.insert_kline_ticks_batch(kline_ticks)
                logger.info("backfill %s: %d ticks inseridos (%.0fmin de histórico)",
                            mint[:8], len(kline_ticks),
                            (now_ts - ts_mig_unix) / 60)
        except Exception as e:
            logger.debug("backfill %s: %s", mint[:8], e)

    # ------------------------------------------------------------------
    # Threads
    # ------------------------------------------------------------------

    def start(self):
        threading.Thread(target=self._loop,         daemon=True, name="hot-monitor").start()
        threading.Thread(target=self._precise_loop, daemon=True, name="hot-precise").start()
        threading.Thread(target=self.startup_backfill, daemon=True, name="hot-backfill").start()
        if self._helius:
            self._helius.start()
        logger.info("HotMonitor iniciado (grad=%ds precise=%ds janela=%dmin helius=%s)",
                    HOT_INTERVAL, HOT_PRECISE_SEC, HOT_WINDOW_MIN,
                    "ON" if self._helius else "OFF")

    def startup_backfill(self):
        """
        Ao reiniciar, re-popula _tokens para tokens ainda na janela quente e
        reagenda/executa checkpoints ausentes para tokens já graduados.
        Usa total_supply=1B (padrão para tokens pump-style Solana).
        """
        time.sleep(5)  # aguarda scheduler e conexões estabilizarem

        try:
            # Backfill histórico: tokens mortos antes do checkpoint de 1h
            n_fixed = db.backfill_recovery_false()
            if n_fixed:
                logger.info("Backfill recovery=FALSE: %d tokens sem mc_1h marcados como não recuperados", n_fixed)

            tokens = db.get_tokens_for_backfill()
        except Exception as e:
            logger.warning("startup_backfill: DB indisponível, pulando backfill (%s)", e)
            return

        if not tokens:
            logger.info("Backfill startup: nenhum token pendente")
            return

        now = datetime.now(timezone.utc)
        logger.info("Backfill startup: %d tokens com checkpoints pendentes", len(tokens))

        for row in tokens:
            mint         = row["mint"]
            symbol       = row.get("token_nome") or mint[:8]
            mc_migration = row.get("mc_migration") or 0
            ts_migration = row["ts_migration"]
            ts_mig_unix  = int(ts_migration.timestamp())
            elapsed_sec  = (now - ts_migration).total_seconds()
            total_supply = 1_000_000_000

            if elapsed_sec < HOT_WINDOW_MIN * 60:
                # Ainda na janela quente — re-adiciona ao _tokens
                self.add(mint, mc_migration, ts_migration, symbol, total_supply)
                logger.info("Backfill: hot re-add %s (%.0fmin restantes)",
                            symbol, (HOT_WINDOW_MIN * 60 - elapsed_sec) / 60)
                continue

            # Já passou da janela — backfill de mc_1h se ausente
            if row.get("mc_1h") is not None:
                continue
            target_ts = ts_mig_unix + 3600
            remaining = target_ts - now.timestamp()
            if remaining <= 0:
                threading.Thread(
                    target=self._checkpoint,
                    args=(mint, mc_migration, total_supply, "1h", symbol, ts_mig_unix),
                    daemon=True,
                ).start()
                time.sleep(1.0)
            else:
                self.scheduler.schedule(remaining, self._checkpoint,
                                        mint, mc_migration, total_supply,
                                        "1h", symbol, ts_mig_unix)
                logger.info("Backfill: agendado %s 1h em %.0fmin",
                            symbol, remaining / 60)

    def _flush_tick_buffer(self):
        """Drena o buffer de ticks para o banco em uma única transação."""
        with self._tick_lock:
            batch = self._tick_buffer[:]
            self._tick_buffer.clear()
        if not batch:
            return
        try:
            db.insert_ticks_batch(batch)
            logger.debug("tick_buffer: %d ticks inseridos", len(batch))
        except Exception as e:
            logger.warning("flush_tick_buffer: %s", e)

    def _loop(self):
        while True:
            try:
                self._tick()
                self._flush_tick_buffer()
            except Exception as e:
                logger.exception("HotMonitor erro: %s", e)
            time.sleep(HOT_INTERVAL)

    def _precise_loop(self):
        """A cada HOT_PRECISE_SEC busca kline 1m por token quente e aplica o low real da vela."""
        while True:
            time.sleep(HOT_PRECISE_SEC)
            try:
                self._precise_bottom_tick()
            except Exception as e:
                logger.exception("PreciseBottom erro: %s", e)

    # ------------------------------------------------------------------
    # Tick de graduação
    # ------------------------------------------------------------------

    def _tick(self):
        with self._lock:
            snapshot = list(self._tokens.items())
        if not snapshot:
            return

        now = datetime.now(timezone.utc)
        to_graduate = []

        for mint, state in snapshot:
            if mint in self._cancelled:
                with self._lock:
                    self._tokens.pop(mint, None)
                continue
            elapsed_sec = (now - state["ts_migration"]).total_seconds()
            if elapsed_sec / 60 >= HOT_WINDOW_MIN:
                to_graduate.append((mint, state, elapsed_sec))

        for mint, state, elapsed_sec in to_graduate:
            with self._lock:
                self._tokens.pop(mint, None)
            self._graduate(mint, state, elapsed_sec)

    # ------------------------------------------------------------------
    # Tick preciso — kline low real por token quente
    # ------------------------------------------------------------------

    def _precise_bottom_tick(self):
        with self._lock:
            snapshot = [(m, dict(s)) for m, s in self._tokens.items()
                        if m not in self._cancelled]
        if not snapshot:
            return

        now_ts = int(datetime.now(timezone.utc).timestamp())
        for mint, state in snapshot:
            try:
                candles = self.gmgn.get_token_kline(
                    mint, "sol", "1m",
                    from_ts=now_ts - 300,   # B.2: 2min→5min — cobre gaps entre polls
                    to_ts=now_ts,
                )
                # Loop único cronológico: mc_min/max, ticks e bounce num passo só.
                # Dispara no PRIMEIRO close que passa todos os filtros (A.2, A.3, A.6).
                # Antes: só verificava a última vela → entrava 50-100% acima do fundo.
                # Agora: verifica cada vela em ordem, para no primeiro sinal válido.
                kline_ticks = []
                candles_sorted = sorted(candles, key=lambda c: int(c.get("time", 0)))
                for c in candles_sorted:
                    candle_ts   = int(c.get("time", 0)) / 1000
                    elapsed_sec = int(candle_ts - state["ts_mig_unix"])
                    elapsed_min = elapsed_sec / 60
                    if elapsed_sec < 0 or elapsed_sec > 3660:
                        continue
                    # NÃO ignora os primeiros 90s — o gate de 90s foi removido.
                    # Anteriormente, o low do candle de transição bonding curve → pool era
                    # escrito em mc_min. Agora o kline NÃO escreve mc_min (erpc é a fonte).
                    # O candle pode ser inserido em token_ticks normalmente.

                    low_price = _float(c.get("low"))
                    mc_low = low_price * state["total_supply"] if (low_price and low_price > 0) else None

                    # mc_min vem exclusivamente do erpc (push_mc com token_data=None).
                    # O kline NÃO atualiza mc_min — mantém baseline de decisão monotônico
                    # e auditável apenas por eventos erpc. mc_low aqui é SOMENTE para ticks.

                    # 2. Atualiza mc_max com o CLOSE desta vela (não o high).
                    # Kline high captura spikes intra-candle de transações individuais
                    # em pools com baixa liquidez — um único whale buy pode temporariamente
                    # empurrar o AMM price para 200x mesmo que o close volte ao normal.
                    # close = preço sustentado ao final do minuto → valor confiável.
                    close_for_peak = _float(c.get("close"))
                    if close_for_peak and close_for_peak > 0:
                        mc_close_peak = close_for_peak * state["total_supply"]
                        if mc_close_peak > state["mc_max"]:
                            with self._lock:
                                inner = self._tokens.get(mint)
                                if inner and mc_close_peak > inner["mc_max"]:
                                    inner["mc_max"]   = mc_close_peak
                                    inner["time_max"] = elapsed_min
                            db.update_peak(mint, mc_close_peak, elapsed_min)
                            logger.info("Pico(kline)  %s: $%.0f em %.1fmin (+%.0f%% vs mig)",
                                        state["symbol"], mc_close_peak, elapsed_min,
                                        (mc_close_peak / state["mc_migration"] - 1) * 100)
                            state["mc_max"] = mc_close_peak

                    # 3. Acumula tick kline com OHLC completo.
                    close_p = _float(c.get("close"))
                    open_p  = _float(c.get("open"))
                    high_p  = _float(c.get("high"))
                    vol     = _float(c.get("volume"))
                    supply  = state["total_supply"]
                    if close_p and close_p > 0 and supply:
                        mc_low_tick = mc_low
                        kline_ticks.append({
                            "mint":        mint,
                            "elapsed_sec": elapsed_sec,
                            "mc":          close_p * supply,
                            "mc_open":     open_p  * supply if open_p  else None,
                            "mc_high":     high_p  * supply if high_p  else None,
                            "mc_low":      mc_low_tick,
                            "volume_1m":   vol,
                        })

                    # 4. Verifica bounce com o close desta vela (mc_min já atualizado acima).
                    #    Para no primeiro sinal válido — não espera a última vela.
                    if close_p and close_p > 0:
                        mc_close = close_p * state["total_supply"]
                        with self._lock:
                            inner = self._tokens.get(mint)
                        if inner and not inner.get("bounce_fired") and mint not in self._cancelled:
                            self._check_bounce(mint, inner, mc_close, elapsed_sec)

                if kline_ticks:
                    db.insert_kline_ticks_batch(kline_ticks)

                # Back-filla volume_1m nos ticks trenches existentes
                db.backfill_tick_volumes(mint, candles, state["ts_mig_unix"])

            except Exception as e:
                logger.debug("precise bottom %s: %s", mint[:8], e)
            time.sleep(0.5)

    # ------------------------------------------------------------------
    # Graduação — scan completo do kline 1h + agenda 4h/24h
    # ------------------------------------------------------------------

    def _graduate(self, mint: str, state: dict, elapsed_sec: float):
        symbol       = state["symbol"]
        mc_migration = state["mc_migration"]
        total_supply = state["total_supply"]
        ts_mig_unix  = state["ts_mig_unix"]

        candles_1h = self.gmgn.get_token_kline(
            mint, "sol", "1m",
            from_ts=ts_mig_unix,
            to_ts=ts_mig_unix + 3660,
        )

        mc_1h = None
        src   = None

        if candles_1h:
            for c in candles_1h:
                candle_ts   = int(c.get("time", 0)) / 1000
                elapsed_sec = int(candle_ts - ts_mig_unix)
                elapsed_min = elapsed_sec / 60
                if elapsed_sec < 0 or elapsed_sec > 3660:
                    continue
                # Bug 5 fix: ignora o candle de transição bonding curve → pool.
                # Mesmo filtro do _precise_bottom_tick — elimina mc_min fictício.
                if elapsed_sec < 90:
                    continue
                low = _float(c.get("low"))
                if low and low > 0:
                    db.update_bottom(mint, low * total_supply, elapsed_min)
                close = _float(c.get("close"))
                if close and close > 0:
                    db.update_peak(mint, close * total_supply, elapsed_min)

            last        = candles_1h[-1]
            close_price = _float(last.get("close"))
            if close_price and close_price > 0:
                mc_1h = close_price * total_supply
                src   = "kline"

        # Sempre tenta token/info: fallback para mc_1h E fonte de lock_percent (locked_ratio)
        info = self.gmgn.get_token_info(mint)
        if info:
            if mc_1h is None:
                try:
                    mc_1h = float(info["price"]) * float(info["circulating_supply"])
                    src   = "token/info"
                except Exception:
                    pass
            locked = _float(info.get("locked_ratio"))
            if locked is not None:
                db.update_lock_percent(mint, locked)
                logger.debug("lock_percent %s: %.2f [token/info]", symbol, locked)

        # Fallback final: último MC do push_mc — máximo 5s de defasagem
        if mc_1h is None and state.get("mc_last"):
            mc_1h = state["mc_last"]
            src   = "push_mc"

        if mc_1h:
            var = (mc_1h - mc_migration) / mc_migration if mc_migration else None
            db.update_checkpoint(mint, "1h", mc_1h, var)
            logger.info("Checkpoint 1h %s: $%.0f (%s) [%s]", symbol, mc_1h,
                        f"{var*100:+.1f}%" if var is not None else "N/A", src)
        else:
            logger.warning("Graduacao %s: todas as fontes falharam — 1h pulado", symbol)

        # Validação assíncrona do mc_min via scan on-chain (não bloqueia o trade)
        # Calcula bottom_error_pct = (mc_min_decision - mc_min_true) / mc_min_true
        _mc_min_decision = state.get("mc_min", float("inf"))
        _ts_mig_unix     = ts_mig_unix
        # Captura pool_pda AGORA (antes do unsubscribe deletar do dict)
        _pool_pda_snap   = self._helius.get_pool_pda(mint) if self._helius else None

        def _run_bottom_truth():
            try:
                import helius_rpc
                pool_pda = _pool_pda_snap
                if not pool_pda:
                    logger.debug("bottom_truth %s: pool_pda não disponível — skip", symbol)
                    return
                sol_usd_val = get_sol_usd()
                truth = helius_rpc.get_pool_bottom(
                    pool_pda,
                    _ts_mig_unix,
                    _ts_mig_unix + 3660,
                    total_supply,
                    sol_usd_val,
                )
                if not truth:
                    logger.info("bottom_truth %s: scan on-chain retornou None (sem txs ou RPC indisponível)", symbol)
                    return
                mc_true = truth["mc_min_true"]
                if mc_true > 0 and _mc_min_decision != float("inf"):
                    err = (_mc_min_decision - mc_true) / mc_true
                    quality = "good" if abs(err) <= 0.05 else ("ok" if abs(err) <= 0.15 else "bad")
                else:
                    err     = None
                    quality = None
                db.update_bottom_truth(
                    mint,
                    truth["mc_min_true"],
                    truth["ts_min_true"],
                    truth["txid_min_true"],
                    truth["source_truth"],
                    err,
                    quality,
                )
                logger.info(
                    "bottom_truth %s: decision=$%.0f true=$%.0f error=%s quality=%s txid=%s...",
                    symbol,
                    _mc_min_decision,
                    mc_true,
                    f"{err*100:+.1f}%" if err is not None else "N/A",
                    quality or "N/A",
                    truth["txid_min_true"][:16],
                )
            except Exception as _e:
                logger.warning("bottom_truth %s: erro inesperado: %s", symbol, _e)

        threading.Thread(target=_run_bottom_truth, daemon=True, name=f"btrue-{mint[:8]}").start()

        logger.info("%s saiu da janela quente (bottom=$%.0f em %.1fmin)",
                    symbol, state["mc_min"], state["time_min"])

    def _checkpoint(self, mint: str, mc_migration: float, total_supply: float,
                    label: str, symbol: str, ts_mig_unix: int):
        if mint in self._cancelled:
            return

        delay_sec = {"1h": 3600}[label]
        target_ts = ts_mig_unix + delay_sec
        mc        = None
        src       = "?"

        # Janela estreita ao redor do mark exato (token ativo)
        candles = self.gmgn.get_token_kline(
            mint, "sol", "1m",
            from_ts=target_ts - 120,
            to_ts=target_ts + 60,
        )
        # Fallback: range completo desde a migração — captura último preço de token morto
        if not candles:
            candles = self.gmgn.get_token_kline(
                mint, "sol", "1m",
                from_ts=ts_mig_unix,
                to_ts=target_ts + 60,
            )

        if candles:
            closest     = min(candles, key=lambda c: abs(int(c.get("time", 0)) / 1000 - target_ts))
            close_price = _float(closest.get("close"))
            if close_price and close_price > 0:
                mc  = close_price * total_supply
                src = "kline"

        # Fallback: token/info (1 tentativa)
        if mc is None:
            info = self.gmgn.get_token_info(mint)
            if info:
                try:
                    mc  = float(info["price"]) * float(info["circulating_supply"])
                    src = "token/info"
                except Exception:
                    pass

        # Fallback: último MC do trenches (poll 5s — único dado confiável para Meteora DAMM v2)
        if mc is None:
            with self._lock:
                mc_live = self._mc_live.get(mint)
            if mc_live:
                mc  = mc_live
                src = "trenches_live"

        if mc is None:
            logger.warning("Checkpoint %s %s: todas as fontes falharam", symbol, label)
            return

        var = (mc - mc_migration) / mc_migration if mc_migration else None
        db.update_checkpoint(mint, label, mc, var)
        logger.info("Checkpoint %s %s: $%.0f (%s) [%s]", symbol, label, mc,
                    f"{var*100:+.1f}%" if var is not None else "N/A", src)


# ==============================================================================
# Scanner
# ==============================================================================

class Scanner:
    def __init__(self, gmgn: GmgnClient, hot: HotMonitor):
        self.gmgn = gmgn
        self.hot  = hot
        self._watchlist: dict = {}
        self._seen: set = set()
        self._lock = threading.Lock()
        self._signal_ban_until: float = 0.0  # unix ts — aguarda antes de chamar token_signal

        # Diagnóstico — contadores atualizados a cada ciclo
        self.stats: dict = {
            "last_poll_ts":         None,   # timestamp do último _tick() iniciado
            "last_poll_ok_ts":      None,   # timestamp do último _tick() concluído sem erro
            "last_error_ts":        None,
            "last_error_msg":       None,
            "poll_count":           0,
            "near_completion_seen": 0,      # tokens vistos no near_completion
            "completed_seen":       0,      # tokens vistos no completed
            "watchlist_added":      0,      # tokens adicionados à watchlist
            "migrations_ok":        0,      # tokens que passaram todos os filtros
            "migrations_rejected":  0,      # tokens descartados em _handle_completed
            "last_migration_ts":    None,
            "last_migration_sym":   None,
            "last_reject_reason":   None,
            "last_reject_sym":      None,
        }

    def start(self):
        thread = threading.Thread(target=self._loop, daemon=True, name="scanner")
        thread.start()
        logger.info(
            "Scanner iniciado (poll=%ds) | bc_min=%.0f%% | mc=$%.0f-$%.0f | liq>=$%.0f | fee>=%.1fSOL | "
            "filtros: bundler<%.0f%% bot<%.0f%% sniper<%.0f%% top10<%.0f%% rat<%.0f%% wash=%s",
            POLL_INTERVAL, BC_MIN_WATCHLIST, F_MC_MIN, F_MC_MAX, F_LIQ_MIN, F_FEE_MIN,
            F_BUNDLER_MAX*100, F_BOT_MAX*100, F_SNIPER_MAX*100,
            F_TOP10_MAX*100, F_RAT_MAX*100, F_WASH_BLOCK,
        )

    def _loop(self):
        while True:
            try:
                self._tick()
            except RateLimitError as e:
                wait = max(1.0, e.reset_at - time.time()) + 1
                logger.warning("Rate limit global, aguardando %.1fs", wait)
                self.stats["last_error_ts"]  = time.time()
                self.stats["last_error_msg"] = f"RateLimit — aguarda {wait:.0f}s"
                time.sleep(wait)
            except Exception as e:
                logger.exception("Scanner erro: %s", e)
                self.stats["last_error_ts"]  = time.time()
                self.stats["last_error_msg"] = f"{type(e).__name__}: {e}"
            time.sleep(POLL_INTERVAL)

    def _tick(self):
        self.stats["last_poll_ts"] = time.time()
        self.stats["poll_count"]  += 1
        data = self.gmgn.get_trenches(types=["near_completion", "completed"])

        near_completion_list = data.get("pump", [])
        completed_list       = data.get("completed", [])
        self.stats["near_completion_seen"] = len(near_completion_list)
        self.stats["completed_seen"]       = len(completed_list)

        # near_completion: ignora tokens já conhecidos sem chamar _safe (sem sleep)
        for token in near_completion_list:
            mint = token.get("address")
            with self._lock:
                if mint and (mint in self._watchlist or mint in self._seen):
                    continue
            self._safe(self._handle_near_completion, token)

        # H27 — trending check: 1 chamada por tick (peso=1, capacity=20 → custo mínimo).
        # Pega top-100 pump.fun no último 1min. Tokens ativos durante a graduação da bonding
        # curve aparecem aqui antes de migrarem — indica hype real no momento da migração.
        # Resultado compartilhado entre todos os tokens novos desse tick.
        trending_mints: set = set()
        new_completed = [t for t in completed_list
                         if t.get("address") and t["address"] not in self._seen]
        if new_completed:
            try:
                rank_list = self.gmgn.get_market_trending(
                    interval="1m", limit=100, platforms=["Pump.fun"]
                )
                trending_mints = {t.get("address") or t.get("mint") for t in rank_list if t}
            except Exception as e:
                logger.debug("trending tick check: %s", e)

        # completed: tokens já vistos → apenas push_mc (sem sleep)
        #            tokens novos → _safe com stagger normal
        for token in completed_list:
            mint = token.get("address")
            mc   = _float(token.get("usd_market_cap")) or _float(token.get("market_cap"))
            with self._lock:
                already_seen = bool(mint and mint in self._seen)
            if already_seen:
                if mint and mc:
                    self.hot.push_mc(mint, mc, token)
            else:
                self._safe(self._handle_completed, token, trending_mints)
                if mint and mc and F_MC_MIN <= mc <= F_MC_MAX:
                    self.hot.push_mc(mint, mc, token)

        self.stats["last_poll_ok_ts"] = time.time()
        self._poll_signals()

    def _poll_signals(self):
        """
        Faz poll do feed global de sinais type 12 (SmartDegenBuy) e persiste
        apenas os sinais de tokens que estão ativos no HotMonitor.
        Rate limit: peso 3; chamado a cada 5s (mesma janela do trenches).
        """
        now = time.time()
        if now < self._signal_ban_until:
            return

        with self.hot._lock:
            active_mints = set(self.hot._tokens.keys())

        if not active_mints:
            return

        try:
            entries = self.gmgn.get_token_signal()
        except RateLimitError as e:
            self._signal_ban_until = e.reset_at + 1
            logger.warning("token_signal rate limit — ban até %.0f (%.0fs)",
                           e.reset_at, e.reset_at - now)
            return

        matches = []
        for entry in entries:
            mint = entry.get("token_address")
            if mint not in active_mints:
                continue
            trigger_at = entry.get("trigger_at")
            if not trigger_at:
                continue
            wallets = entry.get("smart_degen_wallets") or []
            matches.append({
                "mint":             mint,
                "trigger_at":       int(trigger_at),
                "trigger_mc":       _float(entry.get("trigger_mc")),
                "first_trigger_mc": _float(entry.get("first_trigger_mc")),
                "signal_times":     entry.get("signal_times"),
                "wallets_json":     json.dumps(wallets) if wallets else None,
            })

        if matches:
            try:
                db.insert_signal_batch(matches)
                logger.info("Sinais type12: %d novos (de %d entries, %d tokens hot)",
                            len(matches), len(entries), len(active_mints))
            except Exception as e:
                logger.warning("insert_signal_batch: %s", e)

    def _safe(self, fn, token: dict, *args):
        try:
            fn(token, *args)
            time.sleep(0.3)
        except RateLimitError:
            raise
        except Exception as e:
            logger.warning("%s erro %s: %s", fn.__name__,
                           token.get("address", "?")[:8], e)

    # --------------------------------------------------------------------------
    # Watchlist
    # --------------------------------------------------------------------------

    def _handle_near_completion(self, token: dict):
        mint = token.get("address")
        if not mint:
            return
        with self._lock:
            if mint in self._watchlist or mint in self._seen:
                return

        bc = float(token.get("bc_progress") or 0)
        if bc < BC_MIN_WATCHLIST:
            return

        ok, motivo = _passes_filter(token)
        if not ok:
            logger.info("Descartado %s (%s): %s",
                        token.get("symbol"), mint[:8], motivo)
            return

        now = datetime.now(timezone.utc)
        created_ts = _int(token.get("created_timestamp"))
        entry = {
            "mint":                     mint,
            "token_nome":               token.get("symbol"),
            "ts_detected":              now,
            "bc_progress_at_detection": bc,
            "is_wash_trading":          bool(token.get("is_wash_trading")),
            "smart_wallets":            _int(token.get("smart_degen_count")),
            "renowned_wallets":         _int(token.get("renowned_count")),
            "sniper_wallets":           _int(token.get("sniper_count")),
            "rug_ratio":                _float(token.get("rug_ratio")),
            "top10_holder_rate":        _float(token.get("top_10_holder_rate")),
            "holders_count":            _int(token.get("holder_count")),
            "volume_1m":                _float(token.get("volume_1m")),
            "volume_5m":                _float(token.get("volume_5m")),
            "volume_15m":               _float(token.get("volume_15m")),
            "liq_migration":            _float(token.get("liquidity")),
            "is_honeypot":              bool(token.get("is_honeypot")) if token.get("is_honeypot") is not None else None,
            "token_created_at":         datetime.fromtimestamp(created_ts, tz=timezone.utc) if created_ts else None,
            "time_to_migrate_sec":      _int(token.get("complete_cost_time")),
            # Fase 1 — campos de qualidade
            "creator_token_status":     token.get("creator_token_status"),
            "creator_balance_rate":     _float(token.get("creator_balance_rate")),
            "dev_team_hold_rate":       _float(token.get("dev_team_hold_rate")),
            "suspected_insider_rate":   _float(token.get("suspected_insider_hold_rate")),
            "top70_sniper_hold_rate":   _float(token.get("top70_sniper_hold_rate")),
            "bluechip_owner_pct":       _float(token.get("bluechip_owner_percentage")),
            "bot_degen_rate":           _float(token.get("bot_degen_rate")),
            "entrapment_ratio":         _float(token.get("entrapment_ratio")),
            "lock_percent":             _float(token.get("lock_percent")),
            "burn_status":              token.get("burn_status"),
            "cto_flag":                 bool(token.get("cto_flag")) if token.get("cto_flag") is not None else None,
            "has_social":               bool(token.get("has_at_least_one_social")) if token.get("has_at_least_one_social") is not None else None,
            "x_follower_count":         _int(token.get("x_user_follower")),
        }

        with self._lock:
            self._watchlist[mint] = entry
            self.stats["watchlist_added"] += 1

        db.upsert_watchlist(entry)

        # Subscrição antecipada no erpc: token ainda na bonding curve mas já
        # monitorado. Quando migrar, o primeiro evento pAMM é processado em t=0
        # sem esperar o próximo poll do trenches (elimina gap de 5-10min).
        if self.hot._helius:
            supply = int(token.get("total_supply") or 1_000_000_000)
            self.hot._helius.subscribe(mint, supply)

        logger.info("Watchlist +%s (%s) bc=%.0f%% smart=%s rug=%.0f%%",
                    token.get("symbol"), mint[:8], bc,
                    entry["smart_wallets"], (entry["rug_ratio"] or 0) * 100)

    # --------------------------------------------------------------------------
    # Migração — MC e qualidade do trenches, zero chamadas de API
    # --------------------------------------------------------------------------

    def _handle_completed(self, token: dict, trending_mints: set = None):
        mint = token.get("address")
        if not mint:
            return

        # Rejeita migrações antigas — evita inundar o dashboard após reinício do bot
        complete_ts = int(token.get("complete_timestamp") or 0)
        if complete_ts > 0:
            age_sec = int(datetime.now(timezone.utc).timestamp()) - complete_ts
            if age_sec > MAX_MIG_AGE_SEC:
                with self._lock:
                    self._seen.add(mint)  # marca como visto para não checar mais
                logger.debug("Migracao antiga %s (%s): %ds — ignorada",
                             token.get("symbol"), mint[:8], age_sec)
                return

        with self._lock:
            if mint in self._seen:
                return
            self._seen.add(mint)

        # Lê watchlist_entry sem pop — só remove depois de passar todos os filtros,
        # para não perder dados se o token for rejeitado (ex: FILTER_PUMP_ONLY).
        with self._lock:
            watchlist_entry = self._watchlist.get(mint)

        symbol   = token.get("symbol") or mint[:8]
        mc       = _float(token.get("usd_market_cap")) or _float(token.get("market_cap"))
        liq      = _float(token.get("liquidity")) or 0
        exchange = (token.get("exchange") or "").lower()
        fee      = _float(token.get("total_fee")) or 0

        # PUMP_ONLY é gate de visibilidade total — tokens não-pump não interessam ao bot
        if F_PUMP_ONLY and "pump" not in exchange:
            logger.debug("Migracao ignorada %s (%s): exchange=%s (FILTER_PUMP_ONLY)",
                         symbol, mint[:8], exchange or "unknown")
            return

        # Avalia filtros de qualidade — NÃO gateiam mais o upsert, apenas hot.add()
        # Assim todos os tokens pump.fun aparecem no /scanner independente dos filtros.
        if not watchlist_entry:
            quality_ok, quality_reason = _passes_filter(token)
        else:
            quality_ok, quality_reason = True, ""

        mc_ok  = bool(mc and F_MC_MIN <= mc <= F_MC_MAX)
        liq_ok = liq >= F_LIQ_MIN
        fee_ok = fee >= F_FEE_MIN
        passed_filters = quality_ok and mc_ok and liq_ok and fee_ok

        if not passed_filters:
            if not quality_ok:
                filter_motivo = quality_reason
            elif not mc_ok:
                filter_motivo = (f"mc=${mc:.0f}<${F_MC_MIN:.0f}" if mc and mc < F_MC_MIN
                                 else f"mc=${mc:.0f}>${F_MC_MAX:.0f}" if mc else "mc=0")
            elif not liq_ok:
                filter_motivo = f"liq=${liq:.0f}<${F_LIQ_MIN:.0f}"
            else:
                filter_motivo = f"total_fee={fee:.4f}<{F_FEE_MIN:.1f}SOL"
            logger.info("Migracao salva (fora filtro) %s (%s): %s", symbol, mint[:8], filter_motivo)
            self.stats["migrations_rejected"] += 1
            self.stats["last_reject_reason"]   = filter_motivo
            self.stats["last_reject_sym"]      = symbol

        # Remove da watchlist
        with self._lock:
            watchlist_entry = self._watchlist.pop(mint, watchlist_entry)

        total_supply      = _float(token.get("total_supply")) or 1_000_000_000
        complete_ts       = int(token.get("complete_timestamp") or 0)
        now               = datetime.now(timezone.utc)
        now_ts            = int(now.timestamp())
        capture_delay_sec = (now_ts - complete_ts) if complete_ts > 0 else None
        # Bug 1 fix: usar o timestamp on-chain da migração, não o momento de detecção pelo bot.
        # Usar 'now' como ts_migration faz elapsed_sec=60s para tokens migrados há 18min → falsos bounces imediatos.
        ts_mig_real       = datetime.fromtimestamp(complete_ts, tz=timezone.utc) if complete_ts > 0 else now

        # Tenta obter o preço exato no momento da migração via kline de 1m.
        # A abertura do candle mais próximo de complete_timestamp é a melhor
        # aproximação do MC real de migração, sem distorção do dump/pump pós-migração.
        mc_kline = None
        if complete_ts > 0:
            candles = self.gmgn.get_token_kline(
                mint, "sol", "1m",
                from_ts=complete_ts - 30,
                to_ts=complete_ts + 90,
            )
            if candles:
                closest = min(candles,
                              key=lambda c: abs(int(c.get("time", 0)) / 1000 - complete_ts))
                open_price = _float(closest.get("open"))
                if open_price and open_price > 0:
                    mc_kline = open_price * total_supply

        mc_observed = mc_kline if mc_kline else mc
        src_observed = "kline" if mc_kline else "trenches"
        mc_bc_ref = None
        sol_usd = get_sol_usd()
        if sol_usd and sol_usd > 0 and MIGRATION_SOL_UNITS > 0:
            mc_bc_ref = sol_usd * MIGRATION_SOL_UNITS
        mc_migration = mc_bc_ref if mc_bc_ref else mc_observed
        src = "bonding_curve" if mc_bc_ref else src_observed

        created_ts          = _int(token.get("created_timestamp"))
        token_created_at    = datetime.fromtimestamp(created_ts, tz=timezone.utc) if created_ts else None
        time_to_migrate_sec = _int(token.get("complete_cost_time"))

        if capture_delay_sec is not None and capture_delay_sec > 90:
            logger.warning(
                "Migracao %s (%s): %ds apos migracao — MC oficial(%s): $%.0f",
                token.get("symbol"), mint[:8], capture_delay_sec, src, mc_migration,
            )
        quality = {
            "smart_wallets":          _int(token.get("smart_degen_count")),
            "renowned_wallets":       _int(token.get("renowned_count")),
            "sniper_wallets":         _int(token.get("sniper_count")),
            "rug_ratio":              _float(token.get("rug_ratio")),
            "bundler_trader_amount_rate": _float(token.get("bundler_trader_amount_rate")),
            "top10_holder_rate":      _float(token.get("top_10_holder_rate")),
            "holders_count":          _int(token.get("holder_count")),
            "volume_1m":              _float(token.get("volume_1m")),
            "volume_5m":              _float(token.get("volume_5m")),
            "volume_15m":             _float(token.get("volume_15m")),
            "liq_migration":          liq,
            "is_wash_trading":        bool(token.get("is_wash_trading")),
            "is_honeypot":            None,
            # Fase 1 — campos de qualidade
            "creator_token_status":   token.get("creator_token_status"),
            "creator_balance_rate":   _float(token.get("creator_balance_rate")),
            "dev_team_hold_rate":     _float(token.get("dev_team_hold_rate")),
            "suspected_insider_rate": _float(token.get("suspected_insider_hold_rate")),
            "top70_sniper_hold_rate": _float(token.get("top70_sniper_hold_rate")),
            "bluechip_owner_pct":     _float(token.get("bluechip_owner_percentage")),
            "bot_degen_rate":         _float(token.get("bot_degen_rate")),
            "entrapment_ratio":       _float(token.get("entrapment_ratio")),
            "lock_percent":           _float(token.get("lock_percent")),
            "burn_status":            token.get("burn_status"),
            "cto_flag":               bool(token.get("cto_flag")) if token.get("cto_flag") is not None else None,
            "has_social":             bool(token.get("has_at_least_one_social")) if token.get("has_at_least_one_social") is not None else None,
            "x_follower_count":       _int(token.get("x_user_follower")),
            # Campos antes descartados — agora salvos
            "exchange":               token.get("exchange"),
            "rat_trader_rate":        _float(token.get("rat_trader_amount_rate")),
            "total_fee_sol":          _float(token.get("total_fee")),
            "bc_progress_migration":  _float(token.get("bc_progress")),
            "complete_timestamp":     _int(token.get("complete_timestamp")),
        }

        if watchlist_entry:
            for k in ("smart_wallets", "renowned_wallets", "sniper_wallets",
                      "rug_ratio", "top10_holder_rate", "holders_count",
                      "volume_1m", "volume_5m", "volume_15m",
                      "liq_migration", "is_honeypot", "is_wash_trading"):
                if watchlist_entry.get(k) is not None:
                    quality[k] = watchlist_entry[k]

        entry = {
            "mint":                     mint,
            "token_nome":               token.get("symbol"),
            "ts_detected":              watchlist_entry.get("ts_detected") if watchlist_entry else None,
            "bc_progress_at_detection": watchlist_entry.get("bc_progress_at_detection") if watchlist_entry else None,
            "ts_migration":             ts_mig_real,
            "mc_migration":             mc_migration,
            "mc_migration_observed":    mc_observed,
            "mc_migration_bc_ref":      mc_bc_ref,
            "mc_migration_source":      src,
            "capture_delay_sec":        capture_delay_sec,
            "token_created_at":         token_created_at,
            "time_to_migrate_sec":      time_to_migrate_sec,
            **quality,
        }

        # H27 — trending check: set coletado 1x por tick em _tick() com interval=1m.
        # pump.fun 5m trending inclui tokens pequenos ativos durante a bonding curve.
        trending_at_migration = (mint in trending_mints) if trending_mints else None
        if trending_at_migration:
            logger.info("Migracao %s (%s) — trending=TRUE no pump.fun 1m", symbol, mint[:8])

        entry["trending_at_migration"] = trending_at_migration
        entry["passed_filters"] = passed_filters
        db.upsert_migration(entry)

        if not passed_filters:
            return

        smart  = entry.get("smart_wallets") or 0
        rug    = entry.get("rug_ratio") or 0
        exch   = token.get("exchange") or "?"
        trend_str = " 🔥trending" if trending_at_migration else ""

        send_alert(
            f"\U0001f514 *Migracao: {symbol}*\n"
            f"MC oficial: ${mc_migration:,.0f} ({src}) | Liq: ${liq:,.0f}\n"
            f"Exchange: {exch}{trend_str}\n"
            f"Smart: {smart} | Rug: {rug:.0%}"
        )
        delay_str = f" delay={capture_delay_sec}s" if capture_delay_sec is not None else ""
        logger.info("Migracao %s (%s) MC_oficial=$%.0f[%s] MC_obs=$%.0f[%s] liq=$%.0f exch=%s smart=%d%s trending=%s",
                    symbol, mint[:8], mc_migration, src, mc_observed, src_observed, liq, exch, smart, delay_str,
                    trending_at_migration)

        self.stats["migrations_ok"]        += 1
        self.stats["last_migration_ts"]     = time.time()
        self.stats["last_migration_sym"]    = symbol

        self.hot.add(mint, mc_migration, ts_mig_real, symbol, total_supply,
                     exchange=token.get("exchange"),
                     time_to_migrate_sec=time_to_migrate_sec,
                     trending_at_migration=trending_at_migration)


# ==============================================================================
# Helpers
# ==============================================================================

def _float(v, divisor: float = 1) -> Optional[float]:
    try:
        return float(v) / divisor if v is not None else None
    except Exception:
        return None


def _int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except Exception:
        return None
