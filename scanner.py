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

POLL_INTERVAL = 30  # segundos


class Scanner:
    """
    Fase 1 — Coleta pura, sem execução.

    Watchlist: tokens com bc_progress >= 90% (near_completion -> data['pump'])
    Monitor:   tokens migrados (completed -> data['completed'])

    Bottom tracker: polling a cada 2min por 30min para capturar mc_bottom.
    Checkpoints:    1h, 4h, 24h.
    """

    def __init__(self, gmgn: GmgnClient, scheduler: HeapScheduler):
        self.gmgn = gmgn
        self.scheduler = scheduler
        self._watchlist: dict = {}   # mint -> entry dict pre-coletado
        self._monitoring: set = set()  # mints com timers ativos
        self._cancelled: set = set()   # mints rugados, ignora callbacks pendentes
        self._lock = threading.Lock()

    def start(self):
        thread = threading.Thread(target=self._loop, daemon=True, name="scanner")
        thread.start()
        logger.info("Scanner iniciado (poll=%ds)", POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Loop principal
    # ------------------------------------------------------------------

    def _loop(self):
        while True:
            try:
                self._tick()
            except RateLimitError as e:
                wait = max(1.0, e.reset_at - time.time()) + 1
                logger.warning("Rate limit global, aguardando %.1fs", wait)
                time.sleep(wait)
            except Exception as e:
                logger.exception("Erro no tick do scanner: %s", e)
            time.sleep(POLL_INTERVAL)

    def _tick(self):
        data = self.gmgn.get_trenches(types=["near_completion", "completed"])

        for token in data.get("pump", []):       # near_completion
            self._safe_handle(self._handle_near_completion, token)

        for token in data.get("completed", []):  # migrados
            self._safe_handle(self._handle_completed, token)

    def _safe_handle(self, fn, token: dict):
        try:
            fn(token)
            time.sleep(0.3)  # stagger entre chamadas
        except RateLimitError:
            raise
        except Exception as e:
            mint = token.get("address", "?")[:8]
            logger.warning("%s erro em %s: %s", fn.__name__, mint, e)

    # ------------------------------------------------------------------
    # Watchlist (bc >= 90%)
    # ------------------------------------------------------------------

    def _handle_near_completion(self, token: dict):
        mint = token.get("address")
        if not mint:
            return
        with self._lock:
            if mint in self._watchlist or mint in self._monitoring:
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
            **self._extract_quality(info, security),
        }

        with self._lock:
            self._watchlist[mint] = entry

        db.upsert_watchlist(entry)
        logger.info("Watchlist +%s (%s) bc=%.0f%%",
                    token.get("symbol"), mint[:8], bc)

    # ------------------------------------------------------------------
    # Migração detectada (bc = 99)
    # ------------------------------------------------------------------

    def _handle_completed(self, token: dict):
        mint = token.get("address")
        if not mint:
            return
        with self._lock:
            if mint in self._monitoring:
                return
            watchlist_entry = self._watchlist.pop(mint, None)
            self._monitoring.add(mint)

        info = self.gmgn.get_token_info(mint)
        if not info:
            with self._lock:
                self._monitoring.discard(mint)
            return

        mc = _calc_mc(info)
        if not mc:
            with self._lock:
                self._monitoring.discard(mint)
            return

        now = datetime.now(timezone.utc)

        if watchlist_entry:
            # Usa dados de qualidade pré-coletados (snapshot antes do dump é mais limpo)
            quality = {k: watchlist_entry[k] for k in (
                "smart_wallets", "renowned_wallets", "sniper_wallets",
                "rug_ratio", "top10_holder_rate", "holders_count",
                "volume_1m", "volume_5m", "volume_15m",
                "liq_migration", "is_honeypot",
            ) if k in watchlist_entry}
        else:
            security = self.gmgn.get_token_security(mint)
            quality = self._extract_quality(info, security)

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
        smart = entry.get("smart_wallets") or 0
        wash_lbl = "⚠️ SIM" if entry.get("is_wash_trading") else "não"
        rug = entry.get("rug_ratio") or 0
        send_alert(
            f"🔔 *Migração: {symbol}*\n"
            f"MC: ${mc:,.0f}\n"
            f"Smart wallets: {smart}\n"
            f"Wash trading: {wash_lbl}\n"
            f"Rug ratio: {rug:.0%}"
        )
        logger.info("Migração %s (%s) MC=$%.0f smart=%d wash=%s",
                    symbol, mint[:8], mc, smart, entry.get("is_wash_trading"))

        self._schedule_checkpoints(mint, mc, now, symbol)

    # ------------------------------------------------------------------
    # Agendamento
    # ------------------------------------------------------------------

    def _schedule_checkpoints(self, mint: str, mc_migration: float,
                               ts_migration: datetime, symbol: str):
        # Bottom tracker: a cada 2min por 30min (15 verificações)
        for i in range(1, 16):
            self.scheduler.schedule(
                i * 120,
                self._track_bottom,
                mint, mc_migration, ts_migration,
            )

        # Checkpoints de MC
        for delay_min, label in [(60, "1h"), (240, "4h"), (1440, "24h")]:
            self.scheduler.schedule(
                delay_min * 60,
                self._checkpoint,
                mint, mc_migration, label, symbol,
            )

    # ------------------------------------------------------------------
    # Callbacks do scheduler
    # ------------------------------------------------------------------

    def _track_bottom(self, mint: str, mc_migration: float, ts_migration: datetime):
        if mint in self._cancelled:
            return

        info = self.gmgn.get_token_info(mint)
        if not info:
            return

        mc = _calc_mc(info)
        if not mc:
            return

        # Token rugged: queda >= 80% antes de 1h -> cancela timers
        if mc_migration and mc < mc_migration * 0.20:
            with self._lock:
                self._cancelled.add(mint)
            logger.info("Token %s rugado (queda >=80%%)", mint[:8])
            return

        elapsed_min = (datetime.now(timezone.utc) - ts_migration).total_seconds() / 60
        db.update_bottom(mint, mc, elapsed_min)

    def _checkpoint(self, mint: str, mc_migration: float, label: str, symbol: str):
        if mint in self._cancelled:
            return

        info = self.gmgn.get_token_info(mint)
        if not info:
            return

        mc = _calc_mc(info)
        if not mc:
            return

        var = (mc - mc_migration) / mc_migration if mc_migration else None
        db.update_checkpoint(mint, label, mc, var)

        logger.info("Checkpoint %s %s var=%s",
                    symbol, label, f"{var*100:+.1f}%" if var is not None else "N/A")

        if label == "24h":
            with self._lock:
                self._monitoring.discard(mint)
                self._cancelled.discard(mint)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_quality(info: dict, security: Optional[dict]) -> dict:
        wts = info.get("wallet_tags_stat") or {}
        stat = info.get("stat") or {}

        result = {
            "smart_wallets":    _int(wts.get("smart_wallets")),
            "renowned_wallets": _int(wts.get("renowned_wallets")),
            "sniper_wallets":   _int(wts.get("sniper_wallets")),
            "rug_ratio":        _float(stat.get("top_bundler_trader_percentage")),
            "top10_holder_rate": _float(stat.get("top_10_holder_rate")),
            "holders_count":    _int(info.get("holder_count")),
            "volume_1m":        _float(stat.get("volume_1m")),
            "volume_5m":        _float(stat.get("volume_5m")),
            "volume_15m":       _float(stat.get("volume_15m")),
            "liq_migration":    _float(info.get("liquidity"), divisor=2),
        }

        if security:
            hp = security.get("is_honeypot")
            result["is_honeypot"] = hp in (True, "yes", 1, "1")

        return result


# ------------------------------------------------------------------
# Helpers de conversão (tolerantes a None/str)
# ------------------------------------------------------------------

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
