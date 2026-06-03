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

import db  # noqa: E402 — importar depois de configurar logging

db.init_db()
db.backfill_data_quality()
print("Migrations concluídas.")
