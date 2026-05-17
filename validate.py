#!/usr/bin/env python3
"""
NeideBOT — Validação de Invariantes
Rodar após cada sessão de código, antes de qualquer deploy para main.
Usage:
    python validate.py
    python validate.py --check="mc_min fictício"
    python validate.py --since=2026-05-10
"""
import os
import sys
import argparse
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ["DATABASE_URL"]

def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)

# ── helpers ────────────────────────────────────────────────────────────────────

def _since_clause(since_dt, col):
    """Returns a SQL fragment ' AND col >= %(since)s' when since_dt is set."""
    return f" AND {col} >= %(since)s" if since_dt else ""

def _params(since_dt, extra=None):
    p = {}
    if since_dt:
        p["since"] = since_dt
    if extra:
        p.update(extra)
    return p

def _fmt_row(r, fields):
    return " | ".join(f"{f}={r[f]}" for f in fields if f in r)

def _run_check(conn, name, level, query, params, fail_fields, fail_condition_desc, threshold=0):
    """
    Execute query, return (passed, n_fail, examples).
    threshold: number of failing rows above which the check is considered failed.
    """
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    n = len(rows)
    passed = n <= threshold
    examples = [dict(r) for r in rows[:3]]
    return passed, n, examples

# ── check definitions ──────────────────────────────────────────────────────────

CHECKS = []

def check(name, level):
    def decorator(fn):
        CHECKS.append({"name": name, "level": level, "fn": fn})
        return fn
    return decorator


# ═══ CRÍTICOS ═══════════════════════════════════════════════════════════════

@check("mc_min fictício em bounce_signals", "CRÍTICO")
def check_mcmin_ficticio(conn, since_dt):
    q = """
        SELECT mint, mc_migration, mc_min,
               ROUND((mc_min / NULLIF(mc_migration,0))::numeric, 3) AS ratio,
               ts_signal
        FROM bounce_signals
        WHERE mc_min < mc_migration * 0.10
          AND mc_migration > 0
    """ + _since_clause(since_dt, "ts_signal")
    with conn.cursor() as cur:
        cur.execute(q, _params(since_dt))
        rows = cur.fetchall()
    n = len(rows)
    passed = n == 0
    examples = [_fmt_row(r, ["mint","mc_migration","mc_min","ratio","ts_signal"]) for r in rows[:3]]
    desc = "mc_min < mc_migration × 0.10 (dados de bonding curve pré-migração contaminando pipeline)"
    return passed, n, examples, desc


@check("mc_min menor que mínimo real em token_ticks", "CRÍTICO")
def check_mcmin_vs_ticks(conn, since_dt):
    # Para cada mint em bounce_signals, verifica se mc_min do bounce é ≥ MIN(ticks.mc) - 5%
    # Mostra discrepâncias > 20%
    since_filter = _since_clause(since_dt, "bs.ts_signal")
    q = f"""
        SELECT bs.mint,
               bs.mc_min                              AS bs_mc_min,
               MIN(tt.mc)                             AS tick_mc_min,
               ROUND((MIN(tt.mc) / NULLIF(bs.mc_min,0))::numeric, 3) AS ratio,
               bs.ts_signal
        FROM bounce_signals bs
        JOIN token_ticks tt ON tt.mint = bs.mint
        WHERE bs.mc_min > 0
          {since_filter.replace('AND', 'AND bs.', 1) if since_dt else ''}
        GROUP BY bs.mint, bs.mc_min, bs.ts_signal
        HAVING MIN(tt.mc) < bs.mc_min * 0.80
    """
    # rewrite since filter cleanly
    q2 = """
        SELECT bs.mint,
               bs.mc_min                              AS bs_mc_min,
               MIN(tt.mc)                             AS tick_mc_min,
               ROUND((MIN(tt.mc) / NULLIF(bs.mc_min,0))::numeric, 3) AS ratio,
               bs.ts_signal
        FROM bounce_signals bs
        JOIN token_ticks tt ON tt.mint = bs.mint
        WHERE bs.mc_min > 0
    """ + (_since_clause(since_dt, "bs.ts_signal")) + """
        GROUP BY bs.mint, bs.mc_min, bs.ts_signal
        HAVING MIN(tt.mc) < bs.mc_min * 0.80
    """
    with conn.cursor() as cur:
        cur.execute(q2, _params(since_dt))
        rows = cur.fetchall()
    n = len(rows)
    passed = n == 0
    examples = [_fmt_row(r, ["mint","bs_mc_min","tick_mc_min","ratio","ts_signal"]) for r in rows[:3]]
    desc = "tick_mc_min < bs_mc_min × 0.80 — o fundo real é 20%+ menor que o registrado no sinal"
    return passed, n, examples, desc


@check("token_ticks com elapsed_sec < 90", "CRÍTICO")
def check_ticks_elapsed(conn, since_dt):
    # elapsed_sec < 90 AND >= 0 são ticks de transição bonding curve
    since_join = ""
    if since_dt:
        since_join = """
            JOIN migrations m ON m.mint = tt.mint
            WHERE tt.elapsed_sec < 90 AND tt.elapsed_sec >= 0
              AND m.ts_migration >= %(since)s
        """
    else:
        since_join = "WHERE tt.elapsed_sec < 90 AND tt.elapsed_sec >= 0"

    q = f"""
        SELECT tt.mint, tt.elapsed_sec, tt.mc, tt.source
        FROM token_ticks tt
        {since_join}
        LIMIT 100
    """
    with conn.cursor() as cur:
        cur.execute(q, _params(since_dt))
        rows = cur.fetchall()
    n = len(rows)
    passed = n == 0
    examples = [_fmt_row(r, ["mint","elapsed_sec","mc","source"]) for r in rows[:3]]
    desc = "ticks com elapsed_sec < 90 contêm preços da bonding curve pré-migração (corrompem mc_min)"
    return passed, n, examples, desc


@check("ts_migration inconsistente com complete_timestamp", "CRÍTICO")
def check_ts_migration(conn, since_dt):
    q = """
        SELECT mint,
               ts_migration,
               complete_timestamp,
               ABS(EXTRACT(epoch FROM ts_migration) - complete_timestamp)::int AS diff_sec
        FROM migrations
        WHERE complete_timestamp IS NOT NULL
          AND ABS(EXTRACT(epoch FROM ts_migration) - complete_timestamp) > 30
    """ + _since_clause(since_dt, "ts_migration")
    with conn.cursor() as cur:
        cur.execute(q, _params(since_dt))
        rows = cur.fetchall()
    n = len(rows)
    passed = n == 0
    examples = [_fmt_row(r, ["mint","ts_migration","complete_timestamp","diff_sec"]) for r in rows[:3]]
    desc = "ts_migration gravado como datetime.now() em vez do timestamp on-chain (distorce elapsed_sec)"
    return passed, n, examples, desc


@check("capture_delay_sec negativo", "CRÍTICO")
def check_capture_delay(conn, since_dt):
    q = """
        SELECT mint, capture_delay_sec, ts_migration, complete_timestamp
        FROM migrations
        WHERE capture_delay_sec < 0
    """ + _since_clause(since_dt, "ts_migration")
    with conn.cursor() as cur:
        cur.execute(q, _params(since_dt))
        rows = cur.fetchall()
    n = len(rows)
    passed = n == 0
    examples = [_fmt_row(r, ["mint","capture_delay_sec","ts_migration","complete_timestamp"]) for r in rows[:3]]
    desc = "capture_delay_sec < 0 é impossível (bot detectou antes da migração on-chain)"
    return passed, n, examples, desc


# ═══ ALTOS ═══════════════════════════════════════════════════════════════════

@check("bounce_pct negativo em bounce_signals", "ALTO")
def check_bounce_pct(conn, since_dt):
    q = """
        SELECT mint, bounce_pct, mc_at_signal, mc_min, ts_signal
        FROM bounce_signals
        WHERE bounce_pct < 0 OR mc_at_signal <= mc_min
    """ + _since_clause(since_dt, "ts_signal")
    with conn.cursor() as cur:
        cur.execute(q, _params(since_dt))
        rows = cur.fetchall()
    n = len(rows)
    passed = n == 0
    examples = [_fmt_row(r, ["mint","bounce_pct","mc_at_signal","mc_min","ts_signal"]) for r in rows[:3]]
    desc = "bounce_pct < 0 ou mc_at_signal ≤ mc_min — sinal disparou com preço abaixo do fundo"
    return passed, n, examples, desc


@check("drop_from_mig negativo em bounce_signals", "ALTO")
def check_drop_from_mig(conn, since_dt):
    q = """
        SELECT mint, drop_from_mig, mc_min, mc_migration, ts_signal
        FROM bounce_signals
        WHERE drop_from_mig < 0 OR mc_min > mc_migration
    """ + _since_clause(since_dt, "ts_signal")
    with conn.cursor() as cur:
        cur.execute(q, _params(since_dt))
        rows = cur.fetchall()
    n = len(rows)
    passed = n == 0
    examples = [_fmt_row(r, ["mint","drop_from_mig","mc_min","mc_migration","ts_signal"]) for r in rows[:3]]
    desc = "drop_from_mig < 0 ou mc_min > mc_migration — fundo acima da migração, nunca houve dump"
    return passed, n, examples, desc


@check("elapsed_sec negativo em bounce_signals", "ALTO")
def check_elapsed_neg(conn, since_dt):
    q = """
        SELECT mint, elapsed_sec, ts_signal
        FROM bounce_signals
        WHERE elapsed_sec < 0
    """ + _since_clause(since_dt, "ts_signal")
    with conn.cursor() as cur:
        cur.execute(q, _params(since_dt))
        rows = cur.fetchall()
    n = len(rows)
    passed = n == 0
    examples = [_fmt_row(r, ["mint","elapsed_sec","ts_signal"]) for r in rows[:3]]
    desc = "elapsed_sec < 0 — bounce registrado antes da migração (dado corrompido)"
    return passed, n, examples, desc


@check("trades duplicados por mint em intervalo curto", "ALTO")
def check_dup_trades(conn, since_dt):
    q = """
        SELECT a.mint,
               a.id AS id_a, a.ts_signal AS ts_a,
               b.id AS id_b, b.ts_signal AS ts_b,
               ABS(EXTRACT(epoch FROM (a.ts_signal - b.ts_signal)))::int AS diff_sec
        FROM trades a
        JOIN trades b ON b.mint = a.mint AND b.id > a.id
        WHERE ABS(EXTRACT(epoch FROM (a.ts_signal - b.ts_signal))) < 60
    """ + _since_clause(since_dt, "a.ts_signal")
    with conn.cursor() as cur:
        cur.execute(q, _params(since_dt))
        rows = cur.fetchall()
    n = len(rows)
    passed = n == 0
    examples = [_fmt_row(r, ["mint","id_a","ts_a","id_b","ts_b","diff_sec"]) for r in rows[:3]]
    desc = "dois trades com mesmo mint em < 60s — bounce_fired disparou duas vezes (race condition)"
    return passed, n, examples, desc


@check("mc_migration fora do range plausível", "ALTO")
def check_mc_migration_range(conn, since_dt):
    q = """
        SELECT mint, mc_migration, mc_migration_source, ts_migration
        FROM migrations
        WHERE mc_migration IS NOT NULL
          AND (mc_migration < 5000 OR mc_migration > 500000)
    """ + _since_clause(since_dt, "ts_migration")
    with conn.cursor() as cur:
        cur.execute(q, _params(since_dt))
        rows = cur.fetchall()
    n = len(rows)
    passed = n == 0
    examples = [_fmt_row(r, ["mint","mc_migration","mc_migration_source","ts_migration"]) for r in rows[:3]]
    desc = "mc_migration < $5k ou > $500k — improvável para 410 SOL × SOL/USD (esperado $30k-$150k)"
    return passed, n, examples, desc


# ═══ MÉDIOS (alertas) ════════════════════════════════════════════════════════

@check("mc_peak inflado por spike intra-candle", "MÉDIO")
def check_mcpeak_spike(conn, since_dt):
    q = """
        SELECT mint, mc_migration, mc_peak,
               ROUND((mc_peak / NULLIF(mc_migration,0))::numeric, 1) AS ratio,
               ts_migration
        FROM migrations
        WHERE mc_peak > mc_migration * 50
          AND mc_migration > 0
    """ + _since_clause(since_dt, "ts_migration")
    with conn.cursor() as cur:
        cur.execute(q, _params(since_dt))
        rows = cur.fetchall()
    n = len(rows)
    passed = n == 0
    examples = [_fmt_row(r, ["mint","mc_migration","mc_peak","ratio","ts_migration"]) for r in rows[:3]]
    desc = "mc_peak > 50× mc_migration — quase certamente spike intra-candle do pAMM, não price action real"
    return passed, n, examples, desc


@check("trades com pnl_pct_real improvável", "MÉDIO")
def check_pnl_range(conn, since_dt):
    q = """
        SELECT id, mint, status, pnl_pct_real, exit_type
        FROM trades
        WHERE pnl_pct_real IS NOT NULL
          AND (pnl_pct_real > 10.0 OR pnl_pct_real < -1.0)
    """ + _since_clause(since_dt, "ts_signal")
    with conn.cursor() as cur:
        cur.execute(q, _params(since_dt))
        rows = cur.fetchall()
    n = len(rows)
    passed = n == 0
    examples = [_fmt_row(r, ["id","mint","status","pnl_pct_real","exit_type"]) for r in rows[:3]]
    desc = "pnl_pct_real > 10 (>1000% ganho) ou < -1.0 (>100% perda) — aritmética impossível ou dado mal gravado"
    return passed, n, examples, desc


@check("trades 'open' muito antigos", "MÉDIO")
def check_open_old(conn, since_dt):
    q = """
        SELECT id, mint, status, ts_signal,
               ROUND(EXTRACT(epoch FROM (NOW() - ts_signal)) / 3600, 1) AS age_hours
        FROM trades
        WHERE status = 'open'
          AND ts_signal < NOW() - INTERVAL '4 hours'
    """
    with conn.cursor() as cur:
        cur.execute(q)
        rows = cur.fetchall()
    n = len(rows)
    passed = n == 0
    examples = [_fmt_row(r, ["id","mint","status","ts_signal","age_hours"]) for r in rows[:3]]
    desc = "trade com status='open' há > 4h — provavelmente fechamento não detectado pelo TradeMonitor"
    return passed, n, examples, desc


@check("h23_window ausente em bounces recentes", "MÉDIO")
def check_h23_window(conn, since_dt):
    q = """
        SELECT mint, ts_signal, h23_window
        FROM bounce_signals
        WHERE h23_window IS NULL
          AND ts_signal > NOW() - INTERVAL '7 days'
    """
    with conn.cursor() as cur:
        cur.execute(q)
        rows = cur.fetchall()
    n = len(rows)
    passed = n == 0
    examples = [_fmt_row(r, ["mint","ts_signal","h23_window"]) for r in rows[:3]]
    desc = "h23_window IS NULL em bounce recente (< 7 dias) — deve ser 'bottom' para janela correta"
    return passed, n, examples, desc


# ── runner ─────────────────────────────────────────────────────────────────────

LEVEL_ORDER = {"CRÍTICO": 0, "ALTO": 1, "MÉDIO": 2}
LEVEL_COLOR = {"CRÍTICO": "🔴", "ALTO": "🟠", "MÉDIO": "🟡"}

def main():
    parser = argparse.ArgumentParser(description="NeideBOT — Validação de Invariantes")
    parser.add_argument("--check", metavar="NOME",
                        help="Rodar apenas o check com este nome (substring match)")
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="Limitar checks a dados após esta data")
    args = parser.parse_args()

    since_dt = None
    if args.since:
        since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print("=" * 60)
    print("NeideBOT — Validação de Invariantes")
    print(now_str)
    if since_dt:
        print(f"Filtrando dados desde: {args.since}")
    print("=" * 60)

    checks_to_run = CHECKS
    if args.check:
        checks_to_run = [c for c in CHECKS if args.check.lower() in c["name"].lower()]
        if not checks_to_run:
            print(f"Nenhum check encontrado com nome '{args.check}'")
            sys.exit(1)

    # sort by severity
    checks_to_run = sorted(checks_to_run, key=lambda c: LEVEL_ORDER[c["level"]])

    n_pass = 0
    n_fail = 0
    n_alert = 0

    try:
        conn = get_conn()
    except Exception as e:
        print(f"\n❌ Não foi possível conectar ao banco: {e}")
        sys.exit(1)

    with conn:
        for chk in checks_to_run:
            name   = chk["name"]
            level  = chk["level"]
            icon   = LEVEL_COLOR[level]
            print(f"\n[{level}] {name}")
            try:
                passed, n, examples, desc = chk["fn"](conn, since_dt)
            except psycopg2.errors.UndefinedColumn as e:
                print(f"  ⚠️  COLUNA AUSENTE — {e.pgerror.strip()}")
                print(f"  (check ignorado — coluna pode não existir nesta versão do schema)")
                continue
            except Exception as e:
                print(f"  ⚠️  ERRO AO EXECUTAR CHECK: {e}")
                continue

            if level == "MÉDIO":
                if passed:
                    print(f"  ✅ OK (n=0)")
                else:
                    n_alert += 1
                    print(f"  ⚠️  ALERTA (n={n}) — {desc}")
                    for ex in examples:
                        print(f"    {ex}")
                    if n > 3:
                        print(f"    ... (e mais {n - 3})")
            else:
                if passed:
                    # count total rows to show in OK message
                    print(f"  ✅ OK (n={n}, nenhuma violação)")
                    n_pass += 1
                else:
                    n_fail += 1
                    print(f"  ❌ FALHA (n={n}) — {desc}")
                    for ex in examples:
                        print(f"    {ex}")
                    if n > 3:
                        print(f"    ... (e mais {n - 3})")

    conn.close()

    total_hard = sum(1 for c in checks_to_run if c["level"] != "MÉDIO")
    print("\n" + "=" * 60)
    if n_fail == 0:
        print(f"RESULTADO: {n_pass}/{total_hard} checks passaram | {n_alert} alerta(s)")
        print("=" * 60)
        print("Exit 0 — OK para deploy")
        sys.exit(0)
    else:
        print(f"RESULTADO: {n_pass}/{total_hard} checks passaram | {n_fail} FALHA(S) | {n_alert} alerta(s)")
        print("=" * 60)
        print("Exit 1 — corrigir falhas antes de fazer deploy")
        sys.exit(1)


if __name__ == "__main__":
    main()
