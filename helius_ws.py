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

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
_WS_URL        = f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"

# Program IDs relevantes para filtrar swaps
PUMP_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
METEORA_PROGRAM  = "cpamdpKoS7RUn9RDdHsS3SuRH5BVTTvovCHQnGFumm9"
RAYDIUM_PROGRAM  = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"

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
# Inicialização da migração: ~79 SOL (lamports) × ~793M tokens (unidades brutas)
# k = pool_quote_lamports × pool_base_token_units
# Valores em unidades brutas: lamports (×1e9 por SOL) × unidades token (×1e6 por token)
# Faixa válida derivada dos extremos da bonding curve:
#   Mínimo: 50 SOL × 100M tokens = 5×10^10 × 1×10^14 = 5×10^24 (após dump máximo)
#   Máximo: 100 SOL × 1B tokens  = 1×10^11 × 1×10^15 = 1×10^26 (fresh pool)
# Um evento de programa errado com bytes aleatórios produz k completamente diferente.
_K_ABSOLUTE_MIN = 5e23   # abaixo disso é impossível para uma pool pump.fun real
_K_ABSOLUTE_MAX = 5e26   # acima disso é fisicamente impossível
#
# INVARIANTE DIRECIONAL (mais importante que ratio):
#
#   k pode APENAS diminuir ao longo do tempo (fees extraem valor do pool a cada trade).
#   k = pool_base × pool_quote é aritmética de inteiros — sem erro de floating point.
#   Um evento com k > k_established é FISICAMENTE IMPOSSÍVEL na mesma pool.
#   Logo: k_raw > k_established × (1 + tolerância) → deve ser de pool diferente → rejeitar.
#
#   Tolerância de +2%: cobre o primeiro evento (k_established pode ser levemente baixo
#   se o primeiro evento já tinha alguma fee) e variação de arredondamento de inteiros u64.
#
_K_INCREASE_TOLERANCE = 0.02   # 2% — k não pode aumentar mais que isso (fees são monotônicos)
#
# Para o lado de baixo: k pode diminuir muito com muitos trades.
# Fee total por trade: 1% protocolo + 0.25% LP = 1.25%.
# Após 200 trades (heavy activity em 60min): k × (1-0.0125)^200 ≈ 0.08 × k_initial (12x menor).
# K_RATIO_MAX=5.0 estava muito apertado — rejeitaria eventos legítimos após ~128 trades.
# Solução: verificar apenas o lado de AUMENTO (duro, físico) + absoluto_min como piso.
# O K_RATIO_MAX abaixo é mantido apenas como "sanity check" para quedas extremas (>100x),
# que indicam que k_established foi capturado incorretamente (ex: na pool de transição).
_K_RATIO_MAX    = 100.0   # safety net only — >100x queda sugere k_established corrompido
#
# PROTEÇÃO NA INICIALIZAÇÃO:
#
#   pump.fun pAMM sempre migra com ~79 SOL × ~793M tokens → k_initial ≈ 6.265e25.
#   Um secondary pool com 15 SOL × 800M tokens teria k = 1.2e25 (5x abaixo).
#   Se o primeiro evento recebido é desse secondary pool, k_established = 1.2e25.
#   Subsequentes eventos da pool primária (k ≈ 6e25) são rejeitados pelo directional check!
#
#   Solução: restringir inicialização a k próximo do valor teórico de migração.
#   Tolerância ±50% cobre variação real de tokens pós-fees/burns (793M vs 1B) e deflação
#   rápida no dump inicial.
#
#   Qualquer primeiro evento com k fora desse range → provavelmente de pool diferente.
#   k_established só é setado quando k do primeiro evento está na faixa esperada.
#
_K_INIT_EXPECTED  = 6.265e25   # k teórico de migração: 79 SOL × 793M tokens (unidades brutas)
_K_INIT_TOLERANCE = 0.60       # ±60% — cobre supply variable (793M–1000M) e dump inicial


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


def _parse_pump_amm_event(raw: bytes, sol_usd: float, supply: float) -> dict | None:
    """
    Parse pump.fun pAMM BuyEvent ou SellEvent.
    Retorna {mc, side, sol_amount, pool_quote_sol, wallet} para uso em MC + absorção (H22/H24/H25), ou None.

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
      offset 72: lpFeeBasisPoints              u64  (ignorado)
      offset 80: lpFee                         u64  (ignorado)
      offset 88: protocolFeeBasisPoints        u64  (ignorado)
      offset 96: protocolFee                   u64  (ignorado)
      offset 104: quoteAmountInWithLpFee       u64  (ignorado)
      offset 112: userQuoteAmountIn            u64  (ignorado)
      offset 120: pool                         Pubkey 32 bytes (ignorado)
      offset 152: user                         Pubkey 32 bytes ← wallet do trader

    Fonte: pump-fun/pump-public-docs/idl/pump_amm.json
    """
    if len(raw) < 72:
        return None

    # pump.fun tokens têm SEMPRE 1B de supply — não usar o parâmetro supply
    # (GMGN às vezes retorna circulating_supply ou valor em unidades erradas,
    # gerando mc_min 4× abaixo do real e entry_expected completamente errado)
    _PUMP_SUPPLY = 1_000_000_000

    disc         = raw[:8]
    base_amount  = struct.unpack_from("<Q", raw, 16)[0]  # tokens
    pool_base    = struct.unpack_from("<Q", raw, 48)[0]  # token reserves do pool
    pool_quote   = struct.unpack_from("<Q", raw, 56)[0]  # SOL reserves do pool (lamports)
    quote_amount = struct.unpack_from("<Q", raw, 64)[0]  # SOL transacionado

    side       = "buy" if disc == _BUY_DISC else "sell"
    sol_amount = quote_amount / 10 ** _SOL_DECIMALS

    # MC via pool reserves (primary — mais preciso que swap amounts)
    mc = None
    if pool_base > 0 and pool_quote > 0:
        price_sol = (pool_quote / 10 ** _SOL_DECIMALS) / (pool_base / 10 ** _TOKEN_DECIMALS)
        mc_cand   = price_sol * sol_usd * _PUMP_SUPPLY
        if _MC_MIN < mc_cand < _MC_MAX:
            mc = mc_cand

    # Fallback: swap amounts
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

    # H25 — pool depth (SOL no pool no momento deste swap)
    pool_quote_sol = pool_quote / 10 ** _SOL_DECIMALS

    # Constante produto k em unidades brutas (lamports × token_units)
    # Retornada ao chamador para validação de consistência inter-eventos.
    k_raw = pool_base * pool_quote  # unidades: token_units × lamports

    # H24 — wallet do usuário (offset 152, 32 bytes pubkey)
    wallet = None
    if len(raw) >= 184:
        try:
            wallet = _base58.b58encode(raw[152:184]).decode("ascii")
        except Exception:
            pass

    return {"mc": mc, "side": side, "sol": sol_amount, "pool_quote_sol": pool_quote_sol,
            "wallet": wallet, "k_raw": k_raw, "pool_base": pool_base, "pool_quote": pool_quote}


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


def _parse_swap_event_from_logs(logs: list, supply: float, sol_usd: float) -> dict | None:
    """
    Percorre os logs e retorna {mc, side, sol} do primeiro evento Anchor reconhecido.
    Prioridade: pump.fun pAMM → Meteora DAMM v2 → None.

    CRÍTICO: rastreia qual programa está invocando via stack antes de parsear
    qualquer "Program data:". Sem isso, dados de outros programas na mesma
    transação são parseados como eventos do pAMM — gerando preços fictícios
    que nunca aparecem em nenhum gráfico (causa raiz dos mc_min falsos).
    """
    supply = supply or 1_000_000_000

    # Stack de programas invocando (Solana logs têm hierarquia de CPIs)
    # "Program X invoke [N]" empilha X; "Program X success/failed" desempilha
    program_stack: list[str] = []

    for log in logs:
        # Rastreia programa atual
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

        # Só parseia se o programa atual é o pAMM ou Meteora
        current = program_stack[-1] if program_stack else None
        if current not in (PUMP_AMM_PROGRAM, METEORA_PROGRAM):
            # WARNING visível nos logs Railway — confirma que o fix está bloqueando
            # eventos de programas errados. Se aparecer nos logs, o fix é necessário.
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
    """WebSocket manager — subscrição por mint, sem depender de pool address."""

    # Janela de eventos por mint — maxlen impede crescimento ilimitado.
    # Eventos mais velhos que _ABSORPTION_WINDOW_SEC são ignorados na consulta.
    _ABSORPTION_MAXLEN     = 300
    _ABSORPTION_WINDOW_SEC = 90   # janela padrão para get_absorption_window()

    def __init__(self, on_price: Callable[[str, float], None]):
        """on_price(mint, market_cap_usd) — compatível com HotMonitor.push_mc."""
        self._on_price = on_price

        self._mint_supply:    dict[str, float] = {}   # mint → total_supply
        self._sub_id_mint:    dict[int, str]   = {}   # ws_sub_id → mint
        self._req_mint:       dict[int, str]   = {}   # req_id → mint (aguardando confirmação)
        self._last_fetch:     dict[str, float] = {}   # mint → timestamp último parse
        # H22 — rolling window de swap events por mint (side, sol, mc, ts, wallet)
        self._swap_events:    dict[str, deque] = {}
        # H25 — pool_quote (SOL) do último swap por mint
        self._pool_quote_last: dict[str, float] = {}

        # k-consistency gate — constante produto da pool pAMM por mint
        # Estabelecida no primeiro evento válido; usada para rejeitar eventos
        # de secondary pools ou programas errados (k difere por ordens de magnitude).
        self._k_established: dict[str, float] = {}

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
        # Inicializa _sol_usd_cached para o path inline estar pronto imediatamente
        threading.Thread(target=self._init_sol_usd, daemon=True, name="helius-sol-init").start()
        threading.Thread(target=self._run_loop, daemon=True, name="helius-ws").start()
        logger.info("HeliusWS iniciado (url=%s...)", _WS_URL[:40])

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
        """Subscreve ao mint no WebSocket — sem precisar do pool address."""
        with self._lock:
            if mint in self._mint_supply:
                return
            self._mint_supply[mint] = total_supply
            self._swap_events[mint] = deque(maxlen=self._ABSORPTION_MAXLEN)
            self._pool_quote_last[mint] = 0.0

        if self._connected:
            self._send_subscribe(mint)
        # Se não conectado ainda, _on_open resubscreve tudo de _mint_supply

        logger.info("HeliusWS: subscribe mint=%s...", mint[:8])

    def unsubscribe(self, mint: str):
        sub_id_to_remove = None
        with self._lock:
            self._mint_supply.pop(mint, None)
            self._last_fetch.pop(mint, None)
            self._swap_events.pop(mint, None)
            self._pool_quote_last.pop(mint, None)
            self._k_established.pop(mint, None)
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

        with self._lock:
            supply = self._mint_supply.get(mint) or 1_000_000_000

        # ── Tier 1: INLINE, sem rate limit, sem thread ─────────────────
        # Cada swap dispara _check_bounce imediatamente (~5ms no callback WS).
        # Elimina a janela cega de 2s do rate limit antigo.
        # Também acumula evento para janela de absorção H22.
        if _sol_usd_cached > 0:
            try:
                event = _parse_swap_event_from_logs(logs, supply, _sol_usd_cached)
                if event and event["mc"] > 0:
                    # A.9: filtro de thin pool ANTES de qualquer append — impede contaminação
                    # de _swap_events (H22/H23) e _pool_quote_last (H25) com eventos de
                    # secondary pools. pq_check == 0 também é rejeitado (parse incompleto).
                    pq_check = event.get("pool_quote_sol", 0)
                    if pq_check < self._MIN_POOL_SOL:
                        logger.debug("HeliusWS A.9 skip %s: pool_quote=%.1f SOL < %.1f",
                                     mint[:8], pq_check, self._MIN_POOL_SOL)
                        return

                    # ── k-consistency gate ────────────────────────────────────────────
                    # INVARIANTE FÍSICA: k = pool_base × pool_quote é constante produto.
                    # Fees reduzem k monotonicamente a cada trade (≤1.25%/trade).
                    # k NUNCA pode aumentar na mesma pool — qualquer aumento indica pool diferente.
                    #
                    # Dois checks em ordem crescente de custo:
                    #   1. Absolute range: k fora de [5e23, 5e26] → bytes inválidos ou pool impossível
                    #   2. Direcional: k_raw > k_established × 1.02 → FISICAMENTE impossível na mesma pool
                    #   3. Safety net: k_raw < k_established / 100 → k_established foi corrompido
                    #
                    # O check direcional (2) é a inovação central:
                    #   - Rejeita secondary pools quando k salta para cima (diferente de k_established)
                    #   - Permite queda legítima por fees sem limite fixo (>128 trades não é rejeitado)
                    #   - Zero falsos positivos: k nunca aumenta na mesma pool (aritmética de inteiros u64)
                    k_raw = event.get("k_raw", 0)
                    if k_raw > 0:
                        # Check 1: absolute range
                        if not (_K_ABSOLUTE_MIN <= k_raw <= _K_ABSOLUTE_MAX):
                            logger.warning("HeliusWS k-gate ABSOLUTE skip %s: k=%.2e fora de [%.0e, %.0e]",
                                           mint[:8], k_raw, _K_ABSOLUTE_MIN, _K_ABSOLUTE_MAX)
                            return
                        with self._lock:
                            k_est = self._k_established.get(mint)
                        if k_est is None:
                            # Primeiro evento válido para este mint: estabelece k de referência.
                            # Guard de inicialização: k do primeiro evento deve estar próximo
                            # do k teórico de migração (±60%). Protege contra secondary pools
                            # com 10-79 SOL que passam o A.9 filter mas têm k muito menor
                            # que a pool primária — se inicializássemos com esse k, eventos
                            # subsequentes da pool primária (k maior) seriam rejeitados.
                            k_init_min = _K_INIT_EXPECTED * (1.0 - _K_INIT_TOLERANCE)
                            k_init_max = _K_INIT_EXPECTED * (1.0 + _K_INIT_TOLERANCE)
                            if not (k_init_min <= k_raw <= k_init_max):
                                logger.warning(
                                    "HeliusWS k-gate INIT SKIP %s: k=%.3e fora do range de inicialização "
                                    "[%.2e, %.2e] — possível secondary pool; aguardando evento da pool primária",
                                    mint[:8], k_raw, k_init_min, k_init_max,
                                )
                                # NÃO rejeita o evento — pode ser um evento legítimo após heavy trading.
                                # Apenas não inicializa k_established com um valor suspeito.
                                # O evento passa sem gate (primeira detecção melhor que nenhuma).
                                # k_established ficará None até um evento com k na faixa esperada.
                            else:
                                with self._lock:
                                    self._k_established[mint] = k_raw
                                logger.info("HeliusWS k-gate ESTABLISHED %s: k=%.3e (pool_base=%d pool_quote=%d)",
                                            mint[:8], k_raw,
                                            event.get("pool_base", 0), event.get("pool_quote", 0))
                        else:
                            # Check 2 (direcional — o mais importante):
                            # k_raw > k_established × (1 + tolerância) é FISICAMENTE impossível.
                            # Fees só reduzem k. Aumento significa pool diferente → rejeitar.
                            if k_raw > k_est * (1.0 + _K_INCREASE_TOLERANCE):
                                logger.warning(
                                    "HeliusWS k-gate INCREASE REJECT %s: k=%.3e > k_est=%.3e × %.2f "
                                    "(aumento é impossível na mesma pool — secondary pool?) "
                                    "(pool_base=%d pool_quote=%d pool_quote_sol=%.1f)",
                                    mint[:8], k_raw, k_est, 1.0 + _K_INCREASE_TOLERANCE,
                                    event.get("pool_base", 0), event.get("pool_quote", 0), pq_check,
                                )
                                return
                            # Check 3 (safety net): queda de >100x sugere k_established corrompido
                            if k_raw < k_est / _K_RATIO_MAX:
                                logger.warning(
                                    "HeliusWS k-gate DROP REJECT %s: k=%.3e << k_est=%.3e (ratio>%.0fx) "
                                    "— k_established pode estar corrompido; resetando "
                                    "(pool_base=%d pool_quote=%d pool_quote_sol=%.1f)",
                                    mint[:8], k_raw, k_est, _K_RATIO_MAX,
                                    event.get("pool_base", 0), event.get("pool_quote", 0), pq_check,
                                )
                                # Reset k_established: assume que o evento atual é o novo baseline
                                # (possível se o bot reiniciou e o pool já teve muita atividade).
                                with self._lock:
                                    self._k_established[mint] = k_raw
                                # NÃO rejeita — apenas reseta o baseline e continua processando.
                                # Um reset não implica que o evento é inválido.
                            # k pode diminuir por fees — atualiza monotonicamente para baixo
                            if k_raw < k_est:
                                with self._lock:
                                    if self._k_established.get(mint) == k_est:
                                        self._k_established[mint] = k_raw

                    # H22/H24/H25: acumula na rolling window — garantido pool primária (≥ _MIN_POOL_SOL)
                    if event["side"] != "unknown":
                        with self._lock:
                            buf = self._swap_events.get(mint)
                            self._pool_quote_last[mint] = pq_check  # H25: sempre da pool primária
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
                    return
            except Exception as e:
                logger.debug("HeliusWS anchor parse %s: %s", mint[:8], e)

        # ── Tier 2: DexScreener fallback — rate-limitado (HTTP externo) ─
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

    def _on_error(self, ws, error):
        logger.warning("HeliusWS erro: %s", error)

    def _on_close(self, ws, code, msg):
        self._connected = False
        logger.info("HeliusWS fechado (code=%s)", code)

    # ──────────────────────────────────────────────────────────────────
    # Price resolution — Anchor logs → DexScreener fallback
    # ──────────────────────────────────────────────────────────────────

    def get_absorption_window(self, mint: str, window_sec: int = 60,
                              window_end_ts: float = None) -> dict:
        """
        H22/H23 — retorna métricas de fluxo de ordens para um mint na janela de window_sec.

        window_end_ts: timestamp Unix (float) do FIM da janela. Default = time.time() (agora).
        Para capturar absorção REAL do dump (não momentum pós-bounce), passar
        state["ts_min"].timestamp() — janela fica [ts_min - window_sec, ts_min].
        Sem esse parâmetro, a janela captura o bounce subindo, não o fundo sendo absorvido.
        """
        end   = window_end_ts if window_end_ts is not None else time.time()
        cutoff = end - window_sec
        with self._lock:
            events = list(self._swap_events.get(mint, []))

        recent = [e for e in events if cutoff <= e["ts"] <= end]
        buys   = [e for e in recent if e["side"] == "buy"]
        sells  = [e for e in recent if e["side"] == "sell"]

        buy_vol  = sum(e["sol"] for e in buys)
        sell_vol = sum(e["sol"] for e in sells)

        return {
            "buy_count":      len(buys),
            "sell_count":     len(sells),
            "buy_vol_sol":    buy_vol,
            "sell_vol_sol":   sell_vol,
            "largest_buy":    max((e["sol"] for e in buys),  default=0.0),
            "largest_sell":   max((e["sol"] for e in sells), default=0.0),
            "total_events":   len(recent),
            "window_sec":     window_sec,
            "window_end_ts":  end,
        }

    def get_pool_quote(self, mint: str) -> float | None:
        """H25 — SOL no pool no momento do último swap detectado para este mint."""
        with self._lock:
            v = self._pool_quote_last.get(mint, 0)
        return v if v > 0 else None

    def get_dump_seller_concentration(
        self, mint: str, window_start_ts: float, window_end_ts: float
    ) -> dict:
        """H24 — concentração de sellers durante o dump [window_start_ts → window_end_ts].

        Calcula HHI (Herfindahl-Hirschman Index) por volume vendido por wallet única.
        HHI=1.0: um único seller vendeu tudo (dump finito e previsível).
        HHI→0: muitos sellers (pressão de venda estrutural, sem exaustão).

        Retorna: seller_count, seller_hhi, top_seller_frac.
        """
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

    # Profundidade mínima da pool primária do pump.fun pAMM (SOL).
    # Derivado da matemática da bonding curve: com k = 79 SOL × 200M tokens e todos
    # os 1B tokens possíveis vendidos, o mínimo absoluto da pool primária é ~15.8 SOL.
    # Com nosso filtro MC ≥ $5k, o mínimo real é ~21.5 SOL.
    # Eventos Helius com pool_quote_sol < 10 SOL são de secondary pools (MEV, thin pools)
    # não visíveis nos gráficos — a fake pool do evento espúrio tinha 0.83 SOL.
    _MIN_POOL_SOL = float(os.getenv("MIN_POOL_SOL", "10"))

    def get_min_mc_in_window(self, mint: str, from_ts: float, to_ts: float) -> float | None:
        """Retorna o menor MC observado via Helius para um mint no intervalo [from_ts, to_ts].
        Usado para substituir kline low na janela de transição (elapsed < 90s), onde o low
        intra-candle do kline pode incluir preços da bonding curve pré-migração.
        Filtra eventos com pool_quote_sol < MIN_POOL_SOL para rejeitar secondary pools e
        thin pools que produzem preços fictícios não visíveis nos gráficos (A.9).
        Retorna None se não há eventos Helius válidos nessa janela.
        """
        with self._lock:
            events = list(self._swap_events.get(mint, []))
        window = [
            e["mc"] for e in events
            if from_ts <= e["ts"] <= to_ts
            and e["mc"] > 0
            and e.get("pool_quote_sol", 0) >= self._MIN_POOL_SOL  # A.9: rejeita secondary pools
        ]
        return min(window) if window else None

    def _dexscreener_fallback(self, mint: str, supply: float):
        """
        Tier 2 — DexScreener fallback quando Anchor parse falha (token não é pAMM).
        Também atualiza _sol_usd_cached para o path inline do Tier 1.
        """
        global _sol_usd_cached
        from gmgn_client import get_sol_usd

        sol_usd = get_sol_usd()
        if sol_usd:
            _sol_usd_cached = sol_usd  # alimenta o path inline sem bloqueio

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
