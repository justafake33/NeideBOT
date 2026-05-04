import os
import time
import logging
import threading
import json as _json
import base64
import requests
from typing import Optional
from uuid import uuid4
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

GMGN_HOST        = os.getenv("GMGN_HOST",        "https://openapi.gmgn.ai")
GMGN_API_KEY     = os.getenv("GMGN_API_KEY",     "")
GMGN_PRIVATE_KEY = os.getenv("GMGN_PRIVATE_KEY", "")


def _load_ed25519_key(pem: str):
    """Carrega chave Ed25519 PEM. Retorna None se pem vazio ou inválido."""
    if not pem:
        return None
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        pem_bytes = pem.encode() if isinstance(pem, str) else pem
        if not pem_bytes.strip().startswith(b"-----"):
            # Trata raw base64 — envolve em cabeçalho PEM PKCS8
            pem_bytes = (
                b"-----BEGIN PRIVATE KEY-----\n"
                + base64.b64encode(pem_bytes) + b"\n"
                + b"-----END PRIVATE KEY-----\n"
            )
        return load_pem_private_key(pem_bytes, password=None)
    except Exception as e:
        logger.error("GMGN_PRIVATE_KEY inválida: %s", e)
        return None

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
        self._private_key = _load_ed25519_key(GMGN_PRIVATE_KEY)
        if GMGN_PRIVATE_KEY and not self._private_key:
            logger.warning("GMGN_PRIVATE_KEY definida mas inválida — trade desabilitado")
        elif self._private_key:
            logger.info("GMGN_PRIVATE_KEY carregada — trade habilitado")

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

    # ------------------------------------------------------------------
    # Assinatura Ed25519 para endpoints de trade
    # ------------------------------------------------------------------

    def _sign(self, subpath: str, params: dict, body: dict) -> str:
        """
        Ed25519 sobre: subpath + sorted_query_params + compact_json_body + timestamp
        Signature codificada em base64.
        """
        ts          = params.get("timestamp", str(int(time.time())))
        qs          = urlencode(sorted(params.items()))
        body_str    = _json.dumps(body, separators=(",", ":"), sort_keys=True) if body else ""
        message     = f"{subpath}{qs}{body_str}{ts}".encode("utf-8")
        sig         = self._private_key.sign(message)
        return base64.b64encode(sig).decode("ascii")

    def _post_trade(self, path: str, body: dict = None,
                    extra_params: dict = None) -> dict:
        """POST com X-Signature (Ed25519) para endpoints de trade."""
        if not self._private_key:
            raise RuntimeError(
                "GMGN_PRIVATE_KEY não configurada — endpoints de trade desabilitados"
            )
        body   = body or {}
        params = self._params(extra_params)
        sig    = self._sign(path, params, body)
        resp   = self.session.post(
            f"{self.host}{path}",
            json=body,
            params=params,
            headers={"X-Signature": sig},
            timeout=15,
        )
        return self._handle_response(resp)

    # ------------------------------------------------------------------
    # Trade endpoints — Fase 6-lite
    # ------------------------------------------------------------------

    def swap(self, token_mint: str, amount_sol: float,
             slippage_bps: int = 500,
             chain: str = "sol") -> dict:
        """
        POST /v1/trade/swap — executa compra do token com SOL.
        amount_sol:   quantidade em SOL (ex: 0.005 ≈ $1 a $200/SOL).
        slippage_bps: slippage máximo em basis points (500 = 5%).

        Priority fee e tip NÃO são enviados — GMGN gerencia internamente.
        Enviar esses campos com unidade errada pode causar fees absurdos
        (ex: 100000 microlamports/CU × 300k CU = $6+ por trade).
        """
        lamports = int(round(amount_sol * 1_000_000_000))
        body = {
            "chain":         chain,
            "token_address": token_mint,
            "side":          "buy",
            "amount":        str(lamports),
            "slippage":      slippage_bps,
        }
        return self._post_trade("/v1/trade/swap", body, extra_params={"chain": chain})

    def create_strategy(self, token_mint: str, tokens_amount: float,
                        tp_pct: float = 0.75, sl_pct: float = 0.15,
                        chain: str = "sol") -> dict:
        """
        POST /v1/trade/strategy/create — configura TP/SL automático.
        tp_pct: take profit como fração (0.75 = +75%).
        sl_pct: stop loss como fração (0.15 = -15%).
        Retorna dict com strategy_id.
        """
        body = {
            "chain":           chain,
            "token_address":   token_mint,
            "amount":          str(int(tokens_amount)),
            "take_profit_pct": round(tp_pct * 100, 2),
            "stop_loss_pct":   round(sl_pct * 100, 2),
        }
        return self._post_trade(
            "/v1/trade/strategy/create", body, extra_params={"chain": chain}
        )

    def query_order(self, order_id: str, chain: str = "sol") -> dict:
        """GET /v1/trade/query_order — status e fill de uma ordem."""
        return self._get("/v1/trade/query_order", {"chain": chain, "order_id": order_id})

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

    def get_token_signal(self, chain: str = "sol") -> list:
        """
        POST /v1/market/token_signal — feed global de sinais type 12 (SmartDegenBuy).
        Retorna todos os tokens com smart wallets comprando nas últimas ~8.3h.
        Filtragem pelo caller (cross-reference com HotMonitor._tokens).
        """
        body = {"chain": chain, "groups": [{"signal_type": [12]}]}
        try:
            result = self._post("/v1/market/token_signal", body)
            return result.get("data", []) or []
        except RateLimitError:
            raise
        except Exception as e:
            logger.warning("token_signal erro: %s", e)
            return []

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


_sol_usd_lock      = threading.Lock()
_sol_usd_value:    Optional[float] = None
_sol_usd_ts:       float           = 0.0
_SOL_USD_TTL       = 300  # 5 minutes


def get_sol_usd() -> Optional[float]:
    """Retorna preço SOL/USD com cache de 5min. Tenta CoinGecko, fallback GMGN."""
    global _sol_usd_value, _sol_usd_ts

    with _sol_usd_lock:
        if _sol_usd_value and (time.time() - _sol_usd_ts) < _SOL_USD_TTL:
            return _sol_usd_value

    price = _fetch_sol_usd_coingecko()
    if not price:
        price = _fetch_sol_usd_gmgn()

    if price and price > 0:
        with _sol_usd_lock:
            _sol_usd_value = price
            _sol_usd_ts    = time.time()
        logger.debug("SOL/USD atualizado: $%.2f", price)
    else:
        logger.warning("Não foi possível obter preço SOL/USD — usando último conhecido")
        with _sol_usd_lock:
            price = _sol_usd_value

    return price


def _fetch_sol_usd_coingecko() -> Optional[float]:
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "solana", "vs_currencies": "usd"},
            timeout=10,
        )
        resp.raise_for_status()
        return float(resp.json()["solana"]["usd"])
    except Exception as e:
        logger.debug("CoinGecko SOL/USD falhou: %s", e)
        return None


def _fetch_sol_usd_gmgn() -> Optional[float]:
    try:
        resp = requests.get(
            f"{GMGN_HOST.rstrip('/')}/v1/token/info/sol/So11111111111111111111111111111111111111112",
            headers={"X-APIKEY": GMGN_API_KEY},
            params={"timestamp": str(int(time.time())), "client_id": str(uuid4())},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        for key in ("price", "price_usd", "usd_price", "last_price"):
            v = data.get(key)
            if v is not None:
                try:
                    f = float(v)
                    if f > 0:
                        return f
                except Exception:
                    pass
        return None
    except Exception as e:
        logger.debug("GMGN SOL/USD falhou: %s", e)
        return None
