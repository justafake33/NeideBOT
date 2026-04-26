import logging
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


# ==============================================================================
# HotMonitor
# ==============================================================================

class HotMonitor:
    """
    Loop de 30s que monitora todos os tokens migrados na janela quente.
    0-60 min: poll a cada 30s, atualiza mc_bottom.
    Saida: captura mc_1h e agenda 4h/24h.
    """

    def __init__(self, gmgn: GmgnClient, scheduler: HeapScheduler):
        self.gmgn = gmgn
        self.scheduler = scheduler
        self._tokens: dict = {}
        self._cancelled: set = set()
        self._lock = threading.Lock()

    def add(self, mint: str, mc_migration: float, ts_migration: datetime, symbol: str):
        with self._lock:
            if mint not in self._tokens and mint not in self._cancelled:
                self._tokens[mint] = {
                    "mc_migration": mc_migration,
                    "ts_migration": ts_migration,
                    "symbol": symbol,
                    "mc_min": mc_migration,
                    "time_min": 0.0,
                }

    def cancel(self, mint: str):
        with self._lock:
            self._cancelled.add(mint)
            self._tokens.pop(mint, None)

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
            elapsed_min = elapsed_sec / 60

            if elapsed_min >= HOT_WINDOW_MIN:
                to_graduate.append((mint, state, elapsed_sec))
                continue

            self._poll_token(mint, state, elapsed_min)
            time.sleep(0.5)

        for mint, state, elapsed_sec in to_graduate:
            with self._lock:
                self._tokens.pop(mint, None)
            self._graduate(mint, state, elapsed_sec)

    def _poll_token(self, mint: str, state: dict, elapsed_min: float):
        try:
            info = self.gmgn.get_token_info(mint)
            if not info:
                return
            mc = _calc_mc(info)
            if not mc:
                return
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
        except RateLimitError as e:
            wait = max(1.0, e.reset_at - time.time()) + 1
            logger.warning("Rate limit hot monitor, aguardando %.1fs", wait)
            time.sleep(wait)
        except Exception as e:
            logger.warning("Erro poll %s: %s", mint[:8], e)

    def _graduate(self, mint: str, state: dict, elapsed_sec: float):
        symbol = state["symbol"]
        mc_migration = state["mc_migration"]
        try:
            info = self.gmgn.get_token_info(mint)
            if info:
                mc = _calc_mc(info)
                if mc:
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
                                        mint, mc_migration, label, symbol)
        logger.info("%s saiu da janela quente", symbol)

    def _checkpoint(self, mint: str, mc_migration: float, label: str, symbol: str):
        if mint in self._cancelled:
            return
        try:
            info = self.gmgn.get_token_info(mint)
            if not info:
                return
            mc = _calc_mc(info)
            if not mc:
                return
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
    """
    Poll do trenches a cada 30s.

    near_completion (data['pump']):
        Pre-carrega qualidade diretamente do response do trenches (100+ campos).
        Sem chamada extra de API — zero delay na watchlist.

    completed (data['completed']):
        Usa qualidade pre-carregada da watchlist (zero delay).
        Chama token_info apenas para mc_migration e campos ausentes no trenches.
        Entrega ao HotMonitor.
    """

    def __init__(self, gmgn: GmgnClient, hot: HotMonitor):
        self.gmgn = gmgn
        self.hot = hot
        self._watchlist: dict = {}
        self._seen: set = set()
        self._lock = threading.Lock()

    def start(self):
        thread = threading.Thread(target=self._loop, daemon=True, name="scanner")
        thread.start()
        logger.info("Scanner iniciado (poll=%ds)", POLL_INTERVAL)

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
    # Watchlist: extrai qualidade do response do trenches — sem chamada de API extra
    # --------------------------------------------------------------------------

    def _handle_near_completion(self, token: dict):
        mint = token.get("address")
        if not mint:
            return
        with self._lock:
            if mint in self._watchlist or mint in self._seen:
                return

        bc = float(token.get("bc_progress") or 0)
        if bc < 90:
            return

        now = datetime.now(timezone.utc)
        # Todos os campos de qualidade vem do proprio response do trenches.
        # Nenhuma chamada de API adicional — zero delay na watchlist.
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
            "liq_migration":            _float(token.get("liquidity"), divisor=2),
            "is_honeypot":              bool(token.get("is_honeypot")) if token.get("is_honeypot") is not None else None,
        }

        with self._lock:
            self._watchlist[mint] = entry

        db.upsert_watchlist(entry)
        logger.info("Watchlist +%s (%s) bc=%.0f%% smart=%s wash=%s",
                    token.get("symbol"), mint[:8], bc,
                    entry["smart_wallets"], entry["is_wash_trading"])

    # --------------------------------------------------------------------------
    # Migracao: usa dados pre-carregados + 1 chamada para mc_migration
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

        # Unica chamada de API na migracao: pegar mc_migration e campos ausentes no trenches
        info = self.gmgn.get_token_info(mint)
        if not info:
            with self._lock:
                self._seen.discard(mint)
            return

        mc = _calc_mc(info)
        if not mc:
            with self._lock:
                self._seen.discard(mint)
            return

        now = datetime.now(timezone.utc)

        # Base: campos do token_info (post-migration, tem pool)
        wts  = info.get("wallet_tags_stat") or {}
        stat = info.get("stat") or {}
        quality = {
            "smart_wallets":     _int(wts.get("smart_wallets")),
            "renowned_wallets":  _int(wts.get("renowned_wallets")),
            "sniper_wallets":    _int(wts.get("sniper_wallets")),
            "rug_ratio":         _float(stat.get("top_bundler_trader_percentage")),
            "top10_holder_rate": _float(stat.get("top_10_holder_rate")),
            "holders_count":     _int(info.get("holder_count")),
            "volume_1m":         _float(stat.get("volume_1m")),
            "volume_5m":         _float(stat.get("volume_5m")),
            "volume_15m":        _float(stat.get("volume_15m")),
            "liq_migration":     _float(info.get("liquidity"), divisor=2),
            "is_wash_trading":   bool(token.get("is_wash_trading")),
            "is_honeypot":       None,
        }

        # Watchlist tem prioridade: snapshot ANTES do dump = mais limpo
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
            **quality,
        }

        db.upsert_migration(entry)

        symbol = token.get("symbol") or mint[:8]
        smart  = entry.get("smart_wallets") or 0
        rug    = entry.get("rug_ratio") or 0
        wash   = "⚠️ SIM" if entry.get("is_wash_trading") else "nao"

        send_alert(
            f"\U0001f514 *Migracao: {symbol}*\n"
            f"MC: ${mc:,.0f}\n"
            f"Smart wallets: {smart}\n"
            f"Wash trading: {wash}\n"
            f"Rug ratio: {rug:.0%}"
        )
        logger.info("Migracao %s (%s) MC=$%.0f smart=%d",
                    symbol, mint[:8], mc, smart)

        self.hot.add(mint, mc, now, symbol)


# ==============================================================================
# Helpers
# ==============================================================================

def _calc_mc(info: dict) -> Optional[float]:
    try:
        return float(info["price"]) * float(info["circulating_supply"])
    except Exception:
        return None


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
