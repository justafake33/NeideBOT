"""
WebSocket de preço real-time — dois modos de operação:

MODO ERPC (primário, recomendado):
  Uma única subscrição global `transactionSubscribe` no programa pAMM pump.fun.
  Recebe TODAS as transações do pAMM desde o bloco 0, sem janela cega.
  O mint de cada transação é identificado cruzando os accountKeys da tx com
  os mints monitorados em _mint_supply. subscribe()/unsubscribe() apenas
  atualizam o dict local — nenhuma mensagem WS enviada.
  Requer: ERPC_API_KEY

MODO HELIUS (fallback):
  Uma subscrição `logsSubscribe(mentions=[mint])` por token.
  Requer subscrição após a migração → gap de 10-30s (subscription timing gap).
  Requer: HELIUS_API_KEY

Programas suportados (Tier 1 — parse Anchor inline, ~5ms):
  - pump.fun pAMM: pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA
    BuyEvent/SellEvent — pool_base @ offset 48, pool_quote @ offset 56
  - Meteora DAMM v2: EvtSwap2 (candidato — não observado ainda)

Tier 2 (fallback): DexScreener HTTP, rate-limitado a 2s por mint.
"""
import base64
import base58 as _base58
import hashlib
import json
import logging
import os
import struct
import threading
import time
from collections import deque
from typing import Callable

logger = logging.getLogger(__name__)

# ── Endpoints ────────────────────────────────────────────────────────────────
ERPC_API_KEY   = os.getenv("ERPC_API_KEY", "")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
_ERPC_WS_URL   = f"wss://edge.erpc.global?api-key={ERPC_API_KEY}"
_HELIUS_WS_URL = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

# Mantido para compatibilidade com referências externas (_WS_URL era público)
_WS_URL = _HELIUS_WS_URL

# Program IDs relevantes para filtrar swaps
PUMP_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
METEORA_PROGRAM  = "cpamdpKoS7RUn9RDdHsS3SuRH5BVTTvovCHQnGFumm9"
RAYDIUM_PROGRAM  = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
WSOL_MINT        = "So11111111111111111111111111111111111111112"

# Tier 1 (Anchor parse inline): sem rate limit — cada swap dispara _check_bounce
# Tier 2 (DexScreener fallback): rate limit de 2s — chamada HTTP externa
_DEX_RATE_LIMIT_SEC = 2.0

# SOL/USD cache local — atualizado pelo thread DexScreener, nunca bloqueia o WS callback
_sol_usd_cached: float = 0.0
_TOKEN_DECIMALS = 6        # Pump.fun SPL tokens (6 casas decimais)
_SOL_DECIMALS   = 9        # SOL/WSOL em lamports
_MC_MIN         = 1_000    # $1K — abaixo disso é ruído
_MC_MAX         = 10_000_000  # $10M — acima é suspeito para micro-caps

# ── Constante produto k da pool pAMM pump.fun ────────────────────────────────
_K_ABSOLUTE_MIN = 5e23
_K_ABSOLUTE_MAX = 5e26
_K_INCREASE_TOLERANCE = 0.02
_K_RATIO_MAX    = 100.0
_K_INIT_EXPECTED  = 6.265e25
_K_INIT_TOLERANCE = 0.80
# k_init_min = 6.265e25 × 0.20 = 1.253e25 — cobre pools com ~130 trades de fee acumulados.
# Secondary pool com 15 SOL × 793M tokens: k = 1.19e25 < 1.253e25 → bloqueado.


# ─────────────────────────────────────────────────────────────────────────────
# PDA derivation — pool primária pump.fun pAMM
# ─────────────────────────────────────────────────────────────────────────────

# Constantes da curva ed25519 — usadas para verificar se um ponto está on-curve.
# Pré-calculadas para evitar recalcular a cada chamada.
_ED25519_P = 2**255 - 19
_ED25519_D = (-121665 * pow(121666, _ED25519_P - 2, _ED25519_P)) % _ED25519_P


def _is_on_curve_ed25519(point: bytes) -> bool:
    """
    Retorna True se os 32 bytes representam um ponto válido na curva ed25519.
    Implementação via aritmética modular pura — sem dependência de cryptography.
    Equação: -x² + y² = 1 + d·x²·y² (mod p)
    Verificamos se x² = (y²-1)/(d·y²+1) tem raiz quadrada mod p.
    """
    try:
        p  = _ED25519_P
        d  = _ED25519_D
        y  = int.from_bytes(point, "little") & ~(1 << 255)
        y2 = (y * y) % p
        u  = (y2 - 1) % p
        v  = (d * y2 + 1) % p
        x2 = (u * pow(v, p - 2, p)) % p   # x² = u/v mod p (inverso de Fermat)
        if x2 == 0:
            return True   # ponto (0, y) está na curva
        # Verifica se x² tem raiz quadrada mod p (Euler criterion)
        return pow(x2, (p - 1) // 2, p) == 1
    except Exception:
        return False


def _derive_pool_pda(base_mint: str) -> str | None:
    """
    Deriva o endereço PDA da pool primária pump.fun pAMM para um mint.
    Seeds: [b"pool", base_mint_bytes, wsol_bytes, bump_byte]
    Retorna o endereço Base58 do PDA, ou None em caso de erro.
    """
    try:
        mint_b = _base58.b58decode(base_mint)
        wsol_b = _base58.b58decode(WSOL_MINT)
        prog_b = _base58.b58decode(PUMP_AMM_PROGRAM)
        for bump in range(255, -1, -1):
            data = b"pool" + mint_b + wsol_b + bytes([bump]) + prog_b + b"ProgramDerivedAddress"
            candidate = hashlib.sha256(data).digest()
            if not _is_on_curve_ed25519(candidate):
                return _base58.b58encode(candidate).decode()
    except Exception as e:
        logger.debug("_derive_pool_pda %s: %s", base_mint[:8], e)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers para parse de transação Solana (modo erpc)
# ─────────────────────────────────────────────────────────────────────────────

def _compact_u16(data: bytes, offset: int) -> tuple[int, int]:
    """Decodifica Solana compact-u16. Retorna (valor, bytes_consumidos)."""
    val = 0
    shift = 0
    consumed = 0
    for _ in range(3):  # máx 3 bytes para compact-u16
        if offset + consumed >= len(data):
            break
        b = data[offset + consumed]
        consumed += 1
        val |= (b & 0x7f) << shift
        shift += 7
        if not (b & 0x80):
            break
    return val, consumed


def _parse_account_keys_from_tx(tx_b64: str) -> list[str]:
    """
    Extrai as chaves públicas (account keys) de uma transação Solana em base64.

    Formato do wire (retornado pelo erpc com encoding='base64'):
      [compact-u16 num_sigs] [64*num_sigs bytes de assinaturas] [mensagem]

    Mensagem (legacy):
      [3 bytes header] [compact-u16 num_accounts] [32*num_accounts bytes pubkeys] ...

    Mensagem (versioned v0):
      [0x80 byte de versão] [3 bytes header] [compact-u16 num_accounts] ...

    Retorna lista de pubkeys em base58. Retorna [] em caso de erro.
    """
    try:
        raw = base64.b64decode(tx_b64)
    except Exception:
        return []

    if len(raw) < 70:  # mínimo plausível para uma tx com 1 sig
        return []

    offset = 0

    # 1. Número de assinaturas (compact-u16)
    num_sigs, consumed = _compact_u16(raw, offset)
    offset += consumed

    # 2. Pula bytes de assinatura (64 bytes cada)
    offset += 64 * num_sigs

    if offset >= len(raw):
        return []

    # 3. Detecta versioned transaction (bit mais significativo do primeiro byte da mensagem)
    if raw[offset] & 0x80:
        offset += 1  # pula byte de versão (ex: 0x80 = versão 0)

    # 4. Pula header da mensagem (3 bytes fixos)
    offset += 3

    if offset >= len(raw):
        return []

    # 5. Número de accounts (compact-u16)
    num_accounts, consumed = _compact_u16(raw, offset)
    offset += consumed

    # 6. Extrai pubkeys (32 bytes cada)
    keys: list[str] = []
    for _ in range(num_accounts):
        if offset + 32 > len(raw):
            break
        keys.append(_base58.b58encode(raw[offset:offset + 32]).decode("ascii"))
        offset += 32

    return keys


# ─────────────────────────────────────────────────────────────────────────────
# Parse Anchor — discriminadores e parsers por programa
# ─────────────────────────────────────────────────────────────────────────────

def _anchor_disc(event_name: str) -> bytes:
    """Discriminador Anchor: sha256('event:<name>')[:8]."""
    return hashlib.sha256(f"event:{event_name}".encode()).digest()[:8]


# pump.fun pAMM — confirmado via IDL pump-fun/pump-public-docs
_BUY_DISC  = _anchor_disc("BuyEvent")
_SELL_DISC = _anchor_disc("SellEvent")
_PUMP_AMM_DISCS: set[bytes] = {_BUY_DISC, _SELL_DISC}

# Meteora DAMM v2 — candidatos
_METEORA_DISCS: set[bytes] = {
    _anchor_disc("Swap"),
    _anchor_disc("SwapEvent"),
    _anchor_disc("CpAmmSwapEvent"),
    _anchor_disc("EvtSwap2"),
}

_SWAP_ONLY_DISCS: set[bytes] = _PUMP_AMM_DISCS | _METEORA_DISCS


def _parse_pump_amm_event(raw: bytes, sol_usd: float, supply: float) -> dict | None:
    """
    Parse pump.fun pAMM BuyEvent ou SellEvent.
    Retorna {mc, side, sol_amount, pool_quote_sol, wallet, k_raw, pool_base, pool_quote} ou None.

    Layout Borsh (a partir do início, incluindo 8 bytes de disc):
      offset  0: disc                          8 bytes
      offset  8: timestamp                     i64
      offset 16: base_amount_out/in            u64 (tokens)
      offset 24: max_quote_amount_in/min_...   u64 (limite)
      offset 32: user_base_token_reserves      u64
      offset 40: user_quote_token_reserves     u64
      offset 48: pool_base_token_reserves      u64 ← reservas de token no pool
      offset 56: pool_quote_token_reserves     u64 ← reservas de SOL no pool
      offset 64: quote_amount_in/out           u64 (SOL transacionado)
      offset 152: user                         Pubkey 32 bytes ← wallet do trader
    """
    if len(raw) < 72:
        return None

    _PUMP_SUPPLY = 1_000_000_000

    disc         = raw[:8]
    base_amount  = struct.unpack_from("<Q", raw, 16)[0]
    pool_base    = struct.unpack_from("<Q", raw, 48)[0]
    pool_quote   = struct.unpack_from("<Q", raw, 56)[0]
    quote_amount = struct.unpack_from("<Q", raw, 64)[0]

    side       = "buy" if disc == _BUY_DISC else "sell"
    sol_amount = quote_amount / 10 ** _SOL_DECIMALS

    mc = None
    if pool_base > 0 and pool_quote > 0:
        price_sol = (pool_quote / 10 ** _SOL_DECIMALS) / (pool_base / 10 ** _TOKEN_DECIMALS)
        mc_cand   = price_sol * sol_usd * _PUMP_SUPPLY
        if _MC_MIN < mc_cand < _MC_MAX:
            mc = mc_cand

    if mc is None and base_amount > 0 and quote_amount > 0:
        if side == "buy":
            tok_out = base_amount / 10 ** _TOKEN_DECIMALS
            if tok_out > 0:
                mc_cand = (sol_amount / tok_out) * sol_usd * _PUMP_SUPPLY
                if _MC_MIN < mc_cand < _MC_MAX:
                    mc = mc_cand
        else:
            tok_in = base_amount / 10 ** _TOKEN_DECIMALS
            if tok_in > 0:
                mc_cand = (sol_amount / tok_in) * sol_usd * _PUMP_SUPPLY
                if _MC_MIN < mc_cand < _MC_MAX:
                    mc = mc_cand

    if mc is None:
        return None

    pool_quote_sol = pool_quote / 10 ** _SOL_DECIMALS
    k_raw = pool_base * pool_quote

    wallet = None
    if len(raw) >= 184:
        try:
            wallet = _base58.b58encode(raw[152:184]).decode("ascii")
        except Exception:
            pass

    return {"mc": mc, "side": side, "sol": sol_amount, "pool_quote_sol": pool_quote_sol,
            "wallet": wallet, "k_raw": k_raw, "pool_base": pool_base, "pool_quote": pool_quote}


def _parse_meteora_mc(raw: bytes, sol_usd: float, supply: float) -> float | None:
    """Parse Meteora DAMM v2 swap event — layout legado: disc(8)+pool(32)+in(8)+out(8)."""
    if len(raw) < 56:
        return None

    offset     = 8 + 32
    in_amount  = struct.unpack_from("<Q", raw, offset)[0]
    out_amount = struct.unpack_from("<Q", raw, offset + 8)[0]

    if in_amount == 0 or out_amount == 0:
        return None

    supply = supply or 1_000_000_000

    sol_in  = in_amount  / 10 ** _SOL_DECIMALS
    tok_out = out_amount / 10 ** _TOKEN_DECIMALS
    if tok_out > 0:
        mc1 = (sol_in * sol_usd / tok_out) * supply
        if _MC_MIN < mc1 < _MC_MAX:
            return mc1

    tok_in  = in_amount  / 10 ** _TOKEN_DECIMALS
    sol_out = out_amount / 10 ** _SOL_DECIMALS
    if tok_in > 0:
        mc2 = (sol_out * sol_usd / tok_in) * supply
        if _MC_MIN < mc2 < _MC_MAX:
            return mc2

    return None


def _parse_swap_event_from_logs(logs: list, supply: float, sol_usd: float) -> dict | None:
    """
    Percorre logMessages e retorna {mc, side, sol, ...} do primeiro evento Anchor reconhecido.
    Prioridade: pump.fun pAMM → Meteora DAMM v2 → None.

    CRÍTICO: rastreia o programa ativo via stack antes de parsear qualquer 'Program data:'.
    Sem isso, dados de outros programas na mesma transação são parseados como eventos do
    pAMM — gerando preços fictícios.
    """
    supply = supply or 1_000_000_000
    program_stack: list[str] = []

    for log in logs:
        if " invoke [" in log and log.startswith("Program "):
            parts = log.split()
            if len(parts) >= 3:
                program_stack.append(parts[1])
            continue

        if log.startswith("Program ") and (" success" in log or " failed" in log):
            if program_stack:
                program_stack.pop()
            continue

        if not log.startswith("Program data: "):
            continue

        current = program_stack[-1] if program_stack else None
        if current not in (PUMP_AMM_PROGRAM, METEORA_PROGRAM):
            logger.warning("HeliusWS: skip Program data de programa não-pAMM: %s (stack=%s)",
                           current, program_stack[-3:] if program_stack else [])
            continue

        b64 = log[len("Program data: "):]
        try:
            raw = base64.b64decode(b64)
        except Exception:
            continue

        if len(raw) < 8:
            continue

        disc = raw[:8]

        if current == PUMP_AMM_PROGRAM and disc in _PUMP_AMM_DISCS:
            event = _parse_pump_amm_event(raw, sol_usd, supply)
            if event:
                return event

        elif current == METEORA_PROGRAM and disc in _METEORA_DISCS:
            mc = _parse_meteora_mc(raw, sol_usd, supply)
            if mc:
                return {"mc": mc, "side": "unknown", "sol": 0.0}

    return None


# ─────────────────────────────────────────────────────────────────────────────
# HeliusWS
# ─────────────────────────────────────────────────────────────────────────────

class HeliusWS:
    """
    WebSocket manager de preço real-time.

    Modo ERPC (ERPC_API_KEY configurado):
      Uma subscrição global no pAMM. subscribe()/unsubscribe() apenas atualizam
      o dict local — sem mensagens WS. Sem subscription timing gap.

    Modo Helius (fallback, HELIUS_API_KEY configurado):
      Uma subscrição logsSubscribe por mint. Comportamento legado.
    """

    _ABSORPTION_MAXLEN     = 300
    _ABSORPTION_WINDOW_SEC = 90
    _MIN_POOL_SOL = float(os.getenv("MIN_POOL_SOL", "10"))

    def __init__(self, on_price: Callable[[str, float], None]):
        """on_price(mint, market_cap_usd) — compatível com HotMonitor.push_mc."""
        self._on_price = on_price

        # Estado compartilhado (ambos os modos)
        self._mint_supply:     dict[str, float] = {}
        self._last_fetch:      dict[str, float] = {}
        self._swap_events:     dict[str, deque] = {}
        self._pool_quote_last: dict[str, float] = {}
        self._k_established:   dict[str, float] = {}
        self._pool_pda:        dict[str, str]   = {}  # mint → PDA da pool primária

        # Helius mode only
        self._sub_id_mint: dict[int, str] = {}
        self._req_mint:    dict[int, str] = {}

        self._lock      = threading.Lock()
        self._ws_lock   = threading.Lock()
        self._ws        = None
        self._req_id    = 1
        self._running   = False
        self._connected = False
        self._mode      = "none"   # "erpc" | "helius" | "none"
        self._ws_url    = ""

    # ──────────────────────────────────────────────────────────────────
    # API pública
    # ──────────────────────────────────────────────────────────────────

    def start(self):
        if ERPC_API_KEY:
            self._mode   = "erpc"
            self._ws_url = _ERPC_WS_URL
            logger.info("HeliusWS: modo ERPC (global transactionSubscribe)")
        elif HELIUS_API_KEY:
            self._mode   = "helius"
            self._ws_url = _HELIUS_WS_URL
            logger.info("HeliusWS: modo Helius (logsSubscribe por mint)")
        else:
            logger.warning("HeliusWS: nem ERPC_API_KEY nem HELIUS_API_KEY — desativado")
            return

        self._running = True
        threading.Thread(target=self._init_sol_usd, daemon=True, name="helius-sol-init").start()
        threading.Thread(target=self._run_loop,     daemon=True, name="helius-ws").start()
        logger.info("HeliusWS iniciado (url=%s...)", self._ws_url[:55])

    def _init_sol_usd(self):
        global _sol_usd_cached
        try:
            from gmgn_client import get_sol_usd
            price = get_sol_usd()
            if price:
                _sol_usd_cached = price
                logger.debug("HeliusWS: SOL/USD inicializado = $%.2f", price)
        except Exception as e:
            logger.debug("HeliusWS: _init_sol_usd: %s", e)

    def subscribe(self, mint: str, total_supply: float):
        """Registra o mint para monitoramento. No modo erpc, nenhuma mensagem WS é enviada."""
        with self._lock:
            if mint in self._mint_supply:
                return
            self._mint_supply[mint]     = total_supply
            if mint not in self._swap_events:
                self._swap_events[mint] = deque(maxlen=self._ABSORPTION_MAXLEN)
            self._pool_quote_last[mint] = 0.0

        # Deriva PDA da pool primária em thread própria (não bloqueia o caller)
        def _derive_async():
            pda = _derive_pool_pda(mint)
            if pda:
                with self._lock:
                    self._pool_pda[mint] = pda
                logger.info("HeliusWS: PDA %s... → %s...", mint[:8], pda[:8])
            else:
                logger.warning("HeliusWS: falha ao derivar PDA para %s", mint[:8])
        threading.Thread(target=_derive_async, daemon=True, name=f"pda-{mint[:8]}").start()

        if self._mode == "helius" and self._connected:
            self._send_helius_subscribe(mint)

        logger.info("HeliusWS: subscribe %s... (modo=%s)", mint[:8], self._mode)

    def unsubscribe(self, mint: str):
        """Remove o mint do monitoramento. No modo erpc, nenhuma mensagem WS é enviada."""
        sub_id_to_remove = None
        with self._lock:
            self._mint_supply.pop(mint, None)
            self._last_fetch.pop(mint, None)
            self._swap_events.pop(mint, None)
            self._pool_quote_last.pop(mint, None)
            self._k_established.pop(mint, None)
            self._pool_pda.pop(mint, None)
            if self._mode == "helius":
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
                    self._ws_url,
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
        if self._mode == "erpc":
            self._send_erpc_global_subscribe()
        else:
            # Helius: resubscreve todos os mints ativos
            with self._lock:
                self._sub_id_mint.clear()
                self._req_mint.clear()
                active = list(self._mint_supply.keys())
            logger.info("HeliusWS conectado — resubscrevendo %d tokens", len(active))
            for mint in active:
                self._send_helius_subscribe(mint)

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
        except Exception:
            return

        # Confirmação de subscrição
        if isinstance(data.get("result"), int) and "id" in data:
            if self._mode == "helius":
                req_id = data["id"]
                sub_id = data["result"]
                with self._lock:
                    mint = self._req_mint.pop(req_id, None)
                    if mint:
                        self._sub_id_mint[sub_id] = mint
                logger.debug("HeliusWS: sub confirmada %s sub_id=%d", (mint or "?")[:8], sub_id)
            else:
                logger.info("HeliusWS erpc: transactionSubscribe confirmado id=%d", data["result"])
            return

        method = data.get("method")

        if method == "transactionNotification" and self._mode == "erpc":
            self._handle_transaction_notification(data)
        elif method == "logsNotification" and self._mode == "helius":
            self._handle_logs_notification(data)

    def _on_error(self, ws, error):
        logger.warning("HeliusWS erro: %s", error)

    def _on_close(self, ws, code, msg):
        self._connected = False
        logger.info("HeliusWS fechado (code=%s)", code)

    # ──────────────────────────────────────────────────────────────────
    # Modo ERPC — handler de transactionNotification
    # ──────────────────────────────────────────────────────────────────

    def _handle_transaction_notification(self, data: dict):
        """
        Processa uma transactionNotification do erpc.
        Identifica o mint monitorado cruzando os accountKeys da tx com _mint_supply.
        """
        params  = data.get("params", {})
        result  = params.get("result", {})
        value   = result.get("value", result)   # erpc envolve em "value"
        tx_data = value.get("transaction", {})
        meta    = tx_data.get("meta", {})

        # Rejeita transações com erro on-chain
        if meta.get("err"):
            return

        logs = meta.get("logMessages", [])
        if not logs:
            return

        # Filtro rápido: transação envolve o pAMM?
        if not any(PUMP_AMM_PROGRAM in log for log in logs):
            return

        # Extrai account keys da transação para identificar o mint
        tx_array = tx_data.get("transaction", [])
        if not tx_array:
            return
        accounts = _parse_account_keys_from_tx(tx_array[0])
        if not accounts:
            return

        # Identifica o mint monitorado cruzando accounts com _mint_supply.
        with self._lock:
            monitored = set(self._mint_supply.keys())

        mint = None
        for account in accounts:
            if account in monitored:
                mint = account
                break

        if not mint:
            return

        with self._lock:
            supply = self._mint_supply.get(mint) or 1_000_000_000

        self._tier1_process(mint, logs, supply)

    # ──────────────────────────────────────────────────────────────────
    # Modo Helius — handler de logsNotification
    # ──────────────────────────────────────────────────────────────────

    def _handle_logs_notification(self, data: dict):
        """Processa uma logsNotification do Helius (modo legado)."""
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

        with self._lock:
            supply = self._mint_supply.get(mint) or 1_000_000_000

        if not self._tier1_process(mint, logs, supply):
            self._tier2_dexscreener(mint, supply)

    # ──────────────────────────────────────────────────────────────────
    # Tier 1 — parse Anchor inline (ambos os modos)
    # ──────────────────────────────────────────────────────────────────

    def _tier1_process(self, mint: str, logs: list, supply: float) -> bool:
        """
        Parseia o evento Anchor, aplica k-gate e A.9, chama _on_price.
        Retorna True se um price update foi disparado.
        No modo erpc: sem Tier 2 (fallback não faz sentido para stream global).
        No modo helius: retorna False para acionar Tier 2 se necessário.
        """
        if _sol_usd_cached <= 0:
            return False

        try:
            event = _parse_swap_event_from_logs(logs, supply, _sol_usd_cached)
            if not event or event["mc"] <= 0:
                return False

            # A.9: filtro de thin pool — rejeita secondary pools
            pq_check = event.get("pool_quote_sol", 0)
            if pq_check < self._MIN_POOL_SOL:
                logger.debug("HeliusWS A.9 skip %s: pool_quote=%.1f SOL < %.1f",
                             mint[:8], pq_check, self._MIN_POOL_SOL)
                return False

            # ── k-consistency gate ────────────────────────────────────────────
            # k = pool_base × pool_quote é constante produto. Fees reduzem k
            # monotonicamente. k NUNCA pode aumentar na mesma pool.
            k_raw = event.get("k_raw", 0)
            if k_raw > 0:
                # Check 1: absolute range
                if not (_K_ABSOLUTE_MIN <= k_raw <= _K_ABSOLUTE_MAX):
                    logger.warning("HeliusWS k-gate ABSOLUTE skip %s: k=%.2e fora de [%.0e, %.0e]",
                                   mint[:8], k_raw, _K_ABSOLUTE_MIN, _K_ABSOLUTE_MAX)
                    return False

                with self._lock:
                    k_est = self._k_established.get(mint)

                if k_est is None:
                    # Primeiro evento: verifica se k está próximo do valor de migração
                    k_init_min = _K_INIT_EXPECTED * (1.0 - _K_INIT_TOLERANCE)
                    k_init_max = _K_INIT_EXPECTED * (1.0 + _K_INIT_TOLERANCE)
                    if not (k_init_min <= k_raw <= k_init_max):
                        logger.warning(
                            "HeliusWS k-gate INIT SKIP %s: k=%.3e fora de [%.2e, %.2e] "
                            "— possível secondary pool",
                            mint[:8], k_raw, k_init_min, k_init_max,
                        )
                        return False
                    with self._lock:
                        self._k_established[mint] = k_raw
                    logger.info("HeliusWS k-gate ESTABLISHED %s: k=%.3e", mint[:8], k_raw)
                else:
                    # Check 2 (direcional): k não pode aumentar
                    if k_raw > k_est * (1.0 + _K_INCREASE_TOLERANCE):
                        logger.warning(
                            "HeliusWS k-gate INCREASE REJECT %s: k=%.3e > k_est=%.3e "
                            "(secondary pool?)", mint[:8], k_raw, k_est,
                        )
                        return False
                    # Check 3 (safety net): queda de >100x indica k_established corrompido
                    if k_raw < k_est / _K_RATIO_MAX:
                        logger.warning(
                            "HeliusWS k-gate DROP REJECT %s: k=%.3e << k_est=%.3e (>%.0fx) "
                            "— resetando", mint[:8], k_raw, k_est, _K_RATIO_MAX,
                        )
                        with self._lock:
                            self._k_established.pop(mint, None)
                        return False
                    # k legítimo pode cair por fees — atualiza monotonicamente
                    if k_raw < k_est:
                        with self._lock:
                            if self._k_established.get(mint) == k_est:
                                self._k_established[mint] = k_raw

            # H22/H24/H25: acumula na rolling window
            if event["side"] != "unknown":
                with self._lock:
                    buf = self._swap_events.get(mint)
                    self._pool_quote_last[mint] = pq_check
                if buf is not None:
                    buf.append({
                        "ts":             time.time(),
                        "side":           event["side"],
                        "sol":            event["sol"],
                        "mc":             event["mc"],
                        "wallet":         event.get("wallet"),
                        "pool_quote_sol": pq_check,
                    })

            logger.info("HeliusWS price(anchor) %s: $%.0f MC [%s %.4f SOL pool=%.1f]",
                        mint[:8], event["mc"], event["side"], event["sol"], pq_check)
            self._on_price(mint, event["mc"])
            return True

        except Exception as e:
            logger.debug("HeliusWS tier1 %s: %s", mint[:8], e)
            return False

    # ──────────────────────────────────────────────────────────────────
    # Tier 2 — DexScreener fallback (modo Helius apenas)
    # ──────────────────────────────────────────────────────────────────

    def _tier2_dexscreener(self, mint: str, supply: float):
        """Fallback HTTP quando Tier 1 falha. Rate-limitado por mint."""
        now = time.time()
        with self._lock:
            if now - self._last_fetch.get(mint, 0) < _DEX_RATE_LIMIT_SEC:
                return
            self._last_fetch[mint] = now

        threading.Thread(
            target=self._dexscreener_fallback,
            args=(mint, supply),
            daemon=True,
            name=f"hdex-{mint[:6]}",
        ).start()

    def _dexscreener_fallback(self, mint: str, supply: float):
        global _sol_usd_cached
        from gmgn_client import get_sol_usd
        sol_usd = get_sol_usd()
        if sol_usd:
            _sol_usd_cached = sol_usd

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
    # Métodos de análise (H22/H23/H24/H25) — inalterados
    # ──────────────────────────────────────────────────────────────────

    def get_absorption_window(self, mint: str, window_sec: int = 60,
                              window_end_ts: float = None) -> dict:
        """
        H22/H23 — retorna métricas de fluxo de ordens para um mint na janela de window_sec.

        window_end_ts: timestamp Unix do FIM da janela. Default = time.time() (agora).
        Para capturar absorção do dump (não momentum), passar state["ts_min"].timestamp().
        """
        end    = window_end_ts if window_end_ts is not None else time.time()
        cutoff = end - window_sec
        with self._lock:
            events = list(self._swap_events.get(mint, []))

        recent = [e for e in events if cutoff <= e["ts"] <= end]
        buys   = [e for e in recent if e["side"] == "buy"]
        sells  = [e for e in recent if e["side"] == "sell"]

        buy_vol  = sum(e["sol"] for e in buys)
        sell_vol = sum(e["sol"] for e in sells)

        return {
            "buy_count":     len(buys),
            "sell_count":    len(sells),
            "buy_vol_sol":   buy_vol,
            "sell_vol_sol":  sell_vol,
            "largest_buy":   max((e["sol"] for e in buys),  default=0.0),
            "largest_sell":  max((e["sol"] for e in sells), default=0.0),
            "total_events":  len(recent),
            "window_sec":    window_sec,
            "window_end_ts": end,
        }

    def get_pool_quote(self, mint: str) -> float | None:
        """H25 — SOL no pool no momento do último swap detectado para este mint."""
        with self._lock:
            v = self._pool_quote_last.get(mint, 0)
        return v if v > 0 else None

    def get_dump_seller_concentration(
        self, mint: str, window_start_ts: float, window_end_ts: float
    ) -> dict:
        """H24 — concentração de sellers via HHI durante o dump."""
        with self._lock:
            events = list(self._swap_events.get(mint, []))

        sells = [
            e for e in events
            if e["side"] == "sell"
            and window_start_ts <= e["ts"] <= window_end_ts
            and e.get("wallet")
        ]

        if not sells:
            return {"seller_count": 0, "seller_hhi": None, "top_seller_frac": None}

        wallet_vol: dict[str, float] = {}
        for e in sells:
            w = e["wallet"]
            wallet_vol[w] = wallet_vol.get(w, 0) + e["sol"]

        total_vol = sum(wallet_vol.values())
        if total_vol == 0:
            return {"seller_count": len(wallet_vol), "seller_hhi": None, "top_seller_frac": None}

        shares = [v / total_vol for v in wallet_vol.values()]
        hhi = sum(s * s for s in shares)

        return {
            "seller_count":    len(wallet_vol),
            "seller_hhi":      round(hhi, 4),
            "top_seller_frac": round(max(shares), 4),
        }

    def get_min_mc_in_window(self, mint: str, from_ts: float, to_ts: float,
                             return_ts: bool = False):
        """
        Retorna o menor MC observado via Helius/erpc para um mint no intervalo [from_ts, to_ts].
        Filtra eventos com pool_quote_sol < MIN_POOL_SOL (A.9 — rejeita secondary pools).

        return_ts=False (padrão): retorna float | None
        return_ts=True: retorna (float, float) | (None, None) — (mc, ts_evento)
        """
        with self._lock:
            events = list(self._swap_events.get(mint, []))
        window = [
            e for e in events
            if from_ts <= e["ts"] <= to_ts
            and e["mc"] > 0
            and e.get("pool_quote_sol", 0) >= self._MIN_POOL_SOL
        ]
        if not window:
            return (None, None) if return_ts else None
        best = min(window, key=lambda e: e["mc"])
        return (best["mc"], best["ts"]) if return_ts else best["mc"]

    # ──────────────────────────────────────────────────────────────────
    # Helpers internos
    # ──────────────────────────────────────────────────────────────────

    def _send_erpc_global_subscribe(self):
        """Envia UMA subscrição global no pAMM (modo erpc)."""
        req_id = self._next_req_id()
        self._ws_send({
            "jsonrpc": "2.0",
            "id":      req_id,
            "method":  "transactionSubscribe",
            "params":  [
                {
                    "vote":     False,
                    "failed":   False,
                    "accounts": {
                        "include": [PUMP_AMM_PROGRAM],
                    },
                },
                {
                    "commitment":                    "processed",
                    "encoding":                      "base64",
                    "transactionDetails":            "full",
                    "showRewards":                   False,
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        })
        logger.info("HeliusWS erpc: transactionSubscribe enviado (req_id=%d)", req_id)

    def _send_helius_subscribe(self, mint: str):
        """Envia logsSubscribe para um mint (modo Helius)."""
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
