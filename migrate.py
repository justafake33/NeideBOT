#!/usr/bin/env python3
"""Rodar manualmente quando o schema precisar ser atualizado.

Uso:
    python migrate.py

No Railway, executar via "Run Command" ou "One-Off Task" — nunca durante o startup
normal do web/worker (que usa RUN_DB_MIGRATIONS=0 por padrão).

Por que separado:
    init_db() usa ALTER TABLE ADD COLUMN IF NOT EXISTS e CREATE INDEX IF NOT EXISTS,
    que requerem AccessExclusiveLock / ShareLock nas tabelas. Se rodar concorrentemente
    com queries analíticas (que mantêm AccessShareLock), ocorre deadlock.
    Rodar migrate.py isoladamente elimina essa possibilidade.
"""

import logging
import os

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

if not os.getenv("DATABASE_URL"):
    raise SystemExit("DATABASE_URL não configurado. Abortar.")

import time
import psycopg2
import db  # noqa: E402 — importar depois de configurar logging

logger = logging.getLogger(__name__)

_MAX_WAIT = int(os.getenv("MIGRATE_MAX_WAIT_SEC", "120"))
_start = time.time()
while True:
    try:
        db.init_db()
        break
    except psycopg2.OperationalError as e:
        elapsed = time.time() - _start
        if elapsed >= _MAX_WAIT:
            raise SystemExit(f"Banco indisponível após {_MAX_WAIT}s: {e}") from e
        logger.warning("Banco indisponível (%.0fs elapsed), aguardando 5s... (%s)", elapsed, e)
        time.sleep(5)

db.backfill_data_quality()
print("Migrations concluídas.")
