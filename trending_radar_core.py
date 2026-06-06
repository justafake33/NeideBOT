"""Helpers do GMGN Trending Radar.

Arquivo separado para manter `trending_radar.py` pequeno e reduzir conflitos no worker.
"""

from __future__ import annotations

from datetime import datetime, timezone

import db


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
        normalized = radar.normalize_item(
            item, ts_snapshot=ts_snapshot, interval=interval, idx=idx
        )
        if normalized:
            rows.append(normalized)

    ids = db.insert_trending_snapshots(rows)
    return ids, len(rows)
