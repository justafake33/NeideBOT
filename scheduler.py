import heapq
import threading
import time
import logging

import sentry_sdk

logger = logging.getLogger(__name__)


class HeapScheduler:
    """Single-thread heap scheduler — evita explosão de threads do threading.Timer."""

    def __init__(self):
        self._heap: list = []
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._seq = 0
        self._thread = threading.Thread(target=self._run, daemon=True, name="scheduler")
        self._running = False

    def start(self):
        self._running = True
        self._thread.start()
        logger.info("HeapScheduler iniciado")

    def stop(self):
        self._running = False
        self._event.set()

    def schedule(self, delay_seconds: float, callback, *args):
        fire_at = time.monotonic() + delay_seconds
        with self._lock:
            self._seq += 1
            heapq.heappush(self._heap, (fire_at, self._seq, callback, args))
        self._event.set()

    def _safe_callback(self, callback, args):
        try:
            callback(*args)
        except Exception as e:
            logger.exception("Erro no callback agendado")
            sentry_sdk.capture_exception(e)

    def _run(self):
        while self._running:
            try:
                self._event.clear()
                fired = []

                with self._lock:
                    now = time.monotonic()
                    while self._heap and self._heap[0][0] <= now:
                        _, _, callback, args = heapq.heappop(self._heap)
                        fired.append((callback, args))

                    next_fire = self._heap[0][0] if self._heap else None

                for callback, args in fired:
                    threading.Thread(
                        target=self._safe_callback,
                        args=(callback, args),
                        daemon=True
                    ).start()

                if next_fire is not None:
                    wait = max(0.0, next_fire - time.monotonic())
                    self._event.wait(timeout=wait)
                else:
                    self._event.wait(timeout=60.0)

            except Exception as e:
                logger.exception("Erro no loop principal do scheduler")
                sentry_sdk.capture_exception(e)
