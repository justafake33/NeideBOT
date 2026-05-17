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
F_SNIPER_MAX     = float(os.getenv("FILTER_SNIPER_MAX",     "0.05"))  # dados: >5% recovery cai de 33% para 14%
F_RAT_MAX        = float(os.getenv("FILTER_RAT_MAX",        "0.20"))
F_TOP10_MAX      = float(os.getenv("FILTER_TOP10_MAX",      "0.50"))  # dados: >40% recovery cai para 7-15%
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

        # Helius WebSocket — preço real-time via logsSubscribe + DexScreener
        self._helius = None
        if os.getenv("HELIUS_API_KEY"):
            try:
                from helius_ws import HeliusWS
                self._helius = HeliusWS(on_price=self.push_mc)
            except Exception as e:
                logger.warning("HeliusWS não iniciado: %s", e)

    def add(self, mint: str, mc_migration: float, ts_migration: datetime,
            symbol: str, total_supply: float = 1_000_000_000, exchange: str = None):
        added = False
        with self._lock:
            if mint not in self._tokens and mint not in self._cancelled:
                self._tokens[mint] = {
                    "mc_migration": mc_migration,
                    "ts_mig_unix":  int(ts_migration.timestamp()),
                    "ts_migration": ts_migration,
                    "symbol":       symbol,
                    "exchange":     exchange,
                    "mc_min":       mc_migration,
                    "time_min":     0.0,
                    "ts_min":       ts_migration,   # timestamp quando mc_min foi visto pela última vez
                    "mc_max":       mc_migration,
                    "time_max":     0.0,
                    "mc_last":      mc_migration,
                    "total_supply": total_supply,
                }
                added = True

        # Helius: subscrição imediata por mint — sem precisar do pool address
        if added and self._helius and mint not in self._cancelled:
            try:
                self._helius.subscribe(mint, total_supply)
            except Exception as e:
                logger.debug("helius.subscribe %s: %s", mint[:8], e)

    def cancel(self, mint: str):
        with self._lock:
            self._cancelled.add(mint)
            self._tokens.pop(mint, None)
        if self._helius:
            self._helius.unsubscribe(mint)

    def push_mc(self, mint: str, mc: float, token_data: dict = None):
        """Chamado pelo Scanner a cada poll — detecção rápida de rug, bottom e coleta de série temporal."""
        with self._lock:
            self._mc_live[mint] = mc  # captura sempre, mesmo após graduação
            state = self._tokens.get(mint)
            if not state or mint in self._cancelled:
                return

        elapsed_sec = int((datetime.now(timezone.utc) - state["ts_migration"]).total_seconds())
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

        # Pool em transição (primeiros 90s): trenches/Helius podem ter preços da bonding
        # curve pré-migração. Não atualizar mc_min — _precise_bottom_tick cuida disso
        # via A.9 (Helius-only para elapsed < BOUNCE_TIME_MIN_SEC).
        if elapsed_sec >= 90 and mc < state["mc_min"]:
            state["mc_min"] = mc
            state["time_min"] = elapsed_min
            state["ts_min"]   = datetime.now(timezone.utc)
            db.update_bottom(mint, mc, elapsed_min)
            logger.info("Fundo(poll) %s: $%.0f em %.1fmin (%.0f%% abaixo ATH)",
                        state["symbol"], mc, elapsed_min,
                        (1 - mc / state["mc_migration"]) * 100)

        if mc > state["mc_max"]:
            state["mc_max"] = mc
            state["time_max"] = elapsed_min
            db.update_peak(mint, mc, elapsed_min)
            logger.info("Pico(poll)  %s: $%.0f em %.1fmin (+%.0f%% vs mig)",
                        state["symbol"], mc, elapsed_min,
                        (mc / state["mc_migration"] - 1) * 100)

        self._check_bounce(mint, state, mc, elapsed_sec)

    # ------------------------------------------------------------------
    # Bounce detector
    # ------------------------------------------------------------------

    BOUNCE_MIN_PCT       = float(os.getenv("BOUNCE_MIN_PCT",         "0.30"))  # A.7: bounce mínimo 30% (live WR 9.1% abaixo disso)
    BOUNCE_MAX_PCT       = float(os.getenv("BOUNCE_MAX_PCT",         "9.99"))  # desativado por padrão — sem validação GMGN para threshold máximo (nota 87 CLAUDE.md)
    BOUNCE_DROP_MIN      = float(os.getenv("BOUNCE_DROP_MIN",        "0.30"))  # queda mínima da migração
    BOUNCE_TIME_MAX      = float(os.getenv("BOUNCE_TIME_MAX",        "20.0"))  # A.5: janela 30→20min (100% TPs em ≤20min)
    BOUNCE_TIME_MIN_SEC  = int(os.getenv(  "BOUNCE_TIME_MIN_SEC",   "90"))    # A.9: pool em transição nos primeiros 90s — mc_min não atualizado, sinais bloqueados
    BOTTOM_FRESHNESS_SEC = int(os.getenv(  "BOTTOM_FRESHNESS_SEC",  "300"))   # A.6: máx 5min desde o último mc_min

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

        # A.9 — Janela mínima: sinais nos primeiros N segundos têm WR muito baixo (dados: 0-5min → 12.9% WR vs 5-20min → 27% WR)
        if self.BOUNCE_TIME_MIN_SEC > 0 and elapsed_sec < self.BOUNCE_TIME_MIN_SEC:
            logger.debug("BOUNCE skip %s — A.9 muito cedo: %ds < min=%ds", mint[:8], elapsed_sec, self.BOUNCE_TIME_MIN_SEC)
            return
        if mc_min <= 0 or mc_migration <= 0:
            return

        # A.9 — Data source selection by pool maturity:
        # elapsed < BOUNCE_TIME_MIN_SEC: pool ainda em transição bonding curve → pool novo.
        #   Usar APENAS Helius (preço real das pool reserves), ignorar kline (pode ter bonding curve).
        # elapsed >= BOUNCE_TIME_MIN_SEC: pool estável, usar MIN(helius, kline) como antes.
        # Impacto: sinais cedo (< ~5min) usam dado mais preciso, aumentam confiabilidade.

        drop_from_mig = (mc_migration - mc_min) / mc_migration
        if drop_from_mig < self.BOUNCE_DROP_MIN:
            return

        bounce_pct = (mc - mc_min) / mc_min
        if bounce_pct < self.BOUNCE_MIN_PCT:
            return
        if self.BOUNCE_MAX_PCT < 1.0 and bounce_pct > self.BOUNCE_MAX_PCT:
            logger.debug("BOUNCE skip %s — bounce tardio=%.1f%% (max=%.0f%%)",
                         mint[:8], bounce_pct * 100, self.BOUNCE_MAX_PCT * 100)
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

        # A.2 — Dead zone: bounce 20-30% tem WR 4% e EV -10%. Permitir <20% ou ≥30%.
        if 0.20 <= bounce_pct < 0.30:
            logger.debug("BOUNCE skip %s — dead zone bounce=%.1f%%", mint[:8], bounce_pct * 100)
            return

        # A.8 — Preço acima da migração: sinal é artifact de wick intra-candle, não bounce real.
        if mc >= mc_migration:
            logger.debug("BOUNCE skip %s — A.8 mc=$%.0f >= mig=$%.0f", mint[:8], mc, mc_migration)
            return

        # A.3 — Entry ratio 50-65% da migração: WR 0% em 15 tokens. Rejeitar.
        entry_ratio = mc / mc_migration if mc_migration > 0 else 0
        if 0.50 <= entry_ratio <= 0.65:
            logger.debug("BOUNCE skip %s — entry ratio=%.2f (dead zone 50-65%%)", mint[:8], entry_ratio)
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
        ts_min_obj = state.get("ts_min")
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
                "net_sol_flow": (
                    (absorption_window.get("buy_vol_sol") or 0) -
                    (absorption_window.get("sell_vol_sol") or 0)
                ) if absorption_window.get("total_events") else None,
                "h23_window":          "bottom",
                # Janela RISE: [ts_min → ts_signal]
                "rise_buy_vol":  rise_window.get("buy_vol_sol"),
                "rise_sell_vol": rise_window.get("sell_vol_sol"),
                "rise_net_flow": (
                    (rise_window.get("buy_vol_sol")  or 0) -
                    (rise_window.get("sell_vol_sol") or 0)
                ) if rise_window.get("total_events") else None,
                "rise_events":   rise_window.get("total_events"),
                "rise_lrg_buy":  rise_window.get("largest_buy"),
                # H25 — pool depth
                "pool_quote_at_signal": pool_quote_at_signal,
                # H24 — seller concentration durante o dump
                "dump_seller_count":    dump_concentration.get("seller_count"),
                "dump_seller_hhi":      dump_concentration.get("seller_hhi"),
                "dump_top_seller_frac": dump_concentration.get("top_seller_frac"),
                # H26 — buyer concentration na subida
                "buy_concentration":    buy_concentration,
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
            f"  Fundo:    ${mc_min:,.0f} (-{drop_from_mig:.0%})\n"
            f"  Agora:    ${mc:,.0f} (+{bounce_pct:.0%} do fundo)\n"
            f"  Tempo:    {elapsed_min:.1f}min\n"
            f"  Mint: {mint}"
        )
        try:
            send_alert(msg)
        except Exception as e:
            logger.warning("send_alert bounce %s: %s", mint[:8], e)

        logger.info("BOUNCE %s: fundo=$%.0f drop=%.0f%% bounce=%.0f%% em %.1fmin",
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
                )
            except Exception as e:
                logger.error("executor.on_bounce %s: %s", symbol, e)

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

        # Backfill histórico: tokens mortos antes do checkpoint de 1h
        n_fixed = db.backfill_recovery_false()
        if n_fixed:
            logger.info("Backfill recovery=FALSE: %d tokens sem mc_1h marcados como não recuperados", n_fixed)

        tokens = db.get_tokens_for_backfill()
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
                    # Ignora o primeiro candle (cobre transição bonding curve → pool).
                    # O kline low desse candle inclui preços pré-migração e corrompe mc_min.
                    if elapsed_sec < 90:
                        continue

                    # 1. Atualiza mc_min com o low desta vela ANTES de checar bounce.
                    #    Garante que bounce_pct e A.6 usam o fundo correto desta vela.
                    #
                    #    A.9 — Data source selection by pool maturity:
                    #    - elapsed < BOUNCE_TIME_MIN_SEC: pool em transição. Usar APENAS Helius.
                    #      Kline low pode ser bonding curve pré-migração, muito abaixo do real.
                    #    - elapsed >= BOUNCE_TIME_MIN_SEC: pool estável. Usar MIN(helius, kline).
                    #      Helius é preço real (~5ms latência), kline pode ter lag (~200ms).
                    low_price   = _float(c.get("low"))
                    close_price = _float(c.get("close"))
                    mc_low_kline   = low_price   * state["total_supply"] if (low_price   and low_price   > 0) else None
                    mc_close_candle = close_price * state["total_supply"] if (close_price and close_price > 0) else None
                    mc_low = None

                    # Wick guard: em pools finos, um único whale sell empurra o AMM price
                    # momentaneamente para 1/200x do normal e o kline registra isso como 'low'.
                    # Mesmo problema que levou a usar close em vez de high para mc_max.
                    # Se low < close × 0.50 → wick intra-candle, descarta o low.
                    if mc_low_kline and mc_close_candle and mc_low_kline < mc_close_candle * 0.50:
                        logger.debug("Wick ignorado %s: kline_low=$%.0f < close×0.50=$%.0f",
                                     state.get("symbol", mint[:8]), mc_low_kline, mc_close_candle * 0.50)
                        mc_low_kline = None

                    if self._helius:
                        candle_start = state["ts_mig_unix"] + max(0, elapsed_sec - 60)
                        candle_end   = state["ts_mig_unix"] + elapsed_sec
                        mc_low_helius = self._helius.get_min_mc_in_window(mint, candle_start, candle_end)

                        if elapsed_sec < self.BOUNCE_TIME_MIN_SEC:
                            # A.9: pool em transição — confiar APENAS em Helius
                            mc_low = mc_low_helius
                        else:
                            # A.9: pool maduro — usar MIN(helius, kline)
                            mc_low = min(v for v in [mc_low_helius, mc_low_kline] if v is not None) if any([mc_low_helius, mc_low_kline]) else None
                    else:
                        # Sem Helius: usar kline
                        mc_low = mc_low_kline

                    if mc_low and mc_low > 0 and mc_low < state["mc_min"]:
                            candle_dt = datetime.fromtimestamp(candle_ts, tz=timezone.utc)
                            with self._lock:
                                inner = self._tokens.get(mint)
                                if inner and mc_low < inner["mc_min"]:
                                    inner["mc_min"]   = mc_low
                                    inner["time_min"] = elapsed_min
                                    inner["ts_min"]   = candle_dt
                            db.update_bottom(mint, mc_low, elapsed_min)
                            logger.info("Fundo(kline) %s: $%.0f em %.1fmin (%.0f%% abaixo ATH)",
                                        state["symbol"], mc_low, elapsed_min,
                                        (1 - mc_low / state["mc_migration"]) * 100)
                            state["mc_min"] = mc_low

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
                    #    mc_low para elapsed < BOUNCE_TIME_MIN_SEC (90s) vem APENAS do Helius — pool em transição da bonding curve.
                    #    mc_low para elapsed >= BOUNCE_TIME_MIN_SEC usa MIN(helius, kline) — pool estável.
                    #    mc_low já foi calculado no passo 1, reutiliza aqui.
                    close_p = _float(c.get("close"))
                    open_p  = _float(c.get("open"))
                    high_p  = _float(c.get("high"))
                    vol     = _float(c.get("volume"))
                    supply  = state["total_supply"]
                    if close_p and close_p > 0 and supply:
                        # mc_low já foi calculado no passo 1 (com Helius + kline logic)
                        mc_low_tick = (mc_low if elapsed_sec < 90
                                       else (low_p * supply if (low_p and low_p > 0) else None))
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
                time.sleep(wait)
            except Exception as e:
                logger.exception("Scanner erro: %s", e)
            time.sleep(POLL_INTERVAL)

    def _tick(self):
        data = self.gmgn.get_trenches(types=["near_completion", "completed"])

        # near_completion: ignora tokens já conhecidos sem chamar _safe (sem sleep)
        for token in data.get("pump", []):
            mint = token.get("address")
            with self._lock:
                if mint and (mint in self._watchlist or mint in self._seen):
                    continue
            self._safe(self._handle_near_completion, token)

        # completed: tokens já vistos → apenas push_mc (sem sleep)
        #            tokens novos → _safe com stagger normal
        for token in data.get("completed", []):
            mint = token.get("address")
            mc   = _float(token.get("usd_market_cap")) or _float(token.get("market_cap"))
            with self._lock:
                already_seen = bool(mint and mint in self._seen)
            if already_seen:
                if mint and mc:
                    self.hot.push_mc(mint, mc, token)
            else:
                self._safe(self._handle_completed, token)
                if mint and mc and F_MC_MIN <= mc <= F_MC_MAX:
                    self.hot.push_mc(mint, mc, token)

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

    def _safe(self, fn, token: dict):
        try:
            fn(token)
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

        db.upsert_watchlist(entry)
        logger.info("Watchlist +%s (%s) bc=%.0f%% smart=%s rug=%.0f%%",
                    token.get("symbol"), mint[:8], bc,
                    entry["smart_wallets"], (entry["rug_ratio"] or 0) * 100)

    # --------------------------------------------------------------------------
    # Migração — MC e qualidade do trenches, zero chamadas de API
    # --------------------------------------------------------------------------

    def _handle_completed(self, token: dict):
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

        if not watchlist_entry:
            ok, motivo = _passes_filter(token)
            if not ok:
                logger.info("Migracao descartada %s (%s): %s",
                            token.get("symbol"), mint[:8], motivo)
                with self._lock:
                    self._seen.discard(mint)
                return

        mc       = _float(token.get("usd_market_cap")) or _float(token.get("market_cap"))
        liq      = _float(token.get("liquidity")) or 0
        exchange = (token.get("exchange") or "").lower()

        if F_PUMP_ONLY and "pump" not in exchange:
            logger.info("Migracao descartada %s (%s): exchange=%s (FILTER_PUMP_ONLY=true)",
                        token.get("symbol"), mint[:8], exchange or "unknown")
            with self._lock:
                self._seen.discard(mint)
            return

        if not mc or mc < F_MC_MIN or mc > F_MC_MAX:
            motivo = (f"mc=${mc:.0f}<${F_MC_MIN:.0f}" if mc and mc < F_MC_MIN
                      else f"mc=${mc:.0f}>${F_MC_MAX:.0f}" if mc else "mc=0")
            logger.info("Migracao descartada %s (%s): %s",
                        token.get("symbol"), mint[:8], motivo)
            with self._lock:
                self._seen.discard(mint)
            return

        if liq < F_LIQ_MIN:
            logger.info("Migracao descartada %s (%s): liq=$%.0f<$%.0f",
                        token.get("symbol"), mint[:8], liq, F_LIQ_MIN)
            with self._lock:
                self._seen.discard(mint)
            return

        fee = _float(token.get("total_fee")) or 0
        if fee < F_FEE_MIN:
            logger.info("Migracao descartada %s (%s): total_fee=%.4f<%.1f SOL",
                        token.get("symbol"), mint[:8], fee, F_FEE_MIN)
            with self._lock:
                self._seen.discard(mint)
            return

        # Token passou todos os filtros — agora remove da watchlist
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

        db.upsert_migration(entry)

        symbol = token.get("symbol") or mint[:8]
        smart  = entry.get("smart_wallets") or 0
        rug    = entry.get("rug_ratio") or 0
        exch   = token.get("exchange") or "?"

        send_alert(
            f"\U0001f514 *Migracao: {symbol}*\n"
            f"MC oficial: ${mc_migration:,.0f} ({src}) | Liq: ${liq:,.0f}\n"
            f"Exchange: {exch}\n"
            f"Smart: {smart} | Rug: {rug:.0%}"
        )
        delay_str = f" delay={capture_delay_sec}s" if capture_delay_sec is not None else ""
        logger.info("Migracao %s (%s) MC_oficial=$%.0f[%s] MC_obs=$%.0f[%s] liq=$%.0f exch=%s smart=%d%s",
                    symbol, mint[:8], mc_migration, src, mc_observed, src_observed, liq, exch, smart, delay_str)

        self.hot.add(mint, mc_migration, ts_mig_real, symbol, total_supply, exchange=token.get("exchange"))


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
