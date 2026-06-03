#!/usr/bin/env python3
"""NeideBOT background worker.

Runs scanner/HotMonitor/Helius/eRPC services outside the Flask web process.
Keep this as a separate Railway service/process so websocket stalls do not freeze
HTTP analytics endpoints.
"""

import logging
import os
import signal
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Must be set before importing app: app.py conditionally starts services at import.
os.environ.setdefault("RUN_BOT_SERVICES", "1")

import app as _app_module  # noqa: E402,F401  # importing starts services when RUN_BOT_SERVICES=1

_running = True


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
