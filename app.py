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
# /analise — Backtest exploratório (trade de minutos)
# ==============================================================================

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

    def _wr(grp):
        if not grp: return None, 0
        w = sum(1 for x in grp if x["mc_1h"] > x["mc_bottom"])
        return w/len(grp), len(grp)

    base_wr, base_n = _wr(scored)
    r["base_wr"] = f"{base_wr*100:.1f}%" if base_wr is not None else "—"
    r["base_n"]  = base_n

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
        w, n = _wr(grp)
        if w is None:
            continue
        delta = (w - base_wr) * 100 if base_wr else 0
        filter_rows.append({
            "label": label,
            "n":     n,
            "wr":    f"{w*100:.1f}%",
            "delta": f"{delta:+.1f}pp",
            "pos":   delta > 0,
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
<p class="note" style="margin-bottom:10px">Baseline win rate (mc_1h &gt; bottom): <strong>{{ d.base_wr }}</strong> (n={{ d.base_n }})</p>
<table>
<thead><tr><th>Filtro</th><th>N</th><th>Win Rate</th><th>Delta vs Baseline</th></tr></thead>
<tbody>
{% for f in d.filter_rows %}
<tr>
  <td>{{ f.label }}</td>
  <td>{{ f.n }}</td>
  <td>{{ f.wr }}</td>
  <td><span class="tag {{ 't-green' if f.pos else 't-red' }}">{{ f.delta }}</span></td>
</tr>
{% endfor %}
</tbody>
</table>

<!-- Seção 5: R:R -->
<h2>5 — R:R Simulado (entrada no fundo, saída = mc_1h)</h2>
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

</body>
</html>
"""


@app.route("/analise")
def analise():
    rows = db.get_all_migrations(limit=10000)
    d = _run_analysis(rows)
    return render_template_string(ANALISE_HTML, d=d)


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
