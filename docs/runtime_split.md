# NeideBOT runtime split — web + worker

Este documento resume o runtime atual para reduzir conflitos nos documentos principais.

## Processos Railway

- `web`: Flask/Gunicorn para dashboards e endpoints HTTP.
- `worker`: processo dedicado para scanner, HotMonitor, Helius/eRPC e executor.

O `worker` deve iniciar com `RUN_BOT_SERVICES=1`; o `web` deve ficar em modo web-only.

## Start command manual no Railway

Se o `Procfile` do `main` estiver em conflito, configure o serviço `worker` diretamente no Railway com:

```bash
RUN_BOT_SERVICES=1 python bot_worker.py
```

## Endpoints operacionais

- `/debug/my-ip`: mostra o IP de saída do processo web.
- `/debug/runtime-ips`: mostra heartbeats runtime e o IP do worker; use o `worker.outbound_ip` para whitelist GMGN.
- `/debug/schema`: healthcheck read-only do schema runtime; não executa `ALTER TABLE`.

## Config recomendada para micro-calibração V1.5

```env
TRADING_ENABLED=true
CALIBRATION_AMOUNT_USD=1
CALIBRATION_MAX_TRADES=500
SOL_USD_FALLBACK=65
BOUNCE_RECORD_MIN_POOL_SOL=5
RUNTIME_HEARTBEAT_ENABLED=true
RUNTIME_HEARTBEAT_INTERVAL_SEC=300
TRADE_GATE_ENABLED=true
TRADE_REQUIRE_HELIUS_FEATURES=true
TRADE_REQUIRE_H23_BOTTOM=true
TRADE_MAX_ELAPSED_SEC=300
TRADE_MIN_NET_SOL_FLOW=-5
TRADE_MIN_POOL_SOL=40
```

## V2 shadow

O V2 pré-bounce permanece research/shadow. Use `/analise/v2-shadow-live` para materializar decisões V2.1 a partir de `micro_absorption_snapshots` sem executar swaps.

## Migrations

Migrations continuam fora do startup normal. Quando houver mudança de schema, rode temporariamente como one-off/pre-deploy:

```bash
RUN_DB_MIGRATIONS=1 python migrate.py
```

Depois confirme `/debug/schema` = OK antes de habilitar compras reais.
