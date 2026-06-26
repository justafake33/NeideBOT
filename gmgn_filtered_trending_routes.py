"""Endpoint debug do GMGN Filtered Trending (research-only).

/debug/gmgn-filtered-trending  → HTML (default) ou ?format=json

Responde à hipótese:
  scanner universe ∩ GMGN filtered trending → o grupo trending faz mais bounce?
"""

from __future__ import annotations

import os

from flask import Blueprint, Response, jsonify, request

import gmgn_filtered_trending_store as store

gmgn_filtered_trending_bp = Blueprint("gmgn_filtered_trending", __name__)


def _band_from_env() -> dict:
    return {
        "mc_min": float(os.getenv("GMGN_FILTERED_TRENDING_MC_MIN", "25000")),
        "mc_max": float(os.getenv("GMGN_FILTERED_TRENDING_MC_MAX", "35000")),
        "liq_min": float(os.getenv("GMGN_FILTERED_TRENDING_LIQ_MIN", "5000")),
        "fee_min": float(os.getenv("GMGN_FILTERED_TRENDING_FEE_MIN", "1.0")),
    }


def _pct(v) -> str:
    if v is None:
        return "—"
    return f"{v * 100:+.1f}%"


def _rate(v) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.1f}%"


def _num(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


@gmgn_filtered_trending_bp.route("/debug/gmgn-filtered-trending")
def debug_gmgn_filtered_trending():
    fmt = request.args.get("format", "html")
    since = request.args.get("since")
    days = int(request.args.get("days", "14"))
    refresh = request.args.get("refresh", "1") != "0"

    try:
        store.ensure_schema()
        if refresh:
            try:
                store.refresh_outcomes(max_labels=int(request.args.get("batch", "1000")))
            except Exception:
                pass
        rep = store.build_report(since=since, days=days, band=_band_from_env())
    except Exception as e:
        payload = {"ok": False, "error": str(e)}
        if fmt == "json":
            return jsonify(payload), 500
        return Response(f"<pre>FilteredTrending erro: {e}</pre>", status=500, mimetype="text/html")

    if fmt == "json":
        return jsonify(rep)

    ctrl = rep.get("control", {})
    a = ctrl.get("group_a_stats", {})
    b = ctrl.get("group_b_stats", {})
    rec = rep.get("recommendation", {})
    labels = rep.get("labels", {})
    band = rep.get("band", {})

    h = ["<html><body style='font-family:monospace;background:#0d1117;color:#c9d1d9;padding:16px'>"]
    h.append("<h2 style='color:#58a6ff'>GMGN Filtered Trending — hipótese de demanda (research-only)</h2>")
    win = f"since={since}" if since else f"últimos {days}d"
    h.append(f"<p>Janela: {win} | Faixa: MC ${band.get('mc_min'):,.0f}–${band.get('mc_max'):,.0f} · "
             f"liq&gt;${band.get('liq_min'):,.0f} · fee&gt;{band.get('fee_min')} SOL</p>")
    h.append(f"<p><a style='color:#58a6ff' href='/debug/gmgn-filtered-trending?format=json"
             f"{('&since=' + since) if since else ('&days=' + str(days))}'>JSON</a></p>")

    h.append("<h3>Cobertura de labels</h3>")
    h.append(f"<p>Total labels: {labels.get('total') or 0} · mints distintos: {labels.get('distinct_mints') or 0} · "
             f"dentro do scanner: {labels.get('inside_scanner') or 0} · fora (diagnóstico): {labels.get('outside_scanner') or 0}</p>")
    h.append("<table border='1' cellpadding='4' style='border-collapse:collapse;color:#c9d1d9'>")
    h.append("<tr><th>profile</th><th>timeframe</th><th>labels</th><th>mints</th><th>fora scanner</th></tr>")
    for r in rep.get("by_profile", []):
        h.append(f"<tr><td>{r.get('profile_name')}</td><td>{r.get('timeframe')}</td>"
                 f"<td>{r.get('n')}</td><td>{r.get('mints')}</td><td>{r.get('outside')}</td></tr>")
    h.append("</table>")

    h.append("<h3>Grupo A (trending) vs Grupo B (controle) — scanner na faixa operacional</h3>")
    h.append(f"<p>Scanner na faixa: {ctrl.get('scanner_in_band') or 0} · "
             f"Grupo A (apareceu em trending): {ctrl.get('group_a_labeled') or 0} · "
             f"Grupo B (não apareceu): {ctrl.get('group_b_unlabeled') or 0}</p>")
    h.append("<table border='1' cellpadding='4' style='border-collapse:collapse;color:#c9d1d9'>")
    h.append("<tr><th>métrica</th><th style='color:#3fb950'>Grupo A (trending)</th><th>Grupo B (controle)</th></tr>")
    rows = [
        ("n (com ticks)", a.get("n"), b.get("n")),
        ("bounce rate (bounce_signals)", _rate(ctrl.get("group_a_bounce_rate")), _rate(ctrl.get("group_b_bounce_rate"))),
        ("% +20% em 15m", _rate(a.get("pct_up20_in_15m")), _rate(b.get("pct_up20_in_15m"))),
        ("% +30% em 30m", _rate(a.get("pct_up30_in_30m")), _rate(b.get("pct_up30_in_30m"))),
        ("% +50% em 60m", _rate(a.get("pct_up50_in_60m")), _rate(b.get("pct_up50_in_60m"))),
        ("runup mediano 60m", _pct(a.get("median_runup_60m")), _pct(b.get("median_runup_60m"))),
        ("drawdown mediano 60m", _pct(a.get("median_drawdown_60m")), _pct(b.get("median_drawdown_60m"))),
        ("SL antes de TP", _rate(a.get("sl_before_tp_rate")), _rate(b.get("sl_before_tp_rate"))),
        ("tempo mediano até topo (min)", _num(a.get("median_time_to_top_min")), _num(b.get("median_time_to_top_min"))),
    ]
    for label, va, vb in rows:
        h.append(f"<tr><td>{label}</td><td>{va if va is not None else '—'}</td><td>{vb if vb is not None else '—'}</td></tr>")
    h.append("</table>")

    h.append("<h3>Timeframe 5m vs 1h</h3>")
    h.append("<table border='1' cellpadding='4' style='border-collapse:collapse;color:#c9d1d9'>")
    h.append("<tr><th>tf</th><th>computed</th><th>no_ticks</th><th>avg runup 60m</th><th>avg dd 60m</th><th>hit+50% rate</th><th>bounce rate</th></tr>")
    for r in rep.get("by_timeframe", []):
        h.append(f"<tr><td>{r.get('timeframe')}</td><td>{r.get('n_computed')}</td><td>{r.get('n_no_ticks')}</td>"
                 f"<td>{_pct(r.get('avg_runup_60m'))}</td><td>{_pct(r.get('avg_drawdown_60m'))}</td>"
                 f"<td>{_rate(r.get('hit50_rate'))}</td><td>{_rate(r.get('bounce_rate'))}</td></tr>")
    h.append("</table>")

    h.append("<h3>Rank dentro do filtro importa?</h3>")
    h.append("<table border='1' cellpadding='4' style='border-collapse:collapse;color:#c9d1d9'>")
    h.append("<tr><th>rank</th><th>n</th><th>avg runup 60m</th><th>hit+50% rate</th></tr>")
    for r in rep.get("by_rank", []):
        h.append(f"<tr><td>{r.get('rank_lo')}–{r.get('rank_hi')}</td><td>{r.get('n')}</td>"
                 f"<td>{_pct(r.get('avg_runup_60m'))}</td><td>{_rate(r.get('hit50_rate'))}</td></tr>")
    h.append("</table>")

    h.append("<h3>Trending antes ou depois do bounce?</h3><ul>")
    for r in rep.get("trending_vs_bounce", []):
        h.append(f"<li>{r.get('trending_vs_bounce')}: n={r.get('n')} · avg runup 60m={_pct(r.get('avg_runup_60m'))}</li>")
    h.append("</ul>")

    def _examples(title, key):
        h.append(f"<h3>{title}</h3><table border='1' cellpadding='4' style='border-collapse:collapse;color:#c9d1d9'>")
        h.append("<tr><th>symbol</th><th>mint</th><th>profile</th><th>rank</th><th>runup 60m</th><th>dd 60m</th><th>vs bounce</th></tr>")
        for r in rep.get(key, []):
            h.append(f"<tr><td>{r.get('symbol') or '—'}</td><td>{(r.get('mint') or '')[:10]}…</td>"
                     f"<td>{r.get('profile_name')}</td><td>{r.get('rank') if r.get('rank') is not None else '—'}</td>"
                     f"<td>{_pct(r.get('max_runup_60m'))}</td><td>{_pct(r.get('max_drawdown_60m'))}</td>"
                     f"<td>{r.get('trending_vs_bounce') or '—'}</td></tr>")
        h.append("</table>")

    _examples("Melhores casos", "best_cases")
    _examples("Piores casos", "worst_cases")
    _examples("Trending veio ANTES do bounce", "trending_before_bounce")
    _examples("Trending veio DEPOIS do bounce (tarde demais)", "trending_after_bounce")

    edge = rec.get("edge_pp")
    h.append("<h3 style='color:#d29922'>Recomendação (Fase 7 — research-only, sem alterar estratégia)</h3>")
    h.append(f"<p><b>Veredito:</b> {rec.get('verdict')}</p>")
    h.append(f"<p><b>Edge (bounce A − B):</b> {('%.1fpp' % edge) if edge is not None else '—'}</p>")
    h.append(f"<p><b>Sugestão:</b> {rec.get('suggestion')}</p>")
    h.append(f"<p style='color:#8b949e'>{rec.get('note')}</p>")
    h.append("</body></html>")
    return "".join(h)
