import os
import logging
import statistics
from datetime import timezone, timedelta

import csv
import io
from flask import Flask, render_template_string, redirect, Response

import db
from monitoring import init_sentry
from gmgn_client import GmgnClient
from scheduler import HeapScheduler
from scanner import Scanner, HotMonitor
from executor import TradeExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

init_sentry()

app = Flask(__name__)
BRT = timedelta(hours=-3)


# ==============================================================================
# Helpers de formatação
# ==============================================================================

def fmt_mc(v):
    if v is None: return "—"
    if v >= 1_000_000: return f"${v/1_000_000:.1f}M"
    if v >= 1_000:     return f"${v/1_000:.0f}K"
    return f"${v:.0f}"

def fmt_pct(v):
    if v is None: return "—"
    return f"{v*100:+.1f}%"

def fmt_ratio(v):
    if v is None: return "—"
    return f"{v:.0%}"


# ==============================================================================
# Dashboard HTML
# ==============================================================================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="15">
<title>NeideBOT — Scanner</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Courier New', monospace; background: #0d1117; color: #c9d1d9;
         padding: 16px; font-size: 13px; }
  h1   { color: #58a6ff; margin-bottom: 4px; }
  .sub { color: #8b949e; margin-bottom: 20px; font-size: 12px; }
  .stats { display: flex; gap: 16px; margin-bottom: 20px; flex-wrap: wrap; }
  .stat  { background: #161b22; border: 1px solid #30363d; border-radius: 6px;
           padding: 10px 18px; }
  .stat-label { color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing:.05em; }
  .stat-value { font-size: 22px; font-weight: bold; margin-top: 2px; }
  .green { color: #3fb950; } .red { color: #f85149; }
  .yellow { color: #d29922; } .muted { color: #8b949e; }
  .wrap { overflow-x: auto; }
  table { border-collapse: collapse; min-width: 100%; white-space: nowrap; }
  th { background: #161b22; color: #8b949e; padding: 8px 10px; text-align: left;
       border-bottom: 2px solid #30363d; position: sticky; top: 0;
       font-size: 11px; text-transform: uppercase; letter-spacing:.05em; }
  td { padding: 6px 10px; border-bottom: 1px solid #21262d; }
  tr:hover td { background: #161b22; }
  .badge { border-radius: 4px; padding: 2px 7px; font-size: 11px; font-weight: bold; }
  .b-green  { background: #1a3a2a; color: #3fb950; }
  .b-red    { background: #3a1a1a; color: #f85149; }
  .b-yellow { background: #3a2a1a; color: #d29922; }
  .mono { font-size: 11px; color: #8b949e; }
  .links { display: flex; gap: 6px; margin-top: 3px; }
  .links a { font-size: 10px; color: #58a6ff; text-decoration: none; opacity: 0.7; }
  .links a:hover { opacity: 1; text-decoration: underline; }
</style>
</head>
<body>
<h1>NeideBOT — Scanner de Migrações</h1>
<p class="sub">Fase 1 — Coleta pura • poll 5s • kline 1m • horário BRT • atualiza a cada 15s</p>

<div class="stats">
  <div class="stat"><div class="stat-label">Observadas</div>
    <div class="stat-value">{{ total }}</div></div>
  <div class="stat"><div class="stat-label">Recovery</div>
    <div class="stat-value green">{{ recovered }}</div></div>
  <div class="stat"><div class="stat-label">Sem recovery</div>
    <div class="stat-value red">{{ not_recovered }}</div></div>
  <div class="stat"><div class="stat-label">Pendentes</div>
    <div class="stat-value yellow">{{ pending }}</div></div>
</div>

<div class="wrap">
<table>
<thead><tr>
  <th>Data BRT</th><th>Token</th><th>BC%</th><th>T/Migração</th>
  <th>MC Mig</th><th>Pico</th><th>Ganho</th><th>T/Pico</th>
  <th>Fundo</th><th>Queda</th><th>T/Fundo</th>
  <th>1h</th><th>4h</th><th>24h</th>
  <th>Smart</th><th>Sniper</th><th>Rug%</th><th>Top10</th>
  <th>Wash</th><th>HP</th><th>Recovery</th>
</tr></thead>
<tbody>
{% for r in rows %}
<tr>
  <td class="muted">{{ r.dt }}</td>
  <td>
    <strong>{{ r.nome }}</strong>
    <div class="mono">{{ r.mint_short }}</div>
    <div class="links">
      <a href="https://gmgn.ai/sol/token/{{ r.mint }}" target="_blank">GMGN</a>
      <a href="https://dexscreener.com/solana/{{ r.mint }}" target="_blank">DEX</a>
      <a href="https://solscan.io/token/{{ r.mint }}" target="_blank">Scan</a>
    </div>
  </td>
  <td class="muted">{{ r.bc }}</td>
  <td class="muted">{{ r.t_migrate }}</td>
  <td>{{ r.mc_mig }}</td>
  <td class="{{ 'green' if r.gain_raw and r.gain_raw > 0 else 'muted' }}">{{ r.mc_peak }}</td>
  <td class="{{ 'green' if r.gain_raw and r.gain_raw > 0.1 else 'muted' }}">{{ r.gain }}</td>
  <td class="muted">{{ r.ttp }}</td>
  <td>{{ r.mc_bot }}</td>
  <td class="{{ 'red' if r.drop_raw and r.drop_raw > 0.2 else 'muted' }}">{{ r.drop }}</td>
  <td class="muted">{{ r.ttb }}</td>
  <td class="{{ 'green' if r.v1_raw and r.v1_raw > 0 else 'red' if r.v1_raw and r.v1_raw < 0 else 'muted' }}">{{ r.v1 }}</td>
  <td class="{{ 'green' if r.v4_raw and r.v4_raw > 0 else 'red' if r.v4_raw and r.v4_raw < 0 else 'muted' }}">{{ r.v4 }}</td>
  <td class="{{ 'green' if r.v24_raw and r.v24_raw > 0 else 'red' if r.v24_raw and r.v24_raw < 0 else 'muted' }}">{{ r.v24 }}</td>
  <td class="{{ 'green' if r.smart_raw and r.smart_raw >= 3 else '' }}">{{ r.smart }}</td>
  <td class="{{ 'red' if r.sniper_raw and r.sniper_raw >= 3 else '' }}">{{ r.sniper }}</td>
  <td class="{{ 'red' if r.rug_raw and r.rug_raw > 0.3 else '' }}">{{ r.rug }}</td>
  <td class="{{ 'red' if r.top10_raw and r.top10_raw > 0.8 else '' }}">{{ r.top10 }}</td>
  <td class="{{ 'red' if r.wash else '' }}">{{ r.wash_lbl }}</td>
  <td>{{ r.hp }}</td>
  <td>
    {% if r.rec == True %}<span class="badge b-green">✅ SIM</span>
    {% elif r.rec == False %}<span class="badge b-red">❌ NÃO</span>
    {% else %}<span class="badge b-yellow">⏳ —</span>{% endif %}
  </td>
</tr>
{% endfor %}
</tbody>
</table>
</div>
</body>
</html>
"""


@app.route("/")
def index():
    return redirect("/scanner")


@app.route("/scanner")
def scanner_dashboard():
    rows_raw = db.get_all_migrations()

    total        = len(rows_raw)
    recovered    = sum(1 for r in rows_raw if r.get("recovery") is True)
    not_recovered = sum(1 for r in rows_raw if r.get("recovery") is False)
    pending      = total - recovered - not_recovered

    rows = []
    for r in rows_raw:
        ts = r.get("ts_migration") or r.get("ts_detected")
        try:
            dt = ts.astimezone(timezone(BRT)).strftime("%d/%m %H:%M") if ts else "—"
        except Exception:
            dt = "—"

        pct_drop = r.get("pct_drop")
        pct_gain = r.get("pct_gain")
        v1  = r.get("var_1h")
        v4  = r.get("var_4h")
        v24 = r.get("var_24h")
        rug   = r.get("rug_ratio")
        top10 = r.get("top10_holder_rate")
        ttb   = r.get("time_to_bottom")
        ttp   = r.get("time_to_peak")
        ttm   = r.get("time_to_migrate_sec")
        wash  = r.get("is_wash_trading")

        rows.append({
            "dt":         dt,
            "nome":       r.get("token_nome") or "?",
            "mint":       r.get("mint") or "",
            "mint_short": (r.get("mint") or "")[:12] + "…",
            "bc":         f"{r.get('bc_progress_at_detection'):.0f}%" if r.get("bc_progress_at_detection") else "—",
            "t_migrate":  (f"{ttm//60}min" if ttm and ttm < 3600 else f"{ttm//3600}h{(ttm%3600)//60}min" if ttm else "—"),
            "mc_mig":     fmt_mc(r.get("mc_migration")),
            "mc_peak":    fmt_mc(r.get("mc_peak")),
            "gain":       fmt_pct(pct_gain), "gain_raw": pct_gain,
            "ttp":        f"{ttp:.1f}min" if ttp is not None else "—",
            "mc_bot":     fmt_mc(r.get("mc_bottom")),
            "drop":       fmt_pct(pct_drop), "drop_raw": pct_drop,
            "ttb":        f"{ttb:.1f}min" if ttb is not None else "—",
            "v1":         fmt_pct(v1),  "v1_raw":  v1,
            "v4":         fmt_pct(v4),  "v4_raw":  v4,
            "v24":        fmt_pct(v24), "v24_raw": v24,
            "smart":      str(r.get("smart_wallets")) if r.get("smart_wallets") is not None else "—",
            "smart_raw":  r.get("smart_wallets"),
            "sniper":     str(r.get("sniper_wallets")) if r.get("sniper_wallets") is not None else "—",
            "sniper_raw": r.get("sniper_wallets"),
            "rug":        fmt_ratio(rug),   "rug_raw":   rug,
            "top10":      fmt_ratio(top10), "top10_raw": top10,
            "wash":       wash,
            "wash_lbl":   "⚠️" if wash else ("✅" if wash is False else "—"),
            "hp":         "\U0001f6ab" if r.get("is_honeypot") else ("✅" if r.get("is_honeypot") is False else "—"),
            "rec":        r.get("recovery"),
        })

    return render_template_string(
        DASHBOARD_HTML, rows=rows, total=total,
        recovered=recovered, not_recovered=not_recovered, pending=pending,
    )


@app.route("/export/csv")
def export_csv():
    rows = db.get_all_migrations(limit=10000)
    if not rows:
        return "Sem dados", 404
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=migrations.csv"},
    )


@app.route("/export/ticks")
def export_ticks():
    """Exporta token_ticks com join de migrations (mc_migration, recovery, symbol)."""
    with db._cursor() as cur:
        cur.execute("""
            SELECT
                t.mint,
                m.token_nome      AS symbol,
                m.mc_migration,
                m.mc_bottom,
                m.mc_peak,
                m.time_to_bottom,
                m.recovery,
                t.ts,
                t.mc,
                t.mc_open,
                t.mc_high,
                t.mc_low,
                t.volume_1m,
                t.smart_count,
                t.renowned_count,
                t.holders,
                t.source,
                EXTRACT(EPOCH FROM (t.ts - m.ts_migration)) AS sec_since_migration
            FROM token_ticks t
            JOIN migrations m ON m.mint = t.mint
            ORDER BY t.mint, t.ts
        """)
        rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return "Sem ticks", 404
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=token_ticks.csv"},
    )


@app.route("/export/bounces")
def export_bounces():
    """Exporta bounce_signals com todos os campos de qualidade da migração."""
    with db._cursor() as cur:
        cur.execute("""
            SELECT
                b.mint,
                b.symbol,
                b.ts_signal,
                b.mc_bottom,
                b.mc_migration,
                b.elapsed_sec,
                b.elapsed_sec / 60.0          AS elapsed_min,
                (b.mc_migration - b.mc_bottom)
                  / NULLIF(b.mc_migration,0)   AS pct_drop,
                m.mc_peak,
                m.mc_1h,
                m.time_to_bottom,
                m.time_to_peak,
                m.recovery,
                CASE
                  WHEN m.mc_1h IS NOT NULL AND b.mc_bottom IS NOT NULL
                  THEN (m.mc_1h - b.mc_bottom) / NULLIF(b.mc_bottom,0)
                END                            AS gain_at_1h,
                m.smart_wallets,
                m.renowned_wallets,
                m.sniper_wallets,
                m.rug_ratio                    AS bundler_rate,
                m.top10_holder_rate,
                m.bot_degen_rate,
                m.top70_sniper_hold_rate,
                m.rat_trader_rate,
                m.is_wash_trading,
                m.creator_token_status,
                m.creator_balance_rate,
                m.entrapment_ratio,
                m.liq_migration,
                m.exchange
            FROM bounce_signals b
            LEFT JOIN migrations m ON m.mint = b.mint
            ORDER BY b.ts_signal DESC
        """)
        rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return "Sem bounces", 404
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=bounces.csv"},
    )


@app.route("/export/bounce-ticks")
@app.route("/export/bounce_ticks")
def export_bounce_ticks():
    """Exporta ticks dos tokens que tiveram bounce_signal — ideal para estudar padrão pós-bounce."""
    with db._cursor() as cur:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'token_ticks'
        """)
        tick_cols = {r["column_name"] for r in cur.fetchall()}
        ts_expr = "t.tick_ts" if "tick_ts" in tick_cols else "t.ts"

        cur.execute(f"""
            SELECT
                t.mint,
                m.token_nome                    AS symbol,
                b.ts_signal                     AS ts_bounce,
                b.mc_min                        AS mc_bottom,
                b.mc_migration,
                b.elapsed_sec / 60.0            AS elapsed_min_at_bounce,
                b.drop_from_mig                 AS pct_drop,
                m.recovery,
                {ts_expr}                       AS ts,
                EXTRACT(EPOCH FROM ({ts_expr} - b.ts_signal)) AS sec_after_bounce,
                t.mc,
                t.mc_open,
                t.mc_high,
                t.mc_low,
                t.volume_1m,
                t.source,
                (t.mc - b.mc_min) / NULLIF(b.mc_min,0) AS gain_vs_bottom,
                (t.mc - b.mc_min * 1.15) / NULLIF(b.mc_min * 1.15,0) AS gain_vs_entry
            FROM token_ticks t
            JOIN bounce_signals b ON b.mint = t.mint
            JOIN migrations m ON m.mint = t.mint
            WHERE {ts_expr} >= b.ts_signal - INTERVAL '2 minutes'
            ORDER BY t.mint, {ts_expr}
        """)
        rows = [dict(r) for r in cur.fetchall()]

    output = io.StringIO()
    if not rows:
        writer = csv.writer(output)
        writer.writerow(["message"])
        writer.writerow(["Sem ticks de bounces"])
    else:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=bounce_ticks.csv"},
    )


@app.route("/health")
def health():
    return {"status": "ok"}, 200


_signal_ban_until: float = 0.0  # timestamp UTC até quando a conta está banida

@app.route("/debug/signal")
def debug_signal():
    """
    Rota de diagnóstico: testa formatos diferentes do signal type 12
    num único token para descobrir o body correto sem estourar rate limit.
    """
    import time as _time
    from uuid import uuid4 as _uuid4

    global _signal_ban_until

    # Não chama a API enquanto o ban não expirou
    now = _time.time()
    if now < _signal_ban_until:
        wait = int(_signal_ban_until - now)
        return {
            "error": "conta banida",
            "reset_at": int(_signal_ban_until),
            "aguarde_segundos": wait,
            "mensagem": f"Aguarde {wait}s antes de chamar novamente",
        }, 429

    if not gmgn:
        return {"error": "GmgnClient não inicializado"}, 503

    with db._cursor() as cur:
        cur.execute("""
            SELECT mint, token_nome, mc_migration, mc_bottom, mc_1h, recovery
            FROM migrations
            WHERE mc_bottom IS NOT NULL AND ts_migration IS NOT NULL
            ORDER BY ts_migration DESC LIMIT 1
        """)
        tok = dict(cur.fetchone())

    mint = tok["mint"]

    # groups é array de OBJETOS (TokenSignalGroup), não de strings
    # Fonte: github.com/GMGNAI/gmgn-skills src/client/OpenApiClient.ts
    # O endpoint é feed global, sem token_address
    candidates = [
        ({"chain": "sol", "groups": [{"signal_type": [12]}]},                                    {}),
        ({"chain": "sol", "groups": [{"signal_type": [12], "mc_min": 5000, "mc_max": 200000}]},  {}),
        ({"chain": "sol", "groups": [{"signal_type": [12, 13]}]},                                {}),
        ({"chain": "sol", "groups": [{}]},                                                        {}),
    ]

    results = []
    resp = None
    for body, extra_params in candidates:
        _time.sleep(3)
        base_params = {"timestamp": str(int(_time.time())), "client_id": str(_uuid4())}
        base_params.update(extra_params)
        try:
            resp = gmgn.session.post(
                f"{gmgn.host}/v1/market/token_signal",
                json=body,
                params=base_params,
                timeout=10,
            )
            raw = resp.json()
        except Exception as e:
            raw = {"error": str(e)}
            results.append({"body_enviado": body, "params_extras": extra_params, "resposta": raw})
            break

        # Registra ban para evitar chamadas futuras que estendem o ban
        if resp.status_code == 429:
            try:
                reset_at = float(raw.get("reset_at", _time.time() + 300))
            except Exception:
                reset_at = _time.time() + 300
            _signal_ban_until = reset_at
            results.append({"body_enviado": body, "params_extras": extra_params, "resposta": raw})
            break

        results.append({"body_enviado": body, "params_extras": extra_params, "resposta": raw})
        if resp.status_code != 400:
            break

    return {"mint": mint, "token": tok["token_nome"], "testes": results}, 200


@app.route("/debug/signal-live")
def debug_signal_live():
    """
    Testa get_token_signal() diretamente e mostra:
    - quantos entries retornaram no feed global
    - quais fazem match com tokens no HotMonitor
    - últimos 5 sinais salvos no banco
    """
    import time as _time
    if not gmgn:
        return {"error": "GmgnClient não inicializado"}, 503

    # Chama o método novo
    try:
        entries = gmgn.get_token_signal()
    except Exception as e:
        return {"error": str(e)}, 500

    # Tokens ativos no HotMonitor
    with scanner.hot._lock if scanner else _DummyCtx():
        active_mints = set(scanner.hot._tokens.keys()) if scanner else set()

    matches = [e for e in entries if e.get("token_address") in active_mints]

    # Últimos 5 sinais no banco
    try:
        with db._cursor() as cur:
            cur.execute("""
                SELECT mint, trigger_at, trigger_mc, signal_times, ts_recorded
                FROM token_signals
                ORDER BY ts_recorded DESC LIMIT 5
            """)
            saved = [dict(r) for r in cur.fetchall()]
    except Exception:
        saved = []

    return {
        "feed_total":    len(entries),
        "hot_tokens":    len(active_mints),
        "matches":       len(matches),
        "match_list":    [{"mint": e.get("token_address"), "trigger_mc": e.get("trigger_mc"),
                           "signal_times": e.get("signal_times")} for e in matches],
        "saved_recent":  saved,
    }, 200


class _DummyCtx:
    def __enter__(self): return self
    def __exit__(self, *_): pass


# ==============================================================================
# /debug/trades-review — Análise dos tokens que dispararam bounce signals
# ==============================================================================

@app.route("/debug/trades-review")
def debug_trades_review():
    """
    Cruza a tabela trades com migrations e bounce_signals para analisar
    a qualidade dos sinais que dispararam ordens.
    Retorna HTML com todos os campos relevantes de qualidade + resultado.
    """
    with db._cursor() as cur:
        cur.execute("""
            SELECT
                t.id, t.symbol, t.status, t.ts_signal,
                t.mc_bottom, t.entry_expected, t.error_msg,
                m.mint,
                m.ts_migration,
                m.mc_migration, m.mc_migration_source,
                m.mc_peak,  m.time_to_peak,
                m.mc_bottom  AS m_bottom, m.time_to_bottom, m.pct_drop,
                m.mc_1h,    m.var_1h,
                m.recovery,
                m.rug_ratio, m.rat_trader_rate, m.top10_holder_rate,
                m.smart_wallets, m.renowned_wallets, m.is_wash_trading,
                m.creator_token_status, m.creator_balance_rate,
                m.suspected_insider_rate, m.top70_sniper_hold_rate,
                m.entrapment_ratio, m.bot_degen_rate,
                m.liq_migration, m.exchange,
                b.elapsed_sec   AS bounce_elapsed,
                b.drop_from_mig AS bounce_drop,
                b.bounce_pct    AS bounce_pct,
                b.mc_at_signal  AS bounce_mc
            FROM trades t
            LEFT JOIN migrations m ON m.token_nome = t.symbol
            LEFT JOIN bounce_signals b ON b.mint = m.mint
            ORDER BY t.id DESC
        """)
        rows = [dict(r) for r in cur.fetchall()]

    def _pct(v):
        return f"{v:.0%}" if v is not None else "—"
    def _mc(v):
        return f"${v:,.0f}" if v is not None else "—"
    def _f1(v):
        return f"{v:.1f}" if v is not None else "—"

    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Trades Review</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Courier New', monospace; background: #0d1117; color: #c9d1d9; font-size: 12px; }
h1 { padding: 16px; color: #58a6ff; }
table { width: 100%; border-collapse: collapse; }
th { background: #161b22; color: #8b949e; padding: 5px 7px; text-align: left;
     border-bottom: 1px solid #30363d; font-size: 11px; white-space: nowrap; }
td { padding: 4px 7px; border-bottom: 1px solid #21262d; white-space: nowrap; }
tr:hover td { background: #161b22; }
.green { color: #3fb950; } .red { color: #f85149; } .yellow { color: #d29922; }
.sep { background: #21262d; color: #8b949e; font-size: 10px; padding: 2px 7px; }
</style></head><body>
<h1>Trades Review — qualidade dos sinais</h1>
<table><thead><tr>
<th>#</th><th>Symbol</th><th>Status</th><th>Sinal (BRT)</th>
<th>MC mig</th><th>MC bottom</th><th>Drop</th><th>T bottom</th>
<th>MC 1h</th><th>var 1h</th><th>Recovery</th>
<th class="sep">Bounce MC</th><th>Bounce%</th><th>T bounce</th>
<th class="sep">Rug</th><th>Rat</th><th>Top10</th><th>Smart</th><th>Wash</th>
<th class="sep">Creator</th><th>Cr bal</th><th>Insider</th><th>Sniper70</th>
<th>Entrap</th><th>Bot</th>
<th class="sep">Liq mig</th><th>Exchange</th><th>Error</th>
</tr></thead><tbody>"""

    for r in rows:
        rec = r.get("recovery")
        rec_str = '<span class="green">TRUE</span>' if rec else ('<span class="red">FALSE</span>' if rec is not None else "—")
        status = r.get("status","")
        sc = "green" if status=="tp_hit" else ("red" if status in ("sl_hit","error") else "yellow")
        ts = r.get("ts_signal")
        ts_brt = (ts + BRT).strftime("%m/%d %H:%M:%S") if ts else "—"
        var1 = r.get("var_1h")
        var_str = f'<span class="{"green" if var1 and var1>0 else "red"}">{_pct(var1)}</span>' if var1 is not None else "—"
        err = (r.get("error_msg") or "")[:40]
        html += f"""<tr>
<td>{r['id']}</td>
<td><b>{r['symbol']}</b></td>
<td class="{sc}">{status}</td>
<td>{ts_brt}</td>
<td>{_mc(r.get('mc_migration'))}</td>
<td>{_mc(r.get('m_bottom'))}</td>
<td>{_pct(r.get('pct_drop'))}</td>
<td>{_f1(r.get('time_to_bottom'))}min</td>
<td>{_mc(r.get('mc_1h'))}</td>
<td>{var_str}</td>
<td>{rec_str}</td>
<td>{_mc(r.get('bounce_mc'))}</td>
<td>{_pct(r.get('bounce_pct'))}</td>
<td>{_f1(r.get('bounce_elapsed') and r['bounce_elapsed']/60)}min</td>
<td>{_pct(r.get('rug_ratio'))}</td>
<td>{_pct(r.get('rat_trader_rate'))}</td>
<td>{_pct(r.get('top10_holder_rate'))}</td>
<td>{r.get('smart_wallets') or '—'}</td>
<td>{'sim' if r.get('is_wash_trading') else 'não'}</td>
<td>{r.get('creator_token_status') or '—'}</td>
<td>{_pct(r.get('creator_balance_rate'))}</td>
<td>{_pct(r.get('suspected_insider_rate'))}</td>
<td>{_pct(r.get('top70_sniper_hold_rate'))}</td>
<td>{_f1(r.get('entrapment_ratio'))}</td>
<td>{_pct(r.get('bot_degen_rate'))}</td>
<td>{_mc(r.get('liq_migration'))}</td>
<td>{r.get('exchange') or '—'}</td>
<td style="color:#8b949e;font-size:10px">{err}</td>
</tr>"""

    html += "</tbody></table></body></html>"
    return html


# ==============================================================================
# /debug/strategy-sim — Simulação de threshold de bounce e status type 12
# ==============================================================================

@app.route("/debug/strategy-sim")
def debug_strategy_sim():
    THRESHOLDS = [0.05, 0.08, 0.10, 0.15]
    TP = 0.75
    SL = 0.15

    with db._cursor() as cur:
        # --- type 12 signals ---
        cur.execute("""
            SELECT
                COUNT(*)                              AS total_signals,
                COUNT(DISTINCT mint)                  AS tokens_com_sinal,
                MIN(ts_recorded)                      AS primeiro,
                MAX(ts_recorded)                      AS ultimo,
                AVG(trigger_mc)                       AS avg_trigger_mc
            FROM token_signals
        """)
        sig_summary = dict(cur.fetchone())

        cur.execute("""
            SELECT ts.mint, m.token_nome, m.recovery, m.mc_migration,
                   ts.trigger_mc, m.mc_bottom, m.mc_1h,
                   m.time_to_bottom,
                   (ts.trigger_mc < m.mc_migration) AS signal_below_mig
            FROM token_signals ts
            JOIN migrations m ON m.mint = ts.mint
            WHERE m.recovery IS NOT NULL
            ORDER BY ts.ts_recorded DESC
            LIMIT 200
        """)
        sig_rows = [dict(r) for r in cur.fetchall()]

        # --- bounce simulation ---
        cur.execute("""
            SELECT
                m.token_nome, m.mint,
                m.mc_migration, m.mc_bottom, m.time_to_bottom,
                m.pct_drop, m.mc_peak, m.mc_1h, m.recovery,
                b.bounce_pct   AS detected_bounce,
                b.elapsed_sec  AS bounce_elapsed,
                b.mc_at_signal
            FROM migrations m
            JOIN bounce_signals b ON b.mint = m.mint
            WHERE m.mc_bottom IS NOT NULL
              AND m.mc_bottom > 0
            ORDER BY m.ts_migration DESC
        """)
        rows = [dict(r) for r in cur.fetchall()]

    # ---- agrega sinais type12 por token para análise H9 ----
    sig_by_mint = {}
    for r in sig_rows:
        sig_by_mint.setdefault(r["mint"], []).append(r)

    tokens_signal_below = sum(
        1 for mint, sigs in sig_by_mint.items()
        if any(s["signal_below_mig"] for s in sigs)
    )
    rec_with_signal    = [r for r in sig_rows if r["recovery"] is True]
    norec_with_signal  = [r for r in sig_rows if r["recovery"] is False]
    rec_rate_signal    = len(rec_with_signal) / len(sig_rows) * 100 if sig_rows else 0

    # ---- simulação por threshold ----
    def simulate(rows, threshold):
        results = []
        for r in rows:
            bottom = r["mc_bottom"]
            entry  = bottom * (1 + threshold)
            tp_tgt = entry * (1 + TP)
            sl_flr = entry * (1 - SL)
            peak   = r["mc_peak"]
            mc_1h  = r["mc_1h"]

            # TP hit se peak atingiu o alvo
            tp_hit = peak is not None and peak >= tp_tgt
            # SL hit só é possível se sl_flr > mc_bottom (entry muito alta)
            # e mc_1h ficou abaixo (não temos ticks, usamos mc_1h como proxy)
            sl_hit = (not tp_hit) and mc_1h is not None and mc_1h < sl_flr

            if tp_hit:
                pnl = TP
                outcome = "TP"
            elif sl_hit:
                pnl = -SL
                outcome = "SL"
            elif mc_1h is not None:
                pnl = mc_1h / entry - 1
                outcome = "1h_exit"
            else:
                pnl = None
                outcome = "sem_dados"

            results.append({**r, "entry": entry, "tp_tgt": tp_tgt, "sl_flr": sl_flr,
                             "tp_hit": tp_hit, "sl_hit": sl_hit, "pnl": pnl, "outcome": outcome})
        return results

    sim_stats = {}
    for thr in THRESHOLDS:
        res = simulate(rows, thr)
        with_data = [r for r in res if r["pnl"] is not None]
        tp_n  = sum(1 for r in with_data if r["outcome"] == "TP")
        sl_n  = sum(1 for r in with_data if r["outcome"] == "SL")
        ex_n  = sum(1 for r in with_data if r["outcome"] == "1h_exit")
        ev    = sum(r["pnl"] for r in with_data) / len(with_data) if with_data else None
        wr    = tp_n / len(with_data) * 100 if with_data else None
        sim_stats[thr] = {
            "n": len(with_data), "tp": tp_n, "sl": sl_n, "exit": ex_n,
            "wr": wr, "ev": ev, "rows": res
        }

    # ---- HTML ----
    CSS = """
    <style>
    * { box-sizing:border-box; margin:0; padding:0; }
    body { font-family:'Courier New',monospace; background:#0d1117; color:#c9d1d9; font-size:12px; }
    h1,h2 { padding:14px 16px; color:#58a6ff; }
    h2 { font-size:13px; color:#8b949e; padding:8px 16px; }
    .grid { display:flex; gap:14px; padding:0 16px 16px; flex-wrap:wrap; }
    .card { background:#161b22; border:1px solid #30363d; border-radius:6px; padding:10px 16px; min-width:130px; }
    .card .lbl { color:#8b949e; font-size:10px; margin-bottom:3px; }
    .card .val { font-size:16px; font-weight:bold; }
    .green{color:#3fb950} .red{color:#f85149} .yellow{color:#d29922}
    table { width:100%; border-collapse:collapse; }
    th { background:#161b22; color:#8b949e; padding:5px 8px; text-align:left;
         border-bottom:1px solid #30363d; font-size:11px; }
    td { padding:4px 8px; border-bottom:1px solid #21262d; }
    tr:hover td { background:#161b22; }
    .section { padding:16px; }
    .thr-tbl { width:auto; margin:0 16px 16px; }
    .thr-tbl td,.thr-tbl th { padding:6px 14px; }
    </style>"""

    def _mc(v):   return f"${v:,.0f}" if v else "—"
    def _pct(v):  return f"{v:.0%}" if v is not None else "—"
    def _f1(v):   return f"{v:.1f}" if v is not None else "—"

    h = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Strategy Sim</title>{CSS}</head><body>"
    h += "<h1>Strategy Sim — threshold de bounce e status type 12</h1>"

    # --- type 12 summary ---
    h += "<h2>▸ Coleta type 12 (SmartDegenBuy)</h2><div class='grid'>"
    h += f"<div class='card'><div class='lbl'>Total sinais</div><div class='val'>{sig_summary['total_signals']}</div></div>"
    h += f"<div class='card'><div class='lbl'>Tokens únicos</div><div class='val'>{sig_summary['tokens_com_sinal']}</div></div>"
    h += f"<div class='card'><div class='lbl'>Sinal abaixo da mig</div><div class='val yellow'>{tokens_signal_below}</div></div>"
    h += f"<div class='card'><div class='lbl'>Recovery rate (com sinal)</div><div class='val {'green' if rec_rate_signal>=52 else 'red'}'>{rec_rate_signal:.1f}%</div></div>"
    h += f"<div class='card'><div class='lbl'>Baseline recovery</div><div class='val'>52%</div></div>"
    primeiro = sig_summary['primeiro']
    ultimo   = sig_summary['ultimo']
    if primeiro:
        primeiro_brt = (primeiro + BRT).strftime("%m/%d %H:%M")
        ultimo_brt   = (ultimo   + BRT).strftime("%m/%d %H:%M")
        h += f"<div class='card'><div class='lbl'>Período coleta</div><div class='val' style='font-size:11px'>{primeiro_brt}<br>→ {ultimo_brt}</div></div>"
    h += "</div>"

    # --- threshold comparison table ---
    h += "<h2>▸ Simulação de threshold de entrada (TP=75% SL=15%, proxy via mc_peak e mc_1h)</h2>"
    h += "<table class='thr-tbl'><thead><tr>"
    h += "<th>Threshold</th><th>n trades</th><th>TP</th><th>SL</th><th>1h exit</th><th>Win rate</th><th>EV médio</th></tr></thead><tbody>"
    for thr, s in sim_stats.items():
        wr_cls = "green" if (s["wr"] or 0) >= 20 else "yellow"
        ev_cls = "green" if (s["ev"] or 0) > 0 else "red"
        h += f"<tr>"
        h += f"<td><b>+{thr:.0%}</b></td>"
        h += f"<td>{s['n']}</td>"
        h += f"<td class='green'>{s['tp']}</td>"
        h += f"<td class='red'>{s['sl']}</td>"
        h += f"<td class='yellow'>{s['exit']}</td>"
        h += f"<td class='{wr_cls}'>{_f1(s['wr'])}%</td>"
        h += f"<td class='{ev_cls}'>{_pct(s['ev'])}</td>"
        h += "</tr>"
    h += "</tbody></table>"

    # --- detail table for each threshold ---
    for thr in THRESHOLDS:
        s = sim_stats[thr]
        h += f"<h2>▸ Detalhes threshold +{thr:.0%} (mostra apenas tokens com dados)</h2>"
        h += "<div class='section'><table><thead><tr>"
        h += "<th>Token</th><th>MC mig</th><th>MC bottom</th><th>Drop</th><th>T bottom</th>"
        h += "<th>Entry</th><th>TP target</th><th>MC peak</th><th>MC 1h</th>"
        h += "<th>Outcome</th><th>P&L</th><th>Recovery DB</th></tr></thead><tbody>"
        for r in s["rows"]:
            if r["pnl"] is None:
                continue
            oc = r["outcome"]
            oc_cls = "green" if oc == "TP" else ("red" if oc == "SL" else "yellow")
            pnl_cls = "green" if (r["pnl"] or 0) > 0 else "red"
            rec = r["recovery"]
            rec_str = '<span class="green">✓</span>' if rec else ('<span class="red">✗</span>' if rec is not None else "—")
            h += f"<tr>"
            h += f"<td><b>{r['token_nome']}</b></td>"
            h += f"<td>{_mc(r['mc_migration'])}</td>"
            h += f"<td>{_mc(r['mc_bottom'])}</td>"
            h += f"<td>{_pct(r['pct_drop'])}</td>"
            h += f"<td>{_f1(r['time_to_bottom'])}min</td>"
            h += f"<td>{_mc(r['entry'])}</td>"
            h += f"<td>{_mc(r['tp_tgt'])}</td>"
            h += f"<td>{_mc(r['mc_peak'])}</td>"
            h += f"<td>{_mc(r['mc_1h'])}</td>"
            h += f"<td class='{oc_cls}'>{oc}</td>"
            h += f"<td class='{pnl_cls}'>{_pct(r['pnl'])}</td>"
            h += f"<td>{rec_str}</td>"
            h += "</tr>"
        h += "</tbody></table></div>"

    h += "</body></html>"
    return h


# ==============================================================================
# /analise/bounce-quality — O que realmente discrimina bounces bons de ruins
# ==============================================================================

@app.route("/analise/bounce-quality")
def analise_bounce_quality():
    """
    Para cada bounce_signal, caminha os ticks APÓS o sinal e determina
    o outcome real (TP/SL/exit) usando mc_high/mc_low dos kline ticks.
    Depois mostra correlação de cada campo de qualidade com win/loss.
    """
    TP_PCT = 0.75
    SL_PCT = 0.15

    with db._cursor() as cur:
        # Bounce signals com todos os campos de qualidade da migration
        cur.execute("""
            SELECT
                b.mint, b.symbol,
                b.elapsed_sec   AS bounce_elapsed,
                b.mc_min        AS bottom,
                b.mc_at_signal  AS entry,
                b.bounce_pct,
                b.drop_from_mig,
                m.mc_migration,
                m.time_to_bottom,
                m.pct_drop,
                m.mc_1h,
                m.recovery,
                m.rug_ratio,
                m.rat_trader_rate,
                m.top10_holder_rate,
                m.smart_wallets,
                m.renowned_wallets,
                m.is_wash_trading,
                m.bot_degen_rate,
                m.rug_ratio                    AS bundler,
                m.suspected_insider_rate,
                m.top70_sniper_hold_rate,
                m.entrapment_ratio,
                m.creator_token_status,
                m.liq_migration,
                m.exchange,
                m.ts_migration
            FROM bounce_signals b
            JOIN migrations m ON m.mint = b.mint
            WHERE b.mc_at_signal IS NOT NULL AND b.mc_at_signal > 0
            ORDER BY m.ts_migration DESC
        """)
        bounces = [dict(r) for r in cur.fetchall()]

        # Ticks de cada bounce (APENAS após o elapsed_sec do sinal)
        cur.execute("""
            SELECT mint, elapsed_sec, mc, mc_high, mc_low, source
            FROM token_ticks
            WHERE mint = ANY(%s)
            ORDER BY mint, elapsed_sec
        """, ([b["mint"] for b in bounces],))
        all_ticks = cur.fetchall()

    # Agrupa ticks por mint
    ticks_by_mint = {}
    for t in all_ticks:
        ticks_by_mint.setdefault(t["mint"], []).append(t)

    # Calcula outcome real por bounce
    results = []
    for b in bounces:
        entry  = b["entry"]
        tp_tgt = entry * (1 + TP_PCT)
        sl_flr = entry * (1 - SL_PCT)
        ticks  = [t for t in ticks_by_mint.get(b["mint"], [])
                  if t["elapsed_sec"] > b["bounce_elapsed"]]

        outcome = "sem_ticks"
        pnl     = None
        exit_mc = None
        exit_at = None

        for t in ticks:
            # kline ticks têm OHLC — usa high p/ TP e low p/ SL
            high = t["mc_high"] if t["mc_high"] else t["mc"]
            low  = t["mc_low"]  if t["mc_low"]  else t["mc"]

            # SL primeiro (conservador: se mesma vela, assume SL)
            if low is not None and low <= sl_flr:
                outcome = "SL"
                pnl     = -SL_PCT
                exit_mc = sl_flr
                exit_at = t["elapsed_sec"]
                break
            if high is not None and high >= tp_tgt:
                outcome = "TP"
                pnl     = TP_PCT
                exit_mc = tp_tgt
                exit_at = t["elapsed_sec"]
                break
        else:
            # Sem hit — saída no último tick disponível
            if ticks:
                last_mc = ticks[-1]["mc"]
                if last_mc and entry:
                    pnl     = last_mc / entry - 1
                    exit_mc = last_mc
                    exit_at = ticks[-1]["elapsed_sec"]
                    outcome = "exit"
            elif b.get("mc_1h"):
                pnl     = b["mc_1h"] / entry - 1
                exit_mc = b["mc_1h"]
                outcome = "exit_1h"

        results.append({**b, "tp_tgt": tp_tgt, "sl_flr": sl_flr,
                        "outcome": outcome, "pnl": pnl,
                        "exit_mc": exit_mc, "exit_at": exit_at,
                        "n_ticks": len(ticks)})

    # ---- Agregações ----
    with_data  = [r for r in results if r["pnl"] is not None]
    wins       = [r for r in with_data if r["outcome"] == "TP"]
    losses     = [r for r in with_data if r["outcome"] == "SL"]
    exits      = [r for r in with_data if r["outcome"] in ("exit", "exit_1h")]
    no_ticks   = [r for r in results   if r["outcome"] == "sem_ticks"]

    n_total  = len(results)
    n_data   = len(with_data)
    win_rate = len(wins) / n_data * 100 if n_data else 0
    ev       = sum(r["pnl"] for r in with_data) / n_data if n_data else 0

    def avg(lst, key):
        vals = [r[key] for r in lst if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    def _f(v, pct=False):
        if v is None: return "—"
        return f"{v:.0%}" if pct else f"{v:.1f}"

    # Campos numéricos para correlação win vs loss
    FIELDS = [
        ("time_to_bottom",       "T bottom (min)",      False),
        ("pct_drop",             "Drop (%)",            True),
        ("bounce_pct",           "Bounce%",             True),
        ("bot_degen_rate",       "Bot degen",           True),
        ("bundler",              "Rug ratio (bundler)",  True),
        ("rat_trader_rate",      "Rat trader",          True),
        ("top10_holder_rate",    "Top10 holders",       True),
        ("smart_wallets",        "Smart wallets",       False),
        ("renowned_wallets",     "Renowned wallets",    False),
        ("suspected_insider_rate","Insiders",           True),
        ("top70_sniper_hold_rate","Top70 snipers",      True),
        ("entrapment_ratio",     "Entrapment",          False),
        ("liq_migration",        "Liq migração ($)",    False),
        ("n_ticks",              "Ticks pós-sinal",     False),
    ]

    CSS = """<style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Courier New',monospace;background:#0d1117;color:#c9d1d9;font-size:12px}
    h1,h2{padding:12px 16px;color:#58a6ff}
    h2{font-size:13px;color:#8b949e;padding:6px 16px;border-top:1px solid #21262d;margin-top:8px}
    .grid{display:flex;gap:12px;padding:0 16px 12px;flex-wrap:wrap}
    .card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px 16px;min-width:120px}
    .card .lbl{color:#8b949e;font-size:10px;margin-bottom:3px}
    .card .val{font-size:16px;font-weight:bold}
    .green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}
    table{width:100%;border-collapse:collapse}
    th{background:#161b22;color:#8b949e;padding:5px 8px;text-align:left;
       border-bottom:1px solid #30363d;font-size:11px;white-space:nowrap}
    td{padding:4px 8px;border-bottom:1px solid #21262d}
    tr:hover td{background:#161b22}
    .sec{padding:16px}
    .warn{background:#3d3b00;color:#d29922;padding:8px 16px;margin:8px 16px;border-radius:4px;font-size:11px}
    </style>"""

    h = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Bounce Quality</title>{CSS}</head><body>"
    h += "<h1>Bounce Quality — o que discrimina bounces bons de ruins</h1>"

    # --- summary cards ---
    h += "<div class='grid'>"
    h += f"<div class='card'><div class='lbl'>Total bounces</div><div class='val'>{n_total}</div></div>"
    h += f"<div class='card'><div class='lbl'>Com tick data</div><div class='val'>{n_data}</div></div>"
    h += f"<div class='card'><div class='lbl'>Sem ticks</div><div class='val yellow'>{len(no_ticks)}</div></div>"
    h += f"<div class='card'><div class='lbl'>TP hits</div><div class='val green'>{len(wins)}</div></div>"
    h += f"<div class='card'><div class='lbl'>SL hits</div><div class='val red'>{len(losses)}</div></div>"
    h += f"<div class='card'><div class='lbl'>Win rate</div><div class='val {'green' if win_rate>=50 else 'red'}'>{win_rate:.1f}%</div></div>"
    h += f"<div class='card'><div class='lbl'>EV médio</div><div class='val {'green' if ev>0 else 'red'}'>{ev:+.0%}</div></div>"
    h += "</div>"

    if len(no_ticks) > n_data:
        h += f"<div class='warn'>⚠️ {len(no_ticks)}/{n_total} bounces sem ticks — cobertura insuficiente para conclusões sólidas. Acumulando via kline.</div>"

    # --- correlação win vs loss ---
    h += "<h2>▸ Médias por campo: TP hits vs SL hits (quanto maior a diferença, mais discrimina)</h2>"
    h += "<div class='sec'><table><thead><tr>"
    h += "<th>Campo</th><th>TP avg</th><th>SL avg</th><th>Exits avg</th><th>Diferença TP-SL</th><th>Sinal</th></tr></thead><tbody>"

    field_diffs = []
    for key, label, is_pct in FIELDS:
        a_win  = avg(wins,   key)
        a_loss = avg(losses, key)
        a_exit = avg(exits,  key)
        if a_win is None and a_loss is None:
            continue
        diff = (a_win - a_loss) if (a_win is not None and a_loss is not None) else None
        field_diffs.append((key, label, is_pct, a_win, a_loss, a_exit, diff))

    field_diffs.sort(key=lambda x: abs(x[6] or 0), reverse=True)

    for key, label, is_pct, a_win, a_loss, a_exit, diff in field_diffs:
        if diff is None:
            diff_str = "—"
            diff_cls = ""
        else:
            if is_pct:
                diff_str = f"{diff:+.1%}"
            else:
                diff_str = f"{diff:+.1f}"
            diff_cls = "green" if diff > 0 else "red"

        # Interpretação: campo alto = bom para TP? ou ruim?
        if diff is not None:
            if key in ("bot_degen_rate", "bundler", "rug_ratio", "rat_trader_rate",
                       "top10_holder_rate", "suspected_insider_rate", "top70_sniper_hold_rate",
                       "entrapment_ratio", "pct_drop"):
                sinal = "⬇ alto = pior" if diff < 0 else "⬆ alto = melhor (inesperado)"
            else:
                sinal = "⬆ alto = melhor" if diff > 0 else "⬇ alto = pior (inesperado)"
        else:
            sinal = "—"

        fmt = "{:.0%}" if is_pct else "{:.1f}"
        h += f"<tr><td><b>{label}</b></td>"
        h += f"<td class='green'>{fmt.format(a_win) if a_win is not None else '—'}</td>"
        h += f"<td class='red'>{fmt.format(a_loss) if a_loss is not None else '—'}</td>"
        h += f"<td class='yellow'>{fmt.format(a_exit) if a_exit is not None else '—'}</td>"
        h += f"<td class='{diff_cls}'><b>{diff_str}</b></td>"
        h += f"<td style='color:#8b949e;font-size:10px'>{sinal}</td></tr>"

    h += "</tbody></table></div>"

    # --- tabela detalhada ---
    h += "<h2>▸ Resultados individuais (ordenados por tempo de migração)</h2>"
    h += "<div class='sec'><table><thead><tr>"
    h += "<th>Token</th><th>Outcome</th><th>P&L</th><th>Entry</th><th>Exit MC</th>"
    h += "<th>T bottom</th><th>Ticks</th><th>Bot</th><th>Bundler</th>"
    h += "<th>Drop</th><th>Bounce</th><th>Smart</th><th>Creator</th></tr></thead><tbody>"

    for r in sorted(results, key=lambda x: x.get("ts_migration") or 0, reverse=True):
        oc  = r["outcome"]
        oc_cls = "green" if oc == "TP" else ("red" if oc == "SL" else "yellow")
        pnl = r["pnl"]
        pnl_cls = "green" if (pnl or 0) > 0 else "red"
        h += f"<tr>"
        h += f"<td><b>{r['symbol']}</b></td>"
        h += f"<td class='{oc_cls}'>{oc}</td>"
        h += f"<td class='{pnl_cls}'>{pnl:+.0%}</td>" if pnl is not None else "<td>—</td>"
        h += f"<td>${r['entry']:,.0f}</td>"
        h += f"<td>${r['exit_mc']:,.0f}</td>" if r.get("exit_mc") else "<td>—</td>"
        h += f"<td>{r['time_to_bottom']:.1f}min</td>" if r.get("time_to_bottom") else "<td>—</td>"
        h += f"<td>{r['n_ticks']}</td>"
        h += f"<td>{r['bot_degen_rate']:.0%}</td>" if r.get("bot_degen_rate") is not None else "<td>—</td>"
        h += f"<td>{r['bundler']:.0%}</td>" if r.get("bundler") is not None else "<td>—</td>"
        h += f"<td>{r['pct_drop']:.0%}</td>" if r.get("pct_drop") is not None else "<td>—</td>"
        h += f"<td>{r['bounce_pct']:.0%}</td>" if r.get("bounce_pct") is not None else "<td>—</td>"
        h += f"<td>{r['smart_wallets']}</td>" if r.get("smart_wallets") is not None else "<td>—</td>"
        h += f"<td>{r['creator_token_status'] or '—'}</td>"
        h += "</tr>"

    h += "</tbody></table></div></body></html>"
    return h


# ==============================================================================
# /debug/recovery-gap — Por que tokens não têm recovery definido
# ==============================================================================

@app.route("/debug/recovery-gap")
def debug_recovery_gap():
    with db._cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*)                                                         AS total,
                COUNT(recovery)                                                  AS com_recovery,
                COUNT(*) FILTER (WHERE recovery IS NULL)                         AS sem_recovery,
                COUNT(*) FILTER (WHERE recovery IS NULL
                    AND mc_1h IS NULL AND mc_bottom IS NULL)                     AS sem_ambos,
                COUNT(*) FILTER (WHERE recovery IS NULL
                    AND mc_1h IS NULL AND mc_bottom IS NOT NULL)                 AS tem_bottom_falta_1h,
                COUNT(*) FILTER (WHERE recovery IS NULL
                    AND mc_1h IS NOT NULL AND mc_bottom IS NULL)                 AS tem_1h_falta_bottom,
                COUNT(*) FILTER (WHERE recovery IS NULL
                    AND mc_1h IS NOT NULL AND mc_bottom IS NOT NULL)             AS tem_ambos_sem_recovery,
                COUNT(*) FILTER (WHERE recovery IS TRUE)                         AS rec_true,
                COUNT(*) FILTER (WHERE recovery IS FALSE)                        AS rec_false,
                COUNT(*) FILTER (WHERE ts_migration IS NULL)                     AS sem_ts_migration
            FROM migrations
        """)
        totals = dict(cur.fetchone())

        # Distribuição temporal — últimas 2 semanas
        cur.execute("""
            SELECT
                DATE_TRUNC('day', ts_migration AT TIME ZONE 'America/Sao_Paulo') AS dia,
                COUNT(*)                                           AS total,
                COUNT(recovery)                                    AS com_recovery,
                COUNT(*) FILTER (WHERE recovery IS NULL)           AS sem_recovery,
                COUNT(*) FILTER (WHERE recovery IS TRUE)           AS rec_true,
                COUNT(*) FILTER (WHERE recovery IS FALSE)          AS rec_false
            FROM migrations
            WHERE ts_migration IS NOT NULL
              AND ts_migration > NOW() - INTERVAL '14 days'
            GROUP BY 1 ORDER BY 1 DESC
        """)
        daily = [dict(r) for r in cur.fetchall()]

        # Tokens com ambos os campos mas sem recovery — suspeito de bug
        cur.execute("""
            SELECT mint, token_nome, mc_1h, mc_bottom, ts_migration,
                   EXTRACT(EPOCH FROM (NOW() - ts_migration))/3600 AS horas_desde_mig
            FROM migrations
            WHERE recovery IS NULL
              AND mc_1h IS NOT NULL
              AND mc_bottom IS NOT NULL
            ORDER BY ts_migration DESC
            LIMIT 20
        """)
        bug_candidates = [dict(r) for r in cur.fetchall()]

        # Tokens sem mc_1h: quando migraram?
        cur.execute("""
            SELECT
                CASE
                    WHEN ts_migration > NOW() - INTERVAL '2 hours' THEN 'recente (<2h)'
                    WHEN ts_migration > NOW() - INTERVAL '24 hours' THEN 'hoje (2-24h)'
                    WHEN ts_migration > NOW() - INTERVAL '7 days'  THEN 'semana passada'
                    ELSE 'antigo (>7d)'
                END AS janela,
                COUNT(*) AS n
            FROM migrations
            WHERE recovery IS NULL AND mc_1h IS NULL
            GROUP BY 1 ORDER BY MIN(ts_migration) DESC
        """)
        sem_1h_por_janela = [dict(r) for r in cur.fetchall()]

    CSS = """<style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Courier New',monospace;background:#0d1117;color:#c9d1d9;font-size:12px}
    h1{padding:14px 16px;color:#58a6ff}
    h2{font-size:12px;color:#8b949e;padding:8px 16px 4px;border-top:1px solid #21262d;margin-top:10px}
    .grid{display:flex;gap:10px;padding:0 16px 12px;flex-wrap:wrap}
    .card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px 14px;min-width:130px}
    .card .lbl{color:#8b949e;font-size:10px;margin-bottom:3px}
    .card .val{font-size:15px;font-weight:bold}
    .green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}.blue{color:#58a6ff}
    table{width:100%;border-collapse:collapse}
    th{background:#161b22;color:#8b949e;padding:4px 8px;text-align:right;
       border-bottom:1px solid #30363d;font-size:10px}
    th:first-child{text-align:left}
    td{padding:3px 8px;border-bottom:1px solid #21262d;text-align:right}
    td:first-child{text-align:left}
    .sec{padding:0 16px 12px}
    .warn{background:#2a1a00;border:1px solid #d29922;border-radius:4px;
          padding:8px 14px;margin:8px 16px;font-size:11px;color:#d29922}
    </style>"""

    h = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Recovery Gap</title>{CSS}</head><body>"
    h += "<h1>Recovery Gap — por que 619 tokens não têm recovery</h1>"

    t = totals
    gap_pct = t['sem_recovery'] / t['total'] * 100 if t['total'] else 0

    h += "<div class='grid'>"
    h += f"<div class='card'><div class='lbl'>Total migrations</div><div class='val'>{t['total']}</div></div>"
    h += f"<div class='card'><div class='lbl'>Com recovery</div><div class='val green'>{t['com_recovery']}</div></div>"
    h += f"<div class='card'><div class='lbl'>Sem recovery</div><div class='val red'>{t['sem_recovery']} ({gap_pct:.0f}%)</div></div>"
    h += f"<div class='card'><div class='lbl'>recovery=TRUE</div><div class='val green'>{t['rec_true']}</div></div>"
    h += f"<div class='card'><div class='lbl'>recovery=FALSE</div><div class='val red'>{t['rec_false']}</div></div>"
    h += "</div>"

    h += "<h2>▸ Por que estão sem recovery?</h2>"
    h += "<div class='grid'>"
    h += f"<div class='card'><div class='lbl'>Sem mc_1h E sem mc_bottom</div><div class='val yellow'>{t['sem_ambos']}</div></div>"
    h += f"<div class='card'><div class='lbl'>Tem bottom, falta mc_1h</div><div class='val yellow'>{t['tem_bottom_falta_1h']}</div></div>"
    h += f"<div class='card'><div class='lbl'>Tem mc_1h, falta bottom</div><div class='val yellow'>{t['tem_1h_falta_bottom']}</div></div>"
    h += f"<div class='card'><div class='lbl'>Tem ambos, sem recovery ⚠️</div><div class='val red'>{t['tem_ambos_sem_recovery']}</div></div>"
    h += f"<div class='card'><div class='lbl'>Sem ts_migration</div><div class='val'>{t['sem_ts_migration']}</div></div>"
    h += "</div>"

    if t['tem_ambos_sem_recovery'] > 0:
        h += f"<div class='warn'>⚠️ {t['tem_ambos_sem_recovery']} tokens têm mc_1h E mc_bottom mas recovery=NULL — possível bug no update_checkpoint()</div>"

    h += "<h2>▸ Tokens sem mc_1h por janela temporal</h2>"
    h += "<div class='sec'><table><thead><tr><th>Janela</th><th>N sem recovery</th></tr></thead><tbody>"
    for r in sem_1h_por_janela:
        h += f"<tr><td>{r['janela']}</td><td>{r['n']}</td></tr>"
    h += "</tbody></table></div>"

    h += "<h2>▸ Distribuição diária — últimas 2 semanas (BRT)</h2>"
    h += "<div class='sec'><table><thead><tr>"
    h += "<th>Dia</th><th>Total</th><th>c/Recovery</th><th>s/Recovery</th><th>TRUE</th><th>FALSE</th><th>Coverage%</th></tr></thead><tbody>"
    for r in daily:
        cov = r['com_recovery'] / r['total'] * 100 if r['total'] else 0
        cov_cls = "green" if cov >= 70 else ("yellow" if cov >= 40 else "red")
        h += f"<tr><td>{str(r['dia'])[:10]}</td><td>{r['total']}</td>"
        h += f"<td class='green'>{r['com_recovery']}</td>"
        h += f"<td class='red'>{r['sem_recovery']}</td>"
        h += f"<td class='green'>{r['rec_true']}</td>"
        h += f"<td class='red'>{r['rec_false']}</td>"
        h += f"<td class='{cov_cls}'>{cov:.0f}%</td></tr>"
    h += "</tbody></table></div>"

    if bug_candidates:
        h += "<h2>▸ Candidatos a bug — têm mc_1h + mc_bottom mas recovery=NULL</h2>"
        h += "<div class='sec'><table><thead><tr>"
        h += "<th>Token</th><th>mc_1h</th><th>mc_bottom</th><th>Ratio</th><th>Horas desde mig</th></tr></thead><tbody>"
        for r in bug_candidates:
            ratio = r['mc_1h'] / r['mc_bottom'] if r['mc_bottom'] else 0
            h += f"<tr><td>{r['token_nome']}</td>"
            h += f"<td>${r['mc_1h']:,.0f}</td><td>${r['mc_bottom']:,.0f}</td>"
            h += f"<td>{ratio:.2f}x</td><td>{r['horas_desde_mig']:.1f}h</td></tr>"
        h += "</tbody></table></div>"

    h += "</body></html>"
    return h


# ==============================================================================
# /analise/filter-thresholds — Onde cortar cada filtro com base nos dados
# ==============================================================================

@app.route("/analise/filter-thresholds")
def analise_filter_thresholds():
    """
    Para cada campo de filtro, divide os tokens em faixas e mostra
    recovery rate por faixa. Deixa os dados determinarem os thresholds.
    """
    with db._cursor() as cur:
        cur.execute("""
            SELECT
                rug_ratio,
                bot_degen_rate,
                rat_trader_rate,
                top10_holder_rate,
                suspected_insider_rate,
                top70_sniper_hold_rate,
                entrapment_ratio,
                smart_wallets,
                liq_migration,
                volume_1m,
                recovery,
                rug_ratio AS bundler_trader_amount_rate
            FROM migrations
            WHERE recovery IS NOT NULL
        """)
        rows = [dict(r) for r in cur.fetchall()]

    # Campos a analisar: (coluna_db, label, faixas, maior_é_pior)
    # Nota: rug_ratio no banco = bundler_trader_amount_rate do trenches (salvo assim em upsert_migration)
    FIELDS = [
        ("bundler_trader_amount_rate", "Bundler rate (col rug_ratio)", [0,.10,.15,.20,.25,.30,.40,.50,.60,1], True),
        ("bot_degen_rate",             "Bot degen rate",               [0,.10,.20,.30,.40,.50,.60,.70,.80,1], True),
        ("rat_trader_rate",            "Rat trader rate",              [0,.05,.10,.15,.20,.30,.40,.50,1], True),
        ("top10_holder_rate",          "Top 10 holders",               [0,.40,.50,.60,.70,.75,.80,.85,.90,1], True),
        ("suspected_insider_rate",     "Suspected insiders",           [0,.02,.05,.10,.15,.20,.30,1], True),
        ("top70_sniper_hold_rate",     "Top 70 snipers hold",          [0,.02,.05,.10,.15,.20,.30,1], True),
        ("entrapment_ratio",           "Entrapment ratio",             [0,.01,.05,.10,.20,.30,1], True),
        ("smart_wallets",              "Smart wallets",                [-1,0,1,2,3,5,10,999], False),
        ("liq_migration",              "Liquidez migração ($)",        [0,5000,10000,15000,25000,50000,999999], False),
    ]

    def bucket_label(lo, hi, is_int=False):
        if is_int:
            return f"{int(lo)+1}–{int(hi)}" if hi < 999 else f">{int(lo)}"
        if hi >= 1:
            return f">{lo:.0%}"
        return f"{lo:.0%}–{hi:.0%}"

    CSS = """<style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Courier New',monospace;background:#0d1117;color:#c9d1d9;font-size:12px}
    h1{padding:14px 16px;color:#58a6ff}
    h2{font-size:12px;color:#8b949e;padding:6px 16px 4px;
       border-top:1px solid #21262d;margin-top:10px}
    .wrap{padding:0 16px 16px;display:flex;flex-wrap:wrap;gap:20px}
    .block{min-width:340px;max-width:480px}
    .block h3{color:#c9d1d9;font-size:12px;margin-bottom:6px;padding:4px 0;
              border-bottom:1px solid #30363d}
    table{width:100%;border-collapse:collapse}
    th{background:#161b22;color:#8b949e;padding:4px 8px;text-align:right;
       border-bottom:1px solid #30363d;font-size:10px}
    th:first-child{text-align:left}
    td{padding:3px 8px;border-bottom:1px solid #21262d;text-align:right}
    td:first-child{text-align:left;color:#8b949e}
    .bar-cell{width:80px}
    .bar{height:10px;border-radius:2px;display:inline-block}
    .green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}
    .note{background:#161b22;border:1px solid #30363d;border-radius:4px;
          padding:10px 14px;margin:8px 16px 0;font-size:11px;color:#8b949e;line-height:1.7}
    .best{background:#0d2818}
    </style>"""

    n_total = len(rows)
    baseline = sum(1 for r in rows if r["recovery"]) / n_total if n_total else 0

    h = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Filter Thresholds</title>{CSS}</head><body>"
    h += "<h1>Filter Thresholds — onde os dados mandam cortar</h1>"
    h += f"<div class='note'>"
    h += f"<b>Universo:</b> {n_total} tokens com recovery definido &nbsp;|&nbsp; "
    h += f"<b>Baseline recovery rate:</b> <span class='yellow'>{baseline:.1%}</span><br>"
    h += "Cada bloco mostra recovery rate por faixa de valor. "
    h += "O corte ideal está onde a recovery rate cai abaixo do baseline de forma consistente."
    h += "</div>"

    h += "<div class='wrap'>"

    for col, label, edges, higher_is_worse in FIELDS:
        vals = [(r[col], r["recovery"]) for r in rows if r[col] is not None]
        if not vals:
            continue

        is_int = col == "smart_wallets"

        buckets = []
        for i in range(len(edges) - 1):
            lo, hi = edges[i], edges[i + 1]
            if is_int:
                bucket = [(v, rec) for v, rec in vals if lo < v <= hi]
            else:
                bucket = [(v, rec) for v, rec in vals if lo <= v < hi]
            n = len(bucket)
            if n == 0:
                continue
            rr = sum(1 for _, rec in bucket if rec) / n
            lbl = bucket_label(lo, hi, is_int)
            buckets.append((lbl, n, rr, lo, hi))

        if not buckets:
            continue

        # Marca a faixa com maior diferença de recovery vs baseline
        best_cut = None
        if higher_is_worse:
            # Procura primeira faixa onde rr < baseline - 5pp
            for i, (lbl, n, rr, lo, hi) in enumerate(buckets):
                if rr < baseline - 0.05 and n >= 10:
                    best_cut = lo
                    break
        else:
            # Procura faixa onde rr > baseline + 5pp
            for i, (lbl, n, rr, lo, hi) in enumerate(buckets):
                if rr > baseline + 0.05 and n >= 10:
                    best_cut = hi
                    break

        h += "<div class='block'>"
        h += f"<h3>{label}</h3>"
        h += "<table><thead><tr><th>Faixa</th><th>N</th><th>Recovery%</th>"
        h += "<th class='bar-cell'>vs baseline</th></tr></thead><tbody>"

        for lbl, n, rr, lo, hi in buckets:
            delta = rr - baseline
            rr_cls = "green" if delta > 0.05 else ("red" if delta < -0.05 else "yellow")
            delta_str = f"{delta:+.1%}"

            # barra visual
            bar_w = max(2, int(abs(delta) * 200))
            bar_clr = "#3fb950" if delta > 0 else "#f85149"

            is_best_row = (best_cut is not None and
                           ((higher_is_worse and lo >= best_cut) or
                            (not higher_is_worse and hi <= best_cut)))
            row_cls = "best" if is_best_row else ""

            h += f"<tr class='{row_cls}'>"
            h += f"<td>{lbl}</td>"
            h += f"<td>{n}</td>"
            h += f"<td class='{rr_cls}'>{rr:.1%}</td>"
            h += f"<td><span class='bar' style='width:{bar_w}px;background:{bar_clr}'></span> {delta_str}</td>"
            h += "</tr>"

        h += "</tbody></table>"

        if best_cut is not None:
            if higher_is_worse:
                h += f"<div style='font-size:10px;color:#58a6ff;padding:4px 0'>▸ Corte sugerido pelos dados: &lt; {best_cut:.0%}</div>"
            else:
                h += f"<div style='font-size:10px;color:#58a6ff;padding:4px 0'>▸ Corte sugerido pelos dados: &gt; {best_cut}</div>"

        h += "</div>"

    h += "</div></body></html>"
    return h


# ==============================================================================
# /analise/sniper-entry — Simula entrada na primeira queda pós-migração
# ==============================================================================

@app.route("/analise/sniper-entry")
def analise_sniper_entry():
    """
    Bigplayer thesis: token migra → early buyers vendem → dump em segundos.
    Entra na primeira queda (sem esperar bounce). Analisa múltiplos thresholds.
    Usa token_ticks reais: mc_low para trigger e SL, mc_high para TP.
    """
    TP_PCT  = 0.75
    SL_PCT  = 0.15
    THRESHOLDS = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]

    with db._cursor() as cur:
        cur.execute("""
            SELECT m.mint, m.token_nome, m.mc_migration, m.ts_migration,
                   m.time_to_bottom, m.pct_drop, m.recovery, m.mc_1h,
                   m.bot_degen_rate, m.rug_ratio
            FROM migrations m
            WHERE m.mc_migration IS NOT NULL AND m.mc_migration > 0
              AND m.ts_migration IS NOT NULL
              AND EXISTS (SELECT 1 FROM token_ticks t WHERE t.mint = m.mint)
            ORDER BY m.ts_migration DESC
        """)
        tokens = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT mint, elapsed_sec, mc, mc_high, mc_low, source
            FROM token_ticks
            WHERE mint = ANY(%s)
            ORDER BY mint, elapsed_sec
        """, ([t["mint"] for t in tokens],))
        all_ticks = cur.fetchall()

    ticks_by_mint = {}
    for t in all_ticks:
        ticks_by_mint.setdefault(t["mint"], []).append(t)

    # Para cada threshold simula entradas
    grid = {}
    detail_by_threshold = {}

    for thr in THRESHOLDS:
        results = []
        for tok in tokens:
            mint    = tok["mint"]
            mc_mig  = tok["mc_migration"]
            ticks   = ticks_by_mint.get(mint, [])
            if not ticks or not mc_mig:
                continue

            entry_level = mc_mig * (1 - thr)
            tp_target   = entry_level * (1 + TP_PCT)
            sl_floor    = entry_level * (1 - SL_PCT)

            # Acha o primeiro tick onde price toca entry_level
            entry_idx   = None
            entry_time  = None
            for i, t in enumerate(ticks):
                low = t["mc_low"] if t["mc_low"] else t["mc"]
                if low is not None and low <= entry_level:
                    entry_idx  = i
                    entry_time = t["elapsed_sec"]
                    break

            if entry_idx is None:
                continue  # Nunca chegou nesse nível

            # Verifica o mesmo candle da entrada: SL pode acionar imediatamente
            outcome = None
            pnl     = None
            exit_at = None

            entry_tick = ticks[entry_idx]
            entry_low  = entry_tick["mc_low"]  if entry_tick["mc_low"]  else entry_tick["mc"]
            entry_high = entry_tick["mc_high"] if entry_tick["mc_high"] else entry_tick["mc"]

            if entry_low is not None and entry_low <= sl_floor:
                # SL na mesma vela da entrada (dump continuou)
                outcome = "SL_imediato"
                pnl     = -SL_PCT
                exit_at = entry_time
            elif entry_high is not None and entry_high >= tp_target:
                outcome = "TP"
                pnl     = TP_PCT
                exit_at = entry_time
            else:
                # Caminha ticks seguintes
                for t in ticks[entry_idx + 1:]:
                    high = t["mc_high"] if t["mc_high"] else t["mc"]
                    low  = t["mc_low"]  if t["mc_low"]  else t["mc"]
                    if low is not None and low <= sl_floor:
                        outcome = "SL"
                        pnl     = -SL_PCT
                        exit_at = t["elapsed_sec"]
                        break
                    if high is not None and high >= tp_target:
                        outcome = "TP"
                        pnl     = TP_PCT
                        exit_at = t["elapsed_sec"]
                        break
                else:
                    last = ticks[-1]
                    if last["mc"] and entry_level:
                        pnl     = last["mc"] / entry_level - 1
                        exit_at = last["elapsed_sec"]
                        outcome = "exit"

            if pnl is not None:
                results.append({
                    **tok,
                    "thr":        thr,
                    "entry_time": entry_time,
                    "outcome":    outcome,
                    "pnl":        pnl,
                    "exit_at":    exit_at,
                })

        wins   = [r for r in results if r["outcome"] == "TP"]
        sl_imm = [r for r in results if r["outcome"] == "SL_imediato"]
        sls    = [r for r in results if r["outcome"] in ("SL", "SL_imediato")]
        exits  = [r for r in results if r["outcome"] == "exit"]
        n      = len(results)

        ev       = sum(r["pnl"] for r in results) / n if n else 0
        win_rate = len(wins) / n if n else 0
        avg_t    = sum(r["entry_time"] for r in results) / n if n else 0

        grid[thr] = {
            "n": n, "wins": len(wins), "sls": len(sls),
            "sl_imm": len(sl_imm), "exits": len(exits),
            "win_rate": win_rate, "ev": ev, "avg_entry_sec": avg_t,
        }
        detail_by_threshold[thr] = results

    # Melhor EV
    best_thr = max(grid, key=lambda k: grid[k]["ev"]) if grid else None

    # ---- Referência bounce approach ----
    bounce_ref = {"win_rate": 0.125, "ev": -0.04, "n": 48}
    bounce_filt= {"win_rate": 0.313, "ev": 0.131, "n": 16}

    CSS = """<style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Courier New',monospace;background:#0d1117;color:#c9d1d9;font-size:12px}
    h1,h2{padding:12px 16px;color:#58a6ff}
    h2{font-size:13px;color:#8b949e;padding:8px 16px;border-top:1px solid #21262d;margin-top:8px}
    .grid{display:flex;gap:10px;padding:0 16px 12px;flex-wrap:wrap}
    .card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px 14px;min-width:110px}
    .card .lbl{color:#8b949e;font-size:10px;margin-bottom:3px}
    .card .val{font-size:15px;font-weight:bold}
    .green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}.blue{color:#58a6ff}
    table{width:100%;border-collapse:collapse}
    th{background:#161b22;color:#8b949e;padding:5px 8px;text-align:right;
       border-bottom:1px solid #30363d;font-size:11px;white-space:nowrap}
    th:first-child{text-align:left}
    td{padding:4px 8px;border-bottom:1px solid #21262d;text-align:right}
    td:first-child{text-align:left}
    tr:hover td{background:#161b22}
    .sec{padding:0 16px 16px}
    .best{background:#1a2a1a!important}
    .note{background:#1a1a2e;border:1px solid #30363d;border-radius:4px;
          padding:10px 14px;margin:8px 16px;font-size:11px;color:#8b949e;line-height:1.6}
    </style>"""

    h = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Sniper Entry</title>{CSS}</head><body>"
    h += "<h1>Sniper Entry — primeira queda pós-migração (sem esperar bounce)</h1>"

    h += "<div class='note'>"
    h += "<b>Hipótese:</b> token migra → early buyers vendem → dump em segundos → entrar durante a queda.<br>"
    h += f"<b>Universo:</b> {len(tokens)} tokens com ticks &nbsp;|&nbsp; TP={TP_PCT:.0%} &nbsp;|&nbsp; SL={SL_PCT:.0%}<br>"
    h += "<b>SL_imediato:</b> candle de entrada já perfurou o SL — dump continuou sem pausa.<br>"
    h += "<b>⚠️ Resolução real dos ticks:</b> trenches poll a cada ~30s, kline a cada ~60s. "
    h += "O dump dos primeiros 0-10s de migração é <b>invisível</b> nos nossos dados. "
    h += "Entradas marcadas como '0s' são na verdade o primeiro tick capturado (~30-60s após migrar)."
    h += "</div>"

    # --- Grid de thresholds ---
    h += "<h2>▸ Grade de thresholds — entrada na primeira queda</h2>"
    h += "<div class='sec'><table><thead><tr>"
    h += "<th>Threshold entrada</th><th>Trades</th><th>TPs</th><th>SLs</th>"
    h += "<th>SL imediato</th><th>Win rate</th><th>EV</th><th>Tempo médio entrada</th></tr></thead><tbody>"

    for thr in THRESHOLDS:
        g   = grid[thr]
        cls = "best" if thr == best_thr else ""
        ev_cls  = "green" if g["ev"] > 0 else "red"
        wr_cls  = "green" if g["win_rate"] >= 0.25 else "red"
        mark    = " ◀ melhor EV" if thr == best_thr else ""
        h += f"<tr class='{cls}'>"
        h += f"<td><b>-{thr:.0%}</b>{mark}</td>"
        h += f"<td>{g['n']}</td>"
        h += f"<td class='green'>{g['wins']}</td>"
        h += f"<td class='red'>{g['sls']}</td>"
        h += f"<td class='yellow'>{g['sl_imm']}</td>"
        h += f"<td class='{wr_cls}'>{g['win_rate']:.1%}</td>"
        h += f"<td class='{ev_cls}'>{g['ev']:+.1%}</td>"
        h += f"<td>{g['avg_entry_sec']:.0f}s ({g['avg_entry_sec']/60:.1f}min)</td>"
        h += "</tr>"

    # Linha separadora — bounce approach
    h += f"<tr style='border-top:2px solid #58a6ff'>"
    h += f"<td><b>[REFERÊNCIA] Bounce atual</b></td>"
    h += f"<td>{bounce_ref['n']}</td><td class='green'>6</td><td class='red'>42</td>"
    h += f"<td class='yellow'>—</td>"
    h += f"<td class='red'>{bounce_ref['win_rate']:.1%}</td>"
    h += f"<td class='red'>{bounce_ref['ev']:+.1%}</td>"
    h += f"<td>~{15*60:.0f}s (~15min)</td></tr>"
    h += f"<tr>"
    h += f"<td><b>[REFERÊNCIA] Bounce + drop≤65%</b></td>"
    h += f"<td>{bounce_filt['n']}</td><td class='green'>5</td><td class='red'>11</td>"
    h += f"<td class='yellow'>—</td>"
    h += f"<td class='green'>{bounce_filt['win_rate']:.1%}</td>"
    h += f"<td class='green'>{bounce_filt['ev']:+.1%}</td>"
    h += f"<td>~{15*60:.0f}s (~15min)</td></tr>"
    h += "</tbody></table></div>"

    # --- Filtro: entradas no primeiro minuto (≤60s) ---
    h += "<h2>▸ Isolando entradas ≤ 60s após migração — o 'primeiro minuto'</h2>"
    early_rows = []
    for thr in THRESHOLDS:
        early = [r for r in detail_by_threshold[thr] if (r["entry_time"] or 0) <= 60]
        if not early:
            continue
        e_wins = [r for r in early if r["outcome"] == "TP"]
        e_sl_i = [r for r in early if r["outcome"] == "SL_imediato"]
        e_sls  = [r for r in early if r["outcome"] in ("SL", "SL_imediato")]
        n_e    = len(early)
        ev_e   = sum(r["pnl"] for r in early if r["pnl"] is not None) / n_e if n_e else 0
        wr_e   = len(e_wins) / n_e if n_e else 0
        early_rows.append((thr, n_e, len(e_wins), len(e_sls), len(e_sl_i), wr_e, ev_e))

    h += "<div class='sec'><table><thead><tr>"
    h += "<th>Threshold</th><th>Trades ≤60s</th><th>TPs</th><th>SLs</th>"
    h += "<th>SL imediato</th><th>% imediato</th><th>Win rate</th><th>EV</th></tr></thead><tbody>"
    for thr, n_e, wins_e, sls_e, sl_i_e, wr_e, ev_e in early_rows:
        ev_cls = "green" if ev_e > 0 else "red"
        wr_cls = "green" if wr_e >= 0.25 else "red"
        pct_imm = sl_i_e / n_e if n_e else 0
        h += f"<tr><td><b>-{thr:.0%}</b></td><td>{n_e}</td>"
        h += f"<td class='green'>{wins_e}</td><td class='red'>{sls_e}</td>"
        h += f"<td class='yellow'>{sl_i_e}</td>"
        h += f"<td class='{'red' if pct_imm > 0.4 else 'yellow'}'>{pct_imm:.0%}</td>"
        h += f"<td class='{wr_cls}'>{wr_e:.1%}</td>"
        h += f"<td class='{ev_cls}'>{ev_e:+.1%}</td></tr>"
    h += "</tbody></table></div>"

    h += "<div class='note'>"
    h += "<b>Conclusão do primeiro minuto:</b><br>"
    h += "O % de SL_imediato nas entradas ≤60s é a métrica chave. "
    h += "Se >40% das entradas perfuram diretamente o SL sem pausa, significa que "
    h += "o dump <b>não formou fundo ainda</b> — o preço continuou caindo. "
    h += "Um sniper real nesse contexto precisaria de dados sub-segundo (on-chain via WebSocket: "
    h += "Jito bundles, Helius webhooks) para detectar o momento exato de capitulação.<br><br>"
    h += "<b>O que nossos ticks capturam de fato:</b> o estado do token ~30-60s após migrar. "
    h += "Nesse ponto, tokens com fundo rápido (H10 &lt;25min) frequentemente já formaram o fundo "
    h += "e estão em bounce — o que explica por que a estratégia de bounce com drop≤65% é a única com EV positivo."
    h += "</div>"

    # --- Detalhe do melhor threshold ---
    if best_thr is not None:
        best_results = detail_by_threshold[best_thr]
        h += f"<h2>▸ Resultados individuais — threshold -{best_thr:.0%} (melhor EV)</h2>"
        h += "<div class='sec'><table><thead><tr>"
        h += "<th>Token</th><th>Outcome</th><th>P&L</th><th>Entrada (s)</th>"
        h += "<th>Saída (s)</th><th>Drop real</th><th>T bottom</th><th>Bot</th><th>Bundler</th></tr></thead><tbody>"
        for r in sorted(best_results, key=lambda x: x["entry_time"] or 0):
            oc  = r["outcome"]
            oc_cls = "green" if oc == "TP" else ("yellow" if oc == "exit" else "red")
            pnl = r["pnl"]
            pnl_cls = "green" if (pnl or 0) > 0 else "red"
            h += "<tr>"
            h += f"<td><b>{r['token_nome']}</b></td>"
            h += f"<td class='{oc_cls}'>{oc}</td>"
            h += f"<td class='{pnl_cls}'>{pnl:+.0%}</td>" if pnl is not None else "<td>—</td>"
            h += f"<td>{r['entry_time']}s</td>"
            h += f"<td>{r['exit_at']}s</td>" if r.get("exit_at") else "<td>—</td>"
            h += f"<td>{r['pct_drop']:.0%}</td>" if r.get("pct_drop") else "<td>—</td>"
            h += f"<td>{r['time_to_bottom']:.1f}min</td>" if r.get("time_to_bottom") else "<td>—</td>"
            h += f"<td>{r['bot_degen_rate']:.0%}</td>" if r.get("bot_degen_rate") is not None else "<td>—</td>"
            h += f"<td>{r['rug_ratio']:.0%}</td>" if r.get("rug_ratio") is not None else "<td>—</td>"
            h += "</tr>"
        h += "</tbody></table></div>"

    h += "<div class='note' style='margin-top:12px'>"
    h += "<b>Legenda:</b><br>"
    h += "• <b>SL_imediato</b>: dump não parou no nível de entrada — candle perfurou diretamente o SL. Sniper chegou cedo demais.<br>"
    h += "• <b>SL</b>: entrou, preço devolveu. Fundo ainda não havia sido atingido.<br>"
    h += "• <b>TP</b>: entrou e bounce atingiu +75%.<br>"
    h += "• <b>exit</b>: sem TP nem SL — saída no último tick disponível."
    h += "</div>"

    h += "</body></html>"
    return h


# ==============================================================================
# /analise/bottom-score — Backtest retroativo do Bottom Score
# ==============================================================================

@app.route("/analise/bottom-score")
def analise_bottom_score():
    """
    Backtest retroativo do Bottom Score composto (5 métricas derivadas de token_ticks).

    Para cada token com ticks + recovery definido, calcula o score a cada janela
    de 12 ticks (60s) e mede:
    - Se Score >= threshold antecipou o fundo real
    - Discriminação recovery=TRUE vs FALSE
    - Lead time médio antes do fundo
    - Taxa de falso positivo por threshold

    Retorna JSON com resultados completos.
    """
    import math

    THRESHOLD   = 0.55   # threshold principal do score composto
    MIN_TICKS   = 8      # mínimo de ticks para o token ser incluído
    WINDOW      = 12     # janela de ticks para calcular o score (60s)
    MIN_DUMP    = 0.30   # só avalia depois que já caiu >=30% do mc_migration

    # Busca tokens com ticks + recovery + mc_migration + mc_bottom
    with db._cursor() as cur:
        cur.execute("""
            SELECT m.mint, m.token_nome, m.mc_migration, m.mc_bottom,
                   m.time_to_bottom, m.recovery, m.liq_migration,
                   COUNT(t.id) AS tick_count
            FROM migrations m
            JOIN token_ticks t ON t.mint = m.mint
            WHERE m.recovery IS NOT NULL
              AND m.mc_migration IS NOT NULL
              AND m.mc_bottom    IS NOT NULL
              AND m.mc_migration > 0
            GROUP BY m.mint, m.token_nome, m.mc_migration, m.mc_bottom,
                     m.time_to_bottom, m.recovery, m.liq_migration
            HAVING COUNT(t.id) >= %(min_ticks)s
            ORDER BY m.mint
        """, {"min_ticks": MIN_TICKS})
        tokens = [dict(r) for r in cur.fetchall()]

    if not tokens:
        return {"erro": "Nenhum token com ticks + recovery encontrado", "tokens_com_ticks": 0}, 200

    results_by_token = []

    for tok in tokens:
        mint         = tok["mint"]
        mc_mig       = float(tok["mc_migration"])
        mc_bot       = float(tok["mc_bottom"])
        liq_mig      = float(tok["liq_migration"] or 0)
        recovery     = bool(tok["recovery"])
        time_to_bot  = float(tok["time_to_bottom"] or 0)

        # Busca ticks ordenados por elapsed
        with db._cursor() as cur:
            cur.execute("""
                SELECT elapsed_sec, mc, volume_1m, volume_5m, volume_15m,
                       holders_count, liquidity
                FROM token_ticks
                WHERE mint = %(mint)s
                ORDER BY elapsed_sec ASC
            """, {"mint": mint})
            ticks = [dict(r) for r in cur.fetchall()]

        if len(ticks) < MIN_TICKS:
            continue

        # Calcula k (constante AMM) — aproximada via liq_migration e mc_migration
        k_amm = (liq_mig * mc_mig) if liq_mig > 0 and mc_mig > 0 else None

        first_signal_elapsed = None   # primeiro elapsed onde Score >= threshold
        scores_at_bottom     = []     # scores nos ticks ao redor do fundo real
        max_score_before_bot = 0.0

        for i in range(WINDOW, len(ticks)):
            window = ticks[i - WINDOW: i]
            cur_tick = ticks[i]
            mc_cur   = cur_tick.get("mc") or 0

            if mc_cur <= 0 or mc_mig <= 0:
                continue

            # Só avalia após dump mínimo
            if mc_cur > mc_mig * (1 - MIN_DUMP):
                continue

            mc_series   = [t.get("mc") or 0     for t in window]
            vol_1m      = [t.get("volume_1m") or 0 for t in window]
            vol_15m_raw = [t.get("volume_15m") or 0 for t in window]
            holders     = [t.get("holders_count") or 0 for t in window]

            # ── Componente 1: Price Acceleration (desaceleração do dump) ──
            # Compara velocidade dos últimos 4 ticks vs 4 anteriores
            pv_recent = (mc_series[-1] - mc_series[-4]) if len(mc_series) >= 4 else 0
            pv_prior  = (mc_series[-5] - mc_series[-8]) if len(mc_series) >= 8 else 0
            c1 = 1.0 if (pv_recent > pv_prior and pv_prior < 0) else 0.0

            # ── Componente 2: Volume secando (vol_1m atual < 60% da média) ──
            vol_avg = (sum(vol_1m[:-1]) / max(len(vol_1m) - 1, 1))
            c2 = 1.0 if (vol_avg > 0 and vol_1m[-1] < vol_avg * 0.60) else 0.0

            # ── Componente 3: Holders pararam de sair ──
            hold_delta = holders[-1] - holders[-3] if len(holders) >= 3 else 0
            c3 = 1.0 if hold_delta >= 0 else 0.0

            # ── Componente 4: VWAP desvio (preço em extremo oversold) ──
            # Aproximado: mc_cur está abaixo de qual percentil do mc histórico dos ticks
            mc_hist = [t.get("mc") or 0 for t in ticks[:i] if (t.get("mc") or 0) > 0]
            if len(mc_hist) >= 5:
                mc_mean = sum(mc_hist) / len(mc_hist)
                mc_std  = (sum((x - mc_mean)**2 for x in mc_hist) / len(mc_hist)) ** 0.5
                desvio  = (mc_cur - mc_mean) / mc_std if mc_std > 0 else 0
                c4 = 1.0 if desvio < -1.5 else 0.0
            else:
                c4 = 0.0

            # ── Componente 5: Price Impact AMM (pool extremamente rasa) ──
            liq_cur = cur_tick.get("liquidity") or liq_mig
            if k_amm and liq_cur > 0 and mc_cur > 0:
                try:
                    sol_reserve   = math.sqrt(k_amm * mc_cur)
                    new_sol       = sol_reserve + 100
                    new_mc        = (new_sol ** 2) / k_amm
                    price_impact  = (new_mc - mc_cur) / mc_cur
                    c5 = 1.0 if price_impact > 0.05 else 0.0
                except Exception:
                    c5 = 0.0
            else:
                c5 = 0.0

            score = 0.30*c1 + 0.25*c2 + 0.20*c3 + 0.15*c4 + 0.10*c5
            elapsed_cur = cur_tick.get("elapsed_sec") or 0

            # Registra primeiro sinal acima do threshold
            if score >= THRESHOLD and first_signal_elapsed is None:
                first_signal_elapsed = elapsed_cur

            # Score ao redor do fundo real (±60s do time_to_bottom)
            if abs(elapsed_cur / 60 - time_to_bot) <= 1.0:
                scores_at_bottom.append(score)

            if elapsed_cur / 60 <= time_to_bot:
                max_score_before_bot = max(max_score_before_bot, score)

        # Lead time: quanto antes do fundo o score dispara
        lead_sec = None
        if first_signal_elapsed is not None:
            lead_sec = time_to_bot * 60 - first_signal_elapsed

        results_by_token.append({
            "mint":                   mint,
            "symbol":                 tok["token_nome"],
            "recovery":               recovery,
            "mc_migration":           mc_mig,
            "mc_bottom":              mc_bot,
            "time_to_bottom_min":     time_to_bot,
            "tick_count":             tok["tick_count"],
            "first_signal_elapsed_s": first_signal_elapsed,
            "lead_time_sec":          lead_sec,
            "max_score_before_bot":   round(max_score_before_bot, 3),
            "score_at_bottom":        round(sum(scores_at_bottom)/len(scores_at_bottom), 3) if scores_at_bottom else None,
            "signal_fired":           first_signal_elapsed is not None,
        })

    if not results_by_token:
        return {"erro": "Nenhum token com ticks suficientes para análise"}, 200

    # ── Métricas de discriminação ──
    rec_true  = [r for r in results_by_token if r["recovery"]]
    rec_false = [r for r in results_by_token if not r["recovery"]]

    def _rate(lst, key, val):
        if not lst: return None
        return sum(1 for r in lst if r[key] == val) / len(lst)

    def _avg(lst, key):
        vals = [r[key] for r in lst if r[key] is not None]
        return round(sum(vals)/len(vals), 3) if vals else None

    # Taxa de disparo por grupo
    signal_rate_recovery = _rate(rec_true,  "signal_fired", True)
    signal_rate_no_rec   = _rate(rec_false, "signal_fired", True)

    # Score médio antes do fundo por grupo
    avg_score_recovery = _avg(rec_true,  "max_score_before_bot")
    avg_score_no_rec   = _avg(rec_false, "max_score_before_bot")

    # Lead time médio (apenas quando sinal disparou antes do fundo)
    lead_rec  = [r["lead_time_sec"] for r in rec_true  if (r["lead_time_sec"] or 0) > 0]
    lead_norec= [r["lead_time_sec"] for r in rec_false if (r["lead_time_sec"] or 0) > 0]

    # Sweep de thresholds
    threshold_sweep = []
    for thr in [0.30, 0.40, 0.50, 0.55, 0.60, 0.70, 0.80]:
        tp = sum(1 for r in rec_true  if r["max_score_before_bot"] >= thr)
        fp = sum(1 for r in rec_false if r["max_score_before_bot"] >= thr)
        fn = len(rec_true)  - tp
        tn = len(rec_false) - fp
        precision = tp / (tp + fp) if (tp + fp) > 0 else None
        recall    = tp / (tp + fn) if (tp + fn) > 0 else None
        f1        = (2 * precision * recall / (precision + recall)
                     if precision and recall and (precision + recall) > 0 else None)
        threshold_sweep.append({
            "threshold": thr,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(precision, 3) if precision else None,
            "recall":    round(recall,    3) if recall    else None,
            "f1":        round(f1,        3) if f1        else None,
        })

    return {
        "resumo": {
            "tokens_analisados":        len(results_by_token),
            "recovery_true":            len(rec_true),
            "recovery_false":           len(rec_false),
            "threshold_principal":      THRESHOLD,
        },
        "discriminacao": {
            "signal_rate_recovery":     round(signal_rate_recovery, 3) if signal_rate_recovery else None,
            "signal_rate_no_recovery":  round(signal_rate_no_rec,   3) if signal_rate_no_rec   else None,
            "delta_pp":                 round((signal_rate_recovery or 0) - (signal_rate_no_rec or 0), 3),
            "avg_max_score_recovery":   avg_score_recovery,
            "avg_max_score_no_rec":     avg_score_no_rec,
            "lead_time_recovery_p50_s": sorted(lead_rec)[len(lead_rec)//2]  if lead_rec  else None,
            "lead_time_recovery_avg_s": round(sum(lead_rec)/len(lead_rec))   if lead_rec  else None,
            "lead_time_norec_p50_s":    sorted(lead_norec)[len(lead_norec)//2] if lead_norec else None,
        },
        "threshold_sweep":   threshold_sweep,
        "por_token":         results_by_token,
    }, 200

# ==============================================================================
# /analise/smart-flow — Smart count delta durante o dump vs recovery
# ==============================================================================

@app.route("/analise/smart-flow")
def analise_smart_flow():
    """
    Analisa se smart_count (e renowned_count) sobem durante o dump — o sinal
    de que smart money está acumulando enquanto o preço cai.

    Para cada token com ticks + recovery, calcula:
    - delta_smart: variação de smart_count desde o início até o fundo
    - smart_entering: flag se smart_count subiu em ≥2 janelas de 4 ticks (20s)
    - dump_velocity: queda % de MC nos primeiros 2 minutos (preditor de tipo de dump)
    - Correlação de cada feature com recovery=TRUE

    Também retorna sweep de thresholds para delta_smart como sinal isolado.
    """
    WINDOW_SHORT = 4   # 4 ticks = 20s (janela curta)
    MIN_TICKS    = 8

    with db._cursor() as cur:
        cur.execute("""
            SELECT m.mint, m.token_nome, m.mc_migration, m.mc_bottom,
                   m.time_to_bottom, m.recovery, m.smart_wallets,
                   m.renowned_wallets, m.liq_migration,
                   COUNT(t.id) AS tick_count
            FROM migrations m
            JOIN token_ticks t ON t.mint = m.mint
            WHERE m.recovery IS NOT NULL
              AND m.mc_migration IS NOT NULL
              AND m.mc_bottom IS NOT NULL
              AND m.mc_migration > 0
            GROUP BY m.mint, m.token_nome, m.mc_migration, m.mc_bottom,
                     m.time_to_bottom, m.recovery, m.smart_wallets,
                     m.renowned_wallets, m.liq_migration
            HAVING COUNT(t.id) >= %(min_ticks)s
            ORDER BY m.mint
        """, {"min_ticks": MIN_TICKS})
        tokens = [dict(r) for r in cur.fetchall()]

    if not tokens:
        return {"erro": "Nenhum token com ticks + recovery"}, 200

    results = []

    for tok in tokens:
        mint        = tok["mint"]
        mc_mig      = float(tok["mc_migration"])
        mc_bot      = float(tok["mc_bottom"])
        recovery    = bool(tok["recovery"])
        time_to_bot = float(tok["time_to_bottom"] or 0)

        with db._cursor() as cur:
            cur.execute("""
                SELECT elapsed_sec, mc, smart_count, renowned_count,
                       volume_1m, holders_count
                FROM token_ticks
                WHERE mint = %(mint)s
                ORDER BY elapsed_sec ASC
            """, {"mint": mint})
            ticks = [dict(r) for r in cur.fetchall()]

        if len(ticks) < MIN_TICKS:
            continue

        # ── Dump velocity: queda % nos primeiros 2 minutos ──
        ticks_2min = [t for t in ticks if (t.get("elapsed_sec") or 0) <= 120]
        if len(ticks_2min) >= 2:
            mc_start = ticks_2min[0].get("mc") or mc_mig
            mc_2min  = ticks_2min[-1].get("mc") or mc_start
            dump_velocity_2min = (mc_start - mc_2min) / mc_start if mc_start > 0 else None
        else:
            dump_velocity_2min = None

        # ── Smart count: baseline (primeiro tick) e fundo ──
        smart_start    = ticks[0].get("smart_count") or 0
        renowned_start = ticks[0].get("renowned_count") or 0

        # Ticks ao redor do fundo real (±90s do time_to_bottom)
        bot_elapsed = time_to_bot * 60
        ticks_near_bot = [t for t in ticks
                          if abs((t.get("elapsed_sec") or 0) - bot_elapsed) <= 90]

        smart_at_bot    = ticks_near_bot[-1].get("smart_count") or 0    if ticks_near_bot else smart_start
        renowned_at_bot = ticks_near_bot[-1].get("renowned_count") or 0 if ticks_near_bot else renowned_start

        delta_smart    = smart_at_bot    - smart_start
        delta_renowned = renowned_at_bot - renowned_start

        # ── Smart entering: contagem de janelas de 4 ticks onde smart_count subiu ──
        smart_windows_up = 0
        for i in range(WINDOW_SHORT, len(ticks)):
            window = ticks[i - WINDOW_SHORT: i]
            sc = [t.get("smart_count") or 0 for t in window]
            if sc[-1] > sc[0]:
                smart_windows_up += 1

        total_windows   = max(len(ticks) - WINDOW_SHORT, 1)
        smart_entry_rate = smart_windows_up / total_windows  # fração de janelas com smart subindo

        # ── Pct dump total ──
        pct_dump = (mc_mig - mc_bot) / mc_mig if mc_mig > 0 else None

        results.append({
            "mint":               mint,
            "symbol":             tok["token_nome"],
            "recovery":           recovery,
            "time_to_bottom_min": time_to_bot,
            "pct_dump":           round(pct_dump, 3) if pct_dump else None,
            "dump_velocity_2min": round(dump_velocity_2min, 3) if dump_velocity_2min is not None else None,
            "smart_start":        smart_start,
            "smart_at_bottom":    smart_at_bot,
            "delta_smart":        delta_smart,
            "renowned_start":     renowned_start,
            "renowned_at_bot":    renowned_at_bot,
            "delta_renowned":     delta_renowned,
            "smart_entry_rate":   round(smart_entry_rate, 3),
            "smart_windows_up":   smart_windows_up,
            "total_windows":      total_windows,
            "tick_count":         tok["tick_count"],
        })

    if not results:
        return {"erro": "Nenhum resultado calculado"}, 200

    rec_true  = [r for r in results if r["recovery"]]
    rec_false = [r for r in results if not r["recovery"]]

    def _avg(lst, key):
        vals = [r[key] for r in lst if r.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    def _med(lst, key):
        vals = sorted([r[key] for r in lst if r.get(key) is not None])
        return vals[len(vals)//2] if vals else None

    # ── Sweep delta_smart como sinal ──
    delta_sweep = []
    for thr in [-2, -1, 0, 1, 2, 3, 5]:
        tp = sum(1 for r in rec_true  if r["delta_smart"] >= thr)
        fp = sum(1 for r in rec_false if r["delta_smart"] >= thr)
        fn = len(rec_true)  - tp
        tn = len(rec_false) - fp
        prec = tp / (tp + fp) if (tp + fp) > 0 else None
        rec  = tp / (tp + fn) if (tp + fn) > 0 else None
        f1   = (2*prec*rec/(prec+rec)) if prec and rec and (prec+rec) > 0 else None
        delta_sweep.append({
            "delta_smart_min": thr,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(prec, 3) if prec else None,
            "recall":    round(rec,  3) if rec  else None,
            "f1":        round(f1,   3) if f1   else None,
        })

    # ── Sweep smart_entry_rate como sinal ──
    rate_sweep = []
    for thr in [0.05, 0.10, 0.15, 0.20, 0.30, 0.40]:
        tp = sum(1 for r in rec_true  if r["smart_entry_rate"] >= thr)
        fp = sum(1 for r in rec_false if r["smart_entry_rate"] >= thr)
        fn = len(rec_true)  - tp
        tn = len(rec_false) - fp
        prec = tp / (tp + fp) if (tp + fp) > 0 else None
        rec  = tp / (tp + fn) if (tp + fn) > 0 else None
        f1   = (2*prec*rec/(prec+rec)) if prec and rec and (prec+rec) > 0 else None
        rate_sweep.append({
            "entry_rate_min": thr,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(prec, 3) if prec else None,
            "recall":    round(rec,  3) if rec  else None,
            "f1":        round(f1,   3) if f1   else None,
        })

    return {
        "resumo": {
            "tokens_analisados": len(results),
            "recovery_true":     len(rec_true),
            "recovery_false":    len(rec_false),
        },
        "discriminacao": {
            # delta_smart: variação de smart_count entre início e fundo
            "avg_delta_smart_recovery":    _avg(rec_true,  "delta_smart"),
            "avg_delta_smart_no_rec":      _avg(rec_false, "delta_smart"),
            "med_delta_smart_recovery":    _med(rec_true,  "delta_smart"),
            "med_delta_smart_no_rec":      _med(rec_false, "delta_smart"),
            # smart_entry_rate: fração de janelas 20s com smart subindo
            "avg_entry_rate_recovery":     _avg(rec_true,  "smart_entry_rate"),
            "avg_entry_rate_no_rec":       _avg(rec_false, "smart_entry_rate"),
            # dump_velocity: queda nos primeiros 2 minutos
            "avg_dump_vel_2min_recovery":  _avg(rec_true,  "dump_velocity_2min"),
            "avg_dump_vel_2min_no_rec":    _avg(rec_false, "dump_velocity_2min"),
            # time to bottom
            "avg_time_to_bot_recovery":    _avg(rec_true,  "time_to_bottom_min"),
            "avg_time_to_bot_no_rec":      _avg(rec_false, "time_to_bottom_min"),
            # pct_dump total
            "avg_pct_dump_recovery":       _avg(rec_true,  "pct_dump"),
            "avg_pct_dump_no_rec":         _avg(rec_false, "pct_dump"),
        },
        "delta_smart_sweep":   delta_sweep,
        "smart_entry_rate_sweep": rate_sweep,
        "por_token":           results,
    }, 200


# ==============================================================================
# /analise/tick-features — Análise completa de todas as colunas do token_ticks
# ==============================================================================

@app.route("/analise/tick-features")
def analise_tick_features():
    """
    Analisa TODAS as colunas do token_ticks como preditores de recovery:
    - delta_smart:    variação de smart_count (início → fundo)
    - delta_renowned: variação de renowned_count (KOLs)
    - combined_delta: smart + renowned juntos
    - delta_holders:  variação de holders_count (novos entrando = acumulação)
    - vol_spike:      volume_1m no fundo / baseline (capitulação de volume)
    - vol_5m_slope:   inclinação de volume_5m (secando = exaustão vendedora)
    - dump_pct:       queda % total até o fundo

    Retorna sweep de threshold para cada variável + discriminação comparativa.
    """
    MIN_TICKS = 8

    with db._cursor() as cur:
        cur.execute("""
            SELECT m.mint, m.token_nome, m.mc_migration, m.mc_bottom,
                   m.time_to_bottom, m.recovery, m.liq_migration,
                   COUNT(t.id) AS tick_count
            FROM migrations m
            JOIN token_ticks t ON t.mint = m.mint
            WHERE m.recovery IS NOT NULL
              AND m.mc_migration IS NOT NULL
              AND m.mc_bottom    IS NOT NULL
              AND m.mc_migration > 0
            GROUP BY m.mint, m.token_nome, m.mc_migration, m.mc_bottom,
                     m.time_to_bottom, m.recovery, m.liq_migration
            HAVING COUNT(t.id) >= %(min_ticks)s
            ORDER BY m.mint
        """, {"min_ticks": MIN_TICKS})
        tokens = [dict(r) for r in cur.fetchall()]

    if not tokens:
        return {"erro": "Nenhum token com ticks + recovery"}, 200

    results = []

    for tok in tokens:
        mint        = tok["mint"]
        mc_mig      = float(tok["mc_migration"])
        mc_bot      = float(tok["mc_bottom"])
        recovery    = bool(tok["recovery"])
        time_to_bot = float(tok["time_to_bottom"] or 0)

        with db._cursor() as cur:
            cur.execute("""
                SELECT elapsed_sec, mc, smart_count, renowned_count,
                       holders_count, volume_1m, volume_5m
                FROM token_ticks
                WHERE mint = %(mint)s
                ORDER BY elapsed_sec ASC
            """, {"mint": mint})
            ticks = [dict(r) for r in cur.fetchall()]

        if len(ticks) < MIN_TICKS:
            continue

        def _v(t, k): return float(t.get(k) or 0)

        # ── Baseline: primeiros 4 ticks (20s iniciais) ──
        baseline = ticks[:4]
        smart_base    = sum(_v(t, "smart_count")    for t in baseline) / len(baseline)
        renowned_base = sum(_v(t, "renowned_count") for t in baseline) / len(baseline)
        holders_base  = sum(_v(t, "holders_count")  for t in baseline) / len(baseline)
        vol1m_base    = sum(_v(t, "volume_1m")       for t in baseline) / len(baseline)
        vol5m_base    = sum(_v(t, "volume_5m")       for t in baseline) / len(baseline)

        # ── Ticks ao redor do fundo (±90s) ──
        bot_elapsed   = time_to_bot * 60
        near_bot      = [t for t in ticks if abs(_v(t, "elapsed_sec") - bot_elapsed) <= 90]
        if not near_bot:
            near_bot = [ticks[-1]]
        last_near_bot = near_bot[-1]

        smart_bot    = _v(last_near_bot, "smart_count")
        renowned_bot = _v(last_near_bot, "renowned_count")
        holders_bot  = _v(last_near_bot, "holders_count")
        vol1m_bot    = _v(last_near_bot, "volume_1m")

        # ── Deltas absolutos ──
        delta_smart    = smart_bot    - smart_base
        delta_renowned = renowned_bot - renowned_base
        delta_combined = delta_smart  + delta_renowned       # smart + KOL juntos
        delta_holders  = holders_bot  - holders_base

        # ── Vol spike: volume no fundo vs baseline ──
        vol_spike = (vol1m_bot / vol1m_base) if vol1m_base > 1 else None

        # ── Vol 5m slope: inclinação da última metade dos ticks ──
        mid = len(ticks) // 2
        vol5m_first_half = [_v(t, "volume_5m") for t in ticks[:mid] if _v(t, "volume_5m") > 0]
        vol5m_last_half  = [_v(t, "volume_5m") for t in ticks[mid:] if _v(t, "volume_5m") > 0]
        avg_vol5m_first  = sum(vol5m_first_half) / len(vol5m_first_half) if vol5m_first_half else None
        avg_vol5m_last   = sum(vol5m_last_half)  / len(vol5m_last_half)  if vol5m_last_half  else None
        vol5m_slope = None
        if avg_vol5m_first and avg_vol5m_last and avg_vol5m_first > 0:
            vol5m_slope = (avg_vol5m_last - avg_vol5m_first) / avg_vol5m_first  # negativo = secando

        # ── Pct dump ──
        pct_dump = (mc_mig - mc_bot) / mc_mig if mc_mig > 0 else None

        # ── Holders velocity: novos holders por minuto na janela ao redor do fundo ──
        first_20_ticks = ticks[:20]
        hold_start = _v(first_20_ticks[0],  "holders_count")
        hold_end   = _v(first_20_ticks[-1], "holders_count")
        elapsed_span = _v(first_20_ticks[-1], "elapsed_sec") - _v(first_20_ticks[0], "elapsed_sec")
        holders_vel_per_min = ((hold_end - hold_start) / (elapsed_span / 60)
                               if elapsed_span > 0 else None)

        results.append({
            "mint":              mint,
            "symbol":            tok["token_nome"],
            "recovery":          recovery,
            "time_to_bottom_min":time_to_bot,
            "pct_dump":          round(pct_dump,       3) if pct_dump is not None else None,
            "delta_smart":       round(delta_smart,    2),
            "delta_renowned":    round(delta_renowned, 2),
            "delta_combined":    round(delta_combined, 2),
            "delta_holders":     round(delta_holders,  2),
            "vol_spike":         round(vol_spike,      3) if vol_spike is not None else None,
            "vol5m_slope":       round(vol5m_slope,    3) if vol5m_slope is not None else None,
            "holders_vel_per_min": round(holders_vel_per_min, 2) if holders_vel_per_min is not None else None,
            "tick_count":        tok["tick_count"],
        })

    if not results:
        return {"erro": "Nenhum resultado"}, 200

    rec_true  = [r for r in results if r["recovery"]]
    rec_false = [r for r in results if not r["recovery"]]

    def _avg(lst, key):
        vals = [r[key] for r in lst if r.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    def _med(lst, key):
        vals = sorted([r[key] for r in lst if r.get(key) is not None])
        return round(vals[len(vals)//2], 3) if vals else None

    def _sweep(feature, thresholds, direction="above"):
        """Calcula precision/recall/F1 para cada threshold de uma feature."""
        rows = []
        for thr in thresholds:
            if direction == "above":
                tp = sum(1 for r in rec_true  if (r.get(feature) or 0) >= thr)
                fp = sum(1 for r in rec_false if (r.get(feature) or 0) >= thr)
            else:  # below (para vol5m_slope negativo = secando)
                tp = sum(1 for r in rec_true  if (r.get(feature) or 0) <= thr)
                fp = sum(1 for r in rec_false if (r.get(feature) or 0) <= thr)
            fn = len(rec_true)  - tp
            tn = len(rec_false) - fp
            prec = tp / (tp + fp) if (tp + fp) > 0 else None
            rec  = tp / (tp + fn) if (tp + fn) > 0 else None
            f1   = (2*prec*rec/(prec+rec)) if prec and rec and (prec+rec) > 0 else None
            rows.append({
                "threshold": thr, "direction": direction,
                "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                "precision": round(prec, 3) if prec else None,
                "recall":    round(rec,  3) if rec  else None,
                "f1":        round(f1,   3) if f1   else None,
            })
        return rows

    return {
        "resumo": {
            "tokens_analisados": len(results),
            "recovery_true":     len(rec_true),
            "recovery_false":    len(rec_false),
        },
        "discriminacao": {
            # smart money (wallets verificadas)
            "avg_delta_smart_recovery":       _avg(rec_true,  "delta_smart"),
            "avg_delta_smart_no_rec":         _avg(rec_false, "delta_smart"),
            "med_delta_smart_recovery":       _med(rec_true,  "delta_smart"),
            "med_delta_smart_no_rec":         _med(rec_false, "delta_smart"),
            # KOLs (renowned)
            "avg_delta_renowned_recovery":    _avg(rec_true,  "delta_renowned"),
            "avg_delta_renowned_no_rec":      _avg(rec_false, "delta_renowned"),
            "med_delta_renowned_recovery":    _med(rec_true,  "delta_renowned"),
            "med_delta_renowned_no_rec":      _med(rec_false, "delta_renowned"),
            # smart + KOL combinados
            "avg_delta_combined_recovery":    _avg(rec_true,  "delta_combined"),
            "avg_delta_combined_no_rec":      _avg(rec_false, "delta_combined"),
            # holders novos entrando durante dump
            "avg_delta_holders_recovery":     _avg(rec_true,  "delta_holders"),
            "avg_delta_holders_no_rec":       _avg(rec_false, "delta_holders"),
            "avg_holders_vel_recovery":       _avg(rec_true,  "holders_vel_per_min"),
            "avg_holders_vel_no_rec":         _avg(rec_false, "holders_vel_per_min"),
            # volume
            "avg_vol_spike_recovery":         _avg(rec_true,  "vol_spike"),
            "avg_vol_spike_no_rec":           _avg(rec_false, "vol_spike"),
            "avg_vol5m_slope_recovery":       _avg(rec_true,  "vol5m_slope"),
            "avg_vol5m_slope_no_rec":         _avg(rec_false, "vol5m_slope"),
            # timing (já sabemos que discrimina)
            "avg_time_to_bot_recovery":       _avg(rec_true,  "time_to_bottom_min"),
            "avg_time_to_bot_no_rec":         _avg(rec_false, "time_to_bottom_min"),
            "avg_pct_dump_recovery":          _avg(rec_true,  "pct_dump"),
            "avg_pct_dump_no_rec":            _avg(rec_false, "pct_dump"),
        },
        "sweeps": {
            "delta_smart":    _sweep("delta_smart",    [-1, 0, 1, 2, 3, 5]),
            "delta_renowned": _sweep("delta_renowned", [-1, 0, 1, 2, 3]),
            "delta_combined": _sweep("delta_combined", [0, 1, 2, 3, 5, 7]),
            "delta_holders":  _sweep("delta_holders",  [-50, 0, 50, 100, 200]),
            "vol_spike":      _sweep("vol_spike",      [0.5, 1.0, 1.5, 2.0, 3.0]),
            "vol5m_slope":    _sweep("vol5m_slope",    [0, -0.1, -0.2, -0.3, -0.5], direction="below"),
        },
        "por_token": results,
    }, 200


# ==============================================================================
# /analise/h10 — Teste formal de H10: time_to_bottom < 25min prediz recovery?
# ==============================================================================

@app.route("/analise/h10")
def analise_h10():
    """
    H10: tokens com time_to_bottom < 25min têm recovery rate ≥20pp maior
    que tokens com time_to_bottom ≥ 25min.

    Usa o banco completo (1015+ tokens) — não requer token_ticks.
    Também faz sweep de thresholds (10, 15, 20, 25, 30min) para encontrar
    o corte com maior discriminação.
    """
    with db._cursor() as cur:
        cur.execute("""
            SELECT time_to_bottom, recovery, mc_migration, mc_bottom,
                   pct_drop, smart_wallets, liq_migration
            FROM migrations
            WHERE recovery IS NOT NULL
              AND time_to_bottom IS NOT NULL
              AND mc_migration IS NOT NULL
              AND mc_migration > 0
        """)
        rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        return {"erro": "Nenhum dado"}, 200

    total       = len(rows)
    total_rec   = sum(1 for r in rows if r["recovery"])
    baseline_rate = total_rec / total  # recovery rate geral

    sweep = []
    for thr in [5, 10, 15, 20, 25, 30, 40]:
        fast = [r for r in rows if float(r["time_to_bottom"] or 0) <  thr]
        slow = [r for r in rows if float(r["time_to_bottom"] or 0) >= thr]
        if not fast or not slow:
            continue

        rec_fast = sum(1 for r in fast if r["recovery"])
        rec_slow = sum(1 for r in slow if r["recovery"])
        rate_fast = rec_fast / len(fast)
        rate_slow = rec_slow / len(slow)

        sweep.append({
            "threshold_min":    thr,
            "n_fast":           len(fast),
            "n_slow":           len(slow),
            "recovery_rate_fast": round(rate_fast, 3),
            "recovery_rate_slow": round(rate_slow, 3),
            "delta_pp":         round(rate_fast - rate_slow, 3),
            "h10_confirmed":    (rate_fast - rate_slow) >= 0.20,
        })

    # Resultado principal no threshold de 25min
    main = next((s for s in sweep if s["threshold_min"] == 25), None)

    return {
        "hipotese":         "H10: time_to_bottom < 25min → recovery rate ≥20pp maior",
        "dataset":          {"total": total, "recovery_true": total_rec,
                             "baseline_recovery_rate": round(baseline_rate, 3)},
        "resultado_25min":  main,
        "sweep_thresholds": sweep,
        "veredicto": (
            "CONFIRMADA" if main and main["h10_confirmed"] else
            "NEGADA"     if main else "SEM DADOS"
        ),
    }, 200


def _pct(n, total):
    return f"{n/total*100:.1f}%" if total else "—"

def _percentiles(values, ps=(25, 50, 75, 90)):
    if not values:
        return {p: None for p in ps}
    s = sorted(values)
    n = len(s)
    result = {}
    for p in ps:
        idx = (p / 100) * (n - 1)
        lo, hi = int(idx), min(int(idx) + 1, n - 1)
        result[p] = s[lo] + (idx - lo) * (s[hi] - s[lo])
    return result

def _mean(values):
    return statistics.mean(values) if values else None

def _run_analysis(rows):
    """Retorna dict com todos os resultados calculados."""
    r = {}

    # ── Dataset overview ──────────────────────────────────────────────────────
    total       = len(rows)
    has_bottom  = [x for x in rows if x["mc_bottom"] is not None]
    has_peak    = [x for x in rows if x["mc_peak"]   is not None]
    has_1h      = [x for x in rows if x["mc_1h"]     is not None]
    recovered   = [x for x in rows if x["recovery"] is True]
    not_recov   = [x for x in rows if x["recovery"] is False]
    complete    = [x for x in rows if x["mc_bottom"] is not None and x["mc_1h"] is not None and x["mc_bottom"] > 0]

    delays = [x["capture_delay_sec"] for x in rows if x["capture_delay_sec"] is not None]
    delay_p = _percentiles(delays) if delays else {}

    r["total"] = total
    r["has_bottom_n"] = len(has_bottom)
    r["has_peak_n"]   = len(has_peak)
    r["has_1h_n"]     = len(has_1h)
    r["complete_n"]   = len(complete)
    r["recovered_n"]  = len(recovered)
    r["not_recov_n"]  = len(not_recov)
    r["pending_n"]    = total - len(recovered) - len(not_recov)
    r["has_bottom_pct"] = _pct(len(has_bottom), total)
    r["has_1h_pct"]     = _pct(len(has_1h), total)
    r["complete_pct"]   = _pct(len(complete), total)
    r["delay_p50"] = f"{delay_p.get(50,0):.0f}s" if delay_p.get(50) else "—"
    r["delay_p75"] = f"{delay_p.get(75,0):.0f}s" if delay_p.get(75) else "—"
    r["delay_p90"] = f"{delay_p.get(90,0):.0f}s" if delay_p.get(90) else "—"

    # ── Timing ────────────────────────────────────────────────────────────────
    ttb_vals = [x["time_to_bottom"] for x in has_bottom if x["time_to_bottom"] is not None]
    ttp_vals = [x["time_to_peak"]   for x in has_peak   if x["time_to_peak"]   is not None]
    both     = [x for x in rows if x["time_to_bottom"] is not None and x["time_to_peak"] is not None]
    bounce   = [x for x in both if x["time_to_peak"] > x["time_to_bottom"]]

    ttb_p = _percentiles(ttb_vals)
    ttp_p = _percentiles(ttp_vals)

    r["ttb_n"]    = len(ttb_vals)
    r["ttb_p25"]  = f"{ttb_p[25]:.1f}min" if ttb_p.get(25) else "—"
    r["ttb_p50"]  = f"{ttb_p[50]:.1f}min" if ttb_p.get(50) else "—"
    r["ttb_p75"]  = f"{ttb_p[75]:.1f}min" if ttb_p.get(75) else "—"
    r["ttb_p90"]  = f"{ttb_p[90]:.1f}min" if ttb_p.get(90) else "—"
    r["ttb_u5"]   = _pct(sum(1 for v in ttb_vals if v <= 5),  len(ttb_vals))
    r["ttb_u10"]  = _pct(sum(1 for v in ttb_vals if v <= 10), len(ttb_vals))
    r["ttb_u20"]  = _pct(sum(1 for v in ttb_vals if v <= 20), len(ttb_vals))
    r["ttp_p50"]  = f"{ttp_p[50]:.1f}min" if ttp_p.get(50) else "—"
    r["ttp_p75"]  = f"{ttp_p[75]:.1f}min" if ttp_p.get(75) else "—"
    r["bounce_pct"]    = _pct(len(bounce), len(both)) if both else "—"
    r["no_bounce_pct"] = _pct(len(both) - len(bounce), len(both)) if both else "—"
    delta_vals = [x["time_to_peak"] - x["time_to_bottom"] for x in bounce]
    dp = _percentiles(delta_vals)
    r["bounce_dur_p50"] = f"{dp[50]:.1f}min" if dp.get(50) else "—"
    r["bounce_dur_p75"] = f"{dp[75]:.1f}min" if dp.get(75) else "—"

    # ── Dump buckets ──────────────────────────────────────────────────────────
    has_drop = [x for x in rows if x["pct_drop"] is not None and x["mc_bottom"] is not None]
    drop_vals = [x["pct_drop"] for x in has_drop]
    drop_p = _percentiles(drop_vals)
    over20 = sum(1 for v in drop_vals if v >= 0.20)

    r["drop_n"]    = len(drop_vals)
    r["drop_p25"]  = f"{drop_p[25]*100:.0f}%" if drop_p.get(25) else "—"
    r["drop_p50"]  = f"{drop_p[50]*100:.0f}%" if drop_p.get(50) else "—"
    r["drop_p75"]  = f"{drop_p[75]*100:.0f}%" if drop_p.get(75) else "—"
    r["drop_p90"]  = f"{drop_p[90]*100:.0f}%" if drop_p.get(90) else "—"
    r["over20_pct"] = _pct(over20, len(drop_vals))

    buckets_data = []
    for label, lo, hi in [("0–20%",0,.2),("20–40%",.2,.4),("40–60%",.4,.6),("60–80%",.6,.8),(">80%",.8,9.99)]:
        grp = [x for x in has_drop if lo <= x["pct_drop"] < hi]
        if not grp:
            continue
        recov_n = sum(1 for x in grp if x["recovery"] is True)
        rr_vals = [(x["mc_1h"]/x["mc_bottom"]-1) for x in grp if x["mc_1h"] and x["mc_bottom"] and x["mc_bottom"]>0]
        rr_p = _percentiles(rr_vals, (50,))
        win_n = sum(1 for v in rr_vals if v > 0)
        buckets_data.append({
            "label":   label,
            "n":       len(grp),
            "recov":   _pct(recov_n, len(grp)),
            "rr_med":  f"{rr_p[50]*100:+.1f}%" if rr_p.get(50) else "—",
            "win":     _pct(win_n, len(rr_vals)) if rr_vals else "—",
        })
    r["buckets"] = buckets_data

    # ── Filtros (H3/H4/H5/H8) ────────────────────────────────────────────────
    scored = [x for x in rows if x["recovery"] is not None and x["mc_bottom"] is not None and x["mc_1h"] is not None and x["mc_bottom"]>0]

    def _wr_threshold(grp, entry_pct=0.0):
        """Win = mc_1h > mc_bottom * (1 + entry_pct). entry_pct simula slippage/fee de entrada."""
        if not grp: return None, 0
        w = sum(1 for x in grp if x["mc_1h"] > x["mc_bottom"] * (1 + entry_pct))
        return w / len(grp), len(grp)

    # Baseline com entrada realista de 5% acima do fundo (slippage + fee)
    base_wr, base_n = _wr_threshold(scored, entry_pct=0.05)
    r["base_wr"] = f"{base_wr*100:.1f}%" if base_wr is not None else "—"
    r["base_n"]  = base_n
    r["base_note"] = "entrada +5% do fundo (slippage + fee realista)"

    filter_rows = []
    combos = [
        ("H3 — smart ≥ 3",         lambda x: (x["smart_wallets"] or 0) >= 3),
        ("H3 — smart < 3",          lambda x: x["smart_wallets"] is not None and (x["smart_wallets"] or 0) < 3),
        ("H4 — sniper ≥ 3",         lambda x: (x["sniper_wallets"] or 0) >= 3),
        ("H4 — sniper < 3",         lambda x: x["sniper_wallets"] is not None and (x["sniper_wallets"] or 0) < 3),
        ("H5 — top10 ≥ 80%",        lambda x: x["top10_holder_rate"] is not None and x["top10_holder_rate"] >= 0.80),
        ("H5 — top10 < 80%",        lambda x: x["top10_holder_rate"] is not None and x["top10_holder_rate"] < 0.80),
        ("H8 — wash=TRUE",          lambda x: x["is_wash_trading"] is True),
        ("H8 — wash=FALSE",         lambda x: x["is_wash_trading"] is False),
        ("rug ≤ 15%",               lambda x: x["rug_ratio"] is not None and x["rug_ratio"] <= 0.15),
        ("dump ≥ 20%",              lambda x: (x["pct_drop"] or 0) >= 0.20),
        ("dump 20–60%",             lambda x: 0.20 <= (x["pct_drop"] or 0) < 0.60),
        ("smart≥2 + wash=FALSE",    lambda x: (x["smart_wallets"] or 0) >= 2 and x["is_wash_trading"] is False),
        ("smart≥2 + dump≥20%",      lambda x: (x["smart_wallets"] or 0) >= 2 and (x["pct_drop"] or 0) >= 0.20),
        ("smart≥1 + top10<80% + wash=FALSE",
                                     lambda x: (x["smart_wallets"] or 0) >= 1
                                           and x["top10_holder_rate"] is not None and x["top10_holder_rate"] < 0.80
                                           and x["is_wash_trading"] is False),
        ("smart≥2 + rug≤15% + dump≥20%",
                                     lambda x: (x["smart_wallets"] or 0) >= 2
                                           and x["rug_ratio"] is not None and x["rug_ratio"] <= 0.15
                                           and (x["pct_drop"] or 0) >= 0.20),
    ]
    for label, fn in combos:
        grp = [x for x in scored if fn(x)]
        w0, n  = _wr_threshold(grp, entry_pct=0.00)
        w5, _  = _wr_threshold(grp, entry_pct=0.05)
        if w0 is None:
            continue
        delta0 = (w0 - (base_wr or 0)) * 100
        delta5 = (w5 - (base_wr or 0)) * 100 if w5 is not None else 0
        filter_rows.append({
            "label":  label,
            "n":      n,
            "wr0":    f"{w0*100:.1f}%",
            "wr5":    f"{w5*100:.1f}%" if w5 is not None else "—",
            "delta5": f"{delta5:+.1f}pp",
            "pos":    delta5 > 0,
        })
    r["filter_rows"] = filter_rows

    # ── R:R simulado ──────────────────────────────────────────────────────────
    tradeable = [x for x in rows if x["mc_bottom"] and x["mc_1h"] and x["mc_bottom"]>0 and x["pct_drop"] is not None]
    if tradeable:
        gains  = [(x["mc_1h"]/x["mc_bottom"]-1) for x in tradeable]
        wins   = [g for g in gains if g > 0]
        losses = [g for g in gains if g <= 0]
        wr     = len(wins)/len(gains)
        p_g    = _percentiles(gains)
        avg_w  = _mean(wins)
        avg_l  = abs(_mean(losses)) if losses else None
        payoff = avg_w/avg_l if avg_w and avg_l else None
        ev     = wr*avg_w - (1-wr)*avg_l if avg_w and avg_l else None

        r["rr_n"]      = len(tradeable)
        r["rr_wr"]     = f"{wr*100:.1f}%"
        r["rr_avg_w"]  = f"{avg_w*100:+.1f}%" if avg_w else "—"
        r["rr_avg_l"]  = f"{_mean(losses)*100:+.1f}%" if losses else "—"
        r["rr_p50"]    = f"{p_g[50]*100:+.1f}%" if p_g.get(50) else "—"
        r["rr_p75"]    = f"{p_g[75]*100:+.1f}%" if p_g.get(75) else "—"
        r["rr_p90"]    = f"{p_g[90]*100:+.1f}%" if p_g.get(90) else "—"
        r["rr_payoff"] = f"{payoff:.2f}×" if payoff else "—"
        r["rr_ev"]     = f"{ev*100:+.1f}%" if ev is not None else "—"
        r["rr_ev_pos"] = ev is not None and ev > 0

        deep = [x for x in tradeable if x["pct_drop"] >= 0.20]
        if deep:
            g2 = [(x["mc_1h"]/x["mc_bottom"]-1) for x in deep]
            w2 = [g for g in g2 if g > 0]
            l2 = [g for g in g2 if g <= 0]
            wr2 = len(w2)/len(g2)
            p2  = _percentiles(g2)
            aw2 = _mean(w2); al2 = abs(_mean(l2)) if l2 else None
            ev2 = wr2*aw2 - (1-wr2)*al2 if aw2 and al2 else None
            r["rr_deep_n"]      = len(deep)
            r["rr_deep_wr"]     = f"{wr2*100:.1f}%"
            r["rr_deep_p50"]    = f"{p2[50]*100:+.1f}%" if p2.get(50) else "—"
            r["rr_deep_p75"]    = f"{p2[75]*100:+.1f}%" if p2.get(75) else "—"
            r["rr_deep_payoff"] = f"{aw2/al2:.2f}×" if aw2 and al2 else "—"
            r["rr_deep_ev"]     = f"{ev2*100:+.1f}%" if ev2 is not None else "—"
            r["rr_deep_ev_pos"] = ev2 is not None and ev2 > 0
        else:
            r["rr_deep_n"] = 0
    else:
        r["rr_n"] = 0

    # ── TP/SL derivado dos dados reais ──────────────────────────────────────
    # Tokens com bounce confirmado: pico veio DEPOIS do fundo
    bounce_tokens = [
        x for x in rows
        if x.get("mc_bottom") and x.get("mc_peak")
        and x.get("time_to_bottom") is not None
        and x.get("time_to_peak")   is not None
        and x["time_to_peak"] > x["time_to_bottom"]
        and x["mc_bottom"] > 0
    ]
    no_bounce_tokens = [
        x for x in rows
        if x.get("mc_bottom") and x.get("mc_peak")
        and x.get("time_to_bottom") is not None
        and x.get("time_to_peak")   is not None
        and x["time_to_peak"] <= x["time_to_bottom"]
        and x["mc_bottom"] > 0
    ]

    r["bounce_n"]    = len(bounce_tokens)
    r["no_bounce_n"] = len(no_bounce_tokens)
    r["both_n"]      = len(bounce_tokens) + len(no_bounce_tokens)

    if bounce_tokens:
        # Magnitude do bounce: quanto subiu do fundo ao pico (%)
        bounce_mags = [(x["mc_peak"] / x["mc_bottom"] - 1) for x in bounce_tokens]
        bp = _percentiles(bounce_mags, (10, 25, 50, 75, 90))
        r["bounce_mag_p10"] = f"{bp[10]*100:.0f}%"  if bp.get(10) else "—"
        r["bounce_mag_p25"] = f"{bp[25]*100:.0f}%"  if bp.get(25) else "—"
        r["bounce_mag_p50"] = f"{bp[50]*100:.0f}%"  if bp.get(50) else "—"
        r["bounce_mag_p75"] = f"{bp[75]*100:.0f}%"  if bp.get(75) else "—"
        r["bounce_mag_p90"] = f"{bp[90]*100:.0f}%"  if bp.get(90) else "—"

        # Taxa de acerto por nível de TP — dados reais
        tp_rows = []
        for tp_pct, label in [
            (0.10, "+10%"), (0.20, "+20%"), (0.30, "+30%"),
            (0.50, "+50%"), (0.75, "+75%"), (1.00, "+100%"),
            (1.50, "+150%"), (2.00, "+200%"),
        ]:
            # Quantos tokens com bounce teriam batido esse TP?
            hit_bounce = sum(1 for m in bounce_mags if m >= tp_pct)
            # EV aproximado: se bate TP ganha tp_pct, se não bate perde até o fundo
            # Loss estimada: tokens sem bounce — perde a diferença para mc_1h ou trava no stop
            hit_rate = hit_bounce / len(bounce_mags)
            # EV simplificado: apenas sobre tokens com bounce (os sem bounce = perda separada)
            ev_bounce_only = hit_rate * tp_pct - (1 - hit_rate) * _mean(
                [max(0, tp_pct - m) for m in bounce_mags if m < tp_pct] or [0]
            )
            tp_rows.append({
                "tp":        label,
                "hit_n":     hit_bounce,
                "hit_rate":  f"{hit_rate*100:.1f}%",
                "hit_raw":   hit_rate,
                "ev_approx": f"{ev_bounce_only*100:+.1f}%",
                "ev_pos":    ev_bounce_only > 0,
            })
        r["tp_rows"] = tp_rows

        # Janela de oportunidade: tempo entre fundo e pico
        window_vals = [x["time_to_peak"] - x["time_to_bottom"] for x in bounce_tokens]
        wp = _percentiles(window_vals, (25, 50, 75, 90))
        r["window_p25"] = f"{wp[25]:.1f}min" if wp.get(25) else "—"
        r["window_p50"] = f"{wp[50]:.1f}min" if wp.get(50) else "—"
        r["window_p75"] = f"{wp[75]:.1f}min" if wp.get(75) else "—"
        r["window_p90"] = f"{wp[90]:.1f}min" if wp.get(90) else "—"
        r["window_u5"]  = _pct(sum(1 for v in window_vals if v <= 5),  len(window_vals))
        r["window_u10"] = _pct(sum(1 for v in window_vals if v <= 10), len(window_vals))
        r["window_u15"] = _pct(sum(1 for v in window_vals if v <= 15), len(window_vals))

        # SL estimado via downside nos tokens SEM bounce
        # Se você entra e o bounce não acontece, quanto perde relativo ao fundo?
        # Proxy: (mc_1h / mc_bottom - 1) nos tokens sem bounce
        no_bounce_with_1h = [
            x for x in no_bounce_tokens
            if x.get("mc_1h") and x["mc_bottom"] > 0
        ]
        if no_bounce_with_1h:
            sl_vals = [(x["mc_1h"] / x["mc_bottom"] - 1) for x in no_bounce_with_1h]
            sp = _percentiles(sl_vals, (10, 25, 50))
            r["sl_p10"] = f"{sp[10]*100:+.1f}%" if sp.get(10) else "—"
            r["sl_p25"] = f"{sp[25]*100:+.1f}%" if sp.get(25) else "—"
            r["sl_p50"] = f"{sp[50]*100:+.1f}%" if sp.get(50) else "—"
            r["sl_n"]   = len(no_bounce_with_1h)
        else:
            r["sl_n"] = 0

        # R:R ótimo: cruza tp_hit_rate × magnitude_media / loss_media_sem_bounce
        if no_bounce_with_1h:
            avg_loss_no_bounce = abs(_mean([
                (x["mc_1h"] / x["mc_bottom"] - 1) for x in no_bounce_with_1h
            ]))
            rr_optimal = []
            total_tokens = len(bounce_tokens) + len(no_bounce_tokens)
            bounce_rate  = len(bounce_tokens) / total_tokens if total_tokens else 0
            for tp_pct, label in [(0.20,"+20%"),(0.30,"+30%"),(0.50,"+50%"),(1.00,"+100%")]:
                tp_hit_given_bounce = sum(1 for m in bounce_mags if m >= tp_pct) / len(bounce_mags)
                real_win_rate = bounce_rate * tp_hit_given_bounce
                avg_win = tp_pct
                avg_loss = avg_loss_no_bounce * (1 - bounce_rate) + tp_pct * bounce_rate * (1 - tp_hit_given_bounce)
                ev = real_win_rate * avg_win - (1 - real_win_rate) * avg_loss_no_bounce
                rr_optimal.append({
                    "tp":      label,
                    "win_rate": f"{real_win_rate*100:.1f}%",
                    "ev":       f"{ev*100:+.1f}%",
                    "ev_pos":   ev > 0,
                    "payoff":   f"{avg_win/avg_loss_no_bounce:.2f}×" if avg_loss_no_bounce else "—",
                })
            r["rr_optimal"] = rr_optimal
        else:
            r["rr_optimal"] = []
    else:
        r["tp_rows"] = []
        r["rr_optimal"] = []
        r["sl_n"] = 0

    # ── H2 ───────────────────────────────────────────────────────────────────
    rec_ok = [x for x in rows if x["recovery"] is True  and x["time_to_bottom"] is not None]
    rec_no = [x for x in rows if x["recovery"] is False and x["time_to_bottom"] is not None]
    if rec_ok:
        p_ok = _percentiles([x["time_to_bottom"] for x in rec_ok])
        u15_ok = sum(1 for x in rec_ok if x["time_to_bottom"] <= 15)
        r["h2_ok_n"]   = len(rec_ok)
        r["h2_ok_p50"] = f"{p_ok[50]:.1f}min" if p_ok.get(50) else "—"
        r["h2_ok_p75"] = f"{p_ok[75]:.1f}min" if p_ok.get(75) else "—"
        r["h2_ok_u15"] = _pct(u15_ok, len(rec_ok))
    else:
        r["h2_ok_n"] = 0
    if rec_no:
        p_no = _percentiles([x["time_to_bottom"] for x in rec_no])
        u15_no = sum(1 for x in rec_no if x["time_to_bottom"] <= 15)
        r["h2_no_n"]   = len(rec_no)
        r["h2_no_p50"] = f"{p_no[50]:.1f}min" if p_no.get(50) else "—"
        r["h2_no_p75"] = f"{p_no[75]:.1f}min" if p_no.get(75) else "—"
        r["h2_no_u15"] = _pct(u15_no, len(rec_no))
    else:
        r["h2_no_n"] = 0

    # ── Fase 1 — cobertura dos novos campos ───────────────────────────────────
    fase1_fields = [
        ("creator_token_status",  "Creator status"),
        ("creator_balance_rate",  "Creator balance rate"),
        ("dev_team_hold_rate",    "Dev team hold rate"),
        ("suspected_insider_rate","Insider hold rate"),
        ("top70_sniper_hold_rate","Top70 sniper hold rate"),
        ("bluechip_owner_pct",    "Bluechip owner %"),
        ("bot_degen_rate",        "Bot degen rate"),
        ("entrapment_ratio",      "Entrapment ratio"),
        ("lock_percent",          "Lock %"),
        ("burn_status",           "Burn status"),
        ("cto_flag",              "CTO flag"),
        ("has_social",            "Has social"),
        ("x_follower_count",      "X followers"),
    ]
    fase1_rows = []
    total_mig = [x for x in rows if x.get("ts_migration") is not None]
    for col, label in fase1_fields:
        filled = [x for x in total_mig if x.get(col) is not None]
        fase1_rows.append({
            "label": label,
            "filled": len(filled),
            "total":  len(total_mig),
            "pct":    _pct(len(filled), len(total_mig)),
            "ok":     len(filled) > 0,
        })
    r["fase1_rows"]  = fase1_rows
    r["fase1_total"] = len(total_mig)

    # creator_token_status distribuição
    cts = [x["creator_token_status"] for x in total_mig if x.get("creator_token_status")]
    r["cts_hold"]  = cts.count("creator_hold")
    r["cts_close"] = cts.count("creator_close")
    r["cts_total"] = len(cts)

    # creator_close vs recovery
    scored = [x for x in rows if x["recovery"] is not None and x.get("mc_bottom") and x.get("mc_1h") and x["mc_bottom"] > 0]
    close_grp = [x for x in scored if x.get("creator_token_status") == "creator_close"]
    hold_grp  = [x for x in scored if x.get("creator_token_status") == "creator_hold"]
    def _wr_simple(grp):
        if not grp: return None
        return sum(1 for x in grp if x["mc_1h"] > x["mc_bottom"]) / len(grp)
    r["cts_close_wr"] = f"{_wr_simple(close_grp)*100:.1f}%" if _wr_simple(close_grp) else "—"
    r["cts_hold_wr"]  = f"{_wr_simple(hold_grp)*100:.1f}%"  if _wr_simple(hold_grp)  else "—"
    r["cts_close_n"]  = len(close_grp)
    r["cts_hold_n"]   = len(hold_grp)

    # ── Fase 1 — correlação com win rate ─────────────────────────────────────
    # Base: tokens com campos Fase 1 + bottom + 1h preenchidos
    f1_base = [x for x in rows
               if x.get("mc_bottom") and x.get("mc_1h") and x["mc_bottom"] > 0
               and x.get("creator_token_status") is not None]  # proxy: tem ao menos 1 campo novo

    def _wr_delta(grp, base):
        if not grp or not base: return None, None, 0
        w = sum(1 for x in grp if x["mc_1h"] > x["mc_bottom"])
        wr = w / len(grp)
        bw = sum(1 for x in base if x["mc_1h"] > x["mc_bottom"])
        base_wr = bw / len(base)
        return wr, (wr - base_wr) * 100, len(grp)

    f1_corr = []

    # creator_token_status
    for label, fn in [
        ("creator_close (dev vendeu)", lambda x: x.get("creator_token_status") == "creator_close"),
        ("creator_hold  (dev segura)", lambda x: x.get("creator_token_status") == "creator_hold"),
    ]:
        grp = [x for x in f1_base if fn(x)]
        wr, delta, n = _wr_delta(grp, f1_base)
        if wr is not None:
            f1_corr.append({"label": label, "n": n,
                            "wr": f"{wr*100:.1f}%",
                            "delta": f"{delta:+.1f}pp",
                            "pos": delta > 0})

    # bluechip_owner_pct — buckets
    for label, lo, hi in [("<5%", 0, .05), ("5–15%", .05, .15), (">15%", .15, 9)]:
        grp = [x for x in f1_base
               if x.get("bluechip_owner_pct") is not None
               and lo <= x["bluechip_owner_pct"] < hi]
        wr, delta, n = _wr_delta(grp, f1_base)
        if wr is not None:
            f1_corr.append({"label": f"bluechip_owner {label}", "n": n,
                            "wr": f"{wr*100:.1f}%",
                            "delta": f"{delta:+.1f}pp",
                            "pos": delta > 0})

    # suspected_insider_rate — baixo vs alto
    for label, fn in [
        ("insider_rate ≤10%", lambda x: x.get("suspected_insider_rate") is not None and x["suspected_insider_rate"] <= .10),
        ("insider_rate >10%", lambda x: x.get("suspected_insider_rate") is not None and x["suspected_insider_rate"] >  .10),
    ]:
        grp = [x for x in f1_base if fn(x)]
        wr, delta, n = _wr_delta(grp, f1_base)
        if wr is not None:
            f1_corr.append({"label": label, "n": n,
                            "wr": f"{wr*100:.1f}%",
                            "delta": f"{delta:+.1f}pp",
                            "pos": delta > 0})

    # dev_team_hold_rate
    for label, fn in [
        ("dev_hold ≤5%",  lambda x: x.get("dev_team_hold_rate") is not None and x["dev_team_hold_rate"] <= .05),
        ("dev_hold >5%",  lambda x: x.get("dev_team_hold_rate") is not None and x["dev_team_hold_rate"] >  .05),
    ]:
        grp = [x for x in f1_base if fn(x)]
        wr, delta, n = _wr_delta(grp, f1_base)
        if wr is not None:
            f1_corr.append({"label": label, "n": n,
                            "wr": f"{wr*100:.1f}%",
                            "delta": f"{delta:+.1f}pp",
                            "pos": delta > 0})

    # top70_sniper_hold_rate
    for label, fn in [
        ("top70_sniper ≤20%", lambda x: x.get("top70_sniper_hold_rate") is not None and x["top70_sniper_hold_rate"] <= .20),
        ("top70_sniper >20%", lambda x: x.get("top70_sniper_hold_rate") is not None and x["top70_sniper_hold_rate"] >  .20),
    ]:
        grp = [x for x in f1_base if fn(x)]
        wr, delta, n = _wr_delta(grp, f1_base)
        if wr is not None:
            f1_corr.append({"label": label, "n": n,
                            "wr": f"{wr*100:.1f}%",
                            "delta": f"{delta:+.1f}pp",
                            "pos": delta > 0})

    # bot_degen_rate
    for label, fn in [
        ("bot_degen ≤10%", lambda x: x.get("bot_degen_rate") is not None and x["bot_degen_rate"] <= .10),
        ("bot_degen >10%", lambda x: x.get("bot_degen_rate") is not None and x["bot_degen_rate"] >  .10),
    ]:
        grp = [x for x in f1_base if fn(x)]
        wr, delta, n = _wr_delta(grp, f1_base)
        if wr is not None:
            f1_corr.append({"label": label, "n": n,
                            "wr": f"{wr*100:.1f}%",
                            "delta": f"{delta:+.1f}pp",
                            "pos": delta > 0})

    # has_social / cto_flag
    for label, fn in [
        ("has_social = TRUE",  lambda x: x.get("has_social") is True),
        ("has_social = FALSE", lambda x: x.get("has_social") is False),
        ("cto_flag = TRUE",    lambda x: x.get("cto_flag") is True),
    ]:
        grp = [x for x in f1_base if fn(x)]
        wr, delta, n = _wr_delta(grp, f1_base)
        if wr is not None:
            f1_corr.append({"label": label, "n": n,
                            "wr": f"{wr*100:.1f}%",
                            "delta": f"{delta:+.1f}pp",
                            "pos": delta > 0})

    r["f1_base_n"]  = len(f1_base)
    r["f1_base_wr"] = f"{(sum(1 for x in f1_base if x['mc_1h'] > x['mc_bottom'] * 1.05) / len(f1_base) * 100):.1f}%" if f1_base else "—"
    r["f1_corr"]    = f1_corr

    # ── Cenários de entrada realista ─────────────────────────────────────────
    tradeable = [x for x in rows if x.get("mc_bottom") and x.get("mc_1h") and x["mc_bottom"] > 0]
    realistic_scenarios = []
    for label, entry_pct in [
        ("Fundo exato (teórico — impossível na prática)", 0.00),
        ("Fundo +2%  (slippage mínimo)",                 0.02),
        ("Fundo +5%  (slippage + fee — realista)",       0.05),
        ("Fundo +10% (entrada conservadora)",            0.10),
        ("Fundo +15% (entrada tardia)",                  0.15),
    ]:
        if not tradeable:
            continue
        gains  = [(x["mc_1h"] / (x["mc_bottom"] * (1 + entry_pct)) - 1) for x in tradeable]
        wins   = [g for g in gains if g > 0]
        losses = [g for g in gains if g <= 0]
        wr     = len(wins) / len(gains)
        avg_w  = _mean(wins)
        avg_l  = abs(_mean(losses)) if losses else None
        payoff = avg_w / avg_l if avg_w and avg_l else None
        ev     = wr * avg_w - (1 - wr) * avg_l if avg_w and avg_l else None
        realistic_scenarios.append({
            "label":    label,
            "n":        len(tradeable),
            "wr":       f"{wr*100:.1f}%",
            "wr_float": wr,
            "avg_w":    f"{avg_w*100:+.1f}%" if avg_w else "—",
            "avg_l":    f"{_mean(losses)*100:+.1f}%" if losses else "—",
            "payoff":   f"{payoff:.2f}×" if payoff else "—",
            "ev":       f"{ev*100:+.1f}%" if ev is not None else "—",
            "ev_pos":   ev is not None and ev > 0,
        })
    r["realistic_scenarios"] = realistic_scenarios

    # ── Série temporal — análise dos primeiros 10min ──────────────────────────
    try:
        tick_agg = db.get_tick_aggregates(max_elapsed_sec=600)
    except Exception:
        tick_agg = []

    r["ts_n_tokens"] = len(tick_agg)
    if tick_agg:
        with_rec    = [x for x in tick_agg if x.get("recovery") is True]
        without_rec = [x for x in tick_agg if x.get("recovery") is False]
        r["ts_has_recovery_n"]        = len(with_rec)
        r["ts_no_recovery_n"]         = len(without_rec)

        def _mn(lst, key):
            vals = [x[key] for x in lst if x.get(key) is not None]
            return _mean(vals)

        def _fmtv(v, fmt):
            return (fmt % v) if v is not None else "—"

        r["ts_avg_smart_recovery"]    = _fmtv(_mn(with_rec,    'avg_smart'),    "%.2f")
        r["ts_avg_smart_no"]          = _fmtv(_mn(without_rec, 'avg_smart'),    "%.2f")
        r["ts_delta_smart_recovery"]  = _fmtv(_mn(with_rec,    'delta_smart'),  "%.1f")
        r["ts_delta_smart_no"]        = _fmtv(_mn(without_rec, 'delta_smart'),  "%.1f")
        _vol_rec = _mn(with_rec,    'avg_vol_1m')
        _vol_no  = _mn(without_rec, 'avg_vol_1m')
        r["ts_avg_vol_recovery"]      = ("$%,.0f" % _vol_rec) if _vol_rec is not None else "—"
        r["ts_avg_vol_no"]            = ("$%,.0f" % _vol_no)  if _vol_no  is not None else "—"
        r["ts_avg_holders_recovery"]  = _fmtv(_mn(with_rec,    'avg_holders'),  "%.0f")
        r["ts_avg_holders_no"]        = _fmtv(_mn(without_rec, 'avg_holders'),  "%.0f")
        # Exchange breakdown
        exchanges = {}
        for x in tick_agg:
            exch = x.get("exchange") or "unknown"
            if exch not in exchanges:
                exchanges[exch] = {"n": 0, "rec": 0}
            exchanges[exch]["n"] += 1
            if x.get("recovery") is True:
                exchanges[exch]["rec"] += 1
        r["ts_exchanges"] = [
            {"exch": k, "n": v["n"], "rec_pct": f"{v['rec']/v['n']*100:.0f}%" if v["n"] else "—"}
            for k, v in sorted(exchanges.items(), key=lambda kv: -kv[1]["n"])
        ]
    else:
        r["ts_has_recovery_n"] = r["ts_no_recovery_n"] = 0
        r["ts_exchanges"] = []

    return r


ANALISE_HTML = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<title>NeideBOT — Análise</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Courier New', monospace; background: #0d1117; color: #c9d1d9;
       padding: 20px; font-size: 13px; }
h1   { color: #58a6ff; margin-bottom: 4px; font-size: 20px; }
h2   { color: #58a6ff; font-size: 14px; margin: 28px 0 10px; border-bottom: 1px solid #30363d; padding-bottom: 6px; }
.sub { color: #8b949e; margin-bottom: 24px; font-size: 12px; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; margin-bottom: 8px; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 10px 14px; }
.card-label { color: #8b949e; font-size: 10px; text-transform: uppercase; letter-spacing:.05em; }
.card-value { font-size: 20px; font-weight: bold; margin-top: 3px; }
.green { color: #3fb950; } .red { color: #f85149; } .yellow { color: #d29922; } .muted { color: #8b949e; }
.row  { display: flex; gap: 40px; flex-wrap: wrap; margin-bottom: 6px; }
.kv   { display: flex; flex-direction: column; }
.kv span:first-child { color: #8b949e; font-size: 11px; text-transform: uppercase; }
.kv span:last-child  { font-size: 14px; font-weight: bold; }
table { border-collapse: collapse; width: 100%; }
th { background: #161b22; color: #8b949e; padding: 7px 12px; text-align: left;
     border-bottom: 2px solid #30363d; font-size: 11px; text-transform: uppercase; letter-spacing:.05em; }
td { padding: 6px 12px; border-bottom: 1px solid #21262d; }
tr:hover td { background: #161b22; }
.tag { border-radius: 4px; padding: 2px 8px; font-size: 11px; font-weight: bold; display: inline-block; }
.t-green  { background: #1a3a2a; color: #3fb950; }
.t-red    { background: #3a1a1a; color: #f85149; }
.t-yellow { background: #3a2a1a; color: #d29922; }
.sep { border-top: 1px solid #30363d; margin: 20px 0; }
.note { color: #8b949e; font-size: 11px; margin-top: 6px; }
</style>
</head>
<body>
<h1>NeideBOT — Análise Exploratória</h1>
<p class="sub">Trade de minutos • entrada no fundo • saída proxy = mc_1h • baseado em dados reais</p>

<!-- Seção 1: Overview -->
<h2>1 — Dataset</h2>
<div class="grid">
  <div class="card"><div class="card-label">Total migrações</div><div class="card-value">{{ d.total }}</div></div>
  <div class="card"><div class="card-label">Com fundo</div><div class="card-value green">{{ d.has_bottom_n }}</div></div>
  <div class="card"><div class="card-label">Com 1h</div><div class="card-value green">{{ d.has_1h_n }}</div></div>
  <div class="card"><div class="card-label">Completo (bottom+1h)</div><div class="card-value yellow">{{ d.complete_n }}</div></div>
  <div class="card"><div class="card-label">Recovery</div><div class="card-value green">{{ d.recovered_n }}</div></div>
  <div class="card"><div class="card-label">Sem recovery</div><div class="card-value red">{{ d.not_recov_n }}</div></div>
  <div class="card"><div class="card-label">Pendentes</div><div class="card-value muted">{{ d.pending_n }}</div></div>
</div>
<p class="note">Delay captura (mig→bot): p50={{ d.delay_p50 }} | p75={{ d.delay_p75 }} | p90={{ d.delay_p90 }}</p>

<!-- Seção 2: Timing -->
<h2>2 — Timing do Fundo e do Pico</h2>
<div class="row">
  <div class="kv"><span>Fundo (n={{ d.ttb_n }})</span><span></span></div>
</div>
<div class="row" style="margin-bottom:12px">
  <div class="kv"><span>p25</span><span>{{ d.ttb_p25 }}</span></div>
  <div class="kv"><span>p50</span><span class="yellow">{{ d.ttb_p50 }}</span></div>
  <div class="kv"><span>p75</span><span>{{ d.ttb_p75 }}</span></div>
  <div class="kv"><span>p90</span><span>{{ d.ttb_p90 }}</span></div>
  <div class="kv"><span>≤5min</span><span>{{ d.ttb_u5 }}</span></div>
  <div class="kv"><span>≤10min</span><span>{{ d.ttb_u10 }}</span></div>
  <div class="kv"><span>≤20min</span><span>{{ d.ttb_u20 }}</span></div>
</div>
<div class="row">
  <div class="kv"><span>Pico (p50 / p75)</span><span>{{ d.ttp_p50 }} / {{ d.ttp_p75 }}</span></div>
  <div class="kv"><span>Bounce (pico após fundo)</span><span class="green">{{ d.bounce_pct }}</span></div>
  <div class="kv"><span>Sem bounce</span><span class="red">{{ d.no_bounce_pct }}</span></div>
  <div class="kv"><span>Duração bounce p50/p75</span><span>{{ d.bounce_dur_p50 }} / {{ d.bounce_dur_p75 }}</span></div>
</div>

<!-- Seção 3: Dump -->
<h2>3 — Profundidade do Dump (H1, H6)</h2>
<div class="row" style="margin-bottom:12px">
  <div class="kv"><span>p25</span><span>{{ d.drop_p25 }}</span></div>
  <div class="kv"><span>p50</span><span class="yellow">{{ d.drop_p50 }}</span></div>
  <div class="kv"><span>p75</span><span>{{ d.drop_p75 }}</span></div>
  <div class="kv"><span>p90</span><span>{{ d.drop_p90 }}</span></div>
  <div class="kv"><span>Caíram ≥20% (H1)</span><span>{{ d.over20_pct }}</span></div>
</div>
<table>
<thead><tr>
  <th>Queda</th><th>N</th><th>Recovery%</th><th>R:R med (bottom→1h)</th><th>Win Rate (mc_1h &gt; bottom)</th>
</tr></thead>
<tbody>
{% for b in d.buckets %}
<tr>
  <td>{{ b.label }}</td>
  <td>{{ b.n }}</td>
  <td>{{ b.recov }}</td>
  <td>{{ b.rr_med }}</td>
  <td>{{ b.win }}</td>
</tr>
{% endfor %}
</tbody>
</table>

<!-- Seção 4: Filtros -->
<h2>4 — Validação dos Filtros (H3, H4, H5, H8)</h2>
<p class="note" style="margin-bottom:10px">
  Baseline win rate ({{ d.base_note }}): <strong>{{ d.base_wr }}</strong> (n={{ d.base_n }})<br>
  WR+0% = entrada no fundo exato (teórico) | WR+5% = entrada realista com slippage+fee
</p>
<table>
<thead><tr><th>Filtro</th><th>N</th><th>WR +0% (teórico)</th><th>WR +5% (realista)</th><th>Delta vs Baseline (+5%)</th></tr></thead>
<tbody>
{% for f in d.filter_rows %}
<tr>
  <td>{{ f.label }}</td>
  <td>{{ f.n }}</td>
  <td class="muted">{{ f.wr0 }}</td>
  <td>{{ f.wr5 }}</td>
  <td><span class="tag {{ 't-green' if f.pos else 't-red' }}">{{ f.delta5 }}</span></td>
</tr>
{% endfor %}
</tbody>
</table>

<!-- Seção 5: R:R -->
<h2>5 — R:R Teórico (entrada EXATA no fundo mínimo — impossível na prática, ver Seção 5b)</h2>
{% if d.rr_n > 0 %}
<div class="grid">
  <div class="card"><div class="card-label">Universo</div><div class="card-value">{{ d.rr_n }}</div></div>
  <div class="card"><div class="card-label">Win Rate</div><div class="card-value green">{{ d.rr_wr }}</div></div>
  <div class="card"><div class="card-label">Gain médio (winners)</div><div class="card-value green">{{ d.rr_avg_w }}</div></div>
  <div class="card"><div class="card-label">Loss média (losers)</div><div class="card-value red">{{ d.rr_avg_l }}</div></div>
  <div class="card"><div class="card-label">Payoff ratio</div><div class="card-value yellow">{{ d.rr_payoff }}</div></div>
  <div class="card"><div class="card-label">EV por trade</div>
    <div class="card-value {{ 'green' if d.rr_ev_pos else 'red' }}">{{ d.rr_ev }}</div></div>
</div>
<div class="row" style="margin:10px 0 16px">
  <div class="kv"><span>p50</span><span>{{ d.rr_p50 }}</span></div>
  <div class="kv"><span>p75</span><span>{{ d.rr_p75 }}</span></div>
  <div class="kv"><span>p90</span><span>{{ d.rr_p90 }}</span></div>
</div>
{% if d.rr_deep_n > 0 %}
<p style="color:#8b949e;font-size:12px;margin-bottom:8px">Subgrupo: dump ≥20% (n={{ d.rr_deep_n }})</p>
<div class="grid">
  <div class="card"><div class="card-label">Win Rate</div><div class="card-value green">{{ d.rr_deep_wr }}</div></div>
  <div class="card"><div class="card-label">Payoff</div><div class="card-value yellow">{{ d.rr_deep_payoff }}</div></div>
  <div class="card"><div class="card-label">EV</div>
    <div class="card-value {{ 'green' if d.rr_deep_ev_pos else 'red' }}">{{ d.rr_deep_ev }}</div></div>
  <div class="card"><div class="card-label">p50 / p75</div><div class="card-value">{{ d.rr_deep_p50 }} / {{ d.rr_deep_p75 }}</div></div>
</div>
{% endif %}
{% else %}
<p class="muted">Dados insuficientes — aguardar mais migrações completas (bottom + mc_1h).</p>
{% endif %}

<!-- Seção 5b: TP/SL derivado dos dados reais -->
<h2>5b — TP e SL Ótimos: o que os dados revelam</h2>
{% if d.bounce_n > 0 %}
<p class="note" style="margin-bottom:12px">
  Universo: <strong>{{ d.both_n }}</strong> tokens com bottom e peak.
  <span class="green">{{ d.bounce_n }} com bounce</span> (pico após fundo) |
  <span class="red">{{ d.no_bounce_n }} sem bounce</span> (pico veio antes do fundo).
</p>

<div class="row" style="margin-bottom:16px">
  <div class="kv"><span>Magnitude bounce — p10</span><span>{{ d.bounce_mag_p10 }}</span></div>
  <div class="kv"><span>p25</span><span>{{ d.bounce_mag_p25 }}</span></div>
  <div class="kv"><span>p50 (mediana)</span><span class="yellow">{{ d.bounce_mag_p50 }}</span></div>
  <div class="kv"><span>p75</span><span>{{ d.bounce_mag_p75 }}</span></div>
  <div class="kv"><span>p90</span><span>{{ d.bounce_mag_p90 }}</span></div>
</div>
<p class="note" style="margin-bottom:10px">
  Taxa de acerto por nível de TP — sobre tokens que fizeram bounce (base real, não hipotética).
</p>
<table>
<thead><tr>
  <th>TP</th><th>Tokens que teriam batido</th><th>Hit rate</th><th>EV aproximado</th>
</tr></thead>
<tbody>
{% for t in d.tp_rows %}
<tr>
  <td><strong>{{ t.tp }}</strong></td>
  <td>{{ t.hit_n }} / {{ d.bounce_n }}</td>
  <td><span class="tag {{ 't-green' if t.hit_raw > 0.5 else 't-yellow' if t.hit_raw > 0.3 else 't-red' }}">{{ t.hit_rate }}</span></td>
  <td class="{{ 'green' if t.ev_pos else 'red' }}">{{ t.ev_approx }}</td>
</tr>
{% endfor %}
</tbody>
</table>

<h2 style="margin-top:24px">5c — Janela de Oportunidade (tempo entre fundo e pico)</h2>
<p class="note" style="margin-bottom:10px">Quanto tempo você tem para executar a saída após o fundo.</p>
<div class="row" style="margin-bottom:12px">
  <div class="kv"><span>p25</span><span>{{ d.window_p25 }}</span></div>
  <div class="kv"><span>p50</span><span class="yellow">{{ d.window_p50 }}</span></div>
  <div class="kv"><span>p75</span><span>{{ d.window_p75 }}</span></div>
  <div class="kv"><span>p90</span><span>{{ d.window_p90 }}</span></div>
  <div class="kv"><span>≤5min</span><span class="red">{{ d.window_u5 }}</span></div>
  <div class="kv"><span>≤10min</span><span>{{ d.window_u10 }}</span></div>
  <div class="kv"><span>≤15min</span><span>{{ d.window_u15 }}</span></div>
</div>

{% if d.sl_n > 0 %}
<h2 style="margin-top:24px">5d — Stop Loss: o que acontece quando não há bounce (n={{ d.sl_n }})</h2>
<p class="note" style="margin-bottom:10px">
  Tokens onde o pico veio ANTES do fundo — se você comprou "o fundo" mas errou,
  quanto perdeu até a marca de 1h?
</p>
<div class="row">
  <div class="kv"><span>p10 (melhor caso)</span><span>{{ d.sl_p10 }}</span></div>
  <div class="kv"><span>p25</span><span>{{ d.sl_p25 }}</span></div>
  <div class="kv"><span>p50 (mediana)</span><span class="red">{{ d.sl_p50 }}</span></div>
</div>
{% endif %}

{% if d.rr_optimal %}
<h2 style="margin-top:24px">5e — R:R Real: combinando bounce rate × hit rate × loss média</h2>
<p class="note" style="margin-bottom:10px">
  Win rate real = P(bounce) × P(TP atingido | bounce). Loss = perda mediana nos sem-bounce.
  Este é o número que importa para sizing de posição.
</p>
<table>
<thead><tr><th>TP</th><th>Win Rate Real</th><th>Payoff</th><th>EV por trade</th></tr></thead>
<tbody>
{% for r in d.rr_optimal %}
<tr>
  <td><strong>{{ r.tp }}</strong></td>
  <td>{{ r.win_rate }}</td>
  <td>{{ r.payoff }}</td>
  <td class="{{ 'green' if r.ev_pos else 'red' }}">{{ r.ev }}</td>
</tr>
{% endfor %}
</tbody>
</table>
{% endif %}

{% else %}
<p class="muted">Dados insuficientes — aguardar tokens com bottom e peak preenchidos.</p>
{% endif %}

<!-- Seção 5f: Win Rate com entrada realista (mc_1h como saída) -->
<h2>5f — Win Rate com Saída em 1h (comparação — saída em tempo fixo)</h2>
<p class="note" style="margin-bottom:10px">
  Entrada no fundo exato é fisicamente impossível. Cada linha mostra o impacto do premium de entrada no resultado.
  Saída = mc_1h em todos os cenários.
</p>
{% if d.realistic_scenarios %}
<table>
<thead><tr>
  <th>Cenário de entrada</th><th>N</th><th>Win Rate</th>
  <th>Gain médio (winners)</th><th>Loss média (losers)</th>
  <th>Payoff</th><th>EV por trade</th>
</tr></thead>
<tbody>
{% for s in d.realistic_scenarios %}
<tr>
  <td>{{ s.label }}</td>
  <td>{{ s.n }}</td>
  <td><span class="tag {{ 't-green' if s.wr_float > 0.5 else 't-red' }}">{{ s.wr }}</span></td>
  <td class="green">{{ s.avg_w }}</td>
  <td class="red">{{ s.avg_l }}</td>
  <td>{{ s.payoff }}</td>
  <td class="{{ 'green' if s.ev_pos else 'red' }}">{{ s.ev }}</td>
</tr>
{% endfor %}
</tbody>
</table>
<p class="note" style="margin-top:8px">EV negativo com entrada +5% significa que a estratégia de saída em 1h não é lucrativa — precisa de saída mais rápida (target explícito) ou melhor timing de entrada.</p>
{% else %}
<p class="muted">Dados insuficientes.</p>
{% endif %}

<!-- Seção 6: H2 -->
<h2>6 — H2: Fundo em ≤15min nos tokens com recovery?</h2>
{% if d.h2_ok_n > 0 %}
<div class="row" style="margin-bottom:12px">
  <div class="kv"><span>Recovery=TRUE (n={{ d.h2_ok_n }})</span><span></span></div>
</div>
<div class="row" style="margin-bottom:16px">
  <div class="kv"><span>p50</span><span>{{ d.h2_ok_p50 }}</span></div>
  <div class="kv"><span>p75</span><span>{{ d.h2_ok_p75 }}</span></div>
  <div class="kv"><span>≤15min (meta H2: ≥70%)</span><span class="yellow">{{ d.h2_ok_u15 }}</span></div>
</div>
{% endif %}
{% if d.h2_no_n > 0 %}
<div class="row">
  <div class="kv"><span>Recovery=FALSE (n={{ d.h2_no_n }})</span><span></span></div>
</div>
<div class="row">
  <div class="kv"><span>p50</span><span>{{ d.h2_no_p50 }}</span></div>
  <div class="kv"><span>p75</span><span>{{ d.h2_no_p75 }}</span></div>
  <div class="kv"><span>≤15min</span><span>{{ d.h2_no_u15 }}</span></div>
</div>
{% endif %}
{% if d.h2_ok_n == 0 and d.h2_no_n == 0 %}
<p class="muted">Dados insuficientes.</p>
{% endif %}

<!-- Seção 7: Fase 1 — cobertura -->
<h2>7 — Fase 1: Cobertura dos Novos Campos ({{ d.fase1_total }} migrações)</h2>
<p class="note" style="margin-bottom:10px">Campos coletados desde o último deploy, sem custo de API extra.</p>
<table>
<thead><tr><th>Campo</th><th>Preenchidos</th><th>Total</th><th>Cobertura</th></tr></thead>
<tbody>
{% for f in d.fase1_rows %}
<tr>
  <td>{{ f.label }}</td>
  <td>{{ f.filled }}</td>
  <td>{{ f.total }}</td>
  <td><span class="tag {{ 't-green' if f.ok else 't-red' }}">{{ f.pct }}</span></td>
</tr>
{% endfor %}
</tbody>
</table>

{% if d.cts_total > 0 %}
<h2 style="margin-top:24px">7.1 — Creator Token Status (n={{ d.cts_total }})</h2>
<div class="grid" style="margin-bottom:12px">
  <div class="card"><div class="card-label">creator_close (dev vendeu)</div><div class="card-value green">{{ d.cts_close }}</div></div>
  <div class="card"><div class="card-label">creator_hold (dev segura)</div><div class="card-value red">{{ d.cts_hold }}</div></div>
</div>
{% if d.cts_close_n > 0 or d.cts_hold_n > 0 %}
<p class="note" style="margin-bottom:6px">Win rate (mc_1h &gt; bottom) por status do dev:</p>
<div class="row">
  <div class="kv"><span>creator_close (n={{ d.cts_close_n }})</span><span class="green">{{ d.cts_close_wr }}</span></div>
  <div class="kv"><span>creator_hold (n={{ d.cts_hold_n }})</span><span class="red">{{ d.cts_hold_wr }}</span></div>
</div>
{% endif %}
{% endif %}

<!-- Seção 8: Fase 1 — correlação com win rate -->
<h2>8 — Fase 1: Correlação dos Novos Campos com Win Rate</h2>
{% if d.f1_base_n > 0 %}
<p class="note" style="margin-bottom:10px">
  Base: <strong>{{ d.f1_base_n }}</strong> tokens com campos Fase 1 + bottom + mc_1h preenchidos.
  Baseline win rate: <strong>{{ d.f1_base_wr }}</strong>
</p>
<table>
<thead><tr><th>Campo / Grupo</th><th>N</th><th>Win Rate</th><th>Delta vs Baseline</th></tr></thead>
<tbody>
{% for f in d.f1_corr %}
<tr>
  <td>{{ f.label }}</td>
  <td>{{ f.n }}</td>
  <td>{{ f.wr }}</td>
  <td><span class="tag {{ 't-green' if f.pos else 't-red' }}">{{ f.delta }}</span></td>
</tr>
{% endfor %}
</tbody>
</table>
{% else %}
<p class="muted">Dados insuficientes — aguardar tokens com campos Fase 1 preenchidos.</p>
{% endif %}

<!-- Seção 9: Série Temporal -->
<h2>9 — Série Temporal: Primeiros 10min (Smart Flow + Volume)</h2>
{% if d.ts_n_tokens > 0 %}
<p class="note" style="margin-bottom:10px">
  {{ d.ts_n_tokens }} tokens com série temporal coletada.
  Recovery={{ d.ts_has_recovery_n }} | Sem recovery={{ d.ts_no_recovery_n }}
  — comparação de comportamento nos primeiros 10min pós-migração.
</p>
<table>
<thead><tr>
  <th>Métrica (primeiros 10min)</th>
  <th>Recovery=TRUE</th>
  <th>Recovery=FALSE</th>
  <th>Interpretação</th>
</tr></thead>
<tbody>
<tr>
  <td>avg smart_count</td>
  <td class="green">{{ d.ts_avg_smart_recovery }}</td>
  <td class="red">{{ d.ts_avg_smart_no }}</td>
  <td class="muted">Wallets inteligentes presentes</td>
</tr>
<tr>
  <td>delta smart_count (max−min)</td>
  <td class="green">{{ d.ts_delta_smart_recovery }}</td>
  <td class="red">{{ d.ts_delta_smart_no }}</td>
  <td class="muted">Acumulação durante o dump</td>
</tr>
<tr>
  <td>avg volume_1m</td>
  <td class="green">{{ d.ts_avg_vol_recovery }}</td>
  <td class="red">{{ d.ts_avg_vol_no }}</td>
  <td class="muted">Interesse comprador no período</td>
</tr>
<tr>
  <td>avg holders_count</td>
  <td class="green">{{ d.ts_avg_holders_recovery }}</td>
  <td class="red">{{ d.ts_avg_holders_no }}</td>
  <td class="muted">Base de holders durante janela</td>
</tr>
</tbody>
</table>

{% if d.ts_exchanges %}
<p class="note" style="margin-top:16px;margin-bottom:6px">Recovery rate por exchange (tokens com série temporal):</p>
<table style="width:auto">
<thead><tr><th>Exchange</th><th>N tokens</th><th>Recovery%</th></tr></thead>
<tbody>
{% for e in d.ts_exchanges %}
<tr>
  <td>{{ e.exch }}</td>
  <td>{{ e.n }}</td>
  <td>{{ e.rec_pct }}</td>
</tr>
{% endfor %}
</tbody>
</table>
{% endif %}
{% else %}
<p class="muted">Série temporal ainda não coletada — dados disponíveis nas próximas migrações após este deploy.</p>
{% endif %}

</body>
</html>
"""


@app.route("/analise/dump-depth")
def analise_dump_depth():
    """
    H12: dump_depth × time_elapsed → P(recovery)

    Grid 2D: pct_drop (profundidade do dump) × time_to_bottom (minutos).
    Para cada célula: recovery rate, N e flag de zona acionável (N≥15, rate≥60%).

    Também calcula distribuições marginais e a zona ótima de entrada.
    Em tempo real: entrar quando (current_drop%, time_elapsed) cair numa zona ≥60%.
    """
    with db._cursor() as cur:
        cur.execute("""
            SELECT pct_drop, time_to_bottom, recovery
            FROM migrations
            WHERE recovery     IS NOT NULL
              AND pct_drop     IS NOT NULL
              AND time_to_bottom IS NOT NULL
              AND mc_migration > 0
              AND pct_drop BETWEEN 0 AND 1
        """)
        rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        return {"erro": "Nenhum dado"}, 200

    total      = len(rows)
    total_rec  = sum(1 for r in rows if r["recovery"])
    baseline   = round(total_rec / total, 3)

    # Bins de dump depth (pct_drop é 0-1 no banco → multiplicar por 100)
    drop_bins = [
        (0,  30,  "0-30%"),
        (30, 50,  "30-50%"),
        (50, 65,  "50-65%"),
        (65, 80,  "65-80%"),
        (80, 101, "80%+"),
    ]
    # Bins de tempo até o fundo (minutos)
    time_bins = [
        (0,  5,  "0-5min"),
        (5,  10, "5-10min"),
        (10, 20, "10-20min"),
        (20, 30, "20-30min"),
        (30, 999,"30min+"),
    ]

    def cell(subset):
        n   = len(subset)
        rec = sum(1 for r in subset if r["recovery"])
        rate = round(rec / n, 3) if n else None
        return {
            "n":             n,
            "recovery":      rec,
            "recovery_rate": rate,
            "actionable":    n >= 15 and rate is not None and rate >= 0.60,
            "vs_baseline":   round(rate - baseline, 3) if rate is not None else None,
        }

    # Grid 2D
    grid = []
    for dlo, dhi, dlabel in drop_bins:
        row_data = {"drop_range": dlabel, "cells": []}
        for tlo, thi, tlabel in time_bins:
            subset = [
                r for r in rows
                if dlo <= float(r["pct_drop"]) * 100 < dhi
                and tlo <= float(r["time_to_bottom"]) < thi
            ]
            c = cell(subset)
            c["time_range"] = tlabel
            row_data["cells"].append(c)
        grid.append(row_data)

    # Marginais 1D
    drop_marginal = []
    for dlo, dhi, dlabel in drop_bins:
        subset = [r for r in rows if dlo <= float(r["pct_drop"]) * 100 < dhi]
        c = cell(subset)
        c["drop_range"] = dlabel
        drop_marginal.append(c)

    time_marginal = []
    for tlo, thi, tlabel in time_bins:
        subset = [r for r in rows if tlo <= float(r["time_to_bottom"]) < thi]
        c = cell(subset)
        c["time_range"] = tlabel
        time_marginal.append(c)

    # Zonas acionáveis (N≥15, rate≥60%) ordenadas por recovery_rate
    actionable = sorted(
        [
            {
                "drop_range":    dlabel,
                "time_range":    tlabel,
                "n":             c["n"],
                "recovery_rate": c["recovery_rate"],
                "vs_baseline_pp": round((c["recovery_rate"] - baseline) * 100, 1)
                                  if c["recovery_rate"] else None,
            }
            for (dlo, dhi, dlabel), (tlo, thi, tlabel), c in [
                (db_, tb_, cell([
                    r for r in rows
                    if db_[0] <= float(r["pct_drop"]) * 100 < db_[1]
                    and tb_[0] <= float(r["time_to_bottom"]) < tb_[1]
                ]))
                for db_ in drop_bins for tb_ in time_bins
            ]
            if c["actionable"]
        ],
        key=lambda x: x["recovery_rate"] or 0,
        reverse=True,
    )

    return {
        "hipotese":      "H12: dump_depth + time_elapsed → zona probabilística de entrada",
        "dataset":       {"total": total, "recovery": total_rec,
                          "baseline_rate": baseline},
        "grid_2d":       grid,
        "drop_marginal": drop_marginal,
        "time_marginal": time_marginal,
        "zonas_acionaveis": actionable,
        "veredicto": {
            "zonas_encontradas": len(actionable),
            "melhor_zona": actionable[0] if actionable else None,
            "interpretacao": (
                f"{len(actionable)} zona(s) com N≥15 e recovery≥60% — edge identificado"
                if actionable else
                "nenhuma zona acionável com N≥15 e rate≥60% — aumentar dataset"
            ),
        },
    }, 200


@app.route("/analise/backtest")
def analise_backtest():
    """
    Backtester TP/SL com dois modos:

    ?mode=ticks (padrao): tick-level usando token_ticks (~30s).
      Preciso — detecta ordem SL/TP. Limitado a ~68 tokens com ticks.

    ?mode=full: usa toda a base migrations (~400 tokens).
      Usa mc_peak para TP e mc_1h como saida quando TP nao atingido.
      Ligeiramente otimista (nao detecta SL antes do TP), mas 6x mais dados.

    Comparar os dois: se apontam na mesma direcao, confianca alta.
    """
    import collections
    from flask import request

    bounce_pct = float(request.args.get("bounce", "0.15"))
    min_drop   = float(request.args.get("min_drop", "0.30"))
    max_time   = float(request.args.get("max_time", "30"))
    mode       = request.args.get("mode", "ticks")

    with db._cursor() as cur:
        cur.execute("""
            SELECT mint, pct_drop, time_to_bottom, time_to_peak,
                   mc_bottom, mc_peak, mc_1h, mc_migration
            FROM migrations
            WHERE pct_drop       IS NOT NULL
              AND time_to_bottom IS NOT NULL
              AND mc_bottom      IS NOT NULL
              AND mc_migration    > 0
              AND mc_bottom       > 0
        """)
        rows = [dict(r) for r in cur.fetchall()]

    eligible = [
        r for r in rows
        if float(r["pct_drop"]) >= min_drop
        and float(r["time_to_bottom"]) < max_time
    ]

    if not eligible:
        return {"erro": "Nenhum token elegivel com os filtros informados"}, 200

    tp_values = [0.15, 0.20, 0.30, 0.40, 0.50, 0.75, 1.00]
    sl_values = [0.10, 0.15, 0.20, 0.25, 0.30]

    # ── MODO FULL: usa migrations completas (~400 tokens) ─────────────────
    if mode == "full":
        eligible_full = [r for r in eligible if r["mc_peak"] is not None and r["mc_1h"] is not None]

        def simulate_full(tp: float, sl: float):
            wins, losses = [], []
            n_tp_hit, n_sl_exit, n_time_exit, n_sem_dados = 0, 0, 0, 0

            for r in eligible_full:
                mc_bottom = float(r["mc_bottom"])
                entry     = mc_bottom * (1 + bounce_pct)
                tp_target = entry * (1 + tp)
                sl_floor  = entry * (1 - sl)

                peak_after = (
                    r["time_to_peak"] is not None and
                    float(r["time_to_peak"]) >= float(r["time_to_bottom"])
                )
                tp_hit = peak_after and float(r["mc_peak"]) >= tp_target

                if tp_hit:
                    wins.append(tp)
                    n_tp_hit += 1
                else:
                    exit_mc  = float(r["mc_1h"])
                    pnl      = (exit_mc - entry) / entry
                    capped   = max(pnl, -sl)
                    losses.append(capped)
                    if capped == -sl:
                        n_sl_exit += 1
                    else:
                        n_time_exit += 1

            n      = len(wins) + len(losses)
            wr     = len(wins) / n if n else 0
            ag     = sum(wins)   / len(wins)   if wins   else 0
            al     = sum(losses) / len(losses) if losses else 0
            ev     = wr * ag + (1 - wr) * al
            payoff = abs(ag / al)              if al != 0 else None

            return {
                "tp":             f"{tp:.0%}",
                "sl":             f"{sl:.0%}",
                "n_trades":       n,
                "n_tp_hit":       n_tp_hit,
                "n_sl_exit":      n_sl_exit,
                "n_time_exit":    n_time_exit,
                "win_rate":       round(wr,     3),
                "avg_gain":       round(ag,     3),
                "avg_loss":       round(al,     3),
                "payoff_ratio":   round(payoff, 2) if payoff else None,
                "expected_value": round(ev,     4),
            }

        grid = [simulate_full(tp, sl) for tp in tp_values for sl in sl_values]
        best_ev = max((r for r in grid if r["n_trades"] >= 50),
                      key=lambda x: x["expected_value"], default=None)
        best_wr = max((r for r in grid if r["n_trades"] >= 50),
                      key=lambda x: x["win_rate"], default=None)

        return {
            "modo": "full — migrations completas (mc_peak/mc_1h). "
                    "Aviso: nao detecta SL antes do TP (ligeiramente otimista).",
            "parametros": {
                "bounce_entry_pct": f"{bounce_pct:.0%}",
                "min_drop_filter":  f"{min_drop:.0%}",
                "max_time_filter":  f"{max_time:.0f}min",
            },
            "dataset": {
                "tokens_elegiveis":       len(eligible),
                "tokens_com_mc_peak_1h":  len(eligible_full),
            },
            "melhor_ev":       best_ev,
            "melhor_win_rate": best_wr,
            "grid_tp_sl":      grid,
        }, 200

    # ── MODO TICKS: tick-level com OHLC ───────────────────────────────────
    mints = [r["mint"] for r in eligible]

    with db._cursor() as cur:
        cur.execute("""
            SELECT mint, elapsed_sec, mc, mc_open, mc_high, mc_low, source
            FROM token_ticks
            WHERE mint = ANY(%s)
              AND mc IS NOT NULL AND mc > 0
            ORDER BY mint, elapsed_sec
        """, (mints,))
        tick_rows = cur.fetchall()

    # Cada tick: (elapsed_sec, mc_close, mc_open, mc_high, mc_low, source)
    ticks_by_mint: dict = collections.defaultdict(list)
    for row in tick_rows:
        rd = dict(row)
        ticks_by_mint[rd["mint"]].append((
            rd["elapsed_sec"],
            float(rd["mc"]),
            rd["mc_open"],
            rd["mc_high"],
            rd["mc_low"],
            rd.get("source", "trenches"),
        ))

    def simulate(tp: float, sl: float):
        wins, losses = [], []
        n_sl_hit, n_tp_hit, n_time_exit, n_no_ticks = 0, 0, 0, 0

        for r in eligible:
            mint       = r["mint"]
            mc_bottom  = float(r["mc_bottom"])
            bottom_sec = float(r["time_to_bottom"]) * 60
            entry      = mc_bottom * (1 + bounce_pct)
            tp_target  = entry * (1 + tp)
            sl_floor   = entry * (1 - sl)

            ticks       = ticks_by_mint.get(mint, [])
            post_bottom = [t for t in ticks if t[0] > bottom_sec]

            if not post_bottom:
                n_no_ticks += 1
                continue

            sl_hit = False
            tp_hit = False
            for (_es, mc_close, mc_open, mc_high, mc_low, source) in post_bottom:
                # Kline ticks: usa OHLC para detectar cruzamentos intra-vela
                if source == "kline" and mc_high is not None and mc_low is not None:
                    low_hit  = float(mc_low)  <= sl_floor
                    high_hit = float(mc_high) >= tp_target
                    if low_hit and high_hit:
                        # Ambos cruzados na mesma vela — usa open para inferir ordem
                        # Se open < midpoint entry→tp: token caiu primeiro (SL)
                        # Se open > midpoint entry→sl: token subiu primeiro (TP)
                        mid = (sl_floor + tp_target) / 2
                        op  = float(mc_open) if mc_open else mc_close
                        if op <= mid:
                            sl_hit = True
                        else:
                            tp_hit = True
                    elif low_hit:
                        sl_hit = True
                    elif high_hit:
                        tp_hit = True
                else:
                    # Trenches ticks: só tem close
                    if mc_close <= sl_floor:
                        sl_hit = True
                    elif mc_close >= tp_target:
                        tp_hit = True

                if sl_hit or tp_hit:
                    break

            if tp_hit:
                wins.append(tp)
                n_tp_hit += 1
            elif sl_hit:
                losses.append(-sl)
                n_sl_hit += 1
            else:
                last_mc = post_bottom[-1][1]
                pnl     = (last_mc - entry) / entry
                losses.append(pnl)
                n_time_exit += 1

        n      = len(wins) + len(losses)
        wr     = len(wins) / n if n else 0
        ag     = sum(wins)    / len(wins)   if wins   else 0
        al     = sum(losses)  / len(losses) if losses else 0
        ev     = wr * ag + (1 - wr) * al
        payoff = abs(ag / al)               if al != 0 else None

        return {
            "tp":             f"{tp:.0%}",
            "sl":             f"{sl:.0%}",
            "n_trades":       n,
            "n_tp_hit":       n_tp_hit,
            "n_sl_hit":       n_sl_hit,
            "n_time_exit":    n_time_exit,
            "n_no_ticks":     n_no_ticks,
            "win_rate":       round(wr,     3),
            "avg_gain":       round(ag,     3),
            "avg_loss":       round(al,     3),
            "payoff_ratio":   round(payoff, 2) if payoff else None,
            "expected_value": round(ev,     4),
        }

    grid = [simulate(tp, sl) for tp in tp_values for sl in sl_values]

    best_ev = max(
        (r for r in grid if r["n_trades"] >= 20),
        key=lambda x: x["expected_value"],
        default=None,
    )
    best_wr = max(
        (r for r in grid if r["n_trades"] >= 20),
        key=lambda x: x["win_rate"],
        default=None,
    )

    eligible_with_ticks = sum(1 for r in eligible if ticks_by_mint.get(r["mint"]))

    no_tick_tokens = [r for r in eligible if not ticks_by_mint.get(r["mint"])]
    no_tick_recovery_dist = {}
    if no_tick_tokens:
        with db._cursor() as cur:
            nt_mints = [r["mint"] for r in no_tick_tokens]
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE recovery IS TRUE)  AS rec_true,
                    COUNT(*) FILTER (WHERE recovery IS FALSE) AS rec_false,
                    COUNT(*) FILTER (WHERE recovery IS NULL)  AS rec_null,
                    COUNT(*) FILTER (WHERE ts_migration < NOW() - INTERVAL '7 days') AS old_tokens,
                    AVG(pct_drop) AS avg_drop,
                    AVG(time_to_bottom) AS avg_ttb
                FROM migrations
                WHERE mint = ANY(%s)
            """, (nt_mints,))
            nd = dict(cur.fetchone())
            no_tick_recovery_dist = {
                "recovery_true":       nd["rec_true"],
                "recovery_false":      nd["rec_false"],
                "recovery_null":       nd["rec_null"],
                "older_than_7d":       nd["old_tokens"],
                "avg_drop":            round(float(nd["avg_drop"] or 0), 3),
                "avg_time_to_bottom":  round(float(nd["avg_ttb"]  or 0), 1),
            }

    return {
        "modo": "ticks — simulacao tick-level precisa. Use ?mode=full para base completa.",
        "parametros": {
            "bounce_entry_pct": f"{bounce_pct:.0%}",
            "min_drop_filter":  f"{min_drop:.0%}",
            "max_time_filter":  f"{max_time:.0f}min",
        },
        "dataset": {
            "tokens_elegiveis":      len(eligible),
            "tokens_com_ticks":      eligible_with_ticks,
            "tokens_sem_ticks":      len(eligible) - eligible_with_ticks,
            "sem_ticks_diagnostico": no_tick_recovery_dist,
        },
        "melhor_ev":       best_ev,
        "melhor_win_rate": best_wr,
        "grid_tp_sl":      grid,
    }, 200


@app.route("/analise/price-decel")
def analise_price_decel():
    """
    Análise de deceleração de preço como sinal de fundo.

    Para cada token com recovery definido, pega os ticks ao redor do mc_bottom
    confirmado e calcula a taxa de variação de preço em 3 janelas:
      A: [bottom - 120s, bottom - 60s)  — 2 a 1 min antes do fundo
      B: [bottom - 60s,  bottom - 30s)  — 1 min a 30s antes do fundo
      C: [bottom - 30s,  bottom + 5s]   — últimos 30s até o fundo

    Se slope_A < slope_B < slope_C (todos negativos, ficando menos negativos),
    o preço está desacelerando na queda — sinal observável em tempo real.
    """
    from collections import defaultdict

    with db._cursor() as cur:
        cur.execute("""
            SELECT mint, recovery,
                   ROUND(time_to_bottom * 60)::int AS bottom_elapsed,
                   mc_bottom, mc_migration
            FROM migrations
            WHERE recovery IS NOT NULL
              AND time_to_bottom IS NOT NULL
              AND mc_bottom IS NOT NULL
              AND mc_migration IS NOT NULL
              AND mc_migration > 0
        """)
        mig_rows = cur.fetchall()

    if not mig_rows:
        return {"erro": "Nenhum dado"}, 200

    mig_by_mint = {}
    skipped_early_bottom = 0
    for r in mig_rows:
        r = dict(r)
        try:
            be = int(r["bottom_elapsed"])
            if be < 600:  # fundo em < 10min — janela A (5-10min antes) sem dados
                skipped_early_bottom += 1
                continue
            mig_by_mint[r["mint"]] = {
                "recovery":       r["recovery"],
                "bottom_elapsed": be,
                "mc_bottom":      float(r["mc_bottom"]),
                "mc_migration":   float(r["mc_migration"]),
            }
        except (TypeError, ValueError):
            continue
    mints = list(mig_by_mint.keys())

    with db._cursor() as cur:
        cur.execute("""
            SELECT mint, elapsed_sec, mc
            FROM token_ticks
            WHERE mint = ANY(%s)
              AND mc IS NOT NULL
            ORDER BY mint, elapsed_sec
        """, (mints,))
        tick_rows = cur.fetchall()

    ticks_by_mint = defaultdict(list)
    for row in tick_rows:
        row = dict(row)
        try:
            ticks_by_mint[row["mint"]].append(
                (int(row["elapsed_sec"]), float(row["mc"]))
            )
        except (TypeError, ValueError):
            continue

    # debug temporário — remover após confirmar dados
    _sample_mint = next(iter(ticks_by_mint), None)
    # Quantos mints têm pelo menos 1 tick dentro de 60s do bottom
    _covering = sum(
        1 for mint, meta in mig_by_mint.items()
        if any(abs(e - meta["bottom_elapsed"]) <= 60
               for e, _ in ticks_by_mint.get(mint, []))
    )
    _sample_ticks = ticks_by_mint.get(_sample_mint, [])
    _debug = {
        "mints_em_mig_by_mint":        len(mints),
        "tick_rows_retornados":         len(tick_rows),
        "mints_em_ticks_by_mint":       len(ticks_by_mint),
        "mints_ticks_cobrem_fundo":     _covering,
        "skipped_early_bottom_lt3m":    skipped_early_bottom,
        "sample_mint":                  _sample_mint,
        "sample_ticks_count":           len(_sample_ticks),
        "sample_ticks_max_elapsed":     max((e for e, _ in _sample_ticks), default=None),
        "sample_mig_bottom_elapsed":    mig_by_mint.get(_sample_mint, {}).get("bottom_elapsed"),
    }

    def slope_in_window(ticks, bottom_e, lo, hi):
        pts = sorted([(e, mc) for e, mc in ticks if lo <= (e - bottom_e) < hi])
        if len(pts) < 2:
            return None
        dt = pts[-1][0] - pts[0][0]
        if dt == 0:
            return None
        return (pts[-1][1] - pts[0][1]) / dt  # MC/s

    results = {"recovery": [], "no_recovery": []}

    for mint, meta in mig_by_mint.items():
        ticks = ticks_by_mint.get(mint, [])
        if len(ticks) < 4:
            continue

        be  = meta["bottom_elapsed"]
        mc0 = meta["mc_migration"]

        # Janelas alargadas — ticks têm intervalo médio ~30s, não 5s
        s_a = slope_in_window(ticks, be, -600, -300)  # 5-10min antes do fundo
        s_b = slope_in_window(ticks, be, -300, -120)  # 2-5min antes do fundo
        s_c = slope_in_window(ticks, be, -120,   30)  # 2min antes até 30s depois

        if s_a is None or s_b is None or s_c is None:
            continue

        # normaliza por mc_migration → % do MC por segundo
        norm = lambda s: round(s / mc0 * 100, 5)
        na, nb, nc = norm(s_a), norm(s_b), norm(s_c)

        entry = {
            "mint":            mint,
            "slope_a":         na,
            "slope_b":         nb,
            "slope_c":         nc,
            "decel_ab":        round(nb - na, 5),  # positivo = desacelerando
            "decel_bc":        round(nc - nb, 5),  # positivo = desacelerando mais
            "full_decel":      (nb > na) and (nc > nb),  # A < B < C
        }
        key = "recovery" if meta["recovery"] else "no_recovery"
        results[key].append(entry)

    def _avg(entries, key):
        vals = [e[key] for e in entries]
        return round(sum(vals) / len(vals), 5) if vals else None

    def _pct_full_decel(entries):
        if not entries:
            return None
        return round(sum(1 for e in entries if e["full_decel"]) / len(entries) * 100, 1)

    rec   = results["recovery"]
    norec = results["no_recovery"]

    avg_rec_a   = _avg(rec,   "slope_a")
    avg_rec_b   = _avg(rec,   "slope_b")
    avg_rec_c   = _avg(rec,   "slope_c")
    avg_norec_a = _avg(norec, "slope_a")
    avg_norec_b = _avg(norec, "slope_b")
    avg_norec_c = _avg(norec, "slope_c")

    pct_decel_rec   = _pct_full_decel(rec)
    pct_decel_norec = _pct_full_decel(norec)

    delta_pct = round((pct_decel_rec or 0) - (pct_decel_norec or 0), 1)

    discrimina = delta_pct >= 10

    return {
        "hipotese": "Deceleração de preço (d²p/dt² → 0) precede o fundo em recovery tokens",
        "_debug": _debug,
        "dataset": {
            "total_migrations_com_recovery":   len(mig_by_mint) + skipped_early_bottom,
            "filtrados_fundo_lt_3min":         skipped_early_bottom,
            "tokens_analisaveis":              len(mig_by_mint),
            "tokens_com_ticks_suficientes":    len(rec) + len(norec),
            "recovery":                        len(rec),
            "no_recovery":                     len(norec),
        },
        "slopes_normalizados_pct_mc_por_s": {
            "janela_a_10min_5min_antes_fundo": {
                "recovery":    avg_rec_a,
                "no_recovery": avg_norec_a,
            },
            "janela_b_5min_2min_antes_fundo": {
                "recovery":    avg_rec_b,
                "no_recovery": avg_norec_b,
            },
            "janela_c_2min_antes_ate_fundo": {
                "recovery":    avg_rec_c,
                "no_recovery": avg_norec_c,
            },
        },
        "deceleration_full_A_menor_B_menor_C": {
            "pct_recovery_tokens":    pct_decel_rec,
            "pct_no_recovery_tokens": pct_decel_norec,
            "delta_pp":               delta_pct,
        },
        "veredicto": {
            "discrimina":     discrimina,
            "delta_pp":       delta_pct,
            "interpretacao": (
                "recovery tokens desaceleram mais antes do fundo — sinal promissor"
                if discrimina else
                "sem discriminação clara de deceleração entre recovery e no_recovery"
            ),
        },
        "amostra_top10_recovery": sorted(
            rec, key=lambda e: e["decel_bc"], reverse=True
        )[:10],
        "amostra_top10_no_recovery": sorted(
            norec, key=lambda e: e["decel_bc"], reverse=True
        )[:10],
    }, 200


@app.route("/analise")
def analise():
    rows = db.get_all_migrations(limit=10000)
    d = _run_analysis(rows)
    return render_template_string(ANALISE_HTML, d=d)


# ==============================================================================
# /trades — Dashboard Fase 6-lite
# ==============================================================================

_TRADES_HTML = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="15">
<title>NeideBOT — Trades</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Courier New', monospace; background: #0d1117; color: #c9d1d9; font-size: 13px; }
  h1 { padding: 16px; color: #58a6ff; }
  .summary { display: flex; gap: 16px; padding: 0 16px 16px; flex-wrap: wrap; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 12px 18px; min-width: 140px; }
  .card .label { color: #8b949e; font-size: 11px; margin-bottom: 4px; }
  .card .val { font-size: 18px; font-weight: bold; }
  .green { color: #3fb950; }
  .red   { color: #f85149; }
  .yellow{ color: #d29922; }
  table  { width: 100%; border-collapse: collapse; }
  th     { background: #161b22; color: #8b949e; padding: 6px 8px; text-align: left; border-bottom: 1px solid #30363d; font-size: 11px; }
  td     { padding: 5px 8px; border-bottom: 1px solid #21262d; }
  tr:hover td { background: #161b22; }
  .badge { border-radius: 4px; padding: 2px 6px; font-size: 11px; }
  .b-pending  { background: #3d3b00; color: #d29922; }
  .b-open     { background: #003d00; color: #3fb950; }
  .b-strategy_pending { background: #003d3d; color: #39d0d0; }
  .b-tp_hit   { background: #003d00; color: #3fb950; }
  .b-sl_hit   { background: #3d0000; color: #f85149; }
  .b-error    { background: #3d0000; color: #f85149; }
  .b-timeout  { background: #3d3b00; color: #d29922; }
  .section    { padding: 16px; }
</style>
</head>
<body>
<h1>Trades — Fase 6-lite (calibração ${{ amount_usd }}/trade)</h1>
<div class="summary">
  <div class="card"><div class="label">Total trades</div><div class="val">{{ total }}</div></div>
  <div class="card"><div class="label">Abertos</div><div class="val yellow">{{ open_count }}</div></div>
  <div class="card"><div class="label">TP hit</div><div class="val green">{{ tp_count }}</div></div>
  <div class="card"><div class="label">SL hit</div><div class="val red">{{ sl_count }}</div></div>
  <div class="card"><div class="label">Erros</div><div class="val red">{{ err_count }}</div></div>
  {% if win_rate is not none %}
  <div class="card"><div class="label">Win rate</div><div class="val {% if win_rate >= 0.20 %}green{% else %}yellow{% endif %}">{{ "%.1f%%"|format(win_rate*100) }}</div></div>
  {% endif %}
  {% if avg_latency is not none %}
  <div class="card"><div class="label">Latência média</div><div class="val">{{ "%.1fs"|format(avg_latency) }}</div></div>
  {% endif %}
  {% if avg_slippage is not none %}
  <div class="card"><div class="label">Slippage médio</div><div class="val">{{ "%.2f%%"|format(avg_slippage*100) }}</div></div>
  {% endif %}
  {% if ev_real is not none %}
  <div class="card"><div class="label">EV real</div><div class="val {% if ev_real > 0 %}green{% else %}red{% endif %}">{{ "%+.1f%%"|format(ev_real*100) }}</div></div>
  {% endif %}
  <div class="card"><div class="label">Meta calibração</div><div class="val">{{ calibration_max }} trades</div></div>
</div>
<div class="section">
<table>
<thead><tr>
  <th>#</th><th>Symbol</th><th>Status</th><th>Sinal</th>
  <th>MC bottom</th><th>Entry esperado</th><th>Fill USD</th>
  <th>Slippage</th><th>Fee SOL</th><th>Latência</th>
  <th>Tokens</th><th>P&L real</th><th>Strategy</th>
</tr></thead>
<tbody>
{% for t in trades %}
<tr>
  <td>{{ t.id }}</td>
  <td><b>{{ t.symbol or t.mint[:8] }}</b></td>
  <td><span class="badge b-{{ t.status }}">{{ t.status }}</span></td>
  <td>{{ t.ts_signal_brt or '—' }}</td>
  <td>{{ fmt_mc(t.mc_bottom) }}</td>
  <td>{{ fmt_mc(t.entry_expected) }}</td>
  <td>{{ ("$%.4f"|format(t.entry_price_usd)) if t.entry_price_usd else '—' }}</td>
  <td>{{ ("%+.2f%%"|format(t.slippage_pct*100)) if t.slippage_pct is not none else '—' }}</td>
  <td>{{ ("%.5f"|format(t.fee_sol)) if t.fee_sol else '—' }}</td>
  <td>{{ ("%.1fs"|format(t.latency_sec)) if t.latency_sec else '—' }}</td>
  <td>{{ ("%.0f"|format(t.tokens_received)) if t.tokens_received else '—' }}</td>
  <td>
    {% if t.pnl_pct_real is not none %}
      <span class="{% if t.pnl_pct_real > 0 %}green{% else %}red{% endif %}">
        {{ "%+.1f%%"|format(t.pnl_pct_real*100) }}
      </span>
    {% else %}—{% endif %}
  </td>
  <td>{{ t.strategy_id[:12] if t.strategy_id else '—' }}</td>
</tr>
{% else %}
<tr><td colspan="13" style="text-align:center;color:#8b949e;padding:20px">
  Nenhum trade ainda. Ative TRADING_ENABLED=true no Railway.
</td></tr>
{% endfor %}
</tbody>
</table>
</div>
</body></html>
"""


@app.route("/trades")
def trades_dashboard():
    import os as _os
    trades_raw = db.get_trades(limit=200)

    # Formata timestamps em BRT
    for t in trades_raw:
        ts = t.get("ts_signal")
        t["ts_signal_brt"] = (ts + BRT).strftime("%m/%d %H:%M:%S") if ts else None

    # Métricas
    closed   = [t for t in trades_raw if t["status"] in ("tp_hit", "sl_hit")]
    tp_count = sum(1 for t in trades_raw if t["status"] == "tp_hit")
    sl_count = sum(1 for t in trades_raw if t["status"] == "sl_hit")
    err_count= sum(1 for t in trades_raw if t["status"] == "error")
    open_cnt = sum(1 for t in trades_raw if t["status"] in ("open", "pending", "strategy_pending"))

    win_rate     = tp_count / len(closed) if closed else None
    latencies    = [t["latency_sec"] for t in trades_raw if t.get("latency_sec")]
    slippages    = [t["slippage_pct"] for t in trades_raw if t.get("slippage_pct") is not None]
    avg_latency  = sum(latencies) / len(latencies) if latencies else None
    avg_slippage = sum(slippages) / len(slippages) if slippages else None

    # EV real sobre trades fechados
    ev_real = None
    if closed:
        pnls = [t["pnl_pct_real"] for t in closed if t.get("pnl_pct_real") is not None]
        if pnls:
            ev_real = sum(pnls) / len(pnls)

    trading_enabled = _os.getenv("TRADING_ENABLED", "false").lower() == "true"
    from executor import CALIBRATION_AMOUNT, CALIBRATION_MAX

    return render_template_string(
        _TRADES_HTML,
        trades=trades_raw,
        total=len(trades_raw),
        open_count=open_cnt,
        tp_count=tp_count,
        sl_count=sl_count,
        err_count=err_count,
        win_rate=win_rate,
        avg_latency=avg_latency,
        avg_slippage=avg_slippage,
        ev_real=ev_real,
        amount_usd=CALIBRATION_AMOUNT,
        calibration_max=CALIBRATION_MAX,
        fmt_mc=fmt_mc,
    )


# ==============================================================================
# Startup — funciona com Gunicorn e execução direta
# ==============================================================================

gmgn     = None  # exposto globalmente para rotas de debug
scanner  = None  # exposto globalmente para rotas de debug
executor = None  # exposto globalmente para rotas de debug

def _start_services():
    global gmgn, scanner, executor
    if not os.getenv("DATABASE_URL"):
        logger.warning("DATABASE_URL não configurado — serviços não iniciados")
        return
    db.init_db()
    gmgn      = GmgnClient()
    executor  = TradeExecutor(gmgn)
    scheduler = HeapScheduler()
    hot       = HotMonitor(gmgn, scheduler, executor=executor)
    scanner   = Scanner(gmgn, hot)
    scheduler.start()
    hot.start()
    scanner.start()
    logger.info("Todos os serviços iniciados")


_start_services()


@app.route("/debug/my-ip")
def debug_my_ip():
    """Retorna o IP de saída do Railway — necessário para whitelist GMGN."""
    import requests as _req
    try:
        ip = _req.get("https://ifconfig.me/ip", timeout=5).text.strip()
    except Exception as e:
        ip = f"erro: {e}"
    return f"<pre>Railway outbound IP: {ip}\n\nAdicione este IP na whitelist do GMGN OpenAPI.</pre>"


@app.route("/debug/sign-test")
def debug_sign_test():
    """Testa assinatura GMGN com um swap simulado — não executa trade real."""
    import json as _j
    from gmgn_client import GMGN_WALLET_ADDRESS, GMGN_PRIVATE_KEY, SOL_NATIVE_MINT, _load_ed25519_key
    import time, base64
    from uuid import uuid4

    lines = []
    lines.append(f"GMGN_WALLET_ADDRESS : {GMGN_WALLET_ADDRESS or '❌ NÃO CONFIGURADO'}")
    lines.append(f"GMGN_PRIVATE_KEY    : {'✅ SET (' + str(len(GMGN_PRIVATE_KEY)) + ' chars)' if GMGN_PRIVATE_KEY else '❌ NÃO CONFIGURADO'}")

    pk = _load_ed25519_key(GMGN_PRIVATE_KEY)
    lines.append(f"Chave carregada     : {'✅ OK' if pk else '❌ FALHOU'}")

    if pk and GMGN_WALLET_ADDRESS:
        # monta body idêntico ao swap real
        dummy_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # USDC como placeholder
        lamports   = 5_000_000  # ~0.005 SOL
        body       = {
            "chain":        "sol",
            "from_address": GMGN_WALLET_ADDRESS,
            "input_token":  SOL_NATIVE_MINT,
            "output_token": dummy_mint,
            "input_amount": str(lamports),
            "slippage":     500,
        }
        body_str = _j.dumps(body, separators=(",", ":"))
        ts       = str(int(time.time()))
        cid      = str(uuid4())
        params   = {"timestamp": ts, "client_id": cid}
        qs       = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        subpath  = "/v1/trade/swap"
        message  = f"{subpath}:{qs}:{body_str}:{ts}"
        sig      = base64.b64encode(pk.sign(message.encode("utf-8"))).decode("ascii")

        lines.append("")
        lines.append(f"Body JSON           : {body_str}")
        lines.append(f"Sorted QS           : {qs}")
        lines.append(f"Message (signed)    : {message}")
        lines.append(f"Signature (b64)     : {sig[:30]}...")
        lines.append("")
        lines.append("✅ Assinatura gerada com sucesso — formato OK")
        lines.append("Se ainda receber 401, o problema é:")
        lines.append("  1. GMGN_WALLET_ADDRESS não corresponde à GMGN_PRIVATE_KEY")
        lines.append("  2. IP não está na whitelist GMGN")
        lines.append("  3. A chave não está registrada na conta GMGN")

    return "<pre>" + "\n".join(lines) + "</pre>"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
