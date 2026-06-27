"""Storage + análise do GMGN Filtered Trending (research-only).

Hipótese central:
    "Entre os tokens que o scanner já capturou na faixa de migração, aqueles que
     aparecem no GMGN Trending filtrado pela nossa zona operacional têm maior
     chance de bounce."

Este módulo é PASSIVO. Não compra, não vende, não toca executor/scanner/H26/H27.
Apenas:
  1. Persiste labels de "apareceu no GMGN Trending filtrado" (gmgn_scanner_trending_labels)
  2. Calcula outcome a partir de token_ticks (gmgn_scanner_trending_outcomes)
  3. Cruza com migrations/bounce_signals para grupo A (trending) vs grupo B (controle)

Isolado de db.py para reduzir conflito com o arquivo central do projeto.
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any

import db

logger = logging.getLogger("gmgn_filtered_trending")

# Janelas de análise em minutos (Fase 4). 120m é opcional mas sempre coletado.
OUTCOME_WINDOWS_MIN = [5, 15, 30, 60, 120]
_MAX_WINDOW_MIN = max(OUTCOME_WINDOWS_MIN)


# ==============================================================================
# Schema — idempotente, isolado
# ==============================================================================

def ensure_schema() -> None:
    with db._cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gmgn_scanner_trending_labels (
                id                             BIGSERIAL PRIMARY KEY,
                mint                           TEXT NOT NULL,
                ts_seen                        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                profile_name                   TEXT NOT NULL,
                timeframe                      TEXT,
                chain                          TEXT,
                order_by                       TEXT,
                rank_in_filtered_trending      INT,
                symbol                         TEXT,
                market_cap                     FLOAT,
                liquidity                      FLOAT,
                total_fee_sol                  FLOAT,
                volume_window                  FLOAT,
                txs_window                     INT,
                buys_window                    INT,
                sells_window                   INT,
                holders                        INT,
                token_age_minutes              FLOAT,
                -- contexto do universo do scanner no momento em que foi visto
                scanner_status_at_seen         TEXT,
                scanner_mc_at_seen             FLOAT,
                migration_age_sec              INT,
                outside_scanner                BOOL DEFAULT FALSE,
                -- campos derivados
                first_seen_for_mint_profile    BOOL DEFAULT FALSE,
                first_seen_after_migration_sec INT,
                seen_before_bounce             BOOL,
                seen_after_bounce              BOOL,
                raw_json                       TEXT,
                created_at                     TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gsl_mint_ts      ON gmgn_scanner_trending_labels (mint, ts_seen)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gsl_profile_ts   ON gmgn_scanner_trending_labels (profile_name, ts_seen)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gsl_mint_profile ON gmgn_scanner_trending_labels (mint, profile_name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gsl_timeframe_ts ON gmgn_scanner_trending_labels (timeframe, ts_seen)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS gmgn_scanner_trending_outcomes (
                label_id            BIGINT PRIMARY KEY
                                    REFERENCES gmgn_scanner_trending_labels(id) ON DELETE CASCADE,
                mint                TEXT NOT NULL,
                profile_name        TEXT,
                ts_seen             TIMESTAMPTZ NOT NULL,
                entry_mc            FLOAT,
                n_ticks             INT,
                has_ticks           BOOL,
                outcome             TEXT,            -- 'no_ticks' | 'computed'
                matured             BOOL,            -- janela de 120m fechou?
                max_runup_5m        FLOAT,
                max_runup_15m       FLOAT,
                max_runup_30m       FLOAT,
                max_runup_60m       FLOAT,
                max_runup_120m      FLOAT,
                max_drawdown_5m     FLOAT,
                max_drawdown_15m    FLOAT,
                max_drawdown_30m    FLOAT,
                max_drawdown_60m    FLOAT,
                max_drawdown_120m   FLOAT,
                hit_20_before_m20   BOOL,
                hit_30_before_m20   BOOL,
                hit_50_before_m25   BOOL,
                hit_100_before_m30  BOOL,
                time_to_20pct_min   FLOAT,
                time_to_50pct_min   FLOAT,
                time_to_top_min     FLOAT,
                gave_bounce         BOOL,
                bounce_ts           TIMESTAMPTZ,
                trending_vs_bounce  TEXT,            -- 'before' | 'after' | 'no_bounce'
                computed_at         TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gso_mint    ON gmgn_scanner_trending_outcomes (mint)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gso_profile ON gmgn_scanner_trending_outcomes (profile_name)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gso_outcome ON gmgn_scanner_trending_outcomes (outcome)")


# ==============================================================================
# Universo do scanner — migrations recentes (consulta local, sem chamar GMGN)
# ==============================================================================

def get_scanner_universe(window_min: int) -> dict[str, dict]:
    """Mints que o scanner capturou (migrations) dentro da janela recente.

    Retorna {mint: {ts_migration, mc_migration, liq_migration, total_fee_sol,
                    exchange, status, ts_bounce}}.
    status: 'traded' | 'bounce_signaled' | 'migrated_only'.
    """
    out: dict[str, dict] = {}
    with db._cursor() as cur:
        cur.execute("""
            SELECT m.mint, m.ts_migration, m.mc_migration, m.liq_migration,
                   m.total_fee_sol, m.exchange,
                   b.ts_signal AS bounce_ts,
                   (b.mint IS NOT NULL) AS seen_bounce,
                   EXISTS (SELECT 1 FROM trades t WHERE t.mint = m.mint) AS seen_trade
              FROM migrations m
              LEFT JOIN bounce_signals b ON b.mint = m.mint
             WHERE m.ts_migration >= NOW() - (%(win)s || ' minutes')::INTERVAL
        """, {"win": window_min})
        for r in cur.fetchall():
            status = ("traded" if r.get("seen_trade")
                      else "bounce_signaled" if r.get("seen_bounce")
                      else "migrated_only")
            out[r["mint"]] = {
                "ts_migration": r.get("ts_migration"),
                "mc_migration": r.get("mc_migration"),
                "liq_migration": r.get("liq_migration"),
                "total_fee_sol": r.get("total_fee_sol"),
                "exchange": r.get("exchange"),
                "status": status,
                "bounce_ts": r.get("bounce_ts"),
            }
    return out


# ==============================================================================
# Insert de labels — dedup + campos derivados
# ==============================================================================

def insert_labels(rows: list[dict], dedup_sec: int = 600) -> int:
    """Insere labels deduplicando por (mint, profile) dentro de `dedup_sec`.

    A primeira aparição de cada (mint, profile) é sempre preservada e marcada
    com first_seen_for_mint_profile=TRUE. Aparições subsequentes só entram se
    a última já tiver mais de `dedup_sec` segundos.

    Retorna o número de labels inseridos.
    """
    if not rows:
        return 0
    inserted = 0
    with db._cursor() as cur:
        for r in rows:
            mint = r.get("mint")
            profile = r.get("profile_name")
            if not mint or not profile:
                continue

            # Dedup + primeira aparição: olhamos o histórico de (mint, profile).
            cur.execute("""
                SELECT COUNT(*) AS n,
                       EXTRACT(EPOCH FROM (NOW() - MAX(ts_seen))) AS age_last
                  FROM gmgn_scanner_trending_labels
                 WHERE mint = %(mint)s AND profile_name = %(profile)s
            """, {"mint": mint, "profile": profile})
            agg = cur.fetchone() or {}
            n_prev = agg.get("n") or 0
            is_first = (n_prev == 0)

            if not is_first:
                age = agg.get("age_last")
                if age is not None and age < dedup_sec:
                    continue  # ainda dentro da janela de dedup — não repete

            # bounce relation
            bounce_ts = r.get("bounce_ts")
            seen_before = seen_after = None
            if bounce_ts is not None:
                # ts_seen ~ NOW() no momento do insert
                cur.execute(
                    "SELECT (NOW() <= %(b)s) AS before, (NOW() > %(b)s) AS after",
                    {"b": bounce_ts},
                )
                br = cur.fetchone() or {}
                seen_before = br.get("before")
                seen_after = br.get("after")

            mig_age = r.get("migration_age_sec")
            first_after_mig = mig_age if is_first else None

            cur.execute("""
                INSERT INTO gmgn_scanner_trending_labels (
                    mint, profile_name, timeframe, chain, order_by,
                    rank_in_filtered_trending, symbol, market_cap, liquidity,
                    total_fee_sol, volume_window, txs_window, buys_window, sells_window,
                    holders, token_age_minutes, scanner_status_at_seen, scanner_mc_at_seen,
                    migration_age_sec, outside_scanner, first_seen_for_mint_profile,
                    first_seen_after_migration_sec, seen_before_bounce, seen_after_bounce,
                    raw_json
                ) VALUES (
                    %(mint)s, %(profile_name)s, %(timeframe)s, %(chain)s, %(order_by)s,
                    %(rank)s, %(symbol)s, %(market_cap)s, %(liquidity)s,
                    %(total_fee_sol)s, %(volume_window)s, %(txs_window)s, %(buys_window)s, %(sells_window)s,
                    %(holders)s, %(token_age_minutes)s, %(scanner_status_at_seen)s, %(scanner_mc_at_seen)s,
                    %(migration_age_sec)s, %(outside_scanner)s, %(is_first)s,
                    %(first_after_mig)s, %(seen_before)s, %(seen_after)s,
                    %(raw_json)s
                )
            """, {
                "mint": mint,
                "profile_name": profile,
                "timeframe": r.get("timeframe"),
                "chain": r.get("chain"),
                "order_by": r.get("order_by"),
                "rank": r.get("rank_in_filtered_trending"),
                "symbol": r.get("symbol"),
                "market_cap": r.get("market_cap"),
                "liquidity": r.get("liquidity"),
                "total_fee_sol": r.get("total_fee_sol"),
                "volume_window": r.get("volume_window"),
                "txs_window": r.get("txs_window"),
                "buys_window": r.get("buys_window"),
                "sells_window": r.get("sells_window"),
                "holders": r.get("holders"),
                "token_age_minutes": r.get("token_age_minutes"),
                "scanner_status_at_seen": r.get("scanner_status_at_seen"),
                "scanner_mc_at_seen": r.get("scanner_mc_at_seen"),
                "migration_age_sec": mig_age,
                "outside_scanner": bool(r.get("outside_scanner")),
                "is_first": is_first,
                "first_after_mig": first_after_mig,
                "seen_before": seen_before,
                "seen_after": seen_after,
                "raw_json": _json.dumps(r.get("raw") or {}, ensure_ascii=False, default=str),
            })
            inserted += 1
    return inserted


# ==============================================================================
# Outcome — calculado a partir de token_ticks (Fase 4)
# ==============================================================================

def _tick_effective(row: dict) -> tuple[float | None, float | None, float | None]:
    """(mc, mc_high_eff, mc_low_eff) tolerante a NULLs nas colunas OHLC."""
    mc = row.get("mc") or row.get("mc_high") or row.get("mc_low") or row.get("mc_open")
    hi = row.get("mc_high") or row.get("mc") or row.get("mc_open") or row.get("mc_low")
    lo = row.get("mc_low") or row.get("mc") or row.get("mc_open") or row.get("mc_high")
    return mc, hi, lo


def _compute_outcome_from_ticks(ticks: list[dict], entry_mc: float,
                                entry_ts) -> dict:
    """Calcula runup/drawdown por janela + flags path-dependent + tempos.

    ticks: ordenados por tick_ts ASC, já restritos a [entry_ts, entry_ts+120m].
    """
    res: dict[str, Any] = {f"max_runup_{w}m": None for w in OUTCOME_WINDOWS_MIN}
    res.update({f"max_drawdown_{w}m": None for w in OUTCOME_WINDOWS_MIN})
    res.update({
        "hit_20_before_m20": None, "hit_30_before_m20": None,
        "hit_50_before_m25": None, "hit_100_before_m30": None,
        "time_to_20pct_min": None, "time_to_50pct_min": None,
        "time_to_top_min": None,
    })
    if not ticks or not entry_mc or entry_mc <= 0:
        return res

    entry_epoch = entry_ts.timestamp()

    # Runup/drawdown por janela
    for w in OUTCOME_WINDOWS_MIN:
        cutoff = entry_epoch + w * 60
        his, los = [], []
        for r in ticks:
            te = r["tick_ts"].timestamp()
            if te > cutoff:
                break
            _, hi, lo = _tick_effective(r)
            if hi:
                his.append(hi)
            if lo:
                los.append(lo)
        if his:
            res[f"max_runup_{w}m"] = max(his) / entry_mc - 1
        if los:
            res[f"max_drawdown_{w}m"] = min(los) / entry_mc - 1

    # Flags path-dependent: o alvo de subida foi atingido ANTES do stop de queda?
    up_targets = {"hit_20_before_m20": (0.20, -0.20),
                  "hit_30_before_m20": (0.30, -0.20),
                  "hit_50_before_m25": (0.50, -0.25),
                  "hit_100_before_m30": (1.00, -0.30)}
    fired = {k: None for k in up_targets}
    for r in ticks:
        _, hi, lo = _tick_effective(r)
        up = (hi / entry_mc - 1) if hi else None
        dn = (lo / entry_mc - 1) if lo else None
        for k, (up_t, dn_t) in up_targets.items():
            if fired[k] is not None:
                continue
            hit_up = up is not None and up >= up_t
            hit_dn = dn is not None and dn <= dn_t
            if hit_up and hit_dn:
                fired[k] = None  # ambíguo intrabar — deixa indefinido
                # marca como ambíguo via False só se quiser; mantemos None
                fired[k] = False
            elif hit_up:
                fired[k] = True
            elif hit_dn:
                fired[k] = False
    res.update(fired)

    # Tempos até alvos de subida e até o topo
    best_hi = entry_mc
    best_ts = None
    for r in ticks:
        mc, hi, lo = _tick_effective(r)
        te = r["tick_ts"].timestamp()
        up = (hi / entry_mc - 1) if hi else None
        if up is not None:
            if res["time_to_20pct_min"] is None and up >= 0.20:
                res["time_to_20pct_min"] = (te - entry_epoch) / 60
            if res["time_to_50pct_min"] is None and up >= 0.50:
                res["time_to_50pct_min"] = (te - entry_epoch) / 60
        if hi and hi > best_hi:
            best_hi = hi
            best_ts = te
    if best_ts is not None:
        res["time_to_top_min"] = (best_ts - entry_epoch) / 60
    return res


def refresh_outcomes(max_labels: int = 1000) -> dict:
    """Recalcula outcomes para labels sem outcome OU ainda não maduros (<130min).

    no_ticks NUNCA vira loss — fica outcome='no_ticks'.
    """
    summary = {"processed": 0, "no_ticks": 0, "computed": 0, "matured": 0}
    with db._cursor() as cur:
        cur.execute("""
            SELECT l.id, l.mint, l.ts_seen, l.market_cap, l.profile_name,
                   (NOW() - l.ts_seen) >= INTERVAL '130 minutes' AS matured,
                   b.ts_signal AS bounce_ts
              FROM gmgn_scanner_trending_labels l
              LEFT JOIN gmgn_scanner_trending_outcomes o ON o.label_id = l.id
              LEFT JOIN bounce_signals b ON b.mint = l.mint
             WHERE o.label_id IS NULL
                OR o.matured IS NOT TRUE
             ORDER BY l.ts_seen DESC
             LIMIT %(lim)s
        """, {"lim": max_labels})
        labels = [dict(r) for r in cur.fetchall()]

        for lab in labels:
            summary["processed"] += 1
            entry_mc = lab.get("market_cap")

            # entry_mc fallback: tick mais próximo no/após ts_seen
            if not entry_mc or entry_mc <= 0:
                cur.execute("""
                    SELECT COALESCE(mc, mc_high, mc_low, mc_open) AS mc_eff
                      FROM token_ticks
                     WHERE mint = %(mint)s AND tick_ts >= %(ts)s
                     ORDER BY tick_ts ASC LIMIT 1
                """, {"mint": lab["mint"], "ts": lab["ts_seen"]})
                near = cur.fetchone()
                entry_mc = (near or {}).get("mc_eff")

            cur.execute("""
                SELECT tick_ts, mc, mc_open, mc_high, mc_low
                  FROM token_ticks
                 WHERE mint = %(mint)s
                   AND tick_ts >= %(ts)s
                   AND tick_ts <= %(ts)s + (%(win)s || ' minutes')::INTERVAL
                 ORDER BY tick_ts ASC
            """, {"mint": lab["mint"], "ts": lab["ts_seen"], "win": _MAX_WINDOW_MIN})
            ticks = [dict(r) for r in cur.fetchall()]

            has_ticks = bool(ticks)
            if not has_ticks or not entry_mc or entry_mc <= 0:
                outcome = "no_ticks"
                metrics = {}
                summary["no_ticks"] += 1
            else:
                outcome = "computed"
                metrics = _compute_outcome_from_ticks(ticks, entry_mc, lab["ts_seen"])
                summary["computed"] += 1

            bounce_ts = lab.get("bounce_ts")
            gave_bounce = bounce_ts is not None
            if gave_bounce:
                trending_vs_bounce = "before" if lab["ts_seen"] <= bounce_ts else "after"
            else:
                trending_vs_bounce = "no_bounce"
            if lab.get("matured"):
                summary["matured"] += 1

            cur.execute("""
                INSERT INTO gmgn_scanner_trending_outcomes (
                    label_id, mint, profile_name, ts_seen, entry_mc, n_ticks, has_ticks,
                    outcome, matured,
                    max_runup_5m, max_runup_15m, max_runup_30m, max_runup_60m, max_runup_120m,
                    max_drawdown_5m, max_drawdown_15m, max_drawdown_30m, max_drawdown_60m, max_drawdown_120m,
                    hit_20_before_m20, hit_30_before_m20, hit_50_before_m25, hit_100_before_m30,
                    time_to_20pct_min, time_to_50pct_min, time_to_top_min,
                    gave_bounce, bounce_ts, trending_vs_bounce, computed_at
                ) VALUES (
                    %(label_id)s, %(mint)s, %(profile_name)s, %(ts_seen)s, %(entry_mc)s, %(n_ticks)s, %(has_ticks)s,
                    %(outcome)s, %(matured)s,
                    %(max_runup_5m)s, %(max_runup_15m)s, %(max_runup_30m)s, %(max_runup_60m)s, %(max_runup_120m)s,
                    %(max_drawdown_5m)s, %(max_drawdown_15m)s, %(max_drawdown_30m)s, %(max_drawdown_60m)s, %(max_drawdown_120m)s,
                    %(hit_20_before_m20)s, %(hit_30_before_m20)s, %(hit_50_before_m25)s, %(hit_100_before_m30)s,
                    %(time_to_20pct_min)s, %(time_to_50pct_min)s, %(time_to_top_min)s,
                    %(gave_bounce)s, %(bounce_ts)s, %(trending_vs_bounce)s, NOW()
                )
                ON CONFLICT (label_id) DO UPDATE SET
                    entry_mc = EXCLUDED.entry_mc, n_ticks = EXCLUDED.n_ticks,
                    has_ticks = EXCLUDED.has_ticks, outcome = EXCLUDED.outcome,
                    matured = EXCLUDED.matured,
                    max_runup_5m = EXCLUDED.max_runup_5m, max_runup_15m = EXCLUDED.max_runup_15m,
                    max_runup_30m = EXCLUDED.max_runup_30m, max_runup_60m = EXCLUDED.max_runup_60m,
                    max_runup_120m = EXCLUDED.max_runup_120m,
                    max_drawdown_5m = EXCLUDED.max_drawdown_5m, max_drawdown_15m = EXCLUDED.max_drawdown_15m,
                    max_drawdown_30m = EXCLUDED.max_drawdown_30m, max_drawdown_60m = EXCLUDED.max_drawdown_60m,
                    max_drawdown_120m = EXCLUDED.max_drawdown_120m,
                    hit_20_before_m20 = EXCLUDED.hit_20_before_m20, hit_30_before_m20 = EXCLUDED.hit_30_before_m20,
                    hit_50_before_m25 = EXCLUDED.hit_50_before_m25, hit_100_before_m30 = EXCLUDED.hit_100_before_m30,
                    time_to_20pct_min = EXCLUDED.time_to_20pct_min, time_to_50pct_min = EXCLUDED.time_to_50pct_min,
                    time_to_top_min = EXCLUDED.time_to_top_min,
                    gave_bounce = EXCLUDED.gave_bounce, bounce_ts = EXCLUDED.bounce_ts,
                    trending_vs_bounce = EXCLUDED.trending_vs_bounce, computed_at = NOW()
            """, {
                "label_id": lab["id"], "mint": lab["mint"], "profile_name": lab["profile_name"],
                "ts_seen": lab["ts_seen"], "entry_mc": entry_mc, "n_ticks": len(ticks),
                "has_ticks": has_ticks, "outcome": outcome, "matured": bool(lab.get("matured")),
                "gave_bounce": gave_bounce, "bounce_ts": bounce_ts,
                "trending_vs_bounce": trending_vs_bounce,
                **{k: metrics.get(k) for k in (
                    "max_runup_5m", "max_runup_15m", "max_runup_30m", "max_runup_60m", "max_runup_120m",
                    "max_drawdown_5m", "max_drawdown_15m", "max_drawdown_30m", "max_drawdown_60m", "max_drawdown_120m",
                    "hit_20_before_m20", "hit_30_before_m20", "hit_50_before_m25", "hit_100_before_m30",
                    "time_to_20pct_min", "time_to_50pct_min", "time_to_top_min")},
            })
    return summary


# ==============================================================================
# Relatório + grupo de controle (Fases 5 e 6)
# ==============================================================================

def _median(vals: list[float]) -> float | None:
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


def _migration_anchored_outcome(cur, mint: str, ts_migration, mc_migration) -> dict | None:
    """Outcome ancorado na migração (entry=mc_migration, entry_ts=ts_migration).

    Usado para comparar grupo A (trending) vs grupo B (controle) de forma
    apples-to-apples a partir do mesmo ponto de referência.
    Retorna None se não houver mc_migration ou ticks (no_ticks ≠ loss).
    """
    if not mc_migration or mc_migration <= 0 or ts_migration is None:
        return None
    cur.execute("""
        SELECT tick_ts, mc, mc_open, mc_high, mc_low
          FROM token_ticks
         WHERE mint = %(mint)s
           AND tick_ts >= %(ts)s
           AND tick_ts <= %(ts)s + (%(win)s || ' minutes')::INTERVAL
         ORDER BY tick_ts ASC
    """, {"mint": mint, "ts": ts_migration, "win": _MAX_WINDOW_MIN})
    ticks = [dict(r) for r in cur.fetchall()]
    if not ticks:
        return None
    return _compute_outcome_from_ticks(ticks, mc_migration, ts_migration)


def _group_stats(outcomes: list[dict]) -> dict:
    """Agrega outcomes (migration-anchored) num resumo comparável."""
    n = len(outcomes)
    if n == 0:
        return {"n": 0}
    def frac(pred):
        hits = [o for o in outcomes if pred(o)]
        return len(hits) / n
    up20_15 = frac(lambda o: (o.get("max_runup_15m") or -1) >= 0.20)
    up30_30 = frac(lambda o: (o.get("max_runup_30m") or -1) >= 0.30)
    up50_60 = frac(lambda o: (o.get("max_runup_60m") or -1) >= 0.50)
    # SL antes de TP (hit_50_before_m25 == False significa atingiu -25% antes de +50%)
    decided = [o for o in outcomes if o.get("hit_50_before_m25") is not None]
    sl_before_tp = (sum(1 for o in decided if o.get("hit_50_before_m25") is False) / len(decided)) if decided else None
    return {
        "n": n,
        "pct_up20_in_15m": up20_15,
        "pct_up30_in_30m": up30_30,
        "pct_up50_in_60m": up50_60,
        "median_runup_60m": _median([o.get("max_runup_60m") for o in outcomes]),
        "median_drawdown_60m": _median([o.get("max_drawdown_60m") for o in outcomes]),
        "median_return_60m": _median([o.get("max_runup_60m") for o in outcomes]),
        "sl_before_tp_rate": sl_before_tp,
        "median_time_to_top_min": _median([o.get("time_to_top_min") for o in outcomes]),
    }


def build_report(since: str | None = None, days: int = 14,
                 band: dict | None = None) -> dict:
    """Relatório completo para avaliar a hipótese (Fases 5 e 6).

    band: {mc_min, mc_max, liq_min, fee_min} — faixa operacional do controle.
    Grupo A = migrations na faixa que apareceram em ≥1 label de trending filtrado.
    Grupo B = migrations na faixa que NÃO apareceram em nenhum label.
    """
    ensure_schema()
    band = band or {}
    mc_min = band.get("mc_min", 0)
    mc_max = band.get("mc_max", 10 ** 12)
    liq_min = band.get("liq_min", 0)
    fee_min = band.get("fee_min", 0)

    rep: dict[str, Any] = {"since": since, "days": days, "band": band}
    with db._cursor() as cur:
        # Cláusulas de janela temporal com named params (evita passar SQL como texto).
        def _ts_clause(col: str) -> str:
            if since:
                return f"{col} >= %(since)s::timestamptz"
            return f"{col} >= NOW() - (%(days)s || ' days')::INTERVAL"

        since_clause = _ts_clause("m.ts_migration")
        ts_seen_clause = _ts_clause("ts_seen")
        ts_seen_clause_l = _ts_clause("l.ts_seen")
        params = {"since": since, "days": days,
                  "mc_min": mc_min, "mc_max": mc_max, "liq_min": liq_min, "fee_min": fee_min}

        # --- contadores de labels ---
        cur.execute("""
            SELECT COUNT(*) AS total,
                   COUNT(DISTINCT mint) AS distinct_mints,
                   COUNT(*) FILTER (WHERE outside_scanner) AS outside_scanner,
                   COUNT(*) FILTER (WHERE NOT outside_scanner) AS inside_scanner
              FROM gmgn_scanner_trending_labels
             WHERE """ + ts_seen_clause + """
        """, params)
        rep["labels"] = dict(cur.fetchone() or {})

        cur.execute("""
            SELECT profile_name, timeframe, COUNT(*) AS n,
                   COUNT(DISTINCT mint) AS mints,
                   COUNT(*) FILTER (WHERE outside_scanner) AS outside
              FROM gmgn_scanner_trending_labels
             WHERE """ + ts_seen_clause + """
             GROUP BY profile_name, timeframe
             ORDER BY n DESC
        """, params)
        rep["by_profile"] = [dict(r) for r in cur.fetchall()]

        # --- universo do scanner na faixa operacional ---
        cur.execute(f"""
            SELECT m.mint, m.ts_migration, m.mc_migration, m.liq_migration, m.total_fee_sol,
                   EXISTS (SELECT 1 FROM gmgn_scanner_trending_labels l
                            WHERE l.mint = m.mint AND NOT l.outside_scanner) AS labeled,
                   EXISTS (SELECT 1 FROM bounce_signals b WHERE b.mint = m.mint) AS bounced
              FROM migrations m
             WHERE {since_clause}
               AND m.mc_migration >= %(mc_min)s AND m.mc_migration <= %(mc_max)s
               AND COALESCE(m.liq_migration, 0) >= %(liq_min)s
               AND COALESCE(m.total_fee_sol, 0) >= %(fee_min)s
        """, params)
        migs = [dict(r) for r in cur.fetchall()]

        group_a, group_b = [], []
        a_bounce = b_bounce = 0
        for m in migs:
            oc = _migration_anchored_outcome(cur, m["mint"], m["ts_migration"], m["mc_migration"])
            if m["labeled"]:
                if oc is not None:
                    group_a.append(oc)
                if m["bounced"]:
                    a_bounce += 1
            else:
                if oc is not None:
                    group_b.append(oc)
                if m["bounced"]:
                    b_bounce += 1

        n_a = sum(1 for m in migs if m["labeled"])
        n_b = sum(1 for m in migs if not m["labeled"])
        rep["control"] = {
            "scanner_in_band": len(migs),
            "group_a_labeled": n_a,
            "group_b_unlabeled": n_b,
            "group_a_bounce_rate": (a_bounce / n_a) if n_a else None,
            "group_b_bounce_rate": (b_bounce / n_b) if n_b else None,
            "group_a_stats": _group_stats(group_a),
            "group_b_stats": _group_stats(group_b),
        }

        # --- timeframe 5m vs 1h: outcome ancorado no label (entry=ts_seen) ---
        cur.execute("""
            SELECT l.timeframe,
                   COUNT(*) FILTER (WHERE o.outcome = 'computed') AS n_computed,
                   COUNT(*) FILTER (WHERE o.outcome = 'no_ticks') AS n_no_ticks,
                   AVG(o.max_runup_60m) FILTER (WHERE o.outcome='computed') AS avg_runup_60m,
                   AVG(o.max_drawdown_60m) FILTER (WHERE o.outcome='computed') AS avg_drawdown_60m,
                   AVG(CASE WHEN o.hit_50_before_m25 THEN 1.0
                            WHEN o.hit_50_before_m25 IS FALSE THEN 0.0 END) AS hit50_rate,
                   AVG(CASE WHEN o.gave_bounce THEN 1.0 ELSE 0.0 END) AS bounce_rate
              FROM gmgn_scanner_trending_labels l
              JOIN gmgn_scanner_trending_outcomes o ON o.label_id = l.id
             WHERE """ + ts_seen_clause_l + """
             GROUP BY l.timeframe
             ORDER BY l.timeframe
        """, params)
        rep["by_timeframe"] = [dict(r) for r in cur.fetchall()]

        # --- rank buckets (rank importa?) ---
        cur.execute("""
            SELECT width_bucket(l.rank_in_filtered_trending, 1, 101, 10) AS rank_bucket,
                   MIN(l.rank_in_filtered_trending) AS rank_lo,
                   MAX(l.rank_in_filtered_trending) AS rank_hi,
                   COUNT(*) AS n,
                   AVG(o.max_runup_60m) FILTER (WHERE o.outcome='computed') AS avg_runup_60m,
                   AVG(CASE WHEN o.hit_50_before_m25 THEN 1.0
                            WHEN o.hit_50_before_m25 IS FALSE THEN 0.0 END) AS hit50_rate
              FROM gmgn_scanner_trending_labels l
              JOIN gmgn_scanner_trending_outcomes o ON o.label_id = l.id
             WHERE """ + ts_seen_clause_l + """ AND l.rank_in_filtered_trending IS NOT NULL
             GROUP BY rank_bucket
             ORDER BY rank_bucket
        """, params)
        rep["by_rank"] = [dict(r) for r in cur.fetchall()]

        # --- trending antes/depois do bounce ---
        cur.execute("""
            SELECT o.trending_vs_bounce, COUNT(*) AS n,
                   AVG(o.max_runup_60m) FILTER (WHERE o.outcome='computed') AS avg_runup_60m
              FROM gmgn_scanner_trending_outcomes o
              JOIN gmgn_scanner_trending_labels l ON l.id = o.label_id
             WHERE """ + ts_seen_clause_l + """
             GROUP BY o.trending_vs_bounce
        """, params)
        rep["trending_vs_bounce"] = [dict(r) for r in cur.fetchall()]

        # --- exemplos ---
        cur.execute("""
            SELECT l.symbol, l.mint, l.profile_name, l.rank_in_filtered_trending AS rank,
                   o.max_runup_60m, o.max_drawdown_60m, o.trending_vs_bounce, o.outcome
              FROM gmgn_scanner_trending_outcomes o
              JOIN gmgn_scanner_trending_labels l ON l.id = o.label_id
             WHERE """ + ts_seen_clause_l + """ AND o.outcome = 'computed'
             ORDER BY o.max_runup_60m DESC NULLS LAST LIMIT 10
        """, params)
        rep["best_cases"] = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT l.symbol, l.mint, l.profile_name, l.rank_in_filtered_trending AS rank,
                   o.max_runup_60m, o.max_drawdown_60m, o.trending_vs_bounce, o.outcome
              FROM gmgn_scanner_trending_outcomes o
              JOIN gmgn_scanner_trending_labels l ON l.id = o.label_id
             WHERE """ + ts_seen_clause_l + """ AND o.outcome = 'computed'
             ORDER BY o.max_drawdown_60m ASC NULLS LAST LIMIT 10
        """, params)
        rep["worst_cases"] = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT l.symbol, l.mint, l.profile_name, l.ts_seen, o.bounce_ts,
                   o.max_runup_60m
              FROM gmgn_scanner_trending_outcomes o
              JOIN gmgn_scanner_trending_labels l ON l.id = o.label_id
             WHERE """ + ts_seen_clause_l + """ AND o.trending_vs_bounce = 'before'
             ORDER BY o.max_runup_60m DESC NULLS LAST LIMIT 10
        """, params)
        rep["trending_before_bounce"] = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT l.symbol, l.mint, l.profile_name, l.ts_seen, o.bounce_ts,
                   o.max_runup_60m
              FROM gmgn_scanner_trending_outcomes o
              JOIN gmgn_scanner_trending_labels l ON l.id = o.label_id
             WHERE """ + ts_seen_clause_l + """ AND o.trending_vs_bounce = 'after'
             ORDER BY l.ts_seen DESC LIMIT 10
        """, params)
        rep["trending_after_bounce"] = [dict(r) for r in cur.fetchall()]

    rep["recommendation"] = _build_recommendation(rep)
    return rep


def _build_recommendation(rep: dict) -> dict:
    """Fase 7 — recomendação textual SEM alterar estratégia. Só sugere uso futuro."""
    ctrl = rep.get("control", {})
    a = ctrl.get("group_a_bounce_rate")
    b = ctrl.get("group_b_bounce_rate")
    n_a = ctrl.get("group_a_labeled") or 0
    n_b = ctrl.get("group_b_unlabeled") or 0
    edge = None
    if a is not None and b is not None:
        edge = a - b
    verdict = "inconclusivo (dados insuficientes)"
    suggestion = "Continuar coletando — research-only, sem alterar compra/executor/H26/H27."
    if n_a >= 20 and n_b >= 20 and edge is not None:
        if edge >= 0.10:
            verdict = f"edge positivo: grupo trending +{edge*100:.1f}pp de bounce vs controle"
            suggestion = ("Candidato a CONTEXT/BOOST: se H26 aprovado e o token apareceu no "
                          "trending filtrado recentemente, aumentar confiança. NÃO implementar ainda.")
        elif edge <= -0.10:
            verdict = f"edge negativo: grupo trending {edge*100:.1f}pp de bounce vs controle"
            suggestion = "Sem suporte para boost. Não usar como sinal positivo."
        else:
            verdict = f"sem edge claro (delta {edge*100:+.1f}pp)"
            suggestion = "Manter apenas como CONTEXT informativo; não usar como boost/veto."
    return {"verdict": verdict, "edge_pp": (edge * 100 if edge is not None else None),
            "suggestion": suggestion,
            "note": "Recomendação research-only. Nenhuma mudança de estratégia foi aplicada."}
