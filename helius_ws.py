"""
Helius WebSocket — preço real-time via logsSubscribe + parse Anchor direto.

Fluxo por token:
  1. subscribe(mint, total_supply)
       → logsSubscribe(mentions=[mint]) — qualquer tx que mencione o mint,
         sem precisar descobrir o pool address. Subscrição imediata na migração.
  2. Swap detectado na notificação (~200ms pós on-chain):
       → Tier 1: parse Program data: <base64> (zero chamadas, ~0ms)
         pump.fun pAMM (pAMMBay6...) emite BuyEvent/SellEvent com pool reserves.
         Preço calculado de pool_base/pool_quote — exato, sem ambiguidade de fee/direção.
       → Tier 2: DexScreener fallback se parse falhar (1-3s, gratuito)
  3. cancel(mint) → logsUnsubscribe

Por que mentions=[mint] e não mentions=[pool]:
  - Pool address requer chamada ao DexScreener antes da subscrição
  - DexScreener leva 1-5min para indexar tokens novos — janela do V-bottom perdida
  - O mint já é conhecido na migração; está sempre presente em transações de swap
  - Ruído extra (transfers, LP) filtrado pelo check de programa/instrução

Programas suportados (Tier 1):
  - pump.fun pAMM: pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA
    Eventos: BuyEvent (disc=67f4521f...) e SellEvent (disc=3e2f370a...)
    Layout: pool_base_token_reserves @ offset 48, pool_quote_token_reserves @ offset 56
  - Meteora DAMM v2: EvtSwap2 (candidato — não observado ainda)

Rate limit: 1 parse por 2s por token.
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

# Program IDs relevantes para filtrar swaps
PUMP_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
METEORA_PROGRAM  = "cpamdpKoS7RUn9RDdHsS3SuRH5BVTTvovCHQnGFumm9"
RAYDIUM_PROGRAM  = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"

_RATE_LIMIT_SEC = 2.0
_TOKEN_DECIMALS = 6        # Pump.fun SPL tokens (6 casas decimais)
_SOL_DECIMALS   = 9        # SOL/WSOL em lamports
_MC_MIN         = 1_000    # $1K — abaixo disso é ruído
_MC_MAX         = 10_000_000  # $10M — acima é suspeito para micro-caps


# ─────────────────────────────────────────────────────────────────────────────
# Parse Anchor — discriminadores e parsers por programa
# ─────────────────────────────────────────────────────────────────────────────

def _anchor_disc(event_name: str) -> bytes:
    """Discriminador Anchor: sha256('event:<name>')[:8]."""
    return hashlib.sha256(f"event:{event_name}".encode()).digest()[:8]


# pump.fun pAMM — confirmado via IDL pump-fun/pump-public-docs
# BuyEvent disc=67f4521f2cf57777, SellEvent disc=3e2f370aa503dc2a
_BUY_DISC  = _anchor_disc("BuyEvent")
_SELL_DISC = _anchor_disc("SellEvent")
_PUMP_AMM_DISCS: set[bytes] = {_BUY_DISC, _SELL_DISC}

# Meteora DAMM v2 — candidatos (EvtSwap2 não observado ainda nos logs)
_METEORA_DISCS: set[bytes] = {
    _anchor_disc("Swap"),
    _anchor_disc("SwapEvent"),
    _anchor_disc("CpAmmSwapEvent"),
    _anchor_disc("EvtSwap2"),
}

_SWAP_ONLY_DISCS: set[bytes] = _PUMP_AMM_DISCS | _METEORA_DISCS


def _parse_pump_amm_mc(raw: bytes, sol_usd: float, supply: float) -> float | None:
    """
    Parse pump.fun pAMM BuyEvent ou SellEvent — retorna MC em USD.

    Layout Borsh (offset a partir do início dos bytes, incluindo 8 bytes de disc):
      offset  0: disc                          8 bytes
      offset  8: timestamp                     i64  (ignorado)
      offset 16: base_amount_out/in            u64  (tokens)
      offset 24: max_quote_amount_in/min_...   u64  (limite, ignorado)
      offset 32: user_base_token_reserves      u64
      offset 40: user_quote_token_reserves     u64
      offset 48: pool_base_token_reserves      u64  ← reservas de token no pool
      offset 56: pool_quote_token_reserves     u64  ← reservas de SOL no pool
      offset 64: quote_amount_in/out           u64  (SOL transacionado)
      ... (mais campos: fees, pubkeys, ix_name, etc.)

    Fonte: pump-fun/pump-public-docs/idl/pump_amm.json
    """
    if len(raw) < 64:
        return None

    pool_base  = struct.unpack_from("<Q", raw, 48)[0]  # tokens no pool
    pool_quote = struct.unpack_from("<Q", raw, 56)[0]  # SOL no pool (lamports)

    if pool_base > 0 and pool_quote > 0:
        # Preço = (SOL_reserves / 10^9) / (token_reserves / 10^6) SOL/token
        price_sol = (pool_quote / 10 ** _SOL_DECIMALS) / (pool_base / 10 ** _TOKEN_DECIMALS)
        mc = price_sol * sol_usd * supply
        if _MC_MIN < mc < _MC_MAX:
            return mc

    # Fallback: usar amounts da swap (buy=SOL→token, sell=token→SOL)
    disc         = raw[:8]
    base_amount  = struct.unpack_from("<Q", raw, 16)[0]
    quote_amount = struct.unpack_from("<Q", raw, 64)[0]

    if base_amount == 0 or quote_amount == 0:
        return None

    if disc == _BUY_DISC:
        sol_in  = quote_amount / 10 ** _SOL_DECIMALS
        tok_out = base_amount  / 10 ** _TOKEN_DECIMALS
        if tok_out > 0:
            mc2 = (sol_in / tok_out) * sol_usd * supply
            if _MC_MIN < mc2 < _MC_MAX:
                return mc2
    else:  # SellEvent
        tok_in  = base_amount  / 10 ** _TOKEN_DECIMALS
        sol_out = quote_amount / 10 ** _SOL_DECIMALS
        if tok_in > 0:
            mc2 = (sol_out / tok_in) * sol_usd * supply
            if _MC_MIN < mc2 < _MC_MAX:
                return mc2

    return None


def _parse_meteora_mc(raw: bytes, sol_usd: float, supply: float) -> float | None:
    """
    Parse Meteora DAMM v2 swap event — layout legado: disc(8)+pool(32)+in(8)+out(8).
    Tenta as duas direções SOL↔Token.
    """
    if len(raw) < 56:
        return None

    offset     = 8 + 32
    in_amount  = struct.unpack_from("<Q", raw, offset)[0]
    out_amount = struct.unpack_from("<Q", raw, offset + 8)[0]

    if in_amount == 0 or out_amount == 0:
        return None

    supply = supply or 1_000_000_000

    # Direção 1: in=SOL, out=token (compra)
    sol_in  = in_amount  / 10 ** _SOL_DECIMALS
    tok_out = out_amount / 10 ** _TOKEN_DECIMALS
    if tok_out > 0:
        mc1 = (sol_in * sol_usd / tok_out) * supply
        if _MC_MIN < mc1 < _MC_MAX:
            return mc1

    # Direção 2: in=token, out=SOL (venda)
    tok_in  = in_amount  / 10 ** _TOKEN_DECIMALS
    sol_out = out_amount / 10 ** _SOL_DECIMALS
    if tok_in > 0:
        mc2 = (sol_out * sol_usd / tok_in) * supply
        if _MC_MIN < mc2 < _MC_MAX:
            return mc2

    return None


def _parse_mc_from_logs(logs: list, supply: float, sol_usd: float) -> float | None:
    """
    Percorre os logs e retorna o MC calculado dos eventos Anchor.
    Prioridade: pump.fun pAMM → Meteora DAMM v2 → ignora (log DEBUG).
    """
    supply = supply or 1_000_000_000

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

        if disc in _PUMP_AMM_DISCS:
            mc = _parse_pump_amm_mc(raw, sol_usd, supply)
            if mc:
                return mc

        elif disc in _METEORA_DISCS:
            mc = _parse_meteora_mc(raw, sol_usd, supply)
            if mc:
                return mc

        else:
            logger.debug(
                "HeliusWS: Program data desconhecido disc=%s len=%d",
                disc.hex(), len(raw),
            )

    return None


# ─────────────────────────────────────────────────────────────────────────────
# HeliusWS
# ─────────────────────────────────────────────────────────────────────────────

class HeliusWS:
    """WebSocket manager — subscrição por mint, sem depender de pool address."""

    def __init__(self, on_price: Callable[[str, float], None]):
        """on_price(mint, market_cap_usd) — compatível com HotMonitor.push_mc."""
        self._on_price = on_price

        self._mint_supply: dict[str, float] = {}   # mint → total_supply
        self._sub_id_mint: dict[int, str]   = {}   # ws_sub_id → mint
        self._req_mint:    dict[int, str]   = {}   # req_id → mint (aguardando confirmação)
        self._last_fetch:  dict[str, float] = {}   # mint → timestamp último parse

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

    def subscribe(self, mint: str, total_supply: float):
        """Subscreve ao mint no WebSocket — sem precisar do pool address."""
        with self._lock:
            if mint in self._mint_supply:
                return
            self._mint_supply[mint] = total_supply

        if self._connected:
            self._send_subscribe(mint)
        # Se não conectado ainda, _on_open resubscreve tudo de _mint_supply

        logger.info("HeliusWS: subscribe mint=%s...", mint[:8])

    def unsubscribe(self, mint: str):
        sub_id_to_remove = None
        with self._lock:
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
            active = list(self._mint_supply.keys())

        logger.info("HeliusWS conectado — resubscrevendo %d tokens", len(active))
        for mint in active:
            self._send_subscribe(mint)

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

        with self._lock:
            mint = self._sub_id_mint.get(sub_id)
        if not mint:
            return

        logs = value.get("logs", [])

        # Filtra: só swaps dos DEXes relevantes
        is_swap = any(
            PUMP_AMM_PROGRAM in log
            or METEORA_PROGRAM in log
            or RAYDIUM_PROGRAM in log
            or "Instruction: Swap" in log
            or "Instruction: Buy" in log
            or "Instruction: Sell" in log
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
        Tier 1 — Parse Anchor event dos logs (~0ms, zero chamadas extras).
        Tier 2 — DexScreener se parse falhar (1-3s).
        """
        from gmgn_client import get_sol_usd

        supply = supply or 1_000_000_000

        sol_usd = get_sol_usd()
        if sol_usd:
            try:
                mc = _parse_mc_from_logs(logs, supply, sol_usd)
                if mc and mc > 0:
                    logger.info("HeliusWS price(anchor) %s: $%.0f MC", mint[:8], mc)
                    self._on_price(mint, mc)
                    return
            except Exception as e:
                logger.debug("HeliusWS anchor parse %s: %s", mint[:8], e)

        from pool_resolver import get_price_usd
        try:
            price_usd = get_price_usd(mint)
            if price_usd and price_usd > 0:
                mc = price_usd * supply
                logger.info("HeliusWS price(dex) %s: $%.0f MC", mint[:8], mc)
                self._on_price(mint, mc)
        except Exception as e:
            logger.debug("HeliusWS dex fallback %s: %s", mint[:8], e)

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────

    def _send_subscribe(self, mint: str):
        req_id = self._next_req_id()
        with self._lock:
            self._req_mint[req_id] = mint
        self._ws_send({
            "jsonrpc": "2.0",
            "id":      req_id,
            "method":  "logsSubscribe",
            "params":  [
                {"mentions": [mint]},
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
