import os
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

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
                bundler_trader_amount_rate FLOAT,
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
                -- Campos antes descartados do trenches
                exchange                  TEXT,
                rat_trader_rate           FLOAT,
                total_fee_sol             FLOAT,
                bc_progress_migration     FLOAT,
                complete_timestamp        BIGINT,
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
            # Campos antes descartados do trenches
            "ADD COLUMN IF NOT EXISTS exchange                TEXT",
            "ADD COLUMN IF NOT EXISTS rat_trader_rate         FLOAT",
            "ADD COLUMN IF NOT EXISTS total_fee_sol           FLOAT",
            "ADD COLUMN IF NOT EXISTS bc_progress_migration   FLOAT",
            "ADD COLUMN IF NOT EXISTS complete_timestamp           BIGINT",
            "ADD COLUMN IF NOT EXISTS bundler_trader_amount_rate  FLOAT",
        ]:
            cur.execute(f"ALTER TABLE migrations {col}")

        # token_ticks — colunas OHLC e source (adicionadas para kline ticks)
        for col in [
            "ADD COLUMN IF NOT EXISTS mc_open   FLOAT",
            "ADD COLUMN IF NOT EXISTS mc_high   FLOAT",
            "ADD COLUMN IF NOT EXISTS mc_low    FLOAT",
            "ADD COLUMN IF NOT EXISTS source    TEXT NOT NULL DEFAULT 'trenches'",
        ]:
            cur.execute(f"ALTER TABLE token_ticks {col}")

        # Série temporal — ticks a cada 5s durante os 60min de monitoramento
        cur.execute("""
            CREATE TABLE IF NOT EXISTS token_ticks (
                id             BIGSERIAL PRIMARY KEY,
                mint           TEXT        NOT NULL,
                tick_ts        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                elapsed_sec    INT         NOT NULL,
                mc             FLOAT,
                mc_open        FLOAT,
                mc_high        FLOAT,
                mc_low         FLOAT,
                source         TEXT        NOT NULL DEFAULT 'trenches',
                smart_count    INT,
                renowned_count INT,
                volume_1m      FLOAT,
                volume_5m      FLOAT,
                volume_15m     FLOAT,
                holders_count  INT,
                liquidity      FLOAT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_token_ticks_mint    ON token_ticks (mint)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_token_ticks_mint_ts ON token_ticks (mint, tick_ts)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_token_ticks_elapsed ON token_ticks (mint, elapsed_sec)")

        # Sinais type 12 (SmartDegenBuy) — Fase 2
        cur.execute("""
            CREATE TABLE IF NOT EXISTS token_signals (
                id               BIGSERIAL PRIMARY KEY,
                mint             TEXT      NOT NULL,
                trigger_at       BIGINT    NOT NULL,
                trigger_mc       FLOAT,
                first_trigger_mc FLOAT,
                signal_times     INT,
                wallets_json     TEXT,
                ts_recorded      TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (mint, trigger_at)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_token_signals_mint ON token_signals (mint)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_token_signals_trigger ON token_signals (mint, trigger_at)")

        # Bounce signals — detector de bounce em tempo real
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bounce_signals (
                id                   BIGSERIAL PRIMARY KEY,
                mint                 TEXT      NOT NULL,
                symbol               TEXT,
                exchange             TEXT,
                ts_signal            TIMESTAMPTZ DEFAULT NOW(),
                elapsed_sec          INT,
                mc_migration         FLOAT,
                mc_min               FLOAT,
                mc_at_signal         FLOAT,
                drop_from_mig        FLOAT,
                bounce_pct           FLOAT,
                -- H22: absorção pós-fundo (Helius pAMM events, janela 60s)
                absorption_score     FLOAT,
                absorption_grade     TEXT,
                absorption_buy_vol   FLOAT,
                absorption_sell_vol  FLOAT,
                absorption_buy_cnt   INT,
                absorption_sell_cnt  INT,
                absorption_lrg_buy   FLOAT,
                absorption_events    INT,
                -- H23: net SOL flow = buy_vol - sell_vol (capital real entrando vs saindo)
                net_sol_flow         FLOAT,
                UNIQUE (mint)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bounce_signals_ts ON bounce_signals (ts_signal)")
        # Migração: adiciona colunas se a tabela já existir
        for col in [
            "ADD COLUMN IF NOT EXISTS exchange           TEXT",
            "ADD COLUMN IF NOT EXISTS absorption_score    FLOAT",
            "ADD COLUMN IF NOT EXISTS absorption_grade    TEXT",
            "ADD COLUMN IF NOT EXISTS absorption_buy_vol  FLOAT",
            "ADD COLUMN IF NOT EXISTS absorption_sell_vol FLOAT",
            "ADD COLUMN IF NOT EXISTS absorption_buy_cnt  INT",
            "ADD COLUMN IF NOT EXISTS absorption_sell_cnt INT",
            "ADD COLUMN IF NOT EXISTS absorption_lrg_buy  FLOAT",
            "ADD COLUMN IF NOT EXISTS absorption_events   INT",
            "ADD COLUMN IF NOT EXISTS net_sol_flow        FLOAT",
            # Ajuste 2: snapshots pós-bounce +30s e +60s
            "ADD COLUMN IF NOT EXISTS p30s_buy_vol   FLOAT",
            "ADD COLUMN IF NOT EXISTS p30s_sell_vol  FLOAT",
            "ADD COLUMN IF NOT EXISTS p30s_events    INT",
            "ADD COLUMN IF NOT EXISTS p60s_buy_vol   FLOAT",
            "ADD COLUMN IF NOT EXISTS p60s_sell_vol  FLOAT",
            "ADD COLUMN IF NOT EXISTS p60s_events    INT",
        ]:
            cur.execute(f"ALTER TABLE bounce_signals {col}")

        # Backfill H23: preenche net_sol_flow para rows que já têm buy/sell vol mas ainda NULL
        cur.execute("""
            UPDATE bounce_signals
            SET net_sol_flow = absorption_buy_vol - absorption_sell_vol
            WHERE net_sol_flow IS NULL
              AND absorption_events > 0
              AND absorption_buy_vol  IS NOT NULL
              AND absorption_sell_vol IS NOT NULL
        """)

        # Tabela de trades — Fase 6-lite (calibração $1/trade)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id                  BIGSERIAL    PRIMARY KEY,
                mint                TEXT         NOT NULL,
                symbol              TEXT,
                ts_signal           TIMESTAMPTZ,
                ts_order_sent       TIMESTAMPTZ,
                ts_order_confirmed  TIMESTAMPTZ,
                order_id            TEXT,
                strategy_id         TEXT,
                -- Entry
                mc_bottom           FLOAT,
                mc_migration        FLOAT,
                entry_expected      FLOAT,
                entry_price_sol     FLOAT,
                entry_price_usd     FLOAT,
                amount_sol          FLOAT,
                amount_usd          FLOAT,
                tokens_received     FLOAT,
                -- Friction
                slippage_pct        FLOAT,
                fee_sol             FLOAT,
                latency_sec         FLOAT,
                -- Exit (preenchido pelo monitor de estratégia)
                exit_type           TEXT,
                exit_price_usd      FLOAT,
                exit_ts             TIMESTAMPTZ,
                pnl_pct_real        FLOAT,
                pnl_pct_expected    FLOAT,
                -- Config
                tp_pct              FLOAT        DEFAULT 0.75,
                sl_pct              FLOAT        DEFAULT 0.15,
                -- Audit: estado do bounce no momento do sinal
                mc_at_signal                  FLOAT,
                bounce_pct_signal             FLOAT,
                drop_from_mig_signal          FLOAT,
                signal_elapsed_sec            INT,
                -- Audit: config ativa no momento do trade
                config_bounce_min_pct         FLOAT,
                config_bounce_max_pct         FLOAT,
                config_bounce_drop_min        FLOAT,
                config_trade_sl_pct           FLOAT,
                config_trailing_activation_pct FLOAT,
                config_trailing_drawdown_pct   FLOAT,
                config_slippage_bps           INT,
                git_sha                       TEXT,
                -- Estado
                status              TEXT         DEFAULT 'pending',
                error_msg           TEXT,
                created_at          TIMESTAMPTZ  DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_mint   ON trades (mint)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades (status)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_ts     ON trades (created_at DESC)")
        # Colunas adicionadas progressivamente
        for col in [
            "ADD COLUMN IF NOT EXISTS fill_source TEXT",
            "ADD COLUMN IF NOT EXISTS mc_at_signal                   FLOAT",
            "ADD COLUMN IF NOT EXISTS bounce_pct_signal              FLOAT",
            "ADD COLUMN IF NOT EXISTS drop_from_mig_signal           FLOAT",
            "ADD COLUMN IF NOT EXISTS signal_elapsed_sec             INT",
            "ADD COLUMN IF NOT EXISTS config_bounce_min_pct          FLOAT",
            "ADD COLUMN IF NOT EXISTS config_bounce_max_pct          FLOAT",
            "ADD COLUMN IF NOT EXISTS config_bounce_drop_min         FLOAT",
            "ADD COLUMN IF NOT EXISTS config_trade_sl_pct            FLOAT",
            "ADD COLUMN IF NOT EXISTS config_trailing_activation_pct FLOAT",
            "ADD COLUMN IF NOT EXISTS config_trailing_drawdown_pct   FLOAT",
            "ADD COLUMN IF NOT EXISTS config_slippage_bps            INT",
            "ADD COLUMN IF NOT EXISTS git_sha                        TEXT",
        ]:
            try:
                cur.execute(f"ALTER TABLE trades {col}")
            except Exception:
                pass

        # Série temporal de absorção durante o holding — captação pura, zero decisão.
        # Cruzar com outcome (TP/SL) do trade depois que n for suficiente.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trade_absorption_ticks (
                id          BIGSERIAL    PRIMARY KEY,
                trade_id    BIGINT       NOT NULL,
                mint        TEXT         NOT NULL,
                ts          TIMESTAMPTZ  DEFAULT NOW(),
                buy_vol     FLOAT,
                sell_vol    FLOAT,
                buy_count   INT,
                sell_count  INT,
                events      INT,
                lrg_buy     FLOAT
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tat_trade ON trade_absorption_ticks (trade_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_tat_mint  ON trade_absorption_ticks (mint)")

    logger.info("DB inicializado")


def upsert_watchlist(data: dict):
    """Registra token detectado na watchlist (bc >= 80%) antes da migração."""
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
    """Registra ou atualiza token que completou a migração para Raydium/Meteora."""
    with _cursor() as cur:
        cur.execute("""
            INSERT INTO migrations (
                mint, token_nome, ts_detected, bc_progress_at_detection,
                ts_migration, mc_migration,
                mc_migration_observed, mc_migration_bc_ref, mc_migration_source,
                smart_wallets, renowned_wallets, sniper_wallets,
                rug_ratio, bundler_trader_amount_rate, top10_holder_rate, holders_count,
                volume_1m, volume_5m, volume_15m,
                is_wash_trading, liq_migration, is_honeypot,
                capture_delay_sec, token_created_at, time_to_migrate_sec,
                creator_token_status, creator_balance_rate, dev_team_hold_rate,
                suspected_insider_rate, top70_sniper_hold_rate, bluechip_owner_pct,
                bot_degen_rate, entrapment_ratio, lock_percent, burn_status,
                cto_flag, has_social, x_follower_count,
                exchange, rat_trader_rate, total_fee_sol,
                bc_progress_migration, complete_timestamp
            ) VALUES (
                %(mint)s, %(token_nome)s, %(ts_detected)s, %(bc_progress_at_detection)s,
                %(ts_migration)s, %(mc_migration)s,
                %(mc_migration_observed)s, %(mc_migration_bc_ref)s, %(mc_migration_source)s,
                %(smart_wallets)s, %(renowned_wallets)s, %(sniper_wallets)s,
                %(rug_ratio)s, %(bundler_trader_amount_rate)s, %(top10_holder_rate)s, %(holders_count)s,
                %(volume_1m)s, %(volume_5m)s, %(volume_15m)s,
                %(is_wash_trading)s, %(liq_migration)s, %(is_honeypot)s,
                %(capture_delay_sec)s, %(token_created_at)s, %(time_to_migrate_sec)s,
                %(creator_token_status)s, %(creator_balance_rate)s, %(dev_team_hold_rate)s,
                %(suspected_insider_rate)s, %(top70_sniper_hold_rate)s, %(bluechip_owner_pct)s,
                %(bot_degen_rate)s, %(entrapment_ratio)s, %(lock_percent)s, %(burn_status)s,
                %(cto_flag)s, %(has_social)s, %(x_follower_count)s,
                %(exchange)s, %(rat_trader_rate)s, %(total_fee_sol)s,
                %(bc_progress_migration)s, %(complete_timestamp)s
            )
            ON CONFLICT (mint) DO UPDATE SET
                ts_migration           = EXCLUDED.ts_migration,
                mc_migration           = EXCLUDED.mc_migration,
                mc_migration_observed  = EXCLUDED.mc_migration_observed,
                mc_migration_bc_ref    = EXCLUDED.mc_migration_bc_ref,
                mc_migration_source    = EXCLUDED.mc_migration_source,
                capture_delay_sec      = EXCLUDED.capture_delay_sec,
                exchange               = EXCLUDED.exchange,
                rat_trader_rate        = COALESCE(migrations.rat_trader_rate, EXCLUDED.rat_trader_rate),
                total_fee_sol          = EXCLUDED.total_fee_sol,
                bc_progress_migration  = EXCLUDED.bc_progress_migration,
                complete_timestamp     = COALESCE(migrations.complete_timestamp, EXCLUDED.complete_timestamp),
                token_created_at       = COALESCE(migrations.token_created_at, EXCLUDED.token_created_at),
                time_to_migrate_sec    = COALESCE(migrations.time_to_migrate_sec, EXCLUDED.time_to_migrate_sec),
                smart_wallets          = COALESCE(migrations.smart_wallets, EXCLUDED.smart_wallets),
                renowned_wallets       = COALESCE(migrations.renowned_wallets, EXCLUDED.renowned_wallets),
                sniper_wallets         = COALESCE(migrations.sniper_wallets, EXCLUDED.sniper_wallets),
                rug_ratio              = COALESCE(migrations.rug_ratio, EXCLUDED.rug_ratio),
                bundler_trader_amount_rate = COALESCE(migrations.bundler_trader_amount_rate, EXCLUDED.bundler_trader_amount_rate),
                top10_holder_rate      = COALESCE(migrations.top10_holder_rate, EXCLUDED.top10_holder_rate),
                holders_count          = COALESCE(migrations.holders_count, EXCLUDED.holders_count),
                volume_1m              = COALESCE(migrations.volume_1m, EXCLUDED.volume_1m),
                volume_5m              = COALESCE(migrations.volume_5m, EXCLUDED.volume_5m),
                volume_15m             = COALESCE(migrations.volume_15m, EXCLUDED.volume_15m),
                is_wash_trading        = COALESCE(migrations.is_wash_trading, EXCLUDED.is_wash_trading),
                liq_migration          = COALESCE(migrations.liq_migration, EXCLUDED.liq_migration),
                is_honeypot            = COALESCE(migrations.is_honeypot, EXCLUDED.is_honeypot),
                creator_token_status   = COALESCE(migrations.creator_token_status, EXCLUDED.creator_token_status),
                creator_balance_rate   = COALESCE(migrations.creator_balance_rate, EXCLUDED.creator_balance_rate),
                dev_team_hold_rate     = COALESCE(migrations.dev_team_hold_rate, EXCLUDED.dev_team_hold_rate),
                suspected_insider_rate = COALESCE(migrations.suspected_insider_rate, EXCLUDED.suspected_insider_rate),
                top70_sniper_hold_rate = COALESCE(migrations.top70_sniper_hold_rate, EXCLUDED.top70_sniper_hold_rate),
                bluechip_owner_pct     = COALESCE(migrations.bluechip_owner_pct, EXCLUDED.bluechip_owner_pct),
                bot_degen_rate         = COALESCE(migrations.bot_degen_rate, EXCLUDED.bot_degen_rate),
                entrapment_ratio       = COALESCE(migrations.entrapment_ratio, EXCLUDED.entrapment_ratio),
                lock_percent           = COALESCE(migrations.lock_percent, EXCLUDED.lock_percent),
                burn_status            = COALESCE(migrations.burn_status, EXCLUDED.burn_status),
                cto_flag               = COALESCE(migrations.cto_flag, EXCLUDED.cto_flag),
                has_social             = COALESCE(migrations.has_social, EXCLUDED.has_social),
                x_follower_count       = COALESCE(migrations.x_follower_count, EXCLUDED.x_follower_count)
        """, {
            "mint":                   data.get("mint"),
            "token_nome":             data.get("token_nome"),
            "ts_detected":            data.get("ts_detected"),
            "bc_progress_at_detection":data.get("bc_progress_at_detection"),
            "ts_migration":           data.get("ts_migration"),
            "mc_migration":           data.get("mc_migration"),
            "mc_migration_observed":  data.get("mc_migration_observed"),
            "mc_migration_bc_ref":    data.get("mc_migration_bc_ref"),
            "mc_migration_source":    data.get("mc_migration_source"),
            "smart_wallets":          data.get("smart_wallets"),
            "renowned_wallets":       data.get("renowned_wallets"),
            "sniper_wallets":         data.get("sniper_wallets"),
            "rug_ratio":              data.get("rug_ratio"),
            "bundler_trader_amount_rate": data.get("bundler_trader_amount_rate"),
            "top10_holder_rate":      data.get("top10_holder_rate"),
            "holders_count":          data.get("holders_count"),
            "volume_1m":              data.get("volume_1m"),
            "volume_5m":              data.get("volume_5m"),
            "volume_15m":             data.get("volume_15m"),
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
            "exchange":               data.get("exchange"),
            "rat_trader_rate":        data.get("rat_trader_rate"),
            "total_fee_sol":          data.get("total_fee_sol"),
            "bc_progress_migration":  data.get("bc_progress_migration"),
            "complete_timestamp":     data.get("complete_timestamp"),
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
    """Atualiza MC e variação % para checkpoint de 1h."""
    if label != "1h":
        return
    col_mc, col_var = "mc_1h", "var_1h"
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


def set_recovery_false(mint: str):
    """Marca recovery=FALSE para token cancelado (rug/morto) sem mc_1h."""
    with _cursor() as cur:
        cur.execute("""
            UPDATE migrations
            SET recovery = FALSE
            WHERE mint = %(mint)s
              AND recovery IS NULL
              AND mc_bottom IS NOT NULL
        """, {"mint": mint})


def backfill_recovery_false():
    """
    Backfill histórico: tokens com mc_bottom mas sem mc_1h há mais de 2h
    são implicitamente recovery=FALSE (morreram antes do checkpoint de 1h).
    """
    with _cursor() as cur:
        cur.execute("""
            UPDATE migrations
            SET recovery = FALSE
            WHERE recovery IS NULL
              AND mc_bottom IS NOT NULL
              AND mc_1h IS NULL
              AND ts_migration < NOW() - INTERVAL '2 hours'
        """)
        return cur.rowcount


def insert_bounce_signal(data: dict):
    """Registra um sinal de bounce. UNIQUE(mint) — um sinal por token.
    Se o mint já existe com absorption_events NULL, atualiza os campos H22.
    Isso permite que tokens que bounçaram antes do deploy H22 recebam dados
    na próxima sessão (ex: após restart).
    """
    with _cursor() as cur:
        cur.execute("""
            INSERT INTO bounce_signals
                (mint, symbol, exchange, elapsed_sec, mc_migration, mc_min,
                 mc_at_signal, drop_from_mig, bounce_pct,
                 absorption_score, absorption_grade,
                 absorption_buy_vol, absorption_sell_vol,
                 absorption_buy_cnt, absorption_sell_cnt,
                 absorption_lrg_buy, absorption_events,
                 net_sol_flow)
            VALUES
                (%(mint)s, %(symbol)s, %(exchange)s, %(elapsed_sec)s, %(mc_migration)s, %(mc_min)s,
                 %(mc_at_signal)s, %(drop_from_mig)s, %(bounce_pct)s,
                 %(absorption_score)s, %(absorption_grade)s,
                 %(absorption_buy_vol)s, %(absorption_sell_vol)s,
                 %(absorption_buy_cnt)s, %(absorption_sell_cnt)s,
                 %(absorption_lrg_buy)s, %(absorption_events)s,
                 %(net_sol_flow)s)
            ON CONFLICT (mint) DO UPDATE SET
                absorption_score    = EXCLUDED.absorption_score,
                absorption_grade    = EXCLUDED.absorption_grade,
                absorption_buy_vol  = EXCLUDED.absorption_buy_vol,
                absorption_sell_vol = EXCLUDED.absorption_sell_vol,
                absorption_buy_cnt  = EXCLUDED.absorption_buy_cnt,
                absorption_sell_cnt = EXCLUDED.absorption_sell_cnt,
                absorption_lrg_buy  = EXCLUDED.absorption_lrg_buy,
                absorption_events   = EXCLUDED.absorption_events,
                net_sol_flow        = EXCLUDED.net_sol_flow
            WHERE bounce_signals.absorption_events IS NULL
        """, {
            **data,
            "exchange":            data.get("exchange"),
            "absorption_score":    data.get("absorption_score"),
            "absorption_grade":    data.get("absorption_grade"),
            "absorption_buy_vol":  data.get("absorption_buy_vol"),
            "absorption_sell_vol": data.get("absorption_sell_vol"),
            "absorption_buy_cnt":  data.get("absorption_buy_cnt"),
            "absorption_sell_cnt": data.get("absorption_sell_cnt"),
            "absorption_lrg_buy":  data.get("absorption_lrg_buy"),
            "absorption_events":   data.get("absorption_events"),
            "net_sol_flow":        data.get("net_sol_flow"),
        })


def insert_trade_absorption_tick(data: dict):
    """Insere 1 tick de fluxo buy/sell durante um trade aberto (janela 30s rolante).
    Chamado a cada MONITOR_POLL_SEC pelo TradeMonitor enquanto status='open'/'trailing_active'.
    """
    with _cursor() as cur:
        cur.execute("""
            INSERT INTO trade_absorption_ticks
                (trade_id, mint, buy_vol, sell_vol, buy_count, sell_count, events, lrg_buy)
            VALUES
                (%(trade_id)s, %(mint)s, %(buy_vol)s, %(sell_vol)s,
                 %(buy_count)s, %(sell_count)s, %(events)s, %(lrg_buy)s)
        """, data)


def update_bounce_snapshots(mint: str, window_label: str, buy_vol: float, sell_vol: float, events: int):
    """Atualiza snapshots pós-bounce (+30s ou +60s) num bounce_signal existente.
    window_label: '30s' ou '60s' — determina quais colunas atualizar.
    """
    if window_label == "30s":
        cols = "p30s_buy_vol = %(bv)s, p30s_sell_vol = %(sv)s, p30s_events = %(ev)s"
    elif window_label == "60s":
        cols = "p60s_buy_vol = %(bv)s, p60s_sell_vol = %(sv)s, p60s_events = %(ev)s"
    else:
        return
    with _cursor() as cur:
        cur.execute(
            f"UPDATE bounce_signals SET {cols} WHERE mint = %(mint)s",
            {"bv": buy_vol, "sv": sell_vol, "ev": events, "mint": mint},
        )


def insert_ticks_batch(ticks: list):
    """Insere ticks de trenches (source='trenches') — mc = preco no momento do poll."""
    if not ticks:
        return
    with _cursor() as cur:
        cur.executemany("""
            INSERT INTO token_ticks
                (mint, elapsed_sec, mc, source,
                 smart_count, renowned_count,
                 volume_1m, volume_5m, volume_15m, holders_count, liquidity)
            VALUES
                (%(mint)s, %(elapsed_sec)s, %(mc)s, 'trenches',
                 %(smart_count)s, %(renowned_count)s,
                 %(volume_1m)s, %(volume_5m)s, %(volume_15m)s,
                 %(holders_count)s, %(liquidity)s)
            ON CONFLICT DO NOTHING
        """, ticks)


def insert_kline_ticks_batch(ticks: list):
    """
    Insere ticks de kline (source='kline') com OHLC completo.
    Cada tick = uma vela 1m. mc = close, mc_open/mc_high/mc_low da vela.
    ON CONFLICT DO NOTHING para nao duplicar velas ja inseridas.
    """
    if not ticks:
        return
    with _cursor() as cur:
        cur.executemany("""
            INSERT INTO token_ticks
                (mint, elapsed_sec, mc, mc_open, mc_high, mc_low,
                 source, volume_1m)
            VALUES
                (%(mint)s, %(elapsed_sec)s, %(mc)s, %(mc_open)s, %(mc_high)s, %(mc_low)s,
                 'kline', %(volume_1m)s)
            ON CONFLICT DO NOTHING
        """, ticks)


def backfill_tick_volumes(mint: str, candles: list, ts_mig_unix: int):
    """
    Back-filla volume_1m nos ticks existentes usando candles do kline 1m.
    Cada candle cobre 60s; atualiza ticks no intervalo elapsed ±30s do candle.
    Só sobrescreve se volume_1m ainda for NULL (não destrói dados existentes).
    """
    if not candles:
        return
    rows = []
    for c in candles:
        candle_ts   = int(c.get("time", 0)) / 1000
        elapsed_sec = int(candle_ts - ts_mig_unix)
        if elapsed_sec < 0:
            continue
        try:
            vol = float(c.get("volume") or 0)
        except Exception:
            continue
        if vol > 0:
            rows.append({"mint": mint, "elapsed": elapsed_sec, "vol": vol})

    if not rows:
        return
    with _cursor() as cur:
        for r in rows:
            cur.execute("""
                UPDATE token_ticks
                SET volume_1m = %(vol)s
                WHERE mint    = %(mint)s
                  AND elapsed_sec BETWEEN %(lo)s AND %(hi)s
                  AND volume_1m IS NULL
            """, {"mint": r["mint"], "vol": r["vol"],
                  "lo": r["elapsed"] - 30, "hi": r["elapsed"] + 30})


def get_recent_ticks(mint: str, window_sec: int = 120, max_ts=None, min_ts=None) -> list:
    """
    Retorna lista de (tick_ts, mc) de uma janela de ticks para um token.
    Ordenado por tick_ts ASC.

    max_ts: limite superior (padrão NOW). Para slope pré-fundo, passar ts_min.
    min_ts: limite inferior (padrão max_ts - window_sec). Para persistência pós-sinal.
    """
    with _cursor() as cur:
        if max_ts is not None:
            cur.execute("""
                SELECT tick_ts, mc
                FROM token_ticks
                WHERE mint    = %(mint)s
                  AND tick_ts <= %(max_ts)s
                  AND tick_ts >= %(max_ts)s - (%(window)s || ' seconds')::INTERVAL
                  AND mc IS NOT NULL
                ORDER BY tick_ts ASC
            """, {"mint": mint, "window": window_sec, "max_ts": max_ts})
        elif min_ts is not None:
            cur.execute("""
                SELECT tick_ts, mc
                FROM token_ticks
                WHERE mint    = %(mint)s
                  AND tick_ts >= %(min_ts)s
                  AND mc IS NOT NULL
                ORDER BY tick_ts ASC
            """, {"mint": mint, "min_ts": min_ts})
        else:
            cur.execute("""
                SELECT tick_ts, mc
                FROM token_ticks
                WHERE mint    = %(mint)s
                  AND tick_ts >= NOW() - (%(window)s || ' seconds')::INTERVAL
                  AND mc IS NOT NULL
                ORDER BY tick_ts ASC
            """, {"mint": mint, "window": window_sec})
        return [(row["tick_ts"], float(row["mc"])) for row in cur.fetchall()]


def get_tick_aggregates(max_elapsed_sec: int = 600) -> list:
    """
    Retorna métricas agregadas da série temporal dos primeiros N segundos por token.
    JOIN com migrations para ter recovery, mc_bottom, mc_1h.
    Usado na Seção 9 do /analise.
    """
    with _cursor() as cur:
        cur.execute("""
            SELECT
                t.mint,
                COUNT(*)                                          AS tick_count,
                AVG(t.smart_count)                                AS avg_smart,
                MAX(t.smart_count) - MIN(t.smart_count)          AS delta_smart,
                AVG(t.renowned_count)                             AS avg_renowned,
                AVG(t.volume_1m)                                  AS avg_vol_1m,
                MAX(t.volume_1m)                                  AS max_vol_1m,
                AVG(t.volume_5m)                                  AS avg_vol_5m,
                AVG(t.holders_count)                              AS avg_holders,
                MAX(t.holders_count) - MIN(t.holders_count)      AS delta_holders,
                AVG(t.liquidity)                                  AS avg_liquidity,
                m.mc_bottom,
                m.mc_1h,
                m.recovery,
                m.mc_migration,
                m.exchange
            FROM token_ticks t
            JOIN migrations m ON m.mint = t.mint
            WHERE t.elapsed_sec <= %(max_sec)s
            GROUP BY t.mint, m.mc_bottom, m.mc_1h, m.recovery, m.mc_migration, m.exchange
            HAVING COUNT(*) >= 5
        """, {"max_sec": max_elapsed_sec})
        return [dict(row) for row in cur.fetchall()]


def cleanup_old_ticks(days: int = 30):
    """Remove ticks mais antigos que N dias. Chamar uma vez por dia."""
    with _cursor() as cur:
        cur.execute("""
            DELETE FROM token_ticks
            WHERE tick_ts < NOW() - INTERVAL '%(days)s days'
        """, {"days": days})
        deleted = cur.rowcount
    logger.info("cleanup_old_ticks: %d ticks removidos (>%dd)", deleted, days)


def get_tokens_for_backfill() -> list:
    """Retorna tokens que precisam de re-monitoramento ou backfill de mc_1h após restart."""
    with _cursor() as cur:
        cur.execute("""
            SELECT mint, token_nome, mc_migration, ts_migration, mc_1h
            FROM migrations
            WHERE ts_migration IS NOT NULL
              AND ts_migration > NOW() - INTERVAL '2 hours'
              AND mc_1h IS NULL
            ORDER BY ts_migration ASC
        """)
        return [dict(row) for row in cur.fetchall()]


def insert_signal_batch(signals: list):
    """
    Insere sinais type 12 (SmartDegenBuy) em batch. Ignora duplicatas (UNIQUE mint+trigger_at).
    signals: lista de dicts com chaves mint, trigger_at, trigger_mc, first_trigger_mc,
             signal_times, wallets_json.
    """
    if not signals:
        return
    with _cursor() as cur:
        cur.executemany("""
            INSERT INTO token_signals
                (mint, trigger_at, trigger_mc, first_trigger_mc, signal_times, wallets_json)
            VALUES
                (%(mint)s, %(trigger_at)s, %(trigger_mc)s, %(first_trigger_mc)s,
                 %(signal_times)s, %(wallets_json)s)
            ON CONFLICT (mint, trigger_at) DO NOTHING
        """, signals)


def get_all_migrations(limit: int = 2000) -> list:
    with _cursor() as cur:
        cur.execute("""
            SELECT * FROM migrations
            ORDER BY COALESCE(ts_migration, ts_detected) DESC NULLS LAST
            LIMIT %(limit)s
        """, {"limit": limit})
        return [dict(row) for row in cur.fetchall()]


# ==============================================================================
# Trades — Fase 6-lite
# ==============================================================================

def insert_trade(data: dict) -> int:
    """Cria registro de trade. Retorna o id gerado."""
    with _cursor() as cur:
        cur.execute("""
            INSERT INTO trades
                (mint, symbol, ts_signal, mc_bottom, mc_migration, entry_expected,
                 amount_sol, amount_usd, tp_pct, sl_pct, status,
                 mc_at_signal, bounce_pct_signal, drop_from_mig_signal, signal_elapsed_sec,
                 config_bounce_min_pct, config_bounce_max_pct, config_bounce_drop_min,
                 config_trade_sl_pct, config_trailing_activation_pct,
                 config_trailing_drawdown_pct, config_slippage_bps, git_sha)
            VALUES
                (%(mint)s, %(symbol)s, %(ts_signal)s, %(mc_bottom)s, %(mc_migration)s,
                 %(entry_expected)s, %(amount_sol)s, %(amount_usd)s,
                 %(tp_pct)s, %(sl_pct)s, %(status)s,
                 %(mc_at_signal)s, %(bounce_pct_signal)s, %(drop_from_mig_signal)s,
                 %(signal_elapsed_sec)s,
                 %(config_bounce_min_pct)s, %(config_bounce_max_pct)s,
                 %(config_bounce_drop_min)s, %(config_trade_sl_pct)s,
                 %(config_trailing_activation_pct)s, %(config_trailing_drawdown_pct)s,
                 %(config_slippage_bps)s, %(git_sha)s)
            RETURNING id
        """, data)
        return cur.fetchone()["id"]


def update_trade(trade_id: int, data: dict):
    """Atualiza campos do trade por id."""
    if not data:
        return
    sets     = ", ".join(f"{k} = %({k})s" for k in data)
    payload  = dict(data)
    payload["_id"] = trade_id
    with _cursor() as cur:
        cur.execute(f"UPDATE trades SET {sets} WHERE id = %(_id)s", payload)


def get_trade_by_id(trade_id: int) -> Optional[dict]:
    with _cursor() as cur:
        cur.execute("SELECT * FROM trades WHERE id = %(id)s", {"id": trade_id})
        row = cur.fetchone()
        return dict(row) if row else None


def count_trades(exclude_errors: bool = True) -> int:
    """Conta trades (exclui erros por padrão para o limite de calibração)."""
    with _cursor() as cur:
        if exclude_errors:
            cur.execute("SELECT COUNT(*) AS n FROM trades WHERE status != 'error'")
        else:
            cur.execute("SELECT COUNT(*) AS n FROM trades")
        return cur.fetchone()["n"]


def get_trades(limit: int = 200) -> list:
    with _cursor() as cur:
        cur.execute("""
            SELECT * FROM trades ORDER BY created_at DESC LIMIT %(limit)s
        """, {"limit": limit})
        return [dict(row) for row in cur.fetchall()]


def get_trade_by_id(trade_id: int) -> dict | None:
    with _cursor() as cur:
        cur.execute("SELECT * FROM trades WHERE id = %s", (trade_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_open_trades() -> list:
    """Retorna trades ainda sob monitoramento — apenas 'open'.
    'trailing_active' é estado terminal: GMGN gerencia a saída, não monitoramos mais.
    Remover trailing_active daqui impede que o MONITOR_MAX_AGE_H sobrescreva o TP para 'expired'.
    """
    with _cursor() as cur:
        cur.execute("""
            SELECT * FROM trades
            WHERE status = 'open'
              AND entry_expected IS NOT NULL
            ORDER BY created_at ASC
        """)
        return [dict(row) for row in cur.fetchall()]


def get_trailing_active_trades() -> list:
    """Trades com trailing ativo ainda sem exit final — detectar quando GMGN executa o trailing."""
    with _cursor() as cur:
        cur.execute("""
            SELECT * FROM trades
            WHERE status = 'trailing_active'
              AND exit_ts > NOW() - INTERVAL '8 hours'
            ORDER BY exit_ts ASC
        """)
        return [dict(row) for row in cur.fetchall()]


def get_ticks_after(mint: str, min_ts) -> list:
    """Retorna ticks (mc, mc_high, mc_low, tick_ts) para um mint após min_ts."""
    with _cursor() as cur:
        cur.execute("""
            SELECT tick_ts,
                   mc,
                   COALESCE(mc_high, mc) AS mc_high,
                   COALESCE(mc_low,  mc) AS mc_low
            FROM token_ticks
            WHERE mint    = %(mint)s
              AND tick_ts >= %(min_ts)s
              AND mc IS NOT NULL
            ORDER BY tick_ts ASC
        """, {"mint": mint, "min_ts": min_ts})
        return [dict(row) for row in cur.fetchall()]


def get_trades_missing_fill() -> list:
    """Trades com order_id mas sem fill — independente do status.
    Usado pelo TradeMonitor para recuperar fill data em qualquer estado do trade.
    Limita a trades das últimas 24h para não sobrecarregar o GMGN com ordens antigas.
    """
    with _cursor() as cur:
        cur.execute("""
            SELECT * FROM trades
            WHERE order_id IS NOT NULL
              AND entry_price_usd IS NULL
              AND status NOT IN ('error', 'cancelled', 'pending')
              AND created_at > NOW() - INTERVAL '24 hours'
            ORDER BY created_at DESC
            LIMIT 50
        """)
        return [dict(row) for row in cur.fetchall()]


def get_trades_without_strategy() -> list:
    """Trades com order_id mas sem strategy_id — candidatos a recuperação de TP/SL."""
    with _cursor() as cur:
        cur.execute("""
            SELECT * FROM trades
            WHERE order_id IS NOT NULL
              AND strategy_id IS NULL
              AND status NOT IN ('pending', 'cancelled')
            ORDER BY created_at DESC
            LIMIT 20
        """)
        return [dict(row) for row in cur.fetchall()]
