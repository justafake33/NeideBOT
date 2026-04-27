import os
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)
DATABASE_URL = os.getenv("DATABASE_URL")


@contextmanager
def _cursor():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with _cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS migrations (
                id                        SERIAL PRIMARY KEY,
                mint                      TEXT UNIQUE NOT NULL,
                token_nome                TEXT,
                ts_detected               TIMESTAMPTZ,
                bc_progress_at_detection  FLOAT,
                ts_migration              TIMESTAMPTZ,
                mc_migration              FLOAT,
                mc_bottom                 FLOAT,
                time_to_bottom            FLOAT,
                pct_drop                  FLOAT,
                mc_1h                     FLOAT,
                var_1h                    FLOAT,
                mc_4h                     FLOAT,
                var_4h                    FLOAT,
                mc_24h                    FLOAT,
                var_24h                   FLOAT,
                smart_wallets             INT,
                renowned_wallets          INT,
                sniper_wallets            INT,
                rug_ratio                 FLOAT,
                top10_holder_rate         FLOAT,
                holders_count             INT,
                volume_1m                 FLOAT,
                volume_5m                 FLOAT,
                volume_15m                FLOAT,
                is_wash_trading           BOOL,
                liq_migration             FLOAT,
                is_honeypot               BOOL,
                recovery                  BOOL,
                comprado                  BOOL DEFAULT FALSE,
                resultado_pct             FLOAT,
                capture_delay_sec         INT,
                created_at                TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            ALTER TABLE migrations
            ADD COLUMN IF NOT EXISTS capture_delay_sec INT
        """)
    logger.info("DB inicializado")


def upsert_watchlist(data: dict):
    """Registra token detectado na watchlist (bc >= 90%) antes da migração."""
    with _cursor() as cur:
        cur.execute("""
            INSERT INTO migrations (
                mint, token_nome, ts_detected, bc_progress_at_detection,
                smart_wallets, renowned_wallets, sniper_wallets,
                rug_ratio, top10_holder_rate, holders_count,
                volume_1m, volume_5m, volume_15m,
                is_wash_trading, liq_migration, is_honeypot
            ) VALUES (
                %(mint)s, %(token_nome)s, %(ts_detected)s, %(bc_progress_at_detection)s,
                %(smart_wallets)s, %(renowned_wallets)s, %(sniper_wallets)s,
                %(rug_ratio)s, %(top10_holder_rate)s, %(holders_count)s,
                %(volume_1m)s, %(volume_5m)s, %(volume_15m)s,
                %(is_wash_trading)s, %(liq_migration)s, %(is_honeypot)s
            )
            ON CONFLICT (mint) DO NOTHING
        """, {
            "mint": data.get("mint"),
            "token_nome": data.get("token_nome"),
            "ts_detected": data.get("ts_detected"),
            "bc_progress_at_detection": data.get("bc_progress_at_detection"),
            "smart_wallets": data.get("smart_wallets"),
            "renowned_wallets": data.get("renowned_wallets"),
            "sniper_wallets": data.get("sniper_wallets"),
            "rug_ratio": data.get("rug_ratio"),
            "top10_holder_rate": data.get("top10_holder_rate"),
            "holders_count": data.get("holders_count"),
            "volume_1m": data.get("volume_1m"),
            "volume_5m": data.get("volume_5m"),
            "volume_15m": data.get("volume_15m"),
            "is_wash_trading": data.get("is_wash_trading"),
            "liq_migration": data.get("liq_migration"),
            "is_honeypot": data.get("is_honeypot"),
        })


def upsert_migration(data: dict):
    """Registra ou atualiza token que completou a migração para Raydium."""
    with _cursor() as cur:
        cur.execute("""
            INSERT INTO migrations (
                mint, token_nome, ts_detected, bc_progress_at_detection,
                ts_migration, mc_migration,
                smart_wallets, renowned_wallets, sniper_wallets,
                rug_ratio, top10_holder_rate, holders_count,
                volume_1m, volume_5m, volume_15m,
                is_wash_trading, liq_migration, is_honeypot,
                capture_delay_sec
            ) VALUES (
                %(mint)s, %(token_nome)s, %(ts_detected)s, %(bc_progress_at_detection)s,
                %(ts_migration)s, %(mc_migration)s,
                %(smart_wallets)s, %(renowned_wallets)s, %(sniper_wallets)s,
                %(rug_ratio)s, %(top10_holder_rate)s, %(holders_count)s,
                %(volume_1m)s, %(volume_5m)s, %(volume_15m)s,
                %(is_wash_trading)s, %(liq_migration)s, %(is_honeypot)s,
                %(capture_delay_sec)s
            )
            ON CONFLICT (mint) DO UPDATE SET
                ts_migration      = EXCLUDED.ts_migration,
                mc_migration      = EXCLUDED.mc_migration,
                capture_delay_sec = EXCLUDED.capture_delay_sec,
                smart_wallets     = COALESCE(migrations.smart_wallets, EXCLUDED.smart_wallets),
                renowned_wallets  = COALESCE(migrations.renowned_wallets, EXCLUDED.renowned_wallets),
                sniper_wallets    = COALESCE(migrations.sniper_wallets, EXCLUDED.sniper_wallets),
                rug_ratio         = COALESCE(migrations.rug_ratio, EXCLUDED.rug_ratio),
                top10_holder_rate = COALESCE(migrations.top10_holder_rate, EXCLUDED.top10_holder_rate),
                holders_count     = COALESCE(migrations.holders_count, EXCLUDED.holders_count),
                volume_1m         = COALESCE(migrations.volume_1m, EXCLUDED.volume_1m),
                volume_5m         = COALESCE(migrations.volume_5m, EXCLUDED.volume_5m),
                volume_15m        = COALESCE(migrations.volume_15m, EXCLUDED.volume_15m),
                is_wash_trading   = COALESCE(migrations.is_wash_trading, EXCLUDED.is_wash_trading),
                liq_migration     = COALESCE(migrations.liq_migration, EXCLUDED.liq_migration),
                is_honeypot       = COALESCE(migrations.is_honeypot, EXCLUDED.is_honeypot)
        """, {
            "mint": data.get("mint"),
            "token_nome": data.get("token_nome"),
            "ts_detected": data.get("ts_detected"),
            "bc_progress_at_detection": data.get("bc_progress_at_detection"),
            "ts_migration": data.get("ts_migration"),
            "mc_migration": data.get("mc_migration"),
            "smart_wallets": data.get("smart_wallets"),
            "renowned_wallets": data.get("renowned_wallets"),
            "sniper_wallets": data.get("sniper_wallets"),
            "rug_ratio": data.get("rug_ratio"),
            "top10_holder_rate": data.get("top10_holder_rate"),
            "holders_count": data.get("holders_count"),
            "volume_1m": data.get("volume_1m"),
            "volume_5m": data.get("volume_5m"),
            "volume_15m": data.get("volume_15m"),
            "is_wash_trading": data.get("is_wash_trading"),
            "liq_migration": data.get("liq_migration"),
            "is_honeypot": data.get("is_honeypot"),
            "capture_delay_sec": data.get("capture_delay_sec"),
        })


def update_bottom(mint: str, mc: float, time_to_bottom: float):
    """Atualiza mc_bottom apenas se o novo MC for menor que o atual registrado."""
    with _cursor() as cur:
        cur.execute("""
            UPDATE migrations
            SET
                mc_bottom      = %(mc)s,
                time_to_bottom = %(ttb)s,
                pct_drop       = CASE WHEN mc_migration > 0
                                     THEN (mc_migration - %(mc)s) / mc_migration
                                     ELSE NULL END
            WHERE mint = %(mint)s
              AND (mc_bottom IS NULL OR mc_bottom > %(mc)s)
        """, {"mint": mint, "mc": mc, "ttb": time_to_bottom})


def update_checkpoint(mint: str, label: str, mc: float, var: Optional[float]):
    """Atualiza MC e variação % para checkpoints de 1h, 4h ou 24h."""
    cols = {
        "1h":  ("mc_1h",  "var_1h"),
        "4h":  ("mc_4h",  "var_4h"),
        "24h": ("mc_24h", "var_24h"),
    }
    if label not in cols:
        return
    col_mc, col_var = cols[label]
    with _cursor() as cur:
        cur.execute(f"""
            UPDATE migrations
            SET {col_mc} = %(mc)s, {col_var} = %(var)s
            WHERE mint = %(mint)s
        """, {"mint": mint, "mc": mc, "var": var})

        if label == "1h":
            cur.execute("""
                UPDATE migrations
                SET recovery = (
                    mc_1h IS NOT NULL
                    AND mc_bottom IS NOT NULL
                    AND mc_1h > mc_bottom * 1.3
                )
                WHERE mint = %(mint)s
            """, {"mint": mint})


def get_all_migrations(limit: int = 200) -> list:
    with _cursor() as cur:
        cur.execute("""
            SELECT * FROM migrations
            ORDER BY COALESCE(ts_migration, ts_detected) DESC NULLS LAST
            LIMIT %(limit)s
        """, {"limit": limit})
        return [dict(row) for row in cur.fetchall()]
