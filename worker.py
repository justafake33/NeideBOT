#!/usr/bin/env python3
"""NeideBOT background worker.

Runs scanner/HotMonitor/Helius/eRPC services outside the Flask web process.
Keep this as a separate Railway service/process so websocket stalls do not freeze
HTTP analytics endpoints.

Railway split:
  web    → gunicorn app:app (dashboard only, RUN_BOT_SERVICES=0)
  worker → python worker.py (scanner + trading, RUN_BOT_SERVICES=1)

The IP that matters for GMGN whitelist is the WORKER IP, not the web IP.
Check /debug/runtime-ips on the web dashboard to see both.
"""

import logging
import os
import signal
import socket
import threading
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Must be set before importing app: app.py conditionally starts services at import.
os.environ.setdefault("RUN_BOT_SERVICES", "1")

import app as _app_module  # noqa: E402,F401  # importing starts services when RUN_BOT_SERVICES=1
import db

_running = True
_HEARTBEAT_INTERVAL = int(os.getenv("RUNTIME_HEARTBEAT_INTERVAL_SEC", "60"))


def _stop(signum, frame):  # noqa: ARG001
    global _running
    logger.info("Worker received signal %s; shutting down", signum)
    _running = False


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


def _get_outbound_ip() -> str:
    """Obtém IP público de saída do processo atual."""
    try:
        import urllib.request
        with urllib.request.urlopen("https://ifconfig.me/ip", timeout=5) as resp:
            return resp.read().decode().strip()
    except Exception:
        pass
    try:
        import urllib.request
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as resp:
            return resp.read().decode().strip()
    except Exception as e:
        return f"erro:{e}"


def _heartbeat_loop():
    """Atualiza heartbeat do worker no DB a cada RUNTIME_HEARTBEAT_INTERVAL segundos."""
    hostname = socket.gethostname()
    pid = os.getpid()
    railway_env = os.getenv("RAILWAY_ENVIRONMENT", "")
    git_sha = os.getenv("RAILWAY_GIT_COMMIT_SHA", "")[:8]

    while _running:
        try:
            ip = _get_outbound_ip()
            db.upsert_runtime_heartbeat(
                runtime_name="worker",
                service_type="worker",
                outbound_ip=ip,
                hostname=hostname,
                pid=pid,
                metadata={
                    "railway_env": railway_env,
                    "git_sha": git_sha,
                    "run_bot_services": os.getenv("RUN_BOT_SERVICES", "1"),
                },
            )
            logger.info("Worker heartbeat: ip=%s hostname=%s pid=%d", ip, hostname, pid)
        except Exception as e:
            logger.warning("Heartbeat failed: %s", e)
        time.sleep(_HEARTBEAT_INTERVAL)


logger.info("NeideBOT worker running (RUN_BOT_SERVICES=%s)", os.getenv("RUN_BOT_SERVICES"))

# Start heartbeat in background thread
_hb = threading.Thread(target=_heartbeat_loop, daemon=True, name="worker-heartbeat")
_hb.start()

while _running:
    time.sleep(30)

logger.info("NeideBOT worker stopped")
