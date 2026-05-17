"""
Deep analysis: 40 TPs vs 258 SLs (blockchain-verified).
Run on Railway: python scripts/analyze_tp_vs_sl.py
DATABASE_URL must be set in environment.
"""
import os
import sys
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set")
    sys.exit(1)

conn = psycopg2.connect(DATABASE_URL)

def q(label, sql, params=None):
    print(f"\n{'='*70}")
    print(f"  {label}")
    print('='*70)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                print("  (no rows)")
                return rows
            # Print header
            keys = list(rows[0].keys())
            widths = {k: max(len(str(k)), max(len(str(r[k])) if r[k] is not None else 4 for r in rows)) for k in keys}
            header = "  " + "  ".join(str(k).ljust(widths[k]) for k in keys)
            print(header)
            print("  " + "-" * (sum(widths.values()) + 2*len(keys)))
            for row in rows:
                line = "  " + "  ".join(
                    (f"{row[k]:.4f}" if isinstance(row[k], float) else str(row[k]) if row[k] is not None else "NULL").ljust(widths[k])
                    for k in keys
                )
                print(line)
            print(f"  ({len(rows)} rows)")
            return rows
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback; traceback.print_exc()
            return []


# ── Q0: Baseline — count and confirm dataset ────────────────────────────────
q("Q0. Dataset baseline — total trades by status with exit_type", """
    SELECT status, exit_type, COUNT(*) as n,
           ROUND(AVG(pnl_pct_real)*100, 2) as avg_pnl_pct
    FROM trades
    WHERE exit_type IS NOT NULL
    GROUP BY status, exit_type
    ORDER BY n DESC
""")

q("Q0b. Total TP vs SL (blockchain-verified)", """
    SELECT
        status,
        COUNT(*) as n,
        ROUND(AVG(pnl_pct_real)*100, 2) as avg_pnl_pct,
        ROUND(MIN(pnl_pct_real)*100, 2) as min_pnl_pct,
        ROUND(MAX(pnl_pct_real)*100, 2) as max_pnl_pct
    FROM trades
    WHERE exit_type IS NOT NULL AND status IN ('tp','sl')
    GROUP BY status
""")

# ── Q1: bounce_pct distribution ──────────────────────────────────────────────
q("Q1. bounce_pct distribution TP vs SL", """
    SELECT
        CASE WHEN t.status='tp' THEN 'TP' ELSE 'SL' END as outcome,
        ROUND(MIN(bs.bounce_pct)*100, 1) as min_bounce,
        ROUND(AVG(bs.bounce_pct)*100, 1) as avg_bounce,
        ROUND(MAX(bs.bounce_pct)*100, 1) as max_bounce,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY bs.bounce_pct)*100, 1) as median_bounce,
        ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY bs.bounce_pct)*100, 1) as p75_bounce,
        COUNT(*) as n
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1
""")

# ── Q2: elapsed_sec ──────────────────────────────────────────────────────────
q("Q2. elapsed_sec (migration→signal) TP vs SL", """
    SELECT
        CASE WHEN t.status='tp' THEN 'TP' ELSE 'SL' END as outcome,
        ROUND(AVG(bs.elapsed_sec)/60.0, 2) as avg_min,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY bs.elapsed_sec)/60.0, 2) as median_min,
        ROUND(MIN(bs.elapsed_sec)/60.0, 2) as min_min,
        ROUND(MAX(bs.elapsed_sec)/60.0, 2) as max_min
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1
""")

# ── Q3: smart wallets ────────────────────────────────────────────────────────
q("Q3. Smart wallets TP vs SL", """
    SELECT
        CASE WHEN t.status='tp' THEN 'TP' ELSE 'SL' END as outcome,
        ROUND(AVG(m.smart_wallets), 2) as avg_smart,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY m.smart_wallets) as median_smart,
        ROUND(AVG(m.renowned_wallets), 2) as avg_renowned,
        ROUND(AVG(m.sniper_wallets), 2) as avg_sniper
    FROM trades t
    JOIN migrations m ON m.mint = t.mint
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1
""")

# ── Q4: MC migration + liquidity ─────────────────────────────────────────────
q("Q4. MC migration + liquidity TP vs SL", """
    SELECT
        CASE WHEN t.status='tp' THEN 'TP' ELSE 'SL' END as outcome,
        ROUND(AVG(m.mc_migration)) as avg_mc_mig,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY m.mc_migration)) as median_mc_mig,
        ROUND(AVG(m.liq_migration)) as avg_liq,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY m.liq_migration)) as median_liq
    FROM trades t
    JOIN migrations m ON m.mint = t.mint
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1
""")

# ── Q5: WR by bounce_pct bucket ──────────────────────────────────────────────
q("Q5. WR by bounce_pct bucket (granular)", """
    SELECT
        ROUND(bs.bounce_pct*100/10)*10 as bounce_bucket_pct,
        COUNT(*) as n,
        SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END) as tp,
        ROUND(100.0*SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END)/COUNT(*),1) as wr_pct
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1 ORDER BY 1
""")

# ── Q5b: Finer buckets for high bounce ───────────────────────────────────────
q("Q5b. WR by bounce_pct finer buckets (>50%)", """
    SELECT
        CASE
            WHEN bs.bounce_pct < 0.55 THEN '50-55%'
            WHEN bs.bounce_pct < 0.65 THEN '55-65%'
            WHEN bs.bounce_pct < 0.75 THEN '65-75%'
            WHEN bs.bounce_pct < 1.00 THEN '75-100%'
            WHEN bs.bounce_pct < 1.50 THEN '100-150%'
            WHEN bs.bounce_pct < 2.00 THEN '150-200%'
            ELSE '>200%'
        END as bounce_bucket,
        COUNT(*) as n,
        SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END) as tp,
        ROUND(100.0*SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END)/COUNT(*),1) as wr_pct
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
      AND bs.bounce_pct >= 0.50
    GROUP BY 1 ORDER BY MIN(bs.bounce_pct)
""")

# ── Q6: WR by elapsed bucket ─────────────────────────────────────────────────
q("Q6. WR by elapsed bucket", """
    SELECT
        CASE
            WHEN bs.elapsed_sec < 300 THEN '0-5min'
            WHEN bs.elapsed_sec < 600 THEN '5-10min'
            WHEN bs.elapsed_sec < 900 THEN '10-15min'
            WHEN bs.elapsed_sec < 1200 THEN '15-20min'
            ELSE '>20min'
        END as elapsed_bucket,
        COUNT(*) as n,
        SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END) as tp,
        ROUND(100.0*SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END)/COUNT(*),1) as wr_pct
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1 ORDER BY MIN(bs.elapsed_sec)
""")

# ── Q7: Token quality ────────────────────────────────────────────────────────
q("Q7. Token quality fields TP vs SL", """
    SELECT
        CASE WHEN t.status='tp' THEN 'TP' ELSE 'SL' END as outcome,
        ROUND(AVG(m.rug_ratio)*100, 2) as avg_rug_pct,
        ROUND(AVG(m.top10_holder_rate)*100, 2) as avg_top10_pct,
        ROUND(AVG(m.rat_trader_rate)*100, 2) as avg_rat_pct,
        ROUND(AVG(m.bot_degen_rate)*100, 2) as avg_bot_pct,
        ROUND(AVG(m.total_fee_sol), 3) as avg_fee_sol,
        ROUND(SUM(CASE WHEN m.creator_token_status='creator_close' THEN 1 ELSE 0 END)*100.0/COUNT(*), 1) as pct_dev_sold,
        ROUND(SUM(CASE WHEN m.cto_flag=1 THEN 1 ELSE 0 END)*100.0/COUNT(*), 1) as pct_cto
    FROM trades t
    JOIN migrations m ON m.mint = t.mint
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1
""")

# ── Q8: pnl_pct_real distribution of TPs ────────────────────────────────────
q("Q8. pnl_pct_real distribution of TPs (×100 = real %)", """
    SELECT
        ROUND(MIN(pnl_pct_real)*100, 1) as min_pnl_pct,
        ROUND(AVG(pnl_pct_real)*100, 1) as avg_pnl_pct,
        ROUND(MAX(pnl_pct_real)*100, 1) as max_pnl_pct,
        ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY pnl_pct_real)*100, 1) as p25_pnl,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pnl_pct_real)*100, 1) as median_pnl_pct,
        ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY pnl_pct_real)*100, 1) as p75_pnl,
        COUNT(*) as n
    FROM trades
    WHERE exit_type IS NOT NULL AND status='tp'
""")

# ── Q8b: pnl distribution of SLs ────────────────────────────────────────────
q("Q8b. pnl_pct_real distribution of SLs", """
    SELECT
        ROUND(MIN(pnl_pct_real)*100, 1) as min_pnl_pct,
        ROUND(AVG(pnl_pct_real)*100, 1) as avg_pnl_pct,
        ROUND(MAX(pnl_pct_real)*100, 1) as max_pnl_pct,
        ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY pnl_pct_real)*100, 1) as p25_pnl,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pnl_pct_real)*100, 1) as median_pnl_pct,
        ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY pnl_pct_real)*100, 1) as p75_pnl,
        COUNT(*) as n
    FROM trades
    WHERE exit_type IS NOT NULL AND status='sl'
""")

# ── Q9: Phantom bounce analysis ──────────────────────────────────────────────
q("Q9. Phantom bounces (supply bug) — TP vs SL breakdown", """
    SELECT
        CASE WHEN bs.mc_min / NULLIF(m.mc_bottom,0) < 0.6 THEN 'phantom (<60%)'
             WHEN bs.mc_min / NULLIF(m.mc_bottom,0) < 0.8 THEN 'suspect (60-80%)'
             ELSE 'real (>80%)'
        END as phantom_status,
        COUNT(*) as n,
        SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END) as tp,
        ROUND(100.0*SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END)/COUNT(*), 1) as wr_pct,
        ROUND(AVG(bs.bounce_pct)*100, 1) as avg_bounce_pct,
        ROUND(AVG(bs.mc_min / NULLIF(m.mc_bottom,0)), 3) as avg_mc_ratio
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    JOIN migrations m ON m.mint = t.mint
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
      AND m.mc_bottom IS NOT NULL AND m.mc_bottom > 0
      AND bs.mc_min IS NOT NULL
    GROUP BY 1 ORDER BY MIN(bs.mc_min / NULLIF(m.mc_bottom,0))
""")

# ── Q10: drop_from_mig ────────────────────────────────────────────────────────
q("Q10. drop_from_mig (depth of dump) TP vs SL", """
    SELECT
        CASE WHEN t.status='tp' THEN 'TP' ELSE 'SL' END as outcome,
        ROUND(AVG(bs.drop_from_mig)*100, 1) as avg_drop_pct,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY bs.drop_from_mig)*100, 1) as median_drop_pct,
        ROUND(MIN(bs.drop_from_mig)*100, 1) as min_drop_pct,
        ROUND(MAX(bs.drop_from_mig)*100, 1) as max_drop_pct
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1
""")

# ── Q11: WR by drop_from_mig bucket ──────────────────────────────────────────
q("Q11. WR by dump depth bucket", """
    SELECT
        CASE
            WHEN bs.drop_from_mig < 0.50 THEN '<50% drop'
            WHEN bs.drop_from_mig < 0.60 THEN '50-60% drop'
            WHEN bs.drop_from_mig < 0.70 THEN '60-70% drop'
            WHEN bs.drop_from_mig < 0.80 THEN '70-80% drop'
            WHEN bs.drop_from_mig < 0.90 THEN '80-90% drop'
            ELSE '>90% drop'
        END as drop_bucket,
        COUNT(*) as n,
        SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END) as tp,
        ROUND(100.0*SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END)/COUNT(*),1) as wr_pct
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1 ORDER BY MIN(bs.drop_from_mig)
""")

# ── Q12: entry_ratio (mc_at_signal / mc_migration) ───────────────────────────
q("Q12. Entry ratio (mc_at_signal / mc_migration) — WR by bucket", """
    SELECT
        CASE
            WHEN bs.mc_at_signal / NULLIF(bs.mc_migration,0) < 0.20 THEN '<20% of migration'
            WHEN bs.mc_at_signal / NULLIF(bs.mc_migration,0) < 0.35 THEN '20-35%'
            WHEN bs.mc_at_signal / NULLIF(bs.mc_migration,0) < 0.50 THEN '35-50%'
            WHEN bs.mc_at_signal / NULLIF(bs.mc_migration,0) < 0.65 THEN '50-65% (A.3 dead zone)'
            WHEN bs.mc_at_signal / NULLIF(bs.mc_migration,0) < 0.80 THEN '65-80%'
            ELSE '>80% of migration'
        END as entry_ratio_bucket,
        COUNT(*) as n,
        SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END) as tp,
        ROUND(100.0*SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END)/COUNT(*),1) as wr_pct,
        ROUND(AVG(bs.mc_at_signal / NULLIF(bs.mc_migration,0))*100, 1) as avg_entry_ratio_pct
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
      AND bs.mc_migration > 0 AND bs.mc_at_signal IS NOT NULL
    GROUP BY 1 ORDER BY MIN(bs.mc_at_signal / NULLIF(bs.mc_migration,0))
""")

# ── Q13: H22/H23 absorption (bottom window) ──────────────────────────────────
q("Q13. H23 net SOL flow (bottom window) vs WR", """
    SELECT
        CASE
            WHEN bs.h23_window != 'bottom' OR bs.net_sol_flow IS NULL THEN 'no_data'
            WHEN bs.net_sol_flow > 5   THEN '>5 SOL buy (strong)'
            WHEN bs.net_sol_flow > 0   THEN '0-5 SOL buy (light)'
            WHEN bs.net_sol_flow > -5  THEN '-5-0 SOL sell (light)'
            ELSE '<-5 SOL sell (strong)'
        END as h23_bucket,
        COUNT(*) as n,
        SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END) as tp,
        ROUND(100.0*SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END)/COUNT(*),1) as wr_pct
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1 ORDER BY MIN(COALESCE(bs.net_sol_flow, -999))
""")

# ── Q14: Pool depth at signal (H25) ──────────────────────────────────────────
q("Q14. Pool depth at signal (H25) vs WR", """
    SELECT
        CASE
            WHEN bs.pool_quote_at_signal IS NULL THEN 'no_data'
            WHEN bs.pool_quote_at_signal >= 100 THEN '>=100 SOL (deep)'
            WHEN bs.pool_quote_at_signal >= 50  THEN '50-100 SOL'
            WHEN bs.pool_quote_at_signal >= 20  THEN '20-50 SOL'
            ELSE '<20 SOL (thin pool)'
        END as pool_bucket,
        COUNT(*) as n,
        SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END) as tp,
        ROUND(100.0*SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END)/COUNT(*),1) as wr_pct
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1 ORDER BY MIN(COALESCE(bs.pool_quote_at_signal, -1))
""")

# ── Q15: Seller concentration during dump (H24) ──────────────────────────────
q("Q15. Seller concentration at dump (H24 HHI) vs WR", """
    SELECT
        CASE
            WHEN bs.dump_seller_hhi IS NULL THEN 'no_data'
            WHEN bs.dump_seller_hhi >= 0.5 THEN '>=0.5 (1-2 sellers dominate)'
            WHEN bs.dump_seller_hhi >= 0.2 THEN '0.2-0.5 (few sellers)'
            WHEN bs.dump_seller_hhi >= 0.1 THEN '0.1-0.2 (distributed)'
            ELSE '<0.1 (very distributed)'
        END as hhi_bucket,
        COUNT(*) as n,
        SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END) as tp,
        ROUND(100.0*SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END)/COUNT(*),1) as wr_pct,
        ROUND(AVG(bs.dump_seller_hhi), 3) as avg_hhi
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1
""")

# ── Q16: time_to_bottom from migrations ──────────────────────────────────────
q("Q16. time_to_bottom (mins) TP vs SL", """
    SELECT
        CASE WHEN t.status='tp' THEN 'TP' ELSE 'SL' END as outcome,
        ROUND(AVG(m.time_to_bottom), 2) as avg_ttb_min,
        ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY m.time_to_bottom), 2) as median_ttb_min,
        ROUND(MIN(m.time_to_bottom), 2) as min_ttb_min,
        ROUND(MAX(m.time_to_bottom), 2) as max_ttb_min
    FROM trades t
    JOIN migrations m ON m.mint = t.mint
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
      AND m.time_to_bottom IS NOT NULL
    GROUP BY 1
""")

# ── Q17: WR by smart_wallets bucket ──────────────────────────────────────────
q("Q17. WR by smart_wallets bucket (live trades)", """
    SELECT
        CASE
            WHEN m.smart_wallets < 3 THEN '<3 smart'
            WHEN m.smart_wallets < 5 THEN '3-4 smart'
            WHEN m.smart_wallets < 8 THEN '5-7 smart'
            WHEN m.smart_wallets < 12 THEN '8-11 smart'
            ELSE '>=12 smart'
        END as smart_bucket,
        COUNT(*) as n,
        SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END) as tp,
        ROUND(100.0*SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END)/COUNT(*),1) as wr_pct,
        ROUND(AVG(m.smart_wallets), 1) as avg_smart
    FROM trades t
    JOIN migrations m ON m.mint = t.mint
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1 ORDER BY MIN(m.smart_wallets)
""")

# ── Q18: Overhead supply (entry as % of peak) ────────────────────────────────
q("Q18. Overhead supply (mc_at_signal / mc_peak) — who's above us", """
    SELECT
        CASE
            WHEN bs.mc_at_signal / NULLIF(m.mc_peak,0) >= 0.7 THEN '>=70% of peak (lots above)'
            WHEN bs.mc_at_signal / NULLIF(m.mc_peak,0) >= 0.5 THEN '50-70% of peak'
            WHEN bs.mc_at_signal / NULLIF(m.mc_peak,0) >= 0.3 THEN '30-50% of peak'
            ELSE '<30% of peak (most sold)'
        END as overhead_bucket,
        COUNT(*) as n,
        SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END) as tp,
        ROUND(100.0*SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END)/COUNT(*),1) as wr_pct,
        ROUND(AVG(bs.mc_at_signal / NULLIF(m.mc_peak,0))*100, 1) as avg_overhead_pct
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    JOIN migrations m ON m.mint = t.mint
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
      AND m.mc_peak > 0 AND bs.mc_at_signal IS NOT NULL
    GROUP BY 1 ORDER BY MIN(bs.mc_at_signal / NULLIF(m.mc_peak,0))
""")

# ── Q19: Trade config at time of trade (SL pct) ──────────────────────────────
q("Q19. SL config at time of trade vs WR", """
    SELECT
        CASE
            WHEN t.sl_pct <= 0.15 THEN 'SL<=15%'
            WHEN t.sl_pct <= 0.20 THEN 'SL 16-20%'
            WHEN t.sl_pct <= 0.25 THEN 'SL 21-25%'
            ELSE 'SL>25%'
        END as sl_bucket,
        COUNT(*) as n,
        SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END) as tp,
        ROUND(100.0*SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END)/COUNT(*),1) as wr_pct,
        ROUND(AVG(t.sl_pct)*100, 1) as avg_sl_pct
    FROM trades t
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1 ORDER BY MIN(t.sl_pct)
""")

# ── Q20: Multivariate — bounce_pct >=55% AND elapsed <15min ──────────────────
q("Q20. Multivariate: bounce>=55% AND elapsed<15min", """
    SELECT
        CASE
            WHEN bs.bounce_pct >= 0.55 AND bs.elapsed_sec < 900 THEN 'bounce>=55% AND elapsed<15min'
            WHEN bs.bounce_pct >= 0.55 AND bs.elapsed_sec >= 900 THEN 'bounce>=55% AND elapsed>=15min'
            WHEN bs.bounce_pct < 0.55  AND bs.elapsed_sec < 900 THEN 'bounce<55% AND elapsed<15min'
            ELSE 'bounce<55% AND elapsed>=15min'
        END as combo,
        COUNT(*) as n,
        SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END) as tp,
        ROUND(100.0*SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END)/COUNT(*),1) as wr_pct
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1 ORDER BY wr_pct DESC
""")

# ── Q21: Individual TPs — full detail ────────────────────────────────────────
q("Q21. All TPs with key fields", """
    SELECT
        t.id,
        t.symbol,
        t.created_at::date as date,
        ROUND(t.pnl_pct_real*100, 1) as pnl_pct,
        ROUND(bs.bounce_pct*100, 1) as bounce_pct,
        ROUND(bs.elapsed_sec/60.0, 1) as elapsed_min,
        ROUND(bs.drop_from_mig*100, 1) as drop_pct,
        m.smart_wallets as smart,
        ROUND(m.mc_migration) as mc_mig,
        ROUND(bs.mc_min / NULLIF(m.mc_bottom,0), 2) as min_ratio,
        t.sl_pct,
        t.exit_type
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    LEFT JOIN migrations m ON m.mint = t.mint
    WHERE t.exit_type IS NOT NULL AND t.status = 'tp'
    ORDER BY t.pnl_pct_real DESC
""")

# ── Q22: Time series — WR by week ────────────────────────────────────────────
q("Q22. WR evolution by week", """
    SELECT
        DATE_TRUNC('week', t.created_at)::date as week,
        COUNT(*) as n,
        SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END) as tp,
        SUM(CASE WHEN t.status='sl' THEN 1 ELSE 0 END) as sl,
        ROUND(100.0*SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END)/COUNT(*),1) as wr_pct
    FROM trades t
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1 ORDER BY 1
""")

# ── Q23: buy_concentration (H26) ─────────────────────────────────────────────
q("Q23. Buy concentration H26 (largest_buy/buy_vol) vs WR", """
    SELECT
        CASE
            WHEN bs.buy_concentration IS NULL THEN 'no_data'
            WHEN bs.buy_concentration >= 0.8 THEN '>=80% (1 buyer dominated)'
            WHEN bs.buy_concentration >= 0.5 THEN '50-80% (concentrated)'
            WHEN bs.buy_concentration >= 0.2 THEN '20-50% (moderate)'
            ELSE '<20% (distributed)'
        END as conc_bucket,
        COUNT(*) as n,
        SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END) as tp,
        ROUND(100.0*SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END)/COUNT(*),1) as wr_pct
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1
""")

# ── Q24: Rise window — who bought during the bounce itself ───────────────────
q("Q24. Rise window net flow (ts_min→ts_signal) vs WR", """
    SELECT
        CASE
            WHEN bs.rise_net_flow IS NULL THEN 'no_data'
            WHEN bs.rise_net_flow > 10  THEN '>10 SOL (heavy buy during rise)'
            WHEN bs.rise_net_flow > 3   THEN '3-10 SOL'
            WHEN bs.rise_net_flow > 0   THEN '0-3 SOL (light buy)'
            WHEN bs.rise_net_flow > -3  THEN '-3-0 SOL (light sell)'
            ELSE '<-3 SOL (selling into bounce)'
        END as rise_bucket,
        COUNT(*) as n,
        SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END) as tp,
        ROUND(100.0*SUM(CASE WHEN t.status='tp' THEN 1 ELSE 0 END)/COUNT(*),1) as wr_pct
    FROM trades t
    JOIN bounce_signals bs ON bs.mint = t.mint
        AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
    WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
    GROUP BY 1 ORDER BY MIN(COALESCE(bs.rise_net_flow, -999))
""")

# ── Q25: Quick summary — top differences ─────────────────────────────────────
print("\n" + "="*70)
print("  SUMMARY: Variable differences TP vs SL")
print("="*70)

with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute("""
        SELECT
            CASE WHEN t.status='tp' THEN 'TP' ELSE 'SL' END as outcome,
            COUNT(*) as n,
            ROUND(AVG(bs.bounce_pct)*100, 1) as avg_bounce_pct,
            ROUND(AVG(bs.elapsed_sec)/60.0, 1) as avg_elapsed_min,
            ROUND(AVG(bs.drop_from_mig)*100, 1) as avg_drop_pct,
            ROUND(AVG(m.smart_wallets), 1) as avg_smart,
            ROUND(AVG(m.mc_migration)) as avg_mc_mig,
            ROUND(AVG(m.time_to_bottom), 1) as avg_ttb_min,
            ROUND(AVG(bs.mc_at_signal / NULLIF(bs.mc_migration,0))*100, 1) as avg_entry_ratio_pct,
            ROUND(AVG(bs.mc_at_signal / NULLIF(m.mc_peak,0))*100, 1) as avg_overhead_pct
        FROM trades t
        JOIN bounce_signals bs ON bs.mint = t.mint
            AND bs.ts_signal BETWEEN t.created_at - INTERVAL '5 min' AND t.created_at + INTERVAL '30 min'
        LEFT JOIN migrations m ON m.mint = t.mint
        WHERE t.exit_type IS NOT NULL AND t.status IN ('tp','sl')
        GROUP BY 1
    """)
    rows = cur.fetchall()
    for r in rows:
        print(f"\n  {r['outcome']} (n={r['n']}):")
        for k, v in r.items():
            if k not in ('outcome', 'n'):
                print(f"    {k}: {v}")

conn.close()
print("\n\nDone.")
