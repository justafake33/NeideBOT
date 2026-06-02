import os
import logging
import statistics
from datetime import datetime, timezone, timedelta

import csv
import io
from flask import Flask, render_template_string, redirect, Response, request, stream_with_context

import db
from monitoring import init_sentry
from gmgn_client import GmgnClient, get_sol_usd as _get_sol_usd
from scheduler import HeapScheduler
from scanner import Scanner, HotMonitor
from executor import TradeExecutor, TradeMonitor

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

def fmt_mc_full(v):
    """Full number with comma separator — for precision comparison (e.g. trades dashboard)."""
    if v is None: return "—"
    if v >= 1_000_000: return f"${v:,.0f}"
    return f"${v:,.0f}"

def fmt_pct(v):
    if v is None: return "—"
    return f"{v*100:+.1f}%"

def fmt_ratio(v):
    if v is None: return "—"
    return f"{v:.2f}x"

def fmt_ts(dt, tz_delta=BRT):
    if dt is None: return "—"
    if isinstance(dt, str): return dt
    local = dt + tz_delta
    return local.strftime("%m/%d %H:%M")

def fmt_duration(seconds):
    if seconds is None: return "—"
    if seconds < 60: return f"{int(seconds)}s"
    minutes = seconds / 60
    if minutes < 60: return f"{minutes:.1f}m"
    hours = minutes / 60
    if hours < 24: return f"{hours:.1f}h"
    return f"{hours/24:.1f}d"


# ==============================================================================
# GLOBAL STATE
# ==============================================================================

scanner = None
executor = None
monitor = None
gmgn = None


@app.route("/")
def index():
    return redirect("/scanner")


@app.route("/health")
def health():
    return {"status": "ok"}, 200


@app.route("/scanner")
def route_scanner():
    """Real-time migration + watchlist + bounces."""
    from datetime import datetime, timezone
    
    try:
        with db._cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) as n
                FROM migrations
                WHERE mc_migration IS NOT NULL
            """)
            total_migs = cur.fetchone()["n"] or 0
            
            cur.execute("""
                SELECT
                    m.mint, m.token_nome, m.ts_migration, m.mc_migration,
                    m.mc_min, m.pct_drop, m.time_to_bottom,
                    m.smart_wallets, m.rug_ratio, m.top10_holder_rate,
                    bs.mc_at_signal, bs.bounce_pct, bs.elapsed_sec,
                    CASE
                        WHEN t.status = 'tp' THEN '✅ TP'
                        WHEN t.status = 'sl' THEN '❌ SL'
                        WHEN t.status = 'open' THEN '⏳ OPEN'
                        ELSE '—'
                    END as outcome
                FROM migrations m
                LEFT JOIN bounce_signals bs ON m.mint = bs.mint
                LEFT JOIN trades t ON m.mint = t.mint AND t.status IN ('tp', 'sl', 'open')
                WHERE m.ts_migration > NOW() - INTERVAL '7 days'
                ORDER BY m.ts_migration DESC
                LIMIT 100
            """)
            rows = cur.fetchall()
        
        html = """<!DOCTYPE html>
<html>
<head>
    <title>NeideBOT Scanner</title>
    <meta http-equiv="refresh" content="30">
    <style>
        body { font-family: monospace; font-size: 12px; padding: 16px; background: #fafafa; }
        table { border-collapse: collapse; width: 100%; }
        th, td { padding: 4px 8px; border: 1px solid #ccc; text-align: left; }
        th { background: #eee; font-weight: bold; }
        .tp { background: #c8f7c5; }
        .sl { background: #fdc7c7; }
        .open { background: #fff3cd; }
        a { color: #0060df; text-decoration: none; }
        a:hover { text-decoration: underline; }
    </style>
</head>
<body>
<h2>🔍 Scanner — Migrações recentes (últimos 7 dias)</h2>
<p>Total: <b>{}</b> | Auto-refresh 30s</p>

<table>
<thead><tr>
<th>Hora</th><th>Token</th><th>MC</th><th>Drop</th><th>TTB</th>
<th>Smart</th><th>Rug</th><th>Top10</th>
<th>Bounce</th><th>Outcome</th><th>Links</th>
</tr></thead>
<tbody>
{}
</tbody>
</table>
</body>
</html>"""
        
        rows_html = ""
        for r in rows:
            symbol = r["token_nome"] or r["mint"][:8]
            ts = fmt_ts(r["ts_migration"])
            mc = fmt_mc(r["mc_migration"])
            drop = f"{r.get('pct_drop', 0)*100:+.0f}%" if r.get("pct_drop") else "—"
            ttb = f"{r.get('time_to_bottom', 0):.0f}m" if r.get("time_to_bottom") else "—"
            smart = r.get("smart_wallets") or "—"
            rug = f"{r.get('rug_ratio', 0)*100:.0f}%" if r.get("rug_ratio") else "—"
            top10 = f"{r.get('top10_holder_rate', 0)*100:.0f}%" if r.get("top10_holder_rate") else "—"
            bounce = f"{r.get('bounce_pct', 0)*100:+.0f}%" if r.get("bounce_pct") else "—"
            outcome = r.get("outcome") or "—"
            outcome_class = "tp" if "TP" in outcome else ("sl" if "SL" in outcome else ("open" if "OPEN" in outcome else ""))
            links = f'<a href="https://gmgn.ai/sol/token/{r["mint"]}" target="_blank">GMGN</a> | <a href="https://dexscreener.com/solana/{r["mint"]}" target="_blank">DS</a>'
            
            rows_html += f"""<tr>
<td>{ts}</td>
<td><b>{symbol}</b></td>
<td style="text-align:right">{mc}</td>
<td style="text-align:right">{drop}</td>
<td style="text-align:right">{ttb}</td>
<td style="text-align:right">{smart}</td>
<td style="text-align:right">{rug}</td>
<td style="text-align:right">{top10}</td>
<td style="text-align:right">{bounce}</td>
<td class="{outcome_class}">{outcome}</td>
<td>{links}</td>
</tr>"""
        
        return render_template_string(html.format(total_migs, rows_html))
    except Exception as e:
        return f"<pre>Erro ao carregar scanner:\n{e}</pre>", 500


@app.route("/debug/pool-deep-tokens")
def debug_pool_deep_tokens():
    """Tokens com pool_quote > threshold: fundo capturado + outcome simulado + links."""
    from collections import defaultdict
    from datetime import timedelta, timezone

    days       = int(request.args.get("days", 14))
    pool_min   = float(request.args.get("pool_min", 90))
    min_drop   = float(request.args.get("min_drop", 0))   # ex: 0.40 = só eventos com dump ≥ 40%
    entry_pct  = float(request.args.get("entry_pct", 0.10))
    tp_pct     = float(request.args.get("tp_pct", 0.75))
    sl_pct     = float(request.args.get("sl_pct", 0.25))

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    import psycopg2, psycopg2.extras
    conn = psycopg2.connect(db.DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ── 1. Bounces históricos com pool_quote filtrado (completo, não apenas V2 Fase 1)
    cur.execute("""
        SELECT
            bs.mint, m.token_nome as symbol, bs.ts_signal as ts_min, m.ts_migration,
            bs.mc_min, m.mc_migration,
            (bs.mc_at_signal - bs.mc_min) / NULLIF(bs.mc_min, 0) as bounce_pct_now,
            (m.mc_migration - bs.mc_min) / NULLIF(m.mc_migration, 0) as drop_from_mig,
            EXTRACT(EPOCH FROM (bs.ts_signal - m.ts_migration)) / 60.0 as elapsed_sec,
            bs.pool_quote,
            0.0 as pre_10s_net,
            0.0 as pre_30s_net,
            0.0 as rise_5s_net,
            0.0 as rise_10s_net,
            EXTRACT(EPOCH FROM (bs.ts_signal - bs.ts_min)) / 60.0 as bottom_age_sec
        FROM bounce_signals bs
        JOIN migrations m ON bs.mint = m.mint
        WHERE bs.ts_signal >= %(cutoff)s
          AND bs.pool_quote >= %(pool_min)s
          AND (m.mc_migration - bs.mc_min) / NULLIF(m.mc_migration, 0) >= %(min_drop)s
          AND bs.mc_min IS NOT NULL
          AND bs.ts_min IS NOT NULL
        ORDER BY bs.ts_signal DESC
    """, {"cutoff": cutoff, "pool_min": pool_min, "min_drop": min_drop})
    bottoms = [dict(r) for r in cur.fetchall()]

    rows = []
    if bottoms:
        mints     = list({b["mint"] for b in bottoms})
        ts_floor  = min(b["ts_min"] for b in bottoms)
        ts_ceil   = max(b["ts_min"] for b in bottoms) + timedelta(hours=1, minutes=5)

        # ── 2. Snapshots erpc por (mint, ts_min): preço real pós-fundo (2s..60s) ──
        # Usados ANTES dos token_ticks para evitar artefato da vela do fundo.
        cur.execute("""
            SELECT mint, ts_min, ts_snapshot, bottom_age_sec, mc_current
            FROM micro_absorption_snapshots
            WHERE mint = ANY(%(mints)s)
              AND ts_snapshot >= %(ts_floor)s
              AND mc_current IS NOT NULL
            ORDER BY mint, ts_min, bottom_age_sec
        """, {"mints": mints, "ts_floor": ts_floor})
        # erpc_by_key[(mint, ts_min)] = lista ordenada de {ts_snapshot, mc_current}
        erpc_by_key = defaultdict(list)
        for r in cur.fetchall():
            key = (r["mint"], r["ts_min"])
            erpc_by_key[key].append({"ts": r["ts_snapshot"], "mc": float(r["mc_current"] or 0)})

        # ── 3. Token ticks para a janela de 60min (pós primeiro minuto) ──────────
        cur.execute("""
            SELECT mint, tick_ts, mc, mc_open, mc_high, mc_low, source
            FROM token_ticks
            WHERE mint = ANY(%(mints)s)
              AND tick_ts >  %(ts_floor)s
              AND tick_ts <= %(ts_ceil)s
            ORDER BY mint, tick_ts
        """, {"mints": mints, "ts_floor": ts_floor, "ts_ceil": ts_ceil})
        ticks_by_mint = defaultdict(list)
        for t in cur.fetchall():
            ticks_by_mint[t["mint"]].append(dict(t))

        # ── 4. Simula TP/SL para cada bottom ─────────────────────────────────────
        entry_mult = 1.0 + entry_pct
        tp_mult    = 1.0 + tp_pct
        sl_mult    = 1.0 - sl_pct

        for b in bottoms:
            mc_min_v   = b["mc_min"] or 0
            ts_min_v   = b["ts_min"]
            ts_end     = ts_min_v + timedelta(minutes=60)
            entry_mc   = mc_min_v * entry_mult
            tp_mc      = entry_mc * tp_mult
            sl_mc      = entry_mc * sl_mult

            # Fase 1: snapshots erpc (ts_min + 2s .. ts_min + 60s)
            # Preço real post-fundo capturado pelo HeliusWS — sem artefato de OHLC.
            erpc_snaps = erpc_by_key.get((b["mint"], ts_min_v), [])

            ts_tp = ts_sl = None
            max_mc = entry_mc

            for s in erpc_snaps:
                if s["ts"] <= ts_min_v:
                    continue  # snap é do fundo ou antes — pula
                mc_v = s["mc"]
                if mc_v > max_mc:
                    max_mc = mc_v
                if mc_v >= tp_mc and ts_tp is None:
                    ts_tp = s["ts"]
                if mc_v <= sl_mc and ts_sl is None:
                    ts_sl = s["ts"]
                if ts_tp and ts_sl:
                    break

            # Fase 2: token_ticks — apenas candles INTEIRAMENTE após o fundo (60s gap)
            # Ignora o primeiro candle kline que CONTÉM ts_min (mc_high é da descida).
            ts_post_fundo = ts_min_v + timedelta(seconds=60)
            window = [t for t in ticks_by_mint[b["mint"]]
                      if t["tick_ts"] > ts_post_fundo and t["tick_ts"] <= ts_end]

            for t in window:
                if ts_tp and ts_sl:
                    break
                if t["source"] == "kline":
                    hi = t["mc_high"] or t["mc"] or 0
                    lo = t["mc_low"]  or t["mc"] or 0
                else:
                    hi = lo = t["mc"] or 0
                if hi > max_mc:
                    max_mc = hi
                tp_hit = hi >= tp_mc
                sl_hit = lo <= sl_mc
                if tp_hit and sl_hit:
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
                outcome = "tp" if ts_tp <= ts_sl else "sl"
            elif ts_tp:
                outcome = "tp"
            elif ts_sl:
                outcome = "sl"
            else:
                outcome = "no_exit"

            exit_ts = ts_tp if outcome == "tp" else (ts_sl if outcome == "sl" else None)
            min_exit = round((exit_ts - ts_min_v).total_seconds() / 60, 1) if exit_ts else None
            max_gain = round((max_mc / entry_mc - 1) * 100, 1) if entry_mc > 0 else None

            rows.append({
                **b,
                "entry_mc":   entry_mc,
                "tp_mc":      tp_mc,
                "sl_mc":      sl_mc,
                "outcome":    outcome,
                "n_ticks":    len(window),
                "min_exit":   min_exit,
                "max_gain":   max_gain,
            })

    cur.close()
    conn.close()

    # Sort by ts_min DESC
    rows.sort(key=lambda r: r["ts_min"], reverse=True)

    # ── Sumário ────────────────────────────────────────────────────────────────
    n_tp  = sum(1 for r in rows if r["outcome"] == "tp")
    n_sl  = sum(1 for r in rows if r["outcome"] == "sl")
    n_ne  = sum(1 for r in rows if r["outcome"] == "no_exit")
    n_tot = len(rows)
    wr    = n_tp / (n_tp + n_sl) * 100 if (n_tp + n_sl) > 0 else 0
    ev    = (n_tp * tp_pct - (n_sl + n_ne) * sl_pct) / n_tot * 100 if n_tot > 0 else 0

    def _fmt(v, prefix="$", decimals=0):
        if v is None: return "—"
        if decimals == 0: return f"{prefix}{v:,.0f}"
        return f"{prefix}{v:,.{decimals}f}"

    def _pct(v):
        if v is None: return "—"
        return f"{v:+.1f}%"

    def _outcome_cell(o):
        if o == "tp":
            return '<td style="background:#c8f7c5;font-weight:bold;text-align:center">✅ TP</td>'
        if o == "sl":
            return '<td style="background:#fdc7c7;font-weight:bold;text-align:center">❌ SL</td>'
        return '<td style="background:#fff3cd;text-align:center">⏳ no_exit</td>'

    def _flow(v):
        if v is None: return "—"
        color = "#1a7f37" if v > 0 else ("#cc0000" if v < 0 else "#888")
        return f'<span style="color:{color}">{v:+.2f}</span>'

    # Build rows HTML
    rows_html = ""
    for r in rows:
        mint = r["mint"] or ""
        sym  = r["symbol"] or mint[:8]
        ts   = r["ts_min"].strftime("%m/%d %H:%M") if r["ts_min"] else "—"
        pool = f"{r['pool_quote']:.0f}" if r.get("pool_quote") else "—"
        mc_m = _fmt(r.get("mc_migration"))
        mc_b = _fmt(r.get("mc_min"))
        drop_val = r.get("drop_from_mig") or 0
        drop_color = "#1a7f37" if drop_val >= 0.40 else ("#cc0000" if drop_val < 0.20 else "#856404")
        drop = f'<span style="color:{drop_color};font-weight:bold">{drop_val*100:+.1f}%</span>'
        bpct = _pct(r.get("bounce_pct_now") and r["bounce_pct_now"] * 100)
        ela  = f"{r['elapsed_sec']:.0f}s" if r.get("elapsed_sec") else "—"
        ent  = _fmt(r.get("entry_mc"))
        tp_v = _fmt(r.get("tp_mc"))
        sl_v = _fmt(r.get("sl_mc"))
        ntk  = r.get("n_ticks", 0)
        mex  = f"{r['min_exit']}m" if r.get("min_exit") else "—"
        mg   = _pct(r.get("max_gain"))
        p10  = _flow(r.get("pre_10s_net"))
        r5   = _flow(r.get("rise_5s_net"))
        age  = f"{r.get('bottom_age_sec','?')}s"
        gmgn = f'<a href="https://gmgn.ai/sol/token/{mint}" target="_blank">GMGN</a>'
        dex  = f'<a href="https://dexscreener.com/solana/{mint}" target="_blank">DS</a>'

        rows_html += f"""<tr>
  <td>{ts}</td>
  <td><b>{sym}</b></td>
  <td style="font-size:10px">{mint[:12]}…</td>
  <td style="text-align:right"><b>{pool}</b></td>
  <td style="text-align:right">{mc_m}</td>
  <td style="text-align:right">{mc_b}</td>
  <td style="text-align:right">{drop}</td>
  <td style="text-align:right">{ela}</td>
  <td style="text-align:right">{ent}</td>
  <td style="text-align:right">{tp_v}</td>
  <td style="text-align:right">{sl_v}</td>
  <td style="text-align:center">{ntk}</td>
  {_outcome_cell(r["outcome"])}
  <td style="text-align:center">{mex}</td>
  <td style="text-align:right">{mg}</td>
  <td style="text-align:right">{p10}</td>
  <td style="text-align:right">{r5}</td>
  <td style="text-align:center">{age}</td>
  <td>{gmgn} {dex}</td>
</tr>"""

    wr_color = "#1a7f37" if wr >= 40 else ("#cc0000" if wr < 25 else "#856404")
    ev_color = "#1a7f37" if ev > 0 else "#cc0000"

    html = f"""<!DOCTYPE html><html><head><title>Pool Deep Tokens</title>
<meta http-equiv="refresh" content="60">
<style>
body{{font-family:monospace;font-size:12px;padding:16px;background:#fafafa}}
h2{{margin-bottom:6px}}
table{{border-collapse:collapse;width:100%}}
td,th{{padding:3px 6px;border:1px solid #ccc;white-space:nowrap}}
th{{background:#eee;text-align:left}}
.card{{display:inline-block;margin:3px;padding:5px 10px;background:#fff;
       border:1px solid #ddd;border-radius:4px;text-align:center;min-width:90px}}
.card b{{display:block;font-size:15px}}
.card small{{color:#888;font-size:10px}}
a{{color:#0060df}}
</style></head><body>
<h2>🏊 Tokens com Pool &gt; {pool_min:.0f} SOL{f" · Drop ≥ {min_drop:.0%}" if min_drop > 0 else ""} — últimos {days} dias</h2>
<p style="color:#666;font-size:11px">
  Simulação: entry +{entry_pct:.0%} do fundo | TP +{tp_pct:.0%} | SL -{sl_pct:.0%} |
  Pool: <a href="?days={days}&pool_min=50&min_drop={min_drop}">50+</a>
  <a href="?days={days}&pool_min=70&min_drop={min_drop}">70+</a>
  <a href="?days={days}&pool_min=90&min_drop={min_drop}">90+</a>
  <a href="?days={days}&pool_min=120&min_drop={min_drop}">120+</a> |
  Drop mín: <a href="?days={days}&pool_min={pool_min:.0f}&min_drop=0">sem filtro</a>
  <a href="?days={days}&pool_min={pool_min:.0f}&min_drop=0.20">20%+</a>
  <a href="?days={days}&pool_min={pool_min:.0f}&min_drop=0.30">30%+</a>
  <a href="?days={days}&pool_min={pool_min:.0f}&min_drop=0.40">40%+</a>
  <a href="?days={days}&pool_min={pool_min:.0f}&min_drop=0.50">50%+</a>
  <a href="?days={days}&pool_min={pool_min:.0f}&min_drop=0.60">60%+</a> |
  Dias: <a href="?days=7&pool_min={pool_min:.0f}&min_drop={min_drop}">7d</a>
  <a href="?days=14&pool_min={pool_min:.0f}&min_drop={min_drop}">14d</a>
  <a href="?days=30&pool_min={pool_min:.0f}&min_drop={min_drop}">30d</a>
</p>

<div style="margin-bottom:10px">
  <div class="card"><b>{n_tot}</b><small>eventos</small></div>
  <div class="card"><b style="color:{wr_color}">{wr:.1f}%</b><small>WR (TP/TP+SL)</small></div>
  <div class="card"><b style="color:{ev_color}">{ev:+.1f}%</b><small>EV/trade</small></div>
  <div class="card"><b style="color:#1a7f37">{n_tp}</b><small>TP</small></div>
  <div class="card"><b style="color:#cc0000">{n_sl}</b><small>SL</small></div>
  <div class="card"><b style="color:#856404">{n_ne}</b><small>no_exit</small></div>
</div>

<table>
<thead><tr>
  <th>Hora</th><th>Token</th><th>Mint</th>
  <th>Pool SOL</th><th>MC Mig.</th><th>MC Fundo</th>
  <th>Drop%</th><th>t→fundo</th>
  <th>Entry</th><th>TP alvo</th><th>SL alvo</th>
  <th>Ticks</th><th>Outcome</th><th>Saída</th><th>Max Gain</th>
  <th>Pre10</th><th>Rise5</th><th>Snap age</th><th>Links</th>
</tr></thead>
<tbody>
{rows_html if rows_html else '<tr><td colspan="19" style="text-align:center;color:#888">Nenhum evento encontrado com pool ≥ ' + str(pool_min) + ' SOL nos últimos ' + str(days) + ' dias</td></tr>'}
</tbody>
</table>

<p style="color:#888;font-size:10px;margin-top:10px">
  Auto-refresh 60s | Drop% = queda da migração ao fundo | t→fundo = elapsed desde migração |
  Pre10 / Rise5 = fluxo líquido SOL (verde=compra, vermelho=venda) |
  Snap age = antiguidade do snapshot no momento da captura |
  Outcome simulado a partir do mc_min capturado pelo erpc, SEM execução real
</p>
</body></html>"""
    return render_template_string(html)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
