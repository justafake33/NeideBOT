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
import time
from datetime import datetime, timezone
from typing import Any

import db
from gmgn_client import GmgnClient, RateLimitError

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


def _collect_trending_interval(radar, interval: str) -> tuple[list[int], int]:
    ts_snapshot = datetime.now(timezone.utc)
    rows_raw = radar.gmgn.get_market_trending(
        chain=radar.chain,
        interval=interval,
        limit=radar.limit,
        order_by=radar.order_by,
        direction=radar.direction,
        filters=radar.filters or None,
        platforms=radar.platforms or None,
    )
    rows = []
    for idx, item in enumerate(rows_raw, start=1):
        if not isinstance(item, dict):
            continue
        normalized = radar._normalize_item(
            item, ts_snapshot=ts_snapshot, interval=interval, idx=idx
        )
        if normalized:
            rows.append(normalized)

    ids = db.insert_trending_snapshots(rows)
    logger.info(
        "Trending %s/%s: rows=%d inserted=%d",
        radar.chain, interval, len(rows), len(ids),
    )
    return ids, len(rows)


class TrendingRadar:
    def __init__(self, gmgn: GmgnClient):
        self.gmgn = gmgn
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

    def _normalize_item(self, item: dict, *, ts_snapshot: datetime,
                        interval: str, idx: int) -> dict | None:
        mint = item.get("address") or item.get("token_address") or item.get("mint")
        if not mint:
            return None
        return {
            "ts_snapshot": ts_snapshot,
            "chain": self.chain,
            "interval_label": interval,
            "order_by": self.order_by,
            "direction": self.direction,
            "filters": self.filters,
            "platforms": self.platforms,
            "rank_position": _int(item.get("rank")) or idx,
            "mint": mint,
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "creator": item.get("creator"),
            "launchpad_platform": item.get("launchpad_platform"),
            "exchange": item.get("exchange"),
            "open_timestamp": _int(item.get("open_timestamp")),
            "creation_timestamp": _int(item.get("creation_timestamp")),
            "hot_level": _float(item.get("hot_level")),
            "price": _float(item.get("price")),
            "market_cap": _float(item.get("market_cap") or item.get("usd_market_cap")),
            "liquidity": _float(item.get("liquidity")),
            "volume": _float(item.get("volume") or item.get("volume_1h") or item.get("volume_24h")),
            "swaps": _int(item.get("swaps") or item.get("swaps_1h") or item.get("swaps_24h")),
            "buys": _int(item.get("buys") or item.get("buys_24h")),
            "sells": _int(item.get("sells") or item.get("sells_24h")),
            "holder_count": _int(item.get("holder_count")),
            "smart_degen_count": _int(item.get("smart_degen_count")),
            "renowned_count": _int(item.get("renowned_count")),
            "bluechip_owner_percentage": _float(item.get("bluechip_owner_percentage")),
            "rug_ratio": _float(item.get("rug_ratio")),
            "is_wash_trading": _bool(item.get("is_wash_trading")),
            "bundler_rate": _float(item.get("bundler_rate") or item.get("bundler_trader_amount_rate")),
            "rat_trader_amount_rate": _float(item.get("rat_trader_amount_rate")),
            "top10_holder_rate": _float(item.get("top_10_holder_rate") or item.get("top10_holder_rate")),
            "top70_sniper_hold_rate": _float(item.get("top70_sniper_hold_rate")),
            "sniper_count": _int(item.get("sniper_count")),
            "bot_degen_rate": _float(item.get("bot_degen_rate")),
            "dexscr_ad": _bool(item.get("dexscr_ad")),
            "dexscr_trending_bar": _bool(item.get("dexscr_trending_bar")),
            "dexscr_boost_fee": _float(item.get("dexscr_boost_fee")),
            "cto_flag": _bool(item.get("cto_flag")),
            "raw": item,
        }

    def run_once_cycle(self) -> dict:
        all_ids: list[int] = []
        totals = {"intervals": 0, "rows": 0, "inserted": 0}
        for interval in self.intervals:
            ids, row_count = _collect_trending_interval(self, interval)
            all_ids.extend(ids)
            totals["intervals"] += 1
            totals["rows"] += row_count
            totals["inserted"] += len(ids)

        coverage = db.build_trending_coverage(
            all_ids,
            outcome_window_min=self.outcome_window_min,
            take_profit_pct=self.take_profit_pct,
            stop_loss_pct=self.stop_loss_pct,
            cfg=self.coverage_cfg,
        )
        summary = {**totals, "coverage": coverage}
        logger.info("Trending coverage: %s", summary)
        return summary

    def start(self) -> None:
        logger.info(
            "TrendingRadar iniciado chain=%s intervals=%s limit=%d order_by=%s platforms=%s filters=%s poll=%ds",
            self.chain, self.intervals, self.limit, self.order_by, self.platforms, self.filters, self.poll_sec,
        )
        while True:
            try:
                self.run_once_cycle()
            except RateLimitError as e:
                wait = max(1.0, e.reset_at - time.time()) + 1
                logger.warning("GMGN rate limit no TrendingRadar; aguardando %.1fs", wait)
                time.sleep(wait)
            except Exception as e:
                logger.exception("TrendingRadar erro: %s", e)
            if self.run_once:
                return
            time.sleep(self.poll_sec)


def main() -> None:
    db.ensure_trending_radar_schema()
    radar = TrendingRadar(GmgnClient())
    radar.start()


if __name__ == "__main__":
    main()
