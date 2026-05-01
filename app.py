import os
import logging
from datetime import timezone, timedelta

import csv
import io
from flask import Flask, render_template_string, redirect, Response

import db
from monitoring import init_sentry
from gmgn_client import GmgnClient
from scheduler import HeapScheduler
from scanner import Scanner, HotMonitor

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


@app.route("/health")
def health():
    return {"status": "ok"}, 200


# ==============================================================================
# Startup — funciona com Gunicorn e execução direta
# ==============================================================================

def _start_services():
    if not os.getenv("DATABASE_URL"):
        logger.warning("DATABASE_URL não configurado — serviços não iniciados")
        return
    db.init_db()
    gmgn      = GmgnClient()
    scheduler = HeapScheduler()
    hot       = HotMonitor(gmgn, scheduler)
    scanner   = Scanner(gmgn, hot)
    scheduler.start()
    hot.start()
    scanner.start()
    logger.info("Todos os serviços iniciados")


_start_services()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
