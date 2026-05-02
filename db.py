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
                mc_migration_observed     FLOAT,
                mc_migration_bc_ref       FLOAT,
                mc_migration_source       TEXT,
                mc_peak                   FLOAT,
                time_to_peak              FLOAT,
                pct_gain                  FLOAT,
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
                token_created_at          TIMESTAMPTZ,
                time_to_migrate_sec       INT,
                -- Fase 1: campos de qualidade (chegam no trenches, zero custo de API)
                creator_token_status      TEXT,
                creator_balance_rate      FLOAT,
                dev_team_hold_rate        FLOAT,
                suspected_insider_rate    FLOAT,
                top70_sniper_hold_rate    FLOAT,
                bluechip_owner_pct        FLOAT,
                bot_degen_rate            FLOAT,
                entrapment_ratio          FLOAT,
                lock_percent              FLOAT,
                burn_status               TEXT,
                cto_flag                  BOOL,
                has_social                BOOL,
                x_follower_count          INT,
                created_at                TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        for col in [
            "ADD COLUMN IF NOT EXISTS capture_delay_sec       INT",
            "ADD COLUMN IF NOT EXISTS mc_peak                 FLOAT",
            "ADD COLUMN IF NOT EXISTS time_to_peak            FLOAT",
            "ADD COLUMN IF NOT EXISTS pct_gain                FLOAT",
            "ADD COLUMN IF NOT EXISTS token_created_at        TIMESTAMPTZ",
            "ADD COLUMN IF NOT EXISTS time_to_migrate_sec     INT",
            "ADD COLUMN IF NOT EXISTS mc_migration_observed   FLOAT",
            "ADD COLUMN IF NOT EXISTS mc_migration_bc_ref     FLOAT",
            "ADD COLUMN IF NOT EXISTS mc_migration_source     TEXT",
            # Fase 1 — campos de qualidade do trenches
            "ADD COLUMN IF NOT EXISTS creator_token_status    TEXT",
            "ADD COLUMN IF NOT EXISTS creator_balance_rate    FLOAT",
            "ADD COLUMN IF NOT EXISTS dev_team_hold_rate      FLOAT",
            "ADD COLUMN IF NOT EXISTS suspected_insider_rate  FLOAT",
            "ADD COLUMN IF NOT EXISTS top70_sniper_hold_rate  FLOAT",
            "ADD COLUMN IF NOT EXISTS bluechip_owner_pct      FLOAT",
            "ADD COLUMN IF NOT EXISTS bot_degen_rate          FLOAT",
            "ADD COLUMN IF NOT EXISTS entrapment_ratio        FLOAT",
            "ADD COLUMN IF NOT EXISTS lock_percent            FLOAT",
            "ADD COLUMN IF NOT EXISTS burn_status             TEXT",
            "ADD COLUMN IF NOT EXISTS cto_flag               BOOL",
            "ADD COLUMN IF NOT EXISTS has_social              BOOL",
            "ADD COLUMN IF NOT EXISTS x_follower_count        INT",
        ]:
            cur.execute(f"ALTER TABLE migrations {col}")
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
                is_wash_trading, liq_migration, is_honeypot,
                token_created_at, time_to_migrate_sec,
                creator_token_status, creator_balance_rate, dev_team_hold_rate,
                suspected_insider_rate, top70_sniper_hold_rate, bluechip_owner_pct,
                bot_degen_rate, entrapment_ratio, lock_percent, burn_status,
                cto_flag, has_social, x_follower_count
            ) VALUES (
                %(mint)s, %(token_nome)s, %(ts_detected)s, %(bc_progress_at_detection)s,
                %(smart_wallets)s, %(renowned_wallets)s, %(sniper_wallets)s,
                %(rug_ratio)s, %(top10_holder_rate)s, %(holders_count)s,
                %(volume_1m)s, %(volume_5m)s, %(volume_15m)s,
                %(is_wash_trading)s, %(liq_migration)s, %(is_honeypot)s,
                %(token_created_at)s, %(time_to_migrate_sec)s,
                %(creator_token_status)s, %(creator_balance_rate)s, %(dev_team_hold_rate)s,
                %(suspected_insider_rate)s, %(top70_sniper_hold_rate)s, %(bluechip_owner_pct)s,
                %(bot_degen_rate)s, %(entrapment_ratio)s, %(lock_percent)s, %(burn_status)s,
                %(cto_flag)s, %(has_social)s, %(x_follower_count)s
            )
            ON CONFLICT (mint) DO NOTHING
        """, {
            "mint":                    data.get("mint"),
            "token_nome":              data.get("token_nome"),
            "ts_detected":             data.get("ts_detected"),
            "bc_progress_at_detection":data.get("bc_progress_at_detection"),
            "smart_wallets":           data.get("smart_wallets"),
            "renowned_wallets":        data.get("renowned_wallets"),
            "sniper_wallets":          data.get("sniper_wallets"),
            "rug_ratio":               data.get("rug_ratio"),
            "top10_holder_rate":       data.get("top10_holder_rate"),
            "holders_count":           data.get("holders_count"),
            "volume_1m":               data.get("volume_1m"),
            "volume_5m":               data.get("volume_5m"),
            "volume_15m":              data.get("volume_15m"),
            "is_wash_trading":         data.get("is_wash_trading"),
            "liq_migration":           data.get("liq_migration"),
            "is_honeypot":             data.get("is_honeypot"),
            "token_created_at":        data.get("token_created_at"),
            "time_to_migrate_sec":     data.get("time_to_migrate_sec"),
            "creator_token_status":    data.get("creator_token_status"),
            "creator_balance_rate":    data.get("creator_balance_rate"),
            "dev_team_hold_rate":      data.get("dev_team_hold_rate"),
            "suspected_insider_rate":  data.get("suspected_insider_rate"),
            "top70_sniper_hold_rate":  data.get("top70_sniper_hold_rate"),
            "bluechip_owner_pct":      data.get("bluechip_owner_pct"),
            "bot_degen_rate":          data.get("bot_degen_rate"),
            "entrapment_ratio":        data.get("entrapment_ratio"),
            "lock_percent":            data.get("lock_percent"),
            "burn_status":             data.get("burn_status"),
            "cto_flag":                data.get("cto_flag"),
            "has_social":              data.get("has_social"),
            "x_follower_count":        data.get("x_follower_count"),
        })


def upsert_migration(data: dict):
    """Registra ou atualiza token que completou a migração para Raydium."""
    with _cursor() as cur:
        cur.execute("""
            INSERT INTO migrations (
                mint, token_nome, ts_detected, bc_progress_at_detection,
                ts_migration, mc_migration,
                mc_migration_observed, mc_migration_bc_ref, mc_migration_source,
                smart_wallets, renowned_wallets, sniper_wallets,
                rug_ratio, top10_holder_rate, holders_count,
                volume_1m, volume_5m, volume_15m,
                is_wash_trading, liq_migration, is_honeypot,
                capture_delay_sec, token_created_at, time_to_migrate_sec,
                creator_token_status, creator_balance_rate, dev_team_hold_rate,
                suspected_insider_rate, top70_sniper_hold_rate, bluechip_owner_pct,
                bot_degen_rate, entrapment_ratio, lock_percent, burn_status,
                cto_flag, has_social, x_follower_count
            ) VALUES (
                %(mint)s, %(token_nome)s, %(ts_detected)s, %(bc_progress_at_detection)s,
                %(ts_migration)s, %(mc_migration)s,
                %(mc_migration_observed)s, %(mc_migration_bc_ref)s, %(mc_migration_source)s,
                %(smart_wallets)s, %(renowned_wallets)s, %(sniper_wallets)s,
                %(rug_ratio)s, %(top10_holder_rate)s, %(holders_count)s,
                %(volume_1m)s, %(volume_5m)s, %(volume_15m)s,
                %(is_wash_trading)s, %(liq_migration)s, %(is_honeypot)s,
                %(capture_delay_sec)s, %(token_created_at)s, %(time_to_migrate_sec)s,
                %(creator_token_status)s, %(creator_balance_rate)s, %(dev_team_hold_rate)s,
                %(suspected_insider_rate)s, %(top70_sniper_hold_rate)s, %(bluechip_owner_pct)s,
                %(bot_degen_rate)s, %(entrapment_ratio)s, %(lock_percent)s, %(burn_status)s,
                %(cto_flag)s, %(has_social)s, %(x_follower_count)s
            )
            ON CONFLICT (mint) DO UPDATE SET
                ts_migration          = EXCLUDED.ts_migration,
                mc_migration          = EXCLUDED.mc_migration,
                mc_migration_observed = EXCLUDED.mc_migration_observed,
                mc_migration_bc_ref   = EXCLUDED.mc_migration_bc_ref,
                mc_migration_source   = EXCLUDED.mc_migration_source,
                capture_delay_sec     = EXCLUDED.capture_delay_sec,
                token_created_at      = COALESCE(migrations.token_created_at, EXCLUDED.token_created_at),
                time_to_migrate_sec   = COALESCE(migrations.time_to_migrate_sec, EXCLUDED.time_to_migrate_sec),
                smart_wallets         = COALESCE(migrations.smart_wallets, EXCLUDED.smart_wallets),
                renowned_wallets      = COALESCE(migrations.renowned_wallets, EXCLUDED.renowned_wallets),
                sniper_wallets        = COALESCE(migrations.sniper_wallets, EXCLUDED.sniper_wallets),
                rug_ratio             = COALESCE(migrations.rug_ratio, EXCLUDED.rug_ratio),
                top10_holder_rate     = COALESCE(migrations.top10_holder_rate, EXCLUDED.top10_holder_rate),
                holders_count         = COALESCE(migrations.holders_count, EXCLUDED.holders_count),
                volume_1m             = COALESCE(migrations.volume_1m, EXCLUDED.volume_1m),
                volume_5m             = COALESCE(migrations.volume_5m, EXCLUDED.volume_5m),
                volume_15m            = COALESCE(migrations.volume_15m, EXCLUDED.volume_15m),
                is_wash_trading       = COALESCE(migrations.is_wash_trading, EXCLUDED.is_wash_trading),
                liq_migration         = COALESCE(migrations.liq_migration, EXCLUDED.liq_migration),
                is_honeypot           = COALESCE(migrations.is_honeypot, EXCLUDED.is_honeypot),
                creator_token_status  = COALESCE(migrations.creator_token_status, EXCLUDED.creator_token_status),
                creator_balance_rate  = COALESCE(migrations.creator_balance_rate, EXCLUDED.creator_balance_rate),
                dev_team_hold_rate    = COALESCE(migrations.dev_team_hold_rate, EXCLUDED.dev_team_hold_rate),
                suspected_insider_rate= COALESCE(migrations.suspected_insider_rate, EXCLUDED.suspected_insider_rate),
                top70_sniper_hold_rate= COALESCE(migrations.top70_sniper_hold_rate, EXCLUDED.top70_sniper_hold_rate),
                bluechip_owner_pct    = COALESCE(migrations.bluechip_owner_pct, EXCLUDED.bluechip_owner_pct),
                bot_degen_rate        = COALESCE(migrations.bot_degen_rate, EXCLUDED.bot_degen_rate),
                entrapment_ratio      = COALESCE(migrations.entrapment_ratio, EXCLUDED.entrapment_ratio),
                lock_percent          = COALESCE(migrations.lock_percent, EXCLUDED.lock_percent),
                burn_status           = COALESCE(migrations.burn_status, EXCLUDED.burn_status),
                cto_flag              = COALESCE(migrations.cto_flag, EXCLUDED.cto_flag),
                has_social            = COALESCE(migrations.has_social, EXCLUDED.has_social),
                x_follower_count      = COALESCE(migrations.x_follower_count, EXCLUDED.x_follower_count)
        """, {
            "mint": data.get("mint"),
            "token_nome": data.get("token_nome"),
            "ts_detected": data.get("ts_detected"),
            "bc_progress_at_detection": data.get("bc_progress_at_detection"),
            "ts_migration": data.get("ts_migration"),
            "mc_migration": data.get("mc_migration"),
            "mc_migration_observed": data.get("mc_migration_observed"),
            "mc_migration_bc_ref": data.get("mc_migration_bc_ref"),
            "mc_migration_source": data.get("mc_migration_source"),
            "smart_wallets": data.get("smart_wallets"),
            "renowned_wallets": data.get("renowned_wallets"),
            "sniper_wallets": data.get("sniper_wallets"),
            "rug_ratio": data.get("rug_ratio"),
            "top10_holder_rate": data.get("top10_holder_rate"),
            "holders_count": data.get("holders_count"),
            "volume_1m": data.get("volume_1m"),
            "volume_5m": data.get("volume_5m"),
            "volume_15m": data.get("volume_15m"),
            "is_wash_trading":        data.get("is_wash_trading"),
            "liq_migration":          data.get("liq_migration"),
            "is_honeypot":            data.get("is_honeypot"),
            "capture_delay_sec":      data.get("capture_delay_sec"),
            "token_created_at":       data.get("token_created_at"),
            "time_to_migrate_sec":    data.get("time_to_migrate_sec"),
            "creator_token_status":   data.get("creator_token_status"),
            "creator_balance_rate":   data.get("creator_balance_rate"),
            "dev_team_hold_rate":     data.get("dev_team_hold_rate"),
            "suspected_insider_rate": data.get("suspected_insider_rate"),
            "top70_sniper_hold_rate": data.get("top70_sniper_hold_rate"),
            "bluechip_owner_pct":     data.get("bluechip_owner_pct"),
            "bot_degen_rate":         data.get("bot_degen_rate"),
            "entrapment_ratio":       data.get("entrapment_ratio"),
            "lock_percent":           data.get("lock_percent"),
            "burn_status":            data.get("burn_status"),
            "cto_flag":               data.get("cto_flag"),
            "has_social":             data.get("has_social"),
            "x_follower_count":       data.get("x_follower_count"),
        })


def update_peak(mint: str, mc: float, time_to_peak: float):
    """Atualiza mc_peak apenas se o novo MC for maior que o atual registrado."""
    with _cursor() as cur:
        cur.execute("""
            UPDATE migrations
            SET
                mc_peak      = %(mc)s,
                time_to_peak = %(ttp)s,
                pct_gain     = CASE WHEN mc_migration > 0
                                   THEN (%(mc)s - mc_migration) / mc_migration
                                   ELSE NULL END
            WHERE mint = %(mint)s
              AND (mc_peak IS NULL OR mc_peak < %(mc)s)
        """, {"mint": mint, "mc": mc, "ttp": time_to_peak})


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


def update_lock_percent(mint: str, lock_percent: float):
    """Atualiza lock_percent (locked_ratio do token/info) se ainda não preenchido."""
    with _cursor() as cur:
        cur.execute("""
            UPDATE migrations SET lock_percent = %(lp)s
            WHERE mint = %(mint)s AND lock_percent IS NULL
        """, {"mint": mint, "lp": lock_percent})


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


def get_tokens_for_backfill() -> list:
    """Retorna tokens que precisam de re-monitoramento ou backfill de checkpoints após restart."""
    with _cursor() as cur:
        cur.execute("""
            SELECT mint, token_nome, mc_migration, ts_migration,
                   mc_1h, mc_4h, mc_24h
            FROM migrations
            WHERE ts_migration IS NOT NULL
              AND ts_migration > NOW() - INTERVAL '26 hours'
              AND (mc_1h IS NULL OR mc_4h IS NULL OR mc_24h IS NULL)
            ORDER BY ts_migration ASC
        """)
        return [dict(row) for row in cur.fetchall()]


def get_all_migrations(limit: int = 2000) -> list:
    with _cursor() as cur:
        cur.execute("""
            SELECT * FROM migrations
            ORDER BY COALESCE(ts_migration, ts_detected) DESC NULLS LAST
            LIMIT %(limit)s
        """, {"limit": limit})
        return [dict(row) for row in cur.fetchall()]
