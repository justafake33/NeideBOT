"""Storage helpers do GMGN Trending Radar.

Isolado de `db.py` para reduzir conflito com o arquivo central do projeto.
"""

from __future__ import annotations

from psycopg2.extras import execute_values as _execute_values

import db
# ==============================================================================
# GMGN Trending Radar — research-only, sem execução de trades
# ==============================================================================

def ensure_trending_radar_schema() -> None:
    """Cria tabelas do radar de trending sem depender do init_db completo."""
    with db._cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gmgn_trending_snapshots (
                id                         BIGSERIAL PRIMARY KEY,
                ts_snapshot                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                chain                      TEXT NOT NULL,
                interval_label             TEXT NOT NULL,
                order_by                   TEXT,
                direction                  TEXT,
                filters_json               TEXT,
                platforms_json             TEXT,
                rank_position              INT,
                mint                       TEXT NOT NULL,
                symbol                     TEXT,
                name                       TEXT,
                creator                    TEXT,
                launchpad_platform         TEXT,
                exchange                   TEXT,
                open_timestamp             BIGINT,
                creation_timestamp         BIGINT,
                hot_level                  FLOAT,
                price                      FLOAT,
                market_cap                 FLOAT,
                liquidity                  FLOAT,
                volume                     FLOAT,
                swaps                      INT,
                buys                       INT,
                sells                      INT,
                holder_count               INT,
                smart_degen_count          INT,
                renowned_count             INT,
                bluechip_owner_percentage  FLOAT,
                rug_ratio                  FLOAT,
                is_wash_trading            BOOL,
                bundler_rate               FLOAT,
                rat_trader_amount_rate     FLOAT,
                top10_holder_rate          FLOAT,
                top70_sniper_hold_rate     FLOAT,
                sniper_count               INT,
                bot_degen_rate             FLOAT,
                dexscr_ad                  BOOL,
                dexscr_trending_bar        BOOL,
                dexscr_boost_fee           FLOAT,
                cto_flag                   BOOL,
                raw_json                   TEXT,
                created_at                 TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gmgn_trending_snapshots_mint_ts ON gmgn_trending_snapshots (mint, ts_snapshot)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gmgn_trending_snapshots_query ON gmgn_trending_snapshots (chain, interval_label, ts_snapshot)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gmgn_trending_snapshots_rank ON gmgn_trending_snapshots (chain, interval_label, rank_position)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS gmgn_trending_coverage (
                id                         BIGSERIAL PRIMARY KEY,
                snapshot_id                BIGINT UNIQUE REFERENCES gmgn_trending_snapshots(id) ON DELETE CASCADE,
                mint                       TEXT NOT NULL,
                ts_snapshot                TIMESTAMPTZ NOT NULL,
                scanner_status             TEXT,
                reject_reason              TEXT,
                seen_in_migrations         BOOL DEFAULT FALSE,
                seen_in_bounce_signals     BOOL DEFAULT FALSE,
                seen_in_trades             BOOL DEFAULT FALSE,
                migration_age_sec          INT,
                entry_mc                   FLOAT,
                outcome_window_min         INT,
                take_profit_pct            FLOAT,
                stop_loss_pct              FLOAT,
                max_runup_pct              FLOAT,
                max_drawdown_pct           FLOAT,
                first_tp_ts                TIMESTAMPTZ,
                first_sl_ts                TIMESTAMPTZ,
                shadow_win                 BOOL,
                shadow_outcome             TEXT,
                notes                      TEXT,
                created_at                 TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gmgn_trending_coverage_mint_ts ON gmgn_trending_coverage (mint, ts_snapshot)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gmgn_trending_coverage_status ON gmgn_trending_coverage (scanner_status, reject_reason)")


def insert_trending_snapshots(rows: list[dict]) -> list[int]:
    """Insere snapshot de trending e retorna ids criados."""
    ensure_trending_radar_schema()
    if not rows:
        return []
    import json as _json
    payload = []
    for r in rows:
        payload.append({
            **r,
            "filters_json": _json.dumps(r.get("filters") or [], ensure_ascii=False),
            "platforms_json": _json.dumps(r.get("platforms") or [], ensure_ascii=False),
            "raw_json": _json.dumps(r.get("raw") or {}, ensure_ascii=False, default=str),
        })
    with db._cursor() as cur:
        inserted = _execute_values(cur, """
            INSERT INTO gmgn_trending_snapshots (
                ts_snapshot, chain, interval_label, order_by, direction,
                filters_json, platforms_json, rank_position, mint, symbol, name,
                creator, launchpad_platform, exchange, open_timestamp, creation_timestamp,
                hot_level, price, market_cap, liquidity, volume, swaps, buys, sells,
                holder_count, smart_degen_count, renowned_count, bluechip_owner_percentage,
                rug_ratio, is_wash_trading, bundler_rate, rat_trader_amount_rate,
                top10_holder_rate, top70_sniper_hold_rate, sniper_count, bot_degen_rate,
                dexscr_ad, dexscr_trending_bar, dexscr_boost_fee, cto_flag, raw_json
            ) VALUES %s
            RETURNING id
        """, payload, template="""(
                %(ts_snapshot)s, %(chain)s, %(interval_label)s, %(order_by)s, %(direction)s,
                %(filters_json)s, %(platforms_json)s, %(rank_position)s, %(mint)s, %(symbol)s, %(name)s,
                %(creator)s, %(launchpad_platform)s, %(exchange)s, %(open_timestamp)s, %(creation_timestamp)s,
                %(hot_level)s, %(price)s, %(market_cap)s, %(liquidity)s, %(volume)s, %(swaps)s, %(buys)s, %(sells)s,
                %(holder_count)s, %(smart_degen_count)s, %(renowned_count)s, %(bluechip_owner_percentage)s,
                %(rug_ratio)s, %(is_wash_trading)s, %(bundler_rate)s, %(rat_trader_amount_rate)s,
                %(top10_holder_rate)s, %(top70_sniper_hold_rate)s, %(sniper_count)s, %(bot_degen_rate)s,
                %(dexscr_ad)s, %(dexscr_trending_bar)s, %(dexscr_boost_fee)s, %(cto_flag)s, %(raw_json)s
            )""", fetch=True)
        return [row["id"] for row in inserted]


def _trending_reject_reason(snapshot: dict, migration: dict | None, cfg: dict) -> str:
    """Inferência leve: por que um trending token pode não ter entrado no funil."""
    if not migration:
        return "not_in_migrations"
    exchange = (migration.get("exchange") or snapshot.get("exchange") or "").lower()
    if cfg.get("pump_only") and "pump" not in exchange:
        return "not_pump_exchange"
    age = migration.get("migration_age_sec")
    if age is not None and age > cfg.get("max_mig_age_sec", 600):
        return "old_migration_at_snapshot"
    mc = migration.get("mc_migration") or snapshot.get("market_cap")
    if not mc:
        return "mc_missing"
    if mc < cfg.get("mc_min", 0):
        return "mc_low"
    if mc > cfg.get("mc_max", 10**12):
        return "mc_high"
    liq = migration.get("liq_migration") or snapshot.get("liquidity") or 0
    if liq < cfg.get("liq_min", 0):
        return "liq_low"
    fee = migration.get("total_fee_sol") or 0
    if fee and fee < cfg.get("fee_min", 0):
        return "fee_low"
    if snapshot.get("is_wash_trading") or migration.get("is_wash_trading"):
        return "wash_trading"
    rug = migration.get("rug_ratio") if migration.get("rug_ratio") is not None else snapshot.get("rug_ratio")
    if rug is not None and rug > cfg.get("rug_max", 1):
        return "rug_high"
    sniper = migration.get("top70_sniper_hold_rate") if migration.get("top70_sniper_hold_rate") is not None else snapshot.get("top70_sniper_hold_rate")
    if sniper is not None and sniper > cfg.get("sniper_max", 1):
        return "sniper_high"
    rat = migration.get("rat_trader_rate") if migration.get("rat_trader_rate") is not None else snapshot.get("rat_trader_amount_rate")
    if rat is not None and rat > cfg.get("rat_max", 1):
        return "rat_high"
    smart = migration.get("smart_wallets") if migration.get("smart_wallets") is not None else snapshot.get("smart_degen_count")
    if (smart or 0) < cfg.get("smart_min", 0):
        return "smart_low"
    return "eligible_or_rejected_after_signal"


def build_trending_coverage(snapshot_ids: list[int], outcome_window_min: int = 60,
                            take_profit_pct: float = 0.75, stop_loss_pct: float = 0.25,
                            cfg: dict | None = None) -> dict:
    """Cria coverage + outcome shadow para snapshots recém-coletados.

    Entrada hipotética: market_cap do snapshot trending. WR shadow: TP é atingido
    antes de SL dentro da janela usando token_ticks disponíveis. Se não houver ticks,
    outcome fica `no_ticks` — isso mede cobertura, não performance.
    """
    if not snapshot_ids:
        return {"snapshots": 0, "with_ticks": 0, "wins": 0, "losses": 0}
    cfg = cfg or {}
    summary = {"snapshots": 0, "with_ticks": 0, "wins": 0, "losses": 0, "ambiguous": 0, "open": 0, "no_ticks": 0, "no_entry_mc": 0}
    with db._cursor() as cur:
        cur.execute("""
            SELECT s.*,
                   m.ts_migration, m.mc_migration, m.liq_migration, m.exchange AS migration_exchange,
                   m.total_fee_sol, m.smart_wallets, m.rug_ratio AS migration_rug_ratio,
                   m.top70_sniper_hold_rate AS migration_top70_sniper_hold_rate,
                   m.rat_trader_rate, m.is_wash_trading AS migration_is_wash_trading,
                   EXTRACT(EPOCH FROM (s.ts_snapshot - m.ts_migration))::INT AS migration_age_sec,
                   (b.id IS NOT NULL) AS seen_in_bounce_signals,
                   EXISTS (SELECT 1 FROM trades t WHERE t.mint = s.mint) AS seen_in_trades
              FROM gmgn_trending_snapshots s
              LEFT JOIN migrations m ON m.mint = s.mint
              LEFT JOIN bounce_signals b ON b.mint = s.mint
             WHERE s.id = ANY(%s)
             ORDER BY s.id
        """, (snapshot_ids,))
        snapshots = [dict(r) for r in cur.fetchall()]

        for s in snapshots:
            summary["snapshots"] += 1
            migration = None
            if s.get("ts_migration"):
                migration = {
                    "mc_migration": s.get("mc_migration"),
                    "liq_migration": s.get("liq_migration"),
                    "exchange": s.get("migration_exchange"),
                    "total_fee_sol": s.get("total_fee_sol"),
                    "smart_wallets": s.get("smart_wallets"),
                    "rug_ratio": s.get("migration_rug_ratio"),
                    "top70_sniper_hold_rate": s.get("migration_top70_sniper_hold_rate"),
                    "rat_trader_rate": s.get("rat_trader_rate"),
                    "is_wash_trading": s.get("migration_is_wash_trading"),
                    "migration_age_sec": s.get("migration_age_sec"),
                }
            snapshot_for_reason = {
                "exchange": s.get("exchange"),
                "market_cap": s.get("market_cap"),
                "liquidity": s.get("liquidity"),
                "is_wash_trading": s.get("is_wash_trading"),
                "rug_ratio": s.get("rug_ratio"),
                "top70_sniper_hold_rate": s.get("top70_sniper_hold_rate"),
                "rat_trader_amount_rate": s.get("rat_trader_amount_rate"),
                "smart_degen_count": s.get("smart_degen_count"),
            }
            reject_reason = _trending_reject_reason(snapshot_for_reason, migration, cfg)
            seen_migration = bool(migration)
            seen_bounce = bool(s.get("seen_in_bounce_signals"))
            seen_trade = bool(s.get("seen_in_trades"))
            if seen_trade:
                scanner_status = "traded"
            elif seen_bounce:
                scanner_status = "bounce_signaled"
            elif seen_migration:
                scanner_status = "migrated_only"
            else:
                scanner_status = "not_seen"

            entry_mc = s.get("market_cap")
            first_tp_ts = first_sl_ts = None
            max_runup = max_drawdown = None
            shadow_win = None
            shadow_outcome = "no_entry_mc" if not entry_mc else "no_ticks"
            if not entry_mc:
                summary["no_entry_mc"] += 1
            if entry_mc and entry_mc > 0:
                cur.execute("""
                    SELECT tick_ts, COALESCE(mc_high, mc, mc_open, mc_low) AS mc_high_eff,
                           COALESCE(mc_low, mc, mc_open, mc_high) AS mc_low_eff,
                           COALESCE(mc, mc_high, mc_low, mc_open) AS mc_eff
                      FROM token_ticks
                     WHERE mint = %(mint)s
                       AND tick_ts >= %(ts)s
                       AND tick_ts <= %(ts)s + (%(window)s || ' minutes')::INTERVAL
                     ORDER BY tick_ts ASC
                """, {"mint": s["mint"], "ts": s["ts_snapshot"], "window": outcome_window_min})
                ticks = [dict(r) for r in cur.fetchall()]
                if ticks:
                    summary["with_ticks"] += 1
                    hi = max((r.get("mc_high_eff") or 0) for r in ticks)
                    lo_vals = [r.get("mc_low_eff") for r in ticks if r.get("mc_low_eff")]
                    lo = min(lo_vals) if lo_vals else None
                    max_runup = (hi / entry_mc) - 1 if hi else None
                    max_drawdown = (lo / entry_mc) - 1 if lo else None
                    tp_level = entry_mc * (1 + take_profit_pct)
                    sl_level = entry_mc * (1 - stop_loss_pct)
                    for r in ticks:
                        if first_tp_ts is None and (r.get("mc_high_eff") or 0) >= tp_level:
                            first_tp_ts = r["tick_ts"]
                        if first_sl_ts is None and (r.get("mc_low_eff") or 0) <= sl_level:
                            first_sl_ts = r["tick_ts"]
                        if first_tp_ts is not None or first_sl_ts is not None:
                            break
                    if first_tp_ts and first_sl_ts and first_tp_ts == first_sl_ts:
                        shadow_outcome = "ambiguous_intrabar"
                        summary["ambiguous"] += 1
                    elif first_tp_ts and (not first_sl_ts or first_tp_ts < first_sl_ts):
                        shadow_win = True
                        shadow_outcome = "tp_first"
                        summary["wins"] += 1
                    elif first_sl_ts:
                        shadow_win = False
                        shadow_outcome = "sl_first"
                        summary["losses"] += 1
                    else:
                        shadow_outcome = "open_window"
                        summary["open"] += 1
                else:
                    summary["no_ticks"] += 1

            cur.execute("""
                INSERT INTO gmgn_trending_coverage (
                    snapshot_id, mint, ts_snapshot, scanner_status, reject_reason,
                    seen_in_migrations, seen_in_bounce_signals, seen_in_trades,
                    migration_age_sec, entry_mc, outcome_window_min, take_profit_pct,
                    stop_loss_pct, max_runup_pct, max_drawdown_pct, first_tp_ts,
                    first_sl_ts, shadow_win, shadow_outcome
                ) VALUES (
                    %(snapshot_id)s, %(mint)s, %(ts_snapshot)s, %(scanner_status)s, %(reject_reason)s,
                    %(seen_in_migrations)s, %(seen_in_bounce_signals)s, %(seen_in_trades)s,
                    %(migration_age_sec)s, %(entry_mc)s, %(outcome_window_min)s, %(take_profit_pct)s,
                    %(stop_loss_pct)s, %(max_runup_pct)s, %(max_drawdown_pct)s, %(first_tp_ts)s,
                    %(first_sl_ts)s, %(shadow_win)s, %(shadow_outcome)s
                )
                ON CONFLICT (snapshot_id) DO UPDATE SET
                    scanner_status = EXCLUDED.scanner_status,
                    reject_reason = EXCLUDED.reject_reason,
                    seen_in_migrations = EXCLUDED.seen_in_migrations,
                    seen_in_bounce_signals = EXCLUDED.seen_in_bounce_signals,
                    seen_in_trades = EXCLUDED.seen_in_trades,
                    migration_age_sec = EXCLUDED.migration_age_sec,
                    entry_mc = EXCLUDED.entry_mc,
                    max_runup_pct = EXCLUDED.max_runup_pct,
                    max_drawdown_pct = EXCLUDED.max_drawdown_pct,
                    first_tp_ts = EXCLUDED.first_tp_ts,
                    first_sl_ts = EXCLUDED.first_sl_ts,
                    shadow_win = EXCLUDED.shadow_win,
                    shadow_outcome = EXCLUDED.shadow_outcome
            """, {
                "snapshot_id": s["id"],
                "mint": s["mint"],
                "ts_snapshot": s["ts_snapshot"],
                "scanner_status": scanner_status,
                "reject_reason": reject_reason,
                "seen_in_migrations": seen_migration,
                "seen_in_bounce_signals": seen_bounce,
                "seen_in_trades": seen_trade,
                "migration_age_sec": s.get("migration_age_sec"),
                "entry_mc": entry_mc,
                "outcome_window_min": outcome_window_min,
                "take_profit_pct": take_profit_pct,
                "stop_loss_pct": stop_loss_pct,
                "max_runup_pct": max_runup,
                "max_drawdown_pct": max_drawdown,
                "first_tp_ts": first_tp_ts,
                "first_sl_ts": first_sl_ts,
                "shadow_win": shadow_win,
                "shadow_outcome": shadow_outcome,
            })
    return summary


def get_trending_radar_summary(hours: int = 24) -> dict:
    """Resumo simples para avaliar se há edge suficiente para aprofundar."""
    ensure_trending_radar_schema()
    with db._cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) AS snapshots,
                   COUNT(*) FILTER (WHERE c.seen_in_migrations) AS seen_migrations,
                   COUNT(*) FILTER (WHERE c.seen_in_bounce_signals) AS seen_bounces,
                   COUNT(*) FILTER (WHERE c.seen_in_trades) AS seen_trades,
                   COUNT(*) FILTER (WHERE c.shadow_outcome IN ('tp_first','sl_first')) AS resolved,
                   COUNT(*) FILTER (WHERE c.shadow_win IS TRUE) AS wins,
                   COUNT(*) FILTER (WHERE c.shadow_outcome = 'ambiguous_intrabar') AS ambiguous,
                   COUNT(*) FILTER (WHERE c.shadow_outcome = 'no_entry_mc') AS no_entry_mc,
                   AVG(c.max_runup_pct) AS avg_runup,
                   AVG(c.max_drawdown_pct) AS avg_drawdown
              FROM gmgn_trending_snapshots s
              LEFT JOIN gmgn_trending_coverage c ON c.snapshot_id = s.id
             WHERE s.ts_snapshot >= NOW() - (%(hours)s || ' hours')::INTERVAL
        """, {"hours": hours})
        row = dict(cur.fetchone() or {})
        resolved = row.get("resolved") or 0
        wins = row.get("wins") or 0
        row["shadow_wr"] = (wins / resolved) if resolved else None
        cur.execute("""
            SELECT reject_reason, COUNT(*) AS n
              FROM gmgn_trending_coverage
             WHERE ts_snapshot >= NOW() - (%(hours)s || ' hours')::INTERVAL
             GROUP BY reject_reason
             ORDER BY n DESC
             LIMIT 20
        """, {"hours": hours})
        row["reject_reasons"] = [dict(r) for r in cur.fetchall()]
        return row
