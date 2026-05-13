import os
import logging
import statistics
from datetime import timezone, timedelta

import csv
import io
from flask import Flask, render_template_string, redirect, Response, request

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
                t.tick_ts         AS ts,
                t.elapsed_sec,
                t.mc,
                t.mc_open,
                t.mc_high,
                t.mc_low,
                t.volume_1m,
                t.smart_count,
                t.renowned_count,
                t.holders_count,
                t.source,
                EXTRACT(EPOCH FROM (t.tick_ts - m.ts_migration)) AS sec_since_migration
            FROM token_ticks t
            JOIN migrations m ON m.mint = t.mint
            ORDER BY t.mint, t.tick_ts
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
                b.mc_min                       AS mc_bottom,
                b.mc_at_signal                 AS mc_entry,
                b.mc_migration,
                b.elapsed_sec,
                b.elapsed_sec / 60.0           AS elapsed_min,
                b.drop_from_mig                AS pct_drop,
                b.bounce_pct,
                m.mc_peak,
                m.mc_1h,
                m.time_to_bottom,
                m.time_to_peak,
                m.recovery,
                CASE
                  WHEN m.mc_1h IS NOT NULL AND b.mc_min IS NOT NULL
                  THEN (m.mc_1h - b.mc_min) / NULLIF(b.mc_min, 0)
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
def export_bounce_ticks():
    """Exporta ticks dos tokens que tiveram bounce_signal — ideal para estudar padrão pós-bounce."""
    with db._cursor() as cur:
        cur.execute("""
            SELECT
                t.mint,
                m.token_nome                    AS symbol,
                b.ts_signal                     AS ts_bounce,
                b.mc_min                        AS mc_bottom,
                b.mc_migration,
                b.elapsed_sec / 60.0            AS elapsed_min_at_bounce,
                b.drop_from_mig                 AS pct_drop,
                b.bounce_pct,
                m.recovery,
                t.tick_ts                                  AS ts,
                t.elapsed_sec,
                EXTRACT(EPOCH FROM (t.tick_ts - b.ts_signal)) AS sec_after_bounce,
                t.mc,
                t.mc_open,
                t.mc_high,
                t.mc_low,
                t.volume_1m,
                t.source,
                (t.mc - b.mc_min) / NULLIF(b.mc_min,0)              AS gain_vs_bottom,
                (t.mc - b.mc_min * 1.15) / NULLIF(b.mc_min * 1.15,0) AS gain_vs_entry
            FROM token_ticks t
            JOIN bounce_signals b ON b.mint = t.mint
            JOIN migrations m ON m.mint = t.mint
            WHERE t.tick_ts >= b.ts_signal - INTERVAL '2 minutes'
            ORDER BY t.mint, t.tick_ts
        """)
        rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return "Sem ticks de bounces", 404
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=bounce_ticks.csv"},
    )


@app.route("/analise/trailing")
def analise_trailing():
    """
    Análise data-driven para calibrar trailing profit — metodologia big player.

    3 perguntas respondidas com dados reais:
      1. Universo utilizável: quantos bounces tinham pico DEPOIS do sinal?
         (Apenas esses são candidatos reais ao trailing)
      2. Intra-rally volatility CORRETA: drawdown durante a subida real
         (ticks de ts_signal até ts_peak estimado, apenas bounces válidos)
      3. Simulação head-to-head: trailing vs TP fixo 75%
         usando os picos reais de cada token — sem chutar parâmetros
    """
    def _pcts(values, ps=(25, 50, 75, 90, 95)):
        if not values: return {p: None for p in ps}
        s = sorted(values)
        n = len(s)
        out = {}
        for p in ps:
            idx = (p / 100) * (n - 1)
            lo, hi = int(idx), min(int(idx) + 1, n - 1)
            out[p] = s[lo] + (idx - lo) * (s[hi] - s[lo])
        return out

    def _fp(v, mult=100, dec=1):
        return f"{v*mult:.{dec}f}%" if v is not None else "—"
    def _fm(v, dec=1):
        return f"{v:.{dec}f}min" if v is not None else "—"
    def _fc(v, dec=1):
        return f"{v:.{dec}f}" if v is not None else "—"

    with db._cursor() as cur:

        # ── SEÇÃO 1: classificar todos os bounces em válidos vs inválidos ──────
        # Válido = pico chegou DEPOIS do sinal. Inválido = já tínhamos passado do pico.
        cur.execute("""
            SELECT
                b.mint,
                b.symbol,
                b.ts_signal,
                b.mc_min                                                AS mc_bottom,
                b.mc_at_signal                                          AS mc_entry_ref,
                b.elapsed_sec / 60.0                                    AS elapsed_min,
                b.drop_from_mig,
                b.bounce_pct,
                m.mc_peak,
                m.time_to_peak,
                m.recovery,
                m.time_to_bottom,
                -- ganho máximo a partir do entry (mc_bottom * 1.15)
                (m.mc_peak - b.mc_min * 1.15) / NULLIF(b.mc_min * 1.15, 0) AS gain_from_entry,
                -- minutos entre o sinal e o pico (negativo = pico foi antes do sinal)
                m.time_to_peak - (b.elapsed_sec / 60.0)                AS min_signal_to_peak
            FROM bounce_signals b
            JOIN migrations m ON m.mint = b.mint
            WHERE b.mc_min > 0
              AND m.mc_peak IS NOT NULL
        """)
        all_rows = [dict(r) for r in cur.fetchall()]

        # ── SEÇÃO 2: intra-rally drawdown CORRETO ─────────────────────────────
        # Apenas bounces válidos (pico depois do sinal).
        # Ticks somente do intervalo [ts_signal, ts_peak_estimado].
        # Mede a maior queda de qualquer running_max DURANTE a subida real.
        cur.execute("""
            WITH valid_bounces AS (
                SELECT
                    b.mint,
                    b.ts_signal,
                    b.mc_min * 1.15                                     AS mc_entry,
                    -- estima ts do pico como ts_signal + (time_to_peak - elapsed_min) minutos
                    b.ts_signal + ((m.time_to_peak - b.elapsed_sec/60.0) * 60)::int
                        * INTERVAL '1 second'                           AS ts_peak_est
                FROM bounce_signals b
                JOIN migrations m ON m.mint = b.mint
                WHERE b.mc_min > 0
                  AND m.time_to_peak IS NOT NULL
                  AND m.time_to_peak > b.elapsed_sec / 60.0   -- pico depois do sinal
            ),
            rally_ticks AS (
                SELECT
                    vb.mint,
                    vb.mc_entry,
                    t.tick_ts,
                    COALESCE(t.mc_high, t.mc)  AS mc_high,
                    COALESCE(t.mc_low,  t.mc)  AS mc_low,
                    MAX(COALESCE(t.mc_high, t.mc)) OVER (
                        PARTITION BY vb.mint
                        ORDER BY t.tick_ts
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    )                          AS running_max
                FROM valid_bounces vb
                JOIN token_ticks t ON t.mint = vb.mint
                WHERE t.tick_ts >= vb.ts_signal      -- após o sinal
                  AND t.tick_ts <= vb.ts_peak_est    -- antes do pico
            ),
            drawdowns AS (
                SELECT
                    mint,
                    mc_entry,
                    (running_max - mc_low) / NULLIF(running_max, 0) AS dd_pct
                FROM rally_ticks
                WHERE running_max > mc_entry   -- só conta drawdown após entrar em lucro
                  AND mc_low > 0
            )
            SELECT
                mint,
                MAX(dd_pct)  AS max_intra_rally_dd,
                AVG(dd_pct)  AS avg_dd,
                COUNT(*)     AS n_points
            FROM drawdowns
            GROUP BY mint
            HAVING COUNT(*) >= 3
            ORDER BY max_intra_rally_dd
        """)
        dd_rows = [dict(r) for r in cur.fetchall()]

    # ── Classificação ────────────────────────────────────────────────────────
    valid   = [r for r in all_rows if r["min_signal_to_peak"] is not None and r["min_signal_to_peak"] > 0]
    invalid = [r for r in all_rows if r["min_signal_to_peak"] is not None and r["min_signal_to_peak"] <= 0]
    no_peak = [r for r in all_rows if r["min_signal_to_peak"] is None]
    n_total = len(all_rows)

    gains_valid = [r["gain_from_entry"] for r in valid if r["gain_from_entry"] is not None]
    gains_invalid = [r["gain_from_entry"] for r in invalid if r["gain_from_entry"] is not None]
    ttp_valid = [r["min_signal_to_peak"] for r in valid if r["min_signal_to_peak"] is not None]

    pct_valid   = _pcts(gains_valid)
    pct_invalid = _pcts(gains_invalid)

    # Thresholds só nos válidos (onde a estratégia poderia ter funcionado)
    thresholds = [0.30, 0.50, 0.75, 1.00, 1.50, 2.00, 3.00, 5.00]
    thresh = {}
    for t in thresholds:
        n = sum(1 for g in gains_valid if g is not None and g >= t)
        thresh[t] = {"n": n, "pct": n / len(gains_valid) * 100 if gains_valid else 0}

    # Intra-rally drawdown (corrigido)
    dd_vals = [float(r["max_intra_rally_dd"]) for r in dd_rows if r["max_intra_rally_dd"] is not None]
    dd_pcts = _pcts(dd_vals) if dd_vals else {}

    # ── SEÇÃO 3: Simulação head-to-head ──────────────────────────────────────
    # Para cada bounce válido com pico conhecido, simula diferentes estratégias
    # usando o pico real como referência (melhor caso para trailing).
    # Sem ticks: assume que o trailing dispara exatamente no pico × (1 - drawdown_rate).
    FIXED_TP  = 0.75
    FIXED_SL  = 0.15
    configs = [
        # (label, price_scale, drawdown_rate)
        ("TP fixo 75%",             None,  None ),
        ("Trailing 30% (ativ. 50%)", 0.50,  0.30),
        ("Trailing 30% (ativ. 75%)", 0.75,  0.30),
        ("Trailing 40% (ativ. 75%)", 0.75,  0.40),
        ("Trailing 50% (ativ. 75%)", 0.75,  0.50),
        ("Trailing 30% (ativ. 100%)",1.00,  0.30),
        ("Trailing 40% (ativ. 100%)",1.00,  0.40),
    ]

    sim_results = {}
    for label, ps, dr in configs:
        gains = []
        for r in valid:
            g = r.get("gain_from_entry")
            if g is None:
                continue
            if ps is None:
                # TP fixo: min(g, FIXED_TP) se g > 0, else g (SL)
                exit_gain = min(g, FIXED_TP) if g >= 0 else max(g, -FIXED_SL)
            else:
                if g >= ps:
                    # Trailing ativou: sai no pico × (1 - dr), convertido para gain
                    # peak_mc = entry × (1 + g)
                    # exit_mc = peak_mc × (1 - dr) = entry × (1 + g) × (1 - dr)
                    exit_gain = (1 + g) * (1 - dr) - 1
                    # Mas se o trailing exit for pior que o TP fixo (para picos pequenos),
                    # o TP fixo teria disparado primeiro — guarda o melhor entre os dois
                    # (na prática, com trailing puro, o TP fixo não existe; simulamos trailing puro)
                else:
                    # Trailing nunca ativou: TP fixo captura (se chegou lá) ou sai no pico
                    exit_gain = min(g, FIXED_TP) if g >= FIXED_TP else g
                    # Se caiu abaixo do SL: -SL (simplificado — não temos dados de SL aqui)
                    exit_gain = max(exit_gain, -FIXED_SL)
            gains.append(exit_gain)
        avg  = sum(gains) / len(gains) if gains else 0
        wins = sum(1 for g in gains if g >= FIXED_TP)
        bigs = sum(1 for g in gains if g >= 1.00)
        sim_results[label] = {
            "avg_gain": avg,
            "n": len(gains),
            "wins_75": wins,
            "wins_100": bigs,
            "p50": _pcts(gains).get(50),
            "p75": _pcts(gains).get(75),
        }

    # ── HTML ─────────────────────────────────────────────────────────────────
    CSS = """<!DOCTYPE html><html lang="pt-BR"><head>
<meta charset="utf-8"><title>Trailing — Análise Big Player</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Courier New', monospace; background: #0d1117; color: #c9d1d9;
       padding: 20px; font-size: 13px; }
h1 { color: #58a6ff; margin-bottom: 4px; }
h2 { color: #58a6ff; margin: 28px 0 10px; font-size: 15px;
     border-bottom: 1px solid #30363d; padding-bottom: 6px; }
.sub { color: #8b949e; margin-bottom: 20px; font-size: 12px; }
.nav { margin-bottom: 20px; display: flex; gap: 8px; flex-wrap: wrap; }
.nav a { color: #58a6ff; font-size: 12px; text-decoration: none;
         border: 1px solid #30363d; padding: 3px 10px; border-radius: 4px; }
.nav a:hover { background: #161b22; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px,1fr));
        gap: 16px; margin-bottom: 24px; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
.card-title { color: #8b949e; font-size: 11px; text-transform: uppercase;
              letter-spacing:.05em; margin-bottom: 12px; }
.row { display: flex; justify-content: space-between; padding: 5px 0;
       border-bottom: 1px solid #21262d; }
.row:last-child { border-bottom: none; }
.label { color: #8b949e; }
.val { font-weight: bold; }
.green { color: #3fb950; } .yellow { color: #d29922; } .red { color: #f85149; }
.blue  { color: #58a6ff; }
.insight { background: #0d2137; border-left: 3px solid #58a6ff; padding: 12px 16px;
           margin: 14px 0; border-radius: 0 6px 6px 0; line-height: 1.7; }
.warn { background: #1e1500; border-left: 3px solid #d29922; padding: 12px 16px;
        margin: 14px 0; border-radius: 0 6px 6px 0; line-height: 1.7; }
table { border-collapse: collapse; width: 100%; }
th { background: #161b22; color: #8b949e; padding: 7px 10px; text-align: left;
     border-bottom: 2px solid #30363d; font-size: 11px; text-transform: uppercase; }
td { padding: 5px 10px; border-bottom: 1px solid #21262d; }
tr:hover td { background: #161b22; }
.highlight td { background: #0d2137 !important; }
.badge { border-radius: 4px; padding: 1px 7px; font-size: 11px; font-weight: bold;
         display: inline-block; }
.b-green { background: #1a3a2a; color: #3fb950; }
.b-red   { background: #3a1a1a; color: #f85149; }
.b-blue  { background: #0d2137; color: #58a6ff; }
.stat-big { font-size: 28px; font-weight: bold; margin-top: 4px; }
</style></head><body>
<h1>Trailing Profit — Análise Big Player</h1>
<p class="sub">Metodologia: separa universo utilizável → mede volatilidade correta → simula P&L real</p>
<div class="nav">
  <a href="/analise">← Análise</a>
  <a href="/analise/backtest">Backtester</a>
  <a href="/export/bounces">CSV Bounces</a>
  <a href="/export/bounce-ticks">CSV Ticks</a>
</div>
"""

    # ── Seção 1: Universo ─────────────────────────────────────────────────────
    pct_valid_n   = len(valid) / n_total * 100 if n_total else 0
    pct_invalid_n = len(invalid) / n_total * 100 if n_total else 0

    html = CSS + f"""
<h2>1. Universo Utilizável — pico veio depois do sinal?</h2>
<p class="sub" style="margin-top:-6px">
  Trailing só funciona se o pico não passou ainda. Quantos dos {n_total} sinais eram utilizáveis?
</p>
<div class="grid">
  <div class="card">
    <div class="card-title">Válidos — pico DEPOIS do sinal ✅</div>
    <div class="stat-big green">{len(valid)}</div>
    <div style="color:#8b949e;font-size:12px;margin-top:6px">{pct_valid_n:.1f}% dos sinais</div>
    <div style="margin-top:12px;font-size:12px;color:#8b949e">
      Trailing pode funcionar — o token ainda vai subir após a entrada
    </div>
  </div>
  <div class="card">
    <div class="card-title">Inválidos — pico JÁ PASSOU ❌</div>
    <div class="stat-big red">{len(invalid)}</div>
    <div style="color:#8b949e;font-size:12px;margin-top:6px">{pct_invalid_n:.1f}% dos sinais</div>
    <div style="margin-top:12px;font-size:12px;color:#8b949e">
      Compramos depois do pico — trailing não salva, SL que limita o dano
    </div>
  </div>
  <div class="card">
    <div class="card-title">Tempo Sinal → Pico (válidos, {len(ttp_valid)} tokens)</div>
    {"".join(f'<div class="row"><span class="label">P{p}</span><span class="val">{_fm(_pcts(ttp_valid).get(p))}</span></div>' for p in [25,50,75,90,95])}
  </div>
</div>
<div class="warn">
  <b>Insight crítico:</b> {pct_invalid_n:.0f}% dos sinais disparam <em>depois</em> do pico.
  São tokens onde já compramos errado — independente do trailing.
  A análise de trailing a seguir usa <b>apenas os {len(valid)} válidos</b>.
</div>

<h2>2. Ganho Disponível nos Bounces Válidos</h2>
<p class="sub" style="margin-top:-6px">Distribuição do upside máximo a partir do entry (mc_bottom × 1.15) — apenas bounces onde pico veio depois</p>
<div class="grid">
  <div class="card">
    <div class="card-title">Percentis de Ganho Máximo ({len(gains_valid)} tokens)</div>
    {"".join(f'<div class="row"><span class="label">P{p}</span><span class="val {("green" if (pct_valid.get(p) or 0)>0.5 else "yellow")}">{_fp(pct_valid.get(p))}</span></div>' for p in [25,50,75,90,95])}
  </div>
  <div class="card">
    <div class="card-title">Tokens que Atingem Cada Nível</div>
    {"".join(f'<div class="row"><span class="label">+{t*100:.0f}%</span><span class="val {("green" if thresh[t]["pct"]>50 else "yellow")}">{thresh[t]["n"]} ({thresh[t]["pct"]:.0f}%)</span></div>' for t in thresholds)}
  </div>
  <div class="card">
    <div class="card-title">Ganho nos Inválidos (ref: o que perdemos)</div>
    {"".join(f'<div class="row"><span class="label">P{p}</span><span class="val red">{_fp(pct_invalid.get(p))}</span></div>' for p in [25,50,75,90,95])}
    <div style="color:#8b949e;font-size:11px;margin-top:8px">
      Pico JÁ passou — esse ganho nunca foi capturável
    </div>
  </div>
</div>
<div class="insight">
  <b>O que os dados dizem:</b> Nos bounces válidos, P50 = {_fp(pct_valid.get(50))} de ganho máximo.
  Nosso TP fixo em +75% está capturando menos que o mediano disponível em {thresh[0.75]["pct"]:.0f}% dos casos.
  {thresh[2.00]["pct"]:.0f}% dos tokens válidos vão além de +200% — são esses que o trailing captura e o TP fixo perde.
</div>

<h2>3. Volatilidade Intra-Rally — Drawdown Real Durante a Subida</h2>
<p class="sub" style="margin-top:-6px">
  Ticks apenas do intervalo [ts_signal → ts_peak_estimado], apenas tokens válidos,
  drawdown medido só após o token entrar em lucro (running_max > entry).
  Esse é o número real que o drawdown_rate precisa superar.
</p>
<div class="grid">
  <div class="card">
    <div class="card-title">Max Drawdown Intra-Rally ({len(dd_vals)} tokens c/ ticks)</div>
    {("".join(f'<div class="row"><span class="label">P{p}</span><span class="val {"red" if p>=75 else "yellow"}">{_fp(dd_pcts.get(p))}</span></div>' for p in [25,50,75,90,95])) if dd_pcts else "<div class='row'><span>Ticks insuficientes neste filtro</span></div>"}
  </div>
  <div class="card">
    <div class="card-title">Interpretação do Drawdown</div>
    <div class="row"><span class="label">drawdown_rate < P25</span><span class="val red">Shakeout frequente</span></div>
    <div class="row"><span class="label">drawdown_rate = P50</span><span class="val yellow">Shakeout em metade</span></div>
    <div class="row"><span class="label">drawdown_rate = P75</span><span class="val yellow">Shakeout em 25%</span></div>
    <div class="row"><span class="label">drawdown_rate > P90</span><span class="val green">Raro shakeout</span></div>
  </div>
</div>
<div class="insight">
  <b>Regra objetiva:</b> O drawdown_rate deve estar acima do P75 do intra-rally para não ser stopado
  por volatilidade normal durante a subida. Abaixo do P75 = damos saída ao trailing antes do pico real
  em 25%+ dos trades.
</div>

<h2>4. Simulação Head-to-Head — Estratégias vs TP Fixo 75%</h2>
<p class="sub" style="margin-top:-6px">
  Usando os picos reais de cada bounce válido como referência.
  Trailing = sai no pico × (1 − drawdown_rate). SL = −15% em todos.
  <b>Sem ticks</b>: assume trailing dispara exatamente no pico (melhor caso para trailing).
</p>
<table>
  <thead>
    <tr>
      <th>Estratégia</th>
      <th>Avg Gain</th>
      <th>P50 Gain</th>
      <th>P75 Gain</th>
      <th>Saídas ≥+75%</th>
      <th>Saídas ≥+100%</th>
      <th>N</th>
    </tr>
  </thead>
  <tbody>
"""
    best_avg = max(v["avg_gain"] for v in sim_results.values())
    for label, res in sim_results.items():
        is_best = abs(res["avg_gain"] - best_avg) < 0.001
        row_cls = ' class="highlight"' if is_best else ''
        label_badge = f'<span class="badge b-blue">{label}</span>' if is_best else label
        html += f"""
    <tr{row_cls}>
      <td>{label_badge}{"⭐" if is_best else ""}</td>
      <td class="{'green' if res['avg_gain']>0.75 else 'yellow' if res['avg_gain']>0 else 'red'}">{_fp(res['avg_gain'])}</td>
      <td>{_fp(res['p50'])}</td>
      <td>{_fp(res['p75'])}</td>
      <td>{res['wins_75']} ({res['wins_75']/res['n']*100:.0f}%)</td>
      <td>{res['wins_100']} ({res['wins_100']/res['n']*100:.0f}%)</td>
      <td>{res['n']}</td>
    </tr>"""

    html += """
  </tbody>
</table>
<div class="warn" style="margin-top:16px">
  <b>Limitação importante:</b> Esta simulação assume que o trailing dispara <em>exatamente</em> no pico — o melhor caso possível.
  Na prática, o GMGN rastreia o pico em tempo real com granularidade de 1min.
  Em tokens que picam e despencam em segundos (comum em memes), o trailing pode não disparar no pico exato.
  Use estes números como teto de EV, não como expectativa realista.
</div>

<h2>5. Detalhe — Bounces Válidos (pico depois do sinal)</h2>
<table>
  <thead><tr>
    <th>Token</th><th>Gain Máx (entry)</th><th>Sinal→Pico</th><th>Trailing 30%@75%</th><th>TP Fixo 75%</th><th>Delta</th><th>Recovery</th>
  </tr></thead>
  <tbody>
"""
    for r in sorted(valid, key=lambda x: x.get("gain_from_entry") or 0, reverse=True)[:50]:
        g = r.get("gain_from_entry")
        if g is None:
            continue
        tp_fixed   = min(g, FIXED_TP) if g >= FIXED_TP else max(g, -FIXED_SL)
        if g >= 0.75:
            tp_trail = (1 + g) * (1 - 0.30) - 1
        else:
            tp_trail = tp_fixed
        delta = tp_trail - tp_fixed
        rec = r.get("recovery")
        html += f"""
  <tr>
    <td><b>{r.get('symbol') or r['mint'][:8]}</b></td>
    <td class="{'green' if g>1.0 else 'yellow'}">{_fp(g)}</td>
    <td>{_fm(r.get('min_signal_to_peak'))}</td>
    <td class="{'green' if tp_trail>0.75 else 'yellow'}">{_fp(tp_trail)}</td>
    <td class="yellow">{_fp(tp_fixed)}</td>
    <td class="{'green' if delta>0.05 else 'red' if delta<-0.05 else ''}">{_fp(delta)}</td>
    <td class="{'green' if rec else 'red'}">{('TRUE' if rec else 'FALSE') if rec is not None else '—'}</td>
  </tr>"""

    html += "</tbody></table></body></html>"
    return html


@app.route("/analise/invalidos")
def analise_invalidos():
    """
    Investiga por que 52% dos sinais disparam depois do pico.

    Hipótese central: o token bounceou, picou e caiu de volta antes do próximo
    poll GMGN (5s). Detectamos o preço na DESCIDA, não na subida.

    Helius RPC (sub-segundo) resolveria os casos onde o pico ocorreu dentro
    da janela de latência do GMGN (~5-30s). Esta análise quantifica quantos.
    """
    with db._cursor() as cur:

        # Todos os bounces com dados completos
        cur.execute("""
            SELECT
                b.mint,
                b.symbol,
                b.ts_signal,
                b.mc_min                                                    AS mc_bottom,
                b.mc_at_signal,
                b.bounce_pct,
                b.elapsed_sec / 60.0                                        AS elapsed_min_signal,
                b.drop_from_mig,
                m.mc_peak,
                m.mc_migration,
                m.time_to_peak,
                m.time_to_bottom,
                m.recovery,
                -- posição do sinal relativa ao pico (negativo = sinal depois do pico)
                m.time_to_peak - (b.elapsed_sec / 60.0)                    AS min_signal_to_peak,
                -- o sinal capturou quanto % do upside disponível?
                -- (mc_at_signal - mc_bottom) / (mc_peak - mc_bottom)
                CASE WHEN m.mc_peak > b.mc_min
                    THEN (b.mc_at_signal - b.mc_min) / NULLIF(m.mc_peak - b.mc_min, 0)
                END                                                         AS pct_upside_captured,
                -- velocidade do bounce: gain por minuto até o pico
                CASE WHEN m.time_to_peak > 0
                    THEN (m.mc_peak - b.mc_min) / NULLIF(b.mc_min, 0) / m.time_to_peak
                END                                                         AS gain_per_min,
                -- o sinal veio enquanto o preço ainda subia ou já caindo?
                -- se mc_at_signal < mc_peak → compramos abaixo do pico (algum upside)
                -- se mc_at_signal > mc_peak → impossível (mc_peak é o máximo)
                (m.mc_peak - b.mc_at_signal) / NULLIF(m.mc_peak, 0)        AS pct_peak_missed
            FROM bounce_signals b
            JOIN migrations m ON m.mint = b.mint
            WHERE b.mc_min > 0
              AND m.mc_peak IS NOT NULL
              AND b.mc_at_signal IS NOT NULL
        """)
        all_rows = [dict(r) for r in cur.fetchall()]

        # Ticks dos inválidos para medir velocidade real
        cur.execute("""
            SELECT
                b.mint,
                b.symbol,
                b.ts_signal,
                b.mc_min,
                b.mc_at_signal,
                m.time_to_peak,
                b.elapsed_sec / 60.0                          AS elapsed_min_signal,
                -- primeiro tick APÓS o sinal
                MIN(t.tick_ts)                                AS first_tick_after_signal,
                COUNT(t.id) FILTER (WHERE t.tick_ts <= b.ts_signal + INTERVAL '5 minutes') AS ticks_5min,
                -- mc mínimo visto nos 5min após o sinal (continuou caindo?)
                MIN(COALESCE(t.mc_low, t.mc))
                    FILTER (WHERE t.tick_ts >= b.ts_signal
                              AND t.tick_ts <= b.ts_signal + INTERVAL '5 minutes') AS mc_min_after_signal,
                -- mc máximo visto nos 5min após o sinal
                MAX(COALESCE(t.mc_high, t.mc))
                    FILTER (WHERE t.tick_ts >= b.ts_signal
                              AND t.tick_ts <= b.ts_signal + INTERVAL '5 minutes') AS mc_max_after_signal
            FROM bounce_signals b
            JOIN migrations m ON m.mint = b.mint
            JOIN token_ticks t ON t.mint = b.mint
            WHERE b.mc_min > 0
              AND m.time_to_peak IS NOT NULL
              AND m.time_to_peak < b.elapsed_sec / 60.0   -- apenas inválidos
              AND t.tick_ts >= b.ts_signal - INTERVAL '1 minute'
            GROUP BY b.mint, b.symbol, b.ts_signal, b.mc_min, b.mc_at_signal,
                     m.time_to_peak, b.elapsed_sec
        """)
        tick_rows = {r["mint"]: dict(r) for r in cur.fetchall()}

    def _pcts(values, ps=(10, 25, 50, 75, 90)):
        if not values: return {p: None for p in ps}
        s = sorted(values)
        n = len(s)
        out = {}
        for p in ps:
            idx = (p / 100) * (n - 1)
            lo, hi = int(idx), min(int(idx) + 1, n - 1)
            out[p] = s[lo] + (idx - lo) * (s[hi] - s[lo])
        return out

    def _fp(v, mult=100, dec=1):
        return f"{v*mult:.{dec}f}%" if v is not None else "—"
    def _fm(v, dec=1):
        return f"{v:.{dec}f}min" if v is not None else "—"
    def _fs(v):
        return f"{v:.0f}s" if v is not None else "—"

    valid   = [r for r in all_rows if (r["min_signal_to_peak"] or 0) > 0]
    invalid = [r for r in all_rows if (r["min_signal_to_peak"] or 0) <= 0]

    # Para os inválidos: quanto antes do sinal foi o pico?
    # late_by_min = quanto minutos DEPOIS do pico chegou o sinal
    late_vals = [abs(r["min_signal_to_peak"]) for r in invalid
                 if r["min_signal_to_peak"] is not None]
    late_pcts = _pcts(late_vals)

    # Velocidade do bounce (gain/min até o pico) — inválidos vs válidos
    speed_invalid = [r["gain_per_min"] for r in invalid
                     if r["gain_per_min"] is not None and r["gain_per_min"] > 0]
    speed_valid   = [r["gain_per_min"] for r in valid
                     if r["gain_per_min"] is not None and r["gain_per_min"] > 0]

    # Quanto do upside o sinal capturou (posição no pico)
    captured_invalid = [r["pct_upside_captured"] for r in invalid
                        if r["pct_upside_captured"] is not None]
    captured_valid   = [r["pct_upside_captured"] for r in valid
                        if r["pct_upside_captured"] is not None]

    # Estimativa de impacto do Helius:
    # GMGN latência estimada: poll 5s + latência de agregação ~10-20s = ~30s total
    # Se o sinal chegou com menos de 30s de atraso → Helius teria pego
    GMGN_LATENCY_MIN = 30 / 60  # 30 segundos em minutos
    helius_would_catch = [r for r in invalid
                          if r["min_signal_to_peak"] is not None
                          and abs(r["min_signal_to_peak"]) <= GMGN_LATENCY_MIN]
    helius_partial = [r for r in invalid
                      if r["min_signal_to_peak"] is not None
                      and GMGN_LATENCY_MIN < abs(r["min_signal_to_peak"]) <= 2.0]
    helius_no_help = [r for r in invalid
                      if r["min_signal_to_peak"] is not None
                      and abs(r["min_signal_to_peak"]) > 2.0]

    # Quanto % do upside os inválidos capturam (sinal veio em que ponto da curva?)
    # 0% = sinal veio quando preço = bottom (pior)
    # 100% = sinal veio no exato pico
    # Valores > 0 = ainda havia algum upside quando compramos
    neg_upside = sum(1 for r in invalid if (r["pct_upside_captured"] or 0) < 0.05)
    pos_upside = sum(1 for r in invalid if (r["pct_upside_captured"] or 0) >= 0.05)

    html = f"""<!DOCTYPE html><html lang="pt-BR"><head>
<meta charset="utf-8"><title>Análise Inválidos — Sinais Tardios</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: 'Courier New', monospace; background: #0d1117; color: #c9d1d9;
       padding: 20px; font-size: 13px; }}
h1 {{ color: #58a6ff; margin-bottom: 4px; }}
h2 {{ color: #58a6ff; margin: 28px 0 10px; font-size: 15px;
     border-bottom: 1px solid #30363d; padding-bottom: 6px; }}
.sub {{ color: #8b949e; margin-bottom: 20px; font-size: 12px; }}
.nav {{ margin-bottom: 20px; display: flex; gap: 8px; flex-wrap: wrap; }}
.nav a {{ color: #58a6ff; font-size: 12px; text-decoration: none;
         border: 1px solid #30363d; padding: 3px 10px; border-radius: 4px; }}
.nav a:hover {{ background: #161b22; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px,1fr));
        gap: 16px; margin-bottom: 24px; }}
.card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }}
.card-title {{ color: #8b949e; font-size: 11px; text-transform: uppercase;
              letter-spacing:.05em; margin-bottom: 12px; }}
.row {{ display: flex; justify-content: space-between; padding: 5px 0;
       border-bottom: 1px solid #21262d; }}
.row:last-child {{ border-bottom: none; }}
.label {{ color: #8b949e; }}
.green {{ color: #3fb950; }} .yellow {{ color: #d29922; }} .red {{ color: #f85149; }}
.blue  {{ color: #58a6ff; }}
.insight {{ background: #0d2137; border-left: 3px solid #58a6ff; padding: 12px 16px;
           margin: 14px 0; border-radius: 0 6px 6px 0; line-height: 1.7; }}
.warn {{ background: #1e1500; border-left: 3px solid #d29922; padding: 12px 16px;
        margin: 14px 0; border-radius: 0 6px 6px 0; line-height: 1.7; }}
.ok   {{ background: #0d1e0d; border-left: 3px solid #3fb950; padding: 12px 16px;
        margin: 14px 0; border-radius: 0 6px 6px 0; line-height: 1.7; }}
table {{ border-collapse: collapse; width: 100%; }}
th {{ background: #161b22; color: #8b949e; padding: 7px 10px; text-align: left;
     border-bottom: 2px solid #30363d; font-size: 11px; text-transform: uppercase; }}
td {{ padding: 5px 10px; border-bottom: 1px solid #21262d; }}
tr:hover td {{ background: #161b22; }}
.stat-big {{ font-size: 26px; font-weight: bold; margin-top: 4px; }}
.helius-bar {{ display: flex; height: 28px; border-radius: 6px; overflow: hidden;
              margin-top: 12px; }}
.hb-green  {{ background: #238636; display: flex; align-items: center;
              justify-content: center; font-size: 11px; font-weight: bold; color: #fff; }}
.hb-yellow {{ background: #9a6700; display: flex; align-items: center;
              justify-content: center; font-size: 11px; font-weight: bold; color: #fff; }}
.hb-red    {{ background: #8b1a1a; display: flex; align-items: center;
              justify-content: center; font-size: 11px; font-weight: bold; color: #fff; }}
</style></head><body>
<h1>Análise — Sinais Tardios (Inválidos)</h1>
<p class="sub">Por que 52% dos sinais chegam depois do pico? E quanto o Helius RPC resolveria?</p>
<div class="nav">
  <a href="/analise/trailing">← Trailing</a>
  <a href="/analise">Análise</a>
  <a href="/export/bounces">CSV Bounces</a>
</div>

<h2>1. Diagnóstico — O que os dados confirmam</h2>
<div class="insight">
  <b>Hipótese original refutada:</b> Imaginávamos que o pico passava antes do poll GMGN (~30s).
  Os dados mostram P10 de atraso = <b>2.1min</b>, P50 = <b>6.7min</b>. Isso não é latência.<br><br>
  <b>Mecanismo real:</b> Esses tokens tiveram um spike relâmpago nos primeiros <b>0.5–3min</b>
  pós-migração (velocity P50 = 89%/min vs 11.8% dos válidos — 7.5× mais rápido).
  Depois dumpearam por 10–20 minutos. O sinal disparou quando o preço cruzou mc_min × 1.15
  num mini-rally tardio — o fundo estava <b>estagnado há muito tempo</b> quando o sinal chegou.<br><br>
  <b>Helius resolve apenas 3.8% dos inválidos.</b> A solução real é detectar que o fundo é "velho"
  — filtro <b>A.6 (Bottom Freshness)</b> implementado nesta versão.
</div>

<h2>2. Quanto Tempo Depois do Pico o Sinal Chegou?</h2>
<p class="sub" style="margin-top:-6px">
  Para os {len(invalid)} inválidos: distribuição de (elapsed_min_signal − time_to_peak).
  Quanto menor, mais o Helius ajuda.
</p>
<div class="grid">
  <div class="card">
    <div class="card-title">Atraso Sinal vs Pico ({len(late_vals)} tokens)</div>
    {"".join(f'<div class="row"><span class="label">P{p}</span><span class="val {("green" if (late_pcts.get(p) or 99)<0.5 else "yellow" if (late_pcts.get(p) or 99)<2 else "red")}">{_fm(late_pcts.get(p))}</span></div>' for p in [10,25,50,75,90])}
  </div>
  <div class="card">
    <div class="card-title">Velocidade do Bounce — Gain/Minuto</div>
    <div class="row"><span class="label">Inválidos P50</span>
      <span class="val red">{_fp(_pcts(speed_invalid).get(50))}/min</span></div>
    <div class="row"><span class="label">Inválidos P75</span>
      <span class="val red">{_fp(_pcts(speed_invalid).get(75))}/min</span></div>
    <div class="row"><span class="label">Válidos P50</span>
      <span class="val green">{_fp(_pcts(speed_valid).get(50))}/min</span></div>
    <div class="row"><span class="label">Válidos P75</span>
      <span class="val green">{_fp(_pcts(speed_valid).get(75))}/min</span></div>
    <div style="color:#8b949e;font-size:11px;margin-top:8px">
      Inválidos bounceiam mais rápido → pico passa antes do poll
    </div>
  </div>
  <div class="card">
    <div class="card-title">Upside Capturado pelo Sinal</div>
    <div class="row"><span class="label">Inválidos P50</span>
      <span class="val red">{_fp(_pcts(captured_invalid).get(50))}</span></div>
    <div class="row"><span class="label">Inválidos P75</span>
      <span class="val red">{_fp(_pcts(captured_invalid).get(75))}</span></div>
    <div class="row"><span class="label">Válidos P50</span>
      <span class="val green">{_fp(_pcts(captured_valid).get(50))}</span></div>
    <div class="row"><span class="label">Válidos P75</span>
      <span class="val green">{_fp(_pcts(captured_valid).get(75))}</span></div>
    <div style="color:#8b949e;font-size:11px;margin-top:8px">
      0% = sinal veio quando preço = fundo. 100% = sinal veio no pico exato.
    </div>
  </div>
</div>

<h2>3. Impacto Estimado do Helius RPC</h2>
<p class="sub" style="margin-top:-6px">
  Latência GMGN atual: poll 5s + agregação ~25s ≈ <b>30s total</b>.
  Helius RPC: &lt;100ms. Categorização dos {len(invalid)} inválidos:
</p>
<div class="grid">
  <div class="card">
    <div class="card-title">Helius resolve (&lt;30s de atraso)</div>
    <div class="stat-big green">{len(helius_would_catch)}</div>
    <div style="color:#8b949e;font-size:12px;margin-top:6px">
      {len(helius_would_catch)/len(invalid)*100:.1f}% dos inválidos
    </div>
    <div style="color:#8b949e;font-size:12px;margin-top:4px">
      Pico ocorreu dentro da janela de latência do GMGN
    </div>
  </div>
  <div class="card">
    <div class="card-title">Helius ajuda parcial (30s–2min)</div>
    <div class="stat-big yellow">{len(helius_partial)}</div>
    <div style="color:#8b949e;font-size:12px;margin-top:6px">
      {len(helius_partial)/len(invalid)*100:.1f}% dos inválidos
    </div>
    <div style="color:#8b949e;font-size:12px;margin-top:4px">
      Helius pega na subida mas ainda perto do pico
    </div>
  </div>
  <div class="card">
    <div class="card-title">Helius não ajuda (&gt;2min de atraso)</div>
    <div class="stat-big red">{len(helius_no_help)}</div>
    <div style="color:#8b949e;font-size:12px;margin-top:6px">
      {len(helius_no_help)/len(invalid)*100:.1f}% dos inválidos
    </div>
    <div style="color:#8b949e;font-size:12px;margin-top:4px">
      Problema estrutural — não é latência, é sinal tardio mesmo
    </div>
  </div>
</div>
<div class="card" style="margin-bottom:24px">
  <div class="card-title">Composição dos {len(invalid)} Inválidos</div>
  <div class="helius-bar">
    <div class="hb-green" style="width:{len(helius_would_catch)/len(invalid)*100:.0f}%">
      {len(helius_would_catch)/len(invalid)*100:.0f}% Helius resolve
    </div>
    <div class="hb-yellow" style="width:{len(helius_partial)/len(invalid)*100:.0f}%">
      {len(helius_partial)/len(invalid)*100:.0f}% parcial
    </div>
    <div class="hb-red" style="width:{len(helius_no_help)/len(invalid)*100:.0f}%">
      {len(helius_no_help)/len(invalid)*100:.0f}% não ajuda
    </div>
  </div>
</div>

<div class="warn">
  <b>Conclusão:</b> Helius resolve apenas 5 de 132 inválidos (3.8%). O problema é estrutural —
  92% dos inválidos têm fundo estabelecido há 10+ minutos quando o sinal dispara.
  Helius continua válido para <b>redução de custo</b> (Jupiter swap, tips menores),
  mas <b>não resolve a qualidade do sinal</b>. O filtro A.6 é a solução correta.
</div>

<h2>4. Filtro A.6 — Bottom Freshness (IMPLEMENTADO)</h2>
<div class="ok">
  <b>Lógica:</b> O HotMonitor já rastreia <code>state["ts_min"]</code> — o timestamp da última vez
  que mc_min foi atualizado (push_mc + kline). O sinal só dispara se
  <code>(now − ts_min) ≤ BOTTOM_FRESHNESS_SEC</code> (padrão: <b>300s = 5min</b>).<br><br>
  <b>Por que funciona:</b><br>
  &bull; <b>Inválidos:</b> spike em 0.5–2min → ts_min estabelecido em ~3–5min → sinal 13–17min depois → gap enorme → <span class="red">BLOQUEADO</span><br>
  &bull; <b>Válidos:</b> dump progressivo → ts_min atualizado continuamente → sinal 1–3min após o fundo → gap pequeno → <span class="green">PASSA</span><br><br>
  <b>Impacto estimado:</b> {len(helius_no_help)} dos {len(invalid)} inválidos ({len(helius_no_help)/len(invalid)*100:.0f}%) teriam sido bloqueados.
  Universo utilizável: {len(valid)}/{len(all_rows)} ({len(valid)/len(all_rows)*100:.1f}%) → potencialmente
  {len(valid)}/{len(all_rows) - len(helius_no_help)} ({len(valid)/max(len(all_rows)-len(helius_no_help),1)*100:.1f}% dos sinais restantes).<br><br>
  Configurável via env: <code>BOTTOM_FRESHNESS_SEC=300</code>
</div>

<h2>5. Os Inválidos com &gt;2min de Atraso — Análise de Padrão</h2>
<p class="sub" style="margin-top:-6px">
  {len(helius_no_help)} tokens com spike rápido (&lt;3min) e sinal tardio (10–22min).
  A.6 bloqueia todos esses. Tabela para referência histórica.
</p>
<table>
  <thead><tr>
    <th>Token</th><th>Atraso (pico→sinal)</th><th>T Pico</th><th>T Sinal</th>
    <th>Gain Máx</th><th>Upside no Sinal</th><th>Velocidade</th><th>Recovery</th>
  </tr></thead>
  <tbody>
"""
    for r in sorted(helius_no_help, key=lambda x: abs(x.get("min_signal_to_peak") or 0), reverse=True)[:40]:
        late = abs(r.get("min_signal_to_peak") or 0)
        gain = (r.get("mc_peak", 0) - r.get("mc_bottom", 1)) / max(r.get("mc_bottom", 1), 1)
        cap  = r.get("pct_upside_captured") or 0
        spd  = r.get("gain_per_min") or 0
        rec  = r.get("recovery")
        html += f"""
    <tr>
      <td><b>{r.get('symbol') or r['mint'][:8]}</b></td>
      <td class="red">{_fm(late)}</td>
      <td class="muted">{_fm(r.get('time_to_peak'))}</td>
      <td class="muted">{_fm(r.get('elapsed_min_signal'))}</td>
      <td class="{'green' if gain>1 else 'yellow'}">{_fp(gain)}</td>
      <td class="{'green' if cap>0.5 else 'red'}">{_fp(cap)}</td>
      <td>{_fp(spd)}/min</td>
      <td class="{'green' if rec else 'red'}">{('TRUE' if rec else 'FALSE') if rec is not None else '—'}</td>
    </tr>"""

    html += """
  </tbody>
</table>
<div class="insight" style="margin-top:16px">
  <b>Próximo passo após A.6:</b> Validar impacto real nos próximos 30+ sinais.
  Se WR subir de ~25% para ~35%+, A.6 é confirmado. Ajustar BOTTOM_FRESHNESS_SEC se necessário.
  Helius/Jupiter continuam no roadmap mas como <b>redução de custo</b>, não como melhoria de sinal.
</div>
</body></html>"""

    return html


@app.route("/analise/a6-trades")
def analise_a6_trades():
    """
    Retroactive: dos 48 trades reais de $1, quantos A.6 teria bloqueado?
    E esses bloqueados eram vencedores ou perdedores?

    Proxy para ts_min: migrations.time_to_bottom (fundo medido na graduação).
    Gap = elapsed_sec/60 - time_to_bottom. Se gap > 5min → A.6 bloqueia.
    """
    import traceback
    try:
        return _analise_a6_trades_inner()
    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


def _analise_a6_trades_inner():
    FRESHNESS_MIN = 5.0  # 300s / 60

    with db._cursor() as cur:
        cur.execute("""
            SELECT
                t.id,
                t.mint,
                t.symbol,
                t.ts_signal,
                t.status,
                t.pnl_pct_real,
                t.pnl_pct_expected,
                t.entry_expected,
                t.entry_price_usd,
                b.elapsed_sec,
                b.elapsed_sec / 60.0                       AS elapsed_min,
                b.mc_min                                    AS mc_bottom_signal,
                b.mc_at_signal,
                b.bounce_pct,
                b.drop_from_mig,
                m.time_to_bottom,
                m.mc_peak,
                m.mc_migration,
                m.mc_bottom                                 AS mc_bottom_final,
                m.recovery,
                -- Gap estimado: quando o sinal disparou vs quando o fundo foi estabelecido
                -- Proxy: time_to_bottom vem da graduação (60min), não do momento do sinal
                (b.elapsed_sec / 60.0) - m.time_to_bottom  AS gap_min,
                -- Para tokens com ticks: encontrar o tick de menor mc antes do sinal
                (SELECT tick_ts
                 FROM token_ticks tt
                 WHERE tt.mint = t.mint
                   AND tt.tick_ts < t.ts_signal
                   AND tt.tick_ts > t.ts_signal - INTERVAL '60 minutes'
                 ORDER BY COALESCE(tt.mc_low, tt.mc) ASC
                 LIMIT 1)                                   AS ts_bottom_tick,
                (SELECT EXTRACT(EPOCH FROM (t.ts_signal -
                    (SELECT tick_ts
                     FROM token_ticks tt2
                     WHERE tt2.mint = t.mint
                       AND tt2.tick_ts < t.ts_signal
                       AND tt2.tick_ts > t.ts_signal - INTERVAL '60 minutes'
                     ORDER BY COALESCE(tt2.mc_low, tt2.mc) ASC
                     LIMIT 1)))
                )                                           AS gap_sec_ticks
            FROM trades t
            JOIN bounce_signals b ON b.mint = t.mint
            JOIN migrations m ON m.mint = t.mint
            WHERE t.status NOT IN ('pending', 'error')
              AND m.time_to_bottom IS NOT NULL
            ORDER BY t.ts_signal ASC
        """)
        rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        return "<h2>Sem trades com dados suficientes.</h2>", 200

    # Determinar se A.6 bloquearia cada trade
    # Preferir gap_sec_ticks (dado real de ticks) se disponível, fallback para gap_min proxy
    for r in rows:
        tick_gap = r.get("gap_sec_ticks")
        if tick_gap is not None and tick_gap > 0:
            r["gap_sec_used"] = tick_gap
            r["gap_source"] = "ticks"
        else:
            proxy = r.get("gap_min") or 0
            r["gap_sec_used"] = proxy * 60
            r["gap_source"] = "proxy"
        r["a6_blocked"] = r["gap_sec_used"] > (FRESHNESS_MIN * 60)

    # Classificar outcome real de cada trade
    # pnl_pct no DB tem bug de referência (A.4 causou entry_expected 25% abaixo do real)
    # Usamos status + mc_peak vs mc_bottom para estimar se o trade tinha potencial real
    def classify_outcome(r):
        status = r.get("status") or ""
        if "tp" in status:
            return "tp_hit"
        if "sl" in status or status == "done":
            # Verificar pelo mc_peak: se mc_peak > entry × 1.75, havia TP disponível
            entry = r.get("mc_at_signal") or r.get("entry_expected") or 0
            peak  = r.get("mc_peak") or 0
            if entry > 0 and peak > entry * 1.75:
                return "had_tp_potential"
            return "sl_loss"
        if "expired" in status:
            return "expired"
        return status

    for r in rows:
        r["outcome"] = classify_outcome(r)

    blocked = [r for r in rows if r["a6_blocked"]]
    passes  = [r for r in rows if not r["a6_blocked"]]

    blocked_tp   = [r for r in blocked if r["outcome"] in ("tp_hit", "had_tp_potential")]
    blocked_loss = [r for r in blocked if r["outcome"] not in ("tp_hit", "had_tp_potential")]
    passes_tp    = [r for r in passes  if r["outcome"] in ("tp_hit", "had_tp_potential")]
    passes_loss  = [r for r in passes  if r["outcome"] not in ("tp_hit", "had_tp_potential")]

    n = len(rows)

    def _fp(v, mult=100, dec=1):
        return f"{v*mult:.{dec}f}%" if v is not None else "—"
    def _fm(v, dec=1):
        return f"{v:.{dec}f}min" if v is not None else "—"
    def _pct(a, b):
        return f"{a/b*100:.0f}%" if b else "—"

    html = f"""<!DOCTYPE html><html lang="pt-BR"><head>
<meta charset="utf-8"><title>A.6 — Impacto nos 48 Trades Reais</title>
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:'Courier New',monospace; background:#0d1117; color:#c9d1d9;
       padding:20px; font-size:13px; }}
h1 {{ color:#58a6ff; margin-bottom:4px; }}
h2 {{ color:#58a6ff; margin:28px 0 10px; font-size:15px;
     border-bottom:1px solid #30363d; padding-bottom:6px; }}
.sub {{ color:#8b949e; margin-bottom:20px; font-size:12px; }}
.nav {{ margin-bottom:20px; display:flex; gap:8px; flex-wrap:wrap; }}
.nav a {{ color:#58a6ff; font-size:12px; text-decoration:none;
         border:1px solid #30363d; padding:3px 10px; border-radius:4px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
        gap:16px; margin-bottom:24px; }}
.card {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px; }}
.card-title {{ color:#8b949e; font-size:11px; text-transform:uppercase;
              letter-spacing:.05em; margin-bottom:10px; }}
.stat-big {{ font-size:28px; font-weight:bold; margin-top:4px; }}
.row {{ display:flex; justify-content:space-between; padding:5px 0;
       border-bottom:1px solid #21262d; }}
.row:last-child {{ border-bottom:none; }}
.green {{ color:#3fb950; }} .yellow {{ color:#d29922; }}
.red {{ color:#f85149; }} .blue {{ color:#58a6ff; }} .muted {{ color:#8b949e; }}
.ok   {{ background:#0d1e0d; border-left:3px solid #3fb950; padding:12px 16px;
        margin:14px 0; border-radius:0 6px 6px 0; line-height:1.7; }}
.warn {{ background:#1e1500; border-left:3px solid #d29922; padding:12px 16px;
        margin:14px 0; border-radius:0 6px 6px 0; line-height:1.7; }}
.insight {{ background:#0d2137; border-left:3px solid #58a6ff; padding:12px 16px;
           margin:14px 0; border-radius:0 6px 6px 0; line-height:1.7; }}
table {{ border-collapse:collapse; width:100%; }}
th {{ background:#161b22; color:#8b949e; padding:7px 10px; text-align:left;
     border-bottom:2px solid #30363d; font-size:11px; text-transform:uppercase; }}
td {{ padding:5px 10px; border-bottom:1px solid #21262d; font-size:12px; }}
tr:hover td {{ background:#161b22; }}
.badge {{ display:inline-block; padding:2px 8px; border-radius:10px; font-size:10px; font-weight:bold; }}
.badge-red {{ background:#3d0000; color:#f85149; }}
.badge-green {{ background:#0d2e0d; color:#3fb950; }}
.badge-yellow {{ background:#2e2000; color:#d29922; }}
</style></head><body>
<h1>A.6 — Retroativo: impacto nos {n} trades reais</h1>
<p class="sub">Pergunta: dos trades que fizemos, quantos A.6 teria bloqueado? E esses eram vencedores ou perdedores?</p>
<div class="nav">
  <a href="/analise/invalidos">← Inválidos</a>
  <a href="/analise/trailing">Trailing</a>
  <a href="/trades">Trades</a>
</div>

<div class="insight">
  <b>Metodologia:</b> Gap = tempo entre o fundo (última atualização de mc_min) e o sinal.
  Fonte primária: ticks reais (tick com menor mc antes do sinal). Fallback: proxy via migrations.time_to_bottom.<br>
  A.6 bloqueia se gap &gt; {FRESHNESS_MIN:.0f}min. Outcome: tp_hit = status tp, had_tp_potential = mc_peak &gt; entry × 1.75x.
</div>

<h2>Resumo</h2>
<div class="grid">
  <div class="card">
    <div class="card-title">Total de trades analisados</div>
    <div class="stat-big blue">{n}</div>
  </div>
  <div class="card">
    <div class="card-title">A.6 bloquearia</div>
    <div class="stat-big red">{len(blocked)}</div>
    <div style="color:#8b949e;font-size:12px;margin-top:4px">{_pct(len(blocked),n)} dos trades</div>
  </div>
  <div class="card">
    <div class="card-title">Passariam pelo A.6</div>
    <div class="stat-big green">{len(passes)}</div>
    <div style="color:#8b949e;font-size:12px;margin-top:4px">{_pct(len(passes),n)} dos trades</div>
  </div>
</div>

<h2>Trades Bloqueados pelo A.6 ({len(blocked)}) — eram bons ou ruins?</h2>
<div class="grid">
  <div class="card">
    <div class="card-title">Com TP / Potencial</div>
    <div class="stat-big {'red' if len(blocked_tp)/max(len(blocked),1) > 0.3 else 'green'}">{len(blocked_tp)}</div>
    <div style="color:#8b949e;font-size:12px;margin-top:4px">{_pct(len(blocked_tp),len(blocked))} dos bloqueados</div>
    <div style="color:#f85149;font-size:12px">trades "bons" que perderíamos</div>
  </div>
  <div class="card">
    <div class="card-title">Perdas (SL / sem potencial)</div>
    <div class="stat-big green">{len(blocked_loss)}</div>
    <div style="color:#8b949e;font-size:12px;margin-top:4px">{_pct(len(blocked_loss),len(blocked))} dos bloqueados</div>
    <div style="color:#3fb950;font-size:12px">losses evitadas ✓</div>
  </div>
  <div class="card">
    <div class="card-title">WR do grupo bloqueado</div>
    <div class="stat-big {'green' if len(blocked_tp)/max(len(blocked),1) > 0.25 else 'red'}">{_pct(len(blocked_tp),len(blocked))}</div>
    <div style="color:#8b949e;font-size:12px;margin-top:4px">WR dos que A.6 teria bloqueado</div>
  </div>
</div>

<h2>Trades que Passariam ({len(passes)}) — qualidade</h2>
<div class="grid">
  <div class="card">
    <div class="card-title">Com TP / Potencial</div>
    <div class="stat-big green">{len(passes_tp)}</div>
    <div style="color:#8b949e;font-size:12px;margin-top:4px">{_pct(len(passes_tp),len(passes))} dos que passam</div>
  </div>
  <div class="card">
    <div class="card-title">Perdas</div>
    <div class="stat-big red">{len(passes_loss)}</div>
    <div style="color:#8b949e;font-size:12px;margin-top:4px">{_pct(len(passes_loss),len(passes))} dos que passam</div>
  </div>
  <div class="card">
    <div class="card-title">WR do grupo válido</div>
    <div class="stat-big {'green' if len(passes_tp)/max(len(passes),1) > 0.25 else 'yellow'}">{_pct(len(passes_tp),len(passes))}</div>
    <div style="color:#8b949e;font-size:12px;margin-top:4px">WR dos que passariam pelo A.6</div>
  </div>
</div>

<h2>Detalhe — todos os {n} trades</h2>
<table>
  <thead><tr>
    <th>Token</th><th>Gap (fundo→sinal)</th><th>Fonte gap</th>
    <th>T Fundo</th><th>T Sinal</th><th>A.6</th>
    <th>Outcome</th><th>Bounce%</th><th>Drop%</th>
  </tr></thead>
  <tbody>
"""
    for r in rows:
        gap_min = r["gap_sec_used"] / 60
        src = "tick" if r["gap_source"] == "ticks" else "proxy"
        outcome = r["outcome"]
        blocked_flag = r["a6_blocked"]
        outcome_color = "green" if outcome in ("tp_hit","had_tp_potential") else "red" if "sl" in outcome or "loss" in outcome else "yellow"
        a6_color = "red" if blocked_flag else "green"
        a6_label = "BLOQUEIA" if blocked_flag else "PASSA"
        html += f"""
    <tr>
      <td><b>{r.get('symbol') or r['mint'][:8]}</b></td>
      <td class="{'red' if blocked_flag else 'green'}">{gap_min:.1f}min</td>
      <td class="muted">{src}</td>
      <td class="muted">{_fm(r.get('time_to_bottom'))}</td>
      <td class="muted">{_fm(r.get('elapsed_min'))}</td>
      <td><span class="badge badge-{'red' if blocked_flag else 'green'}">{a6_label}</span></td>
      <td class="{outcome_color}">{outcome}</td>
      <td>{_fp(r.get('bounce_pct'))}</td>
      <td>{_fp(r.get('drop_from_mig'))}</td>
    </tr>"""

    html += f"""
  </tbody>
</table>

<div class="{'ok' if len(blocked_tp)/max(len(blocked),1) < 0.25 else 'warn'}" style="margin-top:20px">
  <b>Veredicto A.6 nos trades reais:</b><br>
  Bloquearia {len(blocked)} de {n} trades ({_pct(len(blocked),n)}).
  Desses, {len(blocked_tp)} tinham potencial de TP ({_pct(len(blocked_tp),len(blocked))}) e
  {len(blocked_loss)} eram perdas ({_pct(len(blocked_loss),len(blocked))}).<br><br>
  {"✓ A.6 é eficaz — bloqueou principalmente perdas." if len(blocked_loss) > len(blocked_tp) else "⚠ Cuidado — A.6 bloquearia muitos trades bons. Considerar aumentar BOTTOM_FRESHNESS_SEC."}
</div>
</body></html>"""

    return html


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
    h += "<div style='padding:6px 16px;display:flex;gap:10px;flex-wrap:wrap;border-bottom:1px solid #21262d;margin-bottom:4px'>"
    h += "<a href='/analise' style='color:#58a6ff;font-size:11px;text-decoration:none'>Analise</a>"
    h += "<a href='/analise/deep-bounce' style='color:#58a6ff;font-size:11px;text-decoration:none'>Deep Bounce ↗</a>"
    h += "<a href='/analise/backtest' style='color:#58a6ff;font-size:11px;text-decoration:none'>Backtest</a>"
    h += "<a href='/analise/h10' style='color:#58a6ff;font-size:11px;text-decoration:none'>H10</a>"
    h += "<a href='/scanner' style='color:#58a6ff;font-size:11px;text-decoration:none'>Scanner</a>"
    h += "</div>"

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
  .b-pending          { background: #3d3b00; color: #d29922; }
  .b-open             { background: #003d00; color: #3fb950; }
  .b-strategy_pending { background: #003d3d; color: #39d0d0; }
  .b-tp_hit           { background: #003d00; color: #3fb950; }
  .b-trailing_active  { background: #004d20; color: #56d364; }
  .b-sl_hit           { background: #3d0000; color: #f85149; }
  .b-sl               { background: #3d0000; color: #f85149; }
  .b-error            { background: #3d0000; color: #f85149; }
  .b-timeout          { background: #3d3b00; color: #d29922; }
  .b-expired          { background: #2d2d2d; color: #8b949e; }
  .est { color: #8b949e; font-size: 10px; }
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
  <th>MC bottom</th><th>Entry REAL</th><th>+% above bot</th>
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
  <td>
    {% if t.entry_mc_real %}
      {{ fmt_mc(t.entry_mc_real) }}
    {% else %}
      <span class="est">{{ fmt_mc(t.entry_expected) }}*</span>
    {% endif %}
  </td>
  <td>
    {% if t.entry_divergence is not none %}
      <span class="{% if t.entry_divergence > 0.50 %}red{% elif t.entry_divergence > 0.30 %}yellow{% else %}green{% endif %}">
        +{{ "%.0f%%"|format(t.entry_divergence * 100) }}
      </span>
    {% else %}—{% endif %}
  </td>
  <td>{{ ("%+.2f%%"|format(t.slippage_pct*100)) if t.slippage_pct is not none else '—' }}</td>
  <td>{{ ("%.5f"|format(t.fee_sol)) if t.fee_sol else '—' }}</td>
  <td>{{ ("%.1fs"|format(t.latency_sec)) if t.latency_sec else '—' }}</td>
  <td>{{ ("%.0f"|format(t.tokens_received)) if t.tokens_received else '—' }}</td>
  <td>
    {% if t.pnl_pct_real is not none %}
      <span class="{% if t.pnl_pct_real > 0 %}green{% else %}red{% endif %}">
        {{ "%+.1f%%"|format(t.pnl_pct_real*100) }}
      </span>
      {% if t.status == 'trailing_active' %}
        <span class="est">⚡trailing</span>
      {% endif %}
    {% elif t.status in ('open', 'pending') %}
      <span class="est">GMGN</span>
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

    # Formata timestamps em BRT + calcula entry_mc_real a partir do fill real
    for t in trades_raw:
        ts = t.get("ts_signal")
        t["ts_signal_brt"] = (ts + BRT).strftime("%m/%d %H:%M:%S") if ts else None

        # MC real de execução: preço_por_token × supply (1B Pump.fun)
        usd = t.get("entry_price_usd")
        tkn = t.get("tokens_received")
        if usd and tkn and tkn > 0:
            t["entry_mc_real"] = (usd / tkn) * 1_000_000_000
        else:
            t["entry_mc_real"] = None

        # Divergência: quanto % acima do mc_bottom detectado entramos de fato
        bot = t.get("mc_bottom")
        if t["entry_mc_real"] and bot and bot > 0:
            t["entry_divergence"] = (t["entry_mc_real"] - bot) / bot
        else:
            t["entry_divergence"] = None

    # Métricas — statuses reais do TradeMonitor: "sl", "trailing_active", "expired"
    TP_STATUSES = ("tp_hit", "trailing_active")
    SL_STATUSES = ("sl_hit", "sl")
    OPEN_STATUSES = ("open", "pending", "strategy_pending")
    closed   = [t for t in trades_raw if t["status"] in TP_STATUSES + SL_STATUSES]
    tp_count = sum(1 for t in trades_raw if t["status"] in TP_STATUSES)
    sl_count = sum(1 for t in trades_raw if t["status"] in SL_STATUSES)
    err_count= sum(1 for t in trades_raw if t["status"] == "error")
    open_cnt = sum(1 for t in trades_raw if t["status"] in OPEN_STATUSES)

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
# /analise/deep-bounce — Análise quant profunda dos bounce signals
# ==============================================================================

@app.route("/analise/deep-bounce")
def analise_deep_bounce():
    """
    Análise quant de segunda camada: o que discrimina bounces bons de ruins
    ALÉM dos campos estáticos de qualidade já analisados em bounce-quality.

    Hipóteses investigadas:
      A. Bounce velocity: rapidez do bounce prediz recuperação sustentada?
      B. Entry delay: esperar 2/5/10min melhora o EV?
      C. Dump shape: slope pré-fundo (capitulação vs distribuição)
      D. Bounce persistence: 3 ticks consecutivos acima do entry nível
      E. W-bottom / double-dip: segunda testada do fundo melhora WR?
      F. Dump depth × time_to_bottom grid: zona de maior probabilidade
      G. Bounce size at signal: bounce% grande = momentum ou dead-cat?
      H. TP hit timing: em quantos minutos o TP é atingido (quem apertou o trigger)
      I. MC level at signal vs migration: onde o sinal dispara relativo à migração
    """
    TP_PCT = 0.75
    SL_PCT = 0.15
    MIN_SAMPLE = 5  # abaixo disso, mostrar aviso em vez de stats

    _CSS = """<style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Courier New',monospace;background:#0d1117;color:#c9d1d9;font-size:12px}
    h1{padding:14px 16px;color:#58a6ff;font-size:18px;border-bottom:1px solid #21262d}
    h2{font-size:13px;color:#58a6ff;padding:10px 16px 4px;margin-top:16px;border-top:1px solid #21262d}
    h3{font-size:11px;color:#8b949e;padding:4px 16px 2px}
    .grid{display:flex;gap:10px;padding:8px 16px;flex-wrap:wrap}
    .card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 14px;min-width:110px}
    .card .lbl{color:#8b949e;font-size:10px;margin-bottom:2px}
    .card .val{font-size:15px;font-weight:bold}
    .green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}.muted{color:#8b949e}
    table{width:100%;border-collapse:collapse}
    th{background:#161b22;color:#8b949e;padding:5px 8px;text-align:left;
       border-bottom:1px solid #30363d;font-size:10px;white-space:nowrap}
    td{padding:4px 8px;border-bottom:1px solid #21262d;font-size:11px}
    tr:hover td{background:#161b22}
    .sec{padding:4px 16px 12px}
    .warn{background:#3d3b00;color:#d29922;padding:6px 14px;margin:6px 16px;border-radius:4px;font-size:11px}
    .insight{background:#0d2137;color:#79c0ff;padding:6px 14px;margin:4px 16px;border-radius:4px;font-size:11px;border-left:3px solid #58a6ff}
    .nav{padding:8px 16px;display:flex;gap:10px;flex-wrap:wrap;border-bottom:1px solid #21262d}
    .nav a{color:#58a6ff;font-size:11px;text-decoration:none}
    .nav a:hover{text-decoration:underline}
    .zone-hot{background:#1a3a1a;color:#3fb950}
    .zone-cold{background:#3a1a1a;color:#f85149}
    .zone-mid{background:#2a2a1a;color:#d29922}
    </style>"""

    h = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Deep Bounce Analysis</title>{_CSS}</head><body>"
    h += "<h1>Deep Bounce Analysis — camada quant</h1>"
    h += "<div class='nav'>"
    h += "<a href='/analise'>Analise</a>"
    h += "<a href='/analise/bounce-quality'>Bounce Quality</a>"
    h += "<a href='/analise/backtest'>Backtest</a>"
    h += "<a href='/analise/h10'>H10 (TTB)</a>"
    h += "<a href='/analise/dump-depth'>Dump Depth</a>"
    h += "<a href='/scanner'>Scanner</a>"
    h += "</div>"

    # ------------------------------------------------------------------ #
    # Carrega bounce_signals + migrations + ticks de uma vez
    # ------------------------------------------------------------------ #
    with db._cursor() as cur:
        cur.execute("""
            SELECT
                b.mint,
                b.symbol,
                b.elapsed_sec          AS bounce_elapsed,
                b.mc_min               AS bottom,
                b.mc_at_signal         AS entry,
                b.bounce_pct,
                b.drop_from_mig,
                b.ts_signal,
                m.mc_migration,
                m.time_to_bottom,
                m.pct_drop,
                m.mc_1h,
                m.recovery,
                m.liq_migration,
                m.exchange,
                m.ts_migration,
                m.smart_wallets,
                m.bot_degen_rate,
                m.top10_holder_rate,
                m.rug_ratio,
                m.rat_trader_rate,
                m.creator_token_status
            FROM bounce_signals b
            JOIN migrations m ON m.mint = b.mint
            WHERE b.mc_at_signal IS NOT NULL
              AND b.mc_at_signal > 0
              AND b.mc_min > 0
            ORDER BY b.ts_signal DESC
        """)
        bounces = [dict(r) for r in cur.fetchall()]

        if not bounces:
            return h + "<div class='warn'>Nenhum bounce_signal disponível ainda.</div></body></html>"

        mints = [b["mint"] for b in bounces]

        # Todos os ticks de todos os tokens com bounce
        cur.execute("""
            SELECT mint, elapsed_sec, mc, mc_open, mc_high, mc_low, source, tick_ts
            FROM token_ticks
            WHERE mint = ANY(%s)
            ORDER BY mint, elapsed_sec
        """, (mints,))
        all_ticks_raw = cur.fetchall()

    # Organiza ticks por mint
    ticks_by_mint = {}
    for t in all_ticks_raw:
        ticks_by_mint.setdefault(t["mint"], []).append(dict(t))

    # ------------------------------------------------------------------ #
    # Função: determina outcome para uma entrada num dado elapsed
    # ------------------------------------------------------------------ #
    def _resolve_outcome(ticks_post, entry_mc, tp_pct=TP_PCT, sl_pct=SL_PCT):
        """Caminha ticks_post e retorna (outcome, pnl, exit_elapsed)."""
        if not entry_mc or entry_mc <= 0:
            return "no_entry", None, None
        tp_tgt = entry_mc * (1 + tp_pct)
        sl_flr = entry_mc * (1 - sl_pct)
        for t in ticks_post:
            high = t.get("mc_high") or t.get("mc")
            low  = t.get("mc_low")  or t.get("mc")
            if low is not None and low <= sl_flr:
                return "SL", -sl_pct, t["elapsed_sec"]
            if high is not None and high >= tp_tgt:
                return "TP", tp_pct, t["elapsed_sec"]
        # time exit
        if ticks_post:
            last_mc = ticks_post[-1].get("mc")
            if last_mc and last_mc > 0:
                pnl = last_mc / entry_mc - 1
                return "exit", pnl, ticks_post[-1]["elapsed_sec"]
        return "no_ticks", None, None

    # ------------------------------------------------------------------ #
    # Computa outcome base + features derivadas para cada bounce
    # ------------------------------------------------------------------ #
    records = []
    for b in bounces:
        mint    = b["mint"]
        entry   = b["entry"]
        bottom  = b["bottom"]
        elapsed = b["bounce_elapsed"]
        ttb_sec = (b["time_to_bottom"] or 0) * 60  # minutos → segundos

        all_ticks = ticks_by_mint.get(mint, [])
        ticks_post = [t for t in all_ticks if t["elapsed_sec"] > elapsed]
        ticks_pre  = [t for t in all_ticks if t["elapsed_sec"] <= elapsed]
        ticks_bottom_window = [t for t in ticks_pre
                               if t["elapsed_sec"] >= max(0, ttb_sec - 120)]

        outcome, pnl, exit_elapsed = _resolve_outcome(ticks_post, entry)

        # ---- A. Bounce velocity ----
        # velocity = bounce_pct / seconds_from_bottom_to_signal
        time_bottom_to_signal = elapsed - ttb_sec
        velocity = None
        if time_bottom_to_signal and time_bottom_to_signal > 5:
            velocity = (b["bounce_pct"] or 0) / time_bottom_to_signal  # pct/s

        # ---- C. Dump shape — slope pré-fundo (últimos 2min antes do bottom) ----
        pre_slope = None
        if len(ticks_bottom_window) >= 3:
            mc_vals    = [t["mc"] for t in ticks_bottom_window if t.get("mc")]
            elap_vals  = [t["elapsed_sec"] for t in ticks_bottom_window if t.get("mc")]
            if len(mc_vals) >= 3:
                # Regressão linear simples: slope = cov(x,y)/var(x)
                n     = len(mc_vals)
                mx    = sum(elap_vals) / n
                my    = sum(mc_vals) / n
                cov   = sum((elap_vals[i] - mx) * (mc_vals[i] - my) for i in range(n))
                var_x = sum((elap_vals[i] - mx) ** 2 for i in range(n))
                if var_x > 0:
                    pre_slope = cov / var_x  # USD/sec — negativo = queda
                    # Normaliza pelo mc para obter %mc/sec
                    if my > 0:
                        pre_slope = pre_slope / my  # %/sec

        # ---- D. Bounce persistence — 3 primeiros ticks após sinal ----
        first3 = ticks_post[:3]
        persistent = None
        if len(first3) >= 3:
            persistent = all(
                (t.get("mc_low") or t.get("mc") or 0) >= entry * 0.98
                for t in first3
            )

        # ---- E. W-bottom (double-dip) — dip de volta a 115% do bottom antes de subir ----
        w_bottom_dip = None
        if ticks_post:
            dip_threshold = bottom * 1.15  # "volta perto do fundo"
            found_dip = False
            dipped_back = False
            for t in ticks_post:
                low = t.get("mc_low") or t.get("mc") or 0
                if low <= dip_threshold and not found_dip:
                    found_dip = True
                elif found_dip:
                    high = t.get("mc_high") or t.get("mc") or 0
                    if high >= entry * 1.05:
                        dipped_back = True
                        break
            w_bottom_dip = found_dip  # True = houve dip de volta ao nível do fundo

        # ---- I. MC at signal vs migration ratio ----
        mc_ratio_signal_mig = None
        if b.get("mc_migration") and b["mc_migration"] > 0 and entry:
            mc_ratio_signal_mig = entry / b["mc_migration"]

        records.append({
            **b,
            "ticks_post": ticks_post,
            "ticks_pre":  ticks_pre,
            "outcome":    outcome,
            "pnl":        pnl,
            "exit_elapsed": exit_elapsed,
            "n_ticks_post": len(ticks_post),
            "n_ticks_pre":  len(ticks_pre),
            "velocity":   velocity,
            "pre_slope":  pre_slope,
            "persistent": persistent,
            "w_bottom_dip": w_bottom_dip,
            "mc_ratio_signal_mig": mc_ratio_signal_mig,
            "time_bottom_to_signal": time_bottom_to_signal,
        })

    # ---- Subsets de trabalho ----
    with_outcome = [r for r in records if r["pnl"] is not None]
    wins  = [r for r in with_outcome if r["outcome"] == "TP"]
    losses= [r for r in with_outcome if r["outcome"] == "SL"]

    n_total   = len(records)
    n_data    = len(with_outcome)
    n_no_data = n_total - n_data
    wr_base   = len(wins) / n_data * 100 if n_data else 0
    ev_base   = sum(r["pnl"] for r in with_outcome) / n_data if n_data else 0

    # ---- Helpers ----
    def _avg(lst, key):
        vals = [r[key] for r in lst if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    def _f(v, fmt=".1f"):
        return f"{v:{fmt}}" if v is not None else "—"

    def _wr(lst):
        """Win rate de uma sublista que já tem outcome."""
        with_o = [r for r in lst if r["pnl"] is not None]
        if not with_o:
            return None, 0
        tp = [r for r in with_o if r["outcome"] == "TP"]
        return len(tp) / len(with_o) * 100, len(with_o)

    def _ev(lst):
        with_o = [r for r in lst if r["pnl"] is not None]
        if not with_o:
            return None
        return sum(r["pnl"] for r in with_o) / len(with_o)

    def _row_stat(label, subset_a, label_a, subset_b, label_b, warn_n=MIN_SAMPLE):
        """Gera linha de tabela comparando WR/EV de dois subsets."""
        wr_a, na = _wr(subset_a)
        wr_b, nb = _wr(subset_b)
        ev_a = _ev(subset_a)
        ev_b = _ev(subset_b)
        delta_wr = (wr_a - wr_b) if (wr_a is not None and wr_b is not None) else None
        small = na < warn_n or nb < warn_n

        def _fmt_wr(wr, n):
            if wr is None: return "—"
            cls = "green" if wr >= 30 else ("yellow" if wr >= 20 else "red")
            warn = " ⚠" if n < warn_n else ""
            return f"<span class='{cls}'>{wr:.1f}%</span> (n={n}{warn})"

        def _fmt_ev(ev):
            if ev is None: return "—"
            cls = "green" if ev > 0 else "red"
            return f"<span class='{cls}'>{ev:+.0%}</span>"

        delta_cls = "green" if (delta_wr or 0) > 5 else ("red" if (delta_wr or 0) < -5 else "yellow")
        delta_str = f"<span class='{delta_cls}'>{delta_wr:+.1f}pp</span>" if delta_wr is not None else "—"

        row  = f"<tr><td><b>{label_a}</b></td><td>{_fmt_wr(wr_a,na)}</td><td>{_fmt_ev(ev_a)}</td></tr>"
        row += f"<tr><td><b>{label_b}</b></td><td>{_fmt_wr(wr_b,nb)}</td><td>{_fmt_ev(ev_b)}</td></tr>"
        row += f"<tr style='border-top:1px solid #30363d'><td style='color:#8b949e'>Delta</td><td colspan='2'>{delta_str}</td></tr>"
        return row, delta_wr

    # ================================================================== #
    # HEADER — cards de resumo
    # ================================================================== #
    h += "<div class='grid'>"
    h += f"<div class='card'><div class='lbl'>Total bounces</div><div class='val'>{n_total}</div></div>"
    h += f"<div class='card'><div class='lbl'>Com tick data</div><div class='val'>{n_data}</div></div>"
    h += f"<div class='card'><div class='lbl'>Sem ticks</div><div class='val yellow'>{n_no_data}</div></div>"
    wrcls = "green" if wr_base >= 25 else ("yellow" if wr_base >= 15 else "red")
    h += f"<div class='card'><div class='lbl'>Win rate base</div><div class='val {wrcls}'>{wr_base:.1f}%</div></div>"
    evcls = "green" if ev_base > 0 else "red"
    h += f"<div class='card'><div class='lbl'>EV base (TP75/SL15)</div><div class='val {evcls}'>{ev_base:+.0%}</div></div>"
    h += f"<div class='card'><div class='lbl'>TP hits</div><div class='val green'>{len(wins)}</div></div>"
    h += f"<div class='card'><div class='lbl'>SL hits</div><div class='val red'>{len(losses)}</div></div>"
    h += "</div>"

    if n_no_data > n_data:
        h += f"<div class='warn'>⚠ {n_no_data}/{n_total} bounces sem ticks — stats abaixo podem ter bias de seleção. Kline OHLC acumulando.</div>"

    # ================================================================== #
    # A. BOUNCE VELOCITY
    # ================================================================== #
    h += "<h2>A. Bounce Velocity — quão rápido o preço subiu do fundo ao sinal?</h2>"
    h += "<h3>Hipótese: bounce rápido = capitulação terminou, compradores agressivos. bounce lento = distribuição continuada.</h3>"

    vel_data = [r for r in with_outcome if r.get("velocity") is not None]
    if len(vel_data) < MIN_SAMPLE:
        h += f"<div class='warn'>Amostra insuficiente (n={len(vel_data)}) — aguardar mais bounces com time_to_bottom preenchido.</div>"
    else:
        # Mediana de velocity para split
        sorted_vel = sorted(vel_data, key=lambda r: r["velocity"])
        med_vel = sorted_vel[len(sorted_vel) // 2]["velocity"]
        fast_bounce = [r for r in vel_data if r["velocity"] >= med_vel]
        slow_bounce = [r for r in vel_data if r["velocity"] <  med_vel]

        h += "<div class='grid'>"
        h += f"<div class='card'><div class='lbl'>Velocidade mediana</div><div class='val'>{med_vel*100:.3f}%/s</div></div>"
        h += f"<div class='card'><div class='lbl'>Fast (acima med)</div><div class='val'>{len(fast_bounce)}</div></div>"
        h += f"<div class='card'><div class='lbl'>Slow (abaixo med)</div><div class='val'>{len(slow_bounce)}</div></div>"
        h += "</div>"

        h += "<div class='sec'><table><thead><tr><th>Grupo</th><th>Win Rate</th><th>EV</th></tr></thead><tbody>"
        row_str, delta = _row_stat("velocity", fast_bounce, f"Fast (vel ≥ {med_vel*100:.3f}%/s)", slow_bounce, f"Slow (vel < {med_vel*100:.3f}%/s)")
        h += row_str
        h += "</tbody></table></div>"

        # Tercis de velocity
        n3 = len(vel_data) // 3
        if n3 >= MIN_SAMPLE:
            t1 = sorted_vel[:n3]
            t2 = sorted_vel[n3:2*n3]
            t3 = sorted_vel[2*n3:]
            h += "<h3>Tercis de velocidade</h3>"
            h += "<div class='sec'><table><thead><tr><th>Tercil</th><th>Vel range (%/s)</th><th>Win Rate</th><th>EV</th><th>n</th></tr></thead><tbody>"
            for label, subset in [("Baixo (lento)", t1), ("Médio", t2), ("Alto (rápido)", t3)]:
                wr, n = _wr(subset)
                ev = _ev(subset)
                vel_min = min(r["velocity"] for r in subset) * 100
                vel_max = max(r["velocity"] for r in subset) * 100
                wr_cls = "green" if (wr or 0) >= 30 else ("yellow" if (wr or 0) >= 20 else "red")
                ev_cls = "green" if (ev or 0) > 0 else "red"
                h += f"<tr><td>{label}</td><td>{vel_min:.3f}–{vel_max:.3f}</td>"
                h += f"<td class='{wr_cls}'>{wr:.1f}%</td>" if wr is not None else "<td>—</td>"
                h += f"<td class='{ev_cls}'>{ev:+.0%}</td>" if ev is not None else "<td>—</td>"
                h += f"<td>{n}</td></tr>"
            h += "</tbody></table></div>"

        insight_delta = delta or 0
        if abs(insight_delta) >= 10:
            direction = "RÁPIDO" if insight_delta > 0 else "LENTO"
            h += f"<div class='insight'>Sinal: bounces {direction} têm win rate {abs(insight_delta):.1f}pp {'maior' if insight_delta > 0 else 'menor'}. Filtro potencial.</div>"
        else:
            h += "<div class='insight'>Velocity não discrimina WR de forma clara nessa amostra. Acumular mais dados.</div>"

    # ================================================================== #
    # B. ENTRY DELAY SIMULATION
    # ================================================================== #
    h += "<h2>B. Entry Delay — esperar N minutos após o sinal melhora o EV?</h2>"
    h += "<h3>Hipótese: entrar imediatamente pega dead-cat bounces. Esperar 5min filtra ruído sem perder muito upside.</h3>"

    DELAYS = [0, 60, 120, 300, 600]  # segundos
    delay_stats = []

    for delay_sec in DELAYS:
        sub_results = []
        for r in records:
            all_t = ticks_by_mint.get(r["mint"], [])
            # Ticks após sinal + delay
            ticks_delayed = [t for t in all_t if t["elapsed_sec"] > r["bounce_elapsed"] + delay_sec]
            if not ticks_delayed:
                continue
            # Entry = primeiro tick disponível após delay
            first_tick = ticks_delayed[0]
            entry_mc = first_tick.get("mc_open") or first_tick.get("mc")
            if not entry_mc or entry_mc <= 0:
                continue
            oc, pnl, _ = _resolve_outcome(ticks_delayed[1:], entry_mc)
            if pnl is not None:
                sub_results.append({"outcome": oc, "pnl": pnl, "entry_mc": entry_mc,
                                     "original_entry": r["entry"],
                                     "entry_change": (entry_mc / r["entry"] - 1) if r["entry"] else None})

        n_sub = len(sub_results)
        if n_sub < MIN_SAMPLE:
            delay_stats.append({"delay": delay_sec, "n": n_sub, "wr": None, "ev": None, "avg_entry_change": None})
            continue

        tp_sub = [s for s in sub_results if s["outcome"] == "TP"]
        wr_sub = len(tp_sub) / n_sub * 100
        ev_sub = sum(s["pnl"] for s in sub_results) / n_sub
        avg_entry_chg = sum(s["entry_change"] for s in sub_results if s.get("entry_change") is not None)
        n_chg = sum(1 for s in sub_results if s.get("entry_change") is not None)
        avg_entry_chg = avg_entry_chg / n_chg if n_chg else None

        delay_stats.append({"delay": delay_sec, "n": n_sub, "wr": wr_sub, "ev": ev_sub,
                             "avg_entry_change": avg_entry_chg})

    h += "<div class='sec'><table><thead><tr>"
    h += "<th>Delay entrada</th><th>n</th><th>Win Rate</th><th>EV (TP75/SL15)</th><th>Variação entry médio</th></tr></thead><tbody>"

    best_ev_delay = max((d for d in delay_stats if d["ev"] is not None), key=lambda d: d["ev"], default=None)

    for ds in delay_stats:
        d_label = f"{ds['delay']//60}min" if ds['delay'] >= 60 else "t+0 (imediato)"
        if ds["wr"] is None:
            h += f"<tr><td>{d_label}</td><td class='yellow'>{ds['n']} ⚠</td><td colspan='3' class='muted'>amostra insuficiente</td></tr>"
            continue
        wr_cls = "green" if ds["wr"] >= 30 else ("yellow" if ds["wr"] >= 20 else "red")
        ev_cls = "green" if ds["ev"] > 0 else "red"
        best_marker = " ←MELHOR EV" if best_ev_delay and ds["delay"] == best_ev_delay["delay"] else ""
        h += f"<tr><td><b>{d_label}{best_marker}</b></td><td>{ds['n']}</td>"
        h += f"<td class='{wr_cls}'>{ds['wr']:.1f}%</td>"
        h += f"<td class='{ev_cls}'>{ds['ev']:+.0%}</td>"
        chg_str = f"{ds['avg_entry_change']:+.1%}" if ds['avg_entry_change'] is not None else "—"
        chg_cls = "red" if (ds['avg_entry_change'] or 0) > 0 else "green"
        h += f"<td class='{chg_cls}'>{chg_str}</td></tr>"

    h += "</tbody></table></div>"

    if best_ev_delay and best_ev_delay["delay"] > 0:
        h += f"<div class='insight'>Aguardar {best_ev_delay['delay']//60}min melhora o EV (entry sobe em média {best_ev_delay.get('avg_entry_change', 0) or 0:+.1%}). Requer teste out-of-sample.</div>"
    else:
        h += "<div class='insight'>Entrada imediata tem EV similar ou melhor. Delay não parece ajudar nessa amostra.</div>"

    # ================================================================== #
    # C. DUMP SHAPE — slope pré-fundo
    # ================================================================== #
    h += "<h2>C. Dump Shape — inclinação do preço nos 2min antes do fundo</h2>"
    h += "<h3>Hipótese: capitulação brusca (slope muito negativo) → fundo real. Distribuição gradual (slope suave) → fundo falso.</h3>"
    h += "<h3>Baseado em H11: recovery tokens têm slope_c = -0.171 %MC/s vs -0.107 em no-recovery.</h3>"

    slope_data = [r for r in with_outcome if r.get("pre_slope") is not None]
    if len(slope_data) < MIN_SAMPLE:
        h += f"<div class='warn'>Amostra insuficiente de ticks pré-fundo (n={len(slope_data)}). Necessário pelo menos {MIN_SAMPLE} tokens com 3+ ticks nos 2min antes do bottom.</div>"
    else:
        wins_slope  = [r for r in slope_data if r["outcome"] == "TP"]
        losses_slope= [r for r in slope_data if r["outcome"] == "SL"]
        avg_slope_win  = _avg(wins_slope, "pre_slope")
        avg_slope_loss = _avg(losses_slope, "pre_slope")

        h += "<div class='grid'>"
        h += f"<div class='card'><div class='lbl'>Tokens com slope</div><div class='val'>{len(slope_data)}</div></div>"
        h += f"<div class='card'><div class='lbl'>Slope avg (TP)</div><div class='val green'>{_f(avg_slope_win, '.4f')} %/s</div></div>"
        h += f"<div class='card'><div class='lbl'>Slope avg (SL)</div><div class='val red'>{_f(avg_slope_loss, '.4f')} %/s</div></div>"
        h += "</div>"

        # Split por slope mediano
        sorted_slope = sorted(slope_data, key=lambda r: r["pre_slope"])
        med_slope = sorted_slope[len(sorted_slope) // 2]["pre_slope"]
        steep  = [r for r in slope_data if r["pre_slope"] <= med_slope]   # mais negativo = mais íngreme
        gradual= [r for r in slope_data if r["pre_slope"] >  med_slope]

        h += "<div class='sec'><table><thead><tr><th>Dump Shape</th><th>Win Rate</th><th>EV</th></tr></thead><tbody>"
        row_str, delta = _row_stat("slope", steep, f"Íngreme (slope ≤ {med_slope:.4f})", gradual, f"Gradual (slope > {med_slope:.4f})")
        h += row_str
        h += "</tbody></table></div>"

        if delta is not None and abs(delta) >= 8:
            winner = "ÍNGREME (capitulação)" if delta > 0 else "GRADUAL (distribuição)"
            h += f"<div class='insight'>Dump shape discrimina: {winner} tem {abs(delta):.1f}pp a mais de WR. Consistente com H11 (capitulação brusca = fundo real).</div>"
        else:
            h += "<div class='insight'>Slope pré-fundo não discrimina claramente nessa amostra de bounces. Pode mudar com mais dados.</div>"

    # ================================================================== #
    # D. BOUNCE PERSISTENCE
    # ================================================================== #
    h += "<h2>D. Bounce Persistence — preço sustenta acima do entry nos primeiros 3 ticks?</h2>"
    h += "<h3>Hipótese: 3 ticks consecutivos acima do entry → compradores reais. Confirmável em tempo real como filtro adicional.</h3>"

    persist_data = [r for r in with_outcome if r.get("persistent") is not None]
    if len(persist_data) < MIN_SAMPLE:
        h += f"<div class='warn'>Amostra insuficiente (n={len(persist_data)}) para análise de persistência.</div>"
    else:
        persistent_yes = [r for r in persist_data if r["persistent"] is True]
        persistent_no  = [r for r in persist_data if r["persistent"] is False]

        h += "<div class='grid'>"
        h += f"<div class='card'><div class='lbl'>Persistentes (3+ ticks ok)</div><div class='val green'>{len(persistent_yes)}</div></div>"
        h += f"<div class='card'><div class='lbl'>Não-persistentes</div><div class='val red'>{len(persistent_no)}</div></div>"
        h += f"<div class='card'><div class='lbl'>Sem 3 ticks</div><div class='val yellow'>{len(with_outcome) - len(persist_data)}</div></div>"
        h += "</div>"

        h += "<div class='sec'><table><thead><tr><th>Grupo</th><th>Win Rate</th><th>EV</th></tr></thead><tbody>"
        row_str, delta = _row_stat("persist", persistent_yes, "Persistente (≥3 ticks ok)", persistent_no, "Não-persistente")
        h += row_str
        h += "</tbody></table></div>"

        if delta is not None and delta >= 10:
            h += f"<div class='insight'>FILTRO REAL-TIME: exigir 3 ticks acima do entry melhora WR em {delta:.1f}pp. Implementável sem latência extra (aguarda ~90s após sinal).</div>"
        elif delta is not None and delta <= -10:
            h += f"<div class='insight'>Curioso: bounces NÃO-persistentes têm WR {abs(delta):.1f}pp maior. Possível que movimento rápido (que volta e sobe de novo) seja o padrão de TP.</div>"
        else:
            h += "<div class='insight'>Persistência não discrimina de forma significativa nessa amostra.</div>"

    # ================================================================== #
    # E. W-BOTTOM / DOUBLE-DIP
    # ================================================================== #
    h += "<h2>E. W-Bottom (Double-Dip) — preço volta ao nível do fundo antes de subir?</h2>"
    h += "<h3>Hipótese: segunda testada do fundo (W-shape) confirma suporte. Aguardar o W melhora WR?</h3>"

    wbottom_data = [r for r in with_outcome if r.get("w_bottom_dip") is not None]
    if len(wbottom_data) < MIN_SAMPLE:
        h += f"<div class='warn'>Amostra insuficiente (n={len(wbottom_data)}) para análise de W-bottom.</div>"
    else:
        w_yes = [r for r in wbottom_data if r["w_bottom_dip"] is True]
        w_no  = [r for r in wbottom_data if r["w_bottom_dip"] is False]
        pct_w = len(w_yes) / len(wbottom_data) * 100

        h += "<div class='grid'>"
        h += f"<div class='card'><div class='lbl'>Com double-dip</div><div class='val'>{len(w_yes)} ({pct_w:.0f}%)</div></div>"
        h += f"<div class='card'><div class='lbl'>Sem double-dip</div><div class='val'>{len(w_no)}</div></div>"
        h += "</div>"

        h += "<div class='sec'><table><thead><tr><th>Grupo</th><th>Win Rate</th><th>EV</th></tr></thead><tbody>"
        row_str, delta = _row_stat("wbottom", w_yes, "Com W-bottom (dip de volta)", w_no, "Sem W-bottom (sobe direto)")
        h += row_str
        h += "</tbody></table></div>"

        if delta is not None:
            if delta >= 10:
                h += f"<div class='insight'>W-bottom prediz melhor outcome (+{delta:.1f}pp). Estratégia: aguardar segunda testada antes de entrar.</div>"
            elif delta <= -10:
                h += f"<div class='insight'>Subida direta (sem W) tem WR {abs(delta):.1f}pp maior. Não esperar segunda testada — entra direto no primeiro bounce.</div>"
            else:
                h += "<div class='insight'>Padrão W-bottom não discrimina de forma significativa nessa amostra.</div>"

    # ================================================================== #
    # F. DUMP DEPTH × TIME_TO_BOTTOM GRID (para bounces detectados)
    # ================================================================== #
    h += "<h2>F. Dump Depth × Time-to-Bottom Grid — onde estão os bounces de maior qualidade?</h2>"
    h += "<h3>Hipótese: zona ótima = drop 40-70% + fundo em <15min. Mostra win rate por célula do grid.</h3>"

    DROP_BUCKETS = [
        ("30-40%", 0.30, 0.40),
        ("40-55%", 0.40, 0.55),
        ("55-70%", 0.55, 0.70),
        (">70%",   0.70, 1.01),
    ]
    TTB_BUCKETS = [
        ("<10min",  0, 10),
        ("10-20min", 10, 20),
        ("20-30min", 20, 30),
    ]

    h += "<div class='sec'><table><thead><tr><th>Drop\\TTB</th>"
    for tlabel, _, _ in TTB_BUCKETS:
        h += f"<th>{tlabel}</th>"
    h += "<th>Total Drop</th></tr></thead><tbody>"

    for dlabel, dmin, dmax in DROP_BUCKETS:
        h += f"<tr><td><b>{dlabel}</b></td>"
        drop_row = [r for r in with_outcome if r.get("pct_drop") is not None
                    and dmin <= r["pct_drop"] < dmax]
        for tlabel, tmin, tmax in TTB_BUCKETS:
            cell = [r for r in drop_row
                    if r.get("time_to_bottom") is not None
                    and tmin <= r["time_to_bottom"] < tmax]
            wr, n = _wr(cell)
            if n < MIN_SAMPLE:
                h += f"<td class='muted'>n={n}</td>"
            else:
                wr_cls = "zone-hot" if (wr or 0) >= 40 else ("zone-mid" if (wr or 0) >= 25 else "zone-cold")
                h += f"<td class='{wr_cls}'><b>{wr:.0f}%</b><br><small>n={n}</small></td>"
        # row total
        wr_tot, n_tot = _wr(drop_row)
        wr_tot_cls = "green" if (wr_tot or 0) >= 30 else ("yellow" if (wr_tot or 0) >= 20 else "red")
        h += f"<td class='{wr_tot_cls}'>{wr_tot:.0f}%</td>" if wr_tot is not None else "<td>—</td>"
        h += "</tr>"

    # Column totals
    h += "<tr style='border-top:2px solid #30363d'><td><b>Total TTB</b></td>"
    for tlabel, tmin, tmax in TTB_BUCKETS:
        col = [r for r in with_outcome
               if r.get("time_to_bottom") is not None
               and tmin <= r["time_to_bottom"] < tmax]
        wr, n = _wr(col)
        wr_cls = "green" if (wr or 0) >= 30 else ("yellow" if (wr or 0) >= 20 else "red")
        h += f"<td class='{wr_cls}'>{wr:.0f}% (n={n})</td>" if wr is not None else f"<td>n={n}</td>"
    h += "<td>—</td></tr>"
    h += "</tbody></table></div>"
    h += "<div class='insight'>Zonas verdes (≥40% WR) são candidatas a filtro de entrada em tempo real. Comparar com grid geral em /analise/dump-depth (que usa recovery rate, não WR de bounce).</div>"

    # ================================================================== #
    # G. BOUNCE SIZE AT SIGNAL
    # ================================================================== #
    h += "<h2>G. Bounce Size no Sinal — bounce_pct maior é sinal de momentum ou dead-cat?</h2>"
    h += "<h3>Hipótese: bounce de 30-50% pode indicar que compradores agressivos entraram. >60% pode indicar que já subiu demais.</h3>"

    BOUNCE_BUCKETS = [
        ("15-20%", 0.15, 0.20),
        ("20-30%", 0.20, 0.30),
        ("30-50%", 0.30, 0.50),
        (">50%",   0.50, 10.0),
    ]

    h += "<div class='sec'><table><thead><tr><th>Bounce% no sinal</th><th>n</th><th>Win Rate</th><th>EV</th></tr></thead><tbody>"
    for blabel, bmin, bmax in BOUNCE_BUCKETS:
        bucket = [r for r in with_outcome
                  if r.get("bounce_pct") is not None and bmin <= r["bounce_pct"] < bmax]
        wr, n = _wr(bucket)
        ev = _ev(bucket)
        if n < MIN_SAMPLE:
            h += f"<tr><td>{blabel}</td><td class='yellow'>{n} ⚠</td><td>—</td><td>—</td></tr>"
        else:
            wr_cls = "green" if (wr or 0) >= 30 else ("yellow" if (wr or 0) >= 20 else "red")
            ev_cls = "green" if (ev or 0) > 0 else "red"
            h += f"<tr><td>{blabel}</td><td>{n}</td>"
            h += f"<td class='{wr_cls}'>{wr:.1f}%</td>"
            h += f"<td class='{ev_cls}'>{ev:+.0%}</td></tr>"
    h += "</tbody></table></div>"

    # Correlação linear bounce_pct vs pnl
    valid_pnl_bounce = [(r["bounce_pct"], r["pnl"]) for r in with_outcome
                        if r.get("bounce_pct") is not None and r["pnl"] is not None]
    if len(valid_pnl_bounce) >= MIN_SAMPLE:
        bpcts = [x[0] for x in valid_pnl_bounce]
        pnls  = [x[1] for x in valid_pnl_bounce]
        mbp   = sum(bpcts) / len(bpcts)
        mp    = sum(pnls)  / len(pnls)
        cov   = sum((bpcts[i]-mbp)*(pnls[i]-mp) for i in range(len(bpcts)))
        var_b = sum((b-mbp)**2 for b in bpcts)
        corr_str = "—"
        if var_b > 0:
            var_p = sum((p-mp)**2 for p in pnls)
            if var_p > 0:
                corr = cov / ((var_b * var_p) ** 0.5)
                corr_cls = "green" if corr > 0.15 else ("red" if corr < -0.15 else "yellow")
                corr_str = f"<span class='{corr_cls}'>{corr:+.3f}</span>"
        h += f"<div class='insight'>Correlação de Pearson bounce_pct × pnl: {corr_str} — {'+' if '+' in corr_str else ''}correlação positiva = bounce maior → melhor resultado; negativa = dead-cat.</div>"

    # ================================================================== #
    # H. TP HIT TIMING
    # ================================================================== #
    h += "<h2>H. TP Hit Timing — em quanto tempo após o sinal o TP é atingido?</h2>"
    h += "<h3>Hipótese: a maioria dos TPs ocorre em <15min. Tokens que não atingem TP em 30min raramente atingem depois.</h3>"

    tp_hits = [r for r in wins if r.get("exit_elapsed") is not None and r.get("bounce_elapsed") is not None]
    if len(tp_hits) < MIN_SAMPLE:
        h += f"<div class='warn'>Amostra insuficiente de TP hits (n={len(tp_hits)}) para análise de timing.</div>"
    else:
        # Tempo entre sinal e TP hit (em minutos)
        tp_times = [(r["exit_elapsed"] - r["bounce_elapsed"]) / 60 for r in tp_hits]
        tp_times_sorted = sorted(tp_times)

        buckets_timing = [
            ("<5min",   0, 5),
            ("5-10min", 5, 10),
            ("10-20min",10, 20),
            ("20-40min",20, 40),
            (">40min",  40, 9999),
        ]
        h += "<div class='grid'>"
        med_tp_time = tp_times_sorted[len(tp_times_sorted)//2]
        avg_tp_time = sum(tp_times) / len(tp_times)
        h += f"<div class='card'><div class='lbl'>TP hits totais</div><div class='val green'>{len(tp_hits)}</div></div>"
        h += f"<div class='card'><div class='lbl'>Mediana tempo TP</div><div class='val'>{med_tp_time:.1f}min</div></div>"
        h += f"<div class='card'><div class='lbl'>Média tempo TP</div><div class='val'>{avg_tp_time:.1f}min</div></div>"
        h += "</div>"

        h += "<div class='sec'><table><thead><tr><th>Janela após sinal</th><th>TP hits</th><th>% dos TPs</th><th>Cumulativo</th></tr></thead><tbody>"
        cumulative = 0
        for btlabel, btmin, btmax in buckets_timing:
            count = sum(1 for t in tp_times if btmin <= t < btmax)
            pct   = count / len(tp_times) * 100
            cumulative += count
            cum_pct = cumulative / len(tp_times) * 100
            h += f"<tr><td>{btlabel}</td><td>{count}</td>"
            h += f"<td>{pct:.0f}%</td>"
            cum_cls = "green" if cum_pct >= 70 else ("yellow" if cum_pct >= 40 else "red")
            h += f"<td class='{cum_cls}'>{cum_pct:.0f}%</td></tr>"
        h += "</tbody></table></div>"

        pct_within_30 = sum(1 for t in tp_times if t <= 30) / len(tp_times) * 100
        h += f"<div class='insight'>{pct_within_30:.0f}% dos TPs são atingidos em ≤30min após o sinal. "
        if pct_within_30 >= 70:
            h += "Time-stop de 30min captura a maioria dos wins — consistente com H10.</div>"
        else:
            h += "Alguns TPs ocorrem depois de 30min — time-stop pode estar descartando winners válidos.</div>"

    # ================================================================== #
    # I. MC LEVEL AT SIGNAL VS MIGRATION
    # ================================================================== #
    h += "<h2>I. MC Nível do Sinal vs Migração — onde o bounce dispara relativo ao preço inicial?</h2>"
    h += "<h3>Hipótese: sinal em <40% da migração = dump profundo = chance maior de recovery forte. Sinal em >60% = dump superficial = menos potencial.</h3>"

    ratio_data = [r for r in with_outcome if r.get("mc_ratio_signal_mig") is not None]
    if len(ratio_data) < MIN_SAMPLE:
        h += f"<div class='warn'>Amostra insuficiente (n={len(ratio_data)}) para análise de nível relativo.</div>"
    else:
        RATIO_BUCKETS = [
            ("<20% mig",   0.00, 0.20),
            ("20-30% mig", 0.20, 0.30),
            ("30-40% mig", 0.30, 0.40),
            ("40-50% mig", 0.40, 0.50),
            ("50-65% mig", 0.50, 0.65),
            (">65% mig",   0.65, 10.0),
        ]
        h += "<div class='sec'><table><thead><tr><th>Entry % da migração</th><th>n</th><th>Win Rate</th><th>EV</th><th>Drop médio</th></tr></thead><tbody>"
        for rlabel, rmin, rmax in RATIO_BUCKETS:
            bucket = [r for r in ratio_data if rmin <= r["mc_ratio_signal_mig"] < rmax]
            wr, n = _wr(bucket)
            ev = _ev(bucket)
            avg_drop = _avg(bucket, "pct_drop")
            if n < MIN_SAMPLE:
                h += f"<tr><td>{rlabel}</td><td class='yellow'>{n} ⚠</td><td>—</td><td>—</td><td>—</td></tr>"
            else:
                wr_cls = "green" if (wr or 0) >= 30 else ("yellow" if (wr or 0) >= 20 else "red")
                ev_cls = "green" if (ev or 0) > 0 else "red"
                h += f"<tr><td>{rlabel}</td><td>{n}</td>"
                h += f"<td class='{wr_cls}'>{wr:.1f}%</td>"
                h += f"<td class='{ev_cls}'>{ev:+.0%}</td>"
                h += f"<td>{avg_drop:.0%}</td>" if avg_drop else "<td>—</td>"
                h += "</tr>"
        h += "</tbody></table></div>"

        # Distribuição dos ratios
        ratios = [r["mc_ratio_signal_mig"] for r in ratio_data]
        med_ratio = sorted(ratios)[len(ratios)//2]
        h += f"<div class='insight'>Entry mediano a {med_ratio:.0%} do MC de migração. Sinal dispara quando preço caiu ~{(1-med_ratio):.0%} da migração.</div>"

    # ================================================================== #
    # J. NOVO: Time-to-bottom × Bounce Velocity 2D grid
    # ================================================================== #
    h += "<h2>J. TTB × Velocity 2D — grid ortogonal (novo)</h2>"
    h += "<h3>Pergunta original: o melhor preditor (TTB < 25min) melhora ainda mais combinado com velocity rápida?</h3>"

    grid_data = [r for r in with_outcome
                 if r.get("time_to_bottom") is not None and r.get("velocity") is not None]

    if len(grid_data) < MIN_SAMPLE * 2:
        h += f"<div class='warn'>Amostra insuficiente (n={len(grid_data)}) para grid 2D TTB × Velocity.</div>"
    else:
        vel_vals = [r["velocity"] for r in grid_data]
        med_v = sorted(vel_vals)[len(vel_vals)//2]

        TTB_2 = [("<25min", 0, 25), ("≥25min", 25, 9999)]
        VEL_2 = [(f"Fast (≥{med_v*100:.3f}%/s)", med_v, 1e9), (f"Slow (<{med_v*100:.3f}%/s)", 0, med_v)]

        h += "<div class='sec'><table><thead><tr><th>TTB \\ Velocity</th>"
        for vlabel, _, _ in VEL_2:
            h += f"<th>{vlabel}</th>"
        h += "</tr></thead><tbody>"

        for tlabel, tmin, tmax in TTB_2:
            h += f"<tr><td><b>{tlabel}</b></td>"
            for vlabel, vmin, vmax in VEL_2:
                cell = [r for r in grid_data
                        if tmin <= r["time_to_bottom"] < tmax
                        and vmin <= r["velocity"] < vmax]
                wr, n = _wr(cell)
                if n < MIN_SAMPLE:
                    h += f"<td class='muted'>n={n}</td>"
                else:
                    wr_cls = "zone-hot" if (wr or 0) >= 40 else ("zone-mid" if (wr or 0) >= 25 else "zone-cold")
                    h += f"<td class='{wr_cls}'><b>{wr:.0f}%</b> (n={n})</td>"
            h += "</tr>"
        h += "</tbody></table></div>"
        h += "<div class='insight'>Célula TTB<25min + Fast velocity é a combinação teórica ideal (capitulação rápida + compradores agressivos). Se WR ≥ 45%, implementar como filtro adicional ao bounce detector.</div>"

    # ================================================================== #
    # K. NOVO: Snap quality — tempo entre migração e detecção do bounce
    # ================================================================== #
    h += "<h2>K. Capture Delay — tempo entre a migração e a detecção do bounce</h2>"
    h += "<h3>Pergunta: bounces detectados mais cedo (menor elapsed_sec) têm melhor WR? Ou o bot está chegando tarde demais?</h3>"

    delay_data = [r for r in with_outcome if r.get("bounce_elapsed") is not None]
    if len(delay_data) < MIN_SAMPLE:
        h += f"<div class='warn'>Amostra insuficiente (n={len(delay_data)}).</div>"
    else:
        ELAPSED_BUCKETS = [
            ("<5min",    0,    300),
            ("5-10min",  300,  600),
            ("10-15min", 600,  900),
            ("15-20min", 900,  1200),
            (">20min",   1200, 9999),
        ]
        h += "<div class='sec'><table><thead><tr><th>Elapsed no sinal</th><th>n</th><th>Win Rate</th><th>EV</th></tr></thead><tbody>"
        for el_label, el_min, el_max in ELAPSED_BUCKETS:
            bucket = [r for r in delay_data
                      if el_min <= r["bounce_elapsed"] < el_max]
            wr, n = _wr(bucket)
            ev = _ev(bucket)
            if n < MIN_SAMPLE:
                h += f"<tr><td>{el_label}</td><td class='yellow'>{n} ⚠</td><td>—</td><td>—</td></tr>"
            else:
                wr_cls = "green" if (wr or 0) >= 30 else ("yellow" if (wr or 0) >= 20 else "red")
                ev_cls = "green" if (ev or 0) > 0 else "red"
                h += f"<tr><td>{el_label}</td><td>{n}</td>"
                h += f"<td class='{wr_cls}'>{wr:.1f}%</td>"
                h += f"<td class='{ev_cls}'>{ev:+.0%}</td></tr>"
        h += "</tbody></table></div>"

        elaps_all = [r["bounce_elapsed"] for r in delay_data]
        med_elap  = sorted(elaps_all)[len(elaps_all)//2]
        h += f"<div class='insight'>Bounce mediano detectado em {med_elap//60:.0f}min {med_elap%60:.0f}s após migração. "
        if med_elap < 600:
            h += "Bot está capturando bounces precoces — bom sinal de latência."
        else:
            h += "Mediano > 10min — maioria dos bounces detectados tarde. Pode indicar dump lento (distribuição) dominando a amostra."
        h += "</div>"

    # ================================================================== #
    # L. NOVO: creator_token_status × bounce outcome
    # ================================================================== #
    h += "<h2>L. Creator Status no momento do bounce — dev ainda segura?</h2>"
    h += "<h3>Pergunta: bounces em tokens onde creator_hold=TRUE têm WR diferente? Dev segurando pode ser sinal de fé no projeto OU armadilha de saída.</h3>"

    creator_data = [r for r in with_outcome if r.get("creator_token_status") is not None]
    if len(creator_data) < MIN_SAMPLE:
        h += f"<div class='warn'>Amostra insuficiente com creator_token_status (n={len(creator_data)}).</div>"
    else:
        dev_hold  = [r for r in creator_data if r["creator_token_status"] == "creator_hold"]
        dev_close = [r for r in creator_data if r["creator_token_status"] == "creator_close"]

        h += "<div class='sec'><table><thead><tr><th>Creator Status</th><th>Win Rate</th><th>EV</th></tr></thead><tbody>"
        row_str, delta = _row_stat("creator", dev_hold, "creator_hold (dev segura)", dev_close, "creator_close (dev vendeu)")
        h += row_str
        h += "</tbody></table></div>"

        if delta is not None:
            if delta >= 10:
                h += f"<div class='insight'>Dev segurando → +{delta:.1f}pp WR. Possível: dev ainda acredita no token. Usar como filtro confirmatório.</div>"
            elif delta <= -10:
                h += f"<div class='insight'>Dev JÁ vendeu → +{abs(delta):.1f}pp WR. Contra-intuitivo: mercado bounceou mesmo sem dev. Pode indicar CTO (comunidade assumiu).</div>"
            else:
                h += "<div class='insight'>Creator status não discrimina de forma significativa nessa amostra de bounces.</div>"

    # ================================================================== #
    # TABELA COMPLETA — todos os bounces com features derivadas
    # ================================================================== #
    h += "<h2>Tabela Completa — todos os bounces com features derivadas</h2>"
    h += "<div class='sec'><table><thead><tr>"
    h += "<th>Symbol</th><th>Outcome</th><th>P&L</th><th>Drop</th><th>Bounce%</th>"
    h += "<th>TTB</th><th>Elapsed sinal</th><th>Entry%mig</th>"
    h += "<th>Velocity (%/s)</th><th>Slope pré</th><th>Persist?</th><th>W-bot?</th>"
    h += "<th>Exit (min após)</th></tr></thead><tbody>"

    for r in sorted(records, key=lambda x: x.get("ts_signal") or 0, reverse=True):
        oc  = r["outcome"]
        oc_cls = "green" if oc == "TP" else ("red" if oc == "SL" else "yellow")
        pnl = r["pnl"]
        pnl_cls = "green" if (pnl or 0) > 0 else "red"

        exit_min = None
        if r.get("exit_elapsed") and r.get("bounce_elapsed"):
            exit_min = (r["exit_elapsed"] - r["bounce_elapsed"]) / 60

        h += "<tr>"
        h += f"<td><b>{r.get('symbol') or r['mint'][:8]}</b></td>"
        h += f"<td class='{oc_cls}'>{oc}</td>"
        h += f"<td class='{pnl_cls}'>{pnl:+.0%}</td>" if pnl is not None else "<td class='muted'>—</td>"
        h += f"<td>{r['pct_drop']:.0%}</td>" if r.get("pct_drop") else "<td>—</td>"
        h += f"<td>{r['bounce_pct']:.0%}</td>" if r.get("bounce_pct") else "<td>—</td>"
        h += f"<td>{r['time_to_bottom']:.1f}</td>" if r.get("time_to_bottom") else "<td>—</td>"
        h += f"<td>{r['bounce_elapsed']//60}m{r['bounce_elapsed']%60:02d}s</td>"
        h += f"<td>{r['mc_ratio_signal_mig']:.0%}</td>" if r.get("mc_ratio_signal_mig") else "<td>—</td>"
        vel = r.get("velocity")
        h += f"<td>{vel*100:.4f}</td>" if vel else "<td>—</td>"
        slp = r.get("pre_slope")
        h += f"<td>{slp:.4f}</td>" if slp else "<td>—</td>"
        h += f"<td>{'✅' if r.get('persistent') else ('❌' if r.get('persistent') is False else '—')}</td>"
        h += f"<td>{'✅' if r.get('w_bottom_dip') else ('❌' if r.get('w_bottom_dip') is False else '—')}</td>"
        h += f"<td>{exit_min:.1f}min</td>" if exit_min else "<td>—</td>"
        h += "</tr>"

    h += "</tbody></table></div>"
    h += "</body></html>"
    return h


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
    monitor   = TradeMonitor()
    scheduler = HeapScheduler()
    hot       = HotMonitor(gmgn, scheduler, executor=executor)
    scanner   = Scanner(gmgn, hot)
    scheduler.start()
    hot.start()
    scanner.start()
    monitor.set_helius(hot._helius)  # injeta helius para coleta de absorção durante holding
    monitor.start()
    logger.info("Todos os serviços iniciados")


_start_services()


@app.route("/debug/open-trades")
def debug_open_trades():
    """Diagnóstico de trades abertos — mostra por que TradeMonitor não detectou saída.

    Para cada trade 'open' ou 'trailing_active':
    - entry_expected vs entry_mc_real (fill real)
    - sl_mc e tp_mc calculados
    - ticks disponíveis: count, fonte, menor low, maior high
    - query_order GMGN ao vivo: estado atual da ordem
    """
    import traceback
    try:
        from executor import TRADE_SL_PCT, TRAILING_ACTIVATION_PCT, TRAILING_DRAWDOWN_PCT
        from gmgn_client import get_sol_usd
        from datetime import timezone

        CSS = ("<style>body{background:#0d1117;color:#e6edf3;font:12px/1.6 monospace;padding:20px}"
               "a{color:#58a6ff;text-decoration:none}table{border-collapse:collapse;width:100%}"
               "th{background:#161b22;padding:6px 10px;text-align:left;font-size:11px;color:#8b949e}"
               "td{padding:4px 10px;border-bottom:1px solid #21262d;font-size:11px}"
               ".box{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px 14px;"
               "margin:6px 4px;display:inline-block;vertical-align:top}"
               ".lbl{color:#8b949e;font-size:10px}.val{font-size:16px;font-weight:bold}"
               ".green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}"
               ".dim{color:#484f58}.warn{color:#e3b341}.nav a{color:#58a6ff;margin-right:14px;font-size:11px}"
               "pre{background:#161b22;padding:8px;border-radius:4px;font-size:10px;white-space:pre-wrap;word-break:break-all}"
               "h3{color:#8b949e;font-size:11px;margin:16px 0 6px;border-bottom:1px solid #21262d;padding-bottom:4px}"
               "</style>")

        sol_usd = get_sol_usd() or 0
        now = __import__("datetime").datetime.now(timezone.utc)

        open_trades    = db.get_open_trades()
        trailing_trades = db.get_trailing_active_trades()
        all_trades = open_trades + trailing_trades

        def _fmt_usd(v):
            if v is None: return '<span class="dim">—</span>'
            return f"${v:,.0f}"

        def _fmt_pct(v):
            if v is None: return '<span class="dim">—</span>'
            color = "green" if v >= 0 else "red"
            return f"<span class='{color}'>{v*100:+.1f}%</span>"

        rows_html = ""
        for t in all_trades:
            trade_id        = t["id"]
            mint            = t["mint"]
            symbol          = t.get("symbol") or mint[:8]
            status          = t.get("status") or "?"
            order_id        = t.get("order_id") or ""
            entry_expected  = t.get("entry_expected") or 0
            entry_price_usd = t.get("entry_price_usd")
            tokens_received = t.get("tokens_received")
            created_at      = t.get("created_at")
            ref_ts          = t.get("ts_order_sent") or t.get("ts_signal") or created_at
            sl_pct          = t.get("sl_pct") or TRADE_SL_PCT
            tp_pct          = t.get("tp_pct") or (TRAILING_ACTIVATION_PCT / 100)

            age_min = (now - created_at).total_seconds() / 60 if created_at else 0

            # entry_mc_real
            entry_mc_real = None
            if entry_price_usd and tokens_received and tokens_received > 0:
                entry_mc_real = (entry_price_usd / tokens_received) * 1_000_000_000

            sl_ref = entry_mc_real or entry_expected or 0
            sl_mc  = sl_ref * (1 - sl_pct) if sl_ref else None
            tp_mc  = entry_mc_real * (1 + tp_pct) if entry_mc_real else None

            # Ticks após ref_ts
            ticks = []
            try:
                ticks = db.get_ticks_after(mint, min_ts=ref_ts) if ref_ts else []
            except Exception:
                pass

            n_ticks   = len(ticks)
            min_low   = min((float(tk["mc_low"]) for tk in ticks if tk.get("mc_low")), default=None)
            max_high  = max((float(tk["mc_high"]) for tk in ticks if tk.get("mc_high")), default=None)

            # Diagnóstico: sl_mc atingido nos ticks?
            sl_hit_in_ticks = sl_mc and min_low and min_low <= sl_mc
            tp_hit_in_ticks = tp_mc and max_high and max_high >= tp_mc

            # query_order ao vivo
            gmgn_raw = {}
            gmgn_note = '<span class="dim">sem order_id</span>'
            if order_id:
                try:
                    res = gmgn.query_order(order_id)
                    gmgn_raw = res.get("data") or res
                    state = str((gmgn_raw.get("confirmation") or {}).get("state") or
                                gmgn_raw.get("status") or "").lower()
                    co_list = gmgn_raw.get("condition_orders") or []
                    co_info = ", ".join(
                        f"{co.get('order_type','?')}={co.get('state') or co.get('status','?')}"
                        for co in co_list
                    ) or "nenhum"
                    gmgn_note = f"state={state} | condition_orders: {co_info}"
                except Exception as e:
                    gmgn_note = f'<span class="warn">erro: {e}</span>'

            # Diagnóstico final
            diag = []
            if not entry_price_usd:
                diag.append('<span class="warn">⚠ fill não registrado (entry_price_usd=NULL)</span>')
            if not entry_mc_real and entry_expected:
                diag.append('<span class="warn">⚠ usando entry_expected como ref SL (fill ausente)</span>')
            if entry_mc_real and entry_expected and entry_mc_real > entry_expected * 1.3:
                diag.append(f'<span class="warn">⚠ entrada real {entry_mc_real/entry_expected:.1f}× acima do esperado (slippage alto)</span>')
            if sl_mc and min_low and sl_hit_in_ticks:
                diag.append('<span class="green">✓ SL atingido em ticks — TradeMonitor deveria ter detectado</span>')
            elif sl_mc and min_low and not sl_hit_in_ticks:
                diag.append(f'<span class="red">✗ SL NÃO atingido em ticks (min_low={_fmt_usd(min_low)} > sl_mc={_fmt_usd(sl_mc)})</span>')
            if n_ticks == 0:
                diag.append('<span class="red">✗ zero ticks após ref_ts — TradeMonitor cego</span>')

            status_color = "yellow" if status == "open" else "green" if status == "tp" else "red"
            rows_html += f"""
<h3>#{trade_id} — <b>{symbol}</b>
  <span class='{status_color}'>[{status}]</span>
  <span class='dim'>age={age_min:.0f}min | {mint[:16]}…</span>
</h3>
<table>
  <tr>
    <th>entry_expected</th><th>entry_mc_real</th><th>sl_mc</th><th>tp_mc</th>
    <th>ticks</th><th>min_low</th><th>max_high</th><th>sl_hit?</th><th>tp_hit?</th>
  </tr>
  <tr>
    <td>{_fmt_usd(entry_expected)}</td>
    <td>{_fmt_usd(entry_mc_real)}</td>
    <td>{_fmt_usd(sl_mc)}</td>
    <td>{_fmt_usd(tp_mc) if tp_mc else '<span class="dim">—</span>'}</td>
    <td>{n_ticks}</td>
    <td>{_fmt_usd(min_low)}</td>
    <td>{_fmt_usd(max_high)}</td>
    <td>{"<span class='green'>SIM</span>" if sl_hit_in_ticks else "<span class='dim'>não</span>"}</td>
    <td>{"<span class='green'>SIM</span>" if tp_hit_in_ticks else "<span class='dim'>não</span>"}</td>
  </tr>
</table>
<p><b>GMGN:</b> {gmgn_note}</p>
<p>{'<br>'.join(diag) if diag else '<span class="dim">sem diagnóstico específico</span>'}</p>
<details><summary style='cursor:pointer;color:#58a6ff;font-size:10px'>raw query_order response</summary>
<pre>{__import__("json").dumps(gmgn_raw, indent=2, default=str)[:1500]}</pre>
</details>
"""

        if not all_trades:
            rows_html = "<p class='dim'>Nenhum trade open/trailing_active no momento.</p>"

        n_open     = len(open_trades)
        n_trailing = len(trailing_trades)

        return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Open Trades Debug</title>{CSS}
<meta http-equiv='refresh' content='15'>
</head><body>
<h1 style='color:#58a6ff;font-size:15px'>Debug — Trades Abertos</h1>
<p style='color:#8b949e;font-size:10px'>auto-refresh 15s · mostra entry_expected vs entry_mc_real, sl_mc/tp_mc, ticks, query_order GMGN ao vivo</p>
<div class='nav'>
  <a href='/debug/executor-status'>Executor Status</a>
  <a href='/analise/wr-breakdown'>WR Breakdown</a>
  <a href='/analise/h22-correlation'>H22</a>
</div>
<br>
<div class='box'><div class='lbl'>Open</div><div class='val' style='color:#d29922'>{n_open}</div></div>
<div class='box'><div class='lbl'>Trailing Active</div><div class='val' style='color:#58a6ff'>{n_trailing}</div></div>
<div class='box'><div class='lbl'>SOL/USD</div><div class='val'>${sol_usd:.0f}</div></div>
<br><br>
{rows_html}
</body></html>"""

    except Exception as e:
        return f"<pre>Erro: {e}\n{traceback.format_exc()}</pre>", 500


@app.route("/debug/executor-status")
def debug_executor_status():
    """Diagnóstico completo do pipeline de trading — mostra onde a cadeia pode estar bloqueada."""
    import traceback
    try:
        return _executor_status_inner()
    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


def _executor_status_inner():
    import datetime as _dt

    trading_enabled = os.getenv("TRADING_ENABLED", "false").lower() == "true"
    cal_max         = int(os.getenv("CALIBRATION_MAX_TRADES", "50"))
    cal_amount      = float(os.getenv("CALIBRATION_AMOUNT_USD", "1.0"))
    bounce_min      = float(os.getenv("BOUNCE_MIN_PCT", "0.15"))
    bounce_drop     = float(os.getenv("BOUNCE_DROP_MIN", "0.30"))
    bounce_time_max = float(os.getenv("BOUNCE_TIME_MAX", "20.0"))
    freshness_sec   = int(os.getenv("BOTTOM_FRESHNESS_SEC", "300"))

    # Contagem de trades no DB
    try:
        n_trades = db.count_trades()
        executor_paused = n_trades >= cal_max
    except Exception as e:
        n_trades = f"erro: {e}"
        executor_paused = False

    # Último bounce signal
    last_bounce = None
    last_bounce_ts = None
    try:
        import psycopg2.extras
        conn = psycopg2.connect(db.DATABASE_URL)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM bounce_signals ORDER BY ts_signal DESC LIMIT 1")
            row = cur.fetchone()
            if row:
                last_bounce    = dict(row)
                last_bounce_ts = row.get("ts_signal")
        conn.close()
    except Exception:
        pass

    # Tokens ativos no HotMonitor
    hot_count = 0
    hot_mints = []
    try:
        if scanner and hasattr(scanner, 'hot'):
            hot_tokens = scanner.hot._tokens
            hot_count  = len(hot_tokens)
            hot_mints  = list(hot_tokens.keys())[:10]
    except Exception:
        pass

    # Último scan (via scanner global)
    last_scan_ts = None
    try:
        if scanner and hasattr(scanner, '_last_tick_ts'):
            last_scan_ts = scanner._last_tick_ts
    except Exception:
        pass

    # Status global
    ok   = "✅"
    warn = "⚠️"
    bad  = "❌"

    def row(label, value, status=""):
        color = {"✅": "#4CAF50", "⚠️": "#FF9800", "❌": "#f44336"}.get(status, "#ddd")
        return f"<tr><th>{label}</th><td style='color:{color}'>{value}</td></tr>"

    rows = []
    rows.append(row("TRADING_ENABLED",
                    "true ✅" if trading_enabled else "false ❌  ← se bounces disparam mas nenhum trade acontece, este é o problema",
                    ok if trading_enabled else bad))
    rows.append(row("Trades no DB (não-error)",
                    f"{n_trades} / {cal_max} {'⚠️ LIMITE ATINGIDO — executor pausado' if executor_paused else ok}",
                    bad if executor_paused else ok))
    rows.append(row("CALIBRATION_AMOUNT_USD", f"${cal_amount:.2f} por trade"))
    rows.append(row("BOUNCE_DROP_MIN",    f"{bounce_drop*100:.0f}% queda mínima da migração"))
    rows.append(row("BOUNCE_MIN_PCT",     f"{bounce_min*100:.0f}% bounce mínimo do fundo"))
    rows.append(row("BOUNCE_TIME_MAX",    f"{bounce_time_max:.0f}min janela máxima pós-migração"))
    rows.append(row("BOTTOM_FRESHNESS_SEC", f"{freshness_sec}s (A.6 — fundo deve ser recente)"))
    rows.append(row("HotMonitor tokens ativos", f"{hot_count} token(s) sendo monitorados",
                    ok if hot_count > 0 else warn))

    if last_bounce_ts:
        ago = (_dt.datetime.now(_dt.timezone.utc) - last_bounce_ts).total_seconds()
        ago_str = f"{ago/60:.0f}min atrás" if ago < 3600 else f"{ago/3600:.1f}h atrás"
        b = last_bounce
        rows.append(row("Último bounce detectado",
                        f"{ago_str} — {b.get('symbol','?')} drop={b.get('drop_from_mig',0)*100:.0f}% "
                        f"bounce={b.get('bounce_pct',0)*100:.0f}% em {b.get('elapsed_sec',0)//60}min",
                        ok if ago < 1800 else warn))
    else:
        rows.append(row("Último bounce detectado", "Nenhum no DB", warn))

    # Diagnóstico conclusivo
    if not trading_enabled:
        diagnosis = "❌ <b>TRADING_ENABLED=false</b> — bounces disparam mas executor descarta silenciosamente. Logs mostram 'DRY-RUN'. Ative no Railway."
    elif executor_paused:
        diagnosis = f"❌ <b>Limite de trades atingido</b> ({n_trades}/{cal_max}). Aumente CALIBRATION_MAX_TRADES no Railway (ex: 150)."
    elif hot_count == 0:
        diagnosis = "⚠️ <b>Nenhum token no HotMonitor</b> — scanner não encontrou migrações recentes ou bot acabou de iniciar."
    else:
        diagnosis = "✅ Pipeline parece OK. Se não há trades, provavelmente nenhum bounce passou pelos filtros A.2/A.3/A.5/A.6 ainda."

    h = f"""<!DOCTYPE html><html><head>
<meta charset="utf-8">
<title>Executor Status — NeideBOT</title>
<style>
  body {{ background:#111; color:#ddd; font-family:monospace; padding:20px; }}
  h1,h2 {{ color:#FFD700; }}
  table {{ border-collapse:collapse; width:100%; margin:12px 0; font-size:13px; }}
  th,td {{ border:1px solid #333; padding:8px 10px; text-align:left; }}
  th {{ background:#1e1e1e; color:#aaa; width:280px; }}
  .box {{ background:#1a1a1a; border:1px solid #333; padding:16px; border-radius:6px; margin:12px 0; }}
  .diag {{ font-size:14px; padding:14px; border-radius:4px; background:#1e1e1e; border-left:4px solid #FF9800; }}
</style>
</head><body>
<h1>Executor Status — Pipeline Diagnóstico</h1>
<p style="color:#888;font-size:11px">Atualizado: {_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC</p>

<div class="box diag">{diagnosis}</div>

<div class="box">
  <h2>Configuração do Executor</h2>
  <table>{''.join(rows)}</table>
</div>

<div class="box">
  <h2>HotMonitor — Tokens Ativos ({hot_count})</h2>
  {"<p style='color:#888'>Nenhum token sendo monitorado agora.</p>" if not hot_mints else
   "<p style='color:#888;font-size:11px'>" + " | ".join(f"<code>{m[:12]}…</code>" for m in hot_mints) + "</p>"}
</div>

<div class="box">
  <h2>Checklist rápida</h2>
  <table>
    <tr><th>1. TRADING_ENABLED=true no Railway?</th><td>{'✅' if trading_enabled else '❌ não está ativo'}</td></tr>
    <tr><th>2. Trades abaixo do limite?</th><td>{'✅' if not executor_paused else f'❌ {n_trades}/{cal_max} — aumente CALIBRATION_MAX_TRADES'}</td></tr>
    <tr><th>3. HotMonitor com tokens?</th><td>{'✅' if hot_count > 0 else '⚠️ vazio agora (normal se sem migrações recentes)'}</td></tr>
    <tr><th>4. Algum bounce recente?</th><td>{'✅ ' + ago_str if last_bounce_ts else '⚠️ nenhum ainda'}</td></tr>
  </table>
</div>

<p style="color:#555;font-size:11px">
  Links: <a href="/debug/trade-audit" style="color:#4FC3F7">/debug/trade-audit</a> |
  <a href="/scanner" style="color:#4FC3F7">/scanner</a> |
  <a href="/debug/signal-live" style="color:#4FC3F7">/debug/signal-live</a>
</p>
</body></html>"""
    return h


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
    import html
    from gmgn_client import GMGN_WALLET_ADDRESS, GMGN_PRIVATE_KEY, SOL_NATIVE_MINT, _load_ed25519_key
    import time, base64
    from uuid import uuid4

    lines = []
    lines.append(f"GMGN_WALLET_ADDRESS : {GMGN_WALLET_ADDRESS or '❌ NÃO CONFIGURADO'}")
    lines.append(f"GMGN_PRIVATE_KEY    : {'✅ SET (' + str(len(GMGN_PRIVATE_KEY)) + ' chars)' if GMGN_PRIVATE_KEY else '❌ NÃO CONFIGURADO'}")

    pk = _load_ed25519_key(GMGN_PRIVATE_KEY)
    lines.append(f"Chave carregada     : {'✅ OK' if pk else '❌ FALHOU'}")

    # Verifica keypair apenas para chaves Base58/Base64 (não PEM — GMGN emite signing key separada da wallet)
    if pk and not GMGN_PRIVATE_KEY.strip().startswith("-----"):
        try:
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
            import base58 as _b58
            pub_bytes = pk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            derived_address = _b58.b58encode(pub_bytes).decode("ascii")
            match = derived_address == GMGN_WALLET_ADDRESS
            lines.append(f"Endereço derivado   : {derived_address}")
            if match:
                lines.append("Keypair             : ✅ CORRESPONDE — private key gera este wallet address")
            else:
                lines.append("Keypair             : ❌ NÃO CORRESPONDE — private key não gera este wallet!")
                lines.append(f"  → Esperado: {GMGN_WALLET_ADDRESS}")
                lines.append(f"  → Derivado: {derived_address}")
                lines.append("  → Solução: usar a private key do wallet DJVwyki... ou corrigir GMGN_WALLET_ADDRESS")
        except Exception as e:
            lines.append(f"Keypair check       : ⚠️ não verificado ({e})")
    elif pk and GMGN_PRIVATE_KEY.strip().startswith("-----"):
        lines.append("Keypair             : ℹ️ PEM (chave de signing GMGN — separada da wallet Solana, OK)")

    if pk and GMGN_WALLET_ADDRESS:
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
        lines.append(f"Sorted QS           : {html.escape(qs)}")   # fix: &timestamp não vira ×tamp no browser
        lines.append(f"Message (signed)    : {html.escape(message)}")
        lines.append(f"Signature (b64)     : {sig[:30]}...")
        lines.append("")
        lines.append("✅ Assinatura gerada com sucesso — formato OK")

        # --- Teste LIVE: chama query_order com ID fictício ---
        # Testa a assinatura contra o servidor real sem gastar dinheiro.
        # Se auth OK → resposta "order not found" / 4xx diferente de 401.
        # Se auth falha → 401 AUTH_SIGNATURE_INVALID.
        lines.append("")
        lines.append("--- Teste LIVE (query_order) ---")
        try:
            import requests as _req
            from gmgn_client import GMGN_HOST, GMGN_API_KEY
            fake_order = "test-sign-check-000"
            live_path  = "/v1/trade/query_order"
            live_ts    = str(int(time.time()))
            live_cid   = str(uuid4())
            live_params = {"timestamp": live_ts, "client_id": live_cid, "order_id": fake_order}
            live_qs     = "&".join(f"{k}={v}" for k, v in sorted(live_params.items()))
            live_msg    = f"{live_path}:{live_qs}::{live_ts}"
            live_sig    = base64.b64encode(pk.sign(live_msg.encode("utf-8"))).decode("ascii")
            resp = _req.get(
                f"{GMGN_HOST.rstrip('/')}{live_path}",
                params=live_params,
                headers={"X-APIKEY": GMGN_API_KEY, "X-Signature": live_sig},
                timeout=10,
            )
            lines.append(f"HTTP status  : {resp.status_code}")
            lines.append(f"Body         : {resp.text[:300]}")
            if resp.status_code == 401:
                lines.append("Resultado    : ❌ AUTH_SIGNATURE_INVALID — assinatura rejeitada pelo servidor")
                lines.append("  → IP não está na whitelist OU wallet não registrada na conta GMGN")
            else:
                lines.append("Resultado    : ✅ AUTH OK — assinatura aceita pelo servidor (erro esperado sobre order_id)")
        except Exception as e:
            lines.append(f"Erro na chamada live: {e}")

    return "<pre>" + "\n".join(lines) + "</pre>"


@app.route("/analise/trade-ticks")
def analise_trade_ticks():
    """
    Trajetória de ticks pós-sinal para cada trade executado.
    Mostra MC ao longo do tempo com referências entry_expected e entry_price_usd (se disponível).
    GET /analise/trade-ticks?trade_id=<id>  → trade específico
    GET /analise/trade-ticks                → lista todos os trades com resumo
    """
    import traceback
    try:
        return _trade_ticks_inner()
    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


def _trade_ticks_inner():
    trade_id = request.args.get("trade_id", type=int)
    WINDOW_MIN = int(request.args.get("window", 30))  # minutos após sinal

    with db._cursor() as cur:
        if trade_id:
            cur.execute("""
                SELECT t.id, t.mint, t.symbol, t.status, t.entry_expected,
                       t.entry_price_usd, t.tp_pct, t.sl_pct, t.amount_usd,
                       b.ts_signal, b.mc_min, b.bounce_pct, b.drop_from_mig, b.mc_migration
                FROM trades t
                LEFT JOIN bounce_signals b ON b.mint = t.mint
                WHERE t.id = %s
            """, (trade_id,))
        else:
            cur.execute("""
                SELECT t.id, t.mint, t.symbol, t.status, t.entry_expected,
                       t.entry_price_usd, t.tp_pct, t.sl_pct, t.amount_usd,
                       b.ts_signal, b.mc_min, b.bounce_pct, b.drop_from_mig, b.mc_migration
                FROM trades t
                LEFT JOIN bounce_signals b ON b.mint = t.mint
                WHERE t.status IN ('tp','sl','open')
                ORDER BY t.id DESC
            """)
        trades = [dict(r) for r in cur.fetchall()]

    if not trades:
        return "<pre>Sem trades.</pre>"

    # Detalhe de um trade específico
    if trade_id and trades:
        tr = trades[0]
        ts_sig = tr["ts_signal"]
        if not ts_sig:
            return "<pre>Sem ts_signal para este trade.</pre>"

        with db._cursor() as cur:
            cur.execute("""
                SELECT tick_ts,
                       COALESCE(mc_high, mc) AS mc_high,
                       COALESCE(mc_low, mc)  AS mc_low,
                       mc,
                       source
                FROM token_ticks
                WHERE mint = %s
                  AND tick_ts >= %s
                  AND tick_ts <= %s + INTERVAL '%s minutes'
                  AND mc IS NOT NULL
                ORDER BY tick_ts ASC
            """, (tr["mint"], ts_sig, ts_sig, WINDOW_MIN))
            ticks = [dict(r) for r in cur.fetchall()]

        entry_exp  = tr["entry_expected"] or 0
        entry_real = tr["entry_price_usd"] or 0
        tp_pct     = tr["tp_pct"] or 0.75
        sl_pct     = tr["sl_pct"] or 0.15

        tp_exp  = entry_exp  * (1 + tp_pct) if entry_exp  else None
        sl_exp  = entry_exp  * (1 - sl_pct) if entry_exp  else None
        tp_real = entry_real * (1 + tp_pct) if entry_real else None
        sl_real = entry_real * (1 - sl_pct) if entry_real else None

        # Verificar hits com referência correta (real se disponível, esperado como fallback)
        ref_entry = entry_real or entry_exp
        ref_tp    = tp_real or tp_exp
        ref_sl    = sl_real or sl_exp

        first_tp_hit = next((t for t in ticks if ref_tp and t["mc_high"] >= ref_tp), None)
        first_sl_hit = next((t for t in ticks if ref_sl and t["mc_low"]  <= ref_sl), None)

        def elapsed(tick_ts):
            if not ts_sig: return "?"
            return f"{(tick_ts - ts_sig).total_seconds() / 60:.1f}min"

        tick_rows = ""
        for tk in ticks:
            mins = (tk["tick_ts"] - ts_sig).total_seconds() / 60
            mc   = tk["mc"]
            pct_from_exp  = (mc - entry_exp)  / entry_exp  * 100 if entry_exp  else None
            pct_from_real = (mc - entry_real) / entry_real * 100 if entry_real else None

            flags = ""
            if ref_tp and tk["mc_high"] >= ref_tp:  flags += " <b style='color:#4f4'>▲TP HIT</b>"
            if ref_sl and tk["mc_low"]  <= ref_sl:  flags += " <b style='color:#f44'>▼SL HIT</b>"

            bg = ""
            if ref_tp and tk["mc_high"] >= ref_tp: bg = "background:#1a3a1a"
            if ref_sl and tk["mc_low"]  <= ref_sl: bg = "background:#3a1a1a"

            tick_rows += (f"<tr style='{bg}'>"
                          f"<td>{mins:.1f}min</td>"
                          f"<td>${mc:,.0f}</td>"
                          f"<td>${tk['mc_high']:,.0f}</td>"
                          f"<td>${tk['mc_low']:,.0f}</td>"
                          f"<td>{'—' if pct_from_exp is None else f'{pct_from_exp:+.1f}%'}</td>"
                          f"<td>{'—' if pct_from_real is None else f'{pct_from_real:+.1f}%'}</td>"
                          f"<td style='color:#888;font-size:11px'>{tk['source']}</td>"
                          f"<td>{flags}</td>"
                          f"</tr>")

        real_note = f"${entry_real:,.0f}" if entry_real else "<span style='color:#f44'>NULL — fill não capturado</span>"
        ref_note  = "entry_price_usd (real)" if entry_real else "entry_expected (fallback)"

        return f"""<html><head><title>Ticks #{tr['id']} {tr['symbol']}</title></head>
<body style="font-family:monospace;background:#111;color:#eee;padding:20px">
<h2>Trade #{tr['id']} — {tr['symbol']} ({tr['status'].upper()})</h2>
<p><a href="/analise/trade-ticks" style="color:#4af">← todos os trades</a></p>

<table border=1 cellpadding=6 style="border-collapse:collapse;font-size:13px;margin-bottom:16px">
<tr><td>Status DB</td><td><b>{tr['status']}</b></td></tr>
<tr><td>MC bottom (fundo)</td><td>${tr['mc_min']:,.0f}</td></tr>
<tr><td>entry_expected (sinal)</td><td>${entry_exp:,.0f}</td></tr>
<tr><td>entry_price_usd (fill real)</td><td>{real_note}</td></tr>
<tr><td>TP ref ({ref_note})</td><td style='color:#4f4'>${ref_tp:,.0f} (+{tp_pct*100:.0f}%)</td></tr>
<tr><td>SL ref ({ref_note})</td><td style='color:#f44'>${ref_sl:,.0f} (-{sl_pct*100:.0f}%)</td></tr>
<tr><td>Primeiro TP hit</td><td style='color:#4f4'>{elapsed(first_tp_hit['tick_ts']) if first_tp_hit else '—'}</td></tr>
<tr><td>Primeiro SL hit</td><td style='color:#f44'>{elapsed(first_sl_hit['tick_ts']) if first_sl_hit else '—'}</td></tr>
<tr><td>Ticks carregados</td><td>{len(ticks)} (janela {WINDOW_MIN}min)</td></tr>
</table>

<h3>Trajetória de ticks</h3>
<table border=1 cellpadding=5 style="border-collapse:collapse;font-size:12px">
<tr style="background:#222">
  <th>Tempo</th><th>MC close</th><th>MC high</th><th>MC low</th>
  <th>% vs entry_exp</th><th>% vs entry_real</th><th>Source</th><th>Evento</th>
</tr>
{tick_rows if tick_rows else '<tr><td colspan=8 style="color:#888">Sem ticks nesta janela.</td></tr>'}
</table>
</body></html>"""

    # Lista de todos os trades com resumo de ticks
    list_rows = ""
    for tr in trades:
        ts_sig     = tr["ts_signal"]
        entry_exp  = tr["entry_expected"] or 0
        entry_real = tr["entry_price_usd"] or 0
        tp_pct     = tr["tp_pct"] or 0.75
        sl_pct     = tr["sl_pct"] or 0.15

        n_ticks = 0
        peak_mc = low_mc = None
        if ts_sig:
            with db._cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) AS n,
                           MAX(COALESCE(mc_high, mc)) AS peak,
                           MIN(COALESCE(mc_low,  mc)) AS low
                    FROM token_ticks
                    WHERE mint = %s
                      AND tick_ts >= %s
                      AND tick_ts <= %s + INTERVAL '%s minutes'
                      AND mc IS NOT NULL
                """, (tr["mint"], ts_sig, ts_sig, WINDOW_MIN))
                row = cur.fetchone()
                n_ticks = row["n"] or 0
                peak_mc = row["peak"]
                low_mc  = row["low"]

        ref = entry_real or entry_exp
        tp_hit = peak_mc and ref and peak_mc >= ref * (1 + tp_pct)
        sl_hit = low_mc  and ref and low_mc  <= ref * (1 - sl_pct)

        pct_peak = (peak_mc - ref) / ref * 100 if (peak_mc and ref) else None
        pct_low  = (low_mc  - ref) / ref * 100 if (low_mc  and ref) else None

        real_flag = "✅" if entry_real else "<span style='color:#fa0'>—</span>"
        status_c  = {"tp": "#4f4", "sl": "#f44", "open": "#4af"}.get(tr["status"], "#888")
        outcome = "✅ TP" if tp_hit else ("❌ SL" if sl_hit else "⏳")

        list_rows += (f"<tr>"
                      f"<td><a href='/analise/trade-ticks?trade_id={tr['id']}' style='color:#4af'>#{tr['id']} {tr['symbol']}</a></td>"
                      f"<td style='color:{status_c}'>{tr['status'].upper()}</td>"
                      f"<td>{real_flag}</td>"
                      f"<td>{n_ticks}</td>"
                      f"<td style='color:#4f4'>{'—' if pct_peak is None else f'{pct_peak:+.1f}%'}</td>"
                      f"<td style='color:#f44'>{'—' if pct_low  is None else f'{pct_low:+.1f}%'}</td>"
                      f"<td>{outcome}</td>"
                      f"</tr>")

    return f"""<html><head><title>Trade Ticks</title></head>
<body style="font-family:monospace;background:#111;color:#eee;padding:20px">
<h2>Trajetória de ticks — {len(trades)} trades</h2>
<p style="color:#aaa;font-size:12px">
  Referência: entry_price_usd (real) se disponível, entry_expected (sinal) como fallback.<br>
  Janela: {WINDOW_MIN}min após ts_signal. Clique num trade para ver os ticks completos.
</p>
<table border=1 cellpadding=6 style="border-collapse:collapse;font-size:13px">
<tr style="background:#222">
  <th>Trade</th><th>Status DB</th><th>Fill real?</th>
  <th>N ticks</th><th>Peak %</th><th>Low %</th><th>Deveria?</th>
</tr>
{list_rows}
</table>
<p style="color:#888;font-size:11px;margin-top:8px">
  "Deveria?" = se MC bateu TP ou SL usando referência correta (real ou esperado).<br>
  "Fill real?" = entry_price_usd populado no DB. NULL = fill poll não capturou.
</p>
</body></html>"""


@app.route("/analise/calibration")
def analise_calibration():
    """Sumário completo dos 50 trades de calibração: WR, EV real, friction, overshoot, latência."""
    import traceback
    try:
        return _calibration_inner()
    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


def _calibration_inner():
    SOL_USD = _get_sol_usd() or 150.0
    TP_PCT  = float(os.getenv("TRADE_TP_PCT", "0.75"))
    SL_PCT  = float(os.getenv("TRADE_SL_PCT", "0.15"))

    with db._cursor() as cur:
        cur.execute("""
            SELECT id, mint, symbol, status, pnl_pct_real, pnl_pct_expected,
                   amount_usd, entry_expected, entry_price_usd,
                   slippage_pct, fee_sol, latency_sec,
                   ts_signal, ts_order_sent, ts_order_confirmed,
                   tp_pct, sl_pct, exit_type, exit_ts,
                   mc_bottom, mc_migration, tokens_received
            FROM trades
            ORDER BY id ASC
        """)
        trades = [dict(r) for r in cur.fetchall()]

    if not trades:
        return "<pre>Sem trades no banco.</pre>"

    # Separar por status
    done   = [t for t in trades if t["status"] in ("tp", "sl")]
    open_  = [t for t in trades if t["status"] == "open"]
    errors = [t for t in trades if t["status"] in ("error", "timeout", "pending")]
    tp_    = [t for t in done if t["status"] == "tp"]
    sl_    = [t for t in done if t["status"] == "sl"]

    n_total = len(trades)
    n_done  = len(done)
    n_tp    = len(tp_)
    n_sl    = len(sl_)
    wr      = n_tp / n_done if n_done > 0 else 0

    # EV real (pnl_pct_real já inclui fees)
    pnls = [t["pnl_pct_real"] for t in done if t["pnl_pct_real"] is not None]
    ev_real = sum(pnls) / len(pnls) if pnls else None

    # EV esperado pelo backtest (sem friction)
    ev_backtest = wr * TP_PCT - (1 - wr) * SL_PCT if n_done > 0 else None

    # Friction = diferença entre EV teórico e EV real
    friction_per_trade = (ev_backtest - ev_real) if (ev_backtest is not None and ev_real is not None) else None

    # Break-even WR com friction real
    # EV = wr × TP - (1-wr) × SL - friction = 0
    # wr × (TP + SL) = SL + friction
    be_wr = (SL_PCT + (friction_per_trade or 0)) / (TP_PCT + SL_PCT) if friction_per_trade is not None else SL_PCT / (TP_PCT + SL_PCT)

    # Entry overshoot: quanto pagamos acima do esperado
    overshoots = []
    for t in done:
        if t["entry_price_usd"] and t["entry_expected"] and t["entry_expected"] > 0:
            overshoot = (t["entry_price_usd"] - t["entry_expected"]) / t["entry_expected"]
            overshoots.append(overshoot)
    avg_overshoot = sum(overshoots) / len(overshoots) if overshoots else None

    # Friction em USD por trade
    fees_usd = []
    for t in done:
        if t["fee_sol"] and t["amount_usd"]:
            fee_usd = t["fee_sol"] * SOL_USD
            fees_usd.append(fee_usd)
    avg_fee_usd = sum(fees_usd) / len(fees_usd) if fees_usd else None

    # Latência
    lats = [t["latency_sec"] for t in done if t["latency_sec"] is not None]
    avg_lat = sum(lats) / len(lats) if lats else None

    # Slippage
    slips = [t["slippage_pct"] for t in done if t["slippage_pct"] is not None]
    avg_slip = sum(slips) / len(slips) if slips else None

    # P&L total em USD ($1/trade)
    total_amount = sum(t["amount_usd"] or 0 for t in done)
    total_pnl_usd = sum((t["pnl_pct_real"] or 0) * (t["amount_usd"] or 0) for t in done if t["pnl_pct_real"])

    def c(v, good=True):
        if v is None: return "#888"
        return "#4f4" if (v > 0) == good else "#f44"

    def pct(v):
        return f"{v*100:+.2f}%" if v is not None else "—"

    def usd(v):
        return f"${v:.4f}" if v is not None else "—"

    # Tabela por trade
    trade_rows = ""
    for t in trades:
        s = t["status"] or "—"
        bg = {"tp": "#1a3a1a", "sl": "#3a1a1a", "error": "#2a2a00", "timeout": "#2a2a00"}.get(s, "")
        pnl = t["pnl_pct_real"]
        pnl_s = pct(pnl) if pnl else "—"
        pnl_c = c(pnl) if pnl else "#888"
        over = None
        if t["entry_price_usd"] and t["entry_expected"] and t["entry_expected"] > 0:
            over = (t["entry_price_usd"] - t["entry_expected"]) / t["entry_expected"]
        trade_rows += (f"<tr style='background:{bg}'>"
                       f"<td>#{t['id']}</td>"
                       f"<td>{t['symbol'] or t['mint'][:8]}</td>"
                       f"<td>{s.upper()}</td>"
                       f"<td style='color:{pnl_c}'>{pnl_s}</td>"
                       f"<td>{pct(over) if over is not None else '—'}</td>"
                       f"<td>{(str(round(t['latency_sec'],1))+'s') if t['latency_sec'] else '—'}</td>"
                       f"<td>{pct(t['slippage_pct']) if t['slippage_pct'] else '—'}</td>"
                       f"</tr>")

    return f"""<html><head><title>Calibração — 50 trades</title></head>
<body style="font-family:monospace;background:#111;color:#eee;padding:20px">
<h2>Calibração — {n_total} trades de $1</h2>

<h3>Resultado geral</h3>
<table border=1 cellpadding=8 style="border-collapse:collapse;font-size:14px">
<tr><td>Trades executados</td><td><b>{n_total}</b> (done={n_done}, open={len(open_)}, erro={len(errors)})</td></tr>
<tr><td>TP hits / SL hits</td><td><b style="color:#4f4">{n_tp} TP</b> / <b style="color:#f44">{n_sl} SL</b></td></tr>
<tr><td>Win Rate live</td><td style="color:{c(wr-0.20)}"><b>{wr*100:.1f}%</b></td></tr>
<tr><td>Break-even WR (com friction real)</td><td><b>{be_wr*100:.1f}%</b></td></tr>
<tr><td>EV real (avg pnl_pct_real)</td><td style="color:{c(ev_real)}"><b>{pct(ev_real)}</b> por trade</td></tr>
<tr><td>EV teórico (sem friction)</td><td style="color:{c(ev_backtest)}"><b>{pct(ev_backtest)}</b> por trade</td></tr>
<tr><td>Friction medida</td><td style="color:#f44"><b>{pct(friction_per_trade)}</b> por trade</td></tr>
<tr><td>P&L total (USD)</td><td style="color:{c(total_pnl_usd)}"><b>{usd(total_pnl_usd)}</b> sobre {usd(total_amount)} investido</td></tr>
</table>

<h3>Execução & friction</h3>
<table border=1 cellpadding=8 style="border-collapse:collapse;font-size:14px">
<tr><td>Entry overshoot médio</td><td style="color:#f44"><b>{pct(avg_overshoot)}</b> acima do esperado</td></tr>
<tr><td>Slippage médio</td><td><b>{pct(avg_slip)}</b></td></tr>
<tr><td>Fee médio (SOL→USD)</td><td><b>{usd(avg_fee_usd)}</b> por trade</td></tr>
<tr><td>Latência média (detecção→fill)</td><td><b>{f'{avg_lat:.1f}s' if avg_lat else '—'}</b></td></tr>
</table>

<h3>Todos os trades</h3>
<table border=1 cellpadding=6 style="border-collapse:collapse;font-size:12px">
<tr style="background:#222"><th>#</th><th>Token</th><th>Status</th>
<th>P&L real</th><th>Entry overshoot</th><th>Latência</th><th>Slippage</th></tr>
{trade_rows}
</table>

<p style="color:#888;font-size:11px;margin-top:12px">
  WR break-even = (SL% + friction%) / (TP% + SL%). SOL/USD usado: ${SOL_USD:.0f}.<br>
  entry_overshoot = (fill_price - expected_entry) / expected_entry. Positivo = comprou mais caro.<br>
  Friction = EV_teórico − EV_real (inclui GMGN fee, tip, priority, slippage).
</p>
</body></html>"""


@app.route("/analise/sl-investigation")
def analise_sl_investigation():
    """
    Investigação dos SL trades: quantos eram runners que perdemos por SL apertado?
    Mostra para cada SL: mc_bottom, entry, SL floor, low real, recovery (mc_1h).
    Identifica padrão W-bottom: tokens que foram abaixo do SL floor mas depois subiram.
    """
    try:
        return _sl_investigation_inner()
    except Exception as e:
        import traceback
        return f"<pre>{traceback.format_exc()}</pre>", 500


def _sl_investigation_inner():
    SL_PCT  = float(os.getenv("TRADE_SL_PCT", "0.15"))
    TP_PCT  = 0.75  # trailing activation

    with db._cursor() as cur:
        cur.execute("""
            SELECT
                t.id,
                t.symbol,
                t.mint,
                t.ts_signal,
                t.mc_bottom,
                t.entry_expected,
                t.status,
                t.exit_type,
                t.pnl_pct_real,
                b.mc_min        AS bottom_kline,
                b.mc_at_signal  AS entry_signal,
                b.bounce_pct,
                m.mc_1h,
                m.mc_migration,
                m.time_to_bottom
            FROM trades t
            LEFT JOIN bounce_signals b ON b.mint = t.mint
            LEFT JOIN migrations     m ON m.mint = t.mint
            WHERE t.status IN ('sl', 'sl_hit', 'open', 'trailing_active', 'expired')
               OR t.exit_type IN ('sl', 'sl_hit')
            ORDER BY t.id DESC
            LIMIT 100
        """)
        trades = [dict(r) for r in cur.fetchall()]

    rows = []
    for t in trades:
        mint        = t["mint"]
        ts_signal   = t["ts_signal"]
        mc_bottom   = t["bottom_kline"] or t["mc_bottom"]
        entry_mc    = t["entry_signal"] or t["entry_expected"]
        if not mc_bottom or not entry_mc:
            continue

        sl_floor = entry_mc * (1 - SL_PCT)

        # Ticks após o sinal para ver o trajeto real de preço
        with db._cursor() as cur:
            cur.execute("""
                SELECT
                    MIN(COALESCE(mc_low, mc)) AS actual_low,
                    MAX(COALESCE(mc_high, mc)) AS actual_high,
                    COUNT(*) AS n_ticks
                FROM token_ticks
                WHERE mint = %s AND tick_ts >= %s
            """, (mint, ts_signal))
            tick_agg = cur.fetchone()

        actual_low  = tick_agg["actual_low"]  if tick_agg else None
        actual_high = tick_agg["actual_high"] if tick_agg else None
        n_ticks     = tick_agg["n_ticks"]     if tick_agg else 0

        mc_1h        = t.get("mc_1h")
        recovery_ref = mc_1h or actual_high

        # Quanto o preço caiu abaixo do fundo detectado
        w_depth_pct = (actual_low / mc_bottom - 1) if actual_low and mc_bottom else None
        # Quanto subiu no pico
        peak_gain   = (actual_high / entry_mc - 1) if actual_high and entry_mc else None
        # O SL floor ficou ABAIXO do fundo real? (i.e., o SL está colado no fundo)
        sl_vs_bottom = (sl_floor / mc_bottom - 1) if mc_bottom else None
        # O token era um runner? (mc_1h > entry × 2.0 = +100%)
        is_runner   = recovery_ref and entry_mc and recovery_ref >= entry_mc * 2.0
        # O low real cruzou o SL floor?
        crossed_sl  = actual_low and actual_low <= sl_floor
        # Runner perdido: cruzou SL mas depois subiu muito
        lost_runner = crossed_sl and is_runner

        rows.append({
            **t,
            "mc_bottom":     mc_bottom,
            "entry_mc":      entry_mc,
            "sl_floor":      sl_floor,
            "sl_vs_bottom":  sl_vs_bottom,
            "actual_low":    actual_low,
            "actual_high":   actual_high,
            "w_depth_pct":   w_depth_pct,
            "peak_gain":     peak_gain,
            "mc_1h":         mc_1h,
            "n_ticks":       n_ticks,
            "is_runner":     is_runner,
            "crossed_sl":    crossed_sl,
            "lost_runner":   lost_runner,
        })

    total          = len(rows)
    n_crossed_sl   = sum(1 for r in rows if r["crossed_sl"])
    n_runners      = sum(1 for r in rows if r["is_runner"])
    n_lost_runners = sum(1 for r in rows if r["lost_runner"])
    # Distribuição de W-bottom depth nos lost runners
    lr_depths      = [r["w_depth_pct"] for r in rows if r["lost_runner"] and r["w_depth_pct"] is not None]
    lr_peaks       = [r["peak_gain"]   for r in rows if r["lost_runner"] and r["peak_gain"]   is not None]
    # Profundidade média do W-bottom em todos os SL trades com ticks
    all_depths     = [r["w_depth_pct"] for r in rows if r["w_depth_pct"] is not None]
    # SL floor médio relativo ao mc_bottom
    sl_vs_btm_vals = [r["sl_vs_bottom"] for r in rows if r["sl_vs_bottom"] is not None]
    avg_sl_vs_btm  = sum(sl_vs_btm_vals)/len(sl_vs_btm_vals) if sl_vs_btm_vals else None

    def _fp(v, mult=100): return f"{v*mult:+.1f}%" if v is not None else "—"
    def _fm(v): return f"${v:,.0f}" if v is not None else "—"

    _CSS = """<style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Courier New',monospace;background:#0d1117;color:#c9d1d9;font-size:12px}
    h1{padding:14px 16px;color:#58a6ff;font-size:16px;border-bottom:1px solid #21262d}
    h2{font-size:13px;color:#58a6ff;padding:8px 16px 4px;margin-top:12px}
    .grid{display:flex;gap:10px;padding:8px 16px;flex-wrap:wrap}
    .card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 14px;min-width:120px}
    .lbl{color:#8b949e;font-size:10px;margin-bottom:2px}
    .val{font-size:16px;font-weight:bold}
    .green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}.muted{color:#8b949e}
    table{width:100%;border-collapse:collapse;margin:0 0 16px}
    th{background:#161b22;color:#8b949e;padding:4px 8px;text-align:left;border-bottom:1px solid #30363d;font-size:10px;white-space:nowrap}
    td{padding:3px 8px;border-bottom:1px solid #21262d;font-size:11px}
    tr:hover td{background:#161b22}
    .sec{padding:4px 16px 12px}
    .insight{background:#0d2137;color:#79c0ff;padding:8px 14px;margin:6px 16px;border-radius:4px;font-size:11px;border-left:3px solid #58a6ff}
    .warn{background:#3d2200;color:#d29922;padding:8px 14px;margin:6px 16px;border-radius:4px;font-size:11px}
    .nav{padding:8px 16px;display:flex;gap:10px;flex-wrap:wrap;border-bottom:1px solid #21262d}
    .nav a{color:#58a6ff;font-size:11px;text-decoration:none}
    .runner{background:#1a2a1a}
    .lost{background:#2a1a1a}
    </style>"""

    h = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>SL Investigation</title>{_CSS}</head><body>"
    h += "<h1>Investigação SL — Runners Perdidos</h1>"
    h += "<div class='nav'><a href='/trades'>Trades</a><a href='/analise'>Analise</a></div>"

    # Cards de resumo
    h += "<div class='grid'>"
    h += f"<div class='card'><div class='lbl'>SL trades</div><div class='val'>{total}</div></div>"
    h += f"<div class='card'><div class='lbl'>Cruzaram SL floor</div><div class='val red'>{n_crossed_sl}</div></div>"
    h += f"<div class='card'><div class='lbl'>Runners (≥+100% mc_1h)</div><div class='val yellow'>{n_runners}</div></div>"
    h += f"<div class='card'><div class='lbl'>Runners PERDIDOS</div><div class='val red'>{n_lost_runners}</div></div>"
    if avg_sl_vs_btm is not None:
        h += f"<div class='card'><div class='lbl'>SL floor vs fundo (avg)</div><div class='val {'red' if avg_sl_vs_btm < 0 else 'green'}'>{_fp(avg_sl_vs_btm)}</div></div>"
    h += "</div>"

    # Diagnóstico
    if avg_sl_vs_btm is not None and avg_sl_vs_btm > -0.05:
        h += f"""<div class='warn'>
⚠ SL floor médio está <b>{_fp(avg_sl_vs_btm)}</b> do mc_bottom detectado — praticamente no fundo.
Qualquer retest do fundo (W-bottom) dispara o SL antes do rally real.
</div>"""

    if n_lost_runners > 0:
        avg_depth = sum(lr_depths)/len(lr_depths) if lr_depths else None
        avg_peak  = sum(lr_peaks)/len(lr_peaks)   if lr_peaks  else None
        h += f"""<div class='insight'>
📊 <b>Runners perdidos ({n_lost_runners}):</b>
W-bottom médio = <b>{_fp(avg_depth)}</b> abaixo do mc_bottom detectado.
Peak médio após entrada = <b>{_fp(avg_peak)}</b>.
→ Para capturar esses runners, SL floor deveria estar ≥ <b>{_fp(min(lr_depths) if lr_depths else None)}</b> abaixo do fundo.
</div>"""

    # Tabela principal
    h += "<h2>Detalhe por trade</h2><div class='sec'><table>"
    h += "<tr><th>#</th><th>Symbol</th><th>Fundo</th><th>Entry</th><th>SL floor</th><th>SL vs fundo</th><th>Low real</th><th>W-depth</th><th>Peak</th><th>mc_1h</th><th>Runner?</th><th>Perdido?</th></tr>"

    for r in rows:
        row_cls = "runner" if r["is_runner"] and not r["lost_runner"] else ("lost" if r["lost_runner"] else "")
        runner_icon = "🏆" if r["is_runner"] and not r["lost_runner"] else ("💸" if r["lost_runner"] else "💀")
        sl_vs_btm_color = "red" if r["sl_vs_bottom"] and r["sl_vs_bottom"] > -0.05 else "green"
        w_depth_color   = "red" if r["w_depth_pct"] and r["w_depth_pct"] < -0.05 else "yellow"

        h += f"""<tr class='{row_cls}'>
<td>{r['id']}</td>
<td><b>{r['symbol'] or r['mint'][:8]}</b></td>
<td>{_fm(r['mc_bottom'])}</td>
<td>{_fm(r['entry_mc'])}</td>
<td>{_fm(r['sl_floor'])}</td>
<td class='{sl_vs_btm_color}'>{_fp(r['sl_vs_bottom'])}</td>
<td>{_fm(r['actual_low'])}</td>
<td class='{w_depth_color}'>{_fp(r['w_depth_pct'])}</td>
<td class='green'>{_fp(r['peak_gain'])}</td>
<td>{_fm(r['mc_1h'])}</td>
<td>{"✅" if r['is_runner'] else "❌"}</td>
<td>{runner_icon}</td>
</tr>"""

    h += "</table></div>"

    # Insight sobre o SL ideal
    if lr_depths:
        # Qual SL (% abaixo do mc_bottom) capturaria os runners?
        # SL deve estar abaixo do W-bottom mais profundo dos runners
        # SL floor = mc_bottom × (1 + sl_floor_pct_below_bottom)
        # Para capturar: sl_floor_pct_below_bottom < min(lr_depths)
        safe_floor = min(lr_depths) - 0.02  # 2% de margem abaixo do pior W-bottom
        # SL % from entry = 1 - (mc_bottom × (1+safe_floor)) / (mc_bottom × 1.15)
        sl_from_entry = 1 - (1 + safe_floor) / 1.15
        h += f"""<div class='insight'>
💡 <b>Recomendação baseada nos dados:</b><br>
W-bottom mais profundo nos runners perdidos: <b>{_fp(min(lr_depths))}</b> abaixo do mc_bottom.<br>
SL floor ideal: mc_bottom × {(1+safe_floor):.2f} (= {_fp(safe_floor)} do fundo).<br>
Traduzido para GMGN price_scale (de entry_expected): <b>~{sl_from_entry*100:.0f}%</b> em vez de {SL_PCT*100:.0f}%.<br>
<br>
Mas atenção: tokens que morrem também passam pelo W-bottom.
Precisamos checar se os "mortos" foram mais fundo que os runners.
</div>"""

    h += "</body></html>"
    return h


@app.route("/debug/trade-analysis")
def debug_trade_analysis():
    """Analisa tokens comprados: o que aconteceu após o bounce signal."""
    import traceback
    try:
        return _debug_trade_analysis_inner()
    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


def _debug_trade_analysis_inner():
    mints = request.args.getlist("mint")
    if not mints:
        # Default: últimos 20 trades no banco
        trades = db.get_trades(limit=20)
        mints = [t["mint"] for t in trades if t.get("mint")]

    rows = []
    with db._cursor() as cur:
        for mint in mints:
            cur.execute("""
                SELECT token_nome, mc_migration, mc_bottom, mc_peak, time_to_bottom, pct_drop
                FROM migrations WHERE mint = %s
            """, (mint,))
            mg = cur.fetchone()

            cur.execute("""
                SELECT ts_signal, mc_at_signal, bounce_pct, elapsed_sec
                FROM bounce_signals WHERE mint = %s
            """, (mint,))
            bs = cur.fetchone()

            cur.execute("""
                SELECT id, status, entry_expected, tokens_received,
                       entry_price_sol, strategy_id, error_msg
                FROM trades WHERE mint = %s ORDER BY id DESC LIMIT 1
            """, (mint,))
            tr = cur.fetchone()

            ticks_after = []
            if bs and bs["ts_signal"]:
                cur.execute("""
                    SELECT tick_ts,
                           COALESCE(mc_high, mc) AS mc_high,
                           COALESCE(mc_low,  mc) AS mc_low
                    FROM token_ticks
                    WHERE mint = %s AND tick_ts >= %s
                    ORDER BY tick_ts ASC LIMIT 80
                """, (mint, bs["ts_signal"]))
                ticks_after = cur.fetchall()

            name      = (mg["token_nome"] if mg else mint[:8])
            entry_mc  = bs["mc_at_signal"] if bs else None
            peak_mc   = max((t["mc_high"] for t in ticks_after), default=None)
            low_mc    = min((t["mc_low"]  for t in ticks_after), default=None)
            pct_up    = (peak_mc  - entry_mc) / entry_mc if (peak_mc  and entry_mc) else None
            pct_dn    = (low_mc   - entry_mc) / entry_mc if (low_mc   and entry_mc) else None
            hit_tp    = pct_up  >=  0.75 if pct_up  is not None else None
            hit_sl    = pct_dn  <= -0.15 if pct_dn  is not None else None

            rows.append({
                "mint": mint, "name": name, "mg": mg, "bs": bs, "tr": tr,
                "entry_mc": entry_mc,
                "peak_mc": peak_mc, "low_mc": low_mc,
                "pct_up": pct_up, "pct_dn": pct_dn,
                "hit_tp": hit_tp, "hit_sl": hit_sl,
                "n_ticks": len(ticks_after),
            })

    def fmt(v, pct=False, usd=False):
        if v is None: return "—"
        if pct: return f"{v:+.1%}"
        if usd: return f"${v:,.0f}"
        return str(v)

    html_rows = ""
    for r in rows:
        bg = ""
        result = "—"
        if r["hit_tp"]:  bg = "background:#1a3a1a"; result = "✅ TP HIT"
        elif r["hit_sl"]: bg = "background:#3a1a1a"; result = "❌ SL HIT"
        elif r["pct_up"] is not None: result = "⏳ sem hit"

        tr = r["tr"]
        strat = tr["strategy_id"] if tr else "—"
        strat_ok = "✅" if strat and strat != "—" else "❌ sem TP/SL"

        html_rows += f"""<tr style="{bg}">
  <td>{r['name']}</td>
  <td>{fmt(r['bs']['elapsed_sec'] / 60 if r['bs'] and r['bs']['elapsed_sec'] else None)}min</td>
  <td>{fmt(r['bs']['bounce_pct'] if r['bs'] else None, pct=True)}</td>
  <td>{fmt(r['entry_mc'] if r['bs'] else None, usd=True) if r['bs'] else '—'}</td>
  <td>{fmt(r['peak_mc'], usd=True)}</td>
  <td style="color:#4f4">{fmt(r['pct_up'], pct=True)}</td>
  <td>{fmt(r['low_mc'], usd=True)}</td>
  <td style="color:#f44">{fmt(r['pct_dn'], pct=True)}</td>
  <td>{result}</td>
  <td>{strat_ok}</td>
  <td style="font-size:11px">{r['mint'][:12]}...</td>
</tr>"""

    tp_hits = sum(1 for r in rows if r["hit_tp"])
    sl_hits = sum(1 for r in rows if r["hit_sl"])
    no_hit  = sum(1 for r in rows if not r["hit_tp"] and not r["hit_sl"] and r["pct_up"] is not None)

    return f"""<!DOCTYPE html><html><body style="background:#111;color:#eee;font-family:monospace;padding:20px">
<h2>Análise de Trades — {len(rows)} tokens</h2>
<p>TP hits: <b style="color:#4f4">{tp_hits}</b> | SL hits: <b style="color:#f44">{sl_hits}</b> | Sem hit (dados insuf.): {no_hit}</p>
<table border=1 cellpadding=6 style="border-collapse:collapse;font-size:13px">
<tr style="background:#222"><th>Token</th><th>Elapsed</th><th>Bounce%</th>
<th>Entry MC</th><th>Peak MC</th><th>↑ Max</th>
<th>Low MC</th><th>↓ Min</th><th>Resultado</th><th>TP/SL</th><th>Mint</th></tr>
{html_rows}
</table>
<p style="color:#888;font-size:12px">Baseado em token_ticks após ts_signal. Sem ticks = sem dados pós-entrada.</p>
</body></html>"""


@app.route("/analise/h9")
def analise_h9():
    """H9 — Validação: tokens com signal type 12 perto do fundo têm recovery maior?"""
    with db._cursor() as cur:
        # Resumo geral
        cur.execute("""
            SELECT COUNT(*)              AS total_sinais,
                   COUNT(DISTINCT mint)  AS tokens_unicos,
                   MIN(ts_recorded)      AS primeiro,
                   MAX(ts_recorded)      AS ultimo
            FROM token_signals
        """)
        summ = dict(cur.fetchone())

        # Sinais por dia
        cur.execute("""
            SELECT DATE(ts_recorded) AS dia, COUNT(*) AS n
            FROM token_signals
            GROUP BY 1 ORDER BY 1 DESC LIMIT 14
        """)
        por_dia = cur.fetchall()

        # H9 — breakdown por threshold de proximidade ao fundo
        # trigger_ratio = trigger_mc / mc_migration: quanto menor, mais perto (ou abaixo) da migração
        cur.execute("""
            WITH token_min_ratio AS (
                SELECT m.mint, m.recovery,
                       MIN(ts.trigger_mc / NULLIF(m.mc_migration, 0)) AS min_ratio
                FROM migrations m
                JOIN token_signals ts ON ts.mint = m.mint
                WHERE m.recovery IS NOT NULL AND m.mc_migration > 0
                GROUP BY m.mint, m.recovery
            )
            SELECT
                CASE
                    WHEN min_ratio < 0.50 THEN '< 50% migração (deep bottom)'
                    WHEN min_ratio < 0.80 THEN '50-80% migração'
                    WHEN min_ratio < 1.00 THEN '80-100% migração'
                    WHEN min_ratio < 1.20 THEN '100-120% migração'
                    ELSE '> 120% migração (acima)'
                END AS faixa,
                MIN(min_ratio)                              AS ratio_min,
                COUNT(*)                                    AS n,
                ROUND(AVG(recovery::int) * 100, 1)          AS recovery_rate,
                COUNT(*) FILTER (WHERE recovery = true)     AS n_recovery
            FROM token_min_ratio
            GROUP BY 1 ORDER BY MIN(min_ratio)
        """)
        h9_threshold = cur.fetchall()

        # H9 baseline: tokens sem nenhum sinal
        cur.execute("""
            SELECT COUNT(*) AS n,
                   ROUND(AVG(recovery::int) * 100, 1) AS recovery_rate
            FROM migrations m
            WHERE m.recovery IS NOT NULL
              AND m.mc_migration > 0
              AND NOT EXISTS (SELECT 1 FROM token_signals ts WHERE ts.mint = m.mint)
        """)
        h9_baseline = dict(cur.fetchone())

        # Top tokens por quantidade de sinais
        cur.execute("""
            SELECT ts.mint, m.token_nome, COUNT(*) AS n_sinais,
                   MIN(ts.trigger_mc) AS min_trigger_mc,
                   MIN(ts.trigger_mc / NULLIF(m.mc_migration,0)) AS min_ratio,
                   m.mc_migration, m.recovery
            FROM token_signals ts
            LEFT JOIN migrations m ON m.mint = ts.mint
            GROUP BY ts.mint, m.token_nome, m.mc_migration, m.recovery
            ORDER BY n_sinais DESC LIMIT 20
        """)
        top_tokens = cur.fetchall()

    def _dt(v): return v.strftime("%Y-%m-%d %H:%M") if v else "—"

    # H9 threshold breakdown table
    base_rr  = float(h9_baseline["recovery_rate"] or 0)
    base_n   = h9_baseline["n"]
    thresh_rows = ""
    for r in h9_threshold:
        rr    = float(r["recovery_rate"] or 0)
        delta = rr - base_rr
        col   = "#4f4" if delta >= 10 else "#fa0" if delta >= 0 else "#f44"
        thresh_rows += (
            f"<tr><td>{r['faixa']}</td><td>{r['n']}</td>"
            f"<td>{rr}%</td>"
            f"<td style='color:{col}'>{delta:+.1f}pp</td>"
            f"<td>{r['n_recovery']}</td></tr>"
        )
    thresh_rows += (
        f"<tr style='background:#1a1a2a'><td><b>Sem sinal (baseline)</b></td>"
        f"<td>{base_n}</td><td>{base_rr}%</td><td>—</td><td>—</td></tr>"
    )

    h9_html = f"""
<div style="background:#1a2a1a;padding:16px;border-radius:6px;margin:16px 0">
<b>H9 — Recovery rate por proximidade do sinal ao fundo</b><br>
<small style="color:#888">trigger_ratio = trigger_mc / mc_migration. Quanto menor, mais perto (ou abaixo) da migração.</small><br><br>
<table border=1 cellpadding=6 style="border-collapse:collapse;font-size:13px">
<tr style="background:#222"><th>Faixa</th><th>n tokens</th><th>Recovery rate</th><th>Delta vs baseline</th><th>n recovery</th></tr>
{thresh_rows}
</table>
<br><small style="color:#888">Meta H9: delta ≥ 20pp. Faixa com sinal abaixo de 80% da migração = smart money comprando no dump.</small>
</div>"""

    dias_html = "".join(f"<tr><td>{r['dia']}</td><td>{r['n']}</td></tr>" for r in por_dia)
    top_html  = "".join(f"""<tr>
<td>{r['token_nome'] or r['mint'][:8]}</td>
<td>{r['n_sinais']}</td>
<td>${float(r['min_trigger_mc']):,.0f}</td>
<td>{'$' + f"{float(r['mc_migration']):,.0f}" if r['mc_migration'] else '—'}</td>
<td>{'%.2f×' % float(r['min_ratio']) if r['min_ratio'] else '—'}</td>
<td>{'✅' if r['recovery'] else '❌' if r['recovery'] is not None else '—'}</td>
</tr>""" for r in top_tokens)

    return f"""<!DOCTYPE html><html><body style="background:#111;color:#eee;font-family:monospace;padding:20px">
<h2>Análise H9 — Smart Money Type 12</h2>
<div style="display:flex;gap:20px;flex-wrap:wrap;margin:16px 0">
  <div style="background:#222;padding:16px;border-radius:6px">
    <div style="color:#888;font-size:12px">Total sinais</div>
    <div style="font-size:28px;font-weight:bold">{summ['total_sinais']}</div>
  </div>
  <div style="background:#222;padding:16px;border-radius:6px">
    <div style="color:#888;font-size:12px">Tokens únicos</div>
    <div style="font-size:28px;font-weight:bold">{summ['tokens_unicos']}</div>
  </div>
  <div style="background:#222;padding:16px;border-radius:6px">
    <div style="color:#888;font-size:12px">Primeiro sinal</div>
    <div style="font-size:16px">{_dt(summ['primeiro'])}</div>
  </div>
  <div style="background:#222;padding:16px;border-radius:6px">
    <div style="color:#888;font-size:12px">Último sinal</div>
    <div style="font-size:16px">{_dt(summ['ultimo'])}</div>
  </div>
</div>
{h9_html}
<h3>Sinais por dia</h3>
<table border=1 cellpadding=6 style="border-collapse:collapse;font-size:13px">
<tr style="background:#222"><th>Dia</th><th>Sinais</th></tr>{dias_html}</table>
<h3>Top 20 tokens com mais sinais</h3>
<table border=1 cellpadding=6 style="border-collapse:collapse;font-size:13px">
<tr style="background:#222"><th>Token</th><th>Sinais</th><th>Min trigger MC</th><th>MC migração</th><th>Min ratio</th><th>Recovery</th></tr>
{top_html}</table>
<p style="color:#888;font-size:12px">Min ratio = trigger_mc mais baixo / mc_migration. &lt;1.0 = sinal abaixo da migração (fundo real). &gt;1.0 = sinal acima da migração (momentum).</p>
</body></html>"""


@app.route("/debug/query-order")
def debug_query_order():
    """
    Mostra o response raw do query_order para um order_id real.
    GET /debug/query-order?order_id=<id>
    Ou sem parâmetro → pega o order_id do trade mais recente com status=open/tp/sl.
    """
    import json, traceback
    try:
        order_id = request.args.get("order_id")
        if not order_id:
            with db._cursor() as cur:
                cur.execute("""
                    SELECT order_id, id, symbol, status FROM trades
                    WHERE order_id IS NOT NULL
                    ORDER BY id DESC LIMIT 1
                """)
                row = cur.fetchone()
            if not row:
                return "<pre>Sem trades com order_id no banco.</pre>"
            order_id = row["order_id"]
            trade_info = f"Trade #{row['id']} {row['symbol']} (status={row['status']})"
        else:
            trade_info = f"order_id={order_id}"

        raw = gmgn.query_order(order_id)
        return (f"<html><body style='background:#111;color:#eee;font-family:monospace;padding:20px'>"
                f"<h3>query_order — {trade_info}</h3>"
                f"<pre>{json.dumps(raw, indent=2, default=str)}</pre>"
                f"</body></html>")
    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


@app.route("/debug/test-swap")
def debug_test_swap():
    """
    Executa um swap real de teste com TP/SL embutidos.
    GET /debug/test-swap?mint=<mint>&amount=<usd>
    Padrão: amount=$1. Requer TRADING_ENABLED=true.
    """
    from gmgn_client import get_sol_usd
    import traceback

    mint   = request.args.get("mint", "").strip()
    amount = float(request.args.get("amount", "1.0"))

    lines = []

    if not mint:
        return "<pre>❌ Parâmetro mint obrigatório.\nEx: /debug/test-swap?mint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</pre>", 400

    trading_enabled = os.getenv("TRADING_ENABLED", "false").lower() == "true"
    lines.append(f"TRADING_ENABLED : {'✅ true' if trading_enabled else '❌ false (swap não será executado)'}")
    lines.append(f"Mint            : {mint}")
    lines.append(f"Amount          : ${amount:.2f}")

    sol_usd = get_sol_usd()
    if not sol_usd or sol_usd <= 0:
        return "<pre>❌ Sem preço SOL/USD — tente novamente.</pre>", 500

    amount_sol = amount / sol_usd
    tp_pct     = float(os.getenv("TRADE_TP_PCT",       "0.75"))
    sl_pct     = float(os.getenv("TRADE_SL_PCT",       "0.15"))
    slippage   = int(os.getenv("TRADE_SLIPPAGE_BPS",   "500"))

    lines.append(f"SOL/USD         : ${sol_usd:,.2f}")
    lines.append(f"Amount SOL      : {amount_sol:.6f} SOL")
    lines.append(f"TP              : +{tp_pct*100:.0f}%  →  price_scale = '{round(tp_pct*100,2)}'")
    lines.append(f"SL              : -{sl_pct*100:.0f}%  →  price_scale = '{round(sl_pct*100,2)}'")
    lines.append(f"sell_ratio      : '100' (100% da posição)")
    lines.append(f"Slippage        : {slippage} bps ({slippage/100:.1f}%)")
    lines.append("")

    if not trading_enabled:
        lines.append("⚠️  TRADING_ENABLED=false — swap simulado apenas. Ative para executar real.")
        lines.append("")
        lines.append("Body que seria enviado:")
        lines.append(f"  input_amount    : {int(round(amount_sol * 1_000_000_000))} lamports")
        lines.append(f"  condition_orders: profit_stop price_scale={round(tp_pct*100,2)} sell_ratio=100")
        lines.append(f"                    loss_stop  price_scale={round(sl_pct*100,2)} sell_ratio=100")
        return "<pre>" + "\n".join(lines) + "</pre>"

    try:
        lines.append("Enviando swap...")
        res      = gmgn.swap(
            token_mint=mint,
            amount_sol=amount_sol,
            slippage_bps=slippage,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
        )
        data     = res.get("data") or res
        order_id = data.get("order_id") or data.get("id")
        lines.append(f"✅ Swap enviado!")
        lines.append(f"order_id        : {order_id}")
        lines.append(f"Resposta raw    : {res}")
        lines.append("")
        lines.append("TP e SL estão ativos na GMGN automaticamente.")
        lines.append(f"Verificar em: /debug/sign-test ou query_order/{order_id}")
    except Exception:
        lines.append("❌ Swap falhou:")
        lines.append(traceback.format_exc())

    return "<pre>" + "\n".join(lines) + "</pre>"


@app.route("/debug/backfill-pnl")
def debug_backfill_pnl():
    """
    Recalcula pnl_pct_real para trades tp/sl sem P&L registrado.
    Usa amount_usd, fee_sol e tp_pct/sl_pct do próprio trade.
    GET /debug/backfill-pnl?dry=1 para simular sem salvar.
    """
    dry = request.args.get("dry", "0") not in ("0", "false", "")
    lines = [f"{'[DRY RUN] ' if dry else ''}Backfill P&L de trades tp/sl sem pnl_pct_real", ""]
    updated = 0
    try:
        with db._cursor() as cur:
            cur.execute("""
                SELECT id, symbol, status, tp_pct, sl_pct, amount_usd, amount_sol, fee_sol, pnl_pct_real
                FROM trades
                WHERE status IN ('tp', 'sl')
                  AND pnl_pct_real IS NULL
                ORDER BY created_at DESC
            """)
            rows = [dict(r) for r in cur.fetchall()]

        for r in rows:
            tp_pct    = r["tp_pct"]  or 0.75
            sl_pct    = r["sl_pct"]  or 0.15
            amount_sol = r["amount_sol"] or 0.0
            fee_sol   = r["fee_sol"] or 0.0
            fee_frac  = fee_sol / amount_sol if amount_sol > 0 else 0.0
            pnl_target = tp_pct if r["status"] == "tp" else -sl_pct
            pnl_real   = pnl_target - fee_frac
            exit_usd   = (r["amount_usd"] or 1.0) * (1 + pnl_target)

            lines.append(f"Trade #{r['id']} {r['symbol']} [{r['status'].upper()}]"
                         f" pnl_bruto={pnl_target*100:.1f}%"
                         f" fee={fee_frac*100:.3f}%"
                         f" pnl_real={pnl_real*100:.1f}%"
                         f" exit_usd=${exit_usd:.4f}")
            if not dry:
                db.update_trade(r["id"], {
                    "pnl_pct_real":   pnl_real,
                    "exit_price_usd": exit_usd,
                })
                updated += 1

        lines.append("")
        lines.append(f"{'Simulado' if dry else 'Atualizado'}: {len(rows)} trades | saved={updated}")
    except Exception:
        import traceback
        lines.append(traceback.format_exc())

    return "<pre>" + "\n".join(lines) + "</pre>"


@app.route("/analise/slope-coverage")
def analise_slope_coverage():
    """
    Verifica coverage do slope filter (A.1) antes e após Fase B.
    Slope usa ticks dos 2min ANTES do ts_min (fundo) do bounce signal.
    Coverage = bounce tem >= 3 ticks nessa janela.
    """
    import traceback
    try:
        return _slope_coverage_inner()
    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


def _slope_coverage_inner():
    FASE_B_DATE     = "2026-05-04"
    MIN_TICKS       = int(request.args.get("min_ticks", 3))
    WINDOW_SEC      = int(request.args.get("window_sec", 120))
    SLOPE_THRESHOLD = -0.0008

    # Passo 1: buscar todos os bounces com mc_min
    with db._cursor() as cur:
        cur.execute(f"""
            SELECT id, mint, symbol, ts_signal, mc_min,
                   ts_signal >= '{FASE_B_DATE}'::date AS pos_fase_b
            FROM bounce_signals
            ORDER BY ts_signal DESC
        """)
        bounces = [dict(r) for r in cur.fetchall()]

    if not bounces:
        return "<pre>Sem bounce signals no banco.</pre>"

    # Passo 2: para cada bounce, estimar ts_min = tick com menor mc antes de ts_signal
    # A.1 no código usa ts_min (fundo real), não ts_signal (detecção pós-bounce)
    # Usar ts_signal diretamente inclui a recuperação → slope positivo → análise errada
    def get_ts_min_estimated(mint, ts_signal, mc_min_ref):
        try:
            with db._cursor() as cur:
                cur.execute("""
                    SELECT tick_ts FROM token_ticks
                    WHERE mint = %(mint)s AND tick_ts < %(ts)s AND mc IS NOT NULL
                    ORDER BY mc ASC LIMIT 1
                """, {"mint": mint, "ts": ts_signal})
                row = cur.fetchone()
                return row["tick_ts"] if row else None
        except Exception:
            return None

    # Passo 3: calcular slope nos WINDOW_SEC antes do ts_min_estimated
    def calc_slope_from_ts_min(mint, ts_min):
        try:
            ticks = db.get_recent_ticks(mint, window_sec=WINDOW_SEC, max_ts=ts_min)
            if len(ticks) < MIN_TICKS:
                return None, len(ticks)
            t0, mc0 = ticks[0]
            t1, mc1 = ticks[-1]
            dt = (t1 - t0).total_seconds()
            if dt <= 0 or mc0 <= 0:
                return None, len(ticks)
            return ((mc1 - mc0) / mc0) / dt, len(ticks)
        except Exception:
            return None, 0

    rows = []
    for b in bounces:
        ts_min_est = get_ts_min_estimated(b["mint"], b["ts_signal"], b.get("mc_min"))
        if ts_min_est is None:
            slope, n_ticks = None, 0
        else:
            slope, n_ticks = calc_slope_from_ts_min(b["mint"], ts_min_est)

        tem_slope = n_ticks >= MIN_TICKS
        slope_pass = (slope is not None and slope <= SLOPE_THRESHOLD)
        rows.append({**b, "ts_min_est": ts_min_est, "n_ticks": n_ticks,
                     "tem_slope": tem_slope, "slope": slope, "slope_pass": slope_pass})

    # Resumo por período
    def summary(subset):
        n = len(subset)
        if n == 0:
            return {}
        n_cov     = sum(1 for r in subset if r["tem_slope"])
        n_pass    = sum(1 for r in subset if r["slope_pass"])
        n_fail    = sum(1 for r in subset if r["tem_slope"] and not r["slope_pass"])
        n_no_data = sum(1 for r in subset if not r["tem_slope"])
        return dict(n=n, n_cov=n_cov, n_pass=n_pass, n_fail=n_fail, n_no_data=n_no_data,
                    cov_rate=n_cov/n, pass_rate=n_pass/n_cov if n_cov else 0)

    pre_b  = [r for r in rows if not r["pos_fase_b"]]
    pos_b  = [r for r in rows if r["pos_fase_b"]]
    s_pre  = summary(pre_b)
    s_pos  = summary(pos_b)
    s_all  = summary(rows)

    def pct(v):
        if v is None: return "—"
        c = "#4f4" if v >= 0.50 else ("#fa0" if v >= 0.25 else "#f44")
        return f'<span style="color:{c}">{v*100:.1f}%</span>'

    def slope_fmt(v):
        if v is None: return '<span style="color:#666">sem dados</span>'
        c = "#4f4" if v <= SLOPE_THRESHOLD else "#f44"
        label = "✅ íngreme" if v <= SLOPE_THRESHOLD else "❌ gradual"
        return f'<span style="color:{c}">{v:.6f} {label}</span>'

    sum_rows = ""
    for label, s in [("Antes Fase B", s_pre), ("Após Fase B", s_pos), ("Total", s_all)]:
        if not s:
            continue
        sum_rows += (f"<tr><td><b>{label}</b></td>"
                     f"<td>{s['n']}</td>"
                     f"<td>{s['n_cov']} ({pct(s['cov_rate'])})</td>"
                     f"<td>{s['n_no_data']}</td>"
                     f"<td>{s['n_pass']} ({pct(s['pass_rate'])} dos cobertos)</td>"
                     f"<td>{s['n_fail']}</td></tr>")

    detail_rows = ""
    for r in pos_b[:50]:
        ts_sig = r["ts_signal"].strftime("%m-%d %H:%M") if r["ts_signal"] else "?"
        ts_min_s = r["ts_min_est"].strftime("%H:%M:%S") if r["ts_min_est"] else "—"
        detail_rows += (f"<tr>"
                        f"<td>{ts_sig}</td>"
                        f"<td>{r['symbol'] or r['mint'][:8]}</td>"
                        f"<td>{ts_min_s}</td>"
                        f"<td>{r['n_ticks']}</td>"
                        f"<td>{'✅' if r['tem_slope'] else '❌'}</td>"
                        f"<td>{slope_fmt(r['slope'])}</td>"
                        f"</tr>")

    return f"""<html><head><title>Slope Coverage</title></head>
<body style="font-family:monospace;background:#111;color:#eee;padding:20px">
<h2>Coverage do slope filter (A.1) — antes vs após Fase B</h2>
<p style="color:#aaa">Janela: {WINDOW_SEC}s antes do <b>fundo estimado</b> (tick com menor MC) | Min ticks: {MIN_TICKS} | Threshold: {SLOPE_THRESHOLD}</p>
<p style="color:#fa0"><b>Meta Fase B: ≥ 50% de coverage</b></p>
<p style="color:#aaa;font-size:12px">Fix: slope calculado em relação ao ts_min (fundo real, tick de menor MC),
não ao ts_signal (detecção pós-bounce). ts_signal inclui a recuperação → slope positivo falso.</p>

<h3>Resumo por período</h3>
<table border=1 cellpadding=6 style="border-collapse:collapse;font-size:13px">
<tr style="background:#222">
  <th>Período</th><th>Bounces</th><th>Com cobertura (≥{MIN_TICKS} ticks)</th>
  <th>Sem dados</th><th>Slope íngreme (≤{SLOPE_THRESHOLD})</th><th>Slope gradual</th>
</tr>
{sum_rows}
</table>

<h3>Bounces pós-Fase B (mais recentes primeiro)</h3>
<table border=1 cellpadding=6 style="border-collapse:collapse;font-size:13px">
<tr style="background:#222">
  <th>Data/hora</th><th>Token</th><th>ts_min estimado</th>
  <th>Ticks pré-fundo</th><th>Slope calc?</th><th>Slope valor</th>
</tr>
{detail_rows}
</table>

<p style="color:#888;font-size:12px;margin-top:12px">
  Coverage = bounce tem ≥{MIN_TICKS} ticks nos {WINDOW_SEC}s antes do ts_min_estimado.<br>
  Slope íngreme (≤{SLOPE_THRESHOLD}) → WR 66.7% (n=17). Gradual → WR 0%.<br>
  Fase B deployada em {FASE_B_DATE}: fix ts_min, kline 5min, backfill retroativo, hot-precise 30s.
</p>
</body></html>"""


@app.route("/analise/h13-reanalysis")
def analise_h13_reanalysis():
    """
    Re-análise H13 com metodologia correta (ts_min real via tick menor MC).
    Original H13: n=17, WR 66.7% — cobertura 20.5%, seleção enviesada.
    Agora com ~89% de cobertura: slope ≤-0.0008 passa 92% dos bounces → não discrimina.
    Objetivo: encontrar threshold de slope que discrimine WR, se existir.
    """
    import traceback
    try:
        return _h13_reanalysis_inner()
    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


def _h13_reanalysis_inner():
    WINDOW_SEC  = int(request.args.get("window_sec", 120))
    MIN_TICKS   = 3

    # Buscar todos os bounces + recovery da tabela migrations (outcome real, n=153)
    # NÃO usar trades (n=20 live trades) — seleção enviesada + amostra pequena
    with db._cursor() as cur:
        cur.execute("""
            SELECT b.mint, b.symbol, b.ts_signal, b.mc_min, b.mc_migration,
                   b.drop_from_mig, b.bounce_pct,
                   m.recovery, m.time_to_bottom, m.pct_drop
            FROM bounce_signals b
            LEFT JOIN migrations m ON m.mint = b.mint
            ORDER BY b.ts_signal
        """)
        bounces = [dict(r) for r in cur.fetchall()]

    if not bounces:
        return "<pre>Sem bounces.</pre>"

    # Para cada bounce, estimar ts_min = tick com menor MC antes de ts_signal
    # Depois calcular slope nos WINDOW_SEC antes desse ponto (igual ao A.1 no código)
    def get_slope(mint, ts_signal):
        try:
            with db._cursor() as cur:
                cur.execute("""
                    SELECT tick_ts FROM token_ticks
                    WHERE mint = %(mint)s AND tick_ts < %(ts)s AND mc IS NOT NULL
                    ORDER BY mc ASC LIMIT 1
                """, {"mint": mint, "ts": ts_signal})
                row = cur.fetchone()
                if not row:
                    return None
                ts_min = row["tick_ts"]
            ticks = db.get_recent_ticks(mint, window_sec=WINDOW_SEC, max_ts=ts_min)
            if len(ticks) < MIN_TICKS:
                return None
            t0, mc0 = ticks[0]
            t1, mc1 = ticks[-1]
            dt = (t1 - t0).total_seconds()
            if dt <= 0 or mc0 <= 0:
                return None
            return ((mc1 - mc0) / mc0) / dt
        except Exception:
            return None

    rows = []
    for b in bounces:
        slope = get_slope(b["mint"], b["ts_signal"])
        rows.append({**b, "slope": slope})

    with_slope = [r for r in rows if r["slope"] is not None]
    # Apenas bounces com recovery definido (não None) para análise de WR
    with_outcome = [r for r in with_slope if r["recovery"] is not None]

    # Faixas de slope para análise
    thresholds = [
        ("Gradual (> -0.0008)",     lambda s: s > -0.0008),
        ("Leve (-0.0008 a -0.002)", lambda s: -0.002 < s <= -0.0008),
        ("Médio (-0.002 a -0.004)", lambda s: -0.004 < s <= -0.002),
        ("Íngreme (< -0.004)",      lambda s: s <= -0.004),
        # Thresholds mais agressivos para testar
        ("Ultra-íngreme (< -0.006)",lambda s: s <= -0.006),
    ]

    # WR = recovery rate por faixa (outcome = migrations.recovery)
    faixa_rows = ""
    for label, fn in thresholds:
        subset = [r for r in with_outcome if fn(r["slope"])]
        n = len(subset)
        n_rec = sum(1 for r in subset if r["recovery"])
        wr = n_rec / n if n > 0 else None
        # Baseline recovery rate (todos com outcome)
        base = sum(1 for r in with_outcome if r["recovery"]) / len(with_outcome) if with_outcome else 0
        delta = (wr - base) if wr is not None else None
        wr_str = f"{wr*100:.1f}%" if wr is not None else "—"
        delta_str = f"{delta*100:+.1f}pp" if delta is not None else "—"
        c = "#4f4" if (delta and delta > 0.05) else ("#f44" if (delta and delta < -0.05) else ("#fa0" if n > 0 else "#666"))
        faixa_rows += (f"<tr><td>{label}</td><td>{n}</td><td>{n_rec}</td>"
                       f"<td style='color:{c}'><b>{wr_str}</b></td>"
                       f"<td style='color:{c}'>{delta_str}</td></tr>")

    # Distribuição de slope
    ranges = [(-999, -0.006), (-0.006, -0.004), (-0.004, -0.002), (-0.002, -0.0008), (-0.0008, 0)]
    dist_rows = ""
    for lo, hi in ranges:
        subset_all = [r for r in with_slope if lo <= r["slope"] < hi]
        subset_out = [r for r in with_outcome if lo <= r["slope"] < hi]
        n_rec = sum(1 for r in subset_out if r["recovery"])
        wr = n_rec / len(subset_out) if subset_out else None
        wr_s = f"{wr*100:.1f}%" if wr is not None else "—"
        label = f"{lo:.4f} a {hi:.4f}"
        dist_rows += (f"<tr><td>{label}</td>"
                      f"<td>{len(subset_all)}</td>"
                      f"<td>{len(subset_all)/len(with_slope)*100:.1f}%</td>"
                      f"<td>{len(subset_out)}</td>"
                      f"<td>{wr_s}</td></tr>")

    # Estatísticas gerais
    slopes = [r["slope"] for r in with_slope]
    avg_slope = sum(slopes) / len(slopes) if slopes else 0

    rec_slopes  = [r["slope"] for r in with_outcome if r["recovery"]]
    nrec_slopes = [r["slope"] for r in with_outcome if not r["recovery"]]
    avg_rec  = sum(rec_slopes)/len(rec_slopes) if rec_slopes else None
    avg_nrec = sum(nrec_slopes)/len(nrec_slopes) if nrec_slopes else None
    base_rate = sum(1 for r in with_outcome if r["recovery"]) / len(with_outcome) if with_outcome else 0

    return f"""<html><head><title>H13 Re-análise</title></head>
<body style="font-family:monospace;background:#111;color:#eee;padding:20px">
<h2>H13 Re-análise — Slope pré-fundo vs Recovery Rate (metodologia correta)</h2>
<p style="color:#aaa">Janela: {WINDOW_SEC}s antes do ts_min estimado | Outcome: migrations.recovery (mc_1h &gt; mc_bottom × 1.3)</p>
<p style="color:#fa0">Original H13: n=17, WR 66.7% — seleção enviesada (20.5% cobertura, médido vs trades).
Agora: bounce_signals → migrations.recovery (n={len(with_outcome)} com outcome definido).</p>

<h3>Médias de slope por outcome (n_outcome={len(with_outcome)}, baseline recovery={base_rate*100:.1f}%)</h3>
<table border=1 cellpadding=6 style="border-collapse:collapse;font-size:13px">
<tr><td>Média slope — Recovery TRUE (n={len(rec_slopes)})</td><td style="color:#4f4">{f'{avg_rec:.6f}' if avg_rec else '—'}</td></tr>
<tr><td>Média slope — Recovery FALSE (n={len(nrec_slopes)})</td><td style="color:#f44">{f'{avg_nrec:.6f}' if avg_nrec else '—'}</td></tr>
<tr><td>Média slope geral (n={len(with_slope)})</td><td>{avg_slope:.6f}</td></tr>
</table>
<p style="color:#aaa;font-size:12px">Se médias forem similares → slope não discrimina recovery.</p>

<h3>Recovery rate por faixa de slope</h3>
<p style="color:#aaa;font-size:12px">Baseline recovery = {base_rate*100:.1f}%. Delta = diferença vs baseline.</p>
<table border=1 cellpadding=6 style="border-collapse:collapse;font-size:13px">
<tr style="background:#222"><th>Faixa slope</th><th>N bounces</th><th>Recovery</th><th>Rate</th><th>Delta vs baseline</th></tr>
{faixa_rows}
</table>

<h3>Distribuição de slope + recovery rate por faixa</h3>
<table border=1 cellpadding=6 style="border-collapse:collapse;font-size:13px">
<tr style="background:#222"><th>Faixa slope</th><th>N (todos)</th><th>% do total</th><th>N com outcome</th><th>Recovery rate</th></tr>
{dist_rows}
</table>

<p style="color:#888;font-size:12px;margin-top:12px">
  H13 original: slope ≤-0.0008 → WR 66.7% (n=17). Comparava vs trades — seleção enviesada.<br>
  Esta análise compara vs migrations.recovery — outcome real para todos os bounces com slope calculado.<br>
  Baseline recovery: {base_rate*100:.1f}% (recovery rate dos bounces com slope + outcome definido).
</p>
</body></html>"""


@app.route("/analise/dump-entry")
def analise_dump_entry():
    """
    Exploração: se comprássemos durante o dump (limit buy em X% do mc_migration),
    qual seria o WR e EV em cada nível?

    Deixa os dados dizerem onde está o edge — sem presumir nada.
    Parâmetros: tp=0.75  sl=0.15  trade_usd=10  tip_sol=0.001  prio_sol=0.001
                gmgn_pct=0.01  slip=0.025  sol_usd=auto
    """
    import traceback
    try:
        return _dump_entry_inner()
    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


def _dump_entry_inner():
    from gmgn_client import get_sol_usd as _get_sol_usd

    TP_PCT    = float(request.args.get("tp",        0.75))
    SL_PCT    = float(request.args.get("sl",        0.15))
    TRADE_USD = float(request.args.get("trade_usd", 10.0))
    TIP_SOL   = float(request.args.get("tip_sol",   0.001))
    PRIO_SOL  = float(request.args.get("prio_sol",  0.001))
    GMGN_PCT  = float(request.args.get("gmgn_pct",  0.01))
    SLIP_PCT  = float(request.args.get("slip",       0.025))
    sol_usd   = float(request.args.get("sol_usd",   0)) or _get_sol_usd() or 150.0

    # Friction (igual à persistence-sim)
    fixed_usd   = (TIP_SOL + PRIO_SOL) * 2 * sol_usd
    buy_cost    = fixed_usd / 2 + TRADE_USD * GMGN_PCT + TRADE_USD * SLIP_PCT
    tp_sell_cost = fixed_usd / 2 + TRADE_USD * (1 + TP_PCT) * GMGN_PCT
    sl_sell_cost = fixed_usd / 2 + TRADE_USD * (1 - SL_PCT) * GMGN_PCT
    tp_net_pnl  = TRADE_USD * TP_PCT - buy_cost - tp_sell_cost
    sl_net_pnl  = -TRADE_USD * SL_PCT - buy_cost - sl_sell_cost
    tp_net_pct  = tp_net_pnl / TRADE_USD
    sl_net_pct  = sl_net_pnl / TRADE_USD
    breakeven_wr = (-sl_net_pct) / (tp_net_pct - sl_net_pct)

    # Dados brutos
    with db._cursor() as cur:
        cur.execute("""
            SELECT mc_migration, mc_bottom, mc_peak, recovery,
                   time_to_bottom, mc_1h
            FROM migrations
            WHERE mc_migration > 0
              AND mc_bottom    > 0
              AND mc_peak      > 0
              AND mc_bottom    < mc_migration
        """)
        tokens = [dict(r) for r in cur.fetchall()]

    n_total = len(tokens)

    # Níveis de entrada a explorar: 10% a 90% do mc_migration
    levels = [round(x * 0.05, 2) for x in range(2, 20)]  # 0.10 a 0.95 step 0.05

    rows_data = []
    for T in levels:
        eligible = [
            t for t in tokens
            if t["mc_bottom"] <= t["mc_migration"] * T
        ]
        n = len(eligible)
        if n == 0:
            rows_data.append({"T": T, "n": 0})
            continue

        # Para cada token elegível: entry = mc_migration * T
        # TP price = entry * (1 + TP_PCT)
        # SL price = entry * (1 - SL_PCT)
        # Após a compra, o token pode:
        #   - continuar caindo até mc_bottom < SL price → SL hit
        #   - subir até mc_peak > TP price             → TP potencial
        # Se ambos cruzam → não sabemos a ordem sem ticks (mostrado separado)

        n_tp_only  = 0  # só TP cruzado (clean win)
        n_sl_only  = 0  # só SL cruzado (clean loss)
        n_both     = 0  # ambos cruzados (ambíguo — sem ticks para saber a ordem)
        n_neither  = 0  # nenhum cruzado (saiu por tempo)

        for t in eligible:
            entry  = t["mc_migration"] * T
            tp_lvl = entry * (1 + TP_PCT)
            sl_lvl = entry * (1 - SL_PCT)
            hit_tp = t["mc_peak"]   >= tp_lvl
            hit_sl = t["mc_bottom"] <  sl_lvl

            if   hit_tp and not hit_sl: n_tp_only += 1
            elif hit_sl and not hit_tp: n_sl_only += 1
            elif hit_tp and hit_sl:     n_both    += 1
            else:                       n_neither += 1

        # WR conservador: n_both → todos SL (pior caso)
        wr_bear  = n_tp_only / n
        # WR otimista: n_both → todos TP
        wr_bull  = (n_tp_only + n_both) / n
        # WR central: n_both → 50/50
        wr_mid   = (n_tp_only + n_both * 0.5) / n

        ev_bear = wr_bear * tp_net_pct + (1 - wr_bear) * sl_net_pct
        ev_mid  = wr_mid  * tp_net_pct + (1 - wr_mid)  * sl_net_pct
        ev_bull = wr_bull * tp_net_pct + (1 - wr_bull) * sl_net_pct

        rows_data.append({
            "T": T, "n": n, "pct_of_total": n / n_total,
            "n_tp_only": n_tp_only, "n_sl_only": n_sl_only,
            "n_both": n_both, "n_neither": n_neither,
            "wr_bear": wr_bear, "wr_mid": wr_mid, "wr_bull": wr_bull,
            "ev_bear": ev_bear, "ev_mid": ev_mid, "ev_bull": ev_bull,
        })

    # HTML
    def pct(v, dec=1):
        if v is None: return "—"
        c = "#4f4" if v >= 0 else "#f44"
        return f'<span style="color:{c}">{v*100:+.{dec}f}%</span>'

    def wr_cell(v):
        be = breakeven_wr
        c = "#4f4" if v >= be else "#f66"
        return f'<span style="color:{c}">{v*100:.1f}%</span>'

    header = ("<tr style='background:#222'>"
              "<th>Nível entrada<br>(% mc_mig)</th>"
              "<th>Tokens<br>elegíveis</th>"
              "<th>% do total<br>que chega</th>"
              "<th>✅ TP limpo</th>"
              "<th>❌ SL limpo</th>"
              "<th>⚠️ Ambos<br>(ordem ?)</th>"
              "<th>⏱ Nenhum</th>"
              "<th>WR mín<br>(bear)</th>"
              "<th>WR central<br>(50/50)</th>"
              "<th>WR máx<br>(bull)</th>"
              "<th>EV central<br>/trade</th>"
              "</tr>")

    body = ""
    for r in rows_data:
        if r["n"] == 0:
            body += f"<tr><td>{r['T']*100:.0f}%</td><td colspan=10 style='color:#666'>nenhum token chegou aqui</td></tr>"
            continue
        ev_color = "#4f4" if r["ev_mid"] >= 0 else "#f44"
        bg = "background:#1a2a1a" if r["ev_mid"] >= 0 else ""
        body += (f"<tr style='{bg}'>"
                 f"<td><b>{r['T']*100:.0f}%</b></td>"
                 f"<td>{r['n']}</td>"
                 f"<td>{r['pct_of_total']*100:.1f}%</td>"
                 f"<td>{r['n_tp_only']} ({r['n_tp_only']/r['n']*100:.0f}%)</td>"
                 f"<td>{r['n_sl_only']} ({r['n_sl_only']/r['n']*100:.0f}%)</td>"
                 f"<td>{r['n_both']} ({r['n_both']/r['n']*100:.0f}%)</td>"
                 f"<td>{r['n_neither']} ({r['n_neither']/r['n']*100:.0f}%)</td>"
                 f"<td>{wr_cell(r['wr_bear'])}</td>"
                 f"<td>{wr_cell(r['wr_mid'])}</td>"
                 f"<td>{wr_cell(r['wr_bull'])}</td>"
                 f"<td><span style='color:{ev_color}'>{r['ev_mid']*100:+.1f}%</span></td>"
                 f"</tr>")

    return f"""<html><head><title>Dump Entry Analysis</title></head>
<body style="font-family:monospace;background:#111;color:#eee;padding:20px">
<h2>Exploração: comprar durante o dump — onde está o edge?</h2>
<p style="color:#aaa">
  {n_total} tokens com mc_bottom &lt; mc_migration |
  TP: <b>+{TP_PCT*100:.0f}%</b> | SL: <b>-{SL_PCT*100:.0f}%</b> |
  Trade: <b>${TRADE_USD:.0f}</b> | SOL: <b>${sol_usd:.0f}</b>
</p>
<p style="color:#fa0">
  Break-even WR: <b>{breakeven_wr*100:.1f}%</b> |
  P&L líquido TP: <b>{tp_net_pct*100:+.1f}%</b> |
  P&L líquido SL: <b>{sl_net_pct*100:+.1f}%</b>
</p>

<table border=1 cellpadding=6 style="border-collapse:collapse;font-size:13px">
{header}{body}
</table>

<p style="color:#888;font-size:12px;margin-top:16px">
<b>Nível entrada</b>: limit buy quando mc cai para X% do mc_migration (ex: 30% = dump de -70%).<br>
<b>Tokens elegíveis</b>: aqueles onde mc_bottom ≤ nível de entrada (a ordem teria sido preenchida).<br>
<b>TP limpo</b>: mc_peak ≥ TP price e mc_bottom ≥ SL price — TP definitivamente antes do SL.<br>
<b>SL limpo</b>: mc_peak &lt; TP price e mc_bottom &lt; SL price — SL definitivamente.<br>
<b>Ambos</b>: mc_peak ≥ TP e mc_bottom &lt; SL — ambos os níveis foram tocados, ordem desconhecida sem ticks.<br>
<b>WR mín/central/máx</b>: bear=todos 'ambos' como SL | central=50/50 | bull=todos 'ambos' como TP.<br>
<b>EV central</b>: WR_central × P&L_TP_líquido + (1-WR) × P&L_SL_líquido.<br>
Linhas <span style="color:#4f4">verdes</span>: EV central positivo. Linhas sem cor: EV negativo.
</p>
</body></html>"""


@app.route("/analise/persistence-sim")
def analise_persistence_sim():
    """
    Simula 0-tick vs 1-tick vs 3-tick de persistência com friction real.
    Parâmetros URL (todos opcionais):
      trade_usd=10      tamanho do trade em USD
      tp=0.75           take profit %
      sl=0.15           stop loss %
      tip_sol=0.001     tip fee por ordem (compra + venda)
      prio_sol=0.001    priority fee por ordem (compra + venda)
      gmgn_pct=0.01     fee GMGN sobre valor (compra + venda)
      slip=0.025        slippage médio na compra (estimativa)
      sol_usd=150       preço SOL/USD (padrão: cotação atual)
    """
    import traceback
    try:
        return _persistence_sim_inner()
    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


def _persistence_sim_inner():
    from gmgn_client import get_sol_usd as _get_sol_usd

    # — Parâmetros —
    TRADE_USD  = float(request.args.get("trade_usd", 10.0))
    TP_PCT     = float(request.args.get("tp",        0.75))
    SL_PCT     = float(request.args.get("sl",        0.15))
    TIP_SOL    = float(request.args.get("tip_sol",   0.001))
    PRIO_SOL   = float(request.args.get("prio_sol",  0.001))
    GMGN_PCT   = float(request.args.get("gmgn_pct",  0.01))
    SLIP_PCT   = float(request.args.get("slip",       0.025))
    sol_usd    = float(request.args.get("sol_usd",    0)) or _get_sol_usd() or 150.0

    # Friction por trade (buy + sell)
    fixed_sol_per_trade = (TIP_SOL + PRIO_SOL) * 2          # 0.004 SOL
    fixed_usd_per_trade = fixed_sol_per_trade * sol_usd      # ~$0.60
    gmgn_buy_usd        = TRADE_USD * GMGN_PCT               # 1% da compra
    slippage_usd        = TRADE_USD * SLIP_PCT               # entry slip
    total_buy_cost      = fixed_usd_per_trade / 2 + gmgn_buy_usd + slippage_usd

    # Resultado líquido para TP e SL (as fees de saída incidem sobre o valor de saída)
    tp_exit_gross  = TRADE_USD * (1 + TP_PCT)
    sl_exit_gross  = TRADE_USD * (1 - SL_PCT)
    tp_sell_cost   = fixed_usd_per_trade / 2 + tp_exit_gross * GMGN_PCT
    sl_sell_cost   = fixed_usd_per_trade / 2 + sl_exit_gross * GMGN_PCT

    tp_net_pnl     = tp_exit_gross - tp_sell_cost - TRADE_USD - total_buy_cost
    sl_net_pnl     = sl_exit_gross - sl_sell_cost - TRADE_USD - total_buy_cost
    tp_net_pct     = tp_net_pnl / TRADE_USD
    sl_net_pct     = sl_net_pnl / TRADE_USD

    # — Busca bounce signals com ticks —
    with db._cursor() as cur:
        cur.execute("""
            SELECT b.mint, b.symbol, b.ts_signal, b.mc_min, b.mc_at_signal, b.mc_migration,
                   b.bounce_pct, b.drop_from_mig
            FROM bounce_signals b
            WHERE b.mc_min IS NOT NULL AND b.ts_signal IS NOT NULL
            ORDER BY b.ts_signal ASC
        """)
        bounces = [dict(r) for r in cur.fetchall()]

    # — Cenários —
    scenarios = {
        "imediato":  {"min_ticks": 0, "results": []},
        "1_tick":    {"min_ticks": 1, "results": []},
        "3_ticks":   {"min_ticks": 3, "results": []},
    }

    no_ticks_count = 0

    for b in bounces:
        mint          = b["mint"]
        ts_signal     = b["ts_signal"]
        mc_min        = b["mc_min"]
        entry_expected = mc_min * 1.15 if mc_min else None
        if not entry_expected:
            continue

        ticks = db.get_ticks_after(mint, min_ts=ts_signal)
        if not ticks:
            no_ticks_count += 1
            continue

        for sc_name, sc in scenarios.items():
            min_ticks = sc["min_ticks"]

            # Encontrar tick de entrada
            if min_ticks == 0:
                # Entrada imediata: preço = mc_at_signal (o preço no momento do sinal)
                entry_mc = float(b["mc_at_signal"] or entry_expected)
                entry_tick_idx = 0
            else:
                # Esperar N ticks consecutivos acima do entry_expected
                consecutive = 0
                entry_mc = None
                entry_tick_idx = None
                for i, t in enumerate(ticks):
                    mc_val = float(t["mc"] or 0)
                    if mc_val >= entry_expected:
                        consecutive += 1
                        if consecutive >= min_ticks:
                            entry_mc    = mc_val
                            entry_tick_idx = i
                            break
                    else:
                        consecutive = 0

            if entry_mc is None or entry_mc <= 0:
                continue

            # Overshoot: quanto pagamos acima do entry_expected (price drift durante wait)
            overshoot_pct = (entry_mc / entry_expected - 1)

            # TP e SL calculados sobre o preço REAL de entrada (condition_orders)
            tp_mc = entry_mc * (1 + TP_PCT)
            sl_mc = entry_mc * (1 - SL_PCT)

            # Walk forward para encontrar exit
            post_ticks = ticks[entry_tick_idx:] if entry_tick_idx is not None else ticks
            exit_type = "timeout"
            for t in post_ticks:
                high = float(t["mc_high"] or t["mc"] or 0)
                low  = float(t["mc_low"]  or t["mc"] or 0)
                if high >= tp_mc:
                    exit_type = "tp"
                    break
                if low > 0 and low <= sl_mc:
                    exit_type = "sl"
                    break

            sc["results"].append({
                "mint":         mint,
                "symbol":       b.get("symbol", "?"),
                "exit":         exit_type,
                "overshoot":    overshoot_pct,
                "entry_mc":     entry_mc,
                "entry_expected": entry_expected,
            })

    # — Computar métricas por cenário —
    def calc_metrics(results):
        if not results:
            return {}
        n       = len(results)
        n_tp    = sum(1 for r in results if r["exit"] == "tp")
        n_sl    = sum(1 for r in results if r["exit"] == "sl")
        n_to    = sum(1 for r in results if r["exit"] == "timeout")
        wr      = n_tp / n if n else 0
        ev_pct  = wr * tp_net_pct + (1 - wr) * sl_net_pct
        total_pnl = n_tp * tp_net_pnl + (n_sl + n_to) * sl_net_pnl
        avg_overshoot = sum(r["overshoot"] for r in results) / n if n else 0
        return dict(n=n, n_tp=n_tp, n_sl=n_sl, n_to=n_to, wr=wr,
                    ev_pct=ev_pct, total_pnl=total_pnl, avg_overshoot=avg_overshoot)

    metrics = {k: calc_metrics(sc["results"]) for k, sc in scenarios.items()}
    total_bounces = len(bounces)

    # — HTML —
    CSS = "font-family:monospace;background:#111;color:#eee;padding:20px"

    def pct(v):
        if v is None: return "—"
        color = "#4f4" if v >= 0 else "#f44"
        return f'<span style="color:{color}">{v*100:+.1f}%</span>'

    def usd(v):
        if v is None: return "—"
        color = "#4f4" if v >= 0 else "#f44"
        return f'<span style="color:{color}">${v:+.2f}</span>'

    rows = ""
    for sc_name, m in metrics.items():
        label = {"imediato": "0 ticks (imediato)", "1_tick": "1 tick", "3_ticks": "3 ticks (atual A.4)"}.get(sc_name, sc_name)
        if not m:
            rows += f"<tr><td>{label}</td><td colspan=8>sem dados</td></tr>"
            continue
        rows += (f"<tr>"
                 f"<td><b>{label}</b></td>"
                 f"<td>{m['n']}</td>"
                 f"<td>{m['n_tp']} ({m['wr']*100:.1f}%)</td>"
                 f"<td>{m['n_sl']}</td>"
                 f"<td>{m['n_to']}</td>"
                 f"<td>{pct(m['ev_pct'])}</td>"
                 f"<td>{usd(m['total_pnl'])}</td>"
                 f"<td>{pct(m['avg_overshoot'])}</td>"
                 f"</tr>")

    h = f"""<html><head><title>Persistence Sim</title></head>
<body style="{CSS}">
<h2>Simulação: 0 vs 1 vs 3 ticks de persistência</h2>
<p style="color:#aaa">
  Trade: <b>${TRADE_USD:.0f}</b> |
  TP: <b>+{TP_PCT*100:.0f}%</b> |
  SL: <b>-{SL_PCT*100:.0f}%</b> |
  Tip: <b>{TIP_SOL} SOL/ordem</b> |
  Priority: <b>{PRIO_SOL} SOL/ordem</b> |
  GMGN fee: <b>{GMGN_PCT*100:.0f}%</b> |
  Slippage médio: <b>{SLIP_PCT*100:.1f}%</b> |
  SOL: <b>${sol_usd:.0f}</b>
</p>

<h3>Friction por trade</h3>
<table border=1 cellpadding=6 style="border-collapse:collapse;font-size:13px">
<tr style="background:#222"><th>Item</th><th>Compra</th><th>Venda (TP)</th><th>Venda (SL)</th></tr>
<tr><td>Tip + Priority</td><td>${fixed_usd_per_trade/2:.3f}</td><td>${fixed_usd_per_trade/2:.3f}</td><td>${fixed_usd_per_trade/2:.3f}</td></tr>
<tr><td>GMGN fee (1%)</td><td>${gmgn_buy_usd:.3f}</td><td>${tp_exit_gross*GMGN_PCT:.3f}</td><td>${sl_exit_gross*GMGN_PCT:.3f}</td></tr>
<tr><td>Slippage entry</td><td>${slippage_usd:.3f}</td><td>—</td><td>—</td></tr>
<tr style="background:#222"><td><b>Total</b></td><td><b>${total_buy_cost:.3f}</b></td><td><b>${tp_sell_cost:.3f}</b></td><td><b>${sl_sell_cost:.3f}</b></td></tr>
<tr style="background:#333"><td><b>P&L líquido</b></td><td></td><td>{pct(tp_net_pct)} (${tp_net_pnl:+.2f})</td><td>{pct(sl_net_pct)} (${sl_net_pnl:+.2f})</td></tr>
</table>

<h3>Resultado por cenário</h3>
<p style="color:#888">Total de bounce signals: {total_bounces} | Sem ticks: {no_ticks_count} | Com ticks: {total_bounces - no_ticks_count}</p>
<table border=1 cellpadding=6 style="border-collapse:collapse;font-size:13px">
<tr style="background:#222">
  <th>Cenário</th><th>Trades</th><th>TP (WR)</th><th>SL</th><th>Timeout</th>
  <th>EV/trade</th><th>P&L total</th><th>Overshoot médio</th>
</tr>
{rows}
</table>

<p style="color:#888;font-size:12px;margin-top:20px">
  <b>Overshoot</b>: quanto o preço de entrada subiu acima do entry_expected (mc_bottom×1.15) durante o wait.<br>
  <b>EV/trade</b>: WR × P&L_líquido_TP + (1-WR) × P&L_líquido_SL<br>
  <b>Timeout</b>: janela de ticks acabou sem TP ou SL — contado como SL no EV (pior caso).<br>
  <b>0 ticks</b>: entrada imediata ao sinal (preço = mc_at_signal).<br>
  <b>Slippage de {SLIP_PCT*100:.1f}%</b>: estimativa — use ?slip=0.02 para ajustar.
</p>
</body></html>"""
    return h


@app.route("/debug/trade-audit")
def debug_trade_audit():
    """
    Auditoria completa dos 48 trades de calibração.
    Mostra tudo que temos no banco para correlacionar com Solscan.
    """
    import traceback
    try:
        return _trade_audit_inner()
    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


def _trade_audit_inner():
    sol_usd = _get_sol_usd() or 88.0

    with db._cursor() as cur:
        cur.execute("""
            SELECT id, symbol, mint, ts_signal, ts_order_sent, ts_order_confirmed,
                   order_id, strategy_id,
                   amount_sol, amount_usd, tokens_received,
                   entry_price_sol, entry_price_usd,
                   fee_sol, slippage_pct, latency_sec,
                   tp_pct, sl_pct, status, exit_type, exit_ts,
                   pnl_pct_real, pnl_pct_expected,
                   mc_bottom, mc_migration, entry_expected,
                   error_msg, created_at
            FROM trades
            ORDER BY created_at ASC
        """)
        trades = [dict(r) for r in cur.fetchall()]

    # ---------- Agregados ----------
    total_trades   = len(trades)
    total_sol_in   = sum((t.get("amount_sol") or 0) for t in trades)
    total_fee_sol  = sum((t.get("fee_sol")    or 0) for t in trades)
    total_fee_usd  = total_fee_sol * sol_usd

    status_counts = {}
    for t in trades:
        s = t.get("status") or "unknown"
        status_counts[s] = status_counts.get(s, 0) + 1

    # Trades com fee_sol capturado vs não capturado
    with_fee    = sum(1 for t in trades if t.get("fee_sol") and t["fee_sol"] > 0)
    without_fee = total_trades - with_fee

    # Slippage médio
    slips = [t["slippage_pct"] for t in trades if t.get("slippage_pct") is not None]
    avg_slip = (sum(slips) / len(slips) * 100) if slips else 0

    def brt(dt):
        if not dt: return "—"
        return (dt + BRT).strftime("%m/%d %H:%M:%S")

    def sol_str(v, decimals=6):
        if v is None: return "—"
        return f"{v:.{decimals}f}"

    # ---------- Tabela de trades ----------
    rows = []
    for t in trades:
        status = t.get("status") or "?"
        color = {"tp": "#4CAF50", "sl": "#f44336", "trailing_active": "#FF9800",
                 "expired": "#9E9E9E", "error": "#FF5722", "open": "#2196F3",
                 "pending": "#607D8B"}.get(status, "#888")

        order_id = t.get("order_id") or "—"
        order_short = order_id[:16] + "…" if len(order_id) > 16 else order_id

        fee_sol_v  = t.get("fee_sol") or 0
        fee_usd_v  = fee_sol_v * sol_usd
        slip_v     = t.get("slippage_pct")
        slip_str   = f"{slip_v*100:+.2f}%" if slip_v is not None else "—"

        pnl = t.get("pnl_pct_real")
        pnl_str = f"{pnl*100:+.1f}%" if pnl is not None else "—"
        pnl_color = "#4CAF50" if pnl and pnl > 0 else "#f44336" if pnl and pnl < 0 else "#888"

        rows.append(f"""
        <tr>
          <td>{t['id']}</td>
          <td><b>{t.get('symbol','?')}</b></td>
          <td style="font-size:10px;color:#888">{brt(t.get('ts_signal'))}</td>
          <td style="font-size:10px">{sol_str(t.get('amount_sol'),5)} SOL</td>
          <td style="font-size:10px">{sol_str(t.get('entry_price_sol'),8)}</td>
          <td style="font-size:10px">{int(t['tokens_received']) if t.get('tokens_received') else '—'}</td>
          <td style="color:{'#f44336' if fee_sol_v==0 else '#ccc'};font-size:10px">
            {sol_str(fee_sol_v,6)} SOL<br>
            <span style="color:#888">${fee_usd_v:.4f}</span>
          </td>
          <td style="font-size:10px;color:#aaa">{slip_str}</td>
          <td><span style="color:{color}">{status}</span></td>
          <td style="color:{pnl_color}">{pnl_str}</td>
          <td style="font-size:9px;color:#666;word-break:break-all" title="{order_id}">{order_short}</td>
          <td style="font-size:9px;color:#555;word-break:break-all">{(t.get('mint') or '')[:16]}…</td>
        </tr>""")

    rows_html = "\n".join(rows)

    # ---------- Instruções Solscan ----------
    wallet_hint = os.getenv("GMGN_WALLET_ADDRESS", "")
    wallet_display = wallet_hint if wallet_hint else "— configure GMGN_WALLET_ADDRESS —"
    wallet_link = f"https://solscan.io/account/{wallet_hint}#transfers" if wallet_hint else "#"

    # Datas do range de calibração
    if trades:
        dt_start = min((t["created_at"] for t in trades if t.get("created_at")), default=None)
        dt_end   = max((t["created_at"] for t in trades if t.get("created_at")), default=None)
        range_str = f"{brt(dt_start)} → {brt(dt_end)}"
    else:
        range_str = "—"

    # Breakdown esperado vs capturado
    gmgn_fee_pct  = 0.01  # 1% por swap
    expected_gmgn = total_sol_in * gmgn_fee_pct * sol_usd  # buy side only (sell unknown)
    captured_str  = f"${total_fee_usd:.4f}" if total_fee_usd > 0 else "R$0 (não capturado)"

    h = f"""<!DOCTYPE html><html><head>
<meta charset="utf-8">
<title>Trade Audit — NeideBOT</title>
<style>
  body {{ background:#111; color:#ddd; font-family:monospace; padding:20px; }}
  h1,h2,h3 {{ color:#FFD700; }}
  table {{ border-collapse:collapse; width:100%; margin:16px 0; font-size:12px; }}
  th,td {{ border:1px solid #333; padding:6px 8px; text-align:left; vertical-align:top; }}
  th {{ background:#1e1e1e; color:#aaa; }}
  tr:hover {{ background:#1a1a1a; }}
  .box {{ background:#1a1a1a; border:1px solid #333; padding:16px; border-radius:6px; margin:16px 0; }}
  .warn {{ color:#FF9800; }}
  .ok {{ color:#4CAF50; }}
  .bad {{ color:#f44336; }}
  a {{ color:#4FC3F7; }}
  code {{ background:#222; padding:2px 6px; border-radius:3px; }}
</style>
</head><body>
<h1>Trade Audit — Calibração $1/trade</h1>
<p style="color:#888">Dados do banco + instruções para correlacionar com Solscan</p>

<div class="box">
  <h2>Resumo</h2>
  <table style="width:auto">
    <tr><th>Total trades</th><td>{total_trades}</td></tr>
    <tr><th>Range de datas (BRT)</th><td>{range_str}</td></tr>
    <tr><th>SOL total enviado</th><td>{sol_str(total_sol_in,5)} SOL (${total_sol_in*sol_usd:.2f})</td></tr>
    <tr><th>Status breakdown</th><td>{"  |  ".join(f"{k}: {v}" for k,v in status_counts.items())}</td></tr>
    <tr><th>fee_sol capturado no DB</th><td>{captured_str}
      <span class="warn"> ({with_fee}/{total_trades} trades com fee)</span></td></tr>
    <tr><th>Slippage médio capturado</th><td>{avg_slip:.2f}%</td></tr>
    <tr><th>GMGN 1% fee estimado (só buy)</th><td class="warn">${expected_gmgn:.2f}</td></tr>
  </table>
  <p class="warn"><b>Atenção:</b> o campo <code>fee_sol</code> do banco vem da resposta do GMGN
  ao poll do fill (<code>query_order</code>). Se o GMGN não retornou esse campo,
  fica 0 — <b>não significa que não foi cobrado</b>. A taxa real só aparece on-chain.</p>
</div>

<div class="box">
  <h2>Como investigar no Solscan</h2>
  <p><b>Wallet:</b> <a href="{wallet_link}" target="_blank"><code>{wallet_display}</code></a></p>
  <p>Abra o link acima e vá na aba <b>Transfers</b> ou <b>Transactions</b>.</p>
  <p><b>Range de tempo para filtrar:</b> {range_str}</p>

  <h3>O que procurar em cada transação</h3>
  <ol>
    <li><b>Swap de compra (SOL → TOKEN):</b>
      <ul>
        <li>Aparece como: saída de SOL + entrada de token na wallet</li>
        <li>Na aba "Transaction", você vê: <b>Transaction Fee</b> (base, ~0.000005 SOL) e <b>Priority Fee</b></li>
        <li>Jito tip: procure por uma pequena transferência SOL para um endereço terminando em <code>tip</code> ou para <a href="https://solscan.io/account/T1pyyaTNZsKv2WcRAB8oVnk93mLJw2XzjtVYqCsaHqt" target="_blank">T1pyy…</a> (conta tip Jito)</li>
        <li>GMGN fee: deduzido do swap internamente — aparece como diferença no output amount</li>
      </ul>
    </li>
    <li><b>Swap de venda (TOKEN → SOL) — executado pelo GMGN quando TP/SL bate:</b>
      <ul>
        <li>Aparece como: saída de token + entrada de SOL</li>
        <li><b>Este tx tem fee/tip pagos PELO GMGN, mas debitados da sua wallet</b></li>
        <li>Comparar: SOL recebido vs valor esperado pelo TP/SL</li>
      </ul>
    </li>
  </ol>

  <h3>Fórmula para calcular friction de cada trade</h3>
  <pre style="background:#1e1e1e;padding:12px;border-radius:4px">
Friction = (SOL_total_saiu_da_wallet) - (SOL_recebido_na_venda)
         = SOL_enviado_na_compra + fees_buy + tips_buy
           - SOL_recebido_na_venda + fees_sell + tips_sell

# Onde:
#   SOL_enviado_na_compra ≈ amount_sol abaixo (o capital real do trade)
#   fees_buy + tips_buy   = o que aparece além do amount_sol na tx de compra
#   fees_sell + tips_sell = aparece deduzido do retorno na tx de venda
  </pre>

  <h3>Endereços de tip Jito (para identificar na tx)</h3>
  <pre style="background:#1e1e1e;padding:12px;border-radius:4px">
T1pyyaTNZsKv2WcRAB8oVnk93mLJw2XzjtVYqCsaHqt
96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5
HFqU5x63VTqvB8ky8HFGjWGcuGkEbT9fvkdnxwxGaHi
Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY
ADaUMid9sspnBwsm7iHSTe4q3YFe4EtUBfyvYTyMSXgo
DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh
ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt
DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL
96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5
  </pre>
</div>

<h2>Todos os {total_trades} trades — dados do banco</h2>
<table>
  <tr>
    <th>#</th><th>Token</th><th>Timestamp (BRT)</th>
    <th>SOL enviado</th><th>Preço entry</th><th>Tokens rec.</th>
    <th>Fee (DB)</th><th>Slippage</th>
    <th>Status</th><th>P&L</th>
    <th>Order ID</th><th>Mint</th>
  </tr>
  {rows_html}
</table>

<div class="box">
  <h2>O que o banco NÃO captura (lacunas na auditoria)</h2>
  <ul>
    <li><b>Fee da venda (condition_orders):</b> quando o GMGN executa o TP/SL, ele cria uma transação de venda separada usando credenciais da sua wallet. O fee dessa tx <b>não volta para o bot</b> — só aparece on-chain.</li>
    <li><b>Jito tip real:</b> a GMGN pode ignorar os valores de <code>priority_fee="0.0001"</code> que mandamos e usar valores próprios (mínimo recomendado deles: 0.002-0.006 SOL para condition_orders). Essa é a hipótese principal do $20 perdido.</li>
    <li><b>GMGN platform fee (1%):</b> deduzido internamente no swap, não aparece como campo separado — só como diferença entre SOL enviado × preço de mercado vs tokens recebidos.</li>
  </ul>
  <p class="warn"><b>Hipótese principal:</b> a condition_orders (TP/SL) gera pelo menos 1 transação de venda adicional. Se o GMGN usa 0.003 SOL de tip por conta própria: <code>48 trades × 0.003 SOL × ${sol_usd:.0f}/SOL = ${48*0.003*sol_usd:.2f}</code> só em tips de saída.</p>
</div>

<p style="color:#555;font-size:11px">SOL/USD usado: ${sol_usd:.2f} | {total_trades} trades</p>
</body></html>"""
    return h


@app.route("/debug/wallet-audit")
def debug_wallet_audit():
    """
    Analisa TODAS as transações on-chain da wallet no período de calibração.
    Usa Solana RPC para buscar txs, parsear fees reais e Jito tips.
    GET /debug/wallet-audit?days=3&rpc=<url>
    """
    import traceback
    try:
        return _wallet_audit_inner()
    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


def _wallet_audit_inner():
    import requests as _req
    import time as _time

    WALLET    = os.getenv("GMGN_WALLET_ADDRESS", "")
    RPC_URL   = request.args.get("rpc") or os.getenv("HELIUS_RPC_URL") or "https://api.mainnet-beta.solana.com"
    DAYS_BACK = int(request.args.get("days", "4"))
    sol_usd   = _get_sol_usd() or 94.0

    if not WALLET:
        return "<pre>GMGN_WALLET_ADDRESS não configurado</pre>", 400

    # Jito tip accounts conhecidos
    JITO_TIPS = {
        "T1pyyaTNZsKv2WcRAB8oVnk93mLJw2XzjtVYqCsaHqt",
        "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
        "HFqU5x63VTqvB8ky8HFGjWGcuGkEbT9fvkdnxwxGaHi",
        "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
        "ADaUMid9sspnBwsm7iHSTe4q3YFe4EtUBfyvYTyMSXgo",
        "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
        "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
        "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    }

    IS_PUBLIC_RPC = "mainnet-beta.solana.com" in RPC_URL
    SLEEP_BETWEEN = 1.2 if IS_PUBLIC_RPC else 0.1   # público = 1req/s max prático
    MAX_TX        = 30  if IS_PUBLIC_RPC else 200    # público = limitar para não timeout HTTP

    def rpc(method, params, retries=3):
        for attempt in range(retries):
            try:
                r = _req.post(RPC_URL, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=30)
                r.raise_for_status()
                body = r.json()
                if "error" in body:
                    code = body["error"].get("code", 0)
                    if code == 429 or code == -32429:   # rate limit
                        _time.sleep(3 * (attempt + 1))
                        continue
                    raise RuntimeError(f"RPC error: {body['error']}")
                return body.get("result")
            except (_req.exceptions.RequestException, RuntimeError) as e:
                if attempt < retries - 1:
                    _time.sleep(2 * (attempt + 1))
                else:
                    raise
        return None

    lines_html = []

    # 1. Buscar assinaturas do período
    since_ts = int(_time.time()) - DAYS_BACK * 86400
    lines_html.append(f"<p>Buscando transações dos últimos {DAYS_BACK} dias para <code>{WALLET[:16]}…</code> via <code>{RPC_URL.split('/')[2]}</code></p>")

    try:
        sigs_result = rpc("getSignaturesForAddress", [WALLET, {"limit": 200}])
    except Exception as e:
        return f"<pre>Erro ao buscar assinaturas: {e}\n\nTente: /debug/wallet-audit?rpc=https://YOUR_HELIUS_URL</pre>", 500

    if not sigs_result:
        return "<pre>Nenhuma transação encontrada.</pre>"

    # Filtrar pelo período
    sigs_in_range = [s for s in sigs_result if s.get("blockTime", 0) >= since_ts]
    lines_html.append(f"<p>Total assinaturas: <b>{len(sigs_result)}</b> | No período: <b>{len(sigs_in_range)}</b></p>")

    if not sigs_in_range:
        return "<pre>Nenhuma transação no período especificado. Tente ?days=7</pre>"

    # 2. Buscar detalhes de cada tx
    # RPC público: muito lento (1 req/s), limitado a MAX_TX para evitar timeout Flask
    sigs_to_fetch = sigs_in_range[:MAX_TX]
    if IS_PUBLIC_RPC and len(sigs_in_range) > MAX_TX:
        lines_html.append(
            f"<p class='warn'>⚠️ RPC público limitado a {MAX_TX} txs (de {len(sigs_in_range)}) para evitar timeout. "
            f"Para análise completa: adicione <code>HELIUS_RPC_URL</code> no Railway ou use "
            f"<code>?rpc=https://mainnet.helius-rpc.com/?api-key=SEU_KEY</code> (free tier suficiente).</p>"
        )

    tx_details = []
    errors_fetch = 0
    for s in sigs_to_fetch:
        sig = s["signature"]
        try:
            tx = rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            if not tx:
                errors_fetch += 1
                continue
            tx["_sig"] = sig
            tx["_blockTime"] = s.get("blockTime")
            tx_details.append(tx)
        except Exception:
            errors_fetch += 1
        _time.sleep(SLEEP_BETWEEN)

    coverage = f"{len(tx_details)}/{len(sigs_in_range)}"
    lines_html.append(f"<p>Transações detalhadas: <b>{len(tx_details)}</b> | erros: {errors_fetch} | cobertura: {coverage}</p>")

    # 3. Parsear cada tx
    SOL = 1_000_000_000  # lamports per SOL
    totals = {
        "n_tx": 0,
        "base_fee_lamports": 0,
        "priority_fee_lamports": 0,
        "jito_tip_lamports": 0,
        "sol_out_lamports": 0,   # total SOL que saiu (compras)
        "sol_in_lamports": 0,    # total SOL que entrou (vendas)
        "n_buy": 0,
        "n_sell": 0,
        "n_failed": 0,
    }

    tx_rows = []
    for tx in sorted(tx_details, key=lambda x: x.get("_blockTime", 0)):
        import datetime as _dt
        sig     = tx["_sig"]
        btime   = tx.get("_blockTime", 0)
        dt_str  = _dt.datetime.fromtimestamp(btime).strftime("%m/%d %H:%M:%S") if btime else "—"

        meta    = tx.get("meta") or {}
        err     = meta.get("err")
        is_failed = err is not None

        if is_failed:
            totals["n_failed"] += 1

        # Base fee (inclui priority fee — Solana meta.fee = base + priority)
        base_fee = meta.get("fee", 0)  # lamports
        totals["base_fee_lamports"] += base_fee
        # Priority fee separado (quando disponível)
        prio_fee = 0
        try:
            prio_fee = (meta.get("computeUnitsConsumed") or 0) * 0  # placeholder — extraído abaixo
            # Tentar extrair priority fee do log de rewards
            for reward in (meta.get("rewards") or []):
                if reward.get("rewardType") == "Fee":
                    prio_fee = reward.get("lamports", 0)
        except Exception:
            pass

        # Pre/post balances da nossa wallet
        accounts = []
        try:
            msg = tx.get("transaction", {}).get("message", {})
            accounts = [a.get("pubkey") if isinstance(a, dict) else a
                        for a in msg.get("accountKeys", [])]
        except Exception:
            pass

        wallet_idx = None
        for i, a in enumerate(accounts):
            if str(a) == WALLET:
                wallet_idx = i
                break

        pre_bals  = meta.get("preBalances",  [])
        post_bals = meta.get("postBalances", [])
        wallet_delta = 0
        if wallet_idx is not None and wallet_idx < len(pre_bals) and wallet_idx < len(post_bals):
            wallet_delta = post_bals[wallet_idx] - pre_bals[wallet_idx]  # positivo = ganhou SOL

        # Jito tips — detectar via pre/postBalances das contas Jito
        # Mais robusto que parsear instructions (funciona mesmo sem encoding jsonParsed)
        jito_lamports = 0
        try:
            for i, acc in enumerate(accounts):
                if str(acc) in JITO_TIPS:
                    if i < len(pre_bals) and i < len(post_bals):
                        delta = post_bals[i] - pre_bals[i]
                        if delta > 0:
                            jito_lamports += delta
        except Exception:
            pass
        # Fallback: parsed instructions (para casos sem lookup table)
        if jito_lamports == 0:
            inner = meta.get("innerInstructions") or []
            all_instructions = []
            try:
                all_instructions = tx.get("transaction", {}).get("message", {}).get("instructions", [])
            except Exception:
                pass
            for ix in list(all_instructions) + [ix2 for g in inner for ix2 in g.get("instructions", [])]:
                if not isinstance(ix, dict):
                    continue
                parsed = ix.get("parsed")
                if isinstance(parsed, dict) and parsed.get("type") == "transfer":
                    info = parsed.get("info", {})
                    if info.get("destination", "") in JITO_TIPS:
                        try:
                            jito_lamports += int(info.get("lamports", 0))
                        except Exception:
                            pass
        totals["jito_tip_lamports"] += jito_lamports

        # Classificar tx: buy (SOL sai, token entra) ou sell (token sai, SOL entra)
        # Usa delta da wallet como proxy
        tx_type = "—"
        if not is_failed:
            if wallet_delta < -50_000:   # saiu SOL líquido (compra)
                tx_type = "BUY"
                totals["n_buy"] += 1
                totals["sol_out_lamports"] += abs(wallet_delta)
            elif wallet_delta > 50_000:  # entrou SOL líquido (venda)
                tx_type = "SELL"
                totals["n_sell"] += 1
                totals["sol_in_lamports"] += wallet_delta
            else:
                tx_type = "OTHER"
        totals["n_tx"] += 1

        # Cor
        color = {"BUY": "#2196F3", "SELL": "#4CAF50", "OTHER": "#888", "—": "#555"}.get(tx_type, "#888")
        err_str = "❌ FAILED" if is_failed else ""

        tx_rows.append(f"""<tr>
          <td style="font-size:10px">{dt_str}</td>
          <td><span style="color:{color}">{tx_type}</span> {err_str}</td>
          <td style="font-size:10px">{sig[:20]}…
            <a href="https://solscan.io/tx/{sig}" target="_blank" style="color:#4FC3F7;font-size:9px">↗</a>
          </td>
          <td style="font-size:11px">{wallet_delta/SOL:+.6f} SOL<br>
            <span style="color:#888">${wallet_delta/SOL*sol_usd:+.4f}</span></td>
          <td style="font-size:11px;color:#FF9800">{jito_lamports/SOL:.6f} SOL<br>
            <span style="color:#888">${jito_lamports/SOL*sol_usd:.4f}</span></td>
          <td style="font-size:11px;color:#888">{base_fee/SOL:.6f} SOL</td>
        </tr>""")

    # 4. Sumário
    net_sol    = (totals["sol_in_lamports"] - totals["sol_out_lamports"]) / SOL
    jito_total = totals["jito_tip_lamports"] / SOL
    base_total = totals["base_fee_lamports"] / SOL

    # Buscar saldo atual
    current_bal = None
    try:
        bal_result = rpc("getBalance", [WALLET])
        if bal_result and "value" in bal_result:
            current_bal = bal_result["value"] / SOL
    except Exception:
        pass

    bal_str = f"{current_bal:.6f} SOL (${current_bal*sol_usd:.2f})" if current_bal is not None else "—"

    rows_html = "\n".join(tx_rows)

    h = f"""<!DOCTYPE html><html><head>
<meta charset="utf-8">
<title>Wallet Audit — NeideBOT</title>
<style>
  body {{ background:#111; color:#ddd; font-family:monospace; padding:20px; }}
  h1,h2 {{ color:#FFD700; }}
  table {{ border-collapse:collapse; width:100%; margin:12px 0; font-size:12px; }}
  th,td {{ border:1px solid #333; padding:6px 8px; text-align:left; }}
  th {{ background:#1e1e1e; color:#aaa; }}
  tr:hover {{ background:#1a1a1a; }}
  .box {{ background:#1a1a1a; border:1px solid #333; padding:16px; border-radius:6px; margin:12px 0; }}
  a {{ color:#4FC3F7; }}
  .warn {{ color:#FF9800; }} .ok {{ color:#4CAF50; }} .bad {{ color:#f44336; }}
</style>
</head><body>
<h1>Wallet Audit — On-chain</h1>
<p>Wallet: <a href="https://solscan.io/account/{WALLET}#transfers" target="_blank"><code>{WALLET}</code></a></p>
{"".join(lines_html)}

<div class="box">
  <h2>Breakdown de Custos — Período de {DAYS_BACK} dias</h2>
  <table style="width:auto">
    <tr><th>Saldo atual</th><td>{bal_str}</td></tr>
    <tr><th>Transações totais</th><td>{totals["n_tx"]}</td></tr>
    <tr><th>BUY (SOL saiu)</th><td class="bad">{totals["n_buy"]} txs | {totals["sol_out_lamports"]/SOL:.5f} SOL (${totals["sol_out_lamports"]/SOL*sol_usd:.2f})</td></tr>
    <tr><th>SELL (SOL entrou)</th><td class="ok">{totals["n_sell"]} txs | {totals["sol_in_lamports"]/SOL:.5f} SOL (${totals["sol_in_lamports"]/SOL*sol_usd:.2f})</td></tr>
    <tr><th>Failed txs</th><td class="warn">{totals["n_failed"]}</td></tr>
    <tr><th>Net SOL (sell − buy)</th>
      <td class="{'ok' if net_sol >= 0 else 'bad'}">{net_sol:+.5f} SOL (${net_sol*sol_usd:+.2f})</td></tr>
    <tr><th>Jito Tips totais</th>
      <td class="warn">{jito_total:.6f} SOL (${jito_total*sol_usd:.2f}) — média {jito_total/max(totals["n_tx"],1)*1000:.2f} mSOL/tx</td></tr>
    <tr><th>Base fees totais</th>
      <td style="color:#888">{base_total:.6f} SOL (${base_total*sol_usd:.4f})</td></tr>
  </table>
  <p class="warn"><b>Jito tip médio por tx:</b> {jito_total/max(totals["n_tx"],1)*SOL:.0f} lamports
    = {jito_total/max(totals["n_tx"],1)*1000:.3f} mSOL
    = ${jito_total/max(totals["n_tx"],1)*sol_usd:.4f}</p>
  <p style="color:#888;font-size:11px">
    GMGN platform fee (1% buy+sell) aparece como slippage no delta da wallet, não como campo separado.
    Net SOL = capital retornado − capital investido.
    Friction total = |Net SOL| se negativo + Jito tips.
  </p>
</div>

<h2>Todas as {len(tx_details)} transações</h2>
<table>
  <tr><th>Data (local)</th><th>Tipo</th><th>Assinatura</th><th>Delta wallet</th><th>Jito Tip</th><th>Base Fee</th></tr>
  {rows_html}
</table>

<p style="color:#555;font-size:11px">SOL/USD: ${sol_usd:.2f} | RPC: {RPC_URL.split('/')[2]}</p>
</body></html>"""
    return h


@app.route("/debug/wallet-tokens")
def debug_wallet_tokens():
    """
    Lista todos os tokens SPL na wallet com saldo > 0.
    Cruza com mints dos trades no DB para identificar posições presas.
    GET /debug/wallet-tokens?rpc=<url>
    """
    import traceback
    try:
        return _wallet_tokens_inner()
    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


def _wallet_tokens_inner():
    import requests as _req
    import time as _time

    WALLET  = os.getenv("GMGN_WALLET_ADDRESS", "")
    RPC_URL = request.args.get("rpc") or os.getenv("HELIUS_RPC_URL") or "https://api.mainnet-beta.solana.com"
    sol_usd = _get_sol_usd() or 94.0

    if not WALLET:
        return "<pre>GMGN_WALLET_ADDRESS não configurado</pre>", 400

    def rpc(method, params, retries=3):
        for attempt in range(retries):
            try:
                r = _req.post(RPC_URL, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=30)
                r.raise_for_status()
                body = r.json()
                if "error" in body:
                    code = body["error"].get("code", 0)
                    if code in (429, -32429):
                        _time.sleep(3 * (attempt + 1))
                        continue
                    raise RuntimeError(f"RPC error: {body['error']}")
                return body.get("result")
            except (_req.exceptions.RequestException, RuntimeError) as e:
                if attempt < retries - 1:
                    _time.sleep(2 * (attempt + 1))
                else:
                    raise
        return None

    # 1. Saldo SOL atual
    sol_bal = 0.0
    try:
        r = rpc("getBalance", [WALLET])
        if r and "value" in r:
            sol_bal = r["value"] / 1e9
    except Exception:
        pass

    # 2. Todos os token accounts (SPL) com saldo > 0
    spl_result = rpc("getTokenAccountsByOwner", [
        WALLET,
        {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
        {"encoding": "jsonParsed"}
    ])

    tokens_in_wallet = []   # ATAs com tokens (amount > 0)
    empty_atas       = []   # ATAs vazios com rent preso (amount == 0)
    if spl_result and "value" in spl_result:
        for acct in spl_result["value"]:
            try:
                info       = acct["account"]["data"]["parsed"]["info"]
                mint       = info["mint"]
                amount     = int(info["tokenAmount"]["amount"])
                dec        = int(info["tokenAmount"]["decimals"])
                ui_amt     = float(info["tokenAmount"]["uiAmount"] or 0)
                # SOL preso no ATA (rent-exempt deposit, em lamports)
                ata_sol    = acct["account"].get("lamports", 0) / 1e9
                ata_pubkey = acct.get("pubkey", "")
                if amount > 0:
                    tokens_in_wallet.append({
                        "mint": mint, "amount_raw": amount,
                        "decimals": dec, "ui_amount": ui_amt,
                        "ata_sol": ata_sol, "ata_pubkey": ata_pubkey,
                    })
                else:
                    # ATA vazio — rent preso aqui
                    empty_atas.append({
                        "mint": mint, "ata_sol": ata_sol,
                        "ata_pubkey": ata_pubkey,
                    })
            except Exception:
                continue

    # 3. Buscar mints dos trades no DB para cruzar
    import psycopg2.extras as _pge
    conn = psycopg2.connect(db.DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=_pge.RealDictCursor) as cur:
            cur.execute("""
                SELECT t.id, t.mint, t.status, t.ts_signal, t.entry_expected,
                       t.order_id, t.fee_sol, t.tokens_received
                FROM trades t
                ORDER BY t.ts_signal DESC
            """)
            db_trades = cur.fetchall()
    finally:
        conn.close()

    trade_by_mint = {}
    for row in db_trades:
        tid, mint, status, ts_sig, entry_exp, order_id, fee_sol, tok_recv = row
        trade_by_mint[mint] = {
            "id": tid,
            "status": status,
            "ts_signal": ts_sig,
            "entry_expected": entry_exp,
            "order_id": order_id,
            "fee_sol": fee_sol,
            "tokens_received": tok_recv,
        }

    # 4. Cruzar tokens na wallet com trades DB
    wallet_mints = {t["mint"] for t in tokens_in_wallet}
    db_mints     = set(trade_by_mint.keys())
    stuck_mints  = wallet_mints & db_mints  # tokens que vieram de um trade
    unknown_mints = wallet_mints - db_mints  # tokens que não têm trade registrado

    # Todos os trades com status buy-like mas sem confirmação de sell
    open_trades = {
        m: v for m, v in trade_by_mint.items()
        if v["status"] in ("pending", "error", "sl", "expired", "tp")
    }
    # Trades onde o mint NÃO está mais na wallet = posição resolvida
    resolved_no_token = {m: v for m, v in open_trades.items() if m not in wallet_mints}
    # Trades onde o mint ESTÁ na wallet = posição potencialmente presa
    stuck_trades = {m: v for m, v in open_trades.items() if m in wallet_mints}

    # 5. Tentar buscar preço atual dos tokens presos via GMGN token/info
    gmgn_prices = {}
    gmgn_client = GmgnClient()
    for mint in list(stuck_mints)[:10]:  # limitar a 10 para não gastar rate limit
        try:
            info = gmgn_client.get_token_info(mint, chain="sol")
            if info:
                price_usd = float(info.get("price") or 0)
                gmgn_prices[mint] = price_usd
        except Exception:
            pass
        _time.sleep(0.15)

    # 6. Calcular valor estimado dos tokens presos
    def token_usd(mint, ui_amt):
        p = gmgn_prices.get(mint, 0)
        if p and ui_amt:
            return p * ui_amt
        return None

    # === HTML ===
    def status_badge(s):
        colors = {"tp": "#4CAF50", "sl": "#f44336", "error": "#FF9800",
                  "expired": "#9E9E9E", "pending": "#2196F3", "done": "#4CAF50"}
        c = colors.get(s, "#888")
        return f'<span style="color:{c};font-weight:bold">{s}</span>'

    stuck_rows = ""
    total_stuck_usd = 0.0
    for t in tokens_in_wallet:
        mint = t["mint"]
        if mint not in stuck_mints:
            continue
        trade = trade_by_mint[mint]
        usd_val = token_usd(mint, t["ui_amount"])
        if usd_val:
            total_stuck_usd += usd_val
        entry_mc = trade["entry_expected"]
        ts_sig   = trade["ts_signal"]
        ts_str   = ts_sig.strftime("%m/%d %H:%M") if ts_sig else "—"
        usd_str  = f"${usd_val:.2f}" if usd_val else "N/A"
        stuck_rows += f"""<tr>
          <td style="font-size:10px">{ts_str}</td>
          <td style="font-size:10px"><a href="https://solscan.io/token/{mint}" target="_blank" style="color:#4FC3F7">{mint[:16]}…</a></td>
          <td>{status_badge(trade['status'])}</td>
          <td>{t['ui_amount']:.2f}</td>
          <td style="color:{'#4CAF50' if usd_val else '#888'}">{usd_str}</td>
          <td style="font-size:10px">{trade.get('order_id','—') or '—'}</td>
        </tr>"""

    unknown_rows = ""
    for t in tokens_in_wallet:
        if t["mint"] in stuck_mints:
            continue
        mint   = t["mint"]
        usd_val = token_usd(mint, t["ui_amount"])
        usd_str = f"${usd_val:.2f}" if usd_val else "N/A"
        unknown_rows += f"""<tr>
          <td style="font-size:10px"><a href="https://solscan.io/token/{mint}" target="_blank" style="color:#4FC3F7">{mint[:16]}…</a></td>
          <td>{t['ui_amount']:.2f}</td>
          <td style="color:#888">{usd_str}</td>
          <td><a href="https://gmgn.ai/sol/token/{mint}" target="_blank" style="color:#FF9800;font-size:10px">GMGN ↗</a></td>
        </tr>"""

    # Trades no DB com status != done/tp que NÃO têm tokens na wallet
    missing_rows = ""
    for mint, trade in sorted(resolved_no_token.items(), key=lambda x: x[1]["ts_signal"] or ""):
        ts_sig = trade["ts_signal"]
        ts_str = ts_sig.strftime("%m/%d %H:%M") if ts_sig else "—"
        missing_rows += f"""<tr>
          <td style="font-size:10px">{ts_str}</td>
          <td style="font-size:10px"><a href="https://solscan.io/token/{mint}" target="_blank" style="color:#4FC3F7">{mint[:16]}…</a></td>
          <td>{status_badge(trade['status'])}</td>
          <td style="color:#888;font-size:10px">{trade.get('order_id','—') or '—'}</td>
        </tr>"""

    h = f"""<!DOCTYPE html><html><head>
<meta charset="utf-8">
<title>Wallet Tokens — NeideBOT</title>
<style>
  body {{ background:#111; color:#ddd; font-family:monospace; padding:20px; }}
  h1,h2,h3 {{ color:#FFD700; }}
  table {{ border-collapse:collapse; width:100%; margin:12px 0; font-size:12px; }}
  th,td {{ border:1px solid #333; padding:6px 8px; text-align:left; }}
  th {{ background:#1e1e1e; color:#aaa; }}
  tr:hover {{ background:#1a1a1a; }}
  .box {{ background:#1a1a1a; border:1px solid #333; padding:16px; border-radius:6px; margin:12px 0; }}
  a {{ color:#4FC3F7; }}
  .warn {{ color:#FF9800; }} .ok {{ color:#4CAF50; }} .bad {{ color:#f44336; }}
</style>
</head><body>
<h1>Wallet Tokens — Forense de Posições</h1>
<p>Wallet: <a href="https://solscan.io/account/{WALLET}#spl-transfers" target="_blank"><code>{WALLET}</code></a></p>
<p>RPC: <code>{RPC_URL.split('/')[2]}</code> | SOL/USD: ${sol_usd:.2f}</p>

<div class="box">
  <h2>Resumo</h2>
  <table style="width:auto">
    <tr><th>Saldo SOL (nativo)</th><td><b>{sol_bal:.6f} SOL</b> (${sol_bal*sol_usd:.2f})</td></tr>
    <tr><th>ATAs vazios (rent preso)</th><td class="bad"><b>{len(empty_atas)}</b> contas | {sum(a["ata_sol"] for a in empty_atas):.6f} SOL (${sum(a["ata_sol"] for a in empty_atas)*sol_usd:.2f}) bloqueados</td></tr>
    <tr><th>Tokens SPL com saldo</th><td>{len(tokens_in_wallet)}</td></tr>
    <tr><th>Tokens de trades (presos?)</th><td class="warn">{len(stuck_mints)} | valor estimado: ${total_stuck_usd:.2f}</td></tr>
    <tr><th>Tokens sem trade registrado</th><td class="warn">{len(unknown_mints)}</td></tr>
    <tr><th>Trades sem token na wallet</th><td style="color:#888">{len(resolved_no_token)} (resolvidos/vendidos/perdidos)</td></tr>
  </table>
</div>

<div class="box">
  <h2>🔒 ATAs Vazios — Rent Bloqueado ({len(empty_atas)} contas)</h2>
  <p style="color:#888;font-size:11px">
    Cada token comprado cria um "armário" (ATA) na Solana que custa ~0.002 SOL de depósito (rent).
    Quando a GMGN vende os tokens via condition_orders, ela NÃO fecha esse armário.
    O depósito fica preso. Para recuperar: fechar cada ATA via <code>spl-token close-account</code> ou Phantom UI.
  </p>
  <p style="color:#FF9800">Total bloqueado: <b>{sum(a["ata_sol"] for a in empty_atas):.6f} SOL = ${sum(a["ata_sol"] for a in empty_atas)*sol_usd:.2f}</b>
    | Média por ATA: {sum(a["ata_sol"] for a in empty_atas)/max(len(empty_atas),1)*1000:.2f} mSOL</p>
  {"<table><tr><th>Mint</th><th>SOL preso</th><th>ATA address</th><th>Links</th></tr>" + "".join(f'''<tr>
    <td style="font-size:10px"><a href="https://solscan.io/token/{a["mint"]}" target="_blank" style="color:#4FC3F7">{a["mint"][:20]}…</a></td>
    <td style="color:#f44336">{a["ata_sol"]:.6f} SOL (${a["ata_sol"]*sol_usd:.3f})</td>
    <td style="font-size:10px"><a href="https://solscan.io/account/{a["ata_pubkey"]}" target="_blank" style="color:#888">{a["ata_pubkey"][:20]}…</a></td>
    <td><a href="https://gmgn.ai/sol/token/{a["mint"]}" target="_blank" style="color:#FF9800;font-size:9px">GMGN ↗</a></td>
  </tr>''' for a in sorted(empty_atas, key=lambda x: -x["ata_sol"])) + "</table>" if empty_atas else "<p class='ok'>Nenhum ATA vazio — GMGN está fechando as contas corretamente.</p>"}
</div>

<div class="box">
  <h2>⚠️ Tokens Presos — de trades registrados no DB ({len(stuck_mints)})</h2>
  <p style="color:#888;font-size:11px">Tokens que vieram de um trade e ainda estão na wallet — podem ter SL não executado ou TP não disparado.</p>
  {"<table><tr><th>Data Trade</th><th>Mint</th><th>Status DB</th><th>Quantidade</th><th>Valor Atual</th><th>Order ID</th></tr>" + stuck_rows + "</table>" if stuck_mints else "<p class='ok'>Nenhum — todos os tokens de trades foram vendidos.</p>"}
</div>

<div class="box">
  <h2>Tokens Desconhecidos na Wallet ({len(unknown_mints)})</h2>
  <p style="color:#888;font-size:11px">Tokens sem trade correspondente no DB — podem ser de trades manuais ou airdrops.</p>
  {"<table><tr><th>Mint</th><th>Quantidade</th><th>Valor</th><th>Links</th></tr>" + unknown_rows + "</table>" if unknown_mints else "<p class='ok'>Nenhum.</p>"}
</div>

<div class="box">
  <h2>Trades no DB sem Token na Wallet ({len(resolved_no_token)})</h2>
  <p style="color:#888;font-size:11px">Trades com status != done/tp mas cujo token NÃO está mais na wallet — posição resolvida (vendida pela GMGN, SL executado on-chain, ou token sem valor).</p>
  {"<table><tr><th>Data</th><th>Mint</th><th>Status</th><th>Order ID</th></tr>" + missing_rows + "</table>" if resolved_no_token else "<p class='ok'>Nenhum.</p>"}
</div>

<p style="color:#555;font-size:11px">
  Preços via GMGN (primeiros 10 tokens). Tokens sem preço = token muito novo ou sem liquidez.<br>
  Para ver todas as txs: <a href="/debug/wallet-audit">Wallet Audit</a>
</p>
</body></html>"""
    return h


@app.route("/analise/dip-entry-sim")
def analise_dip_entry_sim():
    """
    Simulação: e se esperássemos o preço cair X% desde o sinal antes de entrar?
    Compara a estratégia atual (entrada imediata) vs entrada com dip limit.
    """
    import traceback
    try:
        return _dip_entry_sim_inner()
    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


def _dip_entry_sim_inner():
    DIP_PCT   = float(request.args.get("dip",  "0.15"))  # % queda do sinal para gatilhar nova entrada
    TP_PCT    = float(request.args.get("tp",   "0.75"))  # TP da nova entrada
    SL_PCT    = float(request.args.get("sl",   "0.15"))  # SL da nova entrada

    TP_STATUSES = ("tp_hit", "trailing_active")
    SL_STATUSES = ("sl_hit", "sl")

    with db._cursor() as cur:
        cur.execute("""
            SELECT
                t.id, t.symbol, t.mint, t.status, t.exit_type, t.pnl_pct_real,
                t.ts_signal, t.created_at,
                COALESCE(b.mc_at_signal, t.entry_expected) AS signal_price,
                b.bounce_pct, b.drop_from_mig AS drop_pct, b.mc_min AS bottom_price
            FROM trades t
            LEFT JOIN bounce_signals b ON b.mint = t.mint
            WHERE t.status IN ('tp_hit','trailing_active','sl_hit','sl','expired')
              AND COALESCE(b.mc_at_signal, t.entry_expected) IS NOT NULL
            ORDER BY t.id ASC
        """)
        trades = [dict(r) for r in cur.fetchall()]

    def is_tp_real(t):
        if t["status"] == "expired" and t.get("pnl_pct_real") and t["pnl_pct_real"] > 0:
            return True
        return t["status"] in TP_STATUSES or t["exit_type"] in TP_STATUSES

    results = []
    n_no_ticks = 0

    for t in trades:
        signal_price = float(t["signal_price"])
        ref_ts       = t["ts_signal"] or t["created_at"]
        mint         = t["mint"]
        was_tp       = is_tp_real(t)

        # Ticks após o sinal
        with db._cursor() as cur:
            cur.execute("""
                SELECT tick_ts,
                       COALESCE(mc_low,  mc) AS low,
                       COALESCE(mc_high, mc) AS high,
                       mc
                FROM token_ticks
                WHERE mint = %s AND tick_ts >= %s AND mc IS NOT NULL
                ORDER BY tick_ts ASC
            """, (mint, ref_ts))
            ticks = [dict(r) for r in cur.fetchall()]

        if not ticks:
            n_no_ticks += 1
            results.append({**t, "was_tp": was_tp, "sim": "no_ticks",
                            "new_entry": None, "sim_tp": None, "sim_sl": None})
            continue

        # Gatilho: primeiro tick onde low <= signal_price * (1 - DIP_PCT)
        dip_trigger  = signal_price * (1 - DIP_PCT)
        new_entry    = None
        trigger_idx  = None
        for i, tick in enumerate(ticks):
            if float(tick["low"]) <= dip_trigger:
                new_entry   = dip_trigger  # entramos no preço alvo
                trigger_idx = i
                break

        if new_entry is None:
            # Preço nunca caiu o suficiente — não entramos
            # Se era TP → perdemos o trade; se era SL → nos salvamos
            sim = "missed_tp" if was_tp else "saved_sl"
            results.append({**t, "was_tp": was_tp, "sim": sim,
                            "new_entry": None, "sim_tp": None, "sim_sl": None,
                            "dip_trigger": dip_trigger})
            continue

        # A partir da nova entrada, procura TP ou SL nos ticks seguintes
        new_tp_price = new_entry * (1 + TP_PCT)
        new_sl_price = new_entry * (1 - SL_PCT)
        sim_outcome  = "open"  # se nenhum hit até o fim dos ticks

        for tick in ticks[trigger_idx:]:
            high = float(tick["high"])
            low  = float(tick["low"])
            if high >= new_tp_price:
                sim_outcome = "tp"
                break
            if low > 0 and low <= new_sl_price:
                sim_outcome = "sl"
                break

        results.append({
            **t,
            "was_tp":      was_tp,
            "sim":         sim_outcome,
            "new_entry":   new_entry,
            "dip_trigger": dip_trigger,
            "sim_tp":      new_tp_price,
            "sim_sl":      new_sl_price,
            "bottom_price": t.get("bottom_price"),
            "entry_vs_bottom": (new_entry / float(t["bottom_price"]) - 1) if t.get("bottom_price") and t["bottom_price"] else None,
        })

    # ── Estatísticas ────────────────────────────────────────────────────────
    real_closed  = [r for r in results if r["was_tp"] is not None and r["sim"] != "no_ticks"]
    real_tp      = sum(1 for r in real_closed if r["was_tp"])
    real_sl      = len(real_closed) - real_tp
    real_wr      = real_tp / len(real_closed) if real_closed else 0

    triggered    = [r for r in results if r["sim"] in ("tp", "sl", "open")]
    missed_tp    = [r for r in results if r["sim"] == "missed_tp"]
    saved_sl     = [r for r in results if r["sim"] == "saved_sl"]

    sim_tp_count = sum(1 for r in triggered if r["sim"] == "tp")
    sim_sl_count = sum(1 for r in triggered if r["sim"] == "sl")
    sim_open     = sum(1 for r in triggered if r["sim"] == "open")
    sim_closed   = [r for r in triggered if r["sim"] in ("tp", "sl")]
    sim_wr       = sim_tp_count / len(sim_closed) if sim_closed else 0

    # EV comparado
    ev_real = real_tp * TP_PCT - real_sl * SL_PCT
    ev_sim  = sim_tp_count * TP_PCT - sim_sl_count * SL_PCT

    _CSS = """<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Courier New',monospace;background:#0d1117;color:#c9d1d9;font-size:12px}
h1{padding:14px 16px;color:#58a6ff;font-size:16px;border-bottom:1px solid #21262d}
h2{font-size:13px;color:#58a6ff;padding:8px 0 4px}
.grid{display:flex;gap:10px;padding:12px 16px;flex-wrap:wrap}
.card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:10px 16px;min-width:130px}
.lbl{color:#8b949e;font-size:10px;margin-bottom:3px}
.val{font-size:20px;font-weight:bold}
.green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}.muted{color:#8b949e}.blue{color:#58a6ff}
.block{padding:6px 16px 14px;border-bottom:1px solid #21262d}
table{width:100%;border-collapse:collapse;margin:6px 0}
th{background:#161b22;color:#8b949e;padding:4px 8px;text-align:left;border-bottom:1px solid #30363d;font-size:10px}
td{padding:3px 8px;border-bottom:1px solid #21262d;font-size:11px}
tr:hover td{background:#161b22}
.nav{padding:8px 16px;display:flex;gap:12px;border-bottom:1px solid #21262d}
.nav a{color:#58a6ff;font-size:11px;text-decoration:none}
.insight{background:#0d2137;color:#79c0ff;padding:10px 14px;margin:8px 16px;border-radius:4px;font-size:12px;border-left:3px solid #58a6ff}
.warn{background:#3d2200;color:#d29922;padding:10px 14px;margin:8px 16px;border-radius:4px;font-size:12px}
.vs{display:flex;gap:24px;padding:12px 16px;align-items:flex-start;flex-wrap:wrap}
.col{flex:1;min-width:200px;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px 16px}
.col-title{color:#8b949e;font-size:11px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px}
.big{font-size:28px;font-weight:bold;margin-bottom:4px}
.sub{font-size:11px;color:#8b949e}
</style>"""

    def _fp(v, mult=100): return f"{v*mult:+.1f}%" if v is not None else "—"
    def _fm(v): return f"${v:,.0f}" if v is not None else "—"

    # Comparativo visual
    wr_delta  = sim_wr - real_wr
    ev_delta  = ev_sim - ev_real
    wr_color  = "green" if wr_delta > 0 else "red"
    ev_color  = "green" if ev_delta > 0 else "red"

    h = f"""<!DOCTYPE html><html><head><meta charset='utf-8'><title>Dip Entry Sim</title>{_CSS}</head><body>
<h1>Simulação: Entrada com Dip Limit (−{DIP_PCT*100:.0f}%)</h1>
<div class='nav'>
  <a href='/trades'>Trades</a><a href='/analise/wr-breakdown'>WR Breakdown</a>
  <a href='?dip=0.05'>dip −5%</a>
  <a href='?dip=0.10'>dip −10%</a>
  <a href='?dip=0.15'>dip −15%</a>
  <a href='?dip=0.20'>dip −20%</a>
  <a href='?dip=0.25'>dip −25%</a>
</div>
<p style='padding:6px 16px;color:#8b949e;font-size:11px'>
  Lógica: aguarda preço cair {DIP_PCT*100:.0f}% do sinal → entra → TP +{TP_PCT*100:.0f}% / SL −{SL_PCT*100:.0f}% da nova entrada.
  Trades sem ticks excluídos da simulação (n={n_no_ticks}).
</p>

<div class='vs'>
  <div class='col'>
    <div class='col-title'>📍 Atual — entrada imediata</div>
    <div class='big {'green' if real_wr > 0.25 else 'red'}'>{real_wr*100:.1f}%</div>
    <div class='sub'>WR | {real_tp} TP / {real_sl} SL / {len(real_closed)} fechados</div>
    <div style='margin-top:10px;font-size:13px'>EV bruto: <b class='{'green' if ev_real >= 0 else 'red'}'>{_fp(ev_real/len(real_closed) if real_closed else None)}</b>/trade</div>
  </div>
  <div class='col'>
    <div class='col-title'>⏳ Simulado — dip −{DIP_PCT*100:.0f}%</div>
    <div class='big {wr_color}'>{sim_wr*100:.1f}%</div>
    <div class='sub'>WR | {sim_tp_count} TP / {sim_sl_count} SL / {len(sim_closed)} fechados</div>
    <div style='margin-top:10px;font-size:13px'>EV bruto: <b class='{'green' if ev_sim >= 0 else 'red'}'>{_fp(ev_sim/len(sim_closed) if sim_closed else None)}</b>/trade</div>
  </div>
  <div class='col'>
    <div class='col-title'>📊 Delta</div>
    <div class='big {wr_color}'>{wr_delta*100:+.1f}pp</div>
    <div class='sub'>variação de WR</div>
    <div style='margin-top:10px;font-size:13px'>
      TPs perdidos: <b class='red'>{len(missed_tp)}</b><br>
      SLs evitados: <b class='green'>{len(saved_sl)}</b><br>
      Dip não atingido: <b class='muted'>{len(missed_tp)+len(saved_sl)}</b> trades (preço foi para cima direto)
    </div>
  </div>
</div>
"""

    # Insight
    if sim_wr > real_wr:
        h += f"""<div class='insight'>
✅ <b>A estratégia dip melhorou o WR em {wr_delta*100:+.1f}pp.</b><br>
De {len(real_closed)} trades, {len(triggered)} teriam atingido o dip (entramos) e {len(saved_sl)+len(missed_tp)} não (preço subiu direto).<br>
Dos {len(saved_sl)} SLs que não atingiram o dip: salvamos todos. Dos {len(missed_tp)} TPs que não atingiram: perdemos todos.<br>
Tradeoff: <b>{len(saved_sl)} SLs evitados</b> vs <b>{len(missed_tp)} TPs perdidos</b>.
</div>"""
    else:
        h += f"""<div class='warn'>
⚠ A estratégia dip <b>piorou ou manteve</b> o WR ({wr_delta*100:+.1f}pp).<br>
Muitos TPs foram perdidos porque o preço subiu sem retroceder {DIP_PCT*100:.0f}%.
</div>"""

    # Tabela de detalhes
    sim_color = {"tp": "green", "sl": "red", "open": "yellow",
                 "missed_tp": "red", "saved_sl": "green", "no_ticks": "muted"}
    sim_label = {"tp": "TP ✅ (sim)", "sl": "SL 💀 (sim)", "open": "⏳ aberto",
                 "missed_tp": "🚫 perdeu TP", "saved_sl": "💾 evitou SL", "no_ticks": "— sem ticks"}

    rows = ""
    for r in results:
        real_cls  = "green" if r["was_tp"] else "red"
        real_txt  = "TP" if r["was_tp"] else "SL"
        s         = r["sim"]
        sc        = sim_color.get(s, "muted")
        sl_txt    = sim_label.get(s, s)
        bp        = f"{r['bounce_pct']*100:.0f}%" if r.get("bounce_pct") else "—"
        ne        = _fm(r.get("new_entry"))
        evb       = f"{r['entry_vs_bottom']*100:+.1f}%" if r.get("entry_vs_bottom") is not None else "—"
        rows += f"""<tr>
<td>{r['id']}</td>
<td><b>{r['symbol'] or r['mint'][:8]}</b></td>
<td class='{real_cls}'>{real_txt}</td>
<td>{bp}</td>
<td class='{sc}'>{sl_txt}</td>
<td>{ne}</td>
<td class='muted'>{evb}</td>
</tr>"""

    h += f"""<div class='block'>
<h2>Detalhe por trade</h2>
<table>
<tr><th>#</th><th>Symbol</th><th>Real</th><th>Bounce%</th><th>Simulado</th><th>Nova entrada</th><th>vs fundo</th></tr>
{rows}
</table>
</div>
</body></html>"""
    return h


@app.route("/analise/wr-breakdown")
def analise_wr_breakdown():
    """
    Breakdown completo do WR por feature — identifica o que discrimina TP de SL.
    Cross-referencia trades com migrations (qualidade), bounce_signals (timing),
    token_signals (H9 smart money), para cada combinação mostra WR.
    """
    import traceback
    try:
        return _wr_breakdown_inner()
    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


def _wr_breakdown_inner():
    # Parâmetro opcional: ?since=YYYY-MM-DD (padrão = todos os trades)
    since = request.args.get("since", "")

    TP_STATUSES = ("tp", "tp_hit", "trailing_active")
    SL_STATUSES = ("sl", "sl_hit")

    with db._cursor() as cur:
        since_clause = "AND t.created_at >= %s" if since else ""
        params = [since] if since else []

        # Diagnóstico: distribuição de status antes de filtrar
        cur.execute(f"SELECT status, exit_type, COUNT(*) as n FROM trades {('WHERE created_at >= %s' if since else '')} GROUP BY status, exit_type ORDER BY n DESC", params)
        status_dist = [dict(r) for r in cur.fetchall()]

        cur.execute(f"""
            SELECT
                t.id,
                t.symbol,
                t.mint,
                t.ts_signal,
                t.created_at,
                t.status,
                t.exit_type,
                t.pnl_pct_real,
                t.entry_expected,
                t.mc_bottom,
                -- bounce_signals
                b.mc_min           AS b_mc_min,
                b.mc_at_signal     AS b_mc_at_signal,
                b.bounce_pct       AS b_bounce_pct,
                b.drop_from_mig    AS b_drop_pct,
                b.elapsed_sec / 60.0 AS b_elapsed_min,
                -- migrations
                m.mc_migration,
                m.time_to_bottom,
                m.creator_token_status,
                m.creator_balance_rate,
                m.bot_degen_rate,
                m.rat_trader_rate,
                m.top10_holder_rate,
                m.top70_sniper_hold_rate,
                m.rug_ratio,
                m.smart_wallets,
                m.liq_migration,
                m.has_social,
                m.cto_flag,
                -- H9: melhor sinal type 12 antes do bounce (trigger_at é BIGINT unix ts)
                (
                    SELECT COUNT(*)
                    FROM token_signals ts
                    WHERE ts.mint = t.mint
                      AND TO_TIMESTAMP(ts.trigger_at) <= COALESCE(t.ts_signal, t.created_at)
                ) AS n_h9_signals,
                (
                    SELECT MIN(ts.trigger_mc / NULLIF(m2.mc_migration, 0))
                    FROM token_signals ts
                    JOIN migrations m2 ON m2.mint = ts.mint
                    WHERE ts.mint = t.mint
                      AND TO_TIMESTAMP(ts.trigger_at) <= COALESCE(t.ts_signal, t.created_at)
                ) AS h9_best_ratio
            FROM trades t
            LEFT JOIN bounce_signals b ON b.mint = t.mint
            LEFT JOIN migrations     m ON m.mint = t.mint
            WHERE t.status IN ('tp','tp_hit','trailing_active','sl','sl_hit','expired','open','pending')
              {since_clause}
            ORDER BY t.id ASC
        """, params)
        trades = [dict(r) for r in cur.fetchall()]

    if not trades:
        return "<pre>Nenhum trade fechado encontrado.</pre>"

    def is_tp(t):
        # "expired" com pnl positivo = trailing_active que o monitor age-out sobrescreveu
        # (bug corrigido: get_open_trades() já não retorna trailing_active; fix para dados históricos)
        if t["status"] == "expired" and t.get("pnl_pct_real") and t["pnl_pct_real"] > 0:
            return True
        return t["status"] in TP_STATUSES or t["exit_type"] in TP_STATUSES

    def is_sl(t):
        if t["status"] == "expired" and t.get("pnl_pct_real") and t["pnl_pct_real"] > 0:
            return False  # não é SL — é TP que expirou (ver is_tp acima)
        return t["status"] in SL_STATUSES or t["exit_type"] in SL_STATUSES

    open_trades = [t for t in trades if t["status"] in ("open", "pending")]
    # Trades invisíveis: expired sem pnl_pct_real (trailing_active que GMGN fechou mas TradeMonitor não detectou)
    ghost_trades = [t for t in trades if t["status"] == "expired" and not t.get("pnl_pct_real")]
    closed = [t for t in trades if is_tp(t) or is_sl(t)]
    total  = len(closed)
    n_tp   = sum(1 for t in closed if is_tp(t))
    n_sl   = sum(1 for t in closed if is_sl(t))
    wr_base = n_tp / total if total else 0

    def wr_bucket(rows):
        if not rows: return None, 0
        n = len(rows)
        w = sum(1 for r in rows if is_tp(r))
        return w / n, n

    def bucket_table(title, splits):
        """splits: list of (label, [rows])"""
        rows_html = ""
        for label, rows in splits:
            w, n = wr_bucket(rows)
            if n == 0:
                continue
            delta = (w - wr_base) if w is not None else None
            bar_w = int((w or 0) * 120)
            delta_s = f"{delta*100:+.1f}pp" if delta is not None else "—"
            color = "green" if delta and delta > 0.03 else ("red" if delta and delta < -0.03 else "yellow")
            rows_html += f"""<tr>
<td>{label}</td>
<td><div style='background:#21262d;border-radius:2px;height:8px;width:120px'>
    <div style='background:#3fb950;height:8px;border-radius:2px;width:{bar_w}px'></div></div></td>
<td class='{color}'><b>{(w or 0)*100:.1f}%</b></td>
<td>{n}</td>
<td class='{color}'>{delta_s}</td>
</tr>"""
        if not rows_html:
            return ""
        return f"""<div class='block'>
<h2>{title}</h2>
<table>
<tr><th>Bucket</th><th>WR visual</th><th>WR</th><th>N</th><th>vs baseline</th></tr>
{rows_html}
</table></div>"""

    # ── Splits por feature ──────────────────────────────────────────────────
    sections = ""

    # 1. bounce_pct (entry timing)
    splits_bounce = [
        ("15–20% bounce",  [t for t in closed if t["b_bounce_pct"] and 0.15 <= t["b_bounce_pct"] < 0.20]),
        ("20–30% bounce",  [t for t in closed if t["b_bounce_pct"] and 0.20 <= t["b_bounce_pct"] < 0.30]),
        ("30–50% bounce",  [t for t in closed if t["b_bounce_pct"] and 0.30 <= t["b_bounce_pct"] < 0.50]),
        (">50% bounce",    [t for t in closed if t["b_bounce_pct"] and t["b_bounce_pct"] >= 0.50]),
    ]
    sections += bucket_table("Bounce % no sinal (timing da entrada)", splits_bounce)

    # 2. elapsed_min (tempo desde migração)
    splits_elapsed = [
        ("<5 min pós-migração",   [t for t in closed if t["b_elapsed_min"] is not None and t["b_elapsed_min"] < 5]),
        ("5–10 min",              [t for t in closed if t["b_elapsed_min"] is not None and 5 <= t["b_elapsed_min"] < 10]),
        ("10–15 min",             [t for t in closed if t["b_elapsed_min"] is not None and 10 <= t["b_elapsed_min"] < 15]),
        ("15–20 min",             [t for t in closed if t["b_elapsed_min"] is not None and 15 <= t["b_elapsed_min"] < 20]),
    ]
    sections += bucket_table("Elapsed min (migração → sinal)", splits_elapsed)

    # 3. drop_pct (profundidade do dump)
    splits_drop = [
        ("30–50% drop",  [t for t in closed if t["b_drop_pct"] and 0.30 <= t["b_drop_pct"] < 0.50]),
        ("50–70% drop",  [t for t in closed if t["b_drop_pct"] and 0.50 <= t["b_drop_pct"] < 0.70]),
        ("70–85% drop",  [t for t in closed if t["b_drop_pct"] and 0.70 <= t["b_drop_pct"] < 0.85]),
        (">85% drop",    [t for t in closed if t["b_drop_pct"] and t["b_drop_pct"] >= 0.85]),
    ]
    sections += bucket_table("Drop % do mc_migration (profundidade do dump)", splits_drop)

    # 4. time_to_bottom (H10)
    splits_ttb = [
        ("<10 min até fundo",    [t for t in closed if t["time_to_bottom"] is not None and t["time_to_bottom"] < 10]),
        ("10–15 min",            [t for t in closed if t["time_to_bottom"] is not None and 10 <= t["time_to_bottom"] < 15]),
        ("15–20 min",            [t for t in closed if t["time_to_bottom"] is not None and 15 <= t["time_to_bottom"] < 20]),
        (">20 min (A.5 borda)",  [t for t in closed if t["time_to_bottom"] is not None and t["time_to_bottom"] >= 20]),
    ]
    sections += bucket_table("Time to bottom (H10) — minutos até o fundo", splits_ttb)

    # 5. H9 smart money signal
    splits_h9 = [
        ("Sem sinal H9",                  [t for t in closed if not t["n_h9_signals"]]),
        ("Com sinal H9 (qualquer ratio)", [t for t in closed if t["n_h9_signals"] and t["n_h9_signals"] > 0]),
        ("H9 ratio < 0.80 (preditivo)",  [t for t in closed if t["h9_best_ratio"] and t["h9_best_ratio"] < 0.80]),
        ("H9 ratio 0.80–1.00",           [t for t in closed if t["h9_best_ratio"] and 0.80 <= t["h9_best_ratio"] < 1.00]),
        ("H9 ratio > 1.00 (pós-bounce)", [t for t in closed if t["h9_best_ratio"] and t["h9_best_ratio"] >= 1.00]),
    ]
    sections += bucket_table("H9 — Smart money signal antes do bounce", splits_h9)

    # 6. creator_token_status
    splits_creator = [
        ("creator_close (dev vendeu)",    [t for t in closed if t["creator_token_status"] == "creator_close"]),
        ("creator_hold (dev segura)",     [t for t in closed if t["creator_token_status"] == "creator_hold"]),
        ("creator_status desconhecido",   [t for t in closed if not t["creator_token_status"]]),
    ]
    sections += bucket_table("Creator status (dev ainda segura?)", splits_creator)

    # 7. bot_degen_rate
    splits_bot = [
        ("<20% bots",   [t for t in closed if t["bot_degen_rate"] is not None and t["bot_degen_rate"] < 0.20]),
        ("20–35% bots", [t for t in closed if t["bot_degen_rate"] is not None and 0.20 <= t["bot_degen_rate"] < 0.35]),
        ("35–50% bots", [t for t in closed if t["bot_degen_rate"] is not None and 0.35 <= t["bot_degen_rate"] < 0.50]),
    ]
    sections += bucket_table("Bot degen rate", splits_bot)

    # 8. smart_wallets
    splits_smart = [
        ("smart=1",    [t for t in closed if t["smart_wallets"] == 1]),
        ("smart=2",    [t for t in closed if t["smart_wallets"] == 2]),
        ("smart=3",    [t for t in closed if t["smart_wallets"] == 3]),
        ("smart≥4",    [t for t in closed if t["smart_wallets"] and t["smart_wallets"] >= 4]),
    ]
    sections += bucket_table("Smart wallets count (FILTER_SMART_MIN=1)", splits_smart)

    # 9. liq_migration
    splits_liq = [
        ("liq $10k–20k",  [t for t in closed if t["liq_migration"] and 10000 <= t["liq_migration"] < 20000]),
        ("liq $20k–50k",  [t for t in closed if t["liq_migration"] and 20000 <= t["liq_migration"] < 50000]),
        ("liq $50k–100k", [t for t in closed if t["liq_migration"] and 50000 <= t["liq_migration"] < 100000]),
        ("liq >$100k",    [t for t in closed if t["liq_migration"] and t["liq_migration"] >= 100000]),
    ]
    sections += bucket_table("Liquidez na migração (H7)", splits_liq)

    # 10. has_social / cto_flag
    splits_social = [
        ("Tem rede social",  [t for t in closed if t["has_social"]]),
        ("Sem rede social",  [t for t in closed if not t["has_social"]]),
        ("CTO flag ativo",   [t for t in closed if t["cto_flag"]]),
        ("Sem CTO",          [t for t in closed if not t["cto_flag"]]),
    ]
    sections += bucket_table("Comunidade (social / CTO)", splits_social)

    # 11. top10_holder_rate (FILTER_TOP10_MAX=0.50 atual vs padrão 0.80)
    splits_top10 = [
        ("top10 <30%",    [t for t in closed if t["top10_holder_rate"] is not None and t["top10_holder_rate"] < 0.30]),
        ("top10 30–50%",  [t for t in closed if t["top10_holder_rate"] is not None and 0.30 <= t["top10_holder_rate"] < 0.50]),
        ("top10 50–65%",  [t for t in closed if t["top10_holder_rate"] is not None and 0.50 <= t["top10_holder_rate"] < 0.65]),
        ("top10 65–80%",  [t for t in closed if t["top10_holder_rate"] is not None and 0.65 <= t["top10_holder_rate"] < 0.80]),
        ("top10 ≥80%",    [t for t in closed if t["top10_holder_rate"] is not None and t["top10_holder_rate"] >= 0.80]),
    ]
    sections += bucket_table("Top10 holder rate (FILTER_TOP10_MAX=0.50 atual — padrão 0.80)", splits_top10)

    # 12. top70_sniper_hold_rate (FILTER_SNIPER_MAX=0.05 atual — muito restritivo?)
    splits_sniper = [
        ("sniper <5%",    [t for t in closed if t["top70_sniper_hold_rate"] is not None and t["top70_sniper_hold_rate"] < 0.05]),
        ("sniper 5–15%",  [t for t in closed if t["top70_sniper_hold_rate"] is not None and 0.05 <= t["top70_sniper_hold_rate"] < 0.15]),
        ("sniper 15–30%", [t for t in closed if t["top70_sniper_hold_rate"] is not None and 0.15 <= t["top70_sniper_hold_rate"] < 0.30]),
        ("sniper ≥30%",   [t for t in closed if t["top70_sniper_hold_rate"] is not None and t["top70_sniper_hold_rate"] >= 0.30]),
    ]
    sections += bucket_table("Top70 sniper hold rate (FILTER_SNIPER_MAX=0.05 atual — calibrar?)", splits_sniper)

    # 13. rat_trader_rate (FILTER_RAT_MAX=0.40 atual)
    splits_rat = [
        ("rat <10%",    [t for t in closed if t["rat_trader_rate"] is not None and t["rat_trader_rate"] < 0.10]),
        ("rat 10–20%",  [t for t in closed if t["rat_trader_rate"] is not None and 0.10 <= t["rat_trader_rate"] < 0.20]),
        ("rat 20–40%",  [t for t in closed if t["rat_trader_rate"] is not None and 0.20 <= t["rat_trader_rate"] < 0.40]),
        ("rat ≥40%",    [t for t in closed if t["rat_trader_rate"] is not None and t["rat_trader_rate"] >= 0.40]),
    ]
    sections += bucket_table("Rat trader rate (FILTER_RAT_MAX=0.40 atual)", splits_rat)

    # 14. rug_ratio (bundler — FILTER_BUNDLER_MAX=0.60 atual)
    splits_rug = [
        ("bundler <10%",  [t for t in closed if t["rug_ratio"] is not None and t["rug_ratio"] < 0.10]),
        ("bundler 10–30%",[t for t in closed if t["rug_ratio"] is not None and 0.10 <= t["rug_ratio"] < 0.30]),
        ("bundler 30–60%",[t for t in closed if t["rug_ratio"] is not None and 0.30 <= t["rug_ratio"] < 0.60]),
        ("bundler ≥60%",  [t for t in closed if t["rug_ratio"] is not None and t["rug_ratio"] >= 0.60]),
    ]
    sections += bucket_table("Rug ratio / bundler rate (FILTER_BUNDLER_MAX=0.60 atual)", splits_rug)

    # ── Tabela individual de todos os trades ──────────────────────────────
    def _fp(v, mult=100): return f"{v*mult:+.1f}%" if v is not None else "—"
    def _fn(v): return f"{v:.0%}" if v is not None else "—"

    rows_html = ""
    for t in sorted(closed, key=lambda x: x["id"]):
        outcome_cls = "green" if is_tp(t) else "red"
        outcome_txt = "TP ✅" if is_tp(t) else "SL 💀"
        pnl = _fp(t.get("pnl_pct_real"))
        h9_r = f"{t['h9_best_ratio']:.2f}" if t["h9_best_ratio"] else "—"
        rows_html += f"""<tr>
<td>{t['id']}</td>
<td><b>{t['symbol'] or t['mint'][:8]}</b></td>
<td class='{outcome_cls}'>{outcome_txt}</td>
<td class='{outcome_cls}'>{pnl}</td>
<td>{t['b_bounce_pct']*100:.0f}% if t['b_bounce_pct'] else '—'</td>
<td>{t['b_elapsed_min']:.0f}m if t['b_elapsed_min'] is not None else '—'</td>
<td>{t['b_drop_pct']*100:.0f}% if t['b_drop_pct'] else '—'</td>
<td>{t['time_to_bottom']:.0f}m if t['time_to_bottom'] is not None else '—'</td>
<td>{t['creator_token_status'] or '—'}</td>
<td>{int(t['n_h9_signals'] or 0)}</td>
<td>{h9_r}</td>
<td>{_fn(t.get('bot_degen_rate'))}</td>
<td>{t.get('smart_wallets') or '—'}</td>
</tr>"""

    # Fix: use proper Python f-string for table cells
    rows_html2 = ""
    for t in sorted(closed, key=lambda x: x["id"]):
        outcome_cls = "green" if is_tp(t) else "red"
        outcome_txt = "TP ✅" if is_tp(t) else "SL 💀"
        pnl = _fp(t.get("pnl_pct_real"))
        h9_r = f"{t['h9_best_ratio']:.2f}" if t["h9_best_ratio"] else "—"
        bp   = f"{t['b_bounce_pct']*100:.0f}%" if t.get("b_bounce_pct") else "—"
        el   = f"{t['b_elapsed_min']:.0f}m"     if t.get("b_elapsed_min") is not None else "—"
        dp   = f"{t['b_drop_pct']*100:.0f}%"   if t.get("b_drop_pct") else "—"
        ttb  = f"{t['time_to_bottom']:.0f}m"   if t.get("time_to_bottom") is not None else "—"
        rows_html2 += f"""<tr>
<td>{t['id']}</td>
<td><b>{t['symbol'] or t['mint'][:8]}</b></td>
<td class='{outcome_cls}'>{outcome_txt}</td>
<td class='{outcome_cls}'>{pnl}</td>
<td>{bp}</td><td>{el}</td><td>{dp}</td><td>{ttb}</td>
<td>{t['creator_token_status'] or '—'}</td>
<td>{int(t['n_h9_signals'] or 0)}</td>
<td>{h9_r}</td>
<td>{_fn(t.get('bot_degen_rate'))}</td>
<td>{t.get('smart_wallets') or '—'}</td>
</tr>"""

    _CSS = """<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Courier New',monospace;background:#0d1117;color:#c9d1d9;font-size:12px}
h1{padding:14px 16px;color:#58a6ff;font-size:16px;border-bottom:1px solid #21262d}
h2{font-size:13px;color:#58a6ff;padding:8px 0 4px}
.grid{display:flex;gap:10px;padding:8px 16px;flex-wrap:wrap;margin-bottom:8px}
.card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 14px;min-width:110px}
.lbl{color:#8b949e;font-size:10px;margin-bottom:2px}
.val{font-size:18px;font-weight:bold}
.green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}.muted{color:#8b949e}
.block{padding:6px 16px 14px;border-bottom:1px solid #21262d}
table{width:100%;border-collapse:collapse;margin:6px 0}
th{background:#161b22;color:#8b949e;padding:4px 8px;text-align:left;border-bottom:1px solid #30363d;font-size:10px}
td{padding:3px 8px;border-bottom:1px solid #21262d;font-size:11px}
tr:hover td{background:#161b22}
.nav{padding:8px 16px;display:flex;gap:12px;border-bottom:1px solid #21262d}
.nav a{color:#58a6ff;font-size:11px;text-decoration:none}
.insight{background:#0d2137;color:#79c0ff;padding:8px 14px;margin:6px 16px;border-radius:4px;font-size:11px;border-left:3px solid #58a6ff}
.warn{background:#3d2200;color:#d29922;padding:8px 14px;margin:6px 16px;border-radius:4px;font-size:11px}
.filt{padding:6px 16px;color:#8b949e;font-size:11px}
</style>"""

    # Find the highest-delta discriminator for the summary box
    all_splits = [
        ("bounce 15-20%",      [t for t in closed if t["b_bounce_pct"] and 0.15 <= t["b_bounce_pct"] < 0.20]),
        ("bounce 30-50%",      [t for t in closed if t["b_bounce_pct"] and 0.30 <= t["b_bounce_pct"] < 0.50]),
        ("elapsed <5m",        [t for t in closed if t["b_elapsed_min"] is not None and t["b_elapsed_min"] < 5]),
        ("elapsed 5-10m",      [t for t in closed if t["b_elapsed_min"] is not None and 5 <= t["b_elapsed_min"] < 10]),
        ("H9 ratio<0.80",      [t for t in closed if t["h9_best_ratio"] and t["h9_best_ratio"] < 0.80]),
        ("creator_close",      [t for t in closed if t["creator_token_status"] == "creator_close"]),
        ("creator_hold",       [t for t in closed if t["creator_token_status"] == "creator_hold"]),
        ("ttb <10m",           [t for t in closed if t["time_to_bottom"] is not None and t["time_to_bottom"] < 10]),
        ("ttb 15-20m",         [t for t in closed if t["time_to_bottom"] is not None and 15 <= t["time_to_bottom"] < 20]),
        ("bot <20%",           [t for t in closed if t["bot_degen_rate"] is not None and t["bot_degen_rate"] < 0.20]),
        ("drop 70-85%",        [t for t in closed if t["b_drop_pct"] and 0.70 <= t["b_drop_pct"] < 0.85]),
        ("tem social",         [t for t in closed if t["has_social"]]),
        ("sem social",         [t for t in closed if not t["has_social"]]),
        ("top10 <50%",         [t for t in closed if t["top10_holder_rate"] is not None and t["top10_holder_rate"] < 0.50]),
        ("top10 >=50%",        [t for t in closed if t["top10_holder_rate"] is not None and t["top10_holder_rate"] >= 0.50]),
        ("sniper <5%",         [t for t in closed if t["top70_sniper_hold_rate"] is not None and t["top70_sniper_hold_rate"] < 0.05]),
        ("sniper 5-30%",       [t for t in closed if t["top70_sniper_hold_rate"] is not None and 0.05 <= t["top70_sniper_hold_rate"] < 0.30]),
        ("rat <20%",           [t for t in closed if t["rat_trader_rate"] is not None and t["rat_trader_rate"] < 0.20]),
        ("rat >=20%",          [t for t in closed if t["rat_trader_rate"] is not None and t["rat_trader_rate"] >= 0.20]),
    ]
    best_label, best_delta, best_wr, best_n = "", 0, 0, 0
    for label, rows in all_splits:
        w, n = wr_bucket(rows)
        if n >= 5 and w is not None:
            delta = abs(w - wr_base)
            if delta > best_delta:
                best_delta, best_label, best_wr, best_n = delta, label, w, n

    # Status distribution HTML
    sd_rows = "".join(
        f"<tr><td>{r['status']}</td><td>{r['exit_type'] or '—'}</td><td>{r['n']}</td>"
        f"<td class='{'yellow' if r['status'] in ('trailing_active','tp_hit') else ('red' if r['status'] in ('sl','sl_hit') else 'muted')}'>"
        f"{'TP' if r['status'] in ('trailing_active','tp_hit') else ('SL' if r['status'] in ('sl','sl_hit') else r['status'])}</td></tr>"
        for r in status_dist
    )

    since_display = since or "todos os trades"
    h = f"""<!DOCTYPE html><html><head><meta charset='utf-8'><title>WR Breakdown</title>{_CSS}</head><body>
<h1>WR Breakdown — Discriminadores de TP vs SL</h1>
<div class='nav'>
  <a href='/trades'>Trades</a>
  <a href='/analise'>Analise</a>
  <a href='/analise/sl-investigation'>SL Investigation</a>
  <a href='/analise/h9'>H9</a>
  <a href='?since=2026-05-10'>Pós-fix (since 2026-05-10)</a>
  <a href='?'>Todos</a>
</div>
<div class='filt'>Filtro ativo: <b>{since_display}</b> | {total} trades fechados (TP+SL) | Baseline WR: <b>{wr_base*100:.1f}%</b> ({n_tp} TP / {n_sl} SL){f" | <span class='yellow'>⏳ {len(open_trades)} pendentes</span>" if open_trades else ""}{f" | <span class='red'>👻 {len(ghost_trades)} sem classificação (expired+pnl=NULL)</span>" if ghost_trades else ""}</div>
<div class='block'>
<h2>Distribuição de status no DB (diagnóstico)</h2>
<table><tr><th>status</th><th>exit_type</th><th>n</th><th>tipo</th></tr>{sd_rows}</table>
<p style='color:#8b949e;font-size:10px;padding:4px 0'>expired com pnl&gt;0 = trailing_active que o monitor age-out sobrescreveu (bug corrigido no db.py)</p>
</div>
"""

    if best_label:
        direction = "positivo" if best_wr > wr_base else "negativo"
        h += f"""<div class='insight'>
🔍 <b>Maior discriminador encontrado (n≥5):</b> <b>{best_label}</b> → WR {best_wr*100:.1f}% vs baseline {wr_base*100:.1f}%
({best_delta*100:+.1f}pp, n={best_n}). Efeito <b>{direction}</b>.
</div>"""

    h += sections

    # Rows for open/pending trades
    open_rows_html = ""
    for t in open_trades:
        age_min = ""
        if t.get("created_at"):
            from datetime import datetime, timezone as _tz
            try:
                age_s = (datetime.now(_tz.utc) - t["created_at"].replace(tzinfo=_tz.utc) if t["created_at"].tzinfo is None else datetime.now(_tz.utc) - t["created_at"]).total_seconds()
                age_min = f"{age_s/60:.0f}m atrás"
            except Exception:
                pass
        bp   = f"{t['b_bounce_pct']*100:.0f}%" if t.get("b_bounce_pct") else "—"
        el   = f"{t['b_elapsed_min']:.0f}m"     if t.get("b_elapsed_min") is not None else "—"
        dp   = f"{t['b_drop_pct']*100:.0f}%"   if t.get("b_drop_pct") else "—"
        ttb  = f"{t['time_to_bottom']:.0f}m"   if t.get("time_to_bottom") is not None else "—"
        h9_r = f"{t['h9_best_ratio']:.2f}" if t.get("h9_best_ratio") else "—"
        open_rows_html += f"""<tr>
<td>{t['id']}</td>
<td><b>{t['symbol'] or t['mint'][:8]}</b></td>
<td class='yellow'>⏳ {t['status']}</td>
<td class='muted'>{age_min}</td>
<td>{bp}</td><td>{el}</td><td>{dp}</td><td>{ttb}</td>
<td>{t['creator_token_status'] or '—'}</td>
<td>{int(t['n_h9_signals'] or 0)}</td>
<td>{h9_r}</td>
<td>—</td>
<td>{t.get('smart_wallets') or '—'}</td>
</tr>"""

    # Ghost trades rows (expired sem pnl — TradeMonitor não detectou saída do trailing)
    ghost_rows_html = ""
    for t in ghost_trades:
        bp   = f"{t['b_bounce_pct']*100:.0f}%" if t.get("b_bounce_pct") else "—"
        el   = f"{t['b_elapsed_min']:.0f}m"     if t.get("b_elapsed_min") is not None else "—"
        dp   = f"{t['b_drop_pct']*100:.0f}%"   if t.get("b_drop_pct") else "—"
        ttb  = f"{t['time_to_bottom']:.0f}m"   if t.get("time_to_bottom") is not None else "—"
        h9_r = f"{t['h9_best_ratio']:.2f}" if t.get("h9_best_ratio") else "—"
        ghost_rows_html += f"""<tr>
<td>{t['id']}</td>
<td><b>{t['symbol'] or t['mint'][:8]}</b></td>
<td class='red'>👻 expired/sem pnl</td>
<td class='muted'>—</td>
<td>{bp}</td><td>{el}</td><td>{dp}</td><td>{ttb}</td>
<td>{t['creator_token_status'] or '—'}</td>
<td>{int(t['n_h9_signals'] or 0)}</td>
<td>{h9_r}</td>
<td>—</td>
<td>{t.get('smart_wallets') or '—'}</td>
</tr>"""

    pending_section = ""
    if open_trades:
        pending_section = f"""<div class='block'>
<h2>⏳ Trades pendentes — status DB não atualizado ({len(open_trades)})</h2>
<p style='color:#8b949e;font-size:10px;padding:4px 0 6px'>GMGN já pode ter executado TP/SL. TradeMonitor atualiza via token_ticks (30s). Não incluídos no WR baseline acima.</p>
<table>
<tr><th>#</th><th>Symbol</th><th>Status DB</th><th>Idade</th><th>Bounce%</th><th>Elapsed</th>
<th>Drop%</th><th>TTB</th><th>Creator</th><th>H9 n</th><th>H9 ratio</th><th>Bots</th><th>Smart</th></tr>
{open_rows_html}
</table></div>"""

    ghost_section = ""
    if ghost_trades:
        ghost_section = f"""<div class='block'>
<h2>👻 Sem classificação — expired + pnl=NULL ({len(ghost_trades)})</h2>
<p style='color:#8b949e;font-size:10px;padding:4px 0 6px'>Esses trades tiveram trailing_active mas o TradeMonitor não detectou a saída (sem ticks após 60min ou fora da janela quente). GMGN fechou, DB não sabe o resultado. Verifique no GMGN se foram TP ou SL para calibrar o WR real.</p>
<table>
<tr><th>#</th><th>Symbol</th><th>Status DB</th><th>—</th><th>Bounce%</th><th>Elapsed</th>
<th>Drop%</th><th>TTB</th><th>Creator</th><th>H9 n</th><th>H9 ratio</th><th>Bots</th><th>Smart</th></tr>
{ghost_rows_html}
</table></div>"""

    h += f"""<div class='block'>
<h2>Trades fechados ({total})</h2>
<table>
<tr><th>#</th><th>Symbol</th><th>Outcome</th><th>P&L</th><th>Bounce%</th><th>Elapsed</th>
<th>Drop%</th><th>TTB</th><th>Creator</th><th>H9 n</th><th>H9 ratio</th><th>Bots</th><th>Smart</th></tr>
{rows_html2}
</table></div>
{pending_section}
{ghost_section}
</body></html>"""
    return h




@app.route("/debug/kline-live")
def debug_kline_live():
    """
    Diagnóstico: GMGN retorna candles parciais (não fechados) no kline?

    Parâmetros:
      mint=<mint>   token a consultar (obrigatório, ou usa um hot token ativo)
      window=120    segundos de lookback (padrão 120s)

    Resposta mostra:
      - Candles retornados com timestamps e OHLCV
      - Se o último candle está dentro do minuto atual (parcial) ou não
      - Comparação low do último candle vs preço atual (trenches mc_last)
    """
    import traceback, time as _time
    from datetime import datetime, timezone

    try:
        mint = request.args.get("mint", "").strip()
        window_sec = int(request.args.get("window", 120))

        # Coleta todos os tokens quentes
        hot_mints = []
        if scanner:
            with scanner.hot._lock:
                hot_mints = list(scanner.hot._tokens.keys())

        now_ts = int(_time.time())
        from_ts = now_ts - window_sec
        to_ts   = now_ts + 120

        lines = []
        lines.append(f"=== /debug/kline-live ===")
        lines.append(f"Agora UTC : {datetime.utcnow().strftime('%H:%M:%S')}")
        lines.append(f"Tokens quentes ativos: {len(hot_mints)}")
        lines.append(f"Janela    : {window_sec}s lookback + 120s fwd")
        lines.append("")

        # Se mint explícito, usa ele; senão tenta todos até achar um com candles
        candidates = [mint] if mint else hot_mints
        if not candidates:
            return "<pre>Nenhum token ativo no HotMonitor. Passe ?mint=... explicitamente.</pre>", 400

        # Testa cada candidato para achar um com dados kline
        chosen_mint   = None
        candles       = []
        skipped       = []
        for m in candidates:
            c = gmgn.get_token_kline(m, "sol", "1m", from_ts=from_ts, to_ts=to_ts)
            if c:
                chosen_mint = m
                candles     = c
                break
            skipped.append(m)

        if skipped:
            lines.append(f"Tokens sem kline (Meteora DAMM v2?): {len(skipped)}")
            for m in skipped:
                lines.append(f"  ✗ {m}")
            lines.append("")

        if not chosen_mint:
            lines.append("⚠️  Nenhum dos tokens quentes tem dados kline disponíveis agora.")
            lines.append("Possíveis causas:")
            lines.append("  • Todos são Meteora DAMM v2 (sem kline histórico)")
            lines.append("  • Tokens muito novos (kline demora alguns minutos para aparecer)")
            lines.append("  • Janela muito curta — tente ?window=3600")
            return "<pre style='font-family:monospace;font-size:13px'>" + "\n".join(lines) + "</pre>"

        mint = chosen_mint
        lines.append(f"Mint com kline: {mint}")
        lines.append("")

        # Busca estado do HotMonitor para o mint escolhido
        mc_last_hot = None
        mc_min_hot  = None
        ts_min_hot  = None
        supply      = None
        if scanner:
            with scanner.hot._lock:
                state = scanner.hot._tokens.get(mint)
            if state:
                mc_last_hot = state.get("mc_last") or state.get("_mc_live")
                mc_min_hot  = state.get("mc_min")
                ts_min_hot  = state.get("ts_min")
                supply      = state.get("total_supply")

        lines.append(f"Candles retornados: {len(candles)}")
        lines.append("")

        if not candles:
            lines.append("⚠️  Nenhum candle retornado.")
        else:
            now_minute_floor = (now_ts // 60) * 60  # início do minuto atual (UTC)
            prev_minute_floor = now_minute_floor - 60

            lines.append(f"Minuto atual começa em : {now_minute_floor}  ({datetime.utcfromtimestamp(now_minute_floor).strftime('%H:%M:%S')} UTC)")
            lines.append(f"Minuto anterior         : {prev_minute_floor}  ({datetime.utcfromtimestamp(prev_minute_floor).strftime('%H:%M:%S')} UTC)")
            lines.append("")

            header = f"{'#':>3}  {'candle_ts':>12}  {'UTC':>8}  {'open':>12}  {'high':>12}  {'low':>12}  {'close':>12}  {'vol':>10}  {'status':>15}"
            lines.append(header)
            lines.append("-" * len(header))

            for i, c in enumerate(candles):
                raw_ts   = int(c.get("time", 0))
                # GMGN kline time é em ms
                c_ts_ms  = raw_ts if raw_ts > 1e10 else raw_ts * 1000
                c_ts_sec = c_ts_ms // 1000
                c_dt     = datetime.utcfromtimestamp(c_ts_sec).strftime("%H:%M:%S")

                if c_ts_sec >= now_minute_floor:
                    status = "⚡ PARCIAL (atual)"
                elif c_ts_sec >= prev_minute_floor:
                    status = "✅ fechado (-1min)"
                else:
                    status = f"closed ({(now_ts - c_ts_sec)//60}min atrás)"

                o = c.get("open",   "?")
                h = c.get("high",   "?")
                l = c.get("low",    "?")
                cl= c.get("close",  "?")
                v = c.get("volume", "?")
                lines.append(f"{i+1:>3}  {c_ts_sec:>12}  {c_dt:>8}  {o:>12}  {h:>12}  {l:>12}  {cl:>12}  {v:>10}  {status:>15}")

            # Análise do último candle
            last = candles[-1]
            last_ts_raw = int(last.get("time", 0))
            last_ts_ms  = last_ts_raw if last_ts_raw > 1e10 else last_ts_raw * 1000
            last_ts_sec = last_ts_ms // 1000
            is_partial  = last_ts_sec >= now_minute_floor

            lines.append("")
            lines.append("=== ANÁLISE DO ÚLTIMO CANDLE ===")
            lines.append(f"Timestamp : {last_ts_sec}  ({datetime.utcfromtimestamp(last_ts_sec).strftime('%H:%M:%S')} UTC)")
            lines.append(f"Parcial?  : {'✅ SIM — candle ainda não fechou' if is_partial else '❌ NÃO — candle já fechado'}")
            lines.append(f"  → Idade : {now_ts - last_ts_sec}s atrás")
            lines.append(f"  → Secs dentro do minuto: {now_ts % 60}s decorridos no minuto atual")

            last_low   = last.get("low")
            last_close = last.get("close")

            lines.append("")
            lines.append("=== COMPARAÇÃO PREÇO ===")

            if supply:
                try:
                    low_mc   = float(last_low)   * supply if last_low   else None
                    close_mc = float(last_close) * supply if last_close else None
                    lines.append(f"Último candle low   : {last_low}  → MC = ${low_mc:,.0f}" if low_mc else "low: N/A")
                    lines.append(f"Último candle close : {last_close}  → MC = ${close_mc:,.0f}" if close_mc else "close: N/A")
                    if mc_last_hot:
                        lines.append(f"HotMonitor mc_last  : ${mc_last_hot:,.0f}  (trenches poll)")
                    if mc_min_hot:
                        lines.append(f"HotMonitor mc_min   : ${mc_min_hot:,.0f}  (mínimo registrado)")
                    if ts_min_hot:
                        age_min = (now_ts - ts_min_hot.timestamp()) if hasattr(ts_min_hot, "timestamp") else "?"
                        lines.append(f"ts_min age          : {age_min:.0f}s atrás" if isinstance(age_min, float) else f"ts_min: {ts_min_hot}")

                    if is_partial and low_mc and mc_last_hot:
                        delta_pct = (mc_last_hot - low_mc) / low_mc * 100
                        if low_mc < mc_last_hot:
                            lines.append(f"\n🔴 kline low (${low_mc:,.0f}) < trenches close (${mc_last_hot:,.0f})")
                            lines.append(f"   → Diferença: +{delta_pct:.1f}% — kline JÁ captura intra-candle lows!")
                            lines.append(f"   → GMGN retorna candle parcial com low real-time ✅")
                        elif low_mc > mc_last_hot:
                            lines.append(f"\n🟡 kline low (${low_mc:,.0f}) > trenches close (${mc_last_hot:,.0f})")
                            lines.append(f"   → Trenches é mais baixo — kline NÃO captura o intra-candle low atual")
                        else:
                            lines.append(f"\n⚪ kline low ≈ trenches close — valores idênticos")
                    elif not is_partial:
                        lines.append(f"\nℹ️  Último candle é fechado — não diz nada sobre candles parciais.")
                        lines.append(f"   Tente novamente em alguns segundos para ver se GMGN inclui o candle atual.")
                except Exception as e:
                    lines.append(f"Erro ao calcular MC: {e}")
            else:
                lines.append(f"Último candle low   : {last_low}")
                lines.append(f"Último candle close : {last_close}")
                lines.append(f"Supply não disponível — token não está no HotMonitor")
                if mc_last_hot:
                    lines.append(f"HotMonitor mc_last  : ${mc_last_hot:,.0f}")

            lines.append("")
            lines.append("=== CONCLUSÃO ===")
            if is_partial:
                lines.append("GMGN retorna o candle do minuto corrente (parcial/não fechado).")
                lines.append("Isso significa que o 'low' desse candle é o intra-candle low em tempo real.")
                lines.append("O _precise_bottom_tick já processa esse low a cada HOT_PRECISE_SEC=5s.")
            else:
                lines.append("GMGN NÃO retornou o candle do minuto atual.")
                lines.append("O kline só mostra candles fechados — sem intra-candle precision.")
                lines.append("Para V-shapes dentro de 1 candle, só trenches poll (5s, close) ou WebSocket ajuda.")

        return "<pre style='font-family:monospace;font-size:13px'>" + "\n".join(lines) + "</pre>"

    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


@app.route("/analise/wbottom")
def analise_wbottom():
    """
    Hipótese W-bottom: o preço retesta o fundo após a entrada?
    Para cada SL, mostra onde o preço foi depois da entrada vs mc_bottom.
    Responde: SL ancorado no fundo (em vez de % fixo da entrada) teria sobrevivido?
    """
    import traceback
    try:
        FLOOR_PCT = float(request.args.get("floor", "0.90"))  # SL floor = mc_bottom × floor_pct

        with db._cursor() as cur:
            # Diagnóstico: conta trades por status e quais têm os campos necessários
            cur.execute("""
                SELECT
                    status,
                    COUNT(*) AS total,
                    COUNT(mc_bottom) AS has_mc_bottom,
                    COUNT(entry_price_usd) AS has_entry_usd,
                    COUNT(tokens_received) AS has_tokens,
                    COUNT(ts_signal) AS has_ts_signal,
                    COUNT(entry_expected) AS has_entry_expected
                FROM trades
                GROUP BY status
                ORDER BY status
            """)
            diag = [dict(r) for r in cur.fetchall()]

            # Busca trades — relaxado: aceita entry_expected como fallback de entry_mc_real
            cur.execute("""
                SELECT
                    t.id,
                    t.symbol,
                    t.mint,
                    t.status,
                    t.mc_bottom,
                    t.entry_expected,
                    t.entry_price_usd,
                    t.tokens_received,
                    t.ts_signal,
                    t.pnl_pct_real,
                    t.sl_pct,
                    t.tp_pct,
                    t.amount_usd
                FROM trades t
                WHERE t.status IN ('sl', 'expired', 'tp', 'trailing_active')
                  AND t.mc_bottom IS NOT NULL AND t.mc_bottom > 0
                  AND t.ts_signal IS NOT NULL
                  AND (t.entry_price_usd IS NOT NULL OR t.entry_expected IS NOT NULL)
                ORDER BY t.created_at DESC
                LIMIT 200
            """)
            trades_raw = [dict(r) for r in cur.fetchall()]

        if not trades_raw:
            diag_html = "<br>".join(
                f"status={d['status']}: total={d['total']} mc_bottom={d['has_mc_bottom']} "
                f"entry_usd={d['has_entry_usd']} tokens={d['has_tokens']} "
                f"ts_signal={d['has_ts_signal']} entry_expected={d['has_entry_expected']}"
                for d in diag
            )
            return f"<pre>Sem trades com dados suficientes.\n\nDiagnóstico:\n{diag_html}</pre>"

        # Busca mínimo MC após entrada para cada trade (nos 30min seguintes)
        min_mc_map = {}
        for tr in trades_raw:
            with db._cursor() as cur:
                cur.execute("""
                    SELECT MIN(COALESCE(mc_low, mc)) AS min_mc
                    FROM token_ticks
                    WHERE mint = %(mint)s
                      AND tick_ts > %(ts)s
                      AND tick_ts < %(ts)s + INTERVAL '30 minutes'
                """, {"mint": tr["mint"], "ts": tr["ts_signal"]})
                row = cur.fetchone()
                min_mc_map[tr["id"]] = row["min_mc"] if row and row["min_mc"] else None

        rows = []
        n_no_ticks = 0
        n_survived  = 0  # SL trades where floor-anchor would have survived
        n_sl_total  = 0

        for t in trades_raw:
            # entry_mc_real: preferir price/tokens (mais preciso), fallback entry_expected
            if t["entry_price_usd"] and t["tokens_received"] and t["tokens_received"] > 0:
                entry_mc_real = (t["entry_price_usd"] / t["tokens_received"]) * 1_000_000_000
            elif t["entry_expected"]:
                entry_mc_real = t["entry_expected"]
            else:
                entry_mc_real = None
            mc_bottom     = t["mc_bottom"]
            min_after     = min_mc_map.get(t["id"])
            is_sl         = t["status"] in ("sl", "expired") and (t.get("pnl_pct_real") or 0) < 0

            if is_sl:
                n_sl_total += 1

            if min_after is None:
                n_no_ticks += 1
                retest_depth = None
                retest_ratio = None
                floor_survives = None
            else:
                retest_depth  = (entry_mc_real - min_after) / entry_mc_real if entry_mc_real else None
                retest_ratio  = min_after / mc_bottom if mc_bottom > 0 else None
                sl_floor_mc   = mc_bottom * FLOOR_PCT
                floor_survives = min_after > sl_floor_mc

                if is_sl and floor_survives:
                    n_survived += 1

            rows.append({
                **t,
                "entry_mc_real":  entry_mc_real,
                "min_after":      min_after,
                "retest_depth":   retest_depth,
                "retest_ratio":   retest_ratio,
                "floor_survives": floor_survives,
                "is_sl":          is_sl,
            })

        n_sl_with_ticks = sum(1 for r in rows if r["is_sl"] and r["min_after"] is not None)
        pct_survived    = n_survived / n_sl_with_ticks * 100 if n_sl_with_ticks else 0

        def _pct(v):
            return f"{v*100:.1f}%" if v is not None else "—"
        def _mc(v):
            return f"${v:,.0f}" if v is not None else "—"
        def _color_ratio(v):
            if v is None: return ""
            if v >= 1.0:  return "color:#f85149"   # abaixo do fundo = rug
            if v >= 0.90: return "color:#e3b341"   # zona critica
            return "color:#3fb950"                  # sobreviveria anchor

        CSS = "<style>body{background:#0d1117;color:#e6edf3;font:13px/1.5 monospace;padding:20px}table{border-collapse:collapse;width:100%}th{background:#161b22;padding:6px 10px;text-align:left;font-size:11px;color:#8b949e}td{padding:5px 10px;border-bottom:1px solid #21262d;font-size:12px}.green{color:#3fb950}.red{color:#f85149}.yellow{color:#e3b341}.nav a{color:#58a6ff;margin-right:16px;text-decoration:none}.big{font-size:28px;font-weight:bold;margin:8px 0}.card{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:16px;margin:12px 0;display:inline-block;min-width:180px;margin-right:12px;vertical-align:top}</style>"

        # Cards de resumo
        cards = f"""
        <div class='card'>
          <div style='color:#8b949e;font-size:11px'>SL trades analisados</div>
          <div class='big'>{n_sl_with_ticks}</div>
          <div style='color:#8b949e;font-size:11px'>com ticks disponíveis ({n_no_ticks} sem ticks)</div>
        </div>
        <div class='card'>
          <div style='color:#8b949e;font-size:11px'>Anchor floor = mc_bottom × {FLOOR_PCT:.0%}</div>
          <div class='big {'green' if pct_survived > 50 else 'red'}'>{pct_survived:.0f}%</div>
          <div style='color:#8b949e;font-size:11px'>dos SLs teriam sobrevivido</div>
        </div>
        <div class='card'>
          <div style='color:#8b949e;font-size:11px'>SLs que teriam virado TP</div>
          <div class='big green'>{n_survived}</div>
          <div style='color:#8b949e;font-size:11px'>se anchor sobrevive → assumindo recuperação</div>
        </div>
        """

        # Tabela detalhada
        table_rows = ""
        for r in sorted(rows, key=lambda x: x["is_sl"], reverse=True):
            status_label = "🔴 SL" if r["is_sl"] else ("🟢 TP" if r["status"] == "tp" else "🟡 " + r["status"])
            floor_label  = ""
            if r["floor_survives"] is not None:
                floor_label = "<span class='green'>✓ sobrevive</span>" if r["floor_survives"] else "<span class='red'>✗ rug</span>"

            table_rows += f"""<tr>
              <td>{r['symbol']}</td>
              <td>{status_label}</td>
              <td>{_mc(r['mc_bottom'])}</td>
              <td>{_mc(r['entry_mc_real'])}</td>
              <td>{_pct((r['entry_mc_real'] - r['mc_bottom']) / r['mc_bottom'] if (r['entry_mc_real'] and r['mc_bottom']) else None)}</td>
              <td>{_mc(r['min_after'])}</td>
              <td style='{_color_ratio(r["retest_ratio"])}'>{_pct(r["retest_depth"])}</td>
              <td style='{_color_ratio(r["retest_ratio"])}'>{f"{r['retest_ratio']:.2f}×" if r["retest_ratio"] else "—"}</td>
              <td>{floor_label}</td>
              <td class='{"green" if (r.get("pnl_pct_real") or 0) > 0 else "red"}'>{_pct(r.get("pnl_pct_real"))}</td>
            </tr>"""

        html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'><title>W-Bottom Analysis</title>{CSS}</head><body>
<h1>📊 Hipótese W-Bottom — Retest do Fundo Após Entrada</h1>
<div class='nav'>
  <a href='/trades'>Trades</a>
  <a href='/analise/wr-breakdown'>WR Breakdown</a>
  <a href='?floor=0.85'>floor 85%</a>
  <a href='?floor=0.90'>floor 90%</a>
  <a href='?floor=0.95'>floor 95%</a>
</div>

<p style='color:#8b949e;margin:12px 0'>
  Pergunta: <b style='color:#e6edf3'>nos SLs, o preço chegou a cair abaixo de mc_bottom × {FLOOR_PCT:.0%}?</b><br>
  Se a maioria ficou <i>acima</i> desse floor → hipótese confirmada → SL ancorado no fundo resolve.
</p>

{cards}

<h2>Legenda das colunas</h2>
<p style='color:#8b949e;font-size:11px'>
  <b>mc_bottom</b>: fundo detectado pelo bot no momento do sinal |
  <b>entry_mc_real</b>: MC real de execução (entry_price_usd / tokens × 1B) |
  <b>entry acima fundo</b>: quanto entramos acima do fundo |
  <b>min_mc pós-entrada</b>: menor MC visto nos ticks 30min após entrada |
  <b>retest depth</b>: queda do entry_mc_real até o min_mc |
  <b>min/bottom</b>: <span class='green'>≥1.0</span> não passou do fundo · <span class='yellow'>0.90-1.0</span> zona crítica · <span class='red'>&lt;0.90</span> rug real |
  <b>anchor {FLOOR_PCT:.0%}</b>: SL em mc_bottom × {FLOOR_PCT:.0%} teria sobrevivido?
</p>

<table>
  <thead><tr>
    <th>Token</th><th>Status</th><th>mc_bottom</th><th>entry_mc_real</th>
    <th>entry acima fundo</th><th>min_mc pós-entrada</th>
    <th>retest depth</th><th>min/bottom</th>
    <th>anchor {FLOOR_PCT:.0%}</th><th>PnL real</th>
  </tr></thead>
  <tbody>{table_rows}</tbody>
</table>
<p style='color:#8b949e;font-size:11px;margin-top:16px'>
  {n_no_ticks} trades sem ticks — excluídos da análise de retest.
  Ticks disponíveis apenas quando token apareceu no trenches poll durante a janela quente.
</p>
</body></html>"""

        return html

    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


@app.route("/debug/bounce-list")
def debug_bounce_list():
    """Lista tokens com bounce > X% das últimas 48h com links DexScreener/GMGN."""
    import traceback
    try:
        min_bounce = float(request.args.get("bounce", "0.50"))
        hours      = int(request.args.get("hours", "48"))

        with db._cursor() as cur:
            cur.execute("""
                SELECT
                    b.mint,
                    b.symbol,
                    b.ts_signal,
                    b.bounce_pct,
                    b.drop_from_mig,
                    b.elapsed_sec,
                    b.mc_min,
                    b.mc_at_signal,
                    b.mc_migration,
                    t.status   AS trade_status,
                    t.pnl_pct_real
                FROM bounce_signals b
                LEFT JOIN trades t ON t.mint = b.mint
                WHERE b.bounce_pct >= %(min_bounce)s
                  AND b.ts_signal >= NOW() - INTERVAL '%(hours)s hours'
                ORDER BY b.ts_signal DESC
            """, {"min_bounce": min_bounce, "hours": hours})
            rows = [dict(r) for r in cur.fetchall()]

        if not rows:
            return f"<pre>Nenhum bounce ≥ {min_bounce:.0%} nas últimas {hours}h.</pre>"

        def _pct(v):
            return f"{float(v)*100:.0f}%" if v is not None else "—"
        def _mc(v):
            return f"${float(v):,.0f}" if v is not None else "—"
        def _outcome(r):
            s = r.get("trade_status")
            if not s:            return "<span style='color:#8b949e'>sem trade</span>"
            if s == "tp":        return f"<span style='color:#3fb950'>TP {_pct(r['pnl_pct_real'])}</span>"
            if s == "sl":        return f"<span style='color:#f85149'>SL {_pct(r['pnl_pct_real'])}</span>"
            if s == "trailing_active": return "<span style='color:#e3b341'>trailing ativo</span>"
            return f"<span style='color:#8b949e'>{s}</span>"

        CSS = "<style>body{background:#0d1117;color:#e6edf3;font:13px/1.6 monospace;padding:20px}a{color:#58a6ff}table{border-collapse:collapse;width:100%}th{background:#161b22;padding:6px 10px;text-align:left;font-size:11px;color:#8b949e}td{padding:5px 10px;border-bottom:1px solid #21262d;font-size:12px}.nav a{color:#58a6ff;margin-right:16px;text-decoration:none;font-size:12px}</style>"

        rows_html = ""
        for r in rows:
            mint      = r["mint"]
            symbol    = r["symbol"] or mint[:8]
            elapsed   = f"{int(r['elapsed_sec'] or 0)//60}min" if r.get("elapsed_sec") else "—"
            dex_url   = f"https://dexscreener.com/solana/{mint}"
            gmgn_url  = f"https://gmgn.ai/sol/token/{mint}"
            ts        = r["ts_signal"].strftime("%H:%M:%S") if r.get("ts_signal") else "—"

            rows_html += f"""<tr>
              <td>{ts}</td>
              <td><b>{symbol}</b></td>
              <td style='color:#3fb950;font-weight:bold'>{_pct(r['bounce_pct'])}</td>
              <td>{_pct(r['drop_from_mig'])}</td>
              <td>{elapsed}</td>
              <td>{_mc(r['mc_min'])}</td>
              <td>{_mc(r['mc_at_signal'])}</td>
              <td>{_outcome(r)}</td>
              <td>
                <a href='{dex_url}' target='_blank'>DEX</a>
                &nbsp;
                <a href='{gmgn_url}' target='_blank'>GMGN</a>
              </td>
              <td style='font-size:10px;color:#8b949e'>{mint}</td>
            </tr>"""

        html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Bounce List</title>{CSS}
<meta http-equiv='refresh' content='30'>
</head><body>
<h2>Bounces ≥ {min_bounce:.0%} — últimas {hours}h</h2>
<div class='nav'>
  <a href='?bounce=0.30'>≥30%</a>
  <a href='?bounce=0.50'>≥50%</a>
  <a href='?bounce=0.75'>≥75%</a>
  <a href='?bounce=1.00'>≥100%</a>
  &nbsp;|&nbsp;
  <a href='?bounce={min_bounce}&hours=24'>24h</a>
  <a href='?bounce={min_bounce}&hours=48'>48h</a>
  <a href='?bounce={min_bounce}&hours=168'>7d</a>
</div>
<p style='color:#8b949e;font-size:11px'>{len(rows)} tokens encontrados · auto-refresh 30s</p>
<table>
  <thead><tr>
    <th>Hora</th><th>Token</th><th>Bounce</th><th>Drop mig</th>
    <th>Elapsed</th><th>MC fundo</th><th>MC sinal</th>
    <th>Trade</th><th>Links</th><th>Mint</th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>
</body></html>"""
        return html

    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


@app.route("/debug/fix-trailing-outcomes")
def debug_fix_trailing_outcomes():
    """Corrige outcomes dos 4 trades confirmados como trailing TP pelo usuário,
    e verifica MUSKOSOPHY (não comprado durante manutenção de IP).
    Acionar UMA VEZ — verifica antes de alterar."""
    import traceback
    try:
        # Confirmações do usuário (2026-05-13):
        # HUMANITY #105  → trailing TP, lucro +56%
        # Kevin     #153 → trailing TP, lucro +311%
        # SLEEP     #174 → trailing TP, lucro +41%
        # DrawPoorly#157 → trailing TP, lucro +46%
        # MUSKOSOPHY#13  → NÃO comprado (manutenção IP) — manter como está mas registrar nota
        CONFIRMED_TPS = [
            (105, 0.56,  "HUMANITY"),
            (153, 3.11,  "Kevin"),
            (174, 0.41,  "SLEEP"),
            (157, 0.46,  "DrawPoorly"),
        ]
        results = []
        with db._cursor() as cur:
            for trade_id, pnl, sym in CONFIRMED_TPS:
                cur.execute("SELECT id, symbol, status, pnl_pct_real FROM trades WHERE id=%s", (trade_id,))
                row = cur.fetchone()
                if not row:
                    results.append(f"AVISO: trade #{trade_id} ({sym}) não encontrado no DB")
                    continue
                before = dict(row)
                cur.execute(
                    "UPDATE trades SET status='tp', pnl_pct_real=%s WHERE id=%s",
                    (pnl, trade_id)
                )
                results.append(
                    f"OK #{trade_id} {sym}: {before['status']} {before['pnl_pct_real']} "
                    f"→ tp / {pnl*100:.0f}%"
                )
            # Verifica MUSKOSOPHY #13
            cur.execute("SELECT id, symbol, status, pnl_pct_real, mint FROM trades WHERE id=13")
            row = cur.fetchone()
            if row:
                r = dict(row)
                results.append(
                    f"INFO #13 MUSKOSOPHY: status={r['status']} pnl={r['pnl_pct_real']} "
                    f"mint={r['mint']} — nao alterado (nao comprado, manter como referencia)"
                )

        html_rows = "".join(f"<li style='padding:3px 0'>{r}</li>" for r in results)
        return (f"<html><body style='background:#0d1117;color:#e6edf3;font:13px monospace;padding:20px'>"
                f"<h2 style='color:#58a6ff'>Fix Trailing Outcomes</h2>"
                f"<ul>{html_rows}</ul>"
                f"<p style='color:#8b949e'>Recarregue /analise/trade-autopsy para ver WR atualizado.</p>"
                f"</body></html>")
    except Exception:
        return f"<pre>{traceback.format_exc()}</pre>", 500


@app.route("/debug/trailing-review")
def debug_trailing_review():
    """Lista os trades classificados como 'trailing_falhou' para revisão manual.
    Mostra mint, timestamps, max_gain_before_exit e links para verificar no chart."""
    import traceback
    try:
        CSS = ("<style>body{background:#0d1117;color:#e6edf3;font:13px/1.6 monospace;padding:20px}"
               "a{color:#58a6ff;text-decoration:none}table{border-collapse:collapse;width:100%}"
               "th{background:#161b22;padding:6px 10px;text-align:left;font-size:11px;color:#8b949e}"
               "td{padding:5px 10px;border-bottom:1px solid #21262d;font-size:12px}"
               ".green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}.dim{color:#484f58}"
               "h1{color:#58a6ff;font-size:16px;margin-bottom:4px}"
               "h2{color:#8b949e;font-size:12px;margin:20px 0 6px}"
               "tr:hover td{background:#161b22}"
               ".tag{padding:2px 7px;border-radius:4px;font-size:11px}"
               ".t-red{background:#3d1a1a;color:#f85149}"
               ".t-yellow{background:#3d2f0a;color:#e3b341}"
               ".t-dim{background:#1c2128;color:#8b949e}</style>")

        with db._cursor() as cur:
            cur.execute("""
                WITH base AS (
                    SELECT
                        t.id,
                        t.mint,
                        t.symbol,
                        t.status,
                        t.ts_signal,
                        t.exit_ts,
                        t.tp_pct,
                        t.sl_pct,
                        t.pnl_pct_real,
                        t.entry_expected,
                        COALESCE(
                            CASE WHEN t.entry_price_usd > 0 AND t.tokens_received > 0
                                 THEN t.entry_price_usd / t.tokens_received * 1e9
                            END,
                            t.entry_expected
                        ) AS entry_mc,
                        CASE
                            WHEN t.status IN ('tp','trailing_active')            THEN 'tp'
                            WHEN t.status IN ('sl')                              THEN 'sl'
                            WHEN t.status = 'expired' AND t.pnl_pct_real > 0    THEN 'tp'
                            WHEN t.status = 'expired' AND t.pnl_pct_real <= 0   THEN 'sl'
                            ELSE NULL
                        END AS outcome
                    FROM trades t
                    WHERE t.status NOT IN ('pending','error','open')
                ),
                tick_pre AS (
                    SELECT
                        b.id AS trade_id,
                        COUNT(tt.id) AS pre_tick_count,
                        MAX(tt.mc)   AS max_mc_pre
                    FROM base b
                    JOIN token_ticks tt ON tt.mint = b.mint
                        AND tt.tick_ts >= b.ts_signal
                        AND tt.tick_ts <= b.exit_ts
                    WHERE b.exit_ts IS NOT NULL
                    GROUP BY b.id
                )
                SELECT
                    b.id,
                    b.mint,
                    b.symbol,
                    b.status,
                    b.outcome,
                    b.ts_signal  AT TIME ZONE 'UTC' AT TIME ZONE 'America/Sao_Paulo' AS ts_signal_brt,
                    b.exit_ts    AT TIME ZONE 'UTC' AT TIME ZONE 'America/Sao_Paulo' AS exit_ts_brt,
                    b.tp_pct,
                    b.sl_pct,
                    b.pnl_pct_real,
                    b.entry_mc,
                    tp.pre_tick_count,
                    tp.max_mc_pre,
                    CASE WHEN b.entry_mc > 0 AND tp.max_mc_pre IS NOT NULL
                         THEN (tp.max_mc_pre - b.entry_mc) / b.entry_mc END AS max_gain_before_exit
                FROM base b
                JOIN tick_pre tp ON tp.trade_id = b.id
                WHERE b.outcome = 'sl'
                ORDER BY b.id ASC
            """)
            rows = [dict(r) for r in cur.fetchall()]

        # Filtra apenas trailing_falhou
        trailing_rows = []
        for r in rows:
            tp_pct    = r.get("tp_pct") or 0.75
            pre_gain  = r.get("max_gain_before_exit")
            pre_ticks = r.get("pre_tick_count") or 0
            if pre_gain is not None and pre_gain >= tp_pct:
                r["spike_suspect"] = pre_ticks < 5 and pre_gain > 1.00
                trailing_rows.append(r)

        def _ts(v):
            if v is None: return "—"
            return v.strftime("%m-%d %H:%M:%S") if hasattr(v, "strftime") else str(v)

        def _pct(v):
            if v is None: return "<span class='dim'>—</span>"
            c = "#3fb950" if v > 0 else "#f85149"
            return f"<span style='color:{c}'>{v*100:+.0f}%</span>"

        rows_html = ""
        for i, r in enumerate(trailing_rows, 1):
            mint = r.get("mint","")
            sym  = r.get("symbol","?")
            spike = r.get("spike_suspect", False)
            pt   = r.get("pre_tick_count") or 0
            gain = r.get("max_gain_before_exit")
            tp_pct = r.get("tp_pct") or 0.75

            # Trailing exit esperado = pico × (1 - drawdown 30%) — estimativa
            expected_trail_exit = None
            if gain is not None and r.get("entry_mc"):
                peak_mc = r["entry_mc"] * (1 + gain)
                expected_trail_exit = (peak_mc * 0.70 - r["entry_mc"]) / r["entry_mc"]

            spike_badge = " <span class='tag t-red'>SPIKE?</span>" if spike else ""
            conf_color = "#3fb950" if pt >= 10 else ("#d29922" if pt >= 5 else "#f85149")

            rows_html += (
                f"<tr>"
                f"<td class='dim'>{i}</td>"
                f"<td class='dim'>{r['id']}</td>"
                f"<td><b>{sym}</b>{spike_badge}</td>"
                f"<td class='dim' style='font-size:10px'>{mint[:20]}...</td>"
                f"<td class='dim'>{_ts(r.get('ts_signal_brt'))}</td>"
                f"<td class='dim'>{_ts(r.get('exit_ts_brt'))}</td>"
                f"<td style='color:{conf_color}'>{pt}</td>"
                f"<td>{_pct(gain)}</td>"
                f"<td>{_pct(expected_trail_exit)}</td>"
                f"<td class='red'>{_pct(r.get('pnl_pct_real'))}</td>"
                f"<td>"
                f"<a href='https://dexscreener.com/solana/{mint}' target='_blank'>DS</a> "
                f"<a href='https://gmgn.ai/sol/token/{mint}' target='_blank'>GMGN</a>"
                f"</td>"
                f"</tr>"
            )

        n = len(trailing_rows)
        n_real  = sum(1 for r in trailing_rows if not r.get("spike_suspect"))
        n_spike = sum(1 for r in trailing_rows if r.get("spike_suspect"))

        html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Trailing Review</title>{CSS}
</head><body>
<h1>Trailing Falhou — {n} casos para revisao manual</h1>
<p style='color:#8b949e;font-size:12px'>
  Classificacao: max_gain_before_exit &ge; tp_pct (+75%) enquanto seguravamos, mas exit foi SL.<br>
  <b style='color:#3fb950'>Trailing REAL</b> (ticks &ge; 5): {n_real} — GMGN genuinamente nao executou trailing<br>
  <b style='color:#f85149'>SPIKE?</b> (ticks &lt; 5 + gain &gt; 100%): {n_spike} — preco pode nao ter chegado la de verdade (mc_high spike)<br>
</p>
<p style='color:#484f58;font-size:11px'>
  Ticks = numero de token_ticks entre ts_signal e exit_ts. Poucos ticks = baixa confianca no max_gain.<br>
  Exit esperado = estimativa de onde trailing (30% drawdown do pico) teria saido.<br>
  Timestamps em BRT (America/Sao_Paulo).
</p>
<table>
  <thead><tr>
    <th>#</th><th>Trade ID</th><th>Token</th><th>Mint</th>
    <th>Entrada (BRT)</th><th>Exit (BRT)</th>
    <th>Ticks</th><th>Max gain</th><th>Exit esperado</th><th>PnL real</th>
    <th>Links</th>
  </tr></thead>
  <tbody>{rows_html if rows_html else "<tr><td colspan='11' class='dim'>Nenhum trailing_falhou encontrado</td></tr>"}</tbody>
</table>
<br>
<p style='color:#484f58;font-size:11px'>
  Para cada token: verifique no grafico se o preco realmente chegou a +{75}% a partir do fundo.<br>
  Se sim e o SL bateu = GMGN nao ativou o trailing (bug ou IP da Railway).<br>
  Se nao = mc_high do kline capturou spike momentaneo, preco real nunca chegou la.
</p>
</body></html>"""
        return html

    except Exception:
        return f"<pre>{traceback.format_exc()}</pre>", 500


@app.route("/debug/absorption")
def debug_absorption():
    """
    H22 — Diagnóstico da coleta de absorção em tempo real.
    Mostra:
      1. Tokens ativos no HotMonitor com janela de absorção ao vivo (Helius deque)
      2. Últimos bounce_signals com campos H22 preenchidos (ou NULL)
      3. Stats de cobertura geral
    """
    import traceback
    try:
        CSS = ("<style>body{background:#0d1117;color:#e6edf3;font:13px/1.6 monospace;padding:20px}"
               "a{color:#58a6ff}table{border-collapse:collapse;width:100%}"
               "th{background:#161b22;padding:6px 10px;text-align:left;font-size:11px;color:#8b949e}"
               "td{padding:5px 10px;border-bottom:1px solid #21262d;font-size:12px}"
               ".box{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px 16px;"
               "margin:8px 4px;display:inline-block;vertical-align:top}"
               ".lbl{color:#8b949e;font-size:11px}.val{font-size:20px;font-weight:bold}"
               ".ok{color:#3fb950}.warn{color:#e3b341}.bad{color:#f85149}"
               ".nav a{color:#58a6ff;margin-right:14px;text-decoration:none;font-size:12px}"
               "h3{margin:20px 0 8px;color:#8b949e;font-size:12px;text-transform:uppercase}</style>")
        # ── 1. Tokens ativos com janela de absorção ao vivo ──────────────────
        live_rows_html = ""
        helius_active = False
        if scanner and hasattr(scanner, "hot") and scanner.hot._helius:
            helius_active = True
            with scanner.hot._lock:
                active = dict(scanner.hot._tokens)

            for mint, state in active.items():
                symbol   = state.get("symbol", mint[:8])
                exchange = state.get("exchange") or "?"
                try:
                    w = scanner.hot._helius.get_absorption_window(mint, window_sec=60)
                except Exception:
                    w = {}

                total    = w.get("total_events", 0)
                buy_vol  = w.get("buy_vol_sol",  0) or 0
                sell_vol = w.get("sell_vol_sol", 0) or 0
                buy_cnt  = w.get("buy_count",    0) or 0
                sell_cnt = w.get("sell_count",   0) or 0
                lrg_buy  = w.get("largest_buy",  0) or 0
                ratio    = buy_vol / sell_vol if sell_vol > 0 else None
                conc     = lrg_buy / buy_vol if buy_vol > 0 else None
                event_cls = "ok" if total >= 5 else ("warn" if total >= 1 else "bad")
                ratio_cls = "ok" if (ratio and ratio >= 1.0) else "bad"
                # H22 só captura pump.fun pAMM — Raydium/Meteora/outros = sem dados
                is_pump = "pump" in exchange.lower() or exchange == "?"
                exch_cls = "" if is_pump else "warn"

                if sell_vol == 0 and buy_vol > 0:
                    ratio_str = "<span class='ok'>∞</span>"
                elif sell_vol == 0 and buy_vol == 0:
                    ratio_str = "<span style='color:#484f58'>no flow</span>"
                else:
                    ratio_str = f"<span class='{ratio_cls}'>{ratio:.2f}×</span>"

                conc_str = f"{conc:.2f}" if conc is not None else "—"

                elapsed = int((
                    __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
                    - state["ts_migration"]
                ).total_seconds() / 60)

                dex_url = f"https://dexscreener.com/solana/{mint}"
                live_rows_html += f"""<tr>
                  <td><b>{symbol}</b></td>
                  <td class='{exch_cls}'>{exchange}</td>
                  <td>{elapsed}min</td>
                  <td class='{event_cls}'>{total} eventos</td>
                  <td class='ok'>{buy_vol:.3f} SOL ({buy_cnt}×)</td>
                  <td class='bad'>{sell_vol:.3f} SOL ({sell_cnt}×)</td>
                  <td>{ratio_str}</td>
                  <td>{lrg_buy:.3f} SOL</td>
                  <td style='color:#8b949e'>{conc_str}</td>
                  <td style='font-size:10px;color:#8b949e'>{mint[:12]}…</td>
                  <td><a href='{dex_url}' target='_blank'>DEX</a></td>
                </tr>"""

        if not live_rows_html:
            live_rows_html = "<tr><td colspan='9' style='color:#484f58'>Nenhum token ativo no HotMonitor agora</td></tr>"

        # ── 2. Últimos 30 bounce_signals com métricas brutas H22 ──────────────────────
        with db._cursor() as cur:
            cur.execute("""
                SELECT b.mint, b.symbol, b.exchange, b.ts_signal, b.bounce_pct, b.drop_from_mig,
                       b.absorption_buy_vol, b.absorption_sell_vol,
                       b.absorption_buy_cnt, b.absorption_sell_cnt,
                       b.absorption_lrg_buy,
                       b.absorption_events,
                       t.status AS trade_status
                FROM bounce_signals b
                LEFT JOIN LATERAL (
                    SELECT status FROM trades
                    WHERE mint = b.mint
                    ORDER BY created_at DESC LIMIT 1
                ) t ON true
                ORDER BY b.ts_signal DESC
                LIMIT 30
            """)
            bounces = [dict(r) for r in cur.fetchall()]

            # Stats gerais de cobertura
            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(absorption_events) AS with_data,
                    COUNT(CASE WHEN absorption_events = 0 OR absorption_events IS NULL THEN 1 END) AS no_data
                FROM bounce_signals
            """)
            stats = dict(cur.fetchone())

            # Stats por exchange — quantos bounces por launchpad, cobertura H22
            cur.execute("""
                SELECT
                    COALESCE(exchange, 'unknown') AS exchange,
                    COUNT(*) AS n,
                    COUNT(absorption_events) AS with_h22
                FROM bounce_signals
                GROUP BY exchange
                ORDER BY n DESC
                LIMIT 10
            """)
            exchange_stats = [dict(r) for r in cur.fetchall()]

            # Contagem de trade_absorption_ticks (monitoramento contínuo durante holding)
            cur.execute("""
                SELECT
                    COUNT(*) AS total_ticks,
                    COUNT(DISTINCT trade_id) AS trades_com_ticks,
                    COUNT(DISTINCT mint) AS mints_com_ticks
                FROM trade_absorption_ticks
            """)
            tat_stats = dict(cur.fetchone())

        bounce_rows_html = ""
        for r in bounces:
            ts       = r["ts_signal"].strftime("%m-%d %H:%M") if r.get("ts_signal") else "—"
            symbol   = r.get("symbol") or r["mint"][:8]
            exchange = r.get("exchange") or "?"
            ev       = r.get("absorption_events")
            bv       = r.get("absorption_buy_vol")
            sv       = r.get("absorption_sell_vol")
            bc       = r.get("absorption_buy_cnt")
            sc       = r.get("absorption_sell_cnt")
            lb       = r.get("absorption_lrg_buy")
            outcome  = r.get("trade_status") or "—"
            ratio    = bv / sv if (bv is not None and sv and float(sv) > 0) else None
            conc     = lb / bv if (lb is not None and bv and float(bv) > 0) else None

            ratio_cls = "ok" if (ratio and ratio >= 1.0) else ("bad" if ratio is not None else "")
            out_cls   = "ok" if outcome == "tp" else ("bad" if outcome in ("sl","expired") else "")
            is_pump   = "pump" in exchange.lower()
            exch_cls  = "" if is_pump else "warn"

            if bv is not None and sv is not None:
                if float(sv) == 0 and float(bv) > 0:
                    ratio_str = "<span class='ok'>∞</span>"
                elif float(sv) == 0 and float(bv) == 0:
                    ratio_str = "<span style='color:#484f58'>no flow</span>"
                else:
                    ratio_str = f"<span class='{ratio_cls}'>{ratio:.2f}×</span>"
            else:
                ratio_str = "—"

            conc_str = f"{conc:.2f}" if conc is not None else "—"

            bounce_rows_html += f"""<tr>
              <td>{ts}</td>
              <td><b>{symbol}</b></td>
              <td class='{exch_cls}'>{exchange}</td>
              <td>{float(r['bounce_pct'])*100:.0f}%</td>
              <td class='{out_cls}'>{outcome}</td>
              <td>{f"{bv:.3f} ({bc}×)" if bv is not None else "<span style='color:#484f58'>NULL</span>"}</td>
              <td>{f"{sv:.3f} ({sc}×)" if sv is not None else "<span style='color:#484f58'>NULL</span>"}</td>
              <td>{ratio_str}</td>
              <td style='color:#8b949e'>{conc_str}</td>
              <td>{ev if ev is not None else "<span style='color:#f85149'>NULL</span>"}</td>
            </tr>"""

        total     = stats.get("total", 0)
        with_data = stats.get("with_data", 0)
        no_data   = stats.get("no_data", 0)
        cov_pct   = f"{with_data/total*100:.0f}%" if total else "—"

        tat_ticks  = tat_stats.get("total_ticks", 0)
        tat_trades = tat_stats.get("trades_com_ticks", 0)
        tat_mints  = tat_stats.get("mints_com_ticks", 0)

        def _box(label, val, color="#e6edf3"):
            return f"<div class='box'><div class='lbl'>{label}</div><div class='val' style='color:{color}'>{val}</div></div>"

        summary = (
            _box("Helius WebSocket", "ON" if helius_active else "OFF",
                 "#3fb950" if helius_active else "#f85149") +
            _box("Bounce signals totais", total) +
            _box("Com dados H22", f"{with_data} ({cov_pct})",
                 "#3fb950" if with_data > 0 else "#f85149") +
            _box("NULL (sem eventos)", no_data,
                 "#f85149" if no_data > with_data else "#e3b341") +
            _box("H22 ticks (holding)", tat_ticks, "#58a6ff") +
            _box("Trades monitorados", tat_trades, "#58a6ff") +
            _box("Mints distintos", tat_mints, "#8b949e")
        )

        # Tabela de cobertura por exchange
        exch_rows = ""
        for row in exchange_stats:
            exch = row["exchange"]
            n    = row["n"]
            h22  = row["with_h22"]
            pct  = f"{h22/n*100:.0f}%" if n else "—"
            is_pump = "pump" in exch.lower()
            cls  = "" if is_pump else "warn"
            note = "✅ H22 captura" if is_pump else "⚠️ sem H22 (não é pump.fun pAMM)"
            exch_rows += f"<tr><td class='{cls}'>{exch}</td><td>{n}</td><td>{h22} ({pct})</td><td class='{cls}'>{note}</td></tr>"

        html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Debug Absorption</title>{CSS}
<meta http-equiv='refresh' content='10'>
</head><body>
<h2>H22 — Absorption Debug</h2>
<p style='color:#8b949e;font-size:11px'>auto-refresh 10s · janela de absorção = 60s · fonte: Helius pAMM (pump.fun)<br>
<b>Dados brutos apenas</b> — score/grade removidos. Correlacionar com outcome (TP/SL) após n≥30 bounces com dados.<br>
⚠️ <b>H22 só captura pump.fun pAMM.</b> Tokens em Raydium/Meteora/outros sempre terão absorption_events=NULL.</p>
<div class='nav'>
  <a href='/analise/h22-correlation'>H22 Correlation</a>
  <a href='/analise/post-sl-recovery'>Post-SL Recovery</a>
  <a href='/debug/bounce-list'>Bounce List</a>
  <a href='/debug/executor-status'>Executor</a>
</div>
<br>
{summary}

<h3>Cobertura H22 por Exchange/Launchpad</h3>
<table>
  <thead><tr><th>Exchange</th><th>Bounces</th><th>Com H22</th><th>Nota</th></tr></thead>
  <tbody>{exch_rows}</tbody>
</table>

<h3>Tokens Ativos — Janela de Absorção Ao Vivo (últimos 60s)</h3>
<table>
  <thead><tr>
    <th>Token</th><th>Exchange</th><th>Elapsed</th><th>Eventos Helius</th>
    <th>Buy vol</th><th>Sell vol</th><th>Ratio</th>
    <th>Largest buy</th><th>Conc.</th><th>Mint</th><th></th>
  </tr></thead>
  <tbody>{live_rows_html}</tbody>
</table>

<h3>Últimos 30 Bounce Signals — Métricas Brutas H22</h3>
<p style='color:#8b949e;font-size:11px'>
  NULL em "Eventos" = bounce sem dados Helius (token não é pump.fun pAMM, ou pAMM ainda não teve swaps).<br>
  <b>∞</b> = buy_vol &gt; 0 e sell_vol = 0 (ninguém vendeu — sinal mais bullish). <b>no flow</b> = sem eventos de nenhum lado.<br>
  <b>Conc.</b> = largest_buy / buy_vol — próximo de 1.0 = 1 whale isolado; &lt; 0.5 = múltiplos compradores reais.<br>
  <b>Exchange em amarelo</b> = não é pump.fun pAMM — H22 nunca terá dados para esse token.
</p>
<table>
  <thead><tr>
    <th>Hora</th><th>Token</th><th>Exchange</th><th>Bounce</th><th>Trade</th>
    <th>Buy SOL</th><th>Sell SOL</th><th>Ratio</th><th>Conc.</th><th>Eventos</th>
  </tr></thead>
  <tbody>{bounce_rows_html}</tbody>
</table>
</body></html>"""
        return html


    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


@app.route("/analise/post-sl-recovery")
def analise_post_sl_recovery():
    """Para cada SL, o que o token fez depois do nosso stop?
    Responde: 'Continuar dumpando é padrão ou exceção?'
    Usa token_ticks (já coletados) — zero chamadas de API.
    Ajuste 3: usa entrada real (entry_price_usd / tokens_received × 1B) quando disponível.
    Ajuste 4: expired com pnl_pct_real >= 0 não é SL real — excluído.
    """
    import traceback
    try:
        with db._cursor() as cur:
            cur.execute("""
                WITH sl_trades AS (
                    SELECT id, mint, symbol,
                           entry_expected,
                           entry_price_usd,
                           tokens_received,
                           amount_usd,
                           exit_ts,
                           COALESCE(tp_pct, 0.75) AS tp_pct,
                           COALESCE(sl_pct, 0.15) AS sl_pct,
                           pnl_pct_real,
                           ts_signal,
                           mc_bottom,
                           status
                    FROM trades
                    WHERE (
                        status = 'sl'
                        OR (status = 'expired' AND pnl_pct_real < 0)
                    )
                      AND exit_ts IS NOT NULL
                      AND (entry_expected IS NOT NULL
                           OR (entry_price_usd IS NOT NULL AND tokens_received IS NOT NULL))
                ),
                ticks_after AS (
                    SELECT
                        s.id AS trade_id,
                        tt.mc,
                        EXTRACT(EPOCH FROM (tt.tick_ts - s.exit_ts))::INT AS secs_after
                    FROM sl_trades s
                    JOIN token_ticks tt ON tt.mint = s.mint
                    WHERE tt.tick_ts > s.exit_ts
                      AND tt.tick_ts <= s.exit_ts + INTERVAL '65 minutes'
                      AND tt.mc IS NOT NULL
                )
                SELECT
                    s.id, s.mint, s.symbol, s.status,
                    s.entry_expected,
                    s.entry_price_usd,
                    s.tokens_received,
                    s.amount_usd,
                    s.exit_ts, s.tp_pct, s.sl_pct,
                    s.pnl_pct_real, s.ts_signal, s.mc_bottom,
                    MAX(CASE WHEN ta.secs_after <= 300  THEN ta.mc END) AS max_mc_5m,
                    MAX(CASE WHEN ta.secs_after <= 600  THEN ta.mc END) AS max_mc_10m,
                    MAX(CASE WHEN ta.secs_after <= 1200 THEN ta.mc END) AS max_mc_20m,
                    MAX(CASE WHEN ta.secs_after <= 3600 THEN ta.mc END) AS max_mc_60m,
                    COUNT(ta.mc) AS tick_count_after
                FROM sl_trades s
                LEFT JOIN ticks_after ta ON ta.trade_id = s.id
                GROUP BY s.id, s.mint, s.symbol, s.status,
                         s.entry_expected, s.entry_price_usd, s.tokens_received,
                         s.amount_usd, s.exit_ts, s.tp_pct, s.sl_pct,
                         s.pnl_pct_real, s.ts_signal, s.mc_bottom
                ORDER BY s.exit_ts DESC
            """)
            rows = [dict(r) for r in cur.fetchall()]

        # Ajuste 3: calcula entry_mc_real — entrada real via fill, fallback para expected
        for r in rows:
            ep  = r.get("entry_price_usd")
            tok = r.get("tokens_received")
            if ep and tok and float(tok) > 0:
                r["entry_mc_real"] = float(ep) / float(tok) * 1_000_000_000
                r["entry_source"]  = "real"
            elif r.get("entry_expected"):
                r["entry_mc_real"] = float(r["entry_expected"])
                r["entry_source"]  = "expected"
            else:
                r["entry_mc_real"] = None
                r["entry_source"]  = None

        # Enriquece cada linha com métricas derivadas usando entrada real
        for r in rows:
            entry  = r["entry_mc_real"] or 0.0
            tp_pct = float(r.get("tp_pct") or 0.75)
            sl_pct = float(r.get("sl_pct") or 0.15)
            sl_mc  = entry * (1 - sl_pct) if entry else 0
            tp_mc  = entry * (1 + tp_pct)  if entry else 0
            r["sl_mc"] = sl_mc
            r["tp_mc"] = tp_mc

            for win, key in [("5m","max_mc_5m"),("10m","max_mc_10m"),
                              ("20m","max_mc_20m"),("60m","max_mc_60m")]:
                mx = r.get(key)
                if mx and sl_mc:
                    r[f"gain_sl_{win}"]    = (float(mx) - sl_mc) / sl_mc
                    r[f"gain_entry_{win}"] = (float(mx) - entry) / entry if entry else None
                else:
                    r[f"gain_sl_{win}"]    = None
                    r[f"gain_entry_{win}"] = None

            mx60 = r.get("max_mc_60m")
            r["hit_tp"]   = mx60 is not None and tp_mc  > 0 and float(mx60) >= tp_mc
            r["hit_75"]   = mx60 is not None and entry  > 0 and float(mx60) >= entry * 1.75
            r["hit_100"]  = mx60 is not None and entry  > 0 and float(mx60) >= entry * 2.00
            r["hit_150"]  = mx60 is not None and entry  > 0 and float(mx60) >= entry * 2.50

        # Summary — apenas trades com ticks pós-SL (sem ticks = sem dados suficientes)
        with_ticks = [r for r in rows if (r.get("tick_count_after") or 0) >= 3]
        n_total     = len(rows)
        n_with_data = len(with_ticks)
        n_hit_tp    = sum(1 for r in with_ticks if r.get("hit_tp"))
        n_hit_75    = sum(1 for r in with_ticks if r.get("hit_75"))
        n_hit_100   = sum(1 for r in with_ticks if r.get("hit_100"))
        n_real_entry= sum(1 for r in rows if r.get("entry_source") == "real")

        def _pct(v):
            if v is None: return "—"
            return f"{float(v)*100:+.0f}%"
        def _mc(v):
            if v is None: return "—"
            v = float(v)
            if v >= 1_000_000: return f"${v/1_000_000:.1f}M"
            if v >= 1_000:     return f"${v/1_000:.0f}K"
            return f"${v:.0f}"
        def _grade(r):
            g = r.get("gain_sl_20m")
            if g is None: return "<span style='color:#484f58'>sem dados</span>"
            if g >= 0.75: return f"<span style='color:#3fb950'>+{g*100:.0f}% teria TP</span>"
            if g >= 0.20: return f"<span style='color:#e3b341'>+{g*100:.0f}%</span>"
            if g >= 0.0:  return f"<span style='color:#8b949e'>+{g*100:.0f}%</span>"
            return f"<span style='color:#f85149'>{g*100:.0f}%</span>"

        CSS = ("<style>body{background:#0d1117;color:#e6edf3;font:13px/1.6 monospace;padding:20px}"
               "a{color:#58a6ff}table{border-collapse:collapse;width:100%}th{background:#161b22;"
               "padding:6px 10px;text-align:left;font-size:11px;color:#8b949e}"
               "td{padding:5px 10px;border-bottom:1px solid #21262d;font-size:12px}"
               ".box{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px 16px;"
               "margin:8px 4px;display:inline-block;vertical-align:top}"
               ".lbl{color:#8b949e;font-size:11px}.val{font-size:20px;font-weight:bold}"
               ".nav a{color:#58a6ff;margin-right:14px;text-decoration:none;font-size:12px}</style>")

        def _box(label, val, color="#e6edf3"):
            return f"<div class='box'><div class='lbl'>{label}</div><div class='val' style='color:{color}'>{val}</div></div>"

        pct_hit_tp  = f"{n_hit_tp/n_with_data*100:.0f}%" if n_with_data else "—"
        pct_hit_75  = f"{n_hit_75/n_with_data*100:.0f}%" if n_with_data else "—"
        pct_hit_100 = f"{n_hit_100/n_with_data*100:.0f}%" if n_with_data else "—"
        tp_pct_label = f"{rows[0]['tp_pct']*100:.0f}%" if rows else "75%"

        summary_html = (
            _box("SLs reais", n_total) +
            _box("Com dados de ticks", n_with_data, "#8b949e") +
            _box(f"Entrada real (fill)", f"{n_real_entry}/{n_total}", "#58a6ff") +
            _box(f"Hit TP ({tp_pct_label}) após SL",
                 f"{pct_hit_tp} ({n_hit_tp})", "#3fb950") +
            _box("Hit +75% após SL", f"{pct_hit_75} ({n_hit_75})", "#3fb950") +
            _box("Hit +100% após SL", f"{pct_hit_100} ({n_hit_100})", "#e3b341")
        )

        rows_html = ""
        for r in rows:
            symbol  = r.get("symbol") or r["mint"][:8]
            ts      = r["exit_ts"].strftime("%m-%d %H:%M") if r.get("exit_ts") else "—"
            n_ticks = r.get("tick_count_after") or 0
            dex_url = f"https://dexscreener.com/solana/{r['mint']}"
            tp_icon = "✅" if r.get("hit_tp") else ("🟡" if r.get("hit_75") else ("⬜" if n_ticks >= 3 else "❓"))
            src_badge = ("<span style='color:#3fb950;font-size:10px'>●real</span>"
                         if r.get("entry_source") == "real"
                         else "<span style='color:#8b949e;font-size:10px'>●est</span>")

            rows_html += f"""<tr>
              <td>{ts}</td>
              <td><b>{symbol}</b></td>
              <td>{_pct(r.get('pnl_pct_real'))}</td>
              <td>{_mc(r.get('entry_mc_real'))} {src_badge}</td>
              <td style='color:#8b949e'>{_mc(r.get('mc_bottom'))}</td>
              <td>{_mc(r.get('max_mc_5m'))}</td>
              <td>{_mc(r.get('max_mc_10m'))}</td>
              <td>{_mc(r.get('max_mc_20m'))}</td>
              <td>{_mc(r.get('max_mc_60m'))}</td>
              <td>{_grade(r)}</td>
              <td style='text-align:center'>{tp_icon}</td>
              <td style='color:#8b949e'>{n_ticks} ticks</td>
              <td><a href='{dex_url}' target='_blank'>DEX</a></td>
            </tr>"""

        html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Post-SL Recovery</title>{CSS}
</head><body>
<h2>Post-SL Recovery — o que o token fez depois do nosso stop?</h2>
<p style='color:#8b949e;font-size:11px'>
  H22: se sellers foram absorvidos, token recupera após SL.<br>
  ✅ = teria atingido o TP se não tivéssemos saído &nbsp;|&nbsp;
  🟡 = subiu ≥75% &nbsp;|&nbsp; ⬜ = sem recuperação relevante &nbsp;|&nbsp; ❓ = sem dados<br>
  <b>●real</b> = entrada via fill real (entry_price_usd/tokens×1B) &nbsp;|&nbsp;
  <b>●est</b> = estimada (mc_bottom×1.15) — menos precisa<br>
  <b>expired excluídos se pnl≥0</b> (trailing exits não são SLs reais).
</p>
<div class='nav'>
  <a href='/analise/wr-breakdown'>WR Breakdown</a>
  <a href='/debug/bounce-list'>Bounce List</a>
  <a href='/analise/h9'>H9</a>
  <a href='/analise/h22-correlation'>H22 Correlation</a>
</div>
<br>
{summary_html}
<br><br>
<table>
  <thead><tr>
    <th>Exit</th><th>Token</th><th>PnL SL</th><th>Entry MC</th><th>Bottom</th>
    <th>Max 5m</th><th>Max 10m</th><th>Max 20m</th><th>Max 60m</th>
    <th>20m gain from SL</th><th>TP?</th><th>Ticks</th><th></th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>
<p style='color:#484f58;font-size:11px;margin-top:12px'>
  Fonte: token_ticks coletados durante os 60min do HotMonitor.<br>
  Trades sem ticks pós-saída = token saiu da janela quente antes do SL ou pouco depois.<br>
  expired com pnl≥0 excluídos (são trailing exits não reconciliados, não SLs reais).
</p>
</body></html>"""
        return html

    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


@app.route("/analise/h22-correlation")
def analise_h22_correlation():
    """H22 — Correlação entre absorção pós-fundo (Helius) e outcome (TP/SL).
    Instrumento de medição pronto antes dos dados chegarem.
    Conclusões estatisticamente válidas só com n≥30 bounces com absorption_events > 0.
    """
    import traceback
    try:
        CSS = ("<style>body{background:#0d1117;color:#e6edf3;font:13px/1.6 monospace;padding:20px}"
               "a{color:#58a6ff;text-decoration:none}table{border-collapse:collapse;width:100%}"
               "th{background:#161b22;padding:6px 10px;text-align:left;font-size:11px;color:#8b949e}"
               "td{padding:5px 10px;border-bottom:1px solid #21262d;font-size:12px}"
               ".box{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px 16px;"
               "margin:8px 4px;display:inline-block;vertical-align:top}"
               ".lbl{color:#8b949e;font-size:11px}.val{font-size:20px;font-weight:bold}"
               ".nav a{color:#58a6ff;margin-right:14px;font-size:12px}"
               ".green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}"
               ".dim{color:#484f58}.warn{color:#e3b341}"
               "h2{color:#8b949e;font-size:12px;margin:20px 0 6px}"
               "h1{color:#58a6ff;font-size:16px;margin-bottom:4px}</style>")

        def _box(label, val, color="#e6edf3"):
            return f"<div class='box'><div class='lbl'>{label}</div><div class='val' style='color:{color}'>{val}</div></div>"

        with db._cursor() as cur:
            # --- Cobertura geral ---
            cur.execute("""
                SELECT
                    COUNT(*) AS total_bounces,
                    COUNT(CASE WHEN absorption_events > 0 THEN 1 END) AS with_helius,
                    COUNT(CASE WHEN absorption_events IS NULL OR absorption_events = 0 THEN 1 END) AS no_helius
                FROM bounce_signals
            """)
            cov = dict(cur.fetchone())

            # --- Bounce signals com trades associados e outcome GMGN ---
            # Join por mint — bounce_signals tem UNIQUE(mint), trades pode ter múltiplos por mint
            # Pega o trade mais recente por mint (qualquer status) para não perder trades open/error
            cur.execute("""
                WITH latest_trade AS (
                    SELECT DISTINCT ON (mint)
                        mint,
                        status,
                        pnl_pct_real
                    FROM trades
                    ORDER BY mint, created_at DESC
                )
                SELECT
                    b.mint,
                    b.symbol,
                    b.exchange,
                    b.ts_signal,
                    b.bounce_pct,
                    b.elapsed_sec,
                    b.mc_min,
                    b.mc_at_signal,
                    b.mc_migration,
                    b.drop_from_mig,
                    b.absorption_events,
                    b.absorption_buy_vol,
                    b.absorption_sell_vol,
                    b.absorption_buy_cnt,
                    b.absorption_sell_cnt,
                    b.absorption_lrg_buy,
                    t.status,
                    t.pnl_pct_real
                FROM bounce_signals b
                LEFT JOIN latest_trade t ON t.mint = b.mint
                ORDER BY b.ts_signal DESC
            """)
            all_rows = [dict(r) for r in cur.fetchall()]

            # --- Trajetória de preço pós-bounce via token_ticks ---
            # Para cada bounce com dados Helius, busca o MC em janelas de +5/+10/+20min após ts_signal
            cur.execute("""
                WITH latest_trade AS (
                    SELECT DISTINCT ON (mint)
                        mint, status, pnl_pct_real
                    FROM trades
                    ORDER BY mint, created_at DESC
                ),
                bounce_base AS (
                    SELECT
                        b.mint, b.symbol, b.ts_signal,
                        b.mc_at_signal, b.mc_min AS mc_bottom,
                        b.bounce_pct, b.drop_from_mig,
                        b.absorption_events,
                        b.absorption_buy_vol, b.absorption_sell_vol,
                        b.absorption_buy_cnt, b.absorption_lrg_buy,
                        t.status, t.pnl_pct_real
                    FROM bounce_signals b
                    LEFT JOIN latest_trade t ON t.mint = b.mint
                    WHERE b.absorption_events > 0 AND b.ts_signal IS NOT NULL
                ),
                tick_agg AS (
                    SELECT
                        b.mint,
                        COUNT(tt.id)                                                                           AS tick_count,
                        MAX(CASE WHEN tt.tick_ts <= b.ts_signal + interval '5 minutes'  THEN COALESCE(tt.mc_high, tt.mc) END) AS mc_max_5min,
                        MAX(CASE WHEN tt.tick_ts <= b.ts_signal + interval '10 minutes' THEN COALESCE(tt.mc_high, tt.mc) END) AS mc_max_10min,
                        MAX(CASE WHEN tt.tick_ts <= b.ts_signal + interval '20 minutes' THEN COALESCE(tt.mc_high, tt.mc) END) AS mc_max_20min,
                        MIN(CASE WHEN tt.tick_ts <= b.ts_signal + interval '20 minutes' THEN COALESCE(tt.mc_low,  tt.mc) END) AS mc_min_20min
                    FROM bounce_base b
                    JOIN token_ticks tt ON tt.mint = b.mint
                        AND tt.tick_ts >  b.ts_signal
                        AND tt.tick_ts <= b.ts_signal + interval '20 minutes'
                    GROUP BY b.mint
                )
                SELECT
                    b.*,
                    ta.tick_count,
                    ta.mc_max_5min,
                    ta.mc_max_10min,
                    ta.mc_max_20min,
                    ta.mc_min_20min,
                    CASE WHEN b.mc_at_signal > 0 AND ta.mc_max_20min IS NOT NULL
                         THEN (ta.mc_max_20min - b.mc_at_signal) / b.mc_at_signal END AS max_gain_pct,
                    CASE WHEN b.mc_at_signal > 0 AND ta.mc_min_20min IS NOT NULL
                         THEN (ta.mc_min_20min - b.mc_at_signal) / b.mc_at_signal END AS min_loss_pct,
                    CASE WHEN b.mc_at_signal > 0 AND ta.mc_max_5min IS NOT NULL
                         THEN (ta.mc_max_5min - b.mc_at_signal) / b.mc_at_signal END AS gain_5min_pct
                FROM bounce_base b
                LEFT JOIN tick_agg ta ON ta.mint = b.mint
                ORDER BY b.ts_signal DESC
            """)
            trajectory_rows = [dict(r) for r in cur.fetchall()]

        # --- Classify outcomes ---
        # trailing_active NÃO é TP confirmado — resultado final ainda desconhecido.
        # Só status='tp' (detectado pelo _check_trailing_exit) conta como TP real.
        TP_STATUS  = {'tp'}
        SL_STATUS  = {'sl'}

        for r in all_rows:
            s = r.get("status") or ""
            if s in TP_STATUS:
                r["outcome"] = "tp"
            elif s in SL_STATUS:
                r["outcome"] = "sl"
            elif s == "expired":
                pnl = r.get("pnl_pct_real")
                r["outcome"] = "tp" if (pnl is not None and pnl > 0) else "sl" if (pnl is not None) else None
            else:
                r["outcome"] = None  # trailing_active, pending, open — resultado pendente

            ev = r.get("absorption_events") or 0
            bv = r.get("absorption_buy_vol") or 0.0
            sv = r.get("absorption_sell_vol") or 0.0
            lb = r.get("absorption_lrg_buy") or 0.0
            r["has_helius"] = ev > 0
            r["ratio"] = bv / sv if sv > 0 else (float("inf") if bv > 0 else None)
            r["concentration"] = lb / bv if bv > 0 else None

        # Rows with Helius data + outcome
        helius_with_outcome = [r for r in all_rows if r["has_helius"] and r["outcome"] is not None]
        helius_all          = [r for r in all_rows if r["has_helius"]]
        traded_rows         = [r for r in all_rows if r["outcome"] is not None]

        n_total    = cov["total_bounces"]
        n_helius   = cov["with_helius"]
        n_no_helius= cov["no_helius"]
        gate_pct   = f"{n_helius/30*100:.0f}%" if n_helius < 30 else "ATINGIDO"

        # --- Bucket helpers ---
        def _wr_row(label, rows_subset, color_fn=None):
            n   = len(rows_subset)
            n_tp= sum(1 for r in rows_subset if r["outcome"] == "tp")
            n_sl= sum(1 for r in rows_subset if r["outcome"] == "sl")
            wr  = n_tp / (n_tp + n_sl) if (n_tp + n_sl) > 0 else None
            wr_str = f"{wr*100:.0f}%" if wr is not None else "—"
            if wr is None:
                wr_color = "#484f58"
            elif wr >= 0.40:
                wr_color = "#3fb950"
            elif wr >= 0.25:
                wr_color = "#d29922"
            else:
                wr_color = "#f85149"
            n_no_out = n - n_tp - n_sl
            note = f"<span class='dim'>({n_no_out} sem trade)</span>" if n_no_out else ""
            return (f"<tr><td>{label}</td><td>{n_tp+n_sl}</td>"
                    f"<td style='color:{wr_color}'>{wr_str}</td>"
                    f"<td>{n_tp}</td><td>{n_sl}</td>"
                    f"<td class='dim'>{n}</td>{note}</tr>")

        # --- Table 1: por absorption_events bucket ---
        buckets_ev = [
            ("0 (sem dados)",      [r for r in traded_rows if not r["has_helius"]]),
            ("1–2 eventos",        [r for r in helius_with_outcome if 1 <= (r.get("absorption_events") or 0) <= 2]),
            ("3–5 eventos",        [r for r in helius_with_outcome if 3 <= (r.get("absorption_events") or 0) <= 5]),
            ("6–10 eventos",       [r for r in helius_with_outcome if 6 <= (r.get("absorption_events") or 0) <= 10]),
            ("≥11 eventos",        [r for r in helius_with_outcome if (r.get("absorption_events") or 0) >= 11]),
        ]
        ev_rows_html = ""
        for label, subset in buckets_ev:
            ev_rows_html += _wr_row(label, subset)

        # --- Table 2: por buy/sell ratio ---
        def _ratio_rows():
            no_flow   = [r for r in helius_with_outcome if r["ratio"] is None]
            inf_ratio = [r for r in helius_with_outcome if r["ratio"] == float("inf")]
            low       = [r for r in helius_with_outcome if r["ratio"] is not None and r["ratio"] != float("inf") and r["ratio"] < 0.5]
            mid       = [r for r in helius_with_outcome if r["ratio"] is not None and r["ratio"] != float("inf") and 0.5 <= r["ratio"] < 1.0]
            bal       = [r for r in helius_with_outcome if r["ratio"] is not None and r["ratio"] != float("inf") and 1.0 <= r["ratio"] < 1.5]
            high      = [r for r in helius_with_outcome if r["ratio"] is not None and r["ratio"] != float("inf") and 1.5 <= r["ratio"] < 3.0]
            dom       = [r for r in helius_with_outcome if r["ratio"] is not None and r["ratio"] != float("inf") and r["ratio"] >= 3.0]
            rows = ""
            rows += _wr_row("∞ (só compra, sell=0)",  inf_ratio)
            rows += _wr_row("≥3× (compra domina)",    dom)
            rows += _wr_row("1.5–3× (ligeira compra)", high)
            rows += _wr_row("1.0–1.5× (equilibrado)", bal)
            rows += _wr_row("0.5–1.0× (leve venda)",  mid)
            rows += _wr_row("<0.5× (venda domina)",   low)
            rows += _wr_row("no flow (ambos=0)",       no_flow)
            return rows

        ratio_rows_html = _ratio_rows()

        # --- Table 3: buy_concentration ---
        def _conc_rows():
            rows = ""
            no_conc = [r for r in helius_with_outcome if r["concentration"] is None]
            high_c  = [r for r in helius_with_outcome if r["concentration"] is not None and r["concentration"] >= 0.8]
            mid_c   = [r for r in helius_with_outcome if r["concentration"] is not None and 0.5 <= r["concentration"] < 0.8]
            low_c   = [r for r in helius_with_outcome if r["concentration"] is not None and r["concentration"] < 0.5]
            rows += _wr_row("≥0.8 (1 whale dominou)",          high_c)
            rows += _wr_row("0.5–0.8 (whale + suporte)",       mid_c)
            rows += _wr_row("<0.5 (múltiplos compradores)",     low_c)
            rows += _wr_row("— (sem buy flow)",                 no_conc)
            return rows

        conc_rows_html = _conc_rows()

        # --- Classify outcomes for trajectory rows ---
        for r in trajectory_rows:
            s = r.get("status") or ""
            if s in TP_STATUS:
                r["outcome"] = "tp"
            elif s in SL_STATUS:
                r["outcome"] = "sl"
            elif s == "expired":
                pnl = r.get("pnl_pct_real")
                r["outcome"] = "tp" if (pnl is not None and pnl > 0) else "sl" if (pnl is not None) else None
            else:
                r["outcome"] = None
            ev = r.get("absorption_events") or 0
            bv = r.get("absorption_buy_vol") or 0.0
            sv = r.get("absorption_sell_vol") or 0.0
            lb = r.get("absorption_lrg_buy") or 0.0
            r["ratio"] = bv / sv if sv > 0 else (float("inf") if bv > 0 else None)
            r["concentration"] = lb / bv if bv > 0 else None

        # --- Aggregated: absorção bucket vs retorno médio (price trajectory) ---
        def _avg(lst):
            lst2 = [x for x in lst if x is not None]
            return sum(lst2) / len(lst2) if lst2 else None

        def _pct_fmt(v, suffix=""):
            if v is None: return "<span class='dim'>—</span>"
            color = "#3fb950" if v > 0 else ("#f85149" if v < 0 else "#8b949e")
            return f"<span style='color:{color}'>{v*100:+.1f}%{suffix}</span>"

        def _traj_bucket_row(label, subset):
            n = len(subset)
            if n == 0:
                return f"<tr><td>{label}</td><td class='dim'>—</td><td class='dim'>—</td><td class='dim'>—</td><td class='dim'>—</td><td class='dim'>—</td><td class='dim'>—</td><td class='dim'>—</td></tr>"
            n_with_ticks = sum(1 for r in subset if r.get("tick_count"))
            n_tp = sum(1 for r in subset if r.get("outcome") == "tp")
            n_sl = sum(1 for r in subset if r.get("outcome") == "sl")
            wr = n_tp / (n_tp + n_sl) if (n_tp + n_sl) > 0 else None
            wr_str = f"{wr*100:.0f}%" if wr is not None else "—"
            wr_color = ("#3fb950" if wr and wr >= 0.40 else "#d29922" if wr and wr >= 0.25 else "#f85149") if wr else "#484f58"
            avg_max = _avg([r.get("max_gain_pct") for r in subset])
            avg_min = _avg([r.get("min_loss_pct") for r in subset])
            avg_5   = _avg([r.get("gain_5min_pct") for r in subset])
            return (f"<tr>"
                    f"<td>{label}</td>"
                    f"<td>{n} <span class='dim'>({n_with_ticks} ticks)</span></td>"
                    f"<td style='color:{wr_color}'>{wr_str} <span class='dim'>({n_tp}T/{n_sl}S)</span></td>"
                    f"<td>{_pct_fmt(avg_5)}</td>"
                    f"<td>{_pct_fmt(avg_max)}</td>"
                    f"<td>{_pct_fmt(avg_min)}</td>"
                    f"</tr>")

        traj_by_events = [
            ("1–2 eventos",  [r for r in trajectory_rows if 1 <= (r.get("absorption_events") or 0) <= 2]),
            ("3–5 eventos",  [r for r in trajectory_rows if 3 <= (r.get("absorption_events") or 0) <= 5]),
            ("6–10 eventos", [r for r in trajectory_rows if 6 <= (r.get("absorption_events") or 0) <= 10]),
            ("≥11 eventos",  [r for r in trajectory_rows if (r.get("absorption_events") or 0) >= 11]),
        ]
        traj_by_ratio = [
            ("∞ (só compra)", [r for r in trajectory_rows if r.get("ratio") == float("inf")]),
            ("≥3×",           [r for r in trajectory_rows if r.get("ratio") is not None and r["ratio"] != float("inf") and r["ratio"] >= 3.0]),
            ("1.5–3×",        [r for r in trajectory_rows if r.get("ratio") is not None and r["ratio"] != float("inf") and 1.5 <= r["ratio"] < 3.0]),
            ("1.0–1.5×",      [r for r in trajectory_rows if r.get("ratio") is not None and r["ratio"] != float("inf") and 1.0 <= r["ratio"] < 1.5]),
            ("<1.0×",         [r for r in trajectory_rows if r.get("ratio") is not None and r["ratio"] != float("inf") and r["ratio"] < 1.0]),
            ("no flow",       [r for r in trajectory_rows if r.get("ratio") is None]),
        ]

        traj_ev_html    = "".join(_traj_bucket_row(l, s) for l, s in traj_by_events)
        traj_ratio_html = "".join(_traj_bucket_row(l, s) for l, s in traj_by_ratio)

        # --- Detail trajectory table ---
        traj_detail_rows = ""
        for r in trajectory_rows[:60]:
            ts = r.get("ts_signal")
            ts_str = ts.strftime("%m-%d %H:%M") if ts else "—"
            sym = r.get("symbol") or "?"
            ev  = r.get("absorption_events") or 0
            bv  = r.get("absorption_buy_vol") or 0
            sv  = r.get("absorption_sell_vol") or 0
            tc  = r.get("tick_count") or 0
            ratio_v = r.get("ratio")
            if ratio_v is None:      ratio_str = "<span class='dim'>no flow</span>"
            elif ratio_v == float("inf"): ratio_str = "<span class='green'>∞</span>"
            else:
                rc = "#3fb950" if ratio_v >= 1.5 else ("#d29922" if ratio_v >= 1.0 else "#f85149")
                ratio_str = f"<span style='color:{rc}'>{ratio_v:.1f}×</span>"

            o = r.get("outcome")
            if o == "tp":   out_str = "<span class='green'>✅ TP</span>"
            elif o == "sl": out_str = "<span class='red'>❌ SL</span>"
            else:           out_str = "<span class='dim'>—</span>"

            mc_sig = r.get("mc_at_signal") or 0
            def _mc_pct(mc_target):
                if not mc_sig or not mc_target: return "<span class='dim'>—</span>"
                pct = (mc_target - mc_sig) / mc_sig
                c = "#3fb950" if pct > 0.10 else ("#f85149" if pct < -0.10 else "#8b949e")
                return f"<span style='color:{c}'>{pct*100:+.0f}%</span>"

            traj_detail_rows += (
                f"<tr>"
                f"<td>{ts_str}</td>"
                f"<td><b>{sym}</b></td>"
                f"<td>{ev} ev / {bv:.3f}◎</td>"
                f"<td>{ratio_str}</td>"
                f"<td>{_mc_pct(r.get('mc_max_5min'))}</td>"
                f"<td>{_mc_pct(r.get('mc_max_10min'))}</td>"
                f"<td style='font-weight:bold'>{_mc_pct(r.get('mc_max_20min'))}</td>"
                f"<td>{_mc_pct(r.get('mc_min_20min'))}</td>"
                f"<td class='dim'>{tc}</td>"
                f"<td>{out_str}</td>"
                f"</tr>"
            )
        if not traj_detail_rows:
            traj_detail_rows = "<tr><td colspan='10' class='dim'>Nenhum bounce com dados Helius + ticks ainda</td></tr>"

        # --- Recent bounces with Helius data ---
        def _outcome_badge(r):
            o = r.get("outcome")
            if o == "tp":   return "<span class='green'>TP</span>"
            if o == "sl":   return "<span class='red'>SL</span>"
            return "<span class='dim'>—</span>"

        def _ratio_fmt(r):
            v = r["ratio"]
            if v is None:      return "<span class='dim'>no flow</span>"
            if v == float("inf"): return "<span class='green'>∞</span>"
            color = "#3fb950" if v >= 1.5 else ("#d29922" if v >= 1.0 else "#f85149")
            return f"<span style='color:{color}'>{v:.2f}×</span>"

        detail_rows = ""
        for r in helius_all[:50]:
            ev   = r.get("absorption_events") or 0
            bv   = r.get("absorption_buy_vol") or 0
            sv   = r.get("absorption_sell_vol") or 0
            bc   = r.get("absorption_buy_cnt") or 0
            lb   = r.get("absorption_lrg_buy") or 0
            conc = f"{lb/bv:.2f}" if bv > 0 else "—"
            ts   = r.get("ts_signal")
            ts_str = ts.strftime("%m-%d %H:%M") if ts else "—"
            detail_rows += (
                f"<tr>"
                f"<td>{ts_str}</td>"
                f"<td>{r.get('symbol','?')}</td>"
                f"<td>{ev}</td>"
                f"<td>{bv:.3f}</td>"
                f"<td>{sv:.3f}</td>"
                f"<td>{_ratio_fmt(r)}</td>"
                f"<td>{bc}</td>"
                f"<td>{conc}</td>"
                f"<td>{_outcome_badge(r)}</td>"
                f"</tr>"
            )

        # --- Gate status ---
        gate_color = "#3fb950" if n_helius >= 30 else ("#d29922" if n_helius >= 15 else "#f85149")
        gate_msg   = ("✅ Gate atingido — conclusões válidas" if n_helius >= 30
                      else f"⏳ {n_helius}/30 — acumulando dados ({gate_pct} do gate)")

        html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>H22 Correlation</title>{CSS}
</head><body>
<h1>H22 — Correlação Absorção → Outcome (TP/SL)</h1>
<p style='color:#8b949e;font-size:11px;margin-bottom:12px'>
  H22: compra real no fundo (Helius pAMM) discrimina TP vs SL.<br>
  Só tokens pump.fun pAMM têm cobertura. Conclusões válidas com n≥30 bounces com Helius data.
</p>
<div class='nav'>
  <a href='/debug/absorption'>Absorption Debug</a>
  <a href='/analise/wr-breakdown'>WR Breakdown</a>
  <a href='/analise/post-sl-recovery'>Post-SL Recovery</a>
  <a href='/analise/h9'>H9</a>
</div>
<br>

{_box("Total bounces", n_total)}
{_box("Com Helius", n_helius, gate_color)}
{_box("Sem Helius", n_no_helius, "#8b949e")}
{_box("Gate 30", gate_msg, gate_color)}
{_box("Com trade+outcome", len(helius_with_outcome), "#58a6ff")}

<br><br>
<p style='color:{gate_color};font-size:12px'>⚠ {gate_msg}</p>
<br>

<h2>▸ Por volume de eventos Helius (absorption_events)</h2>
<p style='color:#484f58;font-size:11px'>
  "0" = sem dados Helius (inclui todos os bounces sem pump.fun pAMM ou anteriores à implementação).
  Tokens com 0 eventos são ruído de base — comparar com grupos ≥1.
</p>
<table>
  <thead><tr>
    <th>Bucket</th><th>n (traded)</th><th>Win Rate</th><th>TP</th><th>SL</th><th>Total bounces</th>
  </tr></thead>
  <tbody>{ev_rows_html}</tbody>
</table>

<br>
<h2>▸ Por ratio buy/sell (volume SOL nos 60s pós-fundo)</h2>
<p style='color:#484f58;font-size:11px'>
  ∞ = houve compra mas ZERO venda — sinal mais bullish possível.<br>
  ≥3× = compradores pagaram 3× mais que vendedores no fundo.
</p>
<table>
  <thead><tr>
    <th>Ratio bucket</th><th>n (traded)</th><th>Win Rate</th><th>TP</th><th>SL</th><th>Total</th>
  </tr></thead>
  <tbody>{ratio_rows_html}</tbody>
</table>

<br>
<h2>▸ Por concentração de compra (largest_buy / buy_vol)</h2>
<p style='color:#484f58;font-size:11px'>
  Alta concentração = 1 whale isolado. Baixa = múltiplos compradores reais.<br>
  Hipótese: múltiplos compradores (conc &lt; 0.5) = suporte mais robusto.
</p>
<table>
  <thead><tr>
    <th>Concentração</th><th>n (traded)</th><th>Win Rate</th><th>TP</th><th>SL</th><th>Total</th>
  </tr></thead>
  <tbody>{conc_rows_html}</tbody>
</table>

<br>
<h2>▸ Absorção vs Trajetória de preço (20min pós-bounce) — por volume de eventos</h2>
<p style='color:#484f58;font-size:11px'>
  Para cada bounce com dados Helius, busca o máximo/mínimo de MC nos token_ticks nas janelas +5min/+10min/+20min após o sinal.<br>
  "max gain +20min" = quanto o token subiu no melhor momento dentro de 20min. "min loss" = pior momento.
</p>
<table>
  <thead><tr>
    <th>Bucket eventos</th><th>n (com ticks)</th><th>WR carteira</th>
    <th>Max gain +5min</th><th>Max gain +20min</th><th>Min loss +20min</th>
  </tr></thead>
  <tbody>{traj_ev_html if traj_ev_html else "<tr><td colspan='6' class='dim'>Sem dados</td></tr>"}</tbody>
</table>

<br>
<h2>▸ Absorção vs Trajetória de preço — por ratio buy/sell</h2>
<table>
  <thead><tr>
    <th>Ratio bucket</th><th>n (com ticks)</th><th>WR carteira</th>
    <th>Max gain +5min</th><th>Max gain +20min</th><th>Min loss +20min</th>
  </tr></thead>
  <tbody>{traj_ratio_html if traj_ratio_html else "<tr><td colspan='6' class='dim'>Sem dados</td></tr>"}</tbody>
</table>

<br>
<h2>▸ Detalhe: absorção + trajetória de preço por bounce (últimos {len(trajectory_rows)})</h2>
<p style='color:#484f58;font-size:11px'>
  Colunas +5/+10/+20min = variação % do MC_at_signal até o máximo registrado em token_ticks nessa janela.<br>
  "min" = pior MC registrado em 20min. Sem ticks = token saiu do HotMonitor antes dos ticks chegarem.
</p>
<table>
  <thead><tr>
    <th>Hora</th><th>Token</th><th>Absorção</th><th>Ratio</th>
    <th>+5min max</th><th>+10min max</th><th>+20min max</th><th>+20min min</th>
    <th>Ticks</th><th>Outcome</th>
  </tr></thead>
  <tbody>{traj_detail_rows}</tbody>
</table>

<br>
<h2>▸ Por volume de eventos Helius (resumo WR — para referência)</h2>
<table>
  <thead><tr>
    <th>Bucket</th><th>n (traded)</th><th>Win Rate</th><th>TP</th><th>SL</th><th>Total bounces</th>
  </tr></thead>
  <tbody>{ev_rows_html}</tbody>
</table>

<br>
<h2>▸ Por ratio buy/sell (resumo WR)</h2>
<table>
  <thead><tr>
    <th>Ratio bucket</th><th>n (traded)</th><th>Win Rate</th><th>TP</th><th>SL</th><th>Total</th>
  </tr></thead>
  <tbody>{ratio_rows_html}</tbody>
</table>

<br>
<h2>▸ Por concentração de compra (largest_buy / buy_vol)</h2>
<table>
  <thead><tr>
    <th>Concentração</th><th>n (traded)</th><th>Win Rate</th><th>TP</th><th>SL</th><th>Total</th>
  </tr></thead>
  <tbody>{conc_rows_html}</tbody>
</table>

<br>
<p style='color:#484f58;font-size:11px;margin-top:12px'>
  Janela Helius: 60s antes do bounce. Janela ticks: 20min pós-sinal.<br>
  GMGN é ground truth para TP/SL — DB status pode ter falsos positivos (TradeMonitor).<br>
  Gate para ativar como filtro: n≥30 com absorption_events &gt; 0 + WR ≥15pp maior vs grupo sem dados.
</p>
</body></html>"""
        return html

    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


@app.route("/analise/sniper-filter")
def analise_sniper_filter():
    """Análise do FILTER_SNIPER_MAX — top70_sniper_hold_rate vs recovery e WR live.
    Responde: o filtro atual (5%) está bloqueando tokens bons ou ruins?
    Dados: migrations (recovery histórico) + trades (WR live GMGN).
    """
    import traceback
    try:
        CSS = ("<style>body{background:#0d1117;color:#e6edf3;font:13px/1.6 monospace;padding:20px}"
               "a{color:#58a6ff;text-decoration:none}table{border-collapse:collapse;width:100%}"
               "th{background:#161b22;padding:6px 10px;text-align:left;font-size:11px;color:#8b949e}"
               "td{padding:5px 10px;border-bottom:1px solid #21262d;font-size:12px}"
               ".box{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px 16px;"
               "margin:8px 4px;display:inline-block;vertical-align:top}"
               ".lbl{color:#8b949e;font-size:11px}.val{font-size:20px;font-weight:bold}"
               ".green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}.dim{color:#484f58}"
               "h2{color:#8b949e;font-size:12px;margin:20px 0 6px}"
               "h1{color:#58a6ff;font-size:16px;margin-bottom:4px}</style>")

        def _box(label, val, color="#e6edf3"):
            return f"<div class='box'><div class='lbl'>{label}</div><div class='val' style='color:{color}'>{val}</div></div>"

        with db._cursor() as cur:
            # 1. Distribuição de top70_sniper_hold_rate nas migrações com recovery
            cur.execute("""
                SELECT
                    CASE
                        WHEN top70_sniper_hold_rate IS NULL THEN 'NULL (sem dado)'
                        WHEN top70_sniper_hold_rate = 0     THEN '0% exato'
                        WHEN top70_sniper_hold_rate <= 0.05 THEN '0-5%'
                        WHEN top70_sniper_hold_rate <= 0.10 THEN '5-10%'
                        WHEN top70_sniper_hold_rate <= 0.20 THEN '10-20%'
                        WHEN top70_sniper_hold_rate <= 0.30 THEN '20-30%'
                        ELSE '>30%'
                    END AS bucket,
                    COUNT(*) AS n,
                    COUNT(CASE WHEN recovery = TRUE  THEN 1 END) AS n_rec,
                    COUNT(CASE WHEN recovery = FALSE THEN 1 END) AS n_norec,
                    ROUND(AVG(CASE WHEN recovery IS NOT NULL
                        THEN (CASE WHEN recovery THEN 1.0 ELSE 0.0 END) END)::numeric, 3) AS rec_rate,
                    ROUND(AVG(top70_sniper_hold_rate)::numeric, 4) AS avg_sniper,
                    MIN(top70_sniper_hold_rate) AS min_sniper,
                    MAX(top70_sniper_hold_rate) AS max_sniper
                FROM migrations
                WHERE ts_migration IS NOT NULL
                GROUP BY 1
                ORDER BY MIN(COALESCE(top70_sniper_hold_rate, -1))
            """)
            hist_rows = [dict(r) for r in cur.fetchall()]

            # 2. Impacto do filtro atual — quantos tokens estão sendo bloqueados
            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(CASE WHEN top70_sniper_hold_rate > 0.05 THEN 1 END) AS blocked,
                    COUNT(CASE WHEN top70_sniper_hold_rate <= 0.05 THEN 1 END) AS passed,
                    COUNT(CASE WHEN top70_sniper_hold_rate IS NULL THEN 1 END) AS no_data,
                    -- Recovery rate dos bloqueados vs passados
                    ROUND(AVG(CASE WHEN top70_sniper_hold_rate > 0.05 AND recovery IS NOT NULL
                        THEN (CASE WHEN recovery THEN 1.0 ELSE 0.0 END) END)::numeric, 3) AS rec_blocked,
                    ROUND(AVG(CASE WHEN top70_sniper_hold_rate <= 0.05 AND recovery IS NOT NULL
                        THEN (CASE WHEN recovery THEN 1.0 ELSE 0.0 END) END)::numeric, 3) AS rec_passed
                FROM migrations
                WHERE ts_migration IS NOT NULL AND recovery IS NOT NULL
            """)
            impact = dict(cur.fetchone())

            # 3. WR live por bucket de sniper (trades com outcome GMGN)
            cur.execute("""
                SELECT
                    CASE
                        WHEN m.top70_sniper_hold_rate IS NULL THEN 'NULL'
                        WHEN m.top70_sniper_hold_rate = 0     THEN '0% exato'
                        WHEN m.top70_sniper_hold_rate <= 0.05 THEN '0-5%'
                        WHEN m.top70_sniper_hold_rate <= 0.10 THEN '5-10%'
                        WHEN m.top70_sniper_hold_rate <= 0.20 THEN '10-20%'
                        ELSE '>20%'
                    END AS bucket,
                    COUNT(*) AS n,
                    COUNT(CASE WHEN t.status = 'tp'
                        OR (t.status = 'expired' AND t.pnl_pct_real > 0) THEN 1 END) AS n_tp,
                    COUNT(CASE WHEN t.status = 'sl'
                        OR (t.status = 'expired' AND t.pnl_pct_real <= 0) THEN 1 END) AS n_sl,
                    ROUND(AVG(m.top70_sniper_hold_rate)::numeric, 4) AS avg_sniper
                FROM trades t
                JOIN migrations m ON m.mint = t.mint
                WHERE t.status IN ('tp', 'sl', 'expired')
                  AND t.pnl_pct_real IS NOT NULL
                GROUP BY 1
                ORDER BY MIN(COALESCE(m.top70_sniper_hold_rate, -1))
            """)
            live_rows = [dict(r) for r in cur.fetchall()]

            # 4. Cobertura — % de migrações que têm top70_sniper_hold_rate preenchido
            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(top70_sniper_hold_rate) AS with_data
                FROM migrations WHERE ts_migration IS NOT NULL
            """)
            cov = dict(cur.fetchone())

            # 5. Diagnóstico dos NULLs — de onde vêm?
            # 5a. Distribuição temporal — NULLs concentrados em época específica?
            cur.execute("""
                SELECT
                    DATE_TRUNC('week', ts_migration) AS semana,
                    COUNT(*) AS total,
                    COUNT(top70_sniper_hold_rate) AS com_dado,
                    COUNT(*) - COUNT(top70_sniper_hold_rate) AS null_count
                FROM migrations
                WHERE ts_migration IS NOT NULL
                GROUP BY 1
                ORDER BY 1 DESC
                LIMIT 12
            """)
            null_temporal = [dict(r) for r in cur.fetchall()]

            # 5b. NULLs têm sniper_wallets (count) preenchido? Campo diferente do rate.
            cur.execute("""
                SELECT
                    CASE WHEN top70_sniper_hold_rate IS NULL THEN 'NULL rate' ELSE 'tem rate' END AS grupo,
                    COUNT(*) AS n,
                    COUNT(sniper_wallets) AS com_sniper_count,
                    COUNT(ts_detected) AS com_watchlist,
                    ROUND(AVG(CASE WHEN recovery IS NOT NULL
                        THEN (CASE WHEN recovery THEN 1.0 ELSE 0.0 END) END)::numeric, 3) AS rec_rate
                FROM migrations
                WHERE ts_migration IS NOT NULL
                GROUP BY 1
            """)
            null_vs_has = [dict(r) for r in cur.fetchall()]

            # 5c. NULLs — qual exchange, bc_progress, e se vieram da watchlist
            cur.execute("""
                SELECT
                    COALESCE(exchange, 'unknown') AS exchange,
                    COUNT(*) AS n,
                    COUNT(top70_sniper_hold_rate) AS com_rate,
                    COUNT(*) - COUNT(top70_sniper_hold_rate) AS null_rate,
                    ROUND(AVG(CASE WHEN recovery IS NOT NULL
                        THEN (CASE WHEN recovery THEN 1.0 ELSE 0.0 END) END)::numeric, 3) AS rec_rate
                FROM migrations
                WHERE ts_migration IS NOT NULL
                GROUP BY 1
                ORDER BY n DESC
                LIMIT 10
            """)
            null_by_exchange = [dict(r) for r in cur.fetchall()]

        def _wr(tp, sl):
            total = (tp or 0) + (sl or 0)
            if not total: return "—", "#484f58"
            wr = (tp or 0) / total
            color = "#3fb950" if wr >= 0.35 else ("#d29922" if wr >= 0.20 else "#f85149")
            return f"{wr*100:.0f}%", color

        # Tabela histórica
        hist_html = ""
        for r in hist_rows:
            n, n_rec, n_norec = r["n"], r["n_rec"] or 0, r["n_norec"] or 0
            rr = r["rec_rate"]
            rr_str = f"{float(rr)*100:.1f}%" if rr is not None else "—"
            rr_color = "#3fb950" if rr and rr >= 0.35 else ("#d29922" if rr and rr >= 0.20 else "#f85149") if rr else "#484f58"
            blocked = "⛔ bloqueado" if r["bucket"] not in ("NULL (sem dado)", "0% exato", "0-5%") else "✅ passa"
            hist_html += (f"<tr><td>{r['bucket']}</td><td>{n}</td>"
                          f"<td style='color:{rr_color}'>{rr_str}</td>"
                          f"<td>{n_rec}</td><td>{n_norec}</td>"
                          f"<td class='dim'>{r['avg_sniper'] or '—'}</td>"
                          f"<td>{blocked}</td></tr>")

        # Tabela live
        live_html = ""
        for r in live_rows:
            n, n_tp, n_sl = r["n"], r["n_tp"] or 0, r["n_sl"] or 0
            wr_str, wr_color = _wr(n_tp, n_sl)
            blocked = "⛔" if r["bucket"] not in ("NULL", "0% exato", "0-5%") else "✅"
            live_html += (f"<tr><td>{r['bucket']}</td><td>{n}</td>"
                          f"<td style='color:{wr_color}'>{wr_str}</td>"
                          f"<td>{n_tp}</td><td>{n_sl}</td>"
                          f"<td class='dim'>{r['avg_sniper'] or '—'}</td>"
                          f"<td>{blocked}</td></tr>")

        # Impacto do filtro
        tot   = impact["total"] or 1
        bloc  = impact["blocked"] or 0
        pas   = impact["passed"] or 0
        nd    = impact["no_data"] or 0
        rb    = impact["rec_blocked"]
        rp    = impact["rec_passed"]
        rb_s  = f"{float(rb)*100:.1f}%" if rb is not None else "—"
        rp_s  = f"{float(rp)*100:.1f}%" if rp is not None else "—"
        delta = f"{(float(rb)-float(rp))*100:+.1f}pp" if rb is not None and rp is not None else "—"
        delta_color = "#3fb950" if rb and rp and float(rb) < float(rp) else "#f85149"

        cov_pct = f"{cov['with_data']/cov['total']*100:.0f}%" if cov['total'] else "—"

        # Tabelas do diagnóstico NULL — construídas fora do f-string (backslash issue)
        def _rr(v):
            return f"{float(v)*100:.1f}%" if v is not None else "—"

        null_vs_has_html = ""
        for r in null_vs_has:
            n = r["n"] or 1
            sc_pct = int((r["com_sniper_count"] or 0) / n * 100)
            wl_pct = int((r["com_watchlist"] or 0) / n * 100)
            null_vs_has_html += (
                f"<tr><td>{r['grupo']}</td><td>{r['n']}</td>"
                f"<td>{r['com_sniper_count']} ({sc_pct}%)</td>"
                f"<td>{r['com_watchlist']} ({wl_pct}%)</td>"
                f"<td>{_rr(r['rec_rate'])}</td></tr>"
            )

        null_by_exch_html = ""
        for r in null_by_exchange:
            null_n = r["null_rate"] or 0
            total_n = r["n"] or 1
            null_color = "#f85149" if null_n > total_n * 0.5 else "#8b949e"
            null_by_exch_html += (
                f"<tr><td>{r['exchange']}</td><td>{r['n']}</td>"
                f"<td style='color:#3fb950'>{r['com_rate']}</td>"
                f"<td style='color:{null_color}'>{null_n}</td>"
                f"<td>{_rr(r['rec_rate'])}</td></tr>"
            )

        null_temporal_html = ""
        for r in null_temporal:
            semana = r["semana"].strftime("%Y-%m-%d") if r["semana"] else "—"
            total_n = r["total"] or 1
            null_n = r["null_count"] or 0
            pct_null = int(null_n / total_n * 100)
            null_color = "#f85149" if pct_null > 50 else "#8b949e"
            null_temporal_html += (
                f"<tr><td>{semana}</td><td>{r['total']}</td>"
                f"<td style='color:#3fb950'>{r['com_dado']}</td>"
                f"<td>{null_n}</td>"
                f"<td style='color:{null_color}'>{pct_null}%</td></tr>"
            )

        summary = (
            _box("Cobertura campo", f"{cov['with_data']}/{cov['total']} ({cov_pct})") +
            _box("Bloqueados pelo filtro >5%", f"{bloc} ({bloc/tot*100:.0f}%)", "#f85149") +
            _box("Passam pelo filtro ≤5%", f"{pas} ({pas/tot*100:.0f}%)", "#3fb950") +
            _box("Sem dado (NULL)", f"{nd} ({nd/tot*100:.0f}%)", "#8b949e") +
            _box("Recovery bloqueados", rb_s, "#f85149") +
            _box("Recovery passados", rp_s, "#3fb950") +
            _box("Delta (bloqueado - passado)", delta, delta_color)
        )

        html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Sniper Filter Analysis</title>{CSS}
</head><body>
<h1>FILTER_SNIPER_MAX — Análise do filtro top70_sniper_hold_rate</h1>
<p style='color:#8b949e;font-size:11px;margin-bottom:12px'>
  top70_sniper_hold_rate = % do supply que os 70 maiores snipers ainda seguram.<br>
  Filtro atual: FILTER_SNIPER_MAX=0.05 (rejeita tokens com sniper hold &gt;5%).<br>
  H4 (CLAUDE.md): "sniper alto levemente positivo (+0.7pp)" — refutada historicamente.<br>
  Análise: os dados confirmam ou contradizem o filtro?
</p>
<div style='margin-bottom:12px'>
  <a href='/analise/wr-breakdown' style='color:#58a6ff;margin-right:14px'>WR Breakdown</a>
  <a href='/analise/h22-correlation' style='color:#58a6ff;margin-right:14px'>H22</a>
</div>

{summary}

<br><br>
<h2>▸ Recovery histórico por bucket (migrations, n={cov['total']})</h2>
<p style='color:#484f58;font-size:11px'>
  Fonte: migrations.recovery = mc_1h &gt; mc_bottom × 1.3. DB — não usar como decisão final,
  mas serve para identificar correlação bruta com o campo.
</p>
<table>
  <thead><tr>
    <th>Bucket sniper</th><th>n</th><th>Recovery rate</th>
    <th>n_recovery</th><th>n_no_rec</th><th>avg sniper</th><th>Filtro atual</th>
  </tr></thead>
  <tbody>{hist_html}</tbody>
</table>

<br>
<h2>▸ WR live por bucket (trades com outcome GMGN)</h2>
<p style='color:#484f58;font-size:11px'>
  Fonte: trades × migrations JOIN. status='tp'/'sl'/'expired' com pnl_pct_real.<br>
  GMGN é ground truth. n pequeno — interpretar com cautela.
</p>
<table>
  <thead><tr>
    <th>Bucket sniper</th><th>n trades</th><th>WR</th>
    <th>TPs</th><th>SLs</th><th>avg sniper</th><th>Filtro</th>
  </tr></thead>
  <tbody>{live_html if live_html else "<tr><td colspan='7' class='dim'>Sem trades com dados de sniper</td></tr>"}</tbody>
</table>

<br>
<p style='color:#484f58;font-size:11px'>
  Delta negativo (bloqueados com recovery MENOR) = filtro correto.<br>
  Delta positivo (bloqueados com recovery MAIOR) = filtro está rejeitando tokens bons.
</p>

<br>
<h2>▸ Diagnóstico dos 38% NULL — de onde vêm?</h2>
<p style='color:#484f58;font-size:11px'>
  799 tokens passam pelo filtro sem dado de sniper. Recovery deles = 20.3% (pior que os com dado).<br>
  Perguntas: o NULL é temporal (campo não existia antes)? Vem de exchange específica? Falta watchlist?
</p>

<b style='color:#8b949e;font-size:11px'>NULL rate vs tem rate — cobertura de campos relacionados</b>
<table style='width:auto;margin:8px 0 16px'>
  <thead><tr>
    <th>Grupo</th><th>n</th><th>tem sniper_count</th><th>tem watchlist</th><th>Recovery rate</th>
  </tr></thead>
  <tbody>{null_vs_has_html}</tbody>
</table>

<b style='color:#8b949e;font-size:11px'>NULL por exchange</b>
<table style='width:auto;margin:8px 0 16px'>
  <thead><tr>
    <th>Exchange</th><th>n total</th><th>com rate</th><th>NULL rate</th><th>Recovery</th>
  </tr></thead>
  <tbody>{null_by_exch_html}</tbody>
</table>

<b style='color:#8b949e;font-size:11px'>Distribuição temporal (últimas 12 semanas)</b>
<table style='width:auto;margin:8px 0 16px'>
  <thead><tr>
    <th>Semana</th><th>Total migrações</th><th>Com rate</th><th>NULL</th><th>% NULL</th>
  </tr></thead>
  <tbody>{null_temporal_html}</tbody>
</table>
</body></html>"""
        return html

    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


@app.route("/analise/trade-autopsy")
def analise_trade_autopsy():
    """Autópsia dos trades reais — O que separa TPs de SLs?
    Pergunta central: o SL bateu porque o sinal estava errado (dump continuou)
    ou porque o bounce era real mas fraco demais para o TP de 75%?
    """
    import traceback
    try:
        CSS = ("<style>body{background:#0d1117;color:#e6edf3;font:13px/1.6 monospace;padding:20px}"
               "a{color:#58a6ff;text-decoration:none}table{border-collapse:collapse;width:100%}"
               "th{background:#161b22;padding:6px 10px;text-align:left;font-size:11px;color:#8b949e}"
               "td{padding:5px 10px;border-bottom:1px solid #21262d;font-size:12px}"
               ".box{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:12px 16px;"
               "margin:8px 4px;display:inline-block;vertical-align:top;min-width:110px}"
               ".lbl{color:#8b949e;font-size:11px}.val{font-size:22px;font-weight:bold;margin-top:2px}"
               ".nav a{color:#58a6ff;margin-right:14px;font-size:12px}"
               ".green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}"
               ".dim{color:#484f58}.warn{color:#e3b341}"
               "h2{color:#8b949e;font-size:12px;margin:24px 0 6px;letter-spacing:.5px}"
               "h1{color:#58a6ff;font-size:16px;margin-bottom:4px}"
               ".bar{display:inline-block;height:10px;background:#3fb950;border-radius:2px;margin-right:6px;vertical-align:middle}"
               ".bar-red{background:#f85149}"
               "tr:hover td{background:#161b22}</style>")

        def _box(label, val, color="#e6edf3", sub=""):
            return (f"<div class='box'><div class='lbl'>{label}</div>"
                    f"<div class='val' style='color:{color}'>{val}</div>"
                    f"{'<div style=color:#484f58;font-size:10px>' + sub + '</div>' if sub else ''}"
                    f"</div>")

        def _wr_color(wr):
            if wr is None: return "#484f58"
            return "#3fb950" if wr >= 0.40 else ("#d29922" if wr >= 0.25 else "#f85149")

        def _pct(v, decimals=0):
            if v is None: return "<span class='dim'>—</span>"
            c = "#3fb950" if v > 0 else ("#f85149" if v < 0 else "#8b949e")
            fmt = f"{{:+.{decimals}f}}%"
            return f"<span style='color:{c}'>{fmt.format(v * 100)}</span>"

        def _num(v, decimals=1):
            if v is None: return "<span class='dim'>—</span>"
            return f"{v:.{decimals}f}"

        with db._cursor() as cur:
            # ===================================================================
            # BASE CTE: todos os trades com classificação de outcome + contexto
            # entry_mc_real calculado de entry_price_usd/tokens_received (preço real)
            # Fallback para entry_expected quando fill não disponível
            # ===================================================================
            cur.execute("""
                WITH base AS (
                    SELECT
                        t.id,
                        t.mint,
                        t.symbol,
                        t.status,
                        t.ts_signal,
                        t.exit_ts,
                        t.pnl_pct_real,
                        t.entry_price_usd,
                        t.tokens_received,
                        t.entry_expected,
                        t.mc_bottom,
                        t.mc_migration          AS t_mc_mig,
                        t.tp_pct,
                        t.sl_pct,
                        t.fill_source,
                        t.amount_usd,
                        -- Entry MC real: de dados de fill; fallback para expected
                        COALESCE(
                            CASE WHEN t.entry_price_usd > 0 AND t.tokens_received > 0
                                 THEN t.entry_price_usd / t.tokens_received * 1e9
                            END,
                            t.entry_expected
                        ) AS entry_mc,
                        -- Outcome limpo
                        CASE
                            WHEN t.status IN ('tp')                              THEN 'tp'
                            WHEN t.status = 'trailing_active'                    THEN 'tp'
                            WHEN t.status = 'expired' AND t.pnl_pct_real > 0    THEN 'tp'
                            WHEN t.status IN ('sl')                              THEN 'sl'
                            WHEN t.status = 'expired' AND t.pnl_pct_real <= 0   THEN 'sl'
                            WHEN t.status = 'expired' AND t.pnl_pct_real IS NULL THEN NULL
                            ELSE NULL
                        END AS outcome,
                        -- bounce_signals
                        b.mc_min            AS b_mc_bottom,
                        b.mc_at_signal      AS b_mc_entry,
                        b.mc_migration      AS b_mc_mig,
                        b.bounce_pct        AS b_bounce_pct,
                        b.drop_from_mig     AS b_drop_pct,
                        b.elapsed_sec / 60.0 AS b_elapsed_min,
                        b.absorption_events,
                        -- migrations
                        m.smart_wallets,
                        m.renowned_wallets,
                        m.creator_token_status,
                        m.top10_holder_rate,
                        m.time_to_bottom,
                        -- entry_ratio: onde entramos no range migração→fundo
                        CASE WHEN b.mc_migration > 0
                             THEN b.mc_at_signal / b.mc_migration
                             ELSE NULL END AS entry_ratio,
                        -- Tempo até exit em minutos
                        CASE WHEN t.exit_ts IS NOT NULL AND t.ts_signal IS NOT NULL
                             THEN EXTRACT(EPOCH FROM (t.exit_ts - t.ts_signal)) / 60.0
                             ELSE NULL END AS mins_to_exit
                    FROM trades t
                    LEFT JOIN bounce_signals b ON b.mint = t.mint
                    LEFT JOIN migrations     m ON m.mint = t.mint
                    WHERE t.status NOT IN ('pending', 'error', 'open')
                ),
                tick_agg AS (
                    SELECT
                        b.id AS trade_id,
                        COUNT(tt.id)                                                           AS tick_count,
                        MAX(COALESCE(tt.mc_high, tt.mc))                                       AS max_mc_ever,
                        MIN(COALESCE(tt.mc_low,  tt.mc))                                       AS min_mc_ever,
                        MAX(CASE WHEN tt.tick_ts <= b.ts_signal + INTERVAL '2 minutes'
                                 THEN COALESCE(tt.mc_high, tt.mc) END)                         AS max_mc_2min,
                        MIN(CASE WHEN tt.tick_ts <= b.ts_signal + INTERVAL '2 minutes'
                                 THEN COALESCE(tt.mc_low,  tt.mc) END)                         AS min_mc_2min,
                        MAX(CASE WHEN tt.tick_ts <= b.ts_signal + INTERVAL '5 minutes'
                                 THEN COALESCE(tt.mc_high, tt.mc) END)                         AS max_mc_5min,
                        MAX(CASE WHEN tt.tick_ts <= b.ts_signal + INTERVAL '10 minutes'
                                 THEN COALESCE(tt.mc_high, tt.mc) END)                         AS max_mc_10min,
                        MAX(CASE WHEN tt.tick_ts <= b.ts_signal + INTERVAL '20 minutes'
                                 THEN COALESCE(tt.mc_high, tt.mc) END)                         AS max_mc_20min
                    FROM base b
                    JOIN token_ticks tt ON tt.mint = b.mint
                        AND tt.tick_ts >= b.ts_signal
                        AND tt.tick_ts <= b.ts_signal + INTERVAL '30 minutes'
                    GROUP BY b.id
                ),
                -- Pré-exit: ticks entre ts_signal e exit_ts (o que aconteceu ENQUANTO segurávamos)
                -- IMPORTANTE: usa tt.mc (close), NÃO mc_high — high é inflado por spikes intra-candle do pAMM
                tick_pre AS (
                    SELECT
                        b.id AS trade_id,
                        COUNT(tt.id) AS pre_tick_count,
                        MAX(tt.mc)   AS max_mc_pre,
                        MIN(tt.mc)   AS min_mc_pre
                    FROM base b
                    JOIN token_ticks tt ON tt.mint = b.mint
                        AND tt.tick_ts >= b.ts_signal
                        AND tt.tick_ts <= b.exit_ts
                    WHERE b.exit_ts IS NOT NULL
                    GROUP BY b.id
                ),
                -- Pós-exit: ticks nos 30min após exit_ts (o que o token fez DEPOIS de sair)
                -- Idem: usa close para medir nível sustentado, não spike momentâneo
                tick_post AS (
                    SELECT
                        b.id AS trade_id,
                        COUNT(tt.id) AS post_tick_count,
                        MAX(tt.mc)   AS max_mc_post,
                        MIN(tt.mc)   AS min_mc_post
                    FROM base b
                    JOIN token_ticks tt ON tt.mint = b.mint
                        AND tt.tick_ts > b.exit_ts
                        AND tt.tick_ts <= b.exit_ts + INTERVAL '30 minutes'
                    WHERE b.exit_ts IS NOT NULL
                    GROUP BY b.id
                )
                SELECT
                    b.*,
                    ta.tick_count,
                    ta.max_mc_ever,
                    ta.min_mc_ever,
                    ta.max_mc_2min,
                    ta.min_mc_2min,
                    ta.max_mc_5min,
                    ta.max_mc_10min,
                    ta.max_mc_20min,
                    -- Gains relativos à entry_mc (janela total)
                    CASE WHEN b.entry_mc > 0 AND ta.max_mc_ever  IS NOT NULL
                         THEN (ta.max_mc_ever  - b.entry_mc) / b.entry_mc END AS max_gain_pct,
                    CASE WHEN b.entry_mc > 0 AND ta.min_mc_ever  IS NOT NULL
                         THEN (ta.min_mc_ever  - b.entry_mc) / b.entry_mc END AS min_loss_pct,
                    CASE WHEN b.entry_mc > 0 AND ta.max_mc_2min  IS NOT NULL
                         THEN (ta.max_mc_2min  - b.entry_mc) / b.entry_mc END AS early_gain_pct,
                    CASE WHEN b.entry_mc > 0 AND ta.max_mc_5min  IS NOT NULL
                         THEN (ta.max_mc_5min  - b.entry_mc) / b.entry_mc END AS gain_5min_pct,
                    CASE WHEN b.entry_mc > 0 AND ta.max_mc_10min IS NOT NULL
                         THEN (ta.max_mc_10min - b.entry_mc) / b.entry_mc END AS gain_10min_pct,
                    -- PRÉ-EXIT: ganho máximo enquanto segurávamos
                    tp.pre_tick_count,
                    tp.max_mc_pre,
                    tp.min_mc_pre,
                    CASE WHEN b.entry_mc > 0 AND tp.max_mc_pre IS NOT NULL
                         THEN (tp.max_mc_pre - b.entry_mc) / b.entry_mc END AS max_gain_before_exit,
                    CASE WHEN b.entry_mc > 0 AND tp.min_mc_pre IS NOT NULL
                         THEN (tp.min_mc_pre - b.entry_mc) / b.entry_mc END AS min_loss_before_exit,
                    -- PÓS-EXIT: quanto o token subiu DEPOIS de sairmos
                    tpo.post_tick_count,
                    tpo.max_mc_post,
                    tpo.min_mc_post,
                    CASE WHEN b.entry_mc > 0 AND tpo.max_mc_post IS NOT NULL
                         THEN (tpo.max_mc_post - b.entry_mc) / b.entry_mc END AS max_gain_after_exit,
                    CASE WHEN b.entry_mc > 0 AND tpo.min_mc_post IS NOT NULL
                         THEN (tpo.min_mc_post - b.entry_mc) / b.entry_mc END AS min_loss_after_exit
                FROM base b
                LEFT JOIN tick_agg ta  ON ta.trade_id  = b.id
                LEFT JOIN tick_pre  tp  ON tp.trade_id  = b.id
                LEFT JOIN tick_post tpo ON tpo.trade_id = b.id
                WHERE b.outcome IS NOT NULL
                ORDER BY b.id ASC
            """)
            rows = [dict(r) for r in cur.fetchall()]

        # --- Classificação de causa-raiz por SL ---
        def _sl_cause(r):
            if r["outcome"] == "tp":
                return "tp"
            tc = r.get("tick_count") or 0
            if tc < 3:
                return "sem_ticks"
            early = r.get("early_gain_pct")
            max_g = r.get("max_gain_pct")
            mins  = r.get("mins_to_exit")
            tp_pct = r.get("tp_pct") or 0.75
            if early is not None and early < -0.05:
                return "dead_from_start"       # caiu desde o tick 1 — sinal errado
            if mins is not None and mins < 5:
                return "fast_dump"             # SL em <5min — sem chance de setup
            if max_g is not None and max_g >= tp_pct * 0.7:
                return "near_tp"               # chegou perto do TP mas não ativou trailing
            if max_g is not None and max_g >= 0.10:
                return "bounce_then_fail"      # bounçou +10% mas não segurou
            return "slow_bleed"                # subida mínima, esgotamento lento

        for r in rows:
            r["sl_cause"] = _sl_cause(r)

        # --- Classificação de qualidade de exit (pré/pós) ---
        def _exit_quality(r):
            """Classifica o exit em 5 categorias baseado no que aconteceu antes e depois."""
            if r["outcome"] != "sl":
                return "tp"
            tp_pct   = r.get("tp_pct") or 0.75
            pre_gain = r.get("max_gain_before_exit")   # max gain ENQUANTO segurava
            post_gain= r.get("max_gain_after_exit")    # max gain nos 30min APÓS sair
            pre_ticks = r.get("pre_tick_count") or 0
            post_ticks= r.get("post_tick_count") or 0
            if pre_ticks < 2 and post_ticks < 2:
                return "sem_dados"
            # Atingiu TP activation (+75%) antes de sair? → trailing deveria ter capturado
            hit_tp_before = pre_gain is not None and pre_gain >= tp_pct
            # Token recuperou ao nível de TP após o SL?
            hit_tp_after  = post_gain is not None and post_gain >= tp_pct
            if hit_tp_before:
                return "trailing_falhou"   # preço passou do TP mas trailing não executou
            if hit_tp_after:
                return "sl_prematuro"      # saímos antes, token foi ao TP sem nós
            if post_gain is not None and post_gain >= 0.20:
                return "sl_mas_recuperou"  # não chegou ao TP mas subiu 20%+ depois
            return "sl_correto"            # token caiu ou ficou flat após a saída

        for r in rows:
            r["exit_quality"] = _exit_quality(r)
            tp_pct = r.get("tp_pct") or 0.75
            pre_gain  = r.get("max_gain_before_exit")
            post_gain = r.get("max_gain_after_exit")
            pre_ticks = r.get("pre_tick_count") or 0
            # Boolean flags explícitos para o template
            r["hit_tp_before_exit"] = pre_gain is not None and pre_gain >= tp_pct
            r["hit_tp_after_exit"]  = post_gain is not None and post_gain >= tp_pct
            # Spike suspect: max_gain muito alto mas poucos ticks — provavelmente intra-candle spike
            # Critério: pre_ticks < 5 E max_gain_before > 100% (improvável sem spike em pool pequena)
            r["spike_suspect"] = (r["hit_tp_before_exit"] and pre_ticks < 5
                                  and pre_gain is not None and pre_gain > 1.00)

        # --- Métricas de cobertura ---
        total   = len(rows)
        n_tp    = sum(1 for r in rows if r["outcome"] == "tp")
        n_sl    = sum(1 for r in rows if r["outcome"] == "sl")
        wr_base = n_tp / total if total else 0
        n_ticks_ok = sum(1 for r in rows if (r.get("tick_count") or 0) >= 10)
        n_real_fill = sum(1 for r in rows if r.get("fill_source") in ("helius", "gmgn"))

        # --- Helper: tabela de WR por bucket ---
        def _bucket_table(buckets, has_traj=False):
            rows_html = ""
            for label, subset in buckets:
                n = len(subset)
                if n == 0:
                    rows_html += f"<tr><td>{label}</td><td class='dim'>—</td><td class='dim'>—</td><td class='dim'>—</td><td class='dim'>—</td>"
                    if has_traj: rows_html += "<td class='dim'>—</td><td class='dim'>—</td>"
                    rows_html += "</tr>"
                    continue
                n_tp_s = sum(1 for r in subset if r["outcome"] == "tp")
                n_sl_s = sum(1 for r in subset if r["outcome"] == "sl")
                wr = n_tp_s / n if n else 0
                delta = wr - wr_base
                delta_str = f"{delta*100:+.0f}pp"
                delta_col = "#3fb950" if delta > 0.05 else ("#f85149" if delta < -0.05 else "#8b949e")
                bar_w = int(wr * 80)
                bar_html = f"<div class='bar' style='width:{bar_w}px'></div>"
                wr_col = _wr_color(wr)
                extra = ""
                if has_traj:
                    avg_max = _avg([r.get("max_gain_pct") for r in subset])
                    avg_min = _avg([r.get("min_loss_pct") for r in subset])
                    extra = f"<td>{_pct(avg_max)}</td><td>{_pct(avg_min)}</td>"
                rows_html += (
                    f"<tr><td>{label}</td>"
                    f"<td>{n}</td>"
                    f"<td>{bar_html}<b style='color:{wr_col}'>{wr*100:.0f}%</b> "
                    f"<span style='color:{delta_col};font-size:11px'>({delta_str})</span></td>"
                    f"<td class='green'>{n_tp_s}</td><td class='red'>{n_sl_s}</td>"
                    f"{extra}</tr>"
                )
            return rows_html

        def _avg(lst):
            lst2 = [x for x in lst if x is not None]
            return sum(lst2) / len(lst2) if lst2 else None

        # ================================================================
        # SEÇÃO 2 — Causa-raiz dos SLs
        # ================================================================
        sl_rows = [r for r in rows if r["outcome"] == "sl"]
        cause_counts = {}
        for r in sl_rows:
            cause_counts[r["sl_cause"]] = cause_counts.get(r["sl_cause"], 0) + 1

        cause_labels = {
            "dead_from_start": ("⬇ Dead from start", "Caiu desde o tick 1 — sinal errado, dump continuou", "#f85149"),
            "fast_dump":       ("⚡ Fast dump <5min", "SL em <5min — sem chance, entrada prematura",         "#f85149"),
            "bounce_then_fail":("〜 Bounce → fail",  "Subiu +10% mas não sustentou — comprador fraco",     "#d29922"),
            "near_tp":         ("🎯 Near TP",         "Chegou perto do TP mas trailing não ativou",         "#e3b341"),
            "slow_bleed":      ("📉 Slow bleed",      "Subida mínima, esgotamento lento",                   "#8b949e"),
            "sem_ticks":       ("❓ Sem ticks",        "Poucos ticks — análise inconclusiva",                "#484f58"),
        }

        cause_summary_html = ""
        for key, (label, desc, color) in cause_labels.items():
            n_c = cause_counts.get(key, 0)
            pct_c = n_c / len(sl_rows) * 100 if sl_rows else 0
            bar_w = int(pct_c * 1.5)
            cause_summary_html += (
                f"<tr>"
                f"<td style='color:{color}'>{label}</td>"
                f"<td>{n_c} <span class='dim'>({pct_c:.0f}%)</span></td>"
                f"<td><div class='bar' style='width:{bar_w}px;background:{color}'></div></td>"
                f"<td class='dim' style='font-size:11px'>{desc}</td>"
                f"</tr>"
            )

        # Tabela individual dos SLs classificados
        sl_detail_html = ""
        for r in sl_rows:
            cause_key = r["sl_cause"]
            cause_lbl, _, cause_col = cause_labels.get(cause_key, (cause_key, "", "#8b949e"))
            mins = r.get("mins_to_exit")
            mins_str = f"{mins:.1f}m" if mins is not None else "—"
            tc = r.get("tick_count") or 0
            sl_detail_html += (
                f"<tr>"
                f"<td class='dim'>{r['id']}</td>"
                f"<td><b>{r.get('symbol','?')}</b></td>"
                f"<td style='color:{cause_col};font-size:11px'>{cause_lbl}</td>"
                f"<td>{_pct(r.get('early_gain_pct'), 0)}</td>"
                f"<td>{_pct(r.get('max_gain_pct'),   0)}</td>"
                f"<td class='dim'>{mins_str}</td>"
                f"<td class='dim'>{tc}</td>"
                f"<td>{_pct(r.get('b_drop_pct'), 0)}</td>"
                f"<td>{_pct(r.get('b_bounce_pct'), 0)}</td>"
                f"<td class='dim'>{r.get('smart_wallets') or '—'}</td>"
                f"</tr>"
            )

        # ================================================================
        # SEÇÃO 3 — drop_from_mig vs WR
        # ================================================================
        drop_buckets = [
            ("30–50%",  [r for r in rows if r.get("b_drop_pct") is not None and 0.30 <= r["b_drop_pct"] < 0.50]),
            ("50–70%",  [r for r in rows if r.get("b_drop_pct") is not None and 0.50 <= r["b_drop_pct"] < 0.70]),
            ("70–85%",  [r for r in rows if r.get("b_drop_pct") is not None and 0.70 <= r["b_drop_pct"] < 0.85]),
            (">85%",    [r for r in rows if r.get("b_drop_pct") is not None and r["b_drop_pct"] >= 0.85]),
            ("sem dado",[r for r in rows if r.get("b_drop_pct") is None]),
        ]
        drop_html = _bucket_table(drop_buckets, has_traj=True)

        # ================================================================
        # SEÇÃO 4 — entry_ratio vs WR
        # ================================================================
        er_buckets = [
            ("<25%",   [r for r in rows if r.get("entry_ratio") is not None and r["entry_ratio"] < 0.25]),
            ("25–35%", [r for r in rows if r.get("entry_ratio") is not None and 0.25 <= r["entry_ratio"] < 0.35]),
            ("35–45%", [r for r in rows if r.get("entry_ratio") is not None and 0.35 <= r["entry_ratio"] < 0.45]),
            ("45–55%", [r for r in rows if r.get("entry_ratio") is not None and 0.45 <= r["entry_ratio"] < 0.55]),
            ("55–65%", [r for r in rows if r.get("entry_ratio") is not None and 0.55 <= r["entry_ratio"] < 0.65]),
            (">65%",   [r for r in rows if r.get("entry_ratio") is not None and r["entry_ratio"] >= 0.65]),
            ("sem dado",[r for r in rows if r.get("entry_ratio") is None]),
        ]
        er_html = _bucket_table(er_buckets, has_traj=True)

        # ================================================================
        # SEÇÃO 5 — Velocidade do SL
        # ================================================================
        # Identifica trades ultra-rápidos (<1min) — candidatos a self-inflicted SL
        ultra_fast_rows = [r for r in sl_rows if r.get("mins_to_exit") is not None and r["mins_to_exit"] < 1]
        n_ultra_fast = len(ultra_fast_rows)

        sl_speed_buckets = [
            ("<1min ⚡",  [r for r in sl_rows if r.get("mins_to_exit") is not None and r["mins_to_exit"] < 1]),
            ("1–5min",   [r for r in sl_rows if r.get("mins_to_exit") is not None and 1  <= r["mins_to_exit"] < 5]),
            ("5–15min",  [r for r in sl_rows if r.get("mins_to_exit") is not None and 5  <= r["mins_to_exit"] < 15]),
            ("15–30min", [r for r in sl_rows if r.get("mins_to_exit") is not None and 15 <= r["mins_to_exit"] < 30]),
            (">30min",   [r for r in sl_rows if r.get("mins_to_exit") is not None and r["mins_to_exit"] >= 30]),
            ("sem dado", [r for r in sl_rows if r.get("mins_to_exit") is None]),
        ]
        speed_html = ""
        for label, subset in sl_speed_buckets:
            n_s = len(subset)
            if n_s == 0:
                speed_html += f"<tr><td>{label}</td><td class='dim'>—</td><td class='dim'>—</td><td class='dim'>—</td><td class='dim'>—</td></tr>"
                continue
            had_pos = sum(1 for r in subset if (r.get("max_gain_pct") or 0) > 0)
            pct_had_pos = had_pos / n_s * 100
            avg_max = _avg([r.get("max_gain_pct") for r in subset])
            avg_early = _avg([r.get("early_gain_pct") for r in subset])
            row_style = " style='background:#2d1a1a'" if "⚡" in label else ""
            speed_html += (
                f"<tr{row_style}><td>{label}</td>"
                f"<td>{n_s}</td>"
                f"<td>{had_pos} <span class='dim'>({pct_had_pos:.0f}%)</span></td>"
                f"<td>{_pct(avg_early, 0)}</td>"
                f"<td>{_pct(avg_max, 0)}</td>"
                f"</tr>"
            )

        # Detalhe dos ultra-fast (<1min) para diagnóstico
        ultra_fast_detail_html = ""
        for r in sorted(ultra_fast_rows, key=lambda x: x.get("mins_to_exit") or 0):
            m = r.get("mins_to_exit")
            secs = int(m * 60) if m is not None else None
            ultra_fast_detail_html += (
                f"<tr>"
                f"<td><b>{r.get('symbol','?')}</b></td>"
                f"<td class='red'>{('%ds' % secs) if secs is not None else '—'}</td>"
                f"<td>{_pct(r.get('early_gain_pct'), 0)}</td>"
                f"<td>{_pct(r.get('max_gain_pct'),   0)}</td>"
                f"<td>{_pct(r.get('b_bounce_pct'),    0)}</td>"
                f"<td>{_pct(r.get('b_drop_pct'),      0)}</td>"
                f"<td class='dim'>{r.get('smart_wallets') or '—'}</td>"
                f"<td class='dim'>{r.get('tick_count') or '—'}</td>"
                f"</tr>"
            )
        if not ultra_fast_detail_html:
            ultra_fast_detail_html = "<tr><td colspan='8' class='dim'>Nenhum SL em &lt;1min</td></tr>"

        if n_ultra_fast > 0:
            ultra_fast_section_html = (
                "<br>"
                "<div style='border:1px solid #f85149;border-radius:6px;padding:12px;background:#1a0d0d;max-width:900px'>"
                "<b style='color:#f85149'>SLs INSTANTANEOS (&lt;1min) — n=" + str(n_ultra_fast) + " — Diagnostico</b>"
                "<p style='color:#c9d1d9;font-size:11px;margin:8px 0'>"
                "<b>O mecanismo:</b> Pool pAMM tem ~79 SOL de liquidez. Nosso buy de $1-10 pode temporariamente"
                " empurrar o preco 5-15% para cima. Quando a ordem fill confirma, o preco natural reverte."
                " Com SL=15% (antigo): threshold = entry x 0.85 = mc_bottom x <b>0.9775</b> — praticamente no fundo."
                " Qualquer ruido revertia ao fundo e trigava o SL em segundos.<br>"
                " Com SL=25% (atual): threshold = entry x 0.75 = mc_bottom x <b>0.8625</b> — exige queda"
                " 13.75% abaixo do fundo gravado. Muito mais robusto contra noise.<br><br>"
                "<b>Dois subtipos distintos:</b><br>"
                "&bull; <span style='color:#f85149'>Real fast dump</span>: max_gain aprox 0% ou negativo — o token continuou caindo apos o sinal.<br>"
                "&bull; <span style='color:#d29922'>Spike artifact</span>: max_gain muito alto (&gt;50%) mas mc_high e intra-candle spike —"
                " um unico comprador movimentou o AMM pontualmente, o <em>kline high</em> registrou, mas o preco real nunca foi la."
                " mc_high em token_ticks ainda inclui spikes (diferente de mc_peak que foi corrigido para usar close)."
                "</p>"
                "<table style='max-width:780px'>"
                "<thead><tr>"
                "<th>Token</th><th>Duracao</th><th>Early +2min</th><th>Max gain*</th>"
                "<th>Bounce%</th><th>Drop%</th><th>Smart</th><th>Ticks</th>"
                "</tr></thead>"
                "<tbody>" + ultra_fast_detail_html + "</tbody>"
                "</table>"
                "<p style='color:#8b949e;font-size:10px;margin:6px 0 0'>"
                "*Max gain usa mc_high dos ticks — spikes intra-candle podem inflar este numero."
                " Tokens com max_gain&gt;50% e ticks=1-2 sao suspeitos de spike artifact, nao bounce real."
                "</p>"
                "</div>"
            )
        else:
            ultra_fast_section_html = ""

        # ================================================================
        # SEÇÃO 5b — Qualidade do exit: pré vs pós
        # ================================================================
        eq_labels = {
            "trailing_falhou":  ("Trailing falhou",   "Preco atingiu TP activation (+75%) mas trailing nao executou corretamente", "#f85149"),
            "sl_prematuro":     ("SL prematuro",      "Saimos antes — token recuperou ao nivel do TP nos 30min seguintes",          "#e3b341"),
            "sl_mas_recuperou": ("SL mas recuperou",  "Nao chegou ao TP mas subiu 20%+ apos nossa saida",                          "#d29922"),
            "sl_correto":       ("SL correto",        "Token nao recuperou — saida foi a decisao certa",                           "#3fb950"),
            "sem_dados":        ("Sem dados",         "Poucos ticks pre ou pos exit — analise inconclusiva",                       "#484f58"),
            "tp":               ("TP",                "Win",                                                                        "#3fb950"),
        }
        eq_counts = {}
        for r in rows:
            k = r.get("exit_quality", "sem_dados")
            eq_counts[k] = eq_counts.get(k, 0) + 1

        eq_summary_html = ""
        for key, (label, desc, color) in eq_labels.items():
            n_c = eq_counts.get(key, 0)
            if n_c == 0:
                continue
            pct_c = n_c / total * 100
            eq_summary_html += (
                f"<tr><td style='color:{color}'>{label}</td>"
                f"<td>{n_c} <span class='dim'>({pct_c:.0f}%)</span></td>"
                f"<td class='dim'>{desc}</td>"
                f"</tr>"
            )

        # Detalhe: trailing_falhou + sl_prematuro + sl_mas_recuperou (os evitáveis)
        evitable = [r for r in rows if r.get("exit_quality") in ("trailing_falhou", "sl_prematuro", "sl_mas_recuperou")]
        eq_detail_html = ""
        for r in sorted(evitable, key=lambda x: (x.get("exit_quality", ""), -(x.get("max_gain_before_exit") or 0))):
            eq = r.get("exit_quality", "")
            eq_colors = {"trailing_falhou": "#f85149", "sl_prematuro": "#e3b341", "sl_mas_recuperou": "#d29922"}
            eq_col = eq_colors.get(eq, "#8b949e")
            mins = r.get("mins_to_exit")
            pre_ticks  = r.get("pre_tick_count") or 0
            post_ticks = r.get("post_tick_count") or 0
            # hit_tp flags como badges
            hit_pre  = r.get("hit_tp_before_exit", False)
            hit_post = r.get("hit_tp_after_exit", False)
            spike    = r.get("spike_suspect", False)
            flag_pre  = "<span style='color:#3fb950;font-weight:bold'>YES</span>"  if hit_pre  else "<span class='dim'>—</span>"
            flag_post = "<span style='color:#e3b341;font-weight:bold'>YES</span>"  if hit_post else "<span class='dim'>—</span>"
            spike_flag = " <span style='color:#f85149;font-size:10px'>[SPIKE?]</span>" if spike else ""
            eq_detail_html += (
                f"<tr>"
                f"<td><b>{r.get('symbol','?')}</b>{spike_flag}</td>"
                f"<td style='color:{eq_col}'>{eq.replace('_',' ')}</td>"
                f"<td>{flag_pre}</td>"
                f"<td>{flag_post}</td>"
                f"<td>{_pct(r.get('max_gain_before_exit'), 0)}</td>"
                f"<td>{_pct(r.get('max_gain_after_exit'),  0)}</td>"
                f"<td>{_pct(r.get('min_loss_before_exit'), 0)}</td>"
                f"<td>{_pct(r.get('min_loss_after_exit'),  0)}</td>"
                f"<td class='dim'>{('%dm' % mins) if mins is not None else '—'}</td>"
                f"<td class='dim'>{pre_ticks}/{post_ticks}</td>"
                f"</tr>"
            )
        if not eq_detail_html:
            eq_detail_html = "<tr><td colspan='10' class='dim'>Sem SLs evitaveis com dados suficientes</td></tr>"

        n_evitable   = len(evitable)
        n_trailing_f = sum(1 for r in rows if r.get("exit_quality") == "trailing_falhou")
        n_trailing_real = sum(1 for r in rows if r.get("exit_quality") == "trailing_falhou"
                              and not r.get("spike_suspect"))
        n_trailing_spike= sum(1 for r in rows if r.get("exit_quality") == "trailing_falhou"
                              and r.get("spike_suspect"))
        n_sl_prem    = sum(1 for r in rows if r.get("exit_quality") == "sl_prematuro")
        n_sl_rec     = sum(1 for r in rows if r.get("exit_quality") == "sl_mas_recuperou")
        n_sl_correct = sum(1 for r in rows if r.get("exit_quality") == "sl_correto")

        # ================================================================
        # SEÇÃO 6 — TPs: trajetória e timing
        # ================================================================
        tp_rows = [r for r in rows if r["outcome"] == "tp"]
        tp_detail_html = ""
        for r in sorted(tp_rows, key=lambda x: x.get("mins_to_exit") or 999):
            mins = r.get("mins_to_exit")
            tp_detail_html += (
                f"<tr>"
                f"<td><b class='green'>{r.get('symbol','?')}</b></td>"
                f"<td>{f'{mins:.1f}m' if mins is not None else '—'}</td>"
                f"<td>{_pct(r.get('max_gain_pct'), 0)}</td>"
                f"<td>{_pct(r.get('pnl_pct_real'), 0)}</td>"
                f"<td>{_pct(r.get('entry_ratio'), 0)}</td>"
                f"<td>{_pct(r.get('b_drop_pct'), 0)}</td>"
                f"<td>{_pct(r.get('b_bounce_pct'), 0)}</td>"
                f"<td class='dim'>{r.get('smart_wallets') or '—'}</td>"
                f"<td class='dim'>{r.get('tick_count') or '—'}</td>"
                f"</tr>"
            )
        if not tp_detail_html:
            tp_detail_html = "<tr><td colspan='9' class='dim'>Nenhum TP com dados disponíveis</td></tr>"

        tp_mins = [r["mins_to_exit"] for r in tp_rows if r.get("mins_to_exit")]
        tp_median = sorted(tp_mins)[len(tp_mins)//2] if tp_mins else None
        tp_avg    = sum(tp_mins)/len(tp_mins) if tp_mins else None

        # ================================================================
        # SEÇÃO 7 — Smart wallets vs WR (trades live)
        # ================================================================
        smart_buckets = [
            ("1",   [r for r in rows if r.get("smart_wallets") == 1]),
            ("2",   [r for r in rows if r.get("smart_wallets") == 2]),
            ("3",   [r for r in rows if r.get("smart_wallets") == 3]),
            ("4",   [r for r in rows if r.get("smart_wallets") == 4]),
            ("5+",  [r for r in rows if (r.get("smart_wallets") or 0) >= 5]),
            ("NULL",[r for r in rows if r.get("smart_wallets") is None]),
        ]
        smart_html = _bucket_table(smart_buckets)

        # ================================================================
        # SEÇÃO 8 — bounce_pct vs WR (dentro dos trades ≥30%)
        # ================================================================
        bp_buckets = [
            ("30–40%",  [r for r in rows if r.get("b_bounce_pct") is not None and 0.30 <= r["b_bounce_pct"] < 0.40]),
            ("40–55%",  [r for r in rows if r.get("b_bounce_pct") is not None and 0.40 <= r["b_bounce_pct"] < 0.55]),
            ("55–70%",  [r for r in rows if r.get("b_bounce_pct") is not None and 0.55 <= r["b_bounce_pct"] < 0.70]),
            (">70%",    [r for r in rows if r.get("b_bounce_pct") is not None and r["b_bounce_pct"] >= 0.70]),
            ("sem dado",[r for r in rows if r.get("b_bounce_pct") is None]),
        ]
        bp_html = _bucket_table(bp_buckets, has_traj=True)

        # ================================================================
        # SEÇÃO 9 — Tabela individual completa
        # ================================================================
        cause_icon = {
            "dead_from_start":  "<span style='color:#f85149'>⬇</span>",
            "fast_dump":        "<span style='color:#f85149'>⚡</span>",
            "bounce_then_fail": "<span style='color:#d29922'>〜</span>",
            "near_tp":          "<span style='color:#e3b341'>🎯</span>",
            "slow_bleed":       "<span style='color:#8b949e'>📉</span>",
            "sem_ticks":        "<span class='dim'>❓</span>",
            "tp":               "<span class='green'>✅</span>",
        }
        all_detail_html = ""
        for r in rows:
            o = r["outcome"]
            out_str = f"<span class='green'>TP</span>" if o == "tp" else f"<span class='red'>SL</span>"
            icon = cause_icon.get(r["sl_cause"], "")
            er = r.get("entry_ratio")
            er_str = f"{er*100:.0f}%" if er is not None else "<span class='dim'>—</span>"
            all_detail_html += (
                f"<tr>"
                f"<td class='dim'>{r['id']}</td>"
                f"<td>{r.get('symbol','?')}</td>"
                f"<td>{out_str} {icon}</td>"
                f"<td>{_pct(r.get('b_drop_pct'), 0)}</td>"
                f"<td>{_pct(r.get('b_bounce_pct'), 0)}</td>"
                f"<td class='dim'>{('%dm' % r['b_elapsed_min']) if r.get('b_elapsed_min') else '—'}</td>"
                f"<td class='dim'>{er_str}</td>"
                f"<td class='dim'>{r.get('smart_wallets') or '—'}</td>"
                f"<td>{_pct(r.get('early_gain_pct'), 0)}</td>"
                f"<td>{_pct(r.get('max_gain_pct'),   0)}</td>"
                f"<td class='dim'>{('%dm' % r['mins_to_exit']) if r.get('mins_to_exit') else '—'}</td>"
                f"<td class='dim'>{r.get('tick_count') or '—'}</td>"
                f"<td>{_pct(r.get('pnl_pct_real'), 0)}</td>"
                f"</tr>"
            )

        # ================================================================
        # RENDER
        # ================================================================
        wr_col = _wr_color(wr_base)
        html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<title>Trade Autopsy</title>{CSS}
</head><body>
<h1>Trade Autopsy — O que separa TPs de SLs?</h1>
<p style='color:#8b949e;font-size:11px;margin-bottom:8px'>
  Pergunta central: o SL bateu porque o <b style='color:#f85149'>sinal estava errado</b> (dump continuou)
  ou porque o <b style='color:#d29922'>bounce era real mas fraco</b> (não atingiu TP de 75%)?<br>
  GMGN = ground truth. DB WR pode ter falsos positivos — usar com cautela.
</p>
<div class='nav'>
  <a href='/analise/wr-breakdown'>WR Breakdown</a>
  <a href='/analise/h22-correlation'>H22</a>
  <a href='/analise/post-sl-recovery'>Post-SL Recovery</a>
  <a href='/analise/backtest'>Backtester</a>
  <a href='/debug/open-trades'>Open Trades</a>
</div><br>

{_box("Trades analisados", total)}
{_box("TP", n_tp, "#3fb950")}
{_box("SL", n_sl, "#f85149")}
{_box("WR base", f"{wr_base*100:.0f}%", wr_col)}
{_box("Com ≥10 ticks", n_ticks_ok, "#58a6ff", "análise de trajetória confiável")}
{_box("Fill real (Helius/GMGN)", n_real_fill, "#8b949e", "entry_mc calculado do fill")}

<br><br>

<!-- SEÇÃO 2: Causa-raiz dos SLs -->
<h2>▸ 1. CAUSA-RAIZ DOS SLs — O que causou a perda? (n={len(sl_rows)} SLs)</h2>
<p style='color:#484f58;font-size:11px'>
  Classifica cada SL com base nos primeiros ticks após a entrada.<br>
  "Dead from start": early_gain &lt; -5% nos primeiros 2min — token nunca bounçou de verdade.<br>
  "Near TP": max_gain ≥ 70% do TP_PCT — token bounçou mas trailing não ativou a tempo.
</p>
<table style='max-width:700px'>
  <thead><tr><th>Causa</th><th>n SLs (%)</th><th>Dist.</th><th>Descrição</th></tr></thead>
  <tbody>{cause_summary_html}</tbody>
</table>

<br>
<p style='color:#8b949e;font-size:11px'>Detalhe por SL:</p>
<table>
  <thead><tr>
    <th>#</th><th>Token</th><th>Causa</th>
    <th>Early +2min</th><th>Max gain</th><th>Duração</th><th>Ticks</th>
    <th>Drop%</th><th>Bounce%</th><th>Smart</th>
  </tr></thead>
  <tbody>{sl_detail_html if sl_detail_html else "<tr><td colspan='10' class='dim'>Sem SLs com dados</td></tr>"}</tbody>
</table>

<br>
<!-- SEÇÃO 5: Velocidade do SL -->
<h2>▸ 2. VELOCIDADE DO SL — Quanto tempo durou antes de bater?</h2>
<p style='color:#484f58;font-size:11px'>
  "Teve pico positivo" = max_gain &gt; 0 antes de bater o SL (bounce existiu mas foi insuficiente).<br>
  SL em <b style='color:#f85149'>&lt;1min ⚡</b> = diagnóstico abaixo — pode ser SL auto-infligido pelo próprio impacto de preço da compra.
</p>
<table style='max-width:650px'>
  <thead><tr>
    <th>Tempo até SL</th><th>n SLs</th><th>Teve pico positivo</th>
    <th>Early gain +2min</th><th>Max gain</th>
  </tr></thead>
  <tbody>{speed_html}</tbody>
</table>

{ultra_fast_section_html}

<br>
<!-- SEÇÃO PRÉ/PÓS EXIT -->
<h2>&#9658; 2b. PRÉ vs PÓS EXIT — O que o token fez antes e depois de sairmos?</h2>
<p style='color:#484f58;font-size:11px'>
  <b>max_gain_before_exit</b> = max MC entre ts_signal e exit_ts (enquanto seguravamos).<br>
  <b>max_gain_after_exit</b> = max MC nos 30min apos exit_ts (o que deixamos na mesa).<br>
  <b>hit_tp_before</b> = preco atingiu threshold de ativacao (+{int((rows[0].get("tp_pct") or 0.75)*100)}%) enquanto seguravamos.<br>
  <b>[SPIKE?]</b> = max_gain alto mas &lt;5 ticks pre-exit — provavel spike intra-candle, nao price action real.
</p>
<table style='max-width:600px'>
  <thead><tr><th>Categoria</th><th>n ({total} trades)</th><th>Descricao</th></tr></thead>
  <tbody>{eq_summary_html}</tbody>
</table>
<br>
<div style='display:flex;gap:12px;flex-wrap:wrap;margin-bottom:10px'>
  <div class='box'>
    <div class='lbl'>Trailing falhou (total)</div>
    <div class='val' style='color:#f85149'>{n_trailing_f}</div>
    <div style='color:#484f58;font-size:10px'>{n_trailing_real} reais / {n_trailing_spike} spike suspect</div>
  </div>
  <div class='box'>
    <div class='lbl'>Trailing REAL (≥5 ticks)</div>
    <div class='val' style='color:#f85149'>{n_trailing_real}</div>
    <div style='color:#484f58;font-size:10px'>GMGN genuinamente falhou — investigar</div>
  </div>
  <div class='box'>
    <div class='lbl'>SL prematuro</div>
    <div class='val' style='color:#e3b341'>{n_sl_prem}</div>
    <div style='color:#484f58;font-size:10px'>Token recuperou ao TP apos nosso SL</div>
  </div>
  <div class='box'>
    <div class='lbl'>SL + recuperou 20%+</div>
    <div class='val' style='color:#d29922'>{n_sl_rec}</div>
    <div style='color:#484f58;font-size:10px'>Nao chegou ao TP mas subiu</div>
  </div>
  <div class='box'>
    <div class='lbl'>SL correto</div>
    <div class='val' style='color:#3fb950'>{n_sl_correct}</div>
    <div style='color:#484f58;font-size:10px'>Token nao recuperou — saida certa</div>
  </div>
</div>
<p style='color:#8b949e;font-size:11px'>SLs evitaveis (trailing+prematuro+recuperou): <b>{n_evitable}</b> de <b>{n_sl}</b> SLs totais ({int(n_evitable/n_sl*100) if n_sl else 0}%)</p>
<table>
  <thead><tr>
    <th>Token</th><th>Categoria</th>
    <th>hit_tp_before</th><th>hit_tp_after</th>
    <th>Max pré-exit</th><th>Max pós-exit</th>
    <th>Min pré-exit</th><th>Min pós-exit</th>
    <th>Duração</th><th>Ticks pre/pos</th>
  </tr></thead>
  <tbody>{eq_detail_html}</tbody>
</table>

<br>
<!-- SEÇÃO 3: drop_from_mig vs WR -->
<h2>▸ 3. PROFUNDIDADE DO DUMP vs WR — Qual zona de dump gera melhor resultado?</h2>
<p style='color:#484f58;font-size:11px'>
  H histórica: dump mais profundo → recovery melhor (dataset de 400+ tokens).<br>
  Esta seção valida (ou nega) nos ~{total} trades reais executados. delta = vs WR base de {wr_base*100:.0f}%.
</p>
<table style='max-width:750px'>
  <thead><tr>
    <th>Dump da migração</th><th>n</th><th>Win Rate (delta base)</th><th>TP</th><th>SL</th>
    <th>Max gain avg</th><th>Min loss avg</th>
  </tr></thead>
  <tbody>{drop_html}</tbody>
</table>

<br>
<!-- SEÇÃO 4: entry_ratio vs WR -->
<h2>▸ 4. ENTRY RATIO vs WR — Onde entramos no range migração→fundo?</h2>
<p style='color:#484f58;font-size:11px'>
  entry_ratio = mc_at_signal / mc_migration. Ex: 35% = entramos quando o token estava 35% do MC de migração.<br>
  H15 histórica: zona 50-65% = WR 0%. Validação nos trades live abaixo.
</p>
<table style='max-width:750px'>
  <thead><tr>
    <th>Entry ratio</th><th>n</th><th>Win Rate (delta base)</th><th>TP</th><th>SL</th>
    <th>Max gain avg</th><th>Min loss avg</th>
  </tr></thead>
  <tbody>{er_html}</tbody>
</table>

<br>
<!-- SEÇÃO 6: TPs -->
<h2>▸ 5. TPs — Perfil dos winners (n={len(tp_rows)})</h2>
<p style='color:#484f58;font-size:11px'>
  Mediana tempo até fechar: <b style='color:#3fb950'>{f"{tp_median:.1f}min" if tp_median else "—"}</b> |
  Média: <b>{f"{tp_avg:.1f}min" if tp_avg else "—"}</b>
</p>
<table>
  <thead><tr>
    <th>Token</th><th>Duração</th><th>Max gain</th><th>P&L real</th>
    <th>Entry ratio</th><th>Drop%</th><th>Bounce%</th><th>Smart</th><th>Ticks</th>
  </tr></thead>
  <tbody>{tp_detail_html}</tbody>
</table>

<br>
<!-- SEÇÃO 7: Smart wallets -->
<h2>▸ 6. SMART WALLETS vs WR — Confirmação nos trades live</h2>
<table style='max-width:600px'>
  <thead><tr><th>Smart count</th><th>n</th><th>Win Rate (delta base)</th><th>TP</th><th>SL</th></tr></thead>
  <tbody>{smart_html}</tbody>
</table>

<br>
<!-- SEÇÃO 8: bounce_pct -->
<h2>▸ 7. BOUNCE_PCT vs WR — Dentro dos trades ≥30%</h2>
<p style='color:#484f58;font-size:11px'>
  Bounce muito alto = entrada tardia? Bounce 30-40% pode ser zona de melhor R:R.
</p>
<table style='max-width:750px'>
  <thead><tr>
    <th>Bounce no sinal</th><th>n</th><th>Win Rate (delta base)</th><th>TP</th><th>SL</th>
    <th>Max gain avg</th><th>Min loss avg</th>
  </tr></thead>
  <tbody>{bp_html}</tbody>
</table>

<br>
<!-- SEÇÃO 9: Tabela completa -->
<h2>▸ 8. TABELA COMPLETA — Todos os {total} trades</h2>
<p style='color:#484f58;font-size:11px'>
  Ícone = causa-raiz do SL. ⬇ dead_from_start · ⚡ fast_dump · 〜 bounce_then_fail · 🎯 near_tp · 📉 slow_bleed
</p>
<table>
  <thead><tr>
    <th>#</th><th>Token</th><th>Out.</th>
    <th>Drop%</th><th>Bounce%</th><th>Elapsed</th><th>Entry ratio</th>
    <th>Smart</th><th>+2min</th><th>Max gain</th><th>Duração</th><th>Ticks</th><th>P&L</th>
  </tr></thead>
  <tbody>{all_detail_html}</tbody>
</table>

<br>
<p style='color:#484f58;font-size:11px;margin-top:14px'>
  Gate para cada insight virar filtro: delta WR ≥ +10pp · n_bucket ≥ 10 · mecanismo explicável.<br>
  entry_mc = calculado de entry_price_usd/tokens_received quando fill disponível; fallback entry_expected.<br>
  Max gain / Early gain = baseados em mc_high dos token_ticks. Trades sem ticks = análise de trajetória inconclusiva.
</p>
</body></html>"""
        return html

    except Exception:
        return "<pre>" + traceback.format_exc() + "</pre>", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)
