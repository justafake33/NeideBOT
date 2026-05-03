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


@app.route("/analise")
def analise():
    rows = db.get_all_migrations(limit=10000)
    d = _run_analysis(rows)
    return render_template_string(ANALISE_HTML, d=d)


# ==============================================================================
# Startup — funciona com Gunicorn e execução direta
# ==============================================================================

gmgn    = None  # exposto globalmente para rotas de debug
scanner = None  # exposto globalmente para rotas de debug

def _start_services():
    global gmgn, scanner
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
