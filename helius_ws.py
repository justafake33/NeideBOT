"""
Helius WebSocket — rastreamento de preço real-time para tokens Solana.

Fluxo por token:
  1. subscribe(mint, pool_address, total_supply)
       → logsSubscribe(pool_address) no WebSocket Helius
  2. Swap detectado (~200ms pós-confirmação on-chain)
       → get_price_usd(mint) via DexScreener (1-3s latência)
       → on_price(mint, market_cap_usd)
  3. cancel(mint) → logsUnsubscribe

Por que DexScreener e não getParsedTransaction:
  - Não consome créditos Helius (permanece no free plan)
  - Latência de 1-3s é suficiente para V-shapes >5s
  - Sem necessidade de parsear IDL do Meteora DAMM v2

Rate limit: máximo 1 fetch DexScreener por 2s por token
(Tokens pump em dump ativo: ~5-20 swaps/s — não precisamos parsear todos)
"""
import json
import logging
import os
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
_WS_URL        = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

# Program ID do Meteora DAMM v2 — filtra logs irrelevantes na pool
METEORA_PROGRAM = "cpamdpKoS7RUn9RDdHsS3SuRH5BVTTvovCHQnGFumm9"

# Intervalo mínimo entre fetches de preço por token
_RATE_LIMIT_SEC = 2.0


class HeliusWS:
    """WebSocket manager para rastreamento de preço via Helius + DexScreener."""

    def __init__(self, on_price: Callable[[str, float], None]):
        """
        on_price(mint, market_cap_usd): chamado a cada atualização de preço.
        Compatível com HotMonitor.push_mc(mint, mc).
        """
        self._on_price = on_price

        # Estado das subscrições
        self._mint_pool:    dict[str, str]   = {}   # mint → pool_address
        self._pool_mint:    dict[str, str]   = {}   # pool_address → mint
        self._mint_supply:  dict[str, float] = {}   # mint → total_supply
        self._sub_id_mint:  dict[int, str]   = {}   # ws_sub_id → mint
        self._req_mint:     dict[int, str]   = {}   # req_id → mint (aguardando confirmação)
        self._last_fetch:   dict[str, float] = {}   # mint → timestamp do último fetch

        self._lock    = threading.Lock()
        self._ws_lock = threading.Lock()
        self._ws      = None
        self._req_id  = 1
        self._running   = False
        self._connected = False

    # ──────────────────────────────────────────────────────────────────
    # API pública
    # ──────────────────────────────────────────────────────────────────

    def start(self):
        if not HELIUS_API_KEY:
            logger.warning("HELIUS_API_KEY não configurado — HeliusWS desativado")
            return
        self._running = True
        threading.Thread(target=self._run_loop, daemon=True, name="helius-ws").start()
        logger.info("HeliusWS iniciado (url=%s...)", _WS_URL[:40])

    def subscribe(self, mint: str, pool_address: str, total_supply: float):
        """Subscreve ao pool deste token no WebSocket."""
        with self._lock:
            if mint in self._mint_pool:
                return  # já subscrito
            self._mint_pool[mint]   = pool_address
            self._pool_mint[pool_address] = mint
            self._mint_supply[mint] = total_supply

        if self._connected:
            self._send_subscribe(mint, pool_address)
        # Se não conectado ainda, _on_open vai subscrever tudo de _mint_pool

        logger.info("HeliusWS: subscribe %s pool=%s...", mint[:8], pool_address[:8])

    def unsubscribe(self, mint: str):
        """Remove subscrição e limpa estado."""
        sub_id_to_remove = None
        with self._lock:
            self._mint_pool.pop(mint, None)
            pool = None
            for p, m in list(self._pool_mint.items()):
                if m == mint:
                    pool = p
                    break
            if pool:
                self._pool_mint.pop(pool, None)
            self._mint_supply.pop(mint, None)
            self._last_fetch.pop(mint, None)
            for sid, m in list(self._sub_id_mint.items()):
                if m == mint:
                    sub_id_to_remove = sid
                    del self._sub_id_mint[sid]
                    break

        if sub_id_to_remove is not None:
            self._ws_send({
                "jsonrpc": "2.0",
                "id":      self._next_req_id(),
                "method":  "logsUnsubscribe",
                "params":  [sub_id_to_remove],
            })
        logger.info("HeliusWS: unsubscribe %s", mint[:8])

    def stop(self):
        self._running = False
        with self._ws_lock:
            if self._ws:
                try:
                    self._ws.close()
                except Exception:
                    pass

    # ──────────────────────────────────────────────────────────────────
    # WebSocket interno
    # ──────────────────────────────────────────────────────────────────

    def _run_loop(self):
        try:
            import websocket as _ws_lib
        except ImportError:
            logger.error("websocket-client não instalado. Execute: pip install websocket-client")
            return

        backoff = 2.0
        while self._running:
            try:
                ws = _ws_lib.WebSocketApp(
                    _WS_URL,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                with self._ws_lock:
                    self._ws = ws
                ws.run_forever(ping_interval=20, ping_timeout=8)
                backoff = min(backoff * 2, 60)
            except Exception as e:
                logger.warning("HeliusWS loop: %s", e)

            if self._running:
                logger.info("HeliusWS reconectando em %.0fs...", backoff)
                time.sleep(backoff)

    def _on_open(self, ws):
        self._connected = True
        # Limpa sub IDs antigos (inválidos após reconexão)
        with self._lock:
            self._sub_id_mint.clear()
            self._req_mint.clear()
            active = list(self._mint_pool.items())

        logger.info("HeliusWS conectado — resubscrevendo %d tokens", len(active))
        for mint, pool in active:
            self._send_subscribe(mint, pool)

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
        except Exception:
            return

        # Confirmação de subscrição → mapeia req_id → sub_id
        if isinstance(data.get("result"), int) and "id" in data:
            req_id = data["id"]
            sub_id = data["result"]
            with self._lock:
                mint = self._req_mint.pop(req_id, None)
                if mint:
                    self._sub_id_mint[sub_id] = mint
            logger.debug("HeliusWS: sub confirmada %s sub_id=%d", (mint or "?")[:8], sub_id)
            return

        # Notificação de log
        if data.get("method") != "logsNotification":
            return

        params = data.get("params", {})
        sub_id = params.get("subscription")
        value  = params.get("result", {}).get("value", {})

        if value.get("err"):
            return

        signature = value.get("signature")
        if not signature:
            return

        with self._lock:
            mint = self._sub_id_mint.get(sub_id)
        if not mint:
            return

        # Filtra: só processar swaps (não transfers, criação de conta, etc.)
        logs = value.get("logs", [])
        is_swap = any(
            METEORA_PROGRAM in log or "Instruction: Swap" in log
            for log in logs
        )
        if not is_swap:
            return

        # Rate limit por token
        now = time.time()
        with self._lock:
            if now - self._last_fetch.get(mint, 0) < _RATE_LIMIT_SEC:
                return
            self._last_fetch[mint] = now
            supply = self._mint_supply.get(mint)

        threading.Thread(
            target=self._fetch_and_notify,
            args=(mint, supply),
            daemon=True,
            name=f"hprice-{mint[:6]}",
        ).start()

    def _on_error(self, ws, error):
        logger.warning("HeliusWS erro: %s", error)

    def _on_close(self, ws, code, msg):
        self._connected = False
        logger.info("HeliusWS fechado (code=%s)", code)

    # ──────────────────────────────────────────────────────────────────
    # Price fetch
    # ──────────────────────────────────────────────────────────────────

    def _fetch_and_notify(self, mint: str, supply: Optional[float]):
        """Busca preço atual no DexScreener e chama on_price."""
        from pool_resolver import get_price_usd
        try:
            price_usd = get_price_usd(mint)
            if not price_usd or price_usd <= 0:
                return

            mc = price_usd * (supply or 1_000_000_000)
            logger.debug("HeliusWS price %s: $%.0f", mint[:8], mc)
            self._on_price(mint, mc)

        except Exception as e:
            logger.debug("HeliusWS _fetch_and_notify %s: %s", mint[:8], e)

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────

    def _send_subscribe(self, mint: str, pool: str):
        req_id = self._next_req_id()
        with self._lock:
            self._req_mint[req_id] = mint
        self._ws_send({
            "jsonrpc": "2.0",
            "id":      req_id,
            "method":  "logsSubscribe",
            "params":  [
                {"mentions": [pool]},
                {"commitment": "processed"},
            ],
        })

    def _ws_send(self, payload: dict):
        try:
            with self._ws_lock:
                if self._ws and self._connected:
                    self._ws.send(json.dumps(payload))
        except Exception as e:
            logger.debug("HeliusWS ws_send: %s", e)

    def _next_req_id(self) -> int:
        with self._lock:
            rid = self._req_id
            self._req_id += 1
        return rid
