"""
Retenção automática de dados com alertas Telegram.

Roda a cada 6h via HeapScheduler. Antes de deletar qualquer coisa,
envia alerta no Telegram mostrando o que será apagado. Alerta também
quando o banco ultrapassa DB_WARN_SIZE_MB.

Env vars (todas opcionais — defaults conservadores):
    DB_WARN_SIZE_MB              Alerta quando DB ultrapassa X MB (default 600)
    RETENTION_TOKEN_TICKS_DAYS   Retenção token_ticks em dias (default 30)
    RETENTION_MICRO_DAYS         Retenção micro_absorption_snapshots em dias (default 60)
    RETENTION_SELL_EXHAUST_DAYS  Retenção sell_exhaustion_candidates em dias (default 60)
    RETENTION_SIGNALS_DAYS       Retenção token_signals em dias (default 30)
    RETENTION_TRENDING_DAYS      Retenção gmgn_trending_* em dias (default 14)
    RETENTION_ABSORPTION_DAYS    Retenção trade_absorption_ticks em dias (default 30)
    RETENTION_DRY_RUN            Se "true", só alerta sem deletar (default false)
"""

import os
import logging
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor

import db
from telegram_bot import send_alert

logger = logging.getLogger(__name__)

# ── Configuração via env vars ────────────────────────────────────────────────

_WARN_SIZE_MB        = int(os.getenv("DB_WARN_SIZE_MB", "600"))
_DRY_RUN             = os.getenv("RETENTION_DRY_RUN", "false").lower() == "true"

_RETENTION = {
    "token_ticks": {
        "days":      int(os.getenv("RETENTION_TOKEN_TICKS_DAYS", "30")),
        "ts_col":    "tick_ts",
        "label":     "token_ticks (série temporal)",
    },
    "micro_absorption_snapshots": {
        "days":      int(os.getenv("RETENTION_MICRO_DAYS", "60")),
        "ts_col":    "ts",
        "label":     "micro_absorption_snapshots (V2 research)",
    },
    "sell_exhaustion_candidates": {
        "days":      int(os.getenv("RETENTION_SELL_EXHAUST_DAYS", "60")),
        "ts_col":    "ts",
        "label":     "sell_exhaustion_candidates (research)",
    },
    "token_signals": {
        "days":      int(os.getenv("RETENTION_SIGNALS_DAYS", "30")),
        "ts_col":    "ts_recorded",
        "label":     "token_signals (H9)",
    },
    "gmgn_trending_snapshots": {
        "days":      int(os.getenv("RETENTION_TRENDING_DAYS", "14")),
        "ts_col":    "ts",
        "label":     "gmgn_trending_snapshots (TrendingRadar)",
    },
    "gmgn_trending_coverage": {
        "days":      int(os.getenv("RETENTION_TRENDING_DAYS", "14")),
        "ts_col":    "ts",
        "label":     "gmgn_trending_coverage (TrendingRadar)",
    },
    "trade_absorption_ticks": {
        "days":      int(os.getenv("RETENTION_ABSORPTION_DAYS", "30")),
        "ts_col":    "ts",
        "label":     "trade_absorption_ticks (H22 research)",
    },
}

# ── Intervalo de execução ────────────────────────────────────────────────────

INTERVAL_SEC = 6 * 3600  # a cada 6h


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_db_size_mb() -> float:
    """Retorna tamanho atual do banco em MB."""
    try:
        conn = psycopg2.connect(db.DATABASE_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT pg_database_size(current_database())")
            row = cur.fetchone()
        conn.close()
        return round(row[0] / (1024 * 1024), 1) if row else 0.0
    except Exception as e:
        logger.warning("db_retention: não conseguiu medir tamanho do banco: %s", e)
        return 0.0


def _get_table_sizes() -> dict:
    """Retorna tamanho de cada tabela em MB."""
    try:
        conn = psycopg2.connect(db.DATABASE_URL)
        conn.autocommit = True
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT tablename,
                       round(pg_total_relation_size('public.'||tablename) / 1048576.0, 1) AS mb
                FROM pg_tables
                WHERE schemaname = 'public'
                ORDER BY pg_total_relation_size('public.'||tablename) DESC
            """)
            rows = cur.fetchall()
        conn.close()
        return {r["tablename"]: float(r["mb"]) for r in rows}
    except Exception as e:
        logger.warning("db_retention: não conseguiu medir tabelas: %s", e)
        return {}


def _count_rows_to_delete(table: str, ts_col: str, days: int) -> int:
    """Conta linhas que serão deletadas antes de deletar."""
    try:
        conn = psycopg2.connect(db.DATABASE_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {ts_col} < NOW() - INTERVAL '{days} days'"
            )
            row = cur.fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception as e:
        logger.warning("db_retention: erro ao contar rows de %s: %s", table, e)
        return 0


def _delete_old_rows(table: str, ts_col: str, days: int) -> int:
    """Deleta linhas antigas. Retorna quantidade deletada."""
    try:
        conn = psycopg2.connect(db.DATABASE_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {table} WHERE {ts_col} < NOW() - INTERVAL '{days} days'"
            )
            deleted = cur.rowcount
        conn.close()
        return deleted
    except Exception as e:
        logger.warning("db_retention: erro ao deletar rows de %s: %s", table, e)
        return 0


def _table_exists(table: str) -> bool:
    try:
        conn = psycopg2.connect(db.DATABASE_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename=%s",
                (table,)
            )
            exists = cur.fetchone() is not None
        conn.close()
        return exists
    except Exception:
        return False


# ── Job principal ────────────────────────────────────────────────────────────

def run_retention(scheduler=None):
    """
    Job de retenção. Chama scheduler.schedule() para reagendar a si mesmo
    após INTERVAL_SEC.
    """
    try:
        _run_retention_inner()
    except Exception as e:
        logger.exception("db_retention: erro inesperado no job de retenção")
    finally:
        if scheduler is not None:
            scheduler.schedule(INTERVAL_SEC, run_retention, scheduler)


def _run_retention_inner():
    if not db.DATABASE_URL:
        return

    now_str = datetime.now(timezone.utc).strftime("%d/%m %H:%M UTC")
    db_size_mb = _get_db_size_mb()
    table_sizes = _get_table_sizes()

    logger.info("db_retention: iniciando job | banco=%.1fMB | warn_limit=%dMB", db_size_mb, _WARN_SIZE_MB)

    # ── Alerta de disco ──────────────────────────────────────────────────────
    if db_size_mb >= _WARN_SIZE_MB:
        top_tables = sorted(table_sizes.items(), key=lambda x: x[1], reverse=True)[:5]
        top_str = "\n".join(f"  • `{t}`: {mb}MB" for t, mb in top_tables)
        send_alert(
            f"⚠️ *NeideBOT — Disco Quase Cheio*\n\n"
            f"Banco: *{db_size_mb}MB* / limite configurado: {_WARN_SIZE_MB}MB\n\n"
            f"*Maiores tabelas:*\n{top_str}\n\n"
            f"Retenção automática será executada agora.\n"
            f"_{now_str}_"
        )

    # ── Levantamento do que será deletado ────────────────────────────────────
    to_delete = []
    for table, cfg in _RETENTION.items():
        if not _table_exists(table):
            continue
        count = _count_rows_to_delete(table, cfg["ts_col"], cfg["days"])
        if count > 0:
            to_delete.append((table, cfg, count))

    if not to_delete:
        logger.info("db_retention: nada a deletar")
        return

    # ── Alerta antes de deletar ──────────────────────────────────────────────
    lines = "\n".join(
        f"  • `{cfg['label']}`: *{count:,} rows* (>{cfg['days']} dias)"
        for table, cfg, count in to_delete
    )
    mode_str = "🔍 *DRY RUN — nada será deletado*" if _DRY_RUN else "🗑️ Deletando em seguida..."

    send_alert(
        f"🧹 *NeideBOT — Limpeza Automática de Dados*\n\n"
        f"Banco atual: *{db_size_mb}MB*\n\n"
        f"*O que será apagado:*\n{lines}\n\n"
        f"{mode_str}\n"
        f"_(dados essenciais: migrations, bounce\\_signals, trades — nunca apagados)_\n"
        f"_{now_str}_"
    )

    if _DRY_RUN:
        logger.info("db_retention: dry run — nenhuma deleção executada")
        return

    # ── Deleção ──────────────────────────────────────────────────────────────
    total_deleted = 0
    results = []
    for table, cfg, _ in to_delete:
        deleted = _delete_old_rows(table, cfg["ts_col"], cfg["days"])
        total_deleted += deleted
        results.append(f"  • `{cfg['label']}`: {deleted:,} rows deletadas")
        logger.info("db_retention: %s → %d rows deletadas (>%dd)", table, deleted, cfg["days"])

    db_size_after = _get_db_size_mb()
    freed = round(db_size_mb - db_size_after, 1)

    result_str = "\n".join(results)
    send_alert(
        f"✅ *NeideBOT — Limpeza Concluída*\n\n"
        f"*Resultado:*\n{result_str}\n\n"
        f"Banco: *{db_size_mb}MB → {db_size_after}MB* (liberou ~{freed}MB)\n"
        f"Total: *{total_deleted:,} rows* removidas\n"
        f"_{now_str}_"
    )
    logger.info(
        "db_retention: concluído | %d rows deletadas | banco %.1fMB → %.1fMB (liberou %.1fMB)",
        total_deleted, db_size_mb, db_size_after, freed
    )
