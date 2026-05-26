-- ============================================================
-- NeideBOT — Diagnóstico Railway (últimas 12h)
-- Cole no console SQL do Railway e execute bloco a bloco.
-- ============================================================

-- ── 1. TRADES ────────────────────────────────────────────────
-- Quantos trades nas últimas 12h e seu status?
SELECT
    status,
    COUNT(*) AS n,
    MIN(created_at AT TIME ZONE 'America/Sao_Paulo') AS primeiro,
    MAX(created_at AT TIME ZONE 'America/Sao_Paulo') AS ultimo
FROM trades
WHERE created_at >= NOW() - INTERVAL '12 hours'
GROUP BY status
ORDER BY n DESC;

-- Últimos 10 trades (qualquer status)
SELECT
    id,
    symbol,
    status,
    error_msg,
    data_quality,
    created_at AT TIME ZONE 'America/Sao_Paulo' AS ts_brt,
    mc_at_signal,
    bounce_pct_signal,
    signal_elapsed_sec
FROM trades
ORDER BY created_at DESC
LIMIT 10;


-- ── 2. BOUNCE SIGNALS ─────────────────────────────────────────
-- Quantos bounces dispararam nas últimas 12h?
SELECT
    COUNT(*) AS total_bounces,
    MIN(ts_signal AT TIME ZONE 'America/Sao_Paulo') AS primeiro,
    MAX(ts_signal AT TIME ZONE 'America/Sao_Paulo') AS ultimo
FROM bounce_signals
WHERE ts_signal >= NOW() - INTERVAL '12 hours';

-- Últimos 5 bounce signals com detalhes
SELECT
    symbol,
    data_quality,
    ts_signal AT TIME ZONE 'America/Sao_Paulo' AS ts_brt,
    elapsed_sec / 60.0                          AS elapsed_min,
    ROUND(bounce_pct * 100, 1)                  AS bounce_pct,
    ROUND(drop_from_mig * 100, 1)               AS drop_pct,
    mc_min,
    mc_at_signal,
    mc_migration,
    pool_quote_at_signal
FROM bounce_signals
ORDER BY ts_signal DESC
LIMIT 5;


-- ── 3. MIGRATIONS DETECTADAS ──────────────────────────────────
-- Tokens que migraram nas últimas 12h (scanner está rodando?)
SELECT
    COUNT(*) AS migrações_detectadas,
    MIN(ts_migration AT TIME ZONE 'America/Sao_Paulo') AS primeira,
    MAX(ts_migration AT TIME ZONE 'America/Sao_Paulo') AS ultima
FROM migrations
WHERE ts_migration >= NOW() - INTERVAL '12 hours';

-- Últimas 10 migrações com dados de qualidade
SELECT
    token_nome                                         AS symbol,
    ts_migration AT TIME ZONE 'America/Sao_Paulo'     AS ts_brt,
    data_quality,
    bottom_quality,
    ROUND(bottom_error_pct * 100, 1)                  AS err_pct,
    mc_migration,
    mc_bottom,
    exchange,
    smart_wallets,
    ROUND(rug_ratio * 100, 1)                         AS rug_pct
FROM migrations
WHERE ts_migration >= NOW() - INTERVAL '12 hours'
ORDER BY ts_migration DESC
LIMIT 10;


-- ── 4. POR QUE NÃO HOUVE TRADES? ─────────────────────────────
-- Tokens que migraram mas não geraram bounce_signal
-- (scanner detectou, HotMonitor ativo, mas filtros bloquearam)
SELECT
    m.token_nome                                      AS symbol,
    m.ts_migration AT TIME ZONE 'America/Sao_Paulo'  AS ts_mig,
    m.data_quality,
    m.mc_migration,
    m.mc_bottom,
    m.smart_wallets,
    ROUND(m.rug_ratio * 100, 1)                       AS rug_pct,
    ROUND(m.rat_trader_rate * 100, 1)                 AS rat_pct,
    m.exchange,
    b.ts_signal AT TIME ZONE 'America/Sao_Paulo'      AS bounce_ts,
    b.bounce_pct,
    b.elapsed_sec / 60.0                              AS bounce_elapsed_min
FROM migrations m
LEFT JOIN bounce_signals b ON b.mint = m.mint
WHERE m.ts_migration >= NOW() - INTERVAL '12 hours'
ORDER BY m.ts_migration DESC;


-- ── 5. WATCHLIST (tokens em bc_progress ≥ 80%) ───────────────
-- Tokens que entraram na watchlist mas ainda não migraram
SELECT
    token_nome   AS symbol,
    ts_detected AT TIME ZONE 'America/Sao_Paulo' AS detectado,
    bc_progress_at_detection,
    ts_migration,   -- NULL = ainda não migrou
    exchange
FROM migrations
WHERE ts_detected >= NOW() - INTERVAL '12 hours'
  AND ts_migration IS NULL
ORDER BY ts_detected DESC
LIMIT 10;


-- ── 6. RESUMO EXECUTIVO ───────────────────────────────────────
SELECT
    (SELECT COUNT(*) FROM migrations
     WHERE ts_detected  >= NOW() - INTERVAL '12 hours')          AS watchlist_12h,
    (SELECT COUNT(*) FROM migrations
     WHERE ts_migration >= NOW() - INTERVAL '12 hours')          AS migrações_12h,
    (SELECT COUNT(*) FROM bounce_signals
     WHERE ts_signal    >= NOW() - INTERVAL '12 hours')          AS bounces_12h,
    (SELECT COUNT(*) FROM trades
     WHERE created_at   >= NOW() - INTERVAL '12 hours')          AS trades_12h,
    (SELECT COUNT(*) FROM trades
     WHERE created_at   >= NOW() - INTERVAL '12 hours'
       AND status = 'error')                                      AS trade_errors_12h,
    (SELECT MAX(ts_migration) AT TIME ZONE 'America/Sao_Paulo'
     FROM migrations)                                             AS ultima_migração,
    (SELECT MAX(ts_signal) AT TIME ZONE 'America/Sao_Paulo'
     FROM bounce_signals)                                         AS ultimo_bounce,
    (SELECT MAX(created_at) AT TIME ZONE 'America/Sao_Paulo'
     FROM trades)                                                 AS ultimo_trade;
