#!/usr/bin/env python3
"""NeideBOT background worker.

Runs scanner/HotMonitor/Helius/eRPC services outside the Flask web process.
Keep this as a separate Railway service/process so websocket stalls do not freeze
HTTP analytics endpoints.
"""

import logging
import os
import signal
import socket
import threading
import time

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Must be set before importing app: app.py conditionally starts services at import.
os.environ.setdefault("RUN_BOT_SERVICES", "1")

import app as _app_module  # noqa: E402,F401  # importing starts services when RUN_BOT_SERVICES=1

_running = True
RUNTIME_HEARTBEAT_ENABLED = os.getenv("RUNTIME_HEARTBEAT_ENABLED", "true").lower() == "true"
RUNTIME_HEARTBEAT_INTERVAL_SEC = int(os.getenv("RUNTIME_HEARTBEAT_INTERVAL_SEC", "300"))


def _fetch_outbound_ip() -> tuple[str | None, str, str | None]:
    """Consulta IP público de saída do container worker."""
    source = os.getenv("OUTBOUND_IP_URL", "https://ifconfig.me/ip")
    try:
        ip = requests.get(source, timeout=5).text.strip()
        return ip or None, source, None
    except Exception as e:
        return None, source, str(e)


def _runtime_heartbeat_loop():
    """Persiste IP real do worker para o web expor em /debug/runtime-ips."""
    while _running:
        ip, source, error = _fetch_outbound_ip()
        _app_module.db.upsert_runtime_heartbeat({
            "role": os.getenv("RUNTIME_ROLE", "worker"),
            "service_name": os.getenv("RAILWAY_SERVICE_NAME") or os.getenv("RAILWAY_SERVICE_ID"),
            "deployment_id": os.getenv("RAILWAY_DEPLOYMENT_ID"),
            "replica_id": os.getenv("RAILWAY_REPLICA_ID"),
            "hostname": socket.gethostname(),
            "outbound_ip": ip,
            "ip_source": source,
            "error": error,
        })
        if error:
            logger.warning("Worker outbound IP heartbeat failed: %s", error)
        else:
            logger.info("Worker outbound IP heartbeat: %s", ip)
        for _ in range(max(1, RUNTIME_HEARTBEAT_INTERVAL_SEC)):
            if not _running:
                break
            time.sleep(1)


if RUNTIME_HEARTBEAT_ENABLED:
    threading.Thread(
        target=_runtime_heartbeat_loop,
        daemon=True,
        name="runtime-heartbeat",
    ).start()
else:
    logger.info("Runtime heartbeat disabled (RUNTIME_HEARTBEAT_ENABLED=false)")


def _stop(signum, frame):  # noqa: ARG001
    global _running
    logger.info("Worker received signal %s; shutting down", signum)
    _running = False


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)

logger.info("NeideBOT worker running (RUN_BOT_SERVICES=%s)", os.getenv("RUN_BOT_SERVICES"))

while _running:
    time.sleep(30)

logger.info("NeideBOT worker stopped")
