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
HOT_PRECISE_SEC    = int(os.getenv("HOT_PRECISE_SEC",      "60"))   # kline bottom scan
HOT_WINDOW_MIN     = 60

# ------------------------------------------------------------------
# Filtros (configuráveis via env vars no Railway sem redeploy)
# ------------------------------------------------------------------
F_RUG_MAX        = float(os.getenv("FILTER_RUG_MAX",        "0.15"))
F_RAT_MAX        = float(os.getenv("FILTER_RAT_MAX",        "0.20"))
F_TOP10_MAX      = float(os.getenv("FILTER_TOP10_MAX",      "0.80"))
F_SMART_MIN      = int(os.getenv(  "FILTER_SMART_MIN",      "1"))
F_WASH_BLOCK     = os.getenv(      "FILTER_WASH_BLOCK",     "true").lower() == "true"
BC_MIN_WATCHLIST = float(os.getenv("BC_MIN_WATCHLIST",      "80"))
F_MC_MIN         = float(os.getenv("FILTER_MC_MIN",         "5000"))
F_MC_MAX         = float(os.getenv("FILTER_MC_MAX",         "200000"))
F_LIQ_MIN        = float(os.getenv("FILTER_LIQ_MIN",        "10000"))  # liquidez minima em USD
F_FEE_MIN        = float(os.getenv("FILTER_FEE_MIN",        "1.0"))    # total_fee minimo em SOL
MAX_MIG_AGE_SEC  = int(os.getenv(  "MAX_MIG_AGE_SEC",       "600"))    # max 10min desde complete_timestamp
MIGRATION_SOL_UNITS = float(os.getenv("MIGRATION_SOL_UNITS", "410"))


def _passes_filter(token: dict) -> tuple[bool, str]:
    if F_WASH_BLOCK and token.get("is_wash_trading"):
        return False, "wash_trading"

    rug = _float(token.get("rug_ratio"))
    if rug is not None and rug > F_RUG_MAX:
        return False, f"rug_ratio={rug:.0%}>{F_RUG_MAX:.0%}"

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

    def add(self, mint: str, mc_migration: float, ts_migration: datetime,
            symbol: str, total_supply: float = 1_000_000_000):
        with self._lock:
            if mint not in self._tokens and mint not in self._cancelled:
                self._tokens[mint] = {
                    "mc_migration": mc_migration,
                    "ts_mig_unix":  int(ts_migration.timestamp()),
                    "ts_migration": ts_migration,
                    "symbol":       symbol,
                    "mc_min":       mc_migration,
                    "time_min":     0.0,
                    "mc_max":       mc_migration,
                    "time_max":     0.0,
                    "mc_last":      mc_migration,
                    "total_supply": total_supply,
                }

    def cancel(self, mint: str):
        with self._lock:
            self._cancelled.add(mint)
            self._tokens.pop(mint, None)

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
            return

        if mc < state["mc_min"]:
            state["mc_min"] = mc
            state["time_min"] = elapsed_min
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

    BOUNCE_MIN_PCT   = float(os.getenv("BOUNCE_MIN_PCT",   "0.15"))  # bounce mínimo do mc_min
    BOUNCE_DROP_MIN  = float(os.getenv("BOUNCE_DROP_MIN",  "0.30"))  # queda mínima da migração
    BOUNCE_TIME_MAX  = float(os.getenv("BOUNCE_TIME_MAX",  "30.0"))  # janela máxima em minutos

    def _check_bounce(self, mint: str, state: dict, mc: float, elapsed_sec: int):
        """Detecta bounce em tempo real e dispara alerta no Telegram."""
        if state.get("bounce_fired"):
            return

        elapsed_min  = elapsed_sec / 60
        mc_min       = state["mc_min"]
        mc_migration = state["mc_migration"]

        if elapsed_min > self.BOUNCE_TIME_MAX:
            return
        if mc_min <= 0 or mc_migration <= 0:
            return

        drop_from_mig = (mc_migration - mc_min) / mc_migration
        if drop_from_mig < self.BOUNCE_DROP_MIN:
            return

        bounce_pct = (mc - mc_min) / mc_min
        if bounce_pct < self.BOUNCE_MIN_PCT:
            return

        state["bounce_fired"] = True
        symbol = state.get("symbol", mint[:8])

        try:
            db.insert_bounce_signal({
                "mint":         mint,
                "symbol":       symbol,
                "elapsed_sec":  elapsed_sec,
                "mc_migration": mc_migration,
                "mc_min":       mc_min,
                "mc_at_signal": mc,
                "drop_from_mig": round(drop_from_mig, 4),
                "bounce_pct":   round(bounce_pct, 4),
            })
        except Exception as e:
            logger.warning("insert_bounce_signal %s: %s", mint[:8], e)

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
                )
            except Exception as e:
                logger.error("executor.on_bounce %s: %s", symbol, e)

    # ------------------------------------------------------------------
    # Threads
    # ------------------------------------------------------------------

    def start(self):
        threading.Thread(target=self._loop,         daemon=True, name="hot-monitor").start()
        threading.Thread(target=self._precise_loop, daemon=True, name="hot-precise").start()
        threading.Thread(target=self.startup_backfill, daemon=True, name="hot-backfill").start()
        logger.info("HotMonitor iniciado (grad=%ds precise=%ds janela=%dmin)",
                    HOT_INTERVAL, HOT_PRECISE_SEC, HOT_WINDOW_MIN)

    def startup_backfill(self):
        """
        Ao reiniciar, re-popula _tokens para tokens ainda na janela quente e
        reagenda/executa checkpoints ausentes para tokens já graduados.
        Usa total_supply=1B (padrão para tokens pump-style Solana).
        """
        time.sleep(5)  # aguarda scheduler e conexões estabilizarem
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

            # Já passou da janela — backfill de checkpoints ausentes
            for delay_min, label, already_done in [
                (60,   "1h",  row.get("mc_1h")  is not None),
                (240,  "4h",  row.get("mc_4h")  is not None),
                (1440, "24h", row.get("mc_24h") is not None),
            ]:
                if already_done:
                    continue
                target_ts = ts_mig_unix + delay_min * 60
                remaining = target_ts - now.timestamp()
                if remaining <= 0:
                    # Tempo já passou — busca imediata em thread separada
                    threading.Thread(
                        target=self._checkpoint,
                        args=(mint, mc_migration, total_supply, label, symbol, ts_mig_unix),
                        daemon=True,
                    ).start()
                    time.sleep(1.0)  # stagger entre tokens
                else:
                    self.scheduler.schedule(remaining, self._checkpoint,
                                            mint, mc_migration, total_supply,
                                            label, symbol, ts_mig_unix)
                    logger.info("Backfill: agendado %s %s em %.0fmin",
                                symbol, label, remaining / 60)

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
                    from_ts=now_ts - 120,
                    to_ts=now_ts,
                )
                for c in candles:
                    candle_ts   = int(c.get("time", 0)) / 1000
                    elapsed_min = (candle_ts - state["ts_mig_unix"]) / 60
                    if elapsed_min < 0:
                        continue

                    low_price = _float(c.get("low"))
                    if low_price and low_price > 0:
                        mc_low = low_price * state["total_supply"]
                        if mc_low < state["mc_min"]:
                            with self._lock:
                                inner = self._tokens.get(mint)
                                if inner and mc_low < inner["mc_min"]:
                                    inner["mc_min"] = mc_low
                                    inner["time_min"] = elapsed_min
                            db.update_bottom(mint, mc_low, elapsed_min)
                            logger.info("Fundo(kline) %s: $%.0f em %.1fmin (%.0f%% abaixo ATH)",
                                        state["symbol"], mc_low, elapsed_min,
                                        (1 - mc_low / state["mc_migration"]) * 100)
                            state["mc_min"] = mc_low

                    high_price = _float(c.get("high"))
                    if high_price and high_price > 0:
                        mc_high = high_price * state["total_supply"]
                        if mc_high > state["mc_max"]:
                            with self._lock:
                                inner = self._tokens.get(mint)
                                if inner and mc_high > inner["mc_max"]:
                                    inner["mc_max"] = mc_high
                                    inner["time_max"] = elapsed_min
                            db.update_peak(mint, mc_high, elapsed_min)
                            logger.info("Pico(kline)  %s: $%.0f em %.1fmin (+%.0f%% vs mig)",
                                        state["symbol"], mc_high, elapsed_min,
                                        (mc_high / state["mc_migration"] - 1) * 100)
                            state["mc_max"] = mc_high

                # Insere ticks kline com OHLC completo para cada vela
                kline_ticks = []
                for c in candles:
                    candle_ts   = int(c.get("time", 0)) / 1000
                    elapsed_sec = int(candle_ts - state["ts_mig_unix"])
                    if elapsed_sec < 0 or elapsed_sec > 3660:
                        continue
                    close_p = _float(c.get("close"))
                    open_p  = _float(c.get("open"))
                    high_p  = _float(c.get("high"))
                    low_p   = _float(c.get("low"))
                    vol     = _float(c.get("volume"))
                    supply  = state["total_supply"]
                    if not close_p or close_p <= 0 or not supply:
                        continue
                    kline_ticks.append({
                        "mint":        mint,
                        "elapsed_sec": elapsed_sec,
                        "mc":          close_p * supply,
                        "mc_open":     open_p  * supply if open_p  else None,
                        "mc_high":     high_p  * supply if high_p  else None,
                        "mc_low":      low_p   * supply if low_p   else None,
                        "volume_1m":   vol,
                    })
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
                elapsed_min = (candle_ts - ts_mig_unix) / 60
                if elapsed_min < 0:
                    continue
                low = _float(c.get("low"))
                if low and low > 0:
                    db.update_bottom(mint, low * total_supply, elapsed_min)
                high = _float(c.get("high"))
                if high and high > 0:
                    db.update_peak(mint, high * total_supply, elapsed_min)

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

        for delay_min, label in [(240, "4h"), (1440, "24h")]:
            remaining = delay_min * 60 - elapsed_sec
            if remaining > 0:
                self.scheduler.schedule(remaining, self._checkpoint,
                                        mint, mc_migration, total_supply,
                                        label, symbol, ts_mig_unix)

        logger.info("%s saiu da janela quente (bottom=$%.0f em %.1fmin)",
                    symbol, state["mc_min"], state["time_min"])

    def _checkpoint(self, mint: str, mc_migration: float, total_supply: float,
                    label: str, symbol: str, ts_mig_unix: int):
        if mint in self._cancelled:
            return

        delay_sec = {"1h": 3600, "4h": 14400, "24h": 86400}[label]
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
            "filtros: rug<%.0f%% rat<%.0f%% top10<%.0f%% smart>=%d wash=%s",
            POLL_INTERVAL, BC_MIN_WATCHLIST, F_MC_MIN, F_MC_MAX, F_LIQ_MIN, F_FEE_MIN,
            F_RUG_MAX*100, F_RAT_MAX*100, F_TOP10_MAX*100,
            F_SMART_MIN, F_WASH_BLOCK,
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
            watchlist_entry = self._watchlist.pop(mint, None)
            self._seen.add(mint)

        if not watchlist_entry:
            ok, motivo = _passes_filter(token)
            if not ok:
                logger.info("Migracao descartada %s (%s): %s",
                            token.get("symbol"), mint[:8], motivo)
                with self._lock:
                    self._seen.discard(mint)
                return

        mc  = _float(token.get("usd_market_cap")) or _float(token.get("market_cap"))
        liq = _float(token.get("liquidity")) or 0

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

        total_supply      = _float(token.get("total_supply")) or 1_000_000_000
        complete_ts       = int(token.get("complete_timestamp") or 0)
        now               = datetime.now(timezone.utc)
        now_ts            = int(now.timestamp())
        capture_delay_sec = (now_ts - complete_ts) if complete_ts > 0 else None

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
            "rug_ratio":              _float(token.get("bundler_trader_amount_rate")),
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
            "ts_migration":             now,
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

        self.hot.add(mint, mc_migration, now, symbol, total_supply)


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
