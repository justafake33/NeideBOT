"""Helpers do GMGN Trending Radar.

Mantém `trending_radar.py` pequeno e reduz conflitos no worker.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import db
from gmgn_client import RateLimitError


def csv_env(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default).strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def coerce_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def coerce_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except Exception:
        return None


def coerce_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "sim"}
    return bool(value)


def normalize_trending_item(radar, item: dict, *, ts_snapshot: datetime,
                            interval: str, idx: int) -> dict | None:
    mint = item.get("address") or item.get("token_address") or item.get("mint")
    if not mint:
        return None
    return {
        "ts_snapshot": ts_snapshot,
        "chain": radar.chain,
        "interval_label": interval,
        "order_by": radar.order_by,
        "direction": radar.direction,
        "filters": radar.filters,
        "platforms": radar.platforms,
        "rank_position": radar.to_int(item.get("rank")) or idx,
        "mint": mint,
        "symbol": item.get("symbol"),
        "name": item.get("name"),
        "creator": item.get("creator"),
        "launchpad_platform": item.get("launchpad_platform"),
        "exchange": item.get("exchange"),
        "open_timestamp": radar.to_int(item.get("open_timestamp")),
        "creation_timestamp": radar.to_int(item.get("creation_timestamp")),
        "hot_level": radar.to_float(item.get("hot_level")),
        "price": radar.to_float(item.get("price")),
        "market_cap": radar.to_float(item.get("market_cap") or item.get("usd_market_cap")),
        "liquidity": radar.to_float(item.get("liquidity")),
        "volume": radar.to_float(item.get("volume") or item.get("volume_1h") or item.get("volume_24h")),
        "swaps": radar.to_int(item.get("swaps") or item.get("swaps_1h") or item.get("swaps_24h")),
        "buys": radar.to_int(item.get("buys") or item.get("buys_24h")),
        "sells": radar.to_int(item.get("sells") or item.get("sells_24h")),
        "holder_count": radar.to_int(item.get("holder_count")),
        "smart_degen_count": radar.to_int(item.get("smart_degen_count")),
        "renowned_count": radar.to_int(item.get("renowned_count")),
        "bluechip_owner_percentage": radar.to_float(item.get("bluechip_owner_percentage")),
        "rug_ratio": radar.to_float(item.get("rug_ratio")),
        "is_wash_trading": radar.to_bool(item.get("is_wash_trading")),
        "bundler_rate": radar.to_float(item.get("bundler_rate") or item.get("bundler_trader_amount_rate")),
        "rat_trader_amount_rate": radar.to_float(item.get("rat_trader_amount_rate")),
        "top10_holder_rate": radar.to_float(item.get("top_10_holder_rate") or item.get("top10_holder_rate")),
        "top70_sniper_hold_rate": radar.to_float(item.get("top70_sniper_hold_rate")),
        "sniper_count": radar.to_int(item.get("sniper_count")),
        "bot_degen_rate": radar.to_float(item.get("bot_degen_rate")),
        "dexscr_ad": radar.to_bool(item.get("dexscr_ad")),
        "dexscr_trending_bar": radar.to_bool(item.get("dexscr_trending_bar")),
        "dexscr_boost_fee": radar.to_float(item.get("dexscr_boost_fee")),
        "cto_flag": radar.to_bool(item.get("cto_flag")),
        "raw": item,
    }


def collect_trending_interval(radar, interval: str) -> tuple[list[int], int]:
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
        normalized = normalize_trending_item(
            radar, item, ts_snapshot=ts_snapshot, interval=interval, idx=idx
        )
        if normalized:
            rows.append(normalized)

    ids = db.insert_trending_snapshots(rows)
    radar.logger.info(
        "Trending %s/%s: rows=%d inserted=%d",
        radar.chain, interval, len(rows), len(ids),
    )
    return ids, len(rows)


def build_cycle_summary(radar, all_ids: list[int], totals: dict) -> dict:
    coverage = db.build_trending_coverage(
        all_ids,
        outcome_window_min=radar.outcome_window_min,
        take_profit_pct=radar.take_profit_pct,
        stop_loss_pct=radar.stop_loss_pct,
        cfg=radar.coverage_cfg,
    )
    summary = {**totals, "coverage": coverage}
    radar.logger.info("Trending coverage: %s", summary)
    return summary


def run_radar_loop(radar) -> None:
    radar.logger.info(
        "TrendingRadar iniciado chain=%s intervals=%s limit=%d order_by=%s platforms=%s filters=%s poll=%ds",
        radar.chain, radar.intervals, radar.limit, radar.order_by,
        radar.platforms, radar.filters, radar.poll_sec,
    )
    while True:
        try:
            radar.run_once_cycle()
        except RateLimitError as e:
            wait = max(1.0, e.reset_at - time.time()) + 1
            radar.logger.warning("GMGN rate limit no TrendingRadar; aguardando %.1fs", wait)
            time.sleep(wait)
        except Exception as e:
            radar.logger.exception("TrendingRadar erro: %s", e)
        if radar.run_once:
            return
        time.sleep(radar.poll_sec)
