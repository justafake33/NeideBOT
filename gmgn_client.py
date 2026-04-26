import os
import time
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

GMGN_HOST = os.getenv("GMGN_HOST", "https://openapi.gmgn.ai")
GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")
CLIENT_ID = "neidebot-v1"


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

    def _params(self) -> dict:
        return {
            "timestamp": int(time.time() * 1000),
            "client_id": CLIENT_ID,
        }

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
            raise Exception(f"GMGN API error {body.get('code')}: {body.get('message')}")
        return body

    def _get(self, path: str, params: dict = None) -> dict:
        p = self._params()
        if params:
            p.update(params)
        resp = self.session.get(f"{self.host}{path}", params=p, timeout=15)
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
        """POST /v1/trenches — near_completion retorna em data['pump'], completed em data['completed']."""
        body = {
            "chain": chain,
            "type": types or ["near_completion", "completed"],
        }
        result = self._post("/v1/trenches", body)
        return result.get("data", {})

    def get_token_info(self, mint: str, chain: str = "sol") -> Optional[dict]:
        for attempt in range(2):
            try:
                result = self._get(f"/v1/token/info/{chain}/{mint}")
                return result.get("data")
            except RateLimitError as e:
                if attempt == 0:
                    wait = max(1.0, e.reset_at - time.time()) + 1
                    logger.warning("Rate limit em token info, aguardando %.1fs", wait)
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
                    logger.warning("Rate limit em token security, aguardando %.1fs", wait)
                    time.sleep(wait)
                else:
                    raise
            except Exception as e:
                logger.warning("token security %s: %s", mint[:8], e)
                return None
        return None
