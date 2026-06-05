"""Runtime split support: debug routes, worker heartbeat, schema healthcheck.

Kept outside app.py/db.py to minimize merge conflicts with the main branch.
"""

from __future__ import annotations

import os
import socket
from datetime import timezone

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import jsonify, request

DATABASE_URL = os.getenv("DATABASE_URL")


TRADE_AUDIT_ALTERS = [
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
]


def ensure_trade_audit_columns() -> None:
    if not DATABASE_URL:
        return
    conn = _connect()
    try:
        with conn.cursor() as cur:
            for alter in TRADE_AUDIT_ALTERS:
                cur.execute(f"ALTER TABLE trades {alter}")
        conn.commit()
    finally:
        conn.close()


def count_executable_trades() -> int:
    if not DATABASE_URL:
        return 0
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT COUNT(*) AS n
                  FROM trades
                 WHERE status != 'error'
                   AND (order_id IS NOT NULL OR status IN ('open','tp','sl','trailing_active','expired'))
            """)
            row = cur.fetchone()
            return int(row["n"] or 0)
    finally:
        conn.close()

RUNTIME_HEARTBEAT_SQL = """
CREATE TABLE IF NOT EXISTS runtime_heartbeats (
    role          TEXT PRIMARY KEY,
    service_name  TEXT,
    deployment_id TEXT,
    replica_id    TEXT,
    hostname      TEXT,
    outbound_ip   TEXT,
    ip_source     TEXT,
    error         TEXT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

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
        "id", "mint", "ts_signal", "mc_at_signal", "bounce_pct",
        "pool_quote_at_signal", "absorption_events", "net_sol_flow",
        "rise_events", "rise_net_flow", "dump_seller_hhi", "dump_top_seller_frac",
        "buy_concentration", "h23_window", "data_quality",
    },
    "micro_absorption_snapshots": {
        "id", "mint", "ts_snapshot", "bottom_age_sec", "mc_current", "pool_quote",
        "live_5s_net", "live_5s_events", "live_10s_net", "live_10s_events",
        "live_30s_net", "live_30s_events", "live_60s_net", "live_60s_events",
        "pre_5s_buy_concentration", "pre_10s_buy_concentration",
        "rise_5s_buy_concentration", "rise_10s_buy_concentration",
        "live_5s_buy_concentration", "live_10s_buy_concentration", "candidate_flags",
    },
    "token_ticks": {"mint", "tick_ts", "mc", "mc_open", "mc_high", "mc_low", "source"},
    "runtime_heartbeats": {
        "role", "service_name", "deployment_id", "replica_id", "hostname",
        "outbound_ip", "ip_source", "error", "updated_at",
    },
}


def _connect():
    return psycopg2.connect(DATABASE_URL)


def ensure_runtime_heartbeat_table() -> None:
    if not DATABASE_URL:
        return
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(RUNTIME_HEARTBEAT_SQL)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_runtime_hb_updated ON runtime_heartbeats (updated_at DESC)")
        conn.commit()
    finally:
        conn.close()


def upsert_runtime_heartbeat(data: dict) -> None:
    if not DATABASE_URL:
        return
    ensure_runtime_heartbeat_table()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO runtime_heartbeats (
                    role, service_name, deployment_id, replica_id, hostname,
                    outbound_ip, ip_source, error, updated_at
                ) VALUES (
                    %(role)s, %(service_name)s, %(deployment_id)s, %(replica_id)s, %(hostname)s,
                    %(outbound_ip)s, %(ip_source)s, %(error)s, NOW()
                )
                ON CONFLICT (role) DO UPDATE SET
                    service_name  = EXCLUDED.service_name,
                    deployment_id = EXCLUDED.deployment_id,
                    replica_id    = EXCLUDED.replica_id,
                    hostname      = EXCLUDED.hostname,
                    outbound_ip   = EXCLUDED.outbound_ip,
                    ip_source     = EXCLUDED.ip_source,
                    error         = EXCLUDED.error,
                    updated_at    = NOW()
            """, data)
        conn.commit()
    finally:
        conn.close()


def get_runtime_heartbeats() -> list[dict]:
    if not DATABASE_URL:
        return []
    try:
        conn = _connect()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT role, service_name, deployment_id, replica_id, hostname,
                           outbound_ip, ip_source, error, updated_at, created_at
                      FROM runtime_heartbeats
                     ORDER BY role
                """)
                return list(cur.fetchall())
        finally:
            conn.close()
    except Exception:
        return []


def check_runtime_schema() -> dict:
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

    conn = _connect()
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
            missing = sorted(required_cols)
            result["missing_tables"].append(table)
            result["tables"][table] = {"exists": False, "missing_columns": missing}
            result["missing_columns"][table] = missing
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


def _current_outbound_ip() -> tuple[str | None, str, str | None]:
    import requests as _req
    source = os.getenv("OUTBOUND_IP_URL", "https://ifconfig.me/ip")
    try:
        ip = _req.get(source, timeout=5).text.strip()
        return ip or None, source, None
    except Exception as e:
        return None, source, str(e)


def install_runtime_routes(app) -> None:
    @app.route("/debug/runtime-ips")
    def debug_runtime_ips():
        web_ip, source, web_error = _current_outbound_ip()
        rows = get_runtime_heartbeats()
        payload = {
            "web": {
                "role": "web",
                "outbound_ip": web_ip,
                "ip_source": source,
                "error": web_error,
                "service_name": os.getenv("RAILWAY_SERVICE_NAME") or os.getenv("RAILWAY_SERVICE_ID"),
                "deployment_id": os.getenv("RAILWAY_DEPLOYMENT_ID"),
                "replica_id": os.getenv("RAILWAY_REPLICA_ID"),
                "hostname": socket.gethostname(),
            },
            "heartbeats": rows,
            "note": "Use worker.outbound_ip para whitelist GMGN; /debug/my-ip mostra apenas o web.",
        }
        if request.args.get("format") == "json":
            return jsonify(payload)

        def _fmt_dt(v):
            if not v:
                return "—"
            try:
                return v.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            except Exception:
                return str(v)

        h = """<!doctype html><html><head><meta charset='utf-8'><title>Runtime IPs</title>
<style>body{font-family:monospace;background:#0d1117;color:#c9d1d9;padding:16px}table{border-collapse:collapse}td,th{border:1px solid #30363d;padding:7px 10px}.ok{color:#3fb950}.bad{color:#f85149}.warn{color:#d29922}</style></head><body>"""
        h += "<h1>Runtime Outbound IPs</h1>"
        h += "<p class='warn'><b>GMGN whitelist:</b> use o IP do <b>worker</b>, não apenas o web.</p>"
        h += "<p><a style='color:#58a6ff' href='/debug/runtime-ips?format=json'>JSON</a> · <a style='color:#58a6ff' href='/debug/runtime-ips'>Refresh</a></p>"
        h += "<h2>Web atual</h2><table><tr><th>role</th><th>outbound_ip</th><th>source</th><th>error</th><th>service</th><th>deployment</th><th>replica</th><th>hostname</th></tr>"
        web_cls = "ok" if web_ip else "bad"
        h += (
            f"<tr><td>web</td><td class='{web_cls}'>{web_ip or '—'}</td>"
            f"<td>{source}</td><td>{web_error or '—'}</td>"
            f"<td>{payload['web']['service_name'] or '—'}</td><td>{payload['web']['deployment_id'] or '—'}</td>"
            f"<td>{payload['web']['replica_id'] or '—'}</td><td>{payload['web']['hostname'] or '—'}</td></tr></table>"
        )
        h += "<h2>Heartbeats registrados</h2>"
        if not rows:
            h += "<p class='bad'>Nenhum heartbeat encontrado. Aguarde o worker ou rode a migration one-off.</p>"
        else:
            h += "<table><tr><th>role</th><th>outbound_ip</th><th>updated_at</th><th>source</th><th>error</th><th>service</th><th>deployment</th><th>replica</th><th>hostname</th></tr>"
            for r in rows:
                cls = "ok" if r.get("outbound_ip") and not r.get("error") else "bad"
                h += (
                    f"<tr><td>{r.get('role') or '—'}</td><td class='{cls}'>{r.get('outbound_ip') or '—'}</td>"
                    f"<td>{_fmt_dt(r.get('updated_at'))}</td><td>{r.get('ip_source') or '—'}</td>"
                    f"<td>{r.get('error') or '—'}</td><td>{r.get('service_name') or '—'}</td>"
                    f"<td>{r.get('deployment_id') or '—'}</td><td>{r.get('replica_id') or '—'}</td>"
                    f"<td>{r.get('hostname') or '—'}</td></tr>"
                )
            h += "</table>"
        h += "</body></html>"
        return h

    @app.route("/debug/schema")
    def debug_schema():
        schema = check_runtime_schema()
        status = 200 if schema.get("ok") else 503
        if request.args.get("format") == "json":
            return jsonify(schema), status
        rows = []
        for table, info in schema.get("tables", {}).items():
            missing = ", ".join(info.get("missing_columns", [])) or "—"
            coverage = f"{info.get('present_required_count', 0)}/{info.get('required_count', '?')}" if info.get("exists") else "0/?"
            rows.append(f"<tr><td>{table}</td><td>{'OK' if info.get('exists') and not info.get('missing_columns') else 'missing/error'}</td><td>{missing}</td><td>{coverage}</td></tr>")
        return """<!doctype html><html><head><meta charset='utf-8'><title>Runtime Schema</title>
<style>body{font-family:monospace;background:#0d1117;color:#c9d1d9;padding:16px}td,th{border:1px solid #30363d;padding:7px 10px}</style></head><body>""" + \
            f"<h1>Runtime Schema Healthcheck</h1><p>Status: {'OK' if schema.get('ok') else 'MISSING / ERROR'}</p>" + \
            "<table><tr><th>Tabela</th><th>Status</th><th>Colunas faltando</th><th>Coverage</th></tr>" + "".join(rows) + "</table></body></html>"
