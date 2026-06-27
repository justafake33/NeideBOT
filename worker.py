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
    """Obtém IP público de saída do processo atual.
    Usa requests (mesma lib do GMGN swap) para refletir o IP real que o GMGN vê.
    """
    try:
        import requests as _req
        r = _req.get("https://ifconfig.me/ip", timeout=5, headers={"User-Agent": "curl/7.64"})
        return r.text.strip()
    except Exception:
        pass
    try:
        import requests as _req
        r = _req.get("https://api.ipify.org", timeout=5)
        return r.text.strip()
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
        # Collect WS health first (used both for logging and DB persistence)
        ws_health_snapshot = None
        try:
            sc = getattr(_app_module, "scanner", None)
            hw = getattr(getattr(sc, "hot", None), "_helius", None)
            if hw:
                ws_health_snapshot = hw.get_health()
                h = ws_health_snapshot
                logger.info(
                    "WS health: mode=%s global=%s connected=%s active=%d "
                    "sub_ok=%d sub_pend=%d pool_conf=%d ws_msgs=%d tx=%d qsize=%d",
                    h.get("mode"), h.get("erpc_global"), h.get("connected"),
                    h.get("n_active", 0), h.get("n_sub_confirmed", 0),
                    h.get("n_sub_pending", 0), h.get("n_pool_confirmed", 0),
                    h.get("ws_msg_count", 0), h.get("tx_count", 0),
                    h.get("queue_size", 0),
                )
            else:
                logger.info("WS health: HeliusWS=None (scanner=%s)", bool(sc))
        except Exception as e:
            logger.warning("WS health log failed: %s", e)

        try:
            ip = _get_outbound_ip()
            scanner_stats = None
            try:
                sc = getattr(_app_module, "scanner", None)
                if sc and hasattr(sc, "stats"):
                    scanner_stats = dict(sc.stats)
            except Exception:
                pass
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
                    "scanner_stats": scanner_stats,
                    "filters": {
                        "rug_max":     float(os.getenv("FILTER_RUG_MAX",     "0.15")),
                        "bundler_max": float(os.getenv("FILTER_BUNDLER_MAX", "0.20")),
                        "bot_max":     float(os.getenv("FILTER_BOT_MAX",     "0.60")),
                        "sniper_max":  float(os.getenv("FILTER_SNIPER_MAX",  "0.10")),
                        "rat_max":     float(os.getenv("FILTER_RAT_MAX",     "0.20")),
                        "top10_max":   float(os.getenv("FILTER_TOP10_MAX",   "0.70")),
                        "smart_min":   int(os.getenv("FILTER_SMART_MIN",     "1")),
                        "mc_min":      float(os.getenv("FILTER_MC_MIN",      "5000")),
                        "mc_max":      float(os.getenv("FILTER_MC_MAX",      "85000")),
                        "liq_min":     float(os.getenv("FILTER_LIQ_MIN",     "10000")),
                    },
                },
                ws_health=ws_health_snapshot,
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
