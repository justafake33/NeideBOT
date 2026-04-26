import os
import time
import logging
import requests
from typing import Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

GMGN_HOST    = os.getenv("GMGN_HOST", "https://openapi.gmgn.ai")
GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")


class RateLimitError(Exception):
    def __init__(self, reset_at: float):
        self.reset_at = reset_at
        super().__init__(f"Rate limited until {reset_at}")


class GmgnClient:
    def __init__(self):
        self.host = GMGN_HOST.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "X-APIKEY": GMGN_API_KEY,
            "Content-Type": "application/json",
        })

    def _params(self, extra: dict = None) -> dict:
        p = {
            "timestamp": str(int(time.time())),
            "client_id": str(uuid4()),
        }
        if extra:
            p.update(extra)
        return p

    def _handle_response(self, resp: requests.Response) -> dict:
        if resp.status_code == 429:
            try:
                body = resp.json()
                reset_at = float(body.get("reset_at", time.time() + 10))
            except Exception:
                reset_at = time.time() + 10
            raise RateLimitError(reset_at)
        resp.raise_for_status()
        body = resp.json()
        if body.get("code", 0) != 0:
            raise Exception(f"GMGN error {body.get('code')}: {body.get('message')}")
        return body

    def _get(self, path: str, params: dict = None) -> dict:
        resp = self.session.get(
            f"{self.host}{path}",
            params=self._params(params),
            timeout=15,
        )
        return self._handle_response(resp)

    def _post(self, path: str, body: dict = None) -> dict:
        resp = self.session.post(
            f"{self.host}{path}",
            json=body or {},
            params=self._params(),
            timeout=15,
        )
        return self._handle_response(resp)

    def get_trenches(self, types: list = None, chain: str = "sol") -> dict:
        """
        Chama /v1/trenches uma vez por tipo (GMGN não aceita array).
        near_completion -> data['pump']
        completed       -> data['completed']
        """
        combined = {}
        for t in (types or ["near_completion", "completed"]):
            try:
                result = self._post("/v1/trenches", {"chain": chain, "type": t})
                combined.update(result.get("data", {}))
                time.sleep(0.5)
            except RateLimitError:
                raise
            except Exception as e:
                logger.warning("trenches type=%s erro: %s", t, e)
        return combined

    def get_token_info(self, mint: str, chain: str = "sol") -> Optional[dict]:
        for attempt in range(2):
            try:
                result = self._get(f"/v1/token/info/{chain}/{mint}")
                return result.get("data")
            except RateLimitError as e:
                if attempt == 0:
                    wait = max(1.0, e.reset_at - time.time()) + 1
                    logger.warning("Rate limit token info, aguardando %.1fs", wait)
                    time.sleep(wait)
                else:
                    raise
            except Exception as e:
                logger.warning("token info %s: %s", mint[:8], e)
                return None
        return None

    def get_token_security(self, mint: str, chain: str = "sol") -> Optional[dict]:
        for attempt in range(2):
            try:
                result = self._get(f"/v1/token/security/{chain}/{mint}")
                return result.get("data")
            except RateLimitError as e:
                if attempt == 0:
                    wait = max(1.0, e.reset_at - time.time()) + 1
                    logger.warning("Rate limit token security, aguardando %.1fs", wait)
                    time.sleep(wait)
                else:
                    raise
            except Exception as e:
                logger.warning("token security %s: %s", mint[:8], e)
                return None
        return None
