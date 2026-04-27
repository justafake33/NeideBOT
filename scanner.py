import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from gmgn_client import GmgnClient, RateLimitError
import db
from scheduler import HeapScheduler
from telegram_bot import send_alert

logger = logging.getLogger(__name__)

POLL_INTERVAL  = 30
HOT_INTERVAL   = 30
HOT_WINDOW_MIN = 60

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
# HotMonitor — tracking de fundo pós-migração via push do Scanner
# ==============================================================================

class HotMonitor:
    def __init__(self, gmgn: GmgnClient, scheduler: HeapScheduler):
        self.gmgn = gmgn
        self.scheduler = scheduler
        self._tokens: dict = {}
        self._cancelled: set = set()
        self._lock = threading.Lock()

    def add(self, mint: str, mc_migration: float, ts_migration: datetime,
            symbol: str, total_supply: float = 1_000_000_000):
        with self._lock:
            if mint not in self._tokens and mint not in self._cancelled:
                self._tokens[mint] = {
                    "mc_migration": mc_migration,
                    "ts_migration": ts_migration,
                    "symbol":       symbol,
                    "mc_min":       mc_migration,
                    "time_min":     0.0,
                    "total_supply": total_supply,
                }

    def cancel(self, mint: str):
        with self._lock:
            self._cancelled.add(mint)
            self._tokens.pop(mint, None)

    def push_mc(self, mint: str, mc: float):
        """
        Chamado pelo Scanner a cada poll (30s) para tokens que ainda aparecem
        na lista completed do trenches. Zero chamadas extra de API.
        """
        with self._lock:
            state = self._tokens.get(mint)
            if not state or mint in self._cancelled:
                return

        elapsed_min = (datetime.now(timezone.utc) - state["ts_migration"]).total_seconds() / 60

        if mc < state["mc_migration"] * 0.20:
            logger.info("Rug %s: queda >=80%%", state["symbol"])
            self.cancel(mint)
            return

        if mc < state["mc_min"]:
            state["mc_min"] = mc
            state["time_min"] = elapsed_min
            db.update_bottom(mint, mc, elapsed_min)
            logger.info("Fundo %s: $%.0f em %.1fmin (%.0f%% abaixo ATH)",
                        state["symbol"], mc, elapsed_min,
                        (1 - mc / state["mc_migration"]) * 100)

    def start(self):
        thread = threading.Thread(target=self._loop, daemon=True, name="hot-monitor")
        thread.start()
        logger.info("HotMonitor iniciado (loop=%ds, janela=%dmin)", HOT_INTERVAL, HOT_WINDOW_MIN)

    def _loop(self):
        while True:
            try:
                self._tick()
            except Exception as e:
                logger.exception("HotMonitor erro: %s", e)
            time.sleep(HOT_INTERVAL)

    def _tick(self):
        """Apenas controla graduação — MC vem via push_mc() do Scanner."""
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

    def _graduate(self, mint: str, state: dict, elapsed_sec: float):
        symbol       = state["symbol"]
        mc_migration = state["mc_migration"]
        total_supply = state["total_supply"]

        # Após 1h token/info pode já estar indexado pelo GMGN
        info = self.gmgn.get_token_info(mint)
        if info:
            try:
                mc  = float(info["price"]) * float(info["circulating_supply"])
                var = (mc - mc_migration) / mc_migration if mc_migration else None
                db.update_checkpoint(mint, "1h", mc, var)
                logger.info("Checkpoint 1h %s: $%.0f (%s)", symbol, mc,
                            f"{var*100:+.1f}%" if var is not None else "N/A")
            except Exception as e:
                logger.warning("Erro checkpoint 1h %s: %s", symbol, e)

        for delay_min, label in [(240, "4h"), (1440, "24h")]:
            remaining = delay_min * 60 - elapsed_sec
            if remaining > 0:
                self.scheduler.schedule(remaining, self._checkpoint,
                                        mint, mc_migration, total_supply, label, symbol)
        logger.info("%s saiu da janela quente (bottom=$%.0f em %.1fmin)",
                    symbol, state["mc_min"], state["time_min"])

    def _checkpoint(self, mint: str, mc_migration: float, total_supply: float,
                    label: str, symbol: str):
        if mint in self._cancelled:
            return
        info = self.gmgn.get_token_info(mint)
        if not info:
            logger.info("Checkpoint %s %s: token/info indisponivel", symbol, label)
            return
        try:
            mc  = float(info["price"]) * float(info["circulating_supply"])
            var = (mc - mc_migration) / mc_migration if mc_migration else None
            db.update_checkpoint(mint, label, mc, var)
            logger.info("Checkpoint %s %s: $%.0f (%s)", symbol, label, mc,
                        f"{var*100:+.1f}%" if var is not None else "N/A")
        except Exception as e:
            logger.warning("Erro checkpoint %s %s: %s", symbol, label, e)


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
        for token in data.get("pump", []):
            self._safe(self._handle_near_completion, token)
        for token in data.get("completed", []):
            self._safe(self._handle_completed, token)
            # Passa MC atualizado ao HotMonitor — trenches já tem market_cap em tempo real
            mint = token.get("address")
            mc   = _float(token.get("usd_market_cap")) or _float(token.get("market_cap"))
            if mint and mc and F_MC_MIN <= mc <= F_MC_MAX:
                self.hot.push_mc(mint, mc)

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

        if capture_delay_sec is not None and capture_delay_sec > 90:
            logger.warning(
                "Migracao %s (%s): MC capturado %ds apos migracao — dado pode estar impreciso",
                token.get("symbol"), mint[:8], capture_delay_sec,
            )
        quality = {
            "smart_wallets":     _int(token.get("smart_degen_count")),
            "renowned_wallets":  _int(token.get("renowned_count")),
            "sniper_wallets":    _int(token.get("sniper_count")),
            "rug_ratio":         _float(token.get("bundler_trader_amount_rate")),
            "top10_holder_rate": _float(token.get("top_10_holder_rate")),
            "holders_count":     _int(token.get("holder_count")),
            "volume_1m":         _float(token.get("volume_1m")),
            "volume_5m":         _float(token.get("volume_5m")),
            "volume_15m":        _float(token.get("volume_15m")),
            "liq_migration":     liq,
            "is_wash_trading":   bool(token.get("is_wash_trading")),
            "is_honeypot":       None,
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
            "mc_migration":             mc,
            "capture_delay_sec":        capture_delay_sec,
            **quality,
        }

        db.upsert_migration(entry)

        symbol = token.get("symbol") or mint[:8]
        smart  = entry.get("smart_wallets") or 0
        rug    = entry.get("rug_ratio") or 0
        exch   = token.get("exchange") or "?"

        send_alert(
            f"\U0001f514 *Migracao: {symbol}*\n"
            f"MC: ${mc:,.0f} | Liq: ${liq:,.0f}\n"
            f"Exchange: {exch}\n"
            f"Smart: {smart} | Rug: {rug:.0%}"
        )
        delay_str = f" delay={capture_delay_sec}s" if capture_delay_sec is not None else ""
        logger.info("Migracao %s (%s) MC=$%.0f liq=$%.0f exch=%s smart=%d%s",
                    symbol, mint[:8], mc, liq, exch, smart, delay_str)

        self.hot.add(mint, mc, now, symbol, total_supply)


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
