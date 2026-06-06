"""GMGN Trending Radar — research-only / shadow.

Coleta snapshots do endpoint GMGN market/rank e cruza cada token com o funil
atual do NeideBOT. Não envia ordens, não altera scanner/executor e não exige
private key. A ideia é responder:

1. Tokens trending foram vistos pelo NeideBOT?
2. Se foram vistos, pararam onde? migration, bounce, trade?
3. Se tivéssemos usado o snapshot trending como entrada hipotética, teria batido
   TP antes de SL usando os ticks que já possuímos?

Esse worker é propositalmente passivo para validar edge antes de qualquer
adaptação no bot principal.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import db
from gmgn_client import GmgnClient
from trending_radar_core import build_cycle_summary, collect_trending_interval, run_radar_loop

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("trending_radar")


def _csv(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default).strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def _int(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except Exception:
        return None


def _bool(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y", "sim"}
    return bool(v)


class TrendingRadar:
    def __init__(self, gmgn: GmgnClient):
        self.gmgn = gmgn
        self.logger = logger
        self.chain = os.getenv("TRENDING_RADAR_CHAIN", "sol")
        self.intervals = _csv("TRENDING_RADAR_INTERVALS", "1m,5m")
        self.order_by = os.getenv("TRENDING_RADAR_ORDER_BY", "default")
        self.direction = os.getenv("TRENDING_RADAR_DIRECTION", "desc")
        self.limit = int(os.getenv("TRENDING_RADAR_LIMIT", "100"))
        self.poll_sec = int(os.getenv("TRENDING_RADAR_POLL_SEC", "60"))
        self.filters = _csv("TRENDING_RADAR_FILTERS")
        self.platforms = _csv("TRENDING_RADAR_PLATFORMS")
        self.outcome_window_min = int(os.getenv("TRENDING_RADAR_OUTCOME_WINDOW_MIN", "60"))
        self.take_profit_pct = float(os.getenv("TRENDING_RADAR_TP_PCT", "0.75"))
        self.stop_loss_pct = float(os.getenv("TRENDING_RADAR_SL_PCT", "0.25"))
        self.run_once = os.getenv("TRENDING_RADAR_RUN_ONCE", "false").lower() == "true"

        # Replica defaults principais do scanner para classificar coverage.
        self.coverage_cfg = {
            "pump_only": os.getenv("FILTER_PUMP_ONLY", "true").lower() == "true",
            "max_mig_age_sec": int(os.getenv("MAX_MIG_AGE_SEC", "600")),
            "mc_min": float(os.getenv("FILTER_MC_MIN", "5000")),
            "mc_max": float(os.getenv("FILTER_MC_MAX", "200000")),
            "liq_min": float(os.getenv("FILTER_LIQ_MIN", "10000")),
            "fee_min": float(os.getenv("FILTER_FEE_MIN", "1.0")),
            "rug_max": float(os.getenv("FILTER_RUG_MAX", "0.50")),
            "sniper_max": float(os.getenv("FILTER_SNIPER_MAX", "0.10")),
            "rat_max": float(os.getenv("FILTER_RAT_MAX", "0.20")),
            "smart_min": int(os.getenv("FILTER_SMART_MIN", "2")),
        }

    to_float = staticmethod(_float)
    to_int = staticmethod(_int)
    to_bool = staticmethod(_bool)

    def run_once_cycle(self) -> dict:
        all_ids: list[int] = []
        totals = {"intervals": 0, "rows": 0, "inserted": 0}
        for interval in self.intervals:
            ids, row_count = collect_trending_interval(self, interval)
            all_ids.extend(ids)
            totals["intervals"] += 1
            totals["rows"] += row_count
            totals["inserted"] += len(ids)

        return build_cycle_summary(self, all_ids, totals)

    def start(self) -> None:
        run_radar_loop(self)


def main() -> None:
    db.ensure_trending_radar_schema()
    radar = TrendingRadar(GmgnClient())
    radar.start()


if __name__ == "__main__":
    main()
