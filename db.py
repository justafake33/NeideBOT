import os
import logging
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
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


# Runtime schema healthcheck: read-only, no DDL. Used by /debug/schema to catch
# Railway deploys where the migration one-off was not executed before web/worker.
REQUIRED_RUNTIME_SCHEMA = {
    "trades": {
        "id", "mint", "symbol", "status", "order_id",
        "helius_gate_passed", "helius_gate_reason",
        "helius_pool_quote_at_signal", "helius_absorption_events",
        "helius_net_sol_flow", "helius_rise_events", "helius_rise_net_flow",
        "helius_dump_seller_hhi", "helius_buy_concentration", "helius_h23_window",
        "data_quality",
    },
    "bounce_signals": {
        "id", "mint", "ts", "mc_at_signal", "bounce_pct",
        "pool_quote_at_signal", "absorption_events", "net_sol_flow",
        "rise_events", "rise_net_flow", "dump_seller_hhi",
        "dump_top_seller_frac", "buy_concentration", "h23_window",
        "data_quality",
    },
    "micro_absorption_snapshots": {
        "id", "mint", "ts", "bottom_age_sec", "entry_mc",
        "pool_quote", "live_5s_net", "live_5s_events",
        "live_10s_net", "live_10s_events",
        "live_30s_net", "live_30s_events",
        "live_60s_net", "live_60s_events",
        "pre_5s_buy_concentration", "pre_10s_buy_concentration",
        "rise_5s_buy_concentration", "rise_10s_buy_concentration",
        "live_5s_buy_concentration", "live_10s_buy_concentration",
        "candidate_flags",
    },
    "token_ticks": {
        "mint", "tick_ts", "mc", "mc_open", "mc_high", "mc_low", "source",
    },
    "runtime_heartbeats": {
        "runtime_name", "service_type", "outbound_ip", "hostname", "pid",
        "metadata", "ws_health", "updated_at",
    },
}


def check_runtime_schema() -> dict:
    """Read-only schema check for runtime-critical analytics/trade columns.

    This intentionally does not run DDL. Missing columns mean the Railway one-off
    migration should be run with RUN_DB_MIGRATIONS=1 python migrate.py.
    """
    result = {
        "ok": False,
        "database_url_configured": bool(DATABASE_URL),
        "tables": {},
        "missing_tables": [],
        "missing_columns": {},
        "required_schema_version": "helius_trade_gate_v1",
    }
    if not DATABASE_URL:
        result["error"] = "DATABASE_URL not configured"
        return result

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET lock_timeout = '1500ms'")
            cur.execute("SET statement_timeout = '5000ms'")
            cur.execute("""
                SELECT table_name, column_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = ANY(%s)
            """, (list(REQUIRED_RUNTIME_SCHEMA.keys()),))
            rows = cur.fetchall()
    finally:
        conn.close()

    found = {}
    for r in rows:
        found.setdefault(r["table_name"], set()).add(r["column_name"])

    for table, required_cols in REQUIRED_RUNTIME_SCHEMA.items():
        cols = found.get(table, set())
        if not cols:
            result["missing_tables"].append(table)
            result["tables"][table] = {
                "exists": False,
                "missing_columns": sorted(required_cols),
            }
            result["missing_columns"][table] = sorted(required_cols)
            continue
        missing = sorted(required_cols - cols)
        result["tables"][table] = {
            "exists": True,
            "required_columns": sorted(required_cols),
            "missing_columns": missing,
            "present_required_count": len(required_cols) - len(missing),
            "required_count": len(required_cols),
        }
        if missing:
            result["missing_columns"][table] = missing

    result["ok"] = not result["missing_tables"] and not result["missing_columns"]
    return result


def init_db():
    # Advisory lock: evita execução concorrente de DDL (ALTER TABLE / CREATE INDEX)
    # que causaria deadlock com queries analíticas segurando AccessShareLock.
    # pg_advisory_lock é de sessão — liberado automaticamente no conn.close().
    _conn = psycopg2.connect(DATABASE_URL)
    _conn.autocommit = True
    try:
        with _conn.cursor() as _acur:
            _acur.execute("SELECT pg_advisory_lock(hashtext('neidebot_init_db'))")
        logger.info("init_db: advisory lock adquirido")
    except Exception as _e:
        logger.warning("init_db: não conseguiu advisory lock: %s", _e)
        _conn.close()
        _conn = None

    try:
        _do_init_db()
    finally:
        if _conn is not None:
            try:
                with _conn.cursor() as _acur:
                    _acur.execute("SELECT pg_advisory_unlock(hashtext('neidebot_init_db'))")
            except Exception:
                pass
            _conn.close()
            logger.info("init_db: advisory lock liberado")


def _do_init_db():
    """Corpo real do init_db — chamado sob advisory lock por init_db()."""
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
            # mc_min truth — validação on-chain assíncrona pós-graduação
            "ADD COLUMN IF NOT EXISTS mc_min_true       DOUBLE PRECISION",
            "ADD COLUMN IF NOT EXISTS ts_min_true       TIMESTAMPTZ",
            "ADD COLUMN IF NOT EXISTS txid_min_true     TEXT",
            "ADD COLUMN IF NOT EXISTS source_truth      TEXT",
            "ADD COLUMN IF NOT EXISTS bottom_error_pct  DOUBLE PRECISION",
            "ADD COLUMN IF NOT EXISTS bottom_quality    TEXT",
            # Tiering de qualidade de dados
            "ADD COLUMN IF NOT EXISTS data_quality      TEXT",
            # H27 — token estava no trending pump.fun 5m no momento da migração?
            "ADD COLUMN IF NOT EXISTS trending_at_migration BOOLEAN",
            # Visibilidade vs filtros de trading — passou todos os filtros de qualidade/MC/liq?
            "ADD COLUMN IF NOT EXISTS passed_filters        BOOLEAN",
            # Dados de ranking pump.fun — hot_level (heat score) e visiting_count (page views)
            "ADD COLUMN IF NOT EXISTS hot_level             FLOAT",
            "ADD COLUMN IF NOT EXISTS visiting_count        INT",
            # Dados sociais adicionais do trenches
            "ADD COLUMN IF NOT EXISTS tg_call_count         INT",
        ]:
            cur.execute(f"ALTER TABLE migrations {col}")

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
        # token_ticks — colunas OHLC e source (migração para bancos existentes)
        for col in [
            "ADD COLUMN IF NOT EXISTS mc_open   FLOAT",
            "ADD COLUMN IF NOT EXISTS mc_high   FLOAT",
            "ADD COLUMN IF NOT EXISTS mc_low    FLOAT",
            "ADD COLUMN IF NOT EXISTS source    TEXT NOT NULL DEFAULT 'trenches'",
        ]:
            cur.execute(f"ALTER TABLE token_ticks {col}")

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
            # H23 window tag — 'bounce' = janela centrada no bounce (dado antigo, momentum)
            #                   'bottom' = janela centrada no fundo (dado novo, absorção real)
            "ADD COLUMN IF NOT EXISTS h23_window     TEXT",
            # Janela de subida: [ts_min → ts_signal] — quem comprou durante a alta do bounce?
            "ADD COLUMN IF NOT EXISTS rise_buy_vol   FLOAT",
            "ADD COLUMN IF NOT EXISTS rise_sell_vol  FLOAT",
            "ADD COLUMN IF NOT EXISTS rise_net_flow  FLOAT",
            "ADD COLUMN IF NOT EXISTS rise_events    INT",
            "ADD COLUMN IF NOT EXISTS rise_lrg_buy   FLOAT",
            # H25 — profundidade do pool no momento do sinal (SOL no pool)
            "ADD COLUMN IF NOT EXISTS pool_quote_at_signal FLOAT",
            # H24 — concentração de sellers durante o dump (HHI)
            "ADD COLUMN IF NOT EXISTS dump_seller_count INT",
            "ADD COLUMN IF NOT EXISTS dump_seller_hhi   FLOAT",
            "ADD COLUMN IF NOT EXISTS dump_top_seller_frac FLOAT",
            # H26 — concentração de buyers na subida (largest_buy / total_buy_vol)
            "ADD COLUMN IF NOT EXISTS buy_concentration FLOAT",
            # Tiering de qualidade de dados
            "ADD COLUMN IF NOT EXISTS data_quality      TEXT",
            # H27 — token apareceu no GMGN trending nos 15min antes do bounce?
            "ADD COLUMN IF NOT EXISTS trending_at_bounce BOOLEAN",
            # H28 — velocidade de migração (segundos da criação até migrar)
            "ADD COLUMN IF NOT EXISTS time_to_migrate_sec INT",
            # H29 — token estava no trending pump.fun no momento da migração?
            "ADD COLUMN IF NOT EXISTS trending_at_migration BOOLEAN",
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

        # Backfill h23_window: rotula dados históricos como 'bounce' (janela errada)
        # Novos bounces pós-correção recebem 'bottom' no insert do scanner.
        cur.execute("""
            UPDATE bounce_signals
            SET h23_window = 'bounce'
            WHERE h23_window IS NULL
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
                -- Audit: gate Helius de compra real
                helius_gate_passed            BOOLEAN,
                helius_gate_reason            TEXT,
                helius_pool_quote_at_signal   FLOAT,
                helius_absorption_events      INT,
                helius_net_sol_flow           FLOAT,
                helius_rise_events            INT,
                helius_rise_net_flow          FLOAT,
                helius_dump_seller_hhi        FLOAT,
                helius_buy_concentration      FLOAT,
                helius_h23_window             TEXT,
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
            "ADD COLUMN IF NOT EXISTS helius_gate_passed             BOOLEAN",
            "ADD COLUMN IF NOT EXISTS helius_gate_reason             TEXT",
            "ADD COLUMN IF NOT EXISTS helius_pool_quote_at_signal    FLOAT",
            "ADD COLUMN IF NOT EXISTS helius_absorption_events       INT",
            "ADD COLUMN IF NOT EXISTS helius_net_sol_flow            FLOAT",
            "ADD COLUMN IF NOT EXISTS helius_rise_events             INT",
            "ADD COLUMN IF NOT EXISTS helius_rise_net_flow           FLOAT",
            "ADD COLUMN IF NOT EXISTS helius_dump_seller_hhi         FLOAT",
            "ADD COLUMN IF NOT EXISTS helius_buy_concentration       FLOAT",
            "ADD COLUMN IF NOT EXISTS helius_h23_window              TEXT",
            # Tiering de qualidade de dados
            "ADD COLUMN IF NOT EXISTS data_quality                   TEXT",
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

        # ── Fase 1 V2 — Micro absorption snapshots (research-only) ──────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS micro_absorption_snapshots (
                id                   BIGSERIAL PRIMARY KEY,
                mint                 TEXT NOT NULL,
                symbol               TEXT,
                ts_snapshot          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ts_migration         TIMESTAMPTZ,
                ts_min               TIMESTAMPTZ,
                mc_migration         FLOAT,
                mc_min               FLOAT,
                mc_current           FLOAT,
                elapsed_sec          INT,
                bottom_age_sec       FLOAT,
                bounce_pct_now       FLOAT,
                drop_from_mig        FLOAT,
                pool_quote           FLOAT,
                -- PRE windows (centradas no fundo: [ts_min - Xs, ts_min])
                pre_5s_buy           FLOAT,
                pre_5s_sell          FLOAT,
                pre_5s_net           FLOAT,
                pre_5s_events        INT,
                pre_5s_largest_buy   FLOAT,
                pre_5s_largest_sell  FLOAT,
                pre_5s_zero_type     TEXT,
                pre_10s_buy          FLOAT,
                pre_10s_sell         FLOAT,
                pre_10s_net          FLOAT,
                pre_10s_events       INT,
                pre_10s_largest_buy  FLOAT,
                pre_10s_largest_sell FLOAT,
                pre_10s_zero_type    TEXT,
                pre_15s_buy          FLOAT,
                pre_15s_sell         FLOAT,
                pre_15s_net          FLOAT,
                pre_15s_events       INT,
                pre_15s_largest_buy  FLOAT,
                pre_15s_largest_sell FLOAT,
                pre_15s_zero_type    TEXT,
                pre_30s_buy          FLOAT,
                pre_30s_sell         FLOAT,
                pre_30s_net          FLOAT,
                pre_30s_events       INT,
                pre_30s_largest_buy  FLOAT,
                pre_30s_largest_sell FLOAT,
                pre_30s_zero_type    TEXT,
                -- RISE windows (desde ts_min até N segundos depois)
                rise_1s_buy           FLOAT,
                rise_1s_sell          FLOAT,
                rise_1s_net           FLOAT,
                rise_1s_events        INT,
                rise_1s_largest_buy   FLOAT,
                rise_1s_largest_sell  FLOAT,
                rise_1s_zero_type     TEXT,
                rise_3s_buy           FLOAT,
                rise_3s_sell          FLOAT,
                rise_3s_net           FLOAT,
                rise_3s_events        INT,
                rise_3s_largest_buy   FLOAT,
                rise_3s_largest_sell  FLOAT,
                rise_3s_zero_type     TEXT,
                rise_5s_buy           FLOAT,
                rise_5s_sell          FLOAT,
                rise_5s_net           FLOAT,
                rise_5s_events        INT,
                rise_5s_largest_buy   FLOAT,
                rise_5s_largest_sell  FLOAT,
                rise_5s_zero_type     TEXT,
                rise_10s_buy          FLOAT,
                rise_10s_sell         FLOAT,
                rise_10s_net          FLOAT,
                rise_10s_events       INT,
                rise_10s_largest_buy  FLOAT,
                rise_10s_largest_sell FLOAT,
                rise_10s_zero_type    TEXT,
                rise_now_buy          FLOAT,
                rise_now_sell         FLOAT,
                rise_now_net          FLOAT,
                rise_now_events       INT,
                rise_now_largest_buy  FLOAT,
                rise_now_largest_sell FLOAT,
                rise_now_zero_type    TEXT,
                -- LIVE windows (últimos Xs antes de agora)
                live_5s_buy           FLOAT,
                live_5s_sell          FLOAT,
                live_5s_net           FLOAT,
                live_5s_events        INT,
                live_10s_buy          FLOAT,
                live_10s_sell         FLOAT,
                live_10s_net          FLOAT,
                live_10s_events       INT,
                -- Concentrations
                buy_concentration_5s   FLOAT,
                buy_concentration_10s  FLOAT,
                sell_concentration_5s  FLOAT,
                sell_concentration_10s FLOAT,
                -- Research flags
                candidate_flags JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_micro_abs_snap_mint_ts "
            "ON micro_absorption_snapshots (mint, ts_snapshot DESC)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_micro_abs_snap_ts "
            "ON micro_absorption_snapshots (ts_snapshot DESC)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_micro_abs_snap_flags "
            "ON micro_absorption_snapshots USING gin(candidate_flags)"
        )
        # Concentrações nomeadas por janela (adicionadas no ajuste pós-Fase1)
        for col in [
            "ADD COLUMN IF NOT EXISTS pre_5s_buy_concentration   FLOAT",
            "ADD COLUMN IF NOT EXISTS pre_10s_buy_concentration  FLOAT",
            "ADD COLUMN IF NOT EXISTS rise_5s_buy_concentration  FLOAT",
            "ADD COLUMN IF NOT EXISTS rise_10s_buy_concentration FLOAT",
            "ADD COLUMN IF NOT EXISTS live_5s_buy_concentration  FLOAT",
            "ADD COLUMN IF NOT EXISTS live_10s_buy_concentration FLOAT",
            # live_30s / live_60s — janelas LIVE mais largas (V2 Fase 1, adicionadas pós-deploy)
            "ADD COLUMN IF NOT EXISTS live_30s_buy    FLOAT",
            "ADD COLUMN IF NOT EXISTS live_30s_sell   FLOAT",
            "ADD COLUMN IF NOT EXISTS live_30s_net    FLOAT",
            "ADD COLUMN IF NOT EXISTS live_30s_events INT",
            "ADD COLUMN IF NOT EXISTS live_60s_buy    FLOAT",
            "ADD COLUMN IF NOT EXISTS live_60s_sell   FLOAT",
            "ADD COLUMN IF NOT EXISTS live_60s_net    FLOAT",
            "ADD COLUMN IF NOT EXISTS live_60s_events INT",
        ]:
            cur.execute(f"ALTER TABLE micro_absorption_snapshots {col}")

        # Sell exhaustion candidates — research-only data accumulation
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sell_exhaustion_candidates (
                id              BIGSERIAL PRIMARY KEY,
                mint            TEXT NOT NULL,
                symbol          TEXT,
                ts_signal       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                mc_at_signal    FLOAT,
                mc_migration    FLOAT,
                mc_min          FLOAT,
                drop_from_mig   FLOAT,
                elapsed_sec     INT,
                pool_quote      FLOAT,
                sell_5s         FLOAT,
                sell_5_15s      FLOAT,
                sell_cnt_5s     INT,
                sell_ratio      FLOAT,
                bottom_age_sec  FLOAT,
                UNIQUE (mint, ts_signal)
            )
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_sell_exh_ts "
            "ON sell_exhaustion_candidates (ts_signal DESC)"
        )

        # Runtime heartbeats — rastreia IP de saída de web e worker no Railway.
        # Coluna primary key é runtime_name (string única: "web", "worker").
        cur.execute("""
            CREATE TABLE IF NOT EXISTS runtime_heartbeats (
                runtime_name  TEXT        PRIMARY KEY,
                service_type  TEXT,
                outbound_ip   TEXT,
                hostname      TEXT,
                pid           INT,
                metadata      JSONB,
                ws_health     JSONB,
                updated_at    TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("""
            ALTER TABLE runtime_heartbeats
                ADD COLUMN IF NOT EXISTS ws_health JSONB
        """)

    logger.info("DB inicializado")


def insert_sell_exhaustion_candidate(data: dict) -> None:
    """Persiste um candidato de sell exhaustion. Research-only, nunca afeta trading."""
    try:
        with _cursor() as cur:
            cur.execute("""
                INSERT INTO sell_exhaustion_candidates (
                    mint, symbol, ts_signal, mc_at_signal, mc_migration, mc_min,
                    drop_from_mig, elapsed_sec, pool_quote,
                    sell_5s, sell_5_15s, sell_cnt_5s, sell_ratio, bottom_age_sec
                ) VALUES (
                    %(mint)s, %(symbol)s, %(ts_signal)s, %(mc_at_signal)s, %(mc_migration)s, %(mc_min)s,
                    %(drop_from_mig)s, %(elapsed_sec)s, %(pool_quote)s,
                    %(sell_5s)s, %(sell_5_15s)s, %(sell_cnt_5s)s, %(sell_ratio)s, %(bottom_age_sec)s
                )
                ON CONFLICT (mint, ts_signal) DO NOTHING
            """, data)
    except Exception as e:
        logger.warning("insert_sell_exhaustion_candidate failed: %s", e)


def upsert_runtime_heartbeat(
    runtime_name: str,
    service_type: str,
    outbound_ip: str,
    hostname: str | None = None,
    pid: int | None = None,
    metadata: dict | None = None,
    ws_health: dict | None = None,
) -> None:
    """Atualiza heartbeat do processo Railway (web ou worker). Upsert por runtime_name."""
    import json as _json
    meta_json     = _json.dumps(metadata)  if metadata  else None
    ws_health_json = _json.dumps(ws_health) if ws_health else None
    try:
        with _cursor() as cur:
            cur.execute("""
                INSERT INTO runtime_heartbeats
                    (runtime_name, service_type, outbound_ip, hostname, pid,
                     metadata, ws_health, updated_at)
                VALUES
                    (%(name)s, %(stype)s, %(ip)s, %(host)s, %(pid)s,
                     %(meta)s::jsonb, %(ws)s::jsonb, NOW())
                ON CONFLICT (runtime_name) DO UPDATE SET
                    service_type = EXCLUDED.service_type,
                    outbound_ip  = EXCLUDED.outbound_ip,
                    hostname     = EXCLUDED.hostname,
                    pid          = EXCLUDED.pid,
                    metadata     = EXCLUDED.metadata,
                    ws_health    = EXCLUDED.ws_health,
                    updated_at   = NOW()
            """, {
                "name":  runtime_name,
                "stype": service_type,
                "ip":    outbound_ip,
                "host":  hostname,
                "pid":   pid,
                "meta":  meta_json,
                "ws":    ws_health_json,
            })
    except Exception as e:
        logger.warning("upsert_runtime_heartbeat %s: %s", runtime_name, e)


def get_runtime_heartbeats() -> list:
    """Retorna lista de heartbeats salvos (web + worker)."""
    try:
        with _cursor() as cur:
            cur.execute("""
                SELECT runtime_name, service_type, outbound_ip, hostname, pid,
                       metadata, ws_health, updated_at
                  FROM runtime_heartbeats
                 ORDER BY runtime_name
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning("get_runtime_heartbeats: %s", e)
        return []


def get_worker_ws_health() -> dict:
    """Lê o último ws_health do worker persistido no DB. Usado pelo web service."""
    try:
        with _cursor() as cur:
            cur.execute("""
                SELECT ws_health, updated_at
                  FROM runtime_heartbeats
                 WHERE runtime_name = 'worker'
            """)
            row = cur.fetchone()
            if not row or not row["ws_health"]:
                return {}
            import json as _json
            h = row["ws_health"] if isinstance(row["ws_health"], dict) else _json.loads(row["ws_health"])
            h["_db_updated_at"] = row["updated_at"].isoformat() if row["updated_at"] else None
            return h
    except Exception as e:
        logger.warning("get_worker_ws_health: %s", e)
        return {}


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
                bc_progress_migration, complete_timestamp,
                trending_at_migration, passed_filters,
                hot_level, visiting_count, tg_call_count
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
                %(bc_progress_migration)s, %(complete_timestamp)s,
                %(trending_at_migration)s, %(passed_filters)s,
                %(hot_level)s, %(visiting_count)s, %(tg_call_count)s
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
                x_follower_count       = COALESCE(migrations.x_follower_count, EXCLUDED.x_follower_count),
                trending_at_migration  = COALESCE(migrations.trending_at_migration, EXCLUDED.trending_at_migration),
                passed_filters         = EXCLUDED.passed_filters,
                hot_level              = COALESCE(migrations.hot_level, EXCLUDED.hot_level),
                visiting_count         = COALESCE(migrations.visiting_count, EXCLUDED.visiting_count),
                tg_call_count          = COALESCE(migrations.tg_call_count, EXCLUDED.tg_call_count)
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
            "complete_timestamp":       data.get("complete_timestamp"),
            "trending_at_migration":    data.get("trending_at_migration"),
            "passed_filters":           data.get("passed_filters"),
            "hot_level":                data.get("hot_level"),
            "visiting_count":           data.get("visiting_count"),
            "tg_call_count":            data.get("tg_call_count"),
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


# Timestamps de transição de era (epoch UTC)
# Era 0: < ERA_CLEAN_START_ERPC — corrompido (ts_migration bug + elapsed_sec bug)
# Era 1: ERA_CLEAN_START_ERPC to ERA_CLEAN_START_POOLFIX — erpc ativo, MIN_POOL_SOL=2 (43% phantoms)
# Era 2: >= ERA_CLEAN_START_POOLFIX — dados confiáveis (erpc + MIN_POOL_SOL=10)
_ERA_ERPC_START   = 1747788060  # 2026-05-21 00:41 UTC (primeiro token limpo JOB)
_ERA_POOLFIX_START = 1748044800  # 2026-05-24 00:00 UTC (MIN_POOL_SOL=10 restaurado)


def _classify_migration_quality(complete_timestamp, bottom_quality):
    """Retorna (data_quality, truth_source) para um token de migração."""
    if bottom_quality is not None:
        return bottom_quality, "onchain_scan"
    if complete_timestamp and complete_timestamp >= _ERA_POOLFIX_START:
        return "ok", "none"  # era limpa mas mc_min_true ainda não chegou
    return "bad", "none"


def backfill_data_quality(mint: str = None):
    """Classifica data_quality em migrations, bounce_signals e trades.

    Se mint=None, processa TODOS os registros com data_quality IS NULL.
    Se mint=str, processa apenas o token específico (chamado após update_bottom_truth).
    Regras:
      - bottom_quality preenchido → herda diretamente (evidência on-chain)
      - Era 2 (>= 2026-05-24) sem bottom_quality → 'ok'
      - Era 0/1 → 'bad'
    Para trades/bounce_signals: herdado de migrations via JOIN. Trades em era < 2026-05-24 → 'bad'.
    """
    with _cursor() as cur:
        mint_filter = "AND m.mint = %(mint)s" if mint else ""
        params = {"mint": mint} if mint else {}
        params["era_poolfix"] = _ERA_POOLFIX_START

        # 1. migrations
        cur.execute(f"""
            UPDATE migrations m
               SET data_quality = CASE
                 WHEN m.bottom_quality IS NOT NULL THEN m.bottom_quality
                 WHEN m.complete_timestamp >= %(era_poolfix)s THEN 'ok'
                 ELSE 'bad'
               END
             WHERE m.data_quality IS NULL
               OR  m.data_quality != CASE
                 WHEN m.bottom_quality IS NOT NULL THEN m.bottom_quality
                 WHEN m.complete_timestamp >= %(era_poolfix)s THEN 'ok'
                 ELSE 'bad'
               END
               {mint_filter}
        """, params)
        n_mig = cur.rowcount

        # 2. bounce_signals — herda de migrations; ts_signal < era_poolfix → 'bad'
        cur.execute(f"""
            UPDATE bounce_signals b
               SET data_quality = CASE
                 WHEN b.ts_signal < to_timestamp(%(era_poolfix)s) THEN 'bad'
                 WHEN m.data_quality IS NOT NULL THEN m.data_quality
                 ELSE 'ok'
               END
              FROM migrations m
             WHERE b.mint = m.mint
               AND (b.data_quality IS NULL
                    OR b.data_quality != CASE
                      WHEN b.ts_signal < to_timestamp(%(era_poolfix)s) THEN 'bad'
                      WHEN m.data_quality IS NOT NULL THEN m.data_quality
                      ELSE 'ok'
                    END)
               {mint_filter.replace('m.mint', 'b.mint')}
        """, params)
        n_bs = cur.rowcount

        # 3. trades — herda de migrations; ts_signal < era_poolfix → 'bad'
        cur.execute(f"""
            UPDATE trades t
               SET data_quality = CASE
                 WHEN t.ts_signal < to_timestamp(%(era_poolfix)s) THEN 'bad'
                 WHEN m.data_quality IS NOT NULL THEN m.data_quality
                 ELSE 'ok'
               END
              FROM migrations m
             WHERE t.mint = m.mint
               AND (t.data_quality IS NULL
                    OR t.data_quality != CASE
                      WHEN t.ts_signal < to_timestamp(%(era_poolfix)s) THEN 'bad'
                      WHEN m.data_quality IS NOT NULL THEN m.data_quality
                      ELSE 'ok'
                    END)
               {mint_filter.replace('m.mint', 't.mint')}
        """, params)
        n_tr = cur.rowcount

    if n_mig + n_bs + n_tr > 0:
        logger.info(
            "backfill_data_quality mint=%s: migrations=%d bounce_signals=%d trades=%d",
            mint or "ALL", n_mig, n_bs, n_tr,
        )


def update_bottom_truth(
    mint: str,
    mc_min_true: float,
    ts_min_true: float,
    txid_min_true: str,
    source_truth: str,
    bottom_error_pct: float | None,
    bottom_quality: str | None,
):
    """Persiste o mc_min real verificado on-chain com prova de txid.

    Chamado de forma assíncrona durante a graduação do token.
    bottom_error_pct = (mc_min_decision - mc_min_true) / mc_min_true
      positivo → bot viu fundo mais alto (perdeu o fundo real)
      negativo → bot viu fundo mais baixo (phantom ou secondary pool)
    bottom_quality: 'good' (|error|≤5%), 'ok' (5-15%), 'bad' (>15%)
    """
    from datetime import datetime, timezone
    ts_dt = datetime.fromtimestamp(ts_min_true, tz=timezone.utc) if ts_min_true else None
    with _cursor() as cur:
        cur.execute("""
            UPDATE migrations
               SET mc_min_true      = %(mc_min_true)s,
                   ts_min_true      = %(ts_min_true)s,
                   txid_min_true    = %(txid_min_true)s,
                   source_truth     = %(source_truth)s,
                   bottom_error_pct = %(bottom_error_pct)s,
                   bottom_quality   = %(bottom_quality)s
             WHERE mint = %(mint)s
        """, {
            "mint":             mint,
            "mc_min_true":      mc_min_true,
            "ts_min_true":      ts_dt,
            "txid_min_true":    txid_min_true,
            "source_truth":     source_truth,
            "bottom_error_pct": bottom_error_pct,
            "bottom_quality":   bottom_quality,
        })
    # Propaga data_quality para trades e bounce_signals deste token
    backfill_data_quality(mint=mint)


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
                 net_sol_flow, h23_window,
                 rise_buy_vol, rise_sell_vol, rise_net_flow, rise_events, rise_lrg_buy,
                 pool_quote_at_signal,
                 dump_seller_count, dump_seller_hhi, dump_top_seller_frac,
                 buy_concentration, data_quality, trending_at_bounce, time_to_migrate_sec)
            VALUES
                (%(mint)s, %(symbol)s, %(exchange)s, %(elapsed_sec)s, %(mc_migration)s, %(mc_min)s,
                 %(mc_at_signal)s, %(drop_from_mig)s, %(bounce_pct)s,
                 %(absorption_score)s, %(absorption_grade)s,
                 %(absorption_buy_vol)s, %(absorption_sell_vol)s,
                 %(absorption_buy_cnt)s, %(absorption_sell_cnt)s,
                 %(absorption_lrg_buy)s, %(absorption_events)s,
                 %(net_sol_flow)s, %(h23_window)s,
                 %(rise_buy_vol)s, %(rise_sell_vol)s, %(rise_net_flow)s,
                 %(rise_events)s, %(rise_lrg_buy)s,
                 %(pool_quote_at_signal)s,
                 %(dump_seller_count)s, %(dump_seller_hhi)s, %(dump_top_seller_frac)s,
                 %(buy_concentration)s, %(data_quality)s,
                 %(trending_at_bounce)s, %(time_to_migrate_sec)s)
            ON CONFLICT (mint) DO UPDATE SET
                absorption_score       = EXCLUDED.absorption_score,
                absorption_grade       = EXCLUDED.absorption_grade,
                absorption_buy_vol     = EXCLUDED.absorption_buy_vol,
                absorption_sell_vol    = EXCLUDED.absorption_sell_vol,
                absorption_buy_cnt     = EXCLUDED.absorption_buy_cnt,
                absorption_sell_cnt    = EXCLUDED.absorption_sell_cnt,
                absorption_lrg_buy     = EXCLUDED.absorption_lrg_buy,
                absorption_events      = EXCLUDED.absorption_events,
                net_sol_flow           = EXCLUDED.net_sol_flow,
                h23_window             = EXCLUDED.h23_window,
                rise_buy_vol           = EXCLUDED.rise_buy_vol,
                rise_sell_vol          = EXCLUDED.rise_sell_vol,
                rise_net_flow          = EXCLUDED.rise_net_flow,
                rise_events            = EXCLUDED.rise_events,
                rise_lrg_buy           = EXCLUDED.rise_lrg_buy,
                pool_quote_at_signal   = EXCLUDED.pool_quote_at_signal,
                dump_seller_count      = EXCLUDED.dump_seller_count,
                dump_seller_hhi        = EXCLUDED.dump_seller_hhi,
                dump_top_seller_frac   = EXCLUDED.dump_top_seller_frac,
                buy_concentration      = EXCLUDED.buy_concentration,
                trending_at_bounce     = COALESCE(bounce_signals.trending_at_bounce, EXCLUDED.trending_at_bounce),
                time_to_migrate_sec    = COALESCE(bounce_signals.time_to_migrate_sec, EXCLUDED.time_to_migrate_sec)
            WHERE bounce_signals.absorption_events IS NULL
        """, {
            **data,
            "exchange":              data.get("exchange"),
            "absorption_score":      data.get("absorption_score"),
            "absorption_grade":      data.get("absorption_grade"),
            "absorption_buy_vol":    data.get("absorption_buy_vol"),
            "absorption_sell_vol":   data.get("absorption_sell_vol"),
            "absorption_buy_cnt":    data.get("absorption_buy_cnt"),
            "absorption_sell_cnt":   data.get("absorption_sell_cnt"),
            "absorption_lrg_buy":    data.get("absorption_lrg_buy"),
            "absorption_events":     data.get("absorption_events"),
            "net_sol_flow":          data.get("net_sol_flow"),
            "h23_window":            data.get("h23_window"),
            "rise_buy_vol":          data.get("rise_buy_vol"),
            "rise_sell_vol":         data.get("rise_sell_vol"),
            "rise_net_flow":         data.get("rise_net_flow"),
            "rise_events":           data.get("rise_events"),
            "rise_lrg_buy":          data.get("rise_lrg_buy"),
            "pool_quote_at_signal":  data.get("pool_quote_at_signal"),
            "dump_seller_count":     data.get("dump_seller_count"),
            "dump_seller_hhi":       data.get("dump_seller_hhi"),
            "dump_top_seller_frac":  data.get("dump_top_seller_frac"),
            "buy_concentration":     data.get("buy_concentration"),
            "data_quality":          _trade_data_quality(data.get("ts_signal"), data.get("mint", "")),
            "trending_at_bounce":    data.get("trending_at_bounce"),
            "time_to_migrate_sec":   data.get("time_to_migrate_sec"),
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


def insert_micro_absorption_snapshot(data: dict) -> None:
    """Insere snapshot de micro-absorção (research-only).
    Insert-only — nunca atualiza trades, bounce_signals ou token_ticks.
    Exceções são capturadas aqui e logadas como DEBUG; o scanner não quebra.
    """
    import json as _json
    try:
        flags = data.get("candidate_flags")
        flags_json = _json.dumps(flags) if isinstance(flags, dict) else None

        with _cursor() as cur:
            cur.execute("""
                INSERT INTO micro_absorption_snapshots (
                    mint, symbol, ts_snapshot, ts_migration, ts_min,
                    mc_migration, mc_min, mc_current,
                    elapsed_sec, bottom_age_sec, bounce_pct_now, drop_from_mig,
                    pool_quote,
                    pre_5s_buy,  pre_5s_sell,  pre_5s_net,  pre_5s_events,
                    pre_5s_largest_buy, pre_5s_largest_sell, pre_5s_zero_type,
                    pre_10s_buy, pre_10s_sell, pre_10s_net, pre_10s_events,
                    pre_10s_largest_buy, pre_10s_largest_sell, pre_10s_zero_type,
                    pre_15s_buy, pre_15s_sell, pre_15s_net, pre_15s_events,
                    pre_15s_largest_buy, pre_15s_largest_sell, pre_15s_zero_type,
                    pre_30s_buy, pre_30s_sell, pre_30s_net, pre_30s_events,
                    pre_30s_largest_buy, pre_30s_largest_sell, pre_30s_zero_type,
                    rise_1s_buy,  rise_1s_sell,  rise_1s_net,  rise_1s_events,
                    rise_1s_largest_buy, rise_1s_largest_sell, rise_1s_zero_type,
                    rise_3s_buy,  rise_3s_sell,  rise_3s_net,  rise_3s_events,
                    rise_3s_largest_buy, rise_3s_largest_sell, rise_3s_zero_type,
                    rise_5s_buy,  rise_5s_sell,  rise_5s_net,  rise_5s_events,
                    rise_5s_largest_buy, rise_5s_largest_sell, rise_5s_zero_type,
                    rise_10s_buy, rise_10s_sell, rise_10s_net, rise_10s_events,
                    rise_10s_largest_buy, rise_10s_largest_sell, rise_10s_zero_type,
                    rise_now_buy, rise_now_sell, rise_now_net, rise_now_events,
                    rise_now_largest_buy, rise_now_largest_sell, rise_now_zero_type,
                    live_5s_buy,  live_5s_sell,  live_5s_net,  live_5s_events,
                    live_10s_buy, live_10s_sell, live_10s_net, live_10s_events,
                    live_30s_buy, live_30s_sell, live_30s_net, live_30s_events,
                    live_60s_buy, live_60s_sell, live_60s_net, live_60s_events,
                    buy_concentration_5s,  buy_concentration_10s,
                    sell_concentration_5s, sell_concentration_10s,
                    pre_5s_buy_concentration,  pre_10s_buy_concentration,
                    rise_5s_buy_concentration, rise_10s_buy_concentration,
                    live_5s_buy_concentration, live_10s_buy_concentration,
                    candidate_flags
                ) VALUES (
                    %(mint)s, %(symbol)s, %(ts_snapshot)s, %(ts_migration)s, %(ts_min)s,
                    %(mc_migration)s, %(mc_min)s, %(mc_current)s,
                    %(elapsed_sec)s, %(bottom_age_sec)s, %(bounce_pct_now)s, %(drop_from_mig)s,
                    %(pool_quote)s,
                    %(pre_5s_buy)s,  %(pre_5s_sell)s,  %(pre_5s_net)s,  %(pre_5s_events)s,
                    %(pre_5s_largest_buy)s, %(pre_5s_largest_sell)s, %(pre_5s_zero_type)s,
                    %(pre_10s_buy)s, %(pre_10s_sell)s, %(pre_10s_net)s, %(pre_10s_events)s,
                    %(pre_10s_largest_buy)s, %(pre_10s_largest_sell)s, %(pre_10s_zero_type)s,
                    %(pre_15s_buy)s, %(pre_15s_sell)s, %(pre_15s_net)s, %(pre_15s_events)s,
                    %(pre_15s_largest_buy)s, %(pre_15s_largest_sell)s, %(pre_15s_zero_type)s,
                    %(pre_30s_buy)s, %(pre_30s_sell)s, %(pre_30s_net)s, %(pre_30s_events)s,
                    %(pre_30s_largest_buy)s, %(pre_30s_largest_sell)s, %(pre_30s_zero_type)s,
                    %(rise_1s_buy)s,  %(rise_1s_sell)s,  %(rise_1s_net)s,  %(rise_1s_events)s,
                    %(rise_1s_largest_buy)s, %(rise_1s_largest_sell)s, %(rise_1s_zero_type)s,
                    %(rise_3s_buy)s,  %(rise_3s_sell)s,  %(rise_3s_net)s,  %(rise_3s_events)s,
                    %(rise_3s_largest_buy)s, %(rise_3s_largest_sell)s, %(rise_3s_zero_type)s,
                    %(rise_5s_buy)s,  %(rise_5s_sell)s,  %(rise_5s_net)s,  %(rise_5s_events)s,
                    %(rise_5s_largest_buy)s, %(rise_5s_largest_sell)s, %(rise_5s_zero_type)s,
                    %(rise_10s_buy)s, %(rise_10s_sell)s, %(rise_10s_net)s, %(rise_10s_events)s,
                    %(rise_10s_largest_buy)s, %(rise_10s_largest_sell)s, %(rise_10s_zero_type)s,
                    %(rise_now_buy)s, %(rise_now_sell)s, %(rise_now_net)s, %(rise_now_events)s,
                    %(rise_now_largest_buy)s, %(rise_now_largest_sell)s, %(rise_now_zero_type)s,
                    %(live_5s_buy)s,  %(live_5s_sell)s,  %(live_5s_net)s,  %(live_5s_events)s,
                    %(live_10s_buy)s, %(live_10s_sell)s, %(live_10s_net)s, %(live_10s_events)s,
                    %(live_30s_buy)s, %(live_30s_sell)s, %(live_30s_net)s, %(live_30s_events)s,
                    %(live_60s_buy)s, %(live_60s_sell)s, %(live_60s_net)s, %(live_60s_events)s,
                    %(buy_concentration_5s)s,  %(buy_concentration_10s)s,
                    %(sell_concentration_5s)s, %(sell_concentration_10s)s,
                    %(pre_5s_buy_concentration)s,  %(pre_10s_buy_concentration)s,
                    %(rise_5s_buy_concentration)s, %(rise_10s_buy_concentration)s,
                    %(live_5s_buy_concentration)s, %(live_10s_buy_concentration)s,
                    %(candidate_flags)s::jsonb
                )
            """, {**data, "candidate_flags": flags_json})
    except Exception as e:
        logger.debug("insert_micro_absorption_snapshot %s: %s",
                     str(data.get("mint", ""))[:8], e)


def get_micro_absorption_snapshots(
    days: int = 1,
    mint: Optional[str] = None,
    limit: int = 200,
    summary: bool = False,
    min_age: Optional[float] = None,
) -> list:
    """Retorna micro snapshots recentes para inspeção de pesquisa.

    summary=True → uma linha por bounce (mint + ts_min), o snapshot com
    bottom_age_sec máximo (mais próximo dos 60s finais).
    summary=False → todas as linhas individualmente (série temporal).
    min_age → filtra eventos com bottom_age_sec >= min_age (só eventos completos).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    params: dict = {"limit": limit, "cutoff": cutoff}
    mint_clause = ""
    if mint:
        mint_clause = "AND mint = %(mint)s"
        params["mint"] = mint

    age_having = ""
    if min_age is not None:
        age_having = "AND MAX(bottom_age_sec) >= %(min_age)s"
        params["min_age"] = min_age

    if summary:
        if min_age is not None:
            # Filtra pares (mint, ts_min) cujo snapshot máximo passa do threshold,
            # depois seleciona o snapshot mais completo de cada par.
            query = f"""
                WITH eligible AS (
                    SELECT mint, ts_min
                    FROM micro_absorption_snapshots
                    WHERE ts_snapshot >= %(cutoff)s {mint_clause}
                    GROUP BY mint, ts_min
                    {age_having}
                )
                SELECT DISTINCT ON (s.mint, s.ts_min) s.*
                FROM micro_absorption_snapshots s
                JOIN eligible e ON s.mint = e.mint AND s.ts_min = e.ts_min
                ORDER BY s.mint, s.ts_min DESC, s.bottom_age_sec DESC
                LIMIT %(limit)s
            """
        else:
            query = f"""
                SELECT DISTINCT ON (mint, ts_min) *
                FROM micro_absorption_snapshots
                WHERE ts_snapshot >= %(cutoff)s
                  {mint_clause}
                ORDER BY mint, ts_min DESC, bottom_age_sec DESC
                LIMIT %(limit)s
            """
    else:
        age_clause = "AND bottom_age_sec >= %(min_age)s" if min_age is not None else ""
        query = f"""
            SELECT *
            FROM micro_absorption_snapshots
            WHERE ts_snapshot >= %(cutoff)s
              {mint_clause}
              {age_clause}
            ORDER BY ts_snapshot DESC
            LIMIT %(limit)s
        """

    with _cursor() as cur:
        cur.execute(query, params)
        rows = [dict(row) for row in cur.fetchall()]

    if summary:
        rows.sort(key=lambda r: r.get("ts_min") or r.get("ts_snapshot"), reverse=True)

    return rows


def get_micro_absorption_stats(days: int = 1, mint: Optional[str] = None) -> dict:
    """Conta totais de snapshots, bounce events e tokens no período."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    params: dict = {"cutoff": cutoff}
    mint_clause = ""
    if mint:
        mint_clause = "AND mint = %(mint)s"
        params["mint"] = mint

    query = f"""
        SELECT
            COUNT(*)                                          AS total_snapshots,
            COUNT(DISTINCT (mint, ts_min))                   AS total_bounces,
            COUNT(DISTINCT mint)                             AS total_tokens,
            COUNT(DISTINCT (mint, ts_min))
              FILTER (WHERE bottom_age_sec >= 50)            AS complete_bounces,
            MIN(ts_snapshot)                                 AS first_ts,
            MAX(ts_snapshot)                                 AS last_ts
        FROM micro_absorption_snapshots
        WHERE ts_snapshot >= %(cutoff)s {mint_clause}
    """
    with _cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    return dict(row) if row else {}


def get_micro_outcomes(
    days: int = 7,
    min_age: float = 50,
    entry_pct: float = 0.15,
    tp_pct: float = 0.75,
    sl_pct: float = 0.25,
) -> list:
    """Combina micro_absorption_snapshots (completos) com token_ticks para simular outcome.

    entry_mc  = mc_min * (1 + entry_pct)   — entrada estimada acima do fundo
    tp_mc     = entry_mc * (1 + tp_pct)    — alvo de ganho
    sl_mc     = entry_mc * (1 - sl_pct)    — stop-loss
    outcome   = 'tp' | 'sl' | 'no_exit'   — determinado pelo primeiro cruzamento em 60 min de ticks
    """
    from collections import defaultdict

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # ── 1. Complete bottom events ────────────────────────────────────────────
    bottoms_q = """
        SELECT DISTINCT ON (mint, ts_min)
            mint, symbol, ts_min, ts_migration,
            mc_min, mc_migration, bounce_pct_now, drop_from_mig,
            elapsed_sec, pool_quote, candidate_flags,
            pre_5s_net,  pre_10s_net,  pre_15s_net,  pre_30s_net,
            pre_5s_buy,  pre_10s_buy,  pre_30s_buy,
            pre_5s_sell, pre_10s_sell, pre_30s_sell,
            pre_5s_events, pre_10s_events, pre_30s_events,
            pre_5s_largest_buy,  pre_10s_largest_buy,  pre_30s_largest_buy,
            pre_5s_largest_sell, pre_10s_largest_sell, pre_30s_largest_sell,
            buy_concentration_5s, buy_concentration_10s,
            sell_concentration_5s, sell_concentration_10s,
            rise_1s_net,  rise_3s_net,  rise_5s_net,  rise_10s_net,
            rise_1s_zero_type, rise_3s_zero_type, rise_5s_zero_type, rise_10s_zero_type,
            rise_now_net, live_5s_net, live_10s_net
        FROM micro_absorption_snapshots
        WHERE bottom_age_sec >= %(min_age)s
          AND ts_snapshot >= %(cutoff)s
          AND mc_min IS NOT NULL
          AND ts_min IS NOT NULL
        ORDER BY mint, ts_min, bottom_age_sec DESC
    """
    with _cursor() as cur:
        cur.execute(bottoms_q, {"min_age": min_age, "cutoff": cutoff})
        bottoms = [dict(r) for r in cur.fetchall()]

    if not bottoms:
        return []

    # ── 2. Bulk-fetch token_ticks para todos os mints no período ────────────
    mints = list({b["mint"] for b in bottoms})
    ts_floor = min(b["ts_min"] for b in bottoms)
    ts_ceil  = max(b["ts_min"] for b in bottoms) + timedelta(hours=1, minutes=5)

    ticks_q = """
        SELECT mint, tick_ts, mc, mc_open, mc_high, mc_low, source
        FROM token_ticks
        WHERE mint = ANY(%(mints)s)
          AND tick_ts >  %(ts_floor)s
          AND tick_ts <= %(ts_ceil)s
        ORDER BY mint, tick_ts
    """
    with _cursor() as cur:
        cur.execute(ticks_q, {"mints": mints, "ts_floor": ts_floor, "ts_ceil": ts_ceil})
        ticks_by_mint: dict = defaultdict(list)
        for t in cur.fetchall():
            ticks_by_mint[t["mint"]].append(dict(t))

    # ── 3. Calcula outcome para cada bottom event ────────────────────────────
    results = []
    entry_mult = 1 + entry_pct
    tp_mult    = 1 + tp_pct
    sl_mult    = 1 - sl_pct

    for b in bottoms:
        mc_min = b["mc_min"]
        ts_min = b["ts_min"]
        ts_end = ts_min + timedelta(minutes=60)

        entry_mc = mc_min * entry_mult
        tp_mc    = entry_mc * tp_mult
        sl_mc    = entry_mc * sl_mult

        window_ticks = [
            t for t in ticks_by_mint[b["mint"]]
            if t["tick_ts"] > ts_min and t["tick_ts"] <= ts_end
        ]

        ts_tp = ts_sl = None
        max_mc_seen = entry_mc

        for t in window_ticks:  # já vem ordenado por tick_ts
            if t["source"] == "kline":
                high = t["mc_high"] or t["mc"] or 0
                low  = t["mc_low"]  or t["mc"] or 0
            else:
                high = low = t["mc"] or 0

            if high > max_mc_seen:
                max_mc_seen = high

            tp_hit = high >= tp_mc
            sl_hit = low  <= sl_mc

            if tp_hit and sl_hit:
                # Desempate via mc_open vs midpoint (nota 34 CLAUDE.md)
                mc_open  = t.get("mc_open") or t["mc"] or 0
                midpoint = (tp_mc + sl_mc) / 2
                if mc_open >= midpoint:
                    if ts_tp is None: ts_tp = t["tick_ts"]
                else:
                    if ts_sl is None: ts_sl = t["tick_ts"]
            elif tp_hit and ts_tp is None:
                ts_tp = t["tick_ts"]
            elif sl_hit and ts_sl is None:
                ts_sl = t["tick_ts"]

            if ts_tp and ts_sl:
                break

        if ts_tp and ts_sl:
            outcome = "tp" if ts_tp <= ts_sl else "sl"
        elif ts_tp:
            outcome = "tp"
        elif ts_sl:
            outcome = "sl"
        else:
            outcome = "no_exit"

        exit_ts = ts_tp if outcome == "tp" else (ts_sl if outcome == "sl" else None)
        elapsed_exit = (exit_ts - ts_min).total_seconds() / 60 if exit_ts else None
        max_gain_pct = (max_mc_seen / entry_mc - 1) * 100 if entry_mc > 0 else None

        results.append({
            **b,
            "entry_mc":         entry_mc,
            "tp_mc":            tp_mc,
            "sl_mc":            sl_mc,
            "ts_tp":            ts_tp,
            "ts_sl":            ts_sl,
            "outcome":          outcome,
            "tick_count":       len(window_ticks),
            "elapsed_exit_min": elapsed_exit,
            "max_gain_pct":     max_gain_pct,
        })

    return results


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

def _trade_data_quality(ts_signal, mint: str) -> str:
    """Determina data_quality de um trade no momento da inserção."""
    import time as _time
    ts_epoch = ts_signal.timestamp() if hasattr(ts_signal, "timestamp") else float(ts_signal or 0)
    if ts_epoch < _ERA_POOLFIX_START:
        return "bad"
    # era limpa: herdar da migration se disponível
    try:
        with _cursor() as cur:
            cur.execute("SELECT data_quality FROM migrations WHERE mint = %s", (mint,))
            row = cur.fetchone()
            if row and row["data_quality"]:
                return row["data_quality"]
    except Exception:
        pass
    return "ok"



# Runtime schema healthcheck: read-only, no DDL. Used by /debug/schema to catch
# Railway deploys where the migration one-off was not executed before web/worker.
REQUIRED_RUNTIME_SCHEMA = {
    "trades": {
        "id", "mint", "symbol", "status", "order_id",
        "helius_gate_passed", "helius_gate_reason",
        "helius_pool_quote_at_signal", "helius_absorption_events",
        "helius_net_sol_flow", "helius_rise_events", "helius_rise_net_flow",
        "helius_dump_seller_hhi", "helius_buy_concentration", "helius_h23_window",
        "data_quality",
    },
    "bounce_signals": {
        "id",
        "mint",
        "ts_signal",
        "mc_at_signal",
        "bounce_pct",
        "pool_quote_at_signal",
        "absorption_events",
        "net_sol_flow",
        "rise_events",
        "rise_net_flow",
        "dump_seller_hhi",
        "dump_top_seller_frac",
        "buy_concentration",
        "h23_window",
        "data_quality",
    },
    "micro_absorption_snapshots": {
        "id",
        "mint",
        "ts_snapshot",
        "bottom_age_sec",
        "mc_current",
        "pool_quote",
        "live_5s_net",
        "live_5s_events",
        "live_10s_net",
        "live_10s_events",
        "live_30s_net",
        "live_30s_events",
        "live_60s_net",
        "live_60s_events",
        "pre_5s_buy_concentration",
        "pre_10s_buy_concentration",
        "rise_5s_buy_concentration",
        "rise_10s_buy_concentration",
        "live_5s_buy_concentration",
        "live_10s_buy_concentration",
        "candidate_flags",
    },
    "token_ticks": {
        "mint", "tick_ts", "mc", "mc_open", "mc_high", "mc_low", "source",
    },
    "runtime_heartbeats": {
        "runtime_name", "service_type", "outbound_ip", "hostname", "pid",
        "metadata", "ws_health", "updated_at",
    },
}


def check_runtime_schema() -> dict:
    """Read-only schema check for runtime-critical analytics/trade columns.

    This intentionally does not run DDL. Missing columns mean the Railway one-off
    migration should be run with RUN_DB_MIGRATIONS=1 python migrate.py.
    """
    result = {
        "ok": False,
        "database_url_configured": bool(DATABASE_URL),
        "tables": {},
        "missing_tables": [],
        "missing_columns": {},
        "required_schema_version": "helius_trade_gate_v1",
    }
    if not DATABASE_URL:
        result["error"] = "DATABASE_URL not configured"
        return result

    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SET lock_timeout = '1500ms'")
            cur.execute("SET statement_timeout = '5000ms'")
            cur.execute("""
                SELECT table_name, column_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = ANY(%s)
            """, (list(REQUIRED_RUNTIME_SCHEMA.keys()),))
            rows = cur.fetchall()
    finally:
        conn.close()

    found = {}
    for r in rows:
        found.setdefault(r["table_name"], set()).add(r["column_name"])

    for table, required_cols in REQUIRED_RUNTIME_SCHEMA.items():
        cols = found.get(table, set())
        if not cols:
            result["missing_tables"].append(table)
            result["tables"][table] = {
                "exists": False,
                "missing_columns": sorted(required_cols),
            }
            result["missing_columns"][table] = sorted(required_cols)
            continue
        missing = sorted(required_cols - cols)
        result["tables"][table] = {
            "exists": True,
            "required_columns": sorted(required_cols),
            "missing_columns": missing,
            "present_required_count": len(required_cols) - len(missing),
            "required_count": len(required_cols),
        }
        if missing:
            result["missing_columns"][table] = missing

    result["ok"] = not result["missing_tables"] and not result["missing_columns"]
    return result


def insert_trade(data: dict) -> int:
    """Cria registro de trade. Retorna o id gerado."""
    dq = _trade_data_quality(data.get("ts_signal"), data.get("mint", ""))
    with _cursor() as cur:
        cur.execute("""
            INSERT INTO trades
                (mint, symbol, ts_signal, mc_bottom, mc_migration, entry_expected,
                 amount_sol, amount_usd, tp_pct, sl_pct, status,
                 mc_at_signal, bounce_pct_signal, drop_from_mig_signal, signal_elapsed_sec,
                 config_bounce_min_pct, config_bounce_max_pct, config_bounce_drop_min,
                 config_trade_sl_pct, config_trailing_activation_pct,
                 config_trailing_drawdown_pct, config_slippage_bps, git_sha,
                 helius_gate_passed, helius_gate_reason, helius_pool_quote_at_signal,
                 helius_absorption_events, helius_net_sol_flow, helius_rise_events,
                 helius_rise_net_flow, helius_dump_seller_hhi, helius_buy_concentration,
                 helius_h23_window, data_quality)
            VALUES
                (%(mint)s, %(symbol)s, %(ts_signal)s, %(mc_bottom)s, %(mc_migration)s,
                 %(entry_expected)s, %(amount_sol)s, %(amount_usd)s,
                 %(tp_pct)s, %(sl_pct)s, %(status)s,
                 %(mc_at_signal)s, %(bounce_pct_signal)s, %(drop_from_mig_signal)s,
                 %(signal_elapsed_sec)s,
                 %(config_bounce_min_pct)s, %(config_bounce_max_pct)s,
                 %(config_bounce_drop_min)s, %(config_trade_sl_pct)s,
                 %(config_trailing_activation_pct)s, %(config_trailing_drawdown_pct)s,
                 %(config_slippage_bps)s, %(git_sha)s,
                 %(helius_gate_passed)s, %(helius_gate_reason)s,
                 %(helius_pool_quote_at_signal)s, %(helius_absorption_events)s,
                 %(helius_net_sol_flow)s, %(helius_rise_events)s,
                 %(helius_rise_net_flow)s, %(helius_dump_seller_hhi)s,
                 %(helius_buy_concentration)s, %(helius_h23_window)s,
                 %(data_quality)s)
            RETURNING id
        """, {
            **data,
            "helius_gate_passed": data.get("helius_gate_passed"),
            "helius_gate_reason": data.get("helius_gate_reason"),
            "helius_pool_quote_at_signal": data.get("helius_pool_quote_at_signal"),
            "helius_absorption_events": data.get("helius_absorption_events"),
            "helius_net_sol_flow": data.get("helius_net_sol_flow"),
            "helius_rise_events": data.get("helius_rise_events"),
            "helius_rise_net_flow": data.get("helius_rise_net_flow"),
            "helius_dump_seller_hhi": data.get("helius_dump_seller_hhi"),
            "helius_buy_concentration": data.get("helius_buy_concentration"),
            "helius_h23_window": data.get("helius_h23_window"),
            "data_quality": dq,
        })
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
    """Conta trades para calibração. Bloqueios pré-swap não consomem limite."""
    with _cursor() as cur:
        if exclude_errors:
            cur.execute("""
                SELECT COUNT(*) AS n
                  FROM trades
                 WHERE status != 'error'
                   AND (order_id IS NOT NULL OR status IN ('open','tp','sl','trailing_active','expired'))
            """)
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


def get_micro_outcomes_v2_data(days: int = 7) -> tuple:
    """Returns (events_dict, ticks_dict) for /analise/micro-outcomes-v2.

    events_dict: { (mint, ts_min_iso) -> [snapshots sorted by bottom_age_sec ASC] }
    ticks_dict:  { mint -> [ticks sorted by tick_ts ASC] }

    Usa autocommit=True para evitar manter AccessShareLock durante toda a função —
    prevenindo deadlock com init_db() que precisa de AccessExclusiveLock nas mesmas tabelas.
    """
    from collections import defaultdict
    from datetime import timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    conn.autocommit = True  # cada SELECT libera o lock imediatamente após completar
    try:
        with conn.cursor() as cur:
            cur.execute("SET lock_timeout = '2s'")
            cur.execute("SET statement_timeout = '45s'")
            cur.execute("""
                SELECT
                    mint, symbol, ts_min, ts_migration,
                    ts_snapshot, bottom_age_sec, mc_min, mc_current, mc_migration,
                    bounce_pct_now, drop_from_mig, elapsed_sec, pool_quote, candidate_flags,
                    pre_5s_net, pre_10s_net, pre_15s_net, pre_30s_net,
                    pre_5s_buy, pre_10s_buy, pre_30s_buy,
                    pre_5s_sell, pre_10s_sell, pre_30s_sell,
                    pre_5s_events, pre_10s_events, pre_30s_events,
                    pre_5s_largest_buy, pre_10s_largest_buy, pre_30s_largest_buy,
                    buy_concentration_5s, buy_concentration_10s,
                    rise_1s_net, rise_1s_zero_type,
                    rise_3s_net, rise_3s_zero_type,
                    rise_5s_net, rise_5s_zero_type,
                    rise_10s_net, rise_10s_zero_type,
                    rise_now_net, live_5s_net, live_10s_net
                FROM micro_absorption_snapshots
                WHERE ts_snapshot >= %(cutoff)s
                  AND mc_min IS NOT NULL
                  AND ts_min IS NOT NULL
                ORDER BY mint, ts_min, bottom_age_sec
            """, {"cutoff": cutoff})
            all_snaps = [dict(r) for r in cur.fetchall()]

        if not all_snaps:
            return {}, {}

        events: dict = defaultdict(list)
        for s in all_snaps:
            ts = s["ts_min"]
            key = (s["mint"], ts.isoformat() if hasattr(ts, "isoformat") else str(ts))
            events[key].append(s)

        mints = list({s["mint"] for s in all_snaps})
        ts_floor = min(s["ts_min"] for s in all_snaps)
        ts_ceil  = max(s["ts_min"] for s in all_snaps) + timedelta(hours=1, minutes=5)

        with conn.cursor() as cur:
            cur.execute("""
                SELECT mint, tick_ts, mc, mc_open, mc_high, mc_low, source
                FROM token_ticks
                WHERE mint = ANY(%(mints)s)
                  AND tick_ts > %(ts_floor)s
                  AND tick_ts <= %(ts_ceil)s
                ORDER BY mint, tick_ts
            """, {"mints": mints, "ts_floor": ts_floor, "ts_ceil": ts_ceil})
            ticks_by_mint: dict = defaultdict(list)
            for t in cur.fetchall():
                ticks_by_mint[t["mint"]].append(dict(t))

        return dict(events), dict(ticks_by_mint)
    finally:
        conn.close()


# ==============================================================================
# GMGN Trending Radar — research-only, sem execução de trades
# ==============================================================================

def ensure_trending_radar_schema() -> None:
    """Cria tabelas do radar de trending sem depender do init_db completo."""
    with _cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS gmgn_trending_snapshots (
                id                         BIGSERIAL PRIMARY KEY,
                ts_snapshot                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                chain                      TEXT NOT NULL,
                interval_label             TEXT NOT NULL,
                order_by                   TEXT,
                direction                  TEXT,
                filters_json               TEXT,
                platforms_json             TEXT,
                rank_position              INT,
                mint                       TEXT NOT NULL,
                symbol                     TEXT,
                name                       TEXT,
                creator                    TEXT,
                launchpad_platform         TEXT,
                exchange                   TEXT,
                open_timestamp             BIGINT,
                creation_timestamp         BIGINT,
                hot_level                  FLOAT,
                price                      FLOAT,
                market_cap                 FLOAT,
                liquidity                  FLOAT,
                volume                     FLOAT,
                swaps                      INT,
                buys                       INT,
                sells                      INT,
                holder_count               INT,
                smart_degen_count          INT,
                renowned_count             INT,
                bluechip_owner_percentage  FLOAT,
                rug_ratio                  FLOAT,
                is_wash_trading            BOOL,
                bundler_rate               FLOAT,
                rat_trader_amount_rate     FLOAT,
                top10_holder_rate          FLOAT,
                top70_sniper_hold_rate     FLOAT,
                sniper_count               INT,
                bot_degen_rate             FLOAT,
                dexscr_ad                  BOOL,
                dexscr_trending_bar        BOOL,
                dexscr_boost_fee           FLOAT,
                cto_flag                   BOOL,
                raw_json                   TEXT,
                created_at                 TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gmgn_trending_snapshots_mint_ts ON gmgn_trending_snapshots (mint, ts_snapshot)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gmgn_trending_snapshots_query ON gmgn_trending_snapshots (chain, interval_label, ts_snapshot)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gmgn_trending_snapshots_rank ON gmgn_trending_snapshots (chain, interval_label, rank_position)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS gmgn_trending_coverage (
                id                         BIGSERIAL PRIMARY KEY,
                snapshot_id                BIGINT UNIQUE REFERENCES gmgn_trending_snapshots(id) ON DELETE CASCADE,
                mint                       TEXT NOT NULL,
                ts_snapshot                TIMESTAMPTZ NOT NULL,
                scanner_status             TEXT,
                reject_reason              TEXT,
                seen_in_migrations         BOOL DEFAULT FALSE,
                seen_in_bounce_signals     BOOL DEFAULT FALSE,
                seen_in_trades             BOOL DEFAULT FALSE,
                migration_age_sec          INT,
                entry_mc                   FLOAT,
                outcome_window_min         INT,
                take_profit_pct            FLOAT,
                stop_loss_pct              FLOAT,
                max_runup_pct              FLOAT,
                max_drawdown_pct           FLOAT,
                first_tp_ts                TIMESTAMPTZ,
                first_sl_ts                TIMESTAMPTZ,
                shadow_win                 BOOL,
                shadow_outcome             TEXT,
                notes                      TEXT,
                created_at                 TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gmgn_trending_coverage_mint_ts ON gmgn_trending_coverage (mint, ts_snapshot)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gmgn_trending_coverage_status ON gmgn_trending_coverage (scanner_status, reject_reason)")


def insert_trending_snapshots(rows: list[dict]) -> list[int]:
    """Insere snapshot de trending e retorna ids criados."""
    if not rows:
        return []
    import json as _json
    payload = []
    for r in rows:
        payload.append({
            **r,
            "filters_json": _json.dumps(r.get("filters") or [], ensure_ascii=False),
            "platforms_json": _json.dumps(r.get("platforms") or [], ensure_ascii=False),
            "raw_json": _json.dumps(r.get("raw") or {}, ensure_ascii=False, default=str),
        })
    with _cursor() as cur:
        inserted = execute_values(cur, """
            INSERT INTO gmgn_trending_snapshots (
                ts_snapshot, chain, interval_label, order_by, direction,
                filters_json, platforms_json, rank_position, mint, symbol, name,
                creator, launchpad_platform, exchange, open_timestamp, creation_timestamp,
                hot_level, price, market_cap, liquidity, volume, swaps, buys, sells,
                holder_count, smart_degen_count, renowned_count, bluechip_owner_percentage,
                rug_ratio, is_wash_trading, bundler_rate, rat_trader_amount_rate,
                top10_holder_rate, top70_sniper_hold_rate, sniper_count, bot_degen_rate,
                dexscr_ad, dexscr_trending_bar, dexscr_boost_fee, cto_flag, raw_json
            ) VALUES %s
            RETURNING id
        """, payload, template="""(
                %(ts_snapshot)s, %(chain)s, %(interval_label)s, %(order_by)s, %(direction)s,
                %(filters_json)s, %(platforms_json)s, %(rank_position)s, %(mint)s, %(symbol)s, %(name)s,
                %(creator)s, %(launchpad_platform)s, %(exchange)s, %(open_timestamp)s, %(creation_timestamp)s,
                %(hot_level)s, %(price)s, %(market_cap)s, %(liquidity)s, %(volume)s, %(swaps)s, %(buys)s, %(sells)s,
                %(holder_count)s, %(smart_degen_count)s, %(renowned_count)s, %(bluechip_owner_percentage)s,
                %(rug_ratio)s, %(is_wash_trading)s, %(bundler_rate)s, %(rat_trader_amount_rate)s,
                %(top10_holder_rate)s, %(top70_sniper_hold_rate)s, %(sniper_count)s, %(bot_degen_rate)s,
                %(dexscr_ad)s, %(dexscr_trending_bar)s, %(dexscr_boost_fee)s, %(cto_flag)s, %(raw_json)s
            )""", fetch=True)
        return [row["id"] for row in inserted]


def _trending_reject_reason(snapshot: dict, migration: dict | None, cfg: dict) -> str:
    """Inferência leve: por que um trending token pode não ter entrado no funil."""
    if not migration:
        return "not_in_migrations"
    exchange = (migration.get("exchange") or snapshot.get("exchange") or "").lower()
    if cfg.get("pump_only") and "pump" not in exchange:
        return "not_pump_exchange"
    age = migration.get("migration_age_sec")
    if age is not None and age > cfg.get("max_mig_age_sec", 600):
        return "old_migration_at_snapshot"
    mc = migration.get("mc_migration") or snapshot.get("market_cap")
    if not mc:
        return "mc_missing"
    if mc < cfg.get("mc_min", 0):
        return "mc_low"
    if mc > cfg.get("mc_max", 10**12):
        return "mc_high"
    liq = migration.get("liq_migration") or snapshot.get("liquidity") or 0
    if liq < cfg.get("liq_min", 0):
        return "liq_low"
    fee = migration.get("total_fee_sol") or 0
    if fee and fee < cfg.get("fee_min", 0):
        return "fee_low"
    if snapshot.get("is_wash_trading") or migration.get("is_wash_trading"):
        return "wash_trading"
    rug = migration.get("rug_ratio") if migration.get("rug_ratio") is not None else snapshot.get("rug_ratio")
    if rug is not None and rug > cfg.get("rug_max", 1):
        return "rug_high"
    sniper = migration.get("top70_sniper_hold_rate") if migration.get("top70_sniper_hold_rate") is not None else snapshot.get("top70_sniper_hold_rate")
    if sniper is not None and sniper > cfg.get("sniper_max", 1):
        return "sniper_high"
    rat = migration.get("rat_trader_rate") if migration.get("rat_trader_rate") is not None else snapshot.get("rat_trader_amount_rate")
    if rat is not None and rat > cfg.get("rat_max", 1):
        return "rat_high"
    smart = migration.get("smart_wallets") if migration.get("smart_wallets") is not None else snapshot.get("smart_degen_count")
    if (smart or 0) < cfg.get("smart_min", 0):
        return "smart_low"
    return "eligible_or_rejected_after_signal"


def build_trending_coverage(snapshot_ids: list[int], outcome_window_min: int = 60,
                            take_profit_pct: float = 0.75, stop_loss_pct: float = 0.25,
                            cfg: dict | None = None) -> dict:
    """Cria coverage + outcome shadow para snapshots recém-coletados.

    Entrada hipotética: market_cap do snapshot trending. WR shadow: TP é atingido
    antes de SL dentro da janela usando token_ticks disponíveis. Se não houver ticks,
    outcome fica `no_ticks` — isso mede cobertura, não performance.
    """
    if not snapshot_ids:
        return {"snapshots": 0, "with_ticks": 0, "wins": 0, "losses": 0}
    cfg = cfg or {}
    summary = {"snapshots": 0, "with_ticks": 0, "wins": 0, "losses": 0, "ambiguous": 0, "open": 0, "no_ticks": 0, "no_entry_mc": 0}
    with _cursor() as cur:
        cur.execute("""
            SELECT s.*,
                   m.ts_migration, m.mc_migration, m.liq_migration, m.exchange AS migration_exchange,
                   m.total_fee_sol, m.smart_wallets, m.rug_ratio AS migration_rug_ratio,
                   m.top70_sniper_hold_rate AS migration_top70_sniper_hold_rate,
                   m.rat_trader_rate, m.is_wash_trading AS migration_is_wash_trading,
                   EXTRACT(EPOCH FROM (s.ts_snapshot - m.ts_migration))::INT AS migration_age_sec,
                   (b.id IS NOT NULL) AS seen_in_bounce_signals,
                   EXISTS (SELECT 1 FROM trades t WHERE t.mint = s.mint) AS seen_in_trades
              FROM gmgn_trending_snapshots s
              LEFT JOIN migrations m ON m.mint = s.mint
              LEFT JOIN bounce_signals b ON b.mint = s.mint
             WHERE s.id = ANY(%s)
             ORDER BY s.id
        """, (snapshot_ids,))
        snapshots = [dict(r) for r in cur.fetchall()]

        for s in snapshots:
            summary["snapshots"] += 1
            migration = None
            if s.get("ts_migration"):
                migration = {
                    "mc_migration": s.get("mc_migration"),
                    "liq_migration": s.get("liq_migration"),
                    "exchange": s.get("migration_exchange"),
                    "total_fee_sol": s.get("total_fee_sol"),
                    "smart_wallets": s.get("smart_wallets"),
                    "rug_ratio": s.get("migration_rug_ratio"),
                    "top70_sniper_hold_rate": s.get("migration_top70_sniper_hold_rate"),
                    "rat_trader_rate": s.get("rat_trader_rate"),
                    "is_wash_trading": s.get("migration_is_wash_trading"),
                    "migration_age_sec": s.get("migration_age_sec"),
                }
            snapshot_for_reason = {
                "exchange": s.get("exchange"),
                "market_cap": s.get("market_cap"),
                "liquidity": s.get("liquidity"),
                "is_wash_trading": s.get("is_wash_trading"),
                "rug_ratio": s.get("rug_ratio"),
                "top70_sniper_hold_rate": s.get("top70_sniper_hold_rate"),
                "rat_trader_amount_rate": s.get("rat_trader_amount_rate"),
                "smart_degen_count": s.get("smart_degen_count"),
            }
            reject_reason = _trending_reject_reason(snapshot_for_reason, migration, cfg)
            seen_migration = bool(migration)
            seen_bounce = bool(s.get("seen_in_bounce_signals"))
            seen_trade = bool(s.get("seen_in_trades"))
            if seen_trade:
                scanner_status = "traded"
            elif seen_bounce:
                scanner_status = "bounce_signaled"
            elif seen_migration:
                scanner_status = "migrated_only"
            else:
                scanner_status = "not_seen"

            entry_mc = s.get("market_cap")
            first_tp_ts = first_sl_ts = None
            max_runup = max_drawdown = None
            shadow_win = None
            shadow_outcome = "no_entry_mc" if not entry_mc else "no_ticks"
            if not entry_mc:
                summary["no_entry_mc"] += 1
            if entry_mc and entry_mc > 0:
                cur.execute("""
                    SELECT tick_ts, COALESCE(mc_high, mc, mc_open, mc_low) AS mc_high_eff,
                           COALESCE(mc_low, mc, mc_open, mc_high) AS mc_low_eff,
                           COALESCE(mc, mc_high, mc_low, mc_open) AS mc_eff
                      FROM token_ticks
                     WHERE mint = %(mint)s
                       AND tick_ts >= %(ts)s
                       AND tick_ts <= %(ts)s + (%(window)s || ' minutes')::INTERVAL
                     ORDER BY tick_ts ASC
                """, {"mint": s["mint"], "ts": s["ts_snapshot"], "window": outcome_window_min})
                ticks = [dict(r) for r in cur.fetchall()]
                if ticks:
                    summary["with_ticks"] += 1
                    hi = max((r.get("mc_high_eff") or 0) for r in ticks)
                    lo_vals = [r.get("mc_low_eff") for r in ticks if r.get("mc_low_eff")]
                    lo = min(lo_vals) if lo_vals else None
                    max_runup = (hi / entry_mc) - 1 if hi else None
                    max_drawdown = (lo / entry_mc) - 1 if lo else None
                    tp_level = entry_mc * (1 + take_profit_pct)
                    sl_level = entry_mc * (1 - stop_loss_pct)
                    for r in ticks:
                        if first_tp_ts is None and (r.get("mc_high_eff") or 0) >= tp_level:
                            first_tp_ts = r["tick_ts"]
                        if first_sl_ts is None and (r.get("mc_low_eff") or 0) <= sl_level:
                            first_sl_ts = r["tick_ts"]
                        if first_tp_ts is not None or first_sl_ts is not None:
                            break
                    if first_tp_ts and first_sl_ts and first_tp_ts == first_sl_ts:
                        shadow_outcome = "ambiguous_intrabar"
                        summary["ambiguous"] += 1
                    elif first_tp_ts and (not first_sl_ts or first_tp_ts < first_sl_ts):
                        shadow_win = True
                        shadow_outcome = "tp_first"
                        summary["wins"] += 1
                    elif first_sl_ts:
                        shadow_win = False
                        shadow_outcome = "sl_first"
                        summary["losses"] += 1
                    else:
                        shadow_outcome = "open_window"
                        summary["open"] += 1
                else:
                    summary["no_ticks"] += 1

            cur.execute("""
                INSERT INTO gmgn_trending_coverage (
                    snapshot_id, mint, ts_snapshot, scanner_status, reject_reason,
                    seen_in_migrations, seen_in_bounce_signals, seen_in_trades,
                    migration_age_sec, entry_mc, outcome_window_min, take_profit_pct,
                    stop_loss_pct, max_runup_pct, max_drawdown_pct, first_tp_ts,
                    first_sl_ts, shadow_win, shadow_outcome
                ) VALUES (
                    %(snapshot_id)s, %(mint)s, %(ts_snapshot)s, %(scanner_status)s, %(reject_reason)s,
                    %(seen_in_migrations)s, %(seen_in_bounce_signals)s, %(seen_in_trades)s,
                    %(migration_age_sec)s, %(entry_mc)s, %(outcome_window_min)s, %(take_profit_pct)s,
                    %(stop_loss_pct)s, %(max_runup_pct)s, %(max_drawdown_pct)s, %(first_tp_ts)s,
                    %(first_sl_ts)s, %(shadow_win)s, %(shadow_outcome)s
                )
                ON CONFLICT (snapshot_id) DO UPDATE SET
                    scanner_status = EXCLUDED.scanner_status,
                    reject_reason = EXCLUDED.reject_reason,
                    seen_in_migrations = EXCLUDED.seen_in_migrations,
                    seen_in_bounce_signals = EXCLUDED.seen_in_bounce_signals,
                    seen_in_trades = EXCLUDED.seen_in_trades,
                    migration_age_sec = EXCLUDED.migration_age_sec,
                    entry_mc = EXCLUDED.entry_mc,
                    max_runup_pct = EXCLUDED.max_runup_pct,
                    max_drawdown_pct = EXCLUDED.max_drawdown_pct,
                    first_tp_ts = EXCLUDED.first_tp_ts,
                    first_sl_ts = EXCLUDED.first_sl_ts,
                    shadow_win = EXCLUDED.shadow_win,
                    shadow_outcome = EXCLUDED.shadow_outcome
            """, {
                "snapshot_id": s["id"],
                "mint": s["mint"],
                "ts_snapshot": s["ts_snapshot"],
                "scanner_status": scanner_status,
                "reject_reason": reject_reason,
                "seen_in_migrations": seen_migration,
                "seen_in_bounce_signals": seen_bounce,
                "seen_in_trades": seen_trade,
                "migration_age_sec": s.get("migration_age_sec"),
                "entry_mc": entry_mc,
                "outcome_window_min": outcome_window_min,
                "take_profit_pct": take_profit_pct,
                "stop_loss_pct": stop_loss_pct,
                "max_runup_pct": max_runup,
                "max_drawdown_pct": max_drawdown,
                "first_tp_ts": first_tp_ts,
                "first_sl_ts": first_sl_ts,
                "shadow_win": shadow_win,
                "shadow_outcome": shadow_outcome,
            })
    return summary


def get_trending_radar_summary(hours: int = 24) -> dict:
    """Resumo simples para avaliar se há edge suficiente para aprofundar."""
    with _cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) AS snapshots,
                   COUNT(*) FILTER (WHERE c.seen_in_migrations) AS seen_migrations,
                   COUNT(*) FILTER (WHERE c.seen_in_bounce_signals) AS seen_bounces,
                   COUNT(*) FILTER (WHERE c.seen_in_trades) AS seen_trades,
                   COUNT(*) FILTER (WHERE c.shadow_outcome IN ('tp_first','sl_first')) AS resolved,
                   COUNT(*) FILTER (WHERE c.shadow_win IS TRUE) AS wins,
                   COUNT(*) FILTER (WHERE c.shadow_outcome = 'ambiguous_intrabar') AS ambiguous,
                   COUNT(*) FILTER (WHERE c.shadow_outcome = 'no_entry_mc') AS no_entry_mc,
                   AVG(c.max_runup_pct) AS avg_runup,
                   AVG(c.max_drawdown_pct) AS avg_drawdown
              FROM gmgn_trending_snapshots s
              LEFT JOIN gmgn_trending_coverage c ON c.snapshot_id = s.id
             WHERE s.ts_snapshot >= NOW() - (%(hours)s || ' hours')::INTERVAL
        """, {"hours": hours})
        row = dict(cur.fetchone() or {})
        resolved = row.get("resolved") or 0
        wins = row.get("wins") or 0
        row["shadow_wr"] = (wins / resolved) if resolved else None
        cur.execute("""
            SELECT reject_reason, COUNT(*) AS n
              FROM gmgn_trending_coverage
             WHERE ts_snapshot >= NOW() - (%(hours)s || ' hours')::INTERVAL
             GROUP BY reject_reason
             ORDER BY n DESC
             LIMIT 20
        """, {"hours": hours})
        row["reject_reasons"] = [dict(r) for r in cur.fetchall()]
        return row
