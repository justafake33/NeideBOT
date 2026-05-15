"""
Rodada 2 — Signal-Level Edge Analysis
Pergunta central: O sinal bruto tem EV positivo ANTES da execução?

Fontes: bounce_signals (entrada teórica), token_ticks (caminho pós-sinal),
        migrations (contexto/cohorts apenas)

NÃO usa: trades, pnl_pct_real, status, slippage, fee, entry_price_usd,
          tokens_received, latency_sec
"""
import os, sys
import psycopg2, psycopg2.extras

DB = os.getenv("DATABASE_URL")
if not DB:
    sys.exit("DATABASE_URL não configurado")


def qall(sql):
    c = psycopg2.connect(DB)
    cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql)
    rows = cur.fetchall()
    c.close()
    return rows


def sep(title):
    print(f"\n{'='*72}")
    print(f"  {title}")
    print(f"{'='*72}")


def tbl(headers, rows, widths=None):
    if not rows:
        print("  (sem dados)")
        return
    widths = widths or [18] * len(headers)
    fmt = [f"<{w}" for w in widths]
    line = "  " + "  ".join(f"{h:{f}}" for h, f in zip(headers, fmt))
    print(line)
    print("  " + "-" * (sum(widths) + 2 * len(widths)))
    for row in rows:
        vals = list(row.values()) if hasattr(row, "values") else list(row)
        print("  " + "  ".join(
            f"{(str(v) if v is not None else 'NULL'):{f}}"
            for v, f in zip(vals, fmt)
        ))


# ─────────────────────────────────────────────────────────────────────────────
sep("1. COBERTURA DO SINAL")
# ─────────────────────────────────────────────────────────────────────────────

r = qall("""
SELECT
  COUNT(*)                                                  AS total_bounces,
  COUNT(*) FILTER (WHERE mc_at_signal IS NOT NULL
                     AND mc_at_signal > 0)                 AS valid_entry,
  ROUND(100.0 * COUNT(*) FILTER (WHERE mc_at_signal IS NULL)
        / COUNT(*), 1)                                     AS null_rate_pct,
  COUNT(*) FILTER (WHERE mc_min      IS NOT NULL)          AS has_mc_min,
  COUNT(*) FILTER (WHERE mc_migration IS NOT NULL)         AS has_migration,
  COUNT(*) FILTER (WHERE absorption_events > 0)            AS has_helius_abs
FROM bounce_signals
""")
print("\n1a. bounce_signals totais:")
for k, v in r[0].items():
    print(f"  {k:<35} {v}")

# Cobertura de ticks por janela
r = qall("""
WITH sigs AS (
  SELECT mint, ts_signal
  FROM bounce_signals
  WHERE mc_at_signal IS NOT NULL AND mc_at_signal > 0
),
cov AS (
  SELECT s.mint,
    COUNT(t.id) FILTER (WHERE t.tick_ts BETWEEN s.ts_signal AND s.ts_signal + INTERVAL '5 min')   AS t5,
    COUNT(t.id) FILTER (WHERE t.tick_ts BETWEEN s.ts_signal AND s.ts_signal + INTERVAL '10 min')  AS t10,
    COUNT(t.id) FILTER (WHERE t.tick_ts BETWEEN s.ts_signal AND s.ts_signal + INTERVAL '20 min')  AS t20,
    COUNT(t.id) FILTER (WHERE t.tick_ts BETWEEN s.ts_signal AND s.ts_signal + INTERVAL '30 min')  AS t30,
    COUNT(t.id) FILTER (WHERE t.tick_ts BETWEEN s.ts_signal AND s.ts_signal + INTERVAL '60 min')  AS t60
  FROM sigs s
  LEFT JOIN token_ticks t ON t.mint = s.mint AND t.tick_ts > s.ts_signal
  GROUP BY s.mint
)
SELECT
  COUNT(*)                          AS signals_valid_entry,
  COUNT(*) FILTER (WHERE t5  >= 1) AS cov_5min,
  COUNT(*) FILTER (WHERE t10 >= 2) AS cov_10min,
  COUNT(*) FILTER (WHERE t20 >= 3) AS cov_20min,
  COUNT(*) FILTER (WHERE t30 >= 4) AS cov_30min,
  COUNT(*) FILTER (WHERE t60 >= 5) AS cov_60min
FROM cov
""")
print("\n1b. Cobertura de ticks após o sinal:")
for k, v in r[0].items():
    print(f"  {k:<35} {v}")

r = qall("""
SELECT source, COUNT(*) AS n_ticks, COUNT(DISTINCT mint) AS tokens
FROM token_ticks
GROUP BY source ORDER BY n_ticks DESC
""")
print("\n1c. Ticks por source:")
tbl(["source", "n_ticks", "tokens"], r, [15, 10, 8])


# ─────────────────────────────────────────────────────────────────────────────
sep("2. CAMINHO PÓS-SINAL — MFE / MAE por janela")
# ─────────────────────────────────────────────────────────────────────────────

r = qall("""
WITH sigs AS (
  SELECT mint, ts_signal, mc_at_signal AS entry
  FROM bounce_signals
  WHERE mc_at_signal IS NOT NULL AND mc_at_signal > 0
),
ticks AS (
  SELECT
    s.mint, s.entry,
    COALESCE(t.mc_high, t.mc) AS hi,
    COALESCE(t.mc_low,  t.mc) AS lo,
    CASE
      WHEN t.tick_ts <= s.ts_signal + INTERVAL  '5 min' THEN '05m'
      WHEN t.tick_ts <= s.ts_signal + INTERVAL '10 min' THEN '10m'
      WHEN t.tick_ts <= s.ts_signal + INTERVAL '20 min' THEN '20m'
      WHEN t.tick_ts <= s.ts_signal + INTERVAL '30 min' THEN '30m'
      WHEN t.tick_ts <= s.ts_signal + INTERVAL '60 min' THEN '60m'
    END AS w
  FROM sigs s
  JOIN token_ticks t ON t.mint = s.mint
  WHERE t.tick_ts > s.ts_signal
    AND t.tick_ts <= s.ts_signal + INTERVAL '60 min'
),
agg AS (
  SELECT mint, w, entry,
    MAX(hi) / entry - 1 AS mfe,
    MIN(lo) / entry - 1 AS mae
  FROM ticks
  GROUP BY mint, w, entry
)
SELECT
  w AS window,
  COUNT(*) AS n,
  ROUND(AVG(mfe)*100, 1)                                              AS avg_mfe_pct,
  ROUND(PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY mfe)*100, 1)   AS med_mfe_pct,
  ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY mfe)*100, 1)   AS p75_mfe_pct,
  ROUND(PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY mfe)*100, 1)   AS p90_mfe_pct,
  ROUND(AVG(mae)*100, 1)                                             AS avg_mae_pct,
  ROUND(PERCENTILE_CONT(0.5)  WITHIN GROUP (ORDER BY mae)*100, 1)   AS med_mae_pct,
  ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY mae)*100, 1)   AS p25_mae_pct,
  ROUND(PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY mae)*100, 1)   AS p10_mae_pct
FROM agg
GROUP BY w
ORDER BY w
""")
print("\nMFE/MAE acumulado por janela (entry = mc_at_signal):")
tbl(
    ["window","n","avgMFE%","medMFE%","p75MFE%","p90MFE%",
     "avgMAE%","medMAE%","p25MAE%","p10MAE%"],
    r, [8,6,9,9,9,9,9,9,9,9]
)


# ─────────────────────────────────────────────────────────────────────────────
sep("3. GRID TP/SL BRUTO (conservador: ambíguo = SL)")
# ─────────────────────────────────────────────────────────────────────────────
# Abordagem: para cada sinal, achar o PRIMEIRO tick que cruzou TP ou SL.
# Se o mesmo tick cruza ambos → ambíguo → conservador = SL.
# Sinais sem nenhum cruzamento em 60min → unresolved.

print("\nCalculando grid TP/SL — pode levar 1-2 min...")

r = qall("""
WITH sigs AS (
  SELECT mint, ts_signal, mc_at_signal AS entry
  FROM bounce_signals
  WHERE mc_at_signal IS NOT NULL AND mc_at_signal > 0
),
-- Para cada sinal e cada par TP/SL, achar o primeiro tick que cruzou algum nível
first_cross AS (
  SELECT DISTINCT ON (s.mint, p.tp, p.sl)
    s.mint,
    s.entry,
    p.tp,
    p.sl,
    EXTRACT(EPOCH FROM (t.tick_ts - s.ts_signal)) / 60.0 AS elapsed_min,
    COALESCE(t.mc_high, t.mc) >= s.entry * p.tp  AS hit_tp,
    COALESCE(t.mc_low,  t.mc) <= s.entry * p.sl  AS hit_sl
  FROM sigs s
  JOIN token_ticks t
    ON t.mint = s.mint
   AND t.tick_ts > s.ts_signal
   AND t.tick_ts <= s.ts_signal + INTERVAL '60 min'
  CROSS JOIN (VALUES
    (1.30, 0.90),(1.30, 0.85),(1.30, 0.80),(1.30, 0.75),(1.30, 0.70),(1.30, 0.60),
    (1.50, 0.90),(1.50, 0.85),(1.50, 0.80),(1.50, 0.75),(1.50, 0.70),(1.50, 0.60),
    (1.75, 0.90),(1.75, 0.85),(1.75, 0.80),(1.75, 0.75),(1.75, 0.70),(1.75, 0.60),
    (2.00, 0.90),(2.00, 0.85),(2.00, 0.80),(2.00, 0.75),(2.00, 0.70),(2.00, 0.60),
    (2.50, 0.90),(2.50, 0.85),(2.50, 0.80),(2.50, 0.75),(2.50, 0.70),(2.50, 0.60),
    (3.00, 0.90),(3.00, 0.85),(3.00, 0.80),(3.00, 0.75),(3.00, 0.70),(3.00, 0.60)
  ) AS p(tp, sl)
  WHERE COALESCE(t.mc_high, t.mc) >= s.entry * p.tp
     OR COALESCE(t.mc_low,  t.mc) <= s.entry * p.sl
  ORDER BY s.mint, p.tp, p.sl, t.tick_ts
),
-- Atribuir outcome
outcomes AS (
  SELECT
    mint, tp, sl, elapsed_min,
    hit_tp, hit_sl,
    (hit_tp AND hit_sl) AS ambiguous,
    CASE
      WHEN hit_tp AND hit_sl THEN 'sl'   -- conservador
      WHEN hit_tp             THEN 'tp'
      ELSE                        'sl'
    END AS outcome
  FROM first_cross
),
-- Somar unresolved (nunca cruzou nenhum nível)
all_combos AS (
  SELECT s.mint, p.tp, p.sl
  FROM sigs s
  CROSS JOIN (VALUES
    (1.30,0.90),(1.30,0.85),(1.30,0.80),(1.30,0.75),(1.30,0.70),(1.30,0.60),
    (1.50,0.90),(1.50,0.85),(1.50,0.80),(1.50,0.75),(1.50,0.70),(1.50,0.60),
    (1.75,0.90),(1.75,0.85),(1.75,0.80),(1.75,0.75),(1.75,0.70),(1.75,0.60),
    (2.00,0.90),(2.00,0.85),(2.00,0.80),(2.00,0.75),(2.00,0.70),(2.00,0.60),
    (2.50,0.90),(2.50,0.85),(2.50,0.80),(2.50,0.75),(2.50,0.70),(2.50,0.60),
    (3.00,0.90),(3.00,0.85),(3.00,0.80),(3.00,0.75),(3.00,0.70),(3.00,0.60)
  ) AS p(tp,sl)
),
full_res AS (
  SELECT ac.tp, ac.sl,
    COALESCE(o.outcome, 'unresolved')   AS outcome,
    o.ambiguous,
    o.elapsed_min,
    CASE WHEN o.outcome='tp' THEN o.tp - 1.0 ELSE NULL END AS win_ret,
    CASE WHEN COALESCE(o.outcome,'unresolved')='sl' THEN o.sl - 1.0 ELSE NULL END AS loss_ret
  FROM all_combos ac
  LEFT JOIN outcomes o USING (mint, tp, sl)
)
SELECT
  ROUND((tp - 1) * 100)              AS tp_pct,
  ROUND((1 - sl) * 100)              AS sl_pct,
  COUNT(*)                            AS n,
  SUM((outcome='tp')::int)            AS wins,
  SUM((outcome='sl')::int)            AS losses,
  SUM((outcome='unresolved')::int)    AS unresolved,
  SUM(COALESCE(ambiguous,false)::int) AS ambiguous,
  ROUND(
    100.0 * SUM((outcome='tp')::int)
          / NULLIF(SUM((outcome IN ('tp','sl'))::int), 0)
  , 1)                                AS wr_pct,
  -- EV = WR * tp_gain + (1-WR) * sl_loss  (sl_loss é negativo)
  ROUND((
      SUM((outcome='tp')::int)::float
      / NULLIF(SUM((outcome IN ('tp','sl'))::int), 0)
      * (tp - 1.0)
    + (1.0 - SUM((outcome='tp')::int)::float
             / NULLIF(SUM((outcome IN ('tp','sl'))::int), 0))
      * (sl - 1.0)
  ) * 100, 2)                         AS ev_bruto_pct,
  ROUND(AVG(CASE WHEN outcome='tp' THEN elapsed_min END), 1)  AS avg_min_tp,
  ROUND(AVG(CASE WHEN outcome='sl' THEN elapsed_min END), 1)  AS avg_min_sl
FROM full_res
GROUP BY tp, sl
ORDER BY ev_bruto_pct DESC
""")

print("\nGrid completo (ordenado por EV bruto):")
tbl(
    ["TP%","SL%","n","wins","losses","unresolv","ambig","WR%","EV%","minTP","minSL"],
    r,
    [5,5,6,6,7,9,7,6,9,7,7]
)
print("\nTop 10 por EV bruto:")
tbl(
    ["TP%","SL%","n","wins","losses","unresolv","ambig","WR%","EV%","minTP","minSL"],
    r[:10],
    [5,5,6,6,7,9,7,6,9,7,7]
)


# ─────────────────────────────────────────────────────────────────────────────
sep("4. AMBIGUIDADE — ticks onde bate TP e SL simultaneamente")
# ─────────────────────────────────────────────────────────────────────────────

r = qall("""
WITH sigs AS (
  SELECT mint, ts_signal, mc_at_signal AS entry
  FROM bounce_signals
  WHERE mc_at_signal IS NOT NULL AND mc_at_signal > 0
),
ticks AS (
  SELECT s.mint, s.entry,
    COALESCE(t.mc_high, t.mc) AS hi,
    COALESCE(t.mc_low,  t.mc) AS lo
  FROM sigs s
  JOIN token_ticks t ON t.mint = s.mint
  WHERE t.tick_ts > s.ts_signal
    AND t.tick_ts <= s.ts_signal + INTERVAL '60 min'
)
SELECT
  COUNT(*) FILTER (WHERE hi >= entry*1.30 AND lo <= entry*0.85) AS amb_tp30_sl15,
  COUNT(*) FILTER (WHERE hi >= entry*1.75 AND lo <= entry*0.80) AS amb_tp75_sl20,
  COUNT(*) FILTER (WHERE hi >= entry*2.00 AND lo <= entry*0.75) AS amb_tp100_sl25,
  COUNT(*) FILTER (WHERE hi >= entry*3.00 AND lo <= entry*0.70) AS amb_tp200_sl30,
  COUNT(DISTINCT mint) FILTER (WHERE hi >= entry*1.30 AND lo <= entry*0.85) AS tokens_amb
FROM ticks
""")
print("\nTicks ambíguos (bate TP e SL no mesmo candle):")
for k, v in r[0].items():
    print(f"  {k:<40} {v}")


# ─────────────────────────────────────────────────────────────────────────────
sep("5. TRAILING BRUTO (janela 60min, entry = mc_at_signal)")
# ─────────────────────────────────────────────────────────────────────────────

print("\nCalculando trailing — pode levar 1-2 min...")

r = qall("""
WITH sigs AS (
  SELECT mint, ts_signal, mc_at_signal AS entry
  FROM bounce_signals
  WHERE mc_at_signal IS NOT NULL AND mc_at_signal > 0
),
ticks AS (
  SELECT
    s.mint, s.entry,
    t.tick_ts,
    COALESCE(t.mc_high, t.mc) AS hi,
    COALESCE(t.mc_low,  t.mc) AS lo,
    EXTRACT(EPOCH FROM (t.tick_ts - s.ts_signal)) / 60.0 AS elapsed_min
  FROM sigs s
  JOIN token_ticks t ON t.mint = s.mint
  WHERE t.tick_ts > s.ts_signal
    AND t.tick_ts <= s.ts_signal + INTERVAL '60 min'
),
-- Peak corrente por sinal (running max de hi)
running AS (
  SELECT *,
    MAX(hi) OVER (
      PARTITION BY mint
      ORDER BY tick_ts
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS peak
  FROM ticks
),
-- Combinações: activation threshold × drawdown from peak
combos AS (
  SELECT r.*, p.act, p.dd
  FROM running r
  CROSS JOIN (VALUES
    (1.50, 0.20),(1.50, 0.30),(1.50, 0.40),(1.50, 0.50),
    (1.75, 0.20),(1.75, 0.30),(1.75, 0.40),(1.75, 0.50),
    (2.00, 0.20),(2.00, 0.30),(2.00, 0.40),(2.00, 0.50),
    (2.50, 0.20),(2.50, 0.30),(2.50, 0.40),(2.50, 0.50)
  ) AS p(act, dd)
  WHERE peak >= entry * p.act   -- trailing só ativo após ativação
),
-- Primeiro tick onde trailing dispara (price caiu dd% do peak)
trail_exit AS (
  SELECT DISTINCT ON (mint, act, dd)
    mint, entry, act, dd, elapsed_min,
    peak,
    lo / entry - 1.0     AS exit_ret,
    peak / entry - 1.0   AS peak_ret
  FROM combos
  WHERE lo <= peak * (1.0 - dd)
  ORDER BY mint, act, dd, tick_ts
),
-- Sinais que ativaram trailing mas não saíram (expiraram sem dd disparo)
activated AS (
  SELECT DISTINCT mint, act, dd
  FROM combos
),
all_combos AS (
  SELECT s.mint, p.act, p.dd
  FROM sigs s
  CROSS JOIN (VALUES
    (1.50,0.20),(1.50,0.30),(1.50,0.40),(1.50,0.50),
    (1.75,0.20),(1.75,0.30),(1.75,0.40),(1.75,0.50),
    (2.00,0.20),(2.00,0.30),(2.00,0.40),(2.00,0.50),
    (2.50,0.20),(2.50,0.30),(2.50,0.40),(2.50,0.50)
  ) AS p(act,dd)
)
SELECT
  ROUND((act - 1)*100) AS activation_pct,
  ROUND(dd * 100)       AS drawdown_pct,
  -- Total de sinais na base
  (SELECT COUNT(*) FROM sigs)   AS n_signals,
  -- Quantos ativaram trailing
  COUNT(DISTINCT a.mint)        AS n_activated,
  -- Quantos saíram (trailing disparou)
  COUNT(te.mint)                AS n_exits,
  -- Quantos nunca ativaram
  (SELECT COUNT(*) FROM sigs) - COUNT(DISTINCT a.mint) AS n_no_activate,
  -- Resultado dos que saíram
  ROUND(AVG(te.exit_ret)*100, 1)                       AS avg_exit_ret_pct,
  ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP
        (ORDER BY te.exit_ret)*100, 1)                 AS med_exit_ret_pct,
  ROUND(MAX(te.exit_ret)*100, 1)                        AS max_exit_ret_pct,
  ROUND(MIN(te.exit_ret)*100, 1)                        AS min_exit_ret_pct,
  ROUND(AVG(te.peak_ret)*100, 1)                        AS avg_peak_pct,
  ROUND(AVG(te.elapsed_min), 1)                         AS avg_min_to_exit
FROM all_combos ac
LEFT JOIN activated a USING (mint, act, dd)
LEFT JOIN trail_exit te USING (mint, act, dd)
GROUP BY act, dd
ORDER BY avg_exit_ret_pct DESC NULLS LAST
""")
print("\nTrailing bruto (todas as combinações):")
tbl(
    ["act%","dd%","n_sigs","n_activ","n_exits","n_no_act",
     "avgRet%","medRet%","maxRet%","minRet%","avgPeak%","avgMin"],
    r, [6,5,8,9,8,9,9,9,9,9,10,8]
)


# ─────────────────────────────────────────────────────────────────────────────
sep("6. CLASSIFICAÇÃO DO COMPORTAMENTO PÓS-SINAL (janela 30min)")
# ─────────────────────────────────────────────────────────────────────────────

r = qall("""
WITH sigs AS (
  SELECT bs.mint, bs.ts_signal, bs.mc_at_signal AS entry,
         bs.elapsed_sec, bs.bounce_pct, bs.drop_from_mig,
         bs.mc_min, bs.mc_migration
  FROM bounce_signals bs
  WHERE bs.mc_at_signal IS NOT NULL AND bs.mc_at_signal > 0
),
agg AS (
  SELECT
    s.mint, s.entry, s.elapsed_sec, s.bounce_pct,
    s.drop_from_mig, s.mc_min, s.mc_migration,
    MAX(COALESCE(t.mc_high, t.mc))  AS max_hi,
    MIN(COALESCE(t.mc_low,  t.mc))  AS min_lo,
    COUNT(t.id)                      AS n_ticks,
    -- Tempo até máxima e mínima
    MIN(EXTRACT(EPOCH FROM (t.tick_ts - s.ts_signal))/60)
        FILTER (WHERE COALESCE(t.mc_high,t.mc) =
          (SELECT MAX(COALESCE(t2.mc_high,t2.mc))
           FROM token_ticks t2
           WHERE t2.mint=s.mint
             AND t2.tick_ts > s.ts_signal
             AND t2.tick_ts <= s.ts_signal + INTERVAL '30 min')) AS min_to_hi,
    MIN(EXTRACT(EPOCH FROM (t.tick_ts - s.ts_signal))/60)
        FILTER (WHERE COALESCE(t.mc_low,t.mc) =
          (SELECT MIN(COALESCE(t2.mc_low,t2.mc))
           FROM token_ticks t2
           WHERE t2.mint=s.mint
             AND t2.tick_ts > s.ts_signal
             AND t2.tick_ts <= s.ts_signal + INTERVAL '30 min')) AS min_to_lo
  FROM sigs s
  LEFT JOIN token_ticks t
    ON t.mint = s.mint
   AND t.tick_ts > s.ts_signal
   AND t.tick_ts <= s.ts_signal + INTERVAL '30 min'
  GROUP BY s.mint, s.entry, s.elapsed_sec, s.bounce_pct,
           s.drop_from_mig, s.mc_min, s.mc_migration
),
classified AS (
  SELECT *,
    CASE
      WHEN n_ticks = 0 THEN 'no_data'
      -- instant_death: SL -25% em ≤2min
      WHEN min_lo <= entry * 0.75
        AND min_to_lo <= 2.0   THEN 'instant_death'
      -- continuation_dump: SL antes de qualquer TP
      WHEN min_lo <= entry * 0.75
        AND (max_hi < entry * 1.75
          OR (min_to_lo IS NOT NULL AND min_to_hi IS NOT NULL AND min_to_lo < min_to_hi))
                               THEN 'continuation_dump'
      -- moon: +150% antes de dump
      WHEN max_hi >= entry * 2.50 THEN 'moon'
      -- real_bounce: +75% antes de dump
      WHEN max_hi >= entry * 1.75 THEN 'real_bounce'
      -- small_bounce: +30% a +75%
      WHEN max_hi >= entry * 1.30 THEN 'small_bounce'
      ELSE 'chop'
    END AS cat
  FROM agg
)
SELECT
  cat AS category,
  COUNT(*) AS n,
  ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct,
  ROUND(AVG((max_hi/entry - 1)*100), 1)   AS avg_mfe_pct,
  ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY max_hi/entry - 1)*100, 1) AS med_mfe_pct,
  ROUND(AVG((min_lo/entry - 1)*100), 1)   AS avg_mae_pct,
  ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY min_lo/entry - 1)*100, 1) AS med_mae_pct,
  ROUND(AVG(min_to_hi), 1)                AS avg_min_peak,
  ROUND(AVG(min_to_lo), 1)                AS avg_min_bottom,
  ROUND(AVG(bounce_pct)*100, 1)           AS avg_bounce_pct,
  ROUND(AVG(elapsed_sec)/60, 1)           AS avg_elapsed_min
FROM classified
GROUP BY cat
ORDER BY n DESC
""")
print("\nClassificação pós-sinal:")
tbl(
    ["category","n","pct%","avgMFE%","medMFE%","avgMAE%","medMAE%",
     "minPeak","minBot","bnc%","elapsed_min"],
    r, [18,5,6,9,9,9,9,8,8,6,12]
)


# ─────────────────────────────────────────────────────────────────────────────
sep("7. FEATURES DO SINAL — WINNERS vs LOSERS vs CHOP")
# ─────────────────────────────────────────────────────────────────────────────

r = qall("""
WITH sigs AS (
  SELECT bs.mint, bs.ts_signal, bs.mc_at_signal AS entry,
         bs.elapsed_sec, bs.bounce_pct, bs.drop_from_mig,
         bs.mc_min, bs.mc_migration,
         bs.absorption_buy_vol, bs.absorption_sell_vol,
         bs.absorption_buy_cnt, bs.absorption_sell_cnt,
         bs.absorption_lrg_buy, bs.absorption_events,
         bs.p30s_buy_vol, bs.p30s_sell_vol, bs.p30s_events,
         bs.p60s_buy_vol, bs.p60s_sell_vol, bs.p60s_events
  FROM bounce_signals bs
  WHERE bs.mc_at_signal IS NOT NULL AND bs.mc_at_signal > 0
),
agg AS (
  SELECT
    s.*,
    MAX(COALESCE(t.mc_high,t.mc)) AS max_hi,
    MIN(COALESCE(t.mc_low, t.mc)) AS min_lo,
    COUNT(t.id)                    AS n_ticks
  FROM sigs s
  LEFT JOIN token_ticks t
    ON t.mint = s.mint
   AND t.tick_ts > s.ts_signal
   AND t.tick_ts <= s.ts_signal + INTERVAL '30 min'
  GROUP BY s.mint, s.entry, s.elapsed_sec, s.bounce_pct, s.drop_from_mig,
           s.mc_min, s.mc_migration,
           s.absorption_buy_vol, s.absorption_sell_vol, s.absorption_buy_cnt,
           s.absorption_sell_cnt, s.absorption_lrg_buy, s.absorption_events,
           s.p30s_buy_vol, s.p30s_sell_vol, s.p30s_events,
           s.p60s_buy_vol, s.p60s_sell_vol, s.p60s_events, s.ts_signal
),
labeled AS (
  SELECT *,
    CASE
      WHEN n_ticks = 0       THEN 'no_data'
      WHEN max_hi >= entry * 1.75 THEN 'winner'
      WHEN min_lo <= entry * 0.75 THEN 'loser'
      ELSE 'chop'
    END AS outcome
  FROM agg
)
SELECT
  outcome,
  COUNT(*)                                  AS n,
  ROUND(AVG(elapsed_sec)/60, 1)            AS avg_elapsed_min,
  ROUND(AVG(bounce_pct)*100, 1)            AS avg_bounce_pct,
  ROUND(AVG(drop_from_mig)*100, 1)         AS avg_drop_pct,
  ROUND(AVG(mc_at_signal/NULLIF(mc_migration,0))*100, 1) AS avg_entry_ratio_pct,
  ROUND(AVG(mc_min/NULLIF(mc_migration,0))*100, 1)       AS avg_bot_ratio_pct,
  ROUND(AVG(absorption_events), 1)         AS avg_abs_events,
  ROUND(AVG(absorption_buy_vol/NULLIF(absorption_sell_vol,0)), 2) AS avg_bsv_ratio,
  ROUND(AVG(absorption_lrg_buy/NULLIF(absorption_buy_vol,0)), 2)  AS avg_lrg_conc,
  ROUND(AVG(absorption_buy_vol), 4)        AS avg_buy_sol,
  ROUND(AVG(p30s_buy_vol/NULLIF(p30s_sell_vol,0)), 2)   AS avg_p30_bsv,
  ROUND(AVG(p60s_buy_vol/NULLIF(p60s_sell_vol,0)), 2)   AS avg_p60_bsv
FROM labeled
WHERE outcome != 'no_data'
GROUP BY outcome
ORDER BY outcome
""")
print("\nFeatures médias por outcome (winner=+75%, loser=-25%, chop=entre):")
tbl(
    ["outcome","n","elapsed_min","bnc%","drop%","entry_r%","bot_r%",
     "abs_ev","bsv","lrg_c","buy_sol","p30_bsv","p60_bsv"],
    r, [10,5,12,6,6,9,7,7,6,6,9,9,9]
)


# ─────────────────────────────────────────────────────────────────────────────
sep("8. COHORTS — BUCKETS DO SINAL")
# ─────────────────────────────────────────────────────────────────────────────

def cohort(label, bucket_sql):
    rows = qall(f"""
    WITH sigs AS (
      SELECT bs.mint, bs.ts_signal, bs.mc_at_signal AS entry,
             bs.elapsed_sec, bs.bounce_pct, bs.drop_from_mig,
             bs.mc_min, bs.mc_migration
      FROM bounce_signals bs
      WHERE bs.mc_at_signal IS NOT NULL AND bs.mc_at_signal > 0
    ),
    agg AS (
      SELECT
        s.mint, s.entry,
        {bucket_sql} AS bucket,
        MAX(COALESCE(t.mc_high,t.mc)) AS max_hi,
        MIN(COALESCE(t.mc_low, t.mc)) AS min_lo,
        COUNT(t.id)                    AS n_ticks
      FROM sigs s
      LEFT JOIN token_ticks t
        ON t.mint=s.mint
       AND t.tick_ts > s.ts_signal
       AND t.tick_ts <= s.ts_signal + INTERVAL '30 min'
      GROUP BY s.mint, s.entry, s.elapsed_sec, s.bounce_pct,
               s.drop_from_mig, s.mc_min, s.mc_migration
    )
    SELECT
      COALESCE(bucket,'NULL') AS bucket,
      COUNT(*)                AS n,
      COUNT(*) FILTER (WHERE n_ticks>0) AS with_ticks,
      ROUND(100.0*COUNT(*) FILTER (WHERE max_hi>=entry*1.75 AND n_ticks>0)
            /NULLIF(COUNT(*) FILTER (WHERE n_ticks>0),0),1) AS pct_winner,
      ROUND(100.0*COUNT(*) FILTER (WHERE min_lo<=entry*0.75
                                     AND (max_hi<entry*1.75 OR max_hi IS NULL)
                                     AND n_ticks>0)
            /NULLIF(COUNT(*) FILTER (WHERE n_ticks>0),0),1) AS pct_dump,
      ROUND(100.0*COUNT(*) FILTER (WHERE max_hi>=entry*2.50 AND n_ticks>0)
            /NULLIF(COUNT(*) FILTER (WHERE n_ticks>0),0),1) AS pct_moon,
      ROUND(AVG(CASE WHEN n_ticks>0 THEN (max_hi/entry-1)*100 END),1) AS avg_mfe,
      ROUND(AVG(CASE WHEN n_ticks>0 THEN (min_lo/entry-1)*100 END),1) AS avg_mae,
      -- EV usando TP=75% SL=25% (apenas resolvidos)
      ROUND((
        SUM((max_hi>=entry*1.75 AND n_ticks>0)::int)::float
        / NULLIF(SUM(((max_hi>=entry*1.75 OR min_lo<=entry*0.75) AND n_ticks>0)::int),0)
        * 0.75
        + (1.0 - SUM((max_hi>=entry*1.75 AND n_ticks>0)::int)::float
               / NULLIF(SUM(((max_hi>=entry*1.75 OR min_lo<=entry*0.75) AND n_ticks>0)::int),0))
        * (-0.25)
      )*100, 1) AS ev_tp75_sl25_pct
    FROM agg
    GROUP BY bucket
    ORDER BY bucket
    """)
    print(f"\n--- {label} ---")
    tbl(["bucket","n","w/ticks","winner%","dump%","moon%","avgMFE%","avgMAE%","EV_75/25%"],
        rows, [22,5,8,9,7,7,9,9,11])

cohort("elapsed_sec (minutos até o sinal)",
    """CASE
       WHEN elapsed_sec <  120 THEN '01_0-2min'
       WHEN elapsed_sec <  300 THEN '02_2-5min'
       WHEN elapsed_sec <  600 THEN '03_5-10min'
       WHEN elapsed_sec <  900 THEN '04_10-15min'
       WHEN elapsed_sec < 1200 THEN '05_15-20min'
       ELSE                         '06_20min+'
    END""")

cohort("bounce_pct (força do bounce)",
    """CASE
       WHEN bounce_pct < 0.40 THEN '01_30-40%'
       WHEN bounce_pct < 0.50 THEN '02_40-50%'
       WHEN bounce_pct < 0.75 THEN '03_50-75%'
       WHEN bounce_pct < 1.00 THEN '04_75-100%'
       ELSE                        '05_100%+'
    END""")

cohort("drop_from_mig (profundidade do dump)",
    """CASE
       WHEN drop_from_mig < 0.40 THEN '01_30-40%'
       WHEN drop_from_mig < 0.50 THEN '02_40-50%'
       WHEN drop_from_mig < 0.60 THEN '03_50-60%'
       WHEN drop_from_mig < 0.70 THEN '04_60-70%'
       ELSE                           '05_70%+'
    END""")

cohort("mc_at_signal / mc_migration (entry relativo)",
    """CASE
       WHEN mc_at_signal/NULLIF(mc_migration,0) < 0.25 THEN '01_0-25%'
       WHEN mc_at_signal/NULLIF(mc_migration,0) < 0.40 THEN '02_25-40%'
       WHEN mc_at_signal/NULLIF(mc_migration,0) < 0.50 THEN '03_40-50%'
       WHEN mc_at_signal/NULLIF(mc_migration,0) < 0.65 THEN '04_50-65%'
       WHEN mc_at_signal/NULLIF(mc_migration,0) < 0.80 THEN '05_65-80%'
       ELSE                                                  '06_80%+'
    END""")

cohort("mc_min / mc_migration (fundo relativo à migração)",
    """CASE
       WHEN mc_min/NULLIF(mc_migration,0) < 0.20 THEN '01_0-20%'
       WHEN mc_min/NULLIF(mc_migration,0) < 0.30 THEN '02_20-30%'
       WHEN mc_min/NULLIF(mc_migration,0) < 0.40 THEN '03_30-40%'
       WHEN mc_min/NULLIF(mc_migration,0) < 0.50 THEN '04_40-50%'
       ELSE                                            '05_50%+'
    END""")


# ─────────────────────────────────────────────────────────────────────────────
sep("9. CONTEXTO DO TOKEN — cohorts de migrations")
# ─────────────────────────────────────────────────────────────────────────────

r = qall("""
WITH sigs AS (
  SELECT bs.mint, bs.ts_signal, bs.mc_at_signal AS entry
  FROM bounce_signals bs
  WHERE bs.mc_at_signal IS NOT NULL AND bs.mc_at_signal > 0
),
joined AS (
  SELECT
    s.mint, s.entry,
    m.smart_wallets, m.renowned_wallets,
    m.top10_holder_rate, m.liq_migration,
    m.exchange, m.bot_degen_rate,
    m.creator_token_status,
    MAX(COALESCE(t.mc_high,t.mc)) AS max_hi,
    MIN(COALESCE(t.mc_low, t.mc)) AS min_lo,
    COUNT(t.id)                    AS n_ticks
  FROM sigs s
  JOIN migrations m ON m.mint = s.mint
  LEFT JOIN token_ticks t
    ON t.mint = s.mint
   AND t.tick_ts > s.ts_signal
   AND t.tick_ts <= s.ts_signal + INTERVAL '30 min'
  GROUP BY s.mint, s.entry,
           m.smart_wallets, m.renowned_wallets,
           m.top10_holder_rate, m.liq_migration,
           m.exchange, m.bot_degen_rate, m.creator_token_status
)
SELECT
  feature, bucket, n,
  wr_pct, avg_mfe, avg_mae, ev_pct
FROM (

  SELECT 'smart_wallets' AS feature,
    CASE
      WHEN smart_wallets = 0 THEN 'smart=0'
      WHEN smart_wallets = 1 THEN 'smart=1'
      WHEN smart_wallets = 2 THEN 'smart=2'
      WHEN smart_wallets = 3 THEN 'smart=3'
      WHEN smart_wallets <= 5 THEN 'smart=4-5'
      ELSE 'smart=6+'
    END AS bucket,
    COUNT(*) FILTER (WHERE n_ticks>0) AS n,
    ROUND(100.0*COUNT(*) FILTER (WHERE max_hi>=entry*1.75 AND n_ticks>0)
          /NULLIF(COUNT(*) FILTER (WHERE n_ticks>0),0),1) AS wr_pct,
    ROUND(AVG(CASE WHEN n_ticks>0 THEN (max_hi/entry-1)*100 END),1) AS avg_mfe,
    ROUND(AVG(CASE WHEN n_ticks>0 THEN (min_lo/entry-1)*100 END),1) AS avg_mae,
    ROUND((
      SUM((max_hi>=entry*1.75 AND n_ticks>0)::int)::float
      /NULLIF(SUM(((max_hi>=entry*1.75 OR min_lo<=entry*0.75) AND n_ticks>0)::int),0)*0.75
      +(1.0-SUM((max_hi>=entry*1.75 AND n_ticks>0)::int)::float
             /NULLIF(SUM(((max_hi>=entry*1.75 OR min_lo<=entry*0.75) AND n_ticks>0)::int),0))*(-0.25)
    )*100,1) AS ev_pct
  FROM joined GROUP BY 2

  UNION ALL

  SELECT 'exchange',
    COALESCE(exchange,'unknown'),
    COUNT(*) FILTER (WHERE n_ticks>0),
    ROUND(100.0*COUNT(*) FILTER (WHERE max_hi>=entry*1.75 AND n_ticks>0)
          /NULLIF(COUNT(*) FILTER (WHERE n_ticks>0),0),1),
    ROUND(AVG(CASE WHEN n_ticks>0 THEN (max_hi/entry-1)*100 END),1),
    ROUND(AVG(CASE WHEN n_ticks>0 THEN (min_lo/entry-1)*100 END),1),
    ROUND((
      SUM((max_hi>=entry*1.75 AND n_ticks>0)::int)::float
      /NULLIF(SUM(((max_hi>=entry*1.75 OR min_lo<=entry*0.75) AND n_ticks>0)::int),0)*0.75
      +(1.0-SUM((max_hi>=entry*1.75 AND n_ticks>0)::int)::float
             /NULLIF(SUM(((max_hi>=entry*1.75 OR min_lo<=entry*0.75) AND n_ticks>0)::int),0))*(-0.25)
    )*100,1)
  FROM joined GROUP BY 2

  UNION ALL

  SELECT 'creator_status',
    COALESCE(creator_token_status,'unknown'),
    COUNT(*) FILTER (WHERE n_ticks>0),
    ROUND(100.0*COUNT(*) FILTER (WHERE max_hi>=entry*1.75 AND n_ticks>0)
          /NULLIF(COUNT(*) FILTER (WHERE n_ticks>0),0),1),
    ROUND(AVG(CASE WHEN n_ticks>0 THEN (max_hi/entry-1)*100 END),1),
    ROUND(AVG(CASE WHEN n_ticks>0 THEN (min_lo/entry-1)*100 END),1),
    ROUND((
      SUM((max_hi>=entry*1.75 AND n_ticks>0)::int)::float
      /NULLIF(SUM(((max_hi>=entry*1.75 OR min_lo<=entry*0.75) AND n_ticks>0)::int),0)*0.75
      +(1.0-SUM((max_hi>=entry*1.75 AND n_ticks>0)::int)::float
             /NULLIF(SUM(((max_hi>=entry*1.75 OR min_lo<=entry*0.75) AND n_ticks>0)::int),0))*(-0.25)
    )*100,1)
  FROM joined GROUP BY 2

  UNION ALL

  SELECT 'top10_holder_rate',
    CASE
      WHEN top10_holder_rate < 0.50 THEN 'top10<50%'
      WHEN top10_holder_rate < 0.65 THEN 'top10_50-65%'
      WHEN top10_holder_rate < 0.80 THEN 'top10_65-80%'
      ELSE 'top10>=80%'
    END,
    COUNT(*) FILTER (WHERE n_ticks>0),
    ROUND(100.0*COUNT(*) FILTER (WHERE max_hi>=entry*1.75 AND n_ticks>0)
          /NULLIF(COUNT(*) FILTER (WHERE n_ticks>0),0),1),
    ROUND(AVG(CASE WHEN n_ticks>0 THEN (max_hi/entry-1)*100 END),1),
    ROUND(AVG(CASE WHEN n_ticks>0 THEN (min_lo/entry-1)*100 END),1),
    ROUND((
      SUM((max_hi>=entry*1.75 AND n_ticks>0)::int)::float
      /NULLIF(SUM(((max_hi>=entry*1.75 OR min_lo<=entry*0.75) AND n_ticks>0)::int),0)*0.75
      +(1.0-SUM((max_hi>=entry*1.75 AND n_ticks>0)::int)::float
             /NULLIF(SUM(((max_hi>=entry*1.75 OR min_lo<=entry*0.75) AND n_ticks>0)::int),0))*(-0.25)
    )*100,1)
  FROM joined GROUP BY 2

) sub
ORDER BY feature, n DESC
""")
print("\nCohorts por features do token (⚠ n<30 = hipótese):")
tbl(
    ["feature","bucket","n","WR%","avgMFE%","avgMAE%","EV_75/25%"],
    r, [20,18,5,6,9,9,11]
)


# ─────────────────────────────────────────────────────────────────────────────
sep("10. RESPOSTA FINAL")
# ─────────────────────────────────────────────────────────────────────────────

# Pegar o melhor EV do grid (seção 3 já calculado acima)
best_ev = r[0] if r else None  # placeholder — já temos `r` da seção 3 acima

# Recalcular sumário rápido para a resposta final
summary = qall("""
WITH sigs AS (
  SELECT mint, ts_signal, mc_at_signal AS entry
  FROM bounce_signals
  WHERE mc_at_signal IS NOT NULL AND mc_at_signal > 0
),
first_cross AS (
  SELECT DISTINCT ON (s.mint, p.tp, p.sl)
    s.mint, p.tp, p.sl,
    COALESCE(t.mc_high,t.mc) >= s.entry * p.tp  AS hit_tp,
    COALESCE(t.mc_low, t.mc) <= s.entry * p.sl  AS hit_sl
  FROM sigs s
  JOIN token_ticks t
    ON t.mint=s.mint
   AND t.tick_ts > s.ts_signal
   AND t.tick_ts <= s.ts_signal + INTERVAL '60 min'
  CROSS JOIN (VALUES (2.00, 0.80), (1.75, 0.80), (2.50, 0.75)) AS p(tp,sl)
  WHERE COALESCE(t.mc_high,t.mc) >= s.entry*p.tp
     OR COALESCE(t.mc_low, t.mc) <= s.entry*p.sl
  ORDER BY s.mint, p.tp, p.sl, t.tick_ts
),
outcomes AS (
  SELECT tp, sl,
    CASE WHEN hit_tp AND hit_sl THEN 'sl'
         WHEN hit_tp THEN 'tp'
         ELSE 'sl'
    END AS outcome
  FROM first_cross
),
all_combos AS (
  SELECT s.mint, p.tp, p.sl
  FROM sigs s
  CROSS JOIN (VALUES (2.00,0.80),(1.75,0.80),(2.50,0.75)) AS p(tp,sl)
),
full AS (
  SELECT ac.tp, ac.sl, COALESCE(o.outcome,'unresolved') AS outcome
  FROM all_combos ac
  LEFT JOIN outcomes o ON o.tp=ac.tp AND o.sl=ac.sl
)
SELECT
  tp, sl,
  COUNT(*) AS n,
  SUM((outcome='tp')::int) AS wins,
  SUM((outcome='sl')::int) AS losses,
  SUM((outcome='unresolved')::int) AS unresolved,
  ROUND(100.0*SUM((outcome='tp')::int)
        /NULLIF(SUM((outcome IN ('tp','sl'))::int),0),1) AS wr_pct,
  ROUND((
    SUM((outcome='tp')::int)::float
    /NULLIF(SUM((outcome IN ('tp','sl'))::int),0)*(tp-1)
    +(1-SUM((outcome='tp')::int)::float
          /NULLIF(SUM((outcome IN ('tp','sl'))::int),0))*(sl-1)
  )*100, 2) AS ev_pct
FROM full
GROUP BY tp, sl
ORDER BY ev_pct DESC
""")

print()
for row in summary:
    tp_pct = round((row['tp']-1)*100)
    sl_pct = round((1-row['sl'])*100)
    print(f"  TP+{tp_pct}% / SL-{sl_pct}%:  "
          f"n={row['n']}  wins={row['wins']}  losses={row['losses']}  "
          f"unresolv={row['unresolved']}  WR={row['wr_pct']}%  EV={row['ev_pct']}%")

print("""
┌─────────────────────────────────────────────────────────────────────┐
│  RESPOSTAS OBRIGATÓRIAS (preencher após ver os números acima)       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  A) EV POSITIVO?                                                    │
│     → Ver EV% na seção 3. Se EV > 0 em configs com TP≥100% e       │
│       n≥30 sinais resolvidos: SIM.                                  │
│       Se EV > 0 apenas em TP≥200% com n<30: INCONCLUSIVO.          │
│       Se EV ≤ 0 em todas as configs: NÃO.                          │
│                                                                     │
│  B) MELHOR CONFIG?                                                  │
│     → A linha com maior EV% na seção 3, desde que n≥30 resolvidos. │
│       Trailing (seção 5) supera TP fixo se avg_exit_ret% for alto  │
│       com n_exits razoável.                                         │
│                                                                     │
│  C) EDGE UNIVERSAL OU EM COHORT?                                    │
│     → Seção 8: se apenas 1-2 buckets têm EV>0 e os outros são      │
│       negativos → edge concentrado. Se maioria dos buckets tem      │
│       EV>0 → edge estrutural.                                       │
│                                                                     │
│  D) PROBLEMA PRINCIPAL?                                             │
│     → (a) Se WR < 20% em todas as configs → sinal sem edge          │
│       (b) Se EV>0 apenas em TP≥200% → dependente de outliers       │
│       (c) Se cohort específico com n≥30 tem WR>35% → nicho claro   │
│       (d) Se with_ticks < 100 → dados insuficientes                │
│                                                                     │
│  E) PERGUNTAS PARA RODADA 3 (execução real):                        │
│     1. Qual o slippage médio real por faixa de mc_at_signal?        │
│     2. Qual a latência real entre ts_signal e fill on-chain?        │
│     3. Quantos sinais têm liquidez < $10k (slippage inaceitável)?  │
│     4. O trailing GMGN executa no mc correto ou há overshoot?       │
│     5. Qual o EV_real = EV_bruto − friction por faixa de size?     │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
""")

print("✅ Rodada 2 concluída.")
