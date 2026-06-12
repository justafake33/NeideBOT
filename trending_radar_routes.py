"""HTTP debug endpoint do GMGN Trending Radar."""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

import db
import trending_radar_store as store

trending_radar_bp = Blueprint("trending_radar", __name__)


def _fmt_mc(v):
    if v is None:
        return "—"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


def _fmt_ratio(v):
    if v is None:
        return "—"
    return f"{v:.0%}"


def get_trending_radar_health(hours: int = 24, limit: int = 20) -> dict:
    """Health/readiness local do Trending Radar para o endpoint debug."""
    store.ensure_trending_radar_schema()
    with db._cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) AS snapshots,
                   MAX(ts_snapshot) AS latest_snapshot,
                   COUNT(*) FILTER (WHERE mint IS NULL OR mint = '') AS null_mint,
                   COUNT(*) FILTER (WHERE rank_position IS NULL) AS null_rank,
                   COUNT(*) FILTER (WHERE market_cap IS NULL) AS null_market_cap,
                   COUNT(*) FILTER (WHERE liquidity IS NULL) AS null_liquidity,
                   COUNT(*) FILTER (WHERE volume IS NULL) AS null_volume,
                   COUNT(*) FILTER (WHERE raw_json IS NULL OR raw_json = '{}') AS null_raw_json
              FROM gmgn_trending_snapshots
             WHERE ts_snapshot >= NOW() - (%(hours)s || ' hours')::INTERVAL
        """, {"hours": hours})
        health = dict(cur.fetchone() or {})
        cur.execute("""
            SELECT ts_snapshot, chain, interval_label, rank_position, mint, symbol,
                   market_cap, liquidity, volume, smart_degen_count, rug_ratio
              FROM gmgn_trending_snapshots
             ORDER BY ts_snapshot DESC, rank_position ASC NULLS LAST
             LIMIT %(limit)s
        """, {"limit": limit})
        health["recent"] = [dict(r) for r in cur.fetchall()]

    snapshots = health.get("snapshots") or 0
    critical_nulls = sum(int(health.get(k) or 0) for k in ("null_mint", "null_rank", "null_market_cap"))
    health["capturing"] = snapshots > 0
    health["critical_nulls"] = critical_nulls
    health["ok"] = snapshots > 0 and critical_nulls == 0
    return health


@trending_radar_bp.route("/debug/trending-radar")
def debug_trending_radar():
    hours = int(request.args.get("hours", "24"))
    fmt = request.args.get("format", "html")
    try:
        store.ensure_trending_radar_schema()
        summary = store.get_trending_radar_summary(hours=hours)
        health = get_trending_radar_health(hours=hours)
        payload = {"hours": hours, "health": health, "summary": summary}
    except Exception as e:
        payload = {"hours": hours, "ok": False, "error": str(e)}
        if fmt == "json":
            return jsonify(payload), 500
        return Response(f"<pre>Trending Radar erro: {e}</pre>", status=500, mimetype="text/html")

    if fmt == "json":
        return jsonify(payload)

    h = "<html><body style='font-family:monospace;background:#0d1117;color:#c9d1d9;padding:16px'>"
    h += "<h2 style='color:#58a6ff'>GMGN Trending Radar</h2>"
    h += f"<p><a style='color:#58a6ff' href='/debug/trending-radar?hours={hours}&format=json'>JSON</a></p>"
    h += f"<p>Janela: {hours}h | Captando: {health.get('capturing')} | OK dados críticos: {health.get('ok')}</p>"
    h += f"<p>Snapshots: {summary.get('snapshots') or 0} | Latest: {health.get('latest_snapshot') or '—'}</p>"
    h += f"<p>Resolved: {summary.get('resolved') or 0} | Wins: {summary.get('wins') or 0} | WR shadow: {((summary.get('shadow_wr') or 0)*100):.1f}% | Ambiguous: {summary.get('ambiguous') or 0}</p>"
    h += f"<p>Nulls críticos: mint={health.get('null_mint') or 0}, rank={health.get('null_rank') or 0}, mc={health.get('null_market_cap') or 0}</p>"
    h += "<h3>Reject reasons</h3><ul>"
    for r in summary.get("reject_reasons", []):
        h += f"<li>{r.get('reject_reason')}: {r.get('n')}</li>"
    h += "</ul><h3>Recent snapshots</h3><table border='1' cellpadding='4' style='border-collapse:collapse;color:#c9d1d9'>"
    h += "<tr><th>ts</th><th>int</th><th>rank</th><th>symbol</th><th>mint</th><th>MC</th><th>Liq</th><th>Vol</th><th>Smart</th><th>Rug</th></tr>"
    for r in health.get("recent", []):
        h += "<tr>"
        h += f"<td>{r.get('ts_snapshot')}</td><td>{r.get('interval_label')}</td><td>{r.get('rank_position')}</td>"
        h += f"<td>{r.get('symbol') or '—'}</td><td>{(r.get('mint') or '')[:10]}…</td>"
        h += f"<td>{_fmt_mc(r.get('market_cap'))}</td><td>{_fmt_mc(r.get('liquidity'))}</td><td>{_fmt_mc(r.get('volume'))}</td>"
        h += f"<td>{r.get('smart_degen_count') if r.get('smart_degen_count') is not None else '—'}</td><td>{_fmt_ratio(r.get('rug_ratio'))}</td>"
        h += "</tr>"
    h += "</table></body></html>"
    return h
