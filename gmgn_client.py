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

GMGN_HOST           = os.getenv("GMGN_HOST",           "https://openapi.gmgn.ai")
GMGN_API_KEY        = os.getenv("GMGN_API_KEY",        "")
GMGN_PRIVATE_KEY    = os.getenv("GMGN_PRIVATE_KEY",    "")
GMGN_WALLET_ADDRESS = os.getenv("GMGN_WALLET_ADDRESS", "")

SOL_NATIVE_MINT = "So11111111111111111111111111111111111111112"


def _load_ed25519_key(pem: str):
    """
    Carrega chave Ed25519. Suporta múltiplos formatos:
      1. PKCS8 PEM (-----BEGIN PRIVATE KEY-----)
      2. Base64 raw (32 bytes seed ou 64 bytes seed+pub)
      3. JSON array de bytes ([1,2,3,...])
      4. Base58 (formato Solana keypair, 64 bytes)
    Railway substitui newlines reais por \\n literais — normalizado aqui.
    """
    if not pem:
        return None

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    raw = pem.strip().replace("\\n", "\n") if isinstance(pem, str) else pem.decode()

    # 1. Tenta PEM padrão (PKCS8)
    if "-----" in raw:
        try:
            return load_pem_private_key(raw.encode(), password=None)
        except Exception as e:
            logger.debug("PEM PKCS8 falhou: %s", e)

    # 2. Tenta JSON array de bytes [1,2,3,...] (formato Solana CLI keypair)
    if raw.startswith("["):
        try:
            import json as _j
            arr   = _j.loads(raw)
            seed  = bytes(arr[:32])
            return Ed25519PrivateKey.from_private_bytes(seed)
        except Exception as e:
            logger.debug("JSON array falhou: %s", e)

    # 3. Tenta base58 (Phantom/Solflare export: 64 bytes = seed+pubkey, ~88 chars Base58)
    # ⚠️ deve vir ANTES do Base64: alfabetos se sobrepõem e Base64 decodifica Base58 erroneamente
    try:
        import base58 as _b58
        _raw_b58 = raw.replace(" ", "").replace("\n", "")
        decoded = _b58.b58decode(_raw_b58)
        if len(decoded) in (32, 64):
            seed = decoded[:32]
            return Ed25519PrivateKey.from_private_bytes(seed)
    except Exception as e:
        logger.debug("Base58 falhou: %s", e)

    # 4. Tenta base64 puro (apenas se tamanho decodificado = 32 ou 64 bytes)
    try:
        raw_b64 = raw.replace(" ", "").replace("\n", "")
        raw_b64 += "=" * (-len(raw_b64) % 4)
        decoded = base64.b64decode(raw_b64)
        if len(decoded) not in (32, 64):
            raise ValueError(f"Base64 decodificou {len(decoded)} bytes — esperado 32 ou 64")
        seed = decoded[:32]
        return Ed25519PrivateKey.from_private_bytes(seed)
    except Exception as e:
        logger.debug("Base64 raw falhou: %s", e)

    logger.error(
        "GMGN_PRIVATE_KEY inválida — nenhum formato reconhecido. "
        "Formatos aceitos: PKCS8 PEM, base64 (32/64 bytes), JSON array, base58."
    )
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


# Gate global — compartilhado por todas as threads e instâncias de GmgnClient.
# Quando qualquer chamada recebe 429, TODAS as threads param até reset_at.
_global_ban_until: float = 0.0
_global_ban_lock  = threading.Lock()


def _set_global_ban(reset_at: float):
    global _global_ban_until
    with _global_ban_lock:
        if reset_at > _global_ban_until:
            _global_ban_until = reset_at
            logger.warning("Rate limit global — todas as threads pausadas até %.0fs (em %.1fs)",
                           reset_at, reset_at - time.time())


def _wait_global_ban():
    """Bloqueia a thread atual até o ban global expirar."""
    now = time.time()
    if now < _global_ban_until:
        wait = _global_ban_until - now + 0.2
        logger.debug("Rate limit global ativo — aguardando %.1fs", wait)
        time.sleep(wait)


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
            wallet_short = GMGN_WALLET_ADDRESS[:8] + "..." if GMGN_WALLET_ADDRESS else "NÃO CONFIGURADO"
            logger.info("GMGN_PRIVATE_KEY carregada — trade habilitado (wallet: %s)", wallet_short)
            if not GMGN_WALLET_ADDRESS:
                logger.warning("GMGN_WALLET_ADDRESS não configurada — trades falharão")

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
            _set_global_ban(reset_at)   # para TODAS as threads imediatamente
            raise RateLimitError(reset_at)
        if not resp.ok:
            try:
                err_body = resp.json()
            except Exception:
                err_body = resp.text
            logger.error("GMGN HTTP %d %s — body: %s", resp.status_code, resp.url, err_body)
        resp.raise_for_status()
        body = resp.json()
        if body.get("code", 0) != 0:
            raise Exception(f"GMGN error {body.get('code')}: {body.get('message')}")
        return body

    def _get(self, path: str, params: dict = None) -> dict:
        _wait_global_ban()
        resp = self.session.get(
            f"{self.host}{path}",
            params=self._params(params),
            timeout=15,
        )
        return self._handle_response(resp)

    def _post(self, path: str, body: dict = None, extra_params: dict = None) -> dict:
        _wait_global_ban()
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

    def _sign(self, subpath: str, params: dict, body_str: str) -> str:
        """
        Ed25519 sobre: "{subpath}:{sorted_qs}:{body_str}:{timestamp}"
        Formato oficial GMGN OpenApiClient (signer.ts buildMessage).
        body_str: JSON já serializado (mesmo string enviado no HTTP body).
        GET requests: body_str="".
        """
        ts      = params.get("timestamp", str(int(time.time())))
        qs      = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        message = f"{subpath}:{qs}:{body_str}:{ts}".encode("utf-8")
        logger.debug("SIGN message: %s", message.decode("utf-8"))
        sig     = self._private_key.sign(message)
        return base64.b64encode(sig).decode("ascii")

    def _post_trade(self, path: str, body: dict = None) -> dict:
        """POST com X-Signature (Ed25519) para endpoints de trade.
        O body é serializado UMA vez e o mesmo string é usado na assinatura e no HTTP body.
        """
        if not self._private_key:
            raise RuntimeError(
                "GMGN_PRIVATE_KEY não configurada — endpoints de trade desabilitados"
            )
        if not GMGN_WALLET_ADDRESS:
            raise RuntimeError(
                "GMGN_WALLET_ADDRESS não configurada — endpoints de trade desabilitados"
            )
        _wait_global_ban()
        body_str = _json.dumps(body or {}, separators=(",", ":"))
        params   = self._params()  # só timestamp + client_id
        logger.info("TRADE POST %s | wallet=%s | body=%s", path,
                    GMGN_WALLET_ADDRESS[:8] + "..." if GMGN_WALLET_ADDRESS else "N/A",
                    body_str)
        sig      = self._sign(path, params, body_str)
        resp     = self.session.post(
            f"{self.host}{path}",
            data=body_str,                               # raw string — mesmo bytes assinados
            params=params,
            headers={"X-Signature": sig, "Content-Type": "application/json"},
            timeout=15,
        )
        return self._handle_response(resp)

    def _get_trade(self, path: str, extra_params: dict = None) -> dict:
        """GET com X-Signature (Ed25519) para endpoints de trade (query_order)."""
        if not self._private_key:
            raise RuntimeError(
                "GMGN_PRIVATE_KEY não configurada — endpoints de trade desabilitados"
            )
        _wait_global_ban()
        params = self._params(extra_params)
        sig    = self._sign(path, params, "")  # GET: body="" (não None — evita "None" literal no f-string)
        resp   = self.session.get(
            f"{self.host}{path}",
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
             trailing_activation_pct: float | None = None,
             trailing_drawdown_pct: float | None = None,
             sl_pct: float | None = None,
             chain: str = "sol") -> dict:
        """
        POST /v1/trade/swap — compra token com SOL.
        condition_orders embutidos: trailing TP + SL fixo calculados pelo GMGN sobre o fill real.

          profit_stop_trace (trailing):
            price_scale   = activation gain % (0-100): "100" = ativa quando dobra
            drawdown_rate = recuo máximo do pico % (0-100): "30" = vende se cair 30% do pico

          loss_stop (SL fixo):
            price_scale = "15" → vende se cair 15% do preço de entrada

          Todos os valores em escala percentual (0-100), NÃO decimal.
        """
        lamports = int(round(amount_sol * 1_000_000_000))
        body = {
            "chain":        chain,
            "from_address": GMGN_WALLET_ADDRESS,
            "input_token":  SOL_NATIVE_MINT,
            "output_token": token_mint,
            "input_amount": str(lamports),
            "slippage":     slippage_bps,
        }
        condition_orders = []
        if trailing_activation_pct is not None and trailing_drawdown_pct is not None:
            condition_orders.append({
                "order_type":    "profit_stop_trace",
                "side":          "sell",
                "price_scale":   str(round(trailing_activation_pct, 2)),  # ex: "100" = +100%
                "drawdown_rate": str(round(trailing_drawdown_pct,   2)),  # ex: "30"  = 30% do pico
                "sell_ratio":    "100",
            })
        if sl_pct is not None:
            condition_orders.append({
                "order_type":  "loss_stop",
                "side":        "sell",
                "price_scale": str(round(sl_pct * 100, 2)),  # 15 = -15%
                "sell_ratio":  "100",
            })
        if condition_orders:
            body["condition_orders"] = condition_orders
            body["priority_fee"] = os.getenv("STRATEGY_PRIORITY_FEE", "0.0001")
            body["tip_fee"]      = os.getenv("STRATEGY_TIP_FEE",      "0.0001")
        return self._post_trade("/v1/trade/swap", body)

    def create_strategy(self, token_mint: str, tokens_amount: float,
                        entry_price_sol: float,
                        tp_pct: float = 0.75, sl_pct: float = 0.15,
                        chain: str = "sol") -> dict:
        """
        Cria TP e SL como duas ordens separadas em /v1/trade/strategy/create.

        check_price: preço trigger em SOL/token — formatado sem notação científica.
        amount_in: quantidade de tokens em unidades RAW SPL (×10^6 para 6 casas decimais).
        """
        tp_price = entry_price_sol * (1 + tp_pct)
        sl_price = entry_price_sol * (1 - sl_pct)
        # Formata sem notação científica — GMGN rejeita "1.342e-07"
        tp_price_str = f"{tp_price:.12f}"
        sl_price_str = f"{sl_price:.12f}"
        amt_str  = str(int(tokens_amount))
        prio_fee = os.getenv("STRATEGY_PRIORITY_FEE", "0.0001")
        tip_fee  = os.getenv("STRATEGY_TIP_FEE",      "0.0001")

        tp_body = {
            "chain":          chain,
            "from_address":   GMGN_WALLET_ADDRESS,
            "base_token":     token_mint,
            "quote_token":    SOL_NATIVE_MINT,
            "order_type":     "limit_order",
            "sub_order_type": "take_profit",
            "check_price":    tp_price_str,
            "amount_in":      amt_str,
            "auto_slippage":  True,
            "priority_fee":   prio_fee,
            "tip_fee":        tip_fee,
        }
        sl_body = {
            "chain":          chain,
            "from_address":   GMGN_WALLET_ADDRESS,
            "base_token":     token_mint,
            "quote_token":    SOL_NATIVE_MINT,
            "order_type":     "limit_order",
            "sub_order_type": "stop_loss",
            "check_price":    sl_price_str,
            "amount_in":      amt_str,
            "auto_slippage":  True,
            "priority_fee":   prio_fee,
            "tip_fee":        tip_fee,
        }

        tp_res = self._post_trade("/v1/trade/strategy/create", tp_body)
        sl_res = self._post_trade("/v1/trade/strategy/create", sl_body)

        tp_data = tp_res.get("data") or tp_res
        sl_data = sl_res.get("data") or sl_res
        logger.info("Estratégia criada — TP order_id=%s SL order_id=%s",
                    tp_data.get("order_id"), sl_data.get("order_id"))
        return {"tp": tp_data, "sl": sl_data}

    def query_order(self, order_id: str, chain: str = "sol") -> dict:
        """GET /v1/trade/query_order — status e fill de uma ordem (signed)."""
        return self._get_trade("/v1/trade/query_order",
                               {"order_id": order_id, "chain": chain})

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

    def get_market_trending(self, chain: str = "sol", interval: str = "5m",
                            limit: int = 100, order_by: str = "default",
                            direction: str = "desc", filters: list[str] | None = None,
                            platforms: list[str] | None = None) -> list:
        """
        GET /v1/market/rank — ranking/trending GMGN.

        Uso inicial do NeideBOT: radar passivo. O método retorna uma lista
        tolerante a pequenas variações de envelope da OpenAPI (`data.rank`,
        `data.list`, `data.items` ou lista direta).
        """
        params = {
            "chain": chain,
            "interval": interval,
            "limit": max(1, min(int(limit or 100), 100)),
            "order_by": order_by,
            "direction": direction,
        }
        if filters:
            params["filter"] = filters
        if platforms:
            params["platform"] = platforms

        result = self._get("/v1/market/rank", params)
        code = result.get("code")
        if code is not None and str(code) not in ("0", "200"):
            logger.warning("market/rank erro API: code=%s message=%s reason=%s",
                           code, result.get("message"), result.get("reason"))
            return []
        data = result.get("data", {})
        if isinstance(data, list):
            return data
        # percorre até 2 níveis de envelopes aninhados
        for candidate in (data, data.get("data") if isinstance(data, dict) else None):
            if not isinstance(candidate, dict):
                continue
            for key in ("rank", "list", "items"):
                rows = candidate.get(key)
                if isinstance(rows, list):
                    return rows
        logger.warning("market/rank envelope inesperado: code=%s data_keys=%s",
                       code, list(data.keys()) if isinstance(data, dict) else type(data).__name__)
        return []

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
        logger.warning("Não foi possível obter preço SOL/USD via CoinGecko/GMGN — usando fallback")
        with _sol_usd_lock:
            price = _sol_usd_value
        if not price:
            try:
                price = float(os.getenv("SOL_USD_FALLBACK", "65"))
                if price <= 0:
                    price = None
            except Exception:
                price = None
        if price and price > 0:
            with _sol_usd_lock:
                _sol_usd_value = price
                _sol_usd_ts = time.time()
            logger.warning("SOL/USD fallback ativo: $%.2f", price)

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
