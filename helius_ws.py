"""
Helius WebSocket — preço real-time via logsSubscribe + parse Anchor direto.

Fluxo por token:
  1. subscribe(mint, pool_address, total_supply)
       → logsSubscribe(pool_address) no WebSocket Helius (~200ms pós on-chain)
  2. Swap detectado na notificação:
       → Tier 1: parse Program data: <base64> da notificação (zero chamadas, ~0ms)
         O Meteora DAMM v2 é Anchor — emite SwapEvent com in_amount/out_amount
         no próprio payload que já recebemos. Preço exato sem custo extra.
       → Tier 2: DexScreener fallback se o parse falhar (1-3s, gratuito)
  3. cancel(mint) → logsUnsubscribe

Rate limit: 1 parse por 2s por token.
Tokens em dump ativo geram 5-20 swaps/s — não precisamos processar todos.
"""
import base64
import hashlib
import json
import logging
import os
import struct
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
_WS_URL        = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

# Program ID do Meteora DAMM v2 (CP AMM)
METEORA_PROGRAM = "cpamdpKoS7RUn9RDdHsS3SuRH5BVTTvovCHQnGFumm9"

_RATE_LIMIT_SEC = 2.0
_TOKEN_DECIMALS = 6        # Pump.fun SPL tokens (6 casas decimais)
_SOL_DECIMALS   = 9        # SOL/WSOL em lamports
_MC_MIN         = 1_000    # $1K — abaixo disso é ruído
_MC_MAX         = 10_000_000  # $10M — acima é suspeito para micro-caps


# ─────────────────────────────────────────────────────────────────────────────
# Parse Anchor — extrai in_amount/out_amount do Program data: <base64>
# ─────────────────────────────────────────────────────────────────────────────

def _anchor_disc(event_name: str) -> bytes:
    """Discriminador Anchor: sha256('event:<name>')[:8]."""
    return hashlib.sha256(f"event:{event_name}".encode()).digest()[:8]


# Candidatos para o nome do evento de swap do Meteora DAMM v2.
# O programa se chama "cpamd" (CP AMM Dynamic) — tentamos as variações mais prováveis.
_SWAP_DISCS: set[bytes] = {
    _anchor_disc("Swap"),
    _anchor_disc("SwapEvent"),
    _anchor_disc("CpAmmSwapEvent"),
    _anchor_disc("AddLiquidity"),   # ignoramos se bater — detectado pelo check de swap abaixo
}
# Apenas estes dois são eventos de swap (os outros são líquidos — descartamos)
_SWAP_ONLY_DISCS: set[bytes] = {
    _anchor_disc("Swap"),
    _anchor_disc("SwapEvent"),
    _anchor_disc("CpAmmSwapEvent"),
}


def _parse_swap_amounts(raw: bytes) -> tuple[int, int] | None:
    """
    Tenta extrair (in_amount_raw, out_amount_raw) de um evento Anchor de swap.

    Layout do payload após 8 bytes de discriminador:
      pool:       Pubkey  = 32 bytes (ignorado)
      in_amount:  u64     = 8 bytes  ← montante de entrada (lamports OU raw tokens)
      out_amount: u64     = 8 bytes  ← montante de saída

    Recebe bytes já decodificados de base64.
    """
    # Mínimo: discriminador(8) + pool(32) + in_amount(8) + out_amount(8) = 56 bytes
    if len(raw) < 56:
        return None

    disc = raw[:8]
    if disc not in _SWAP_ONLY_DISCS:
        return None

    offset     = 8 + 32  # pula discriminador + pool pubkey
    in_amount  = struct.unpack_from("<Q", raw, offset)[0]
    out_amount = struct.unpack_from("<Q", raw, offset + 8)[0]

    if in_amount == 0 or out_amount == 0:
        return None

    return in_amount, out_amount


def _mc_from_amounts(in_raw: int, out_raw: int, supply: float, sol_usd: float) -> float | None:
    """
    Computa MC em USD a partir de in_amount/out_amount de um swap SOL↔Token.
    Tenta as duas direções (compra e venda) e aceita a que der MC plausível.
    """
    supply = supply or 1_000_000_000

    # Direção 1: in=SOL(lamports), out=token(raw) → compra de token
    sol_in  = in_raw  / 10 ** _SOL_DECIMALS
    tok_out = out_raw / 10 ** _TOKEN_DECIMALS
    if tok_out > 0:
        mc1 = (sol_in * sol_usd / tok_out) * supply
        if _MC_MIN < mc1 < _MC_MAX:
            return mc1

    # Direção 2: in=token(raw), out=SOL(lamports) → venda de token
    tok_in  = in_raw  / 10 ** _TOKEN_DECIMALS
    sol_out = out_raw / 10 ** _SOL_DECIMALS
    if tok_in > 0:
        mc2 = (sol_out * sol_usd / tok_in) * supply
        if _MC_MIN < mc2 < _MC_MAX:
            return mc2

    return None


def _parse_mc_from_logs(logs: list, supply: float, sol_usd: float) -> float | None:
    """
    Percorre os logs de uma logsNotification e retorna o MC calculado
    a partir dos eventos Anchor. Retorna None se nada reconhecido.

    Loga INFO para discriminadores desconhecidos — ajuda a identificar o nome
    correto do evento do Meteora DAMM v2 na primeira execução real.
    """
    for log in logs:
        if not log.startswith("Program data: "):
            continue
        b64 = log[len("Program data: "):]
        try:
            raw = base64.b64decode(b64)
        except Exception:
            continue

        if len(raw) < 8:
            continue

        disc = raw[:8]
        if disc not in _SWAP_ONLY_DISCS:
            # Discriminador não reconhecido — loga para calibrar na produção
            logger.info(
                "HeliusWS: Program data desconhecido disc=%s len=%d — "
                "adicionar a _SWAP_ONLY_DISCS se for swap do Meteora DAMM v2",
                disc.hex(), len(raw),
            )
            continue

        result = _parse_swap_amounts(raw)
        if result is None:
            continue
        in_raw, out_raw = result
        mc = _mc_from_amounts(in_raw, out_raw, supply, sol_usd)
        if mc:
            return mc
    return None


# ─────────────────────────────────────────────────────────────────────────────
# HeliusWS
# ─────────────────────────────────────────────────────────────────────────────

class HeliusWS:
    """WebSocket manager — rastreamento de preço via Helius logsSubscribe."""

    def __init__(self, on_price: Callable[[str, float], None]):
        """on_price(mint, market_cap_usd) — compatível com HotMonitor.push_mc."""
        self._on_price = on_price

        self._mint_pool:   dict[str, str]   = {}
        self._pool_mint:   dict[str, str]   = {}
        self._mint_supply: dict[str, float] = {}
        self._sub_id_mint: dict[int, str]   = {}
        self._req_mint:    dict[int, str]   = {}
        self._last_fetch:  dict[str, float] = {}

        self._lock      = threading.Lock()
        self._ws_lock   = threading.Lock()
        self._ws        = None
        self._req_id    = 1
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
        with self._lock:
            if mint in self._mint_pool:
                return
            self._mint_pool[mint]        = pool_address
            self._pool_mint[pool_address] = mint
            self._mint_supply[mint]      = total_supply

        if self._connected:
            self._send_subscribe(mint, pool_address)

        logger.info("HeliusWS: subscribe %s pool=%s...", mint[:8], pool_address[:8])

    def unsubscribe(self, mint: str):
        sub_id_to_remove = None
        with self._lock:
            self._mint_pool.pop(mint, None)
            pool = next((p for p, m in self._pool_mint.items() if m == mint), None)
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
            logger.error("websocket-client não instalado — pip install websocket-client")
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

        # Confirmação de subscrição
        if isinstance(data.get("result"), int) and "id" in data:
            req_id = data["id"]
            sub_id = data["result"]
            with self._lock:
                mint = self._req_mint.pop(req_id, None)
                if mint:
                    self._sub_id_mint[sub_id] = mint
            logger.debug("HeliusWS: sub confirmada %s sub_id=%d", (mint or "?")[:8], sub_id)
            return

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

        # Processa na mesma thread do WS se o parse for rápido (Anchor logs),
        # ou em thread separada apenas para o fallback DexScreener (I/O bloqueante)
        threading.Thread(
            target=self._fetch_and_notify,
            args=(mint, supply, logs),
            daemon=True,
            name=f"hprice-{mint[:6]}",
        ).start()

    def _on_error(self, ws, error):
        logger.warning("HeliusWS erro: %s", error)

    def _on_close(self, ws, code, msg):
        self._connected = False
        logger.info("HeliusWS fechado (code=%s)", code)

    # ──────────────────────────────────────────────────────────────────
    # Price resolution — Anchor logs → DexScreener fallback
    # ──────────────────────────────────────────────────────────────────

    def _fetch_and_notify(self, mint: str, supply: Optional[float], logs: list):
        """
        Tier 1 — Parse Anchor event dos logs (zero chamadas, ~0ms):
          Program data: <base64> já contém in_amount/out_amount do swap.
          Decodificamos Borsh com o discriminador do Meteora DAMM v2.

        Tier 2 — DexScreener fallback (1-3s) se o parse falhar:
          Programa novo, IDL mudou, formato diferente, etc.
        """
        from gmgn_client import get_sol_usd

        supply = supply or 1_000_000_000

        # Tier 1: parse direto dos logs Anchor
        sol_usd = get_sol_usd()
        if sol_usd:
            try:
                mc = _parse_mc_from_logs(logs, supply, sol_usd)
                if mc and mc > 0:
                    logger.debug("HeliusWS price(anchor) %s: $%.0f MC", mint[:8], mc)
                    self._on_price(mint, mc)
                    return
            except Exception as e:
                logger.debug("HeliusWS anchor parse %s: %s", mint[:8], e)

        # Tier 2: DexScreener
        from pool_resolver import get_price_usd
        try:
            price_usd = get_price_usd(mint)
            if price_usd and price_usd > 0:
                mc = price_usd * supply
                logger.debug("HeliusWS price(dex) %s: $%.0f MC", mint[:8], mc)
                self._on_price(mint, mc)
        except Exception as e:
            logger.debug("HeliusWS dex fallback %s: %s", mint[:8], e)

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
