import os
import time
import logging
import requests
from typing import Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

GMGN_HOST    = os.getenv("GMGN_HOST", "https://openapi.gmgn.ai")
GMGN_API_KEY = os.getenv("GMGN_API_KEY", "")

# Plataformas e quote address types por chain (fonte: GMGN OpenApiClient.ts)
_PLATFORMS = {
    "sol": [
        "Pump.fun", "pump_mayhem", "pump_mayhem_agent", "pump_agent",
        "letsbonk", "bonkers", "bags", "memoo", "liquid", "bankr", "zora",
        "surge", "anoncoin", "moonshot_app", "wendotdev", "heaven", "sugar",
        "token_mill", "believe", "trendsfun", "trends_fun", "jup_studio",
        "Moonshot", "boop", "ray_launchpad", "meteora_virtual_curve", "xstocks",
    ],
    "bsc":  ["fourmeme", "fourmeme_agent", "bn_fourmeme", "four_xmode_agent",
             "flap", "clanker", "lunafun"],
    "base": ["clanker", "bankr", "flaunch", "zora", "zora_creator",
             "baseapp", "basememe", "virtuals_v2", "klik"],
}

_QUOTE_ADDRESS_TYPES = {
    "sol":  [4, 5, 3, 1, 13, 0],
    "bsc":  [6, 7, 1, 16, 8, 3, 9, 10, 2, 17, 18, 0],
    "base": [11, 3, 12, 13, 0],
}


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

    def _post(self, path: str, body: dict = None, extra_params: dict = None) -> dict:
        resp = self.session.post(
            f"{self.host}{path}",
            json=body or {},
            params=self._params(extra_params),
            timeout=15,
        )
        return self._handle_response(resp)

    def get_trenches(self, types: list = None, chain: str = "sol") -> dict:
        """
        POST /v1/trenches — chain como query param, body version=v2.
        near_completion -> data['pump'] | completed -> data['completed']
        """
        selected = types or ["near_completion", "completed"]
        section = {
            "filters": ["offchain", "onchain"],
            "launchpad_platform": _PLATFORMS.get(chain, []),
            "quote_address_type": _QUOTE_ADDRESS_TYPES.get(chain, []),
            "launchpad_platform_v2": True,
            "limit": 80,
        }
        body = {"version": "v2"}
        for t in selected:
            body[t] = dict(section)

        result = self._post("/v1/trenches", body, extra_params={"chain": chain})
        return result.get("data", {})

    def get_token_kline(self, mint: str, chain: str = "sol",
                        resolution: str = "1m",
                        from_ts: int = None, to_ts: int = None) -> list:
        """Retorna lista de candles OHLCV. time em ms, open/close/high/low/volume em str."""
        params: dict = {"chain": chain, "address": mint, "resolution": resolution}
        if from_ts is not None:
            params["from"] = str(from_ts)
        if to_ts is not None:
            params["to"] = str(to_ts)
        try:
            result = self._get("/v1/market/token_kline", params)
            return result.get("data", {}).get("list", []) or []
        except Exception as e:
            logger.debug("kline %s: %s", mint[:8], e)
            return []

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
                if "404" not in str(e):
                    logger.warning("token info %s: %s", mint[:8], e)
                return None
        return None

    def get_solana_price_usd(self, chain: str = "sol") -> Optional[float]:
        """Obtém preço spot de SOL em USD usando token info da própria GMGN."""
        sol_mint = "So11111111111111111111111111111111111111112"
        info = self.get_token_info(sol_mint, chain=chain)
        if not info:
            return None

        for key in ("price", "price_usd", "usd_price", "last_price"):
            v = info.get(key)
            try:
                if v is not None and float(v) > 0:
                    return float(v)
            except Exception:
                continue
        return None
