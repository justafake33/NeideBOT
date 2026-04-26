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

POLL_INTERVAL  = 30   # Scanner: intervalo entre polls do trenches (segundos)
HOT_INTERVAL   = 30   # HotMonitor: intervalo do loop (segundos)
HOT_WINDOW_MIN = 60   # Janela quente: primeiros 60min pós-migração


# ==============================================================================
# HotMonitor — thread única, 30s, rastreia TODOS os tokens migrados
# ==============================================================================

class HotMonitor:
    """
    Loop de 30s que processa todos os tokens na janela quente simultaneamente.

    0–60 min pós-migração: poll a cada 30s, atualiza mc_bottom se MC < mínimo.
    Saída da janela: captura mc_1h e agenda 4h/24h no HeapScheduler.
    Rug (queda >= 80%): cancela o token imediatamente.
    """

    def __init__(self, gmgn: GmgnClient, scheduler: HeapScheduler):
        self.gmgn = gmgn
        self.scheduler = scheduler
        self._tokens: dict = {}    # mint -> state
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
        logger.info("HotMonitor iniciado (loop=%ds, janela=%dmin)",
                    HOT_INTERVAL, HOT_WINDOW_MIN)

    # --------------------------------------------------------------------------

    def _loop(self):
        while True:
            try:
                self._tick()
            except Exception as e:
                logger.exception("HotMonitor erro geral: %s", e)
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

            # Saiu da janela quente → gradua
            if elapsed_min >= HOT_WINDOW_MIN:
                to_graduate.append((mint, state, elapsed_sec))
                continue

            self._poll_token(mint, state, elapsed_min)
            time.sleep(0.5)  # stagger entre tokens para respeitar rate limit

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

            # Rug: queda >= 80% do ATH de migração
            if mc < state["mc_migration"] * 0.20:
                drop_pct = (1 - mc / state["mc_migration"]) * 100
                logger.info("Rug %s: queda de %.0f%%", state["symbol"], drop_pct)
                self.cancel(mint)
                return

            # Novo fundo encontrado
            if mc < state["mc_min"]:
                state["mc_min"] = mc
                state["time_min"] = elapsed_min
                db.update_bottom(mint, mc, elapsed_min)
                logger.info("Fundo %s: $%.0f em %.1fmin (%.0f%% abaixo do ATH)",
                            state["symbol"], mc, elapsed_min,
                            (1 - mc / state["mc_migration"]) * 100)

        except RateLimitError as e:
            wait = max(1.0, e.reset_at - time.time()) + 1
            logger.warning("Rate limit no hot monitor, aguardando %.1fs", wait)
            time.sleep(wait)
        except Exception as e:
            logger.warning("Erro ao pollar %s: %s", mint[:8], e)

    def _graduate(self, mint: str, state: dict, elapsed_sec: float):
        """Token sai da janela quente: captura mc_1h e agenda 4h/24h."""
        symbol = state["symbol"]
        mc_migration = state["mc_migration"]

        # Captura mc_1h no momento da graduação (~60 min)
        try:
            info = self.gmgn.get_token_info(mint)
            if info:
                mc = _calc_mc(info)
                if mc:
                    var = (mc - mc_migration) / mc_migration if mc_migration else None
                    db.update_checkpoint(mint, "1h", mc, var)
                    logger.info("Checkpoint 1h %s: $%.0f (%s)",
                                symbol, mc,
                                f"{var*100:+.1f}%" if var is not None else "N/A")
        except Exception as e:
            logger.warning("Erro checkpoint 1h %s: %s", symbol, e)

        # Agenda 4h e 24h calculando o tempo restante desde agora
        for delay_min, label in [(240, "4h"), (1440, "24h")]:
            remaining_sec = delay_min * 60 - elapsed_sec
            if remaining_sec > 0:
                self.scheduler.schedule(
                    remaining_sec,
                    self._checkpoint,
                    mint, mc_migration, label, symbol,
                )

        logger.info("%s saiu da janela quente após %.0fmin", symbol, elapsed_sec / 60)

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
            logger.info("Checkpoint %s %s: $%.0f (%s)",
                        symbol, label, mc,
                        f"{var*100:+.1f}%" if var is not None else "N/A")
        except Exception as e:
            logger.warning("Erro checkpoint %s %s: %s", symbol, label, e)


# ==============================================================================
# Scanner — poll trenches, gerencia watchlist, dispara HotMonitor
# ==============================================================================

class Scanner:
    """
    Poll do trenches a cada 30s.
    near_completion (data['pump'])  -> Watchlist (pre-coleta de qualidade)
    completed (data['completed'])   -> HotMonitor (monitoramento de fundo)
    """

    def __init__(self, gmgn: GmgnClient, hot: HotMonitor):
        self.gmgn = gmgn
        self.hot = hot
        self._watchlist: dict = {}      # mint -> dados pré-coletados
        self._seen: set = set()         # mints já promovidos ao HotMonitor
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

        for token in data.get("pump", []):       # near_completion
            self._safe(self._handle_near_completion, token)

        for token in data.get("completed", []):  # migrados
            self._safe(self._handle_completed, token)

    def _safe(self, fn, token: dict):
        try:
            fn(token)
            time.sleep(0.3)
        except RateLimitError:
            raise
        except Exception as e:
            logger.warning("%s erro %s: %s",
                           fn.__name__, token.get("address", "?")[:8], e)

    # --------------------------------------------------------------------------
    # Watchlist (bc >= 90%)
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

        info = self.gmgn.get_token_info(mint)
        security = self.gmgn.get_token_security(mint)
        if not info:
            return

        now = datetime.now(timezone.utc)
        entry = {
            "mint": mint,
            "token_nome": token.get("symbol"),
            "ts_detected": now,
            "bc_progress_at_detection": bc,
            "is_wash_trading": bool(token.get("is_wash_trading")),
            **_extract_quality(info, security),
        }

        with self._lock:
            self._watchlist[mint] = entry

        db.upsert_watchlist(entry)
        logger.info("Watchlist +%s (%s) bc=%.0f%%",
                    token.get("symbol"), mint[:8], bc)

    # --------------------------------------------------------------------------
    # Migração detectada
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

        info = self.gmgn.get_token_info(mint)
        if not info:
            return
        mc = _calc_mc(info)
        if not mc:
            return

        now = datetime.now(timezone.utc)

        # Snapshot pré-migração é mais limpo (antes do dump)
        if watchlist_entry:
            quality = {k: watchlist_entry[k] for k in (
                "smart_wallets", "renowned_wallets", "sniper_wallets",
                "rug_ratio", "top10_holder_rate", "holders_count",
                "volume_1m", "volume_5m", "volume_15m",
                "liq_migration", "is_honeypot",
            ) if k in watchlist_entry}
        else:
            security = self.gmgn.get_token_security(mint)
            quality = _extract_quality(info, security)

        entry = {
            "mint": mint,
            "token_nome": token.get("symbol"),
            "ts_detected": watchlist_entry.get("ts_detected") if watchlist_entry else None,
            "bc_progress_at_detection": watchlist_entry.get("bc_progress_at_detection") if watchlist_entry else None,
            "ts_migration": now,
            "mc_migration": mc,
            "is_wash_trading": bool(token.get("is_wash_trading")),
            **quality,
        }

        db.upsert_migration(entry)

        symbol = token.get("symbol") or mint[:8]
        smart  = entry.get("smart_wallets") or 0
        rug    = entry.get("rug_ratio") or 0
        wash   = "⚠️ SIM" if entry.get("is_wash_trading") else "não"

        send_alert(
            f"\U0001f514 *Migração: {symbol}*\n"
            f"MC: ${mc:,.0f}\n"
            f"Smart wallets: {smart}\n"
            f"Wash trading: {wash}\n"
            f"Rug ratio: {rug:.0%}"
        )
        logger.info("Migração %s (%s) MC=$%.0f smart=%d",
                    symbol, mint[:8], mc, smart)

        # Entrega ao HotMonitor — ele assume daqui em diante
        self.hot.add(mint, mc, now, symbol)


# ==============================================================================
# Helpers
# ==============================================================================

def _calc_mc(info: dict) -> Optional[float]:
    try:
        return float(info["price"]) * float(info["circulating_supply"])
    except Exception:
        return None


def _extract_quality(info: dict, security: Optional[dict]) -> dict:
    wts  = info.get("wallet_tags_stat") or {}
    stat = info.get("stat") or {}
    result = {
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
    }
    if security:
        hp = security.get("is_honeypot")
        result["is_honeypot"] = hp in (True, "yes", 1, "1")
    return result


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
