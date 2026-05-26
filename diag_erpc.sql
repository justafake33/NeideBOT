-- Diagnóstico de preço — erpc recebendo eventos?
-- Se mc_bottom = NULL ou mc_bottom = mc_migration para tokens recentes,
-- o erpc está silencioso.

SELECT
    token_nome                                        AS symbol,
    ts_migration AT TIME ZONE 'America/Sao_Paulo'     AS ts_mig,
    mc_migration,
    mc_bottom,
    -- Se mc_bottom = NULL ou mc_bottom >= mc_migration * 0.95 → erpc não entregou dump
    CASE
        WHEN mc_bottom IS NULL THEN '❌ sem fundo (erpc silencioso?)'
        WHEN mc_bottom >= mc_migration * 0.95 THEN '⚠️  fundo quase igual à migração'
        ELSE '✅ fundo detectado: ' || ROUND((1 - mc_bottom/mc_migration)*100) || '% dump'
    END AS diagnostico,
    time_to_bottom,
    exchange,
    data_quality
FROM migrations
WHERE ts_migration >= NOW() - INTERVAL '12 hours'
ORDER BY ts_migration DESC
LIMIT 20;


-- Últimos token_ticks — quando foi o último dado de preço capturado?
SELECT
    m.token_nome                                      AS symbol,
    MAX(tt.ts) AT TIME ZONE 'America/Sao_Paulo'       AS ultimo_tick,
    COUNT(tt.id)                                      AS n_ticks,
    MAX(tt.mc)                                        AS mc_max_visto
FROM token_ticks tt
JOIN migrations m ON m.mint = tt.mint
WHERE tt.ts >= NOW() - INTERVAL '12 hours'
GROUP BY m.token_nome
ORDER BY ultimo_tick DESC
LIMIT 10;


-- Resumo: quantos tokens recentes têm mc_bottom NULL vs preenchido
SELECT
    CASE
        WHEN mc_bottom IS NULL THEN 'sem_mc_bottom'
        ELSE 'com_mc_bottom'
    END AS estado,
    COUNT(*) AS n
FROM migrations
WHERE ts_migration >= NOW() - INTERVAL '12 hours'
GROUP BY 1;
