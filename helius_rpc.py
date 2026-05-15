"""
helius_rpc.py — Acesso à blockchain Solana via Helius RPC.

Dois usos principais:
  1. get_swap_fill(): lê o fill real de um swap diretamente da transação on-chain.
     Elimina dependência de GMGN retornar filled_output_amount (que não retorna).
  2. get_token_balance(): verifica se a carteira ainda tem posição num token.
     Detecta quando GMGN executa TP/SL sem precisar de token_ticks.

HELIUS_RPC_URL deve ser configurado no Railway (ex: https://mainnet.helius-rpc.com/?api-key=xxx).
Sem ele, todas as funções retornam None/0 sem erro — degradação graciosa.
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)

WALLET_ADDRESS = os.getenv("GMGN_WALLET_ADDRESS", "")
_TIMEOUT       = 10  # segundos por request RPC

def _build_rpc_url() -> str:
    """Constrói URL HTTP do Helius RPC.
    Aceita HELIUS_RPC_URL (https:// ou wss://) ou HELIUS_API_KEY isolado.
    wss:// é convertido automaticamente para https://.
    """
    url = os.getenv("HELIUS_RPC_URL", "")
    if url.startswith("wss://"):
        url = url.replace("wss://", "https://", 1)
    if url.startswith("https://"):
        return url
    # Fallback: constrói a partir da API key
    key = os.getenv("HELIUS_API_KEY", "")
    if key:
        return f"https://mainnet.helius-rpc.com/?api-key={key}"
    return ""

HELIUS_RPC_URL = _build_rpc_url()


def available() -> bool:
    """True se Helius RPC HTTP está configurado."""
    return bool(HELIUS_RPC_URL)


def _rpc(method: str, params: list):
    """Chama um método JSON-RPC no Helius. Retorna result ou None em erro."""
    if not HELIUS_RPC_URL:
        return None
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        r = requests.post(HELIUS_RPC_URL, json=body, timeout=_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            logger.warning("Helius RPC [%s] error: %s", method, data["error"])
            return None
        return data.get("result")
    except requests.Timeout:
        logger.warning("Helius RPC [%s] timeout (%ds)", method, _TIMEOUT)
        return None
    except Exception as e:
        logger.debug("Helius RPC [%s]: %s", method, e)
        return None


def get_swap_fill(tx_signature: str, token_mint: str, wallet: str = None) -> dict | None:
    """Lê fill real de um swap diretamente da blockchain via getTransaction.

    Analisa os balanços pré/pós da transação para determinar exatamente
    quanto SOL foi gasto e quantos tokens foram recebidos na carteira.

    Args:
        tx_signature: assinatura da transação Solana (base58, ~88 chars)
        token_mint:   endereço do token comprado
        wallet:       carteira do trader (default: GMGN_WALLET_ADDRESS)

    Returns:
        {
          "tokens_received": float,  # tokens em unidades display (÷ 10^decimals)
          "sol_spent":       float,  # SOL gasto (inclui fee)
          "fee_sol":         float,  # fee da tx em SOL
          "block_time":      int,    # unix timestamp do bloco (para medir latência)
          "decimals":        int,    # decimals do token (para validação)
        }
        None se tx não encontrada, não confirmada, ou não parseável.
    """
    wallet = wallet or WALLET_ADDRESS
    if not wallet or not available():
        return None

    result = _rpc("getTransaction", [
        tx_signature,
        {
            "encoding":                       "jsonParsed",
            "commitment":                     "confirmed",
            "maxSupportedTransactionVersion": 0,
        },
    ])
    if not result:
        logger.debug("get_swap_fill %s: transação não encontrada", tx_signature[:16])
        return None

    meta = result.get("meta") or {}
    tx   = result.get("transaction") or {}
    msg  = tx.get("message") or {}

    # accountKeys pode ser lista de strings ou lista de dicts (jsonParsed)
    raw_keys     = msg.get("accountKeys") or []
    account_keys = [
        (k.get("pubkey") if isinstance(k, dict) else str(k))
        for k in raw_keys
    ]

    pre_balances  = meta.get("preBalances")  or []
    post_balances = meta.get("postBalances") or []
    fee_lamports  = meta.get("fee") or 0
    fee_sol       = fee_lamports / 1e9

    # --- SOL gasto: redução no saldo SOL da nossa carteira ---
    # Inclui o valor da compra + fee da tx. Se delta negativo (recebemos SOL), ignorar.
    wallet_idx = next((i for i, k in enumerate(account_keys) if k == wallet), None)
    sol_spent  = 0.0
    if wallet_idx is not None and wallet_idx < len(pre_balances) and wallet_idx < len(post_balances):
        delta_lamports = pre_balances[wallet_idx] - post_balances[wallet_idx]
        sol_spent = max(0.0, delta_lamports / 1e9)

    # --- Tokens recebidos: aumento no saldo do token na nossa carteira ---
    # jsonParsed retorna uiTokenAmount com amount (raw int) e uiAmount (display float)
    # Usamos uiAmount (display) para consistência com o restante do bot.
    pre_tok  = {t["accountIndex"]: t for t in (meta.get("preTokenBalances")  or [])}
    post_tok = {t["accountIndex"]: t for t in (meta.get("postTokenBalances") or [])}

    tokens_received = 0.0
    for idx, post in post_tok.items():
        if post.get("mint") != token_mint:
            continue
        owner = post.get("owner") or ""
        if owner != wallet:
            continue
        pre_ui  = float((pre_tok.get(idx) or {}).get("uiTokenAmount", {}).get("uiAmount") or 0)
        post_ui = float(post.get("uiTokenAmount", {}).get("uiAmount") or 0)
        delta   = post_ui - pre_ui
        if delta > 0:
            tokens_received = delta
            break

    if tokens_received <= 0:
        # Fallback: tenta raw amount / 10^6 (pump.fun decimals=6)
        for idx, post in post_tok.items():
            if post.get("mint") != token_mint:
                continue
            owner = post.get("owner") or ""
            if owner != wallet:
                continue
            pre_raw  = int((pre_tok.get(idx) or {}).get("uiTokenAmount", {}).get("amount") or 0)
            post_raw = int(post.get("uiTokenAmount", {}).get("amount") or 0)
            delta_raw = post_raw - pre_raw
            if delta_raw > 0:
                tokens_received = delta_raw / 1_000_000  # pump.fun = 6 decimals
                break

    if tokens_received <= 0:
        # Log de warning com contexto para diagnóstico — owners reais e mints na tx
        owners_for_mint = [
            post_tok[idx].get("owner") for idx in post_tok
            if post_tok[idx].get("mint") == token_mint
        ]
        logger.warning(
            "get_swap_fill %s: tokens_received=0 para mint=%s wallet=%s "
            "owners_encontrados=%s total_post_tok=%d",
            tx_signature[:16], token_mint[:16], wallet[:16] if wallet else "?",
            owners_for_mint[:3], len(post_tok),
        )
        return None

    decimals_found = None
    for idx in post_tok:
        if post_tok[idx].get("mint") == token_mint and post_tok[idx].get("owner") == wallet:
            decimals_found = (post_tok[idx].get("uiTokenAmount") or {}).get("decimals")
            break

    block_time = result.get("blockTime")

    logger.info(
        "get_swap_fill %s: %.2f tokens (dec=%s) | %.6f SOL gasto | fee=%.6f SOL | block_time=%s",
        tx_signature[:16], tokens_received, decimals_found, sol_spent, fee_sol, block_time,
    )
    return {
        "tokens_received": tokens_received,
        "sol_spent":       sol_spent,
        "fee_sol":         fee_sol,
        "block_time":      block_time,
        "decimals":        decimals_found,
    }


def get_token_balance(token_mint: str, wallet: str = None) -> float:
    """Retorna saldo atual do token na carteira em unidades display.

    Retorna 0.0 se:
      - carteira não tem ATA para esse mint (posição fechada ou nunca aberta)
      - saldo = 0 (posição fechada pelo GMGN)
      - RPC indisponível

    Usado pelo TradeMonitor para detectar quando GMGN executou TP/SL:
    se antes tínhamos tokens e agora balance=0, a posição foi fechada.
    """
    wallet = wallet or WALLET_ADDRESS
    if not wallet or not available():
        return 0.0

    result = _rpc("getTokenAccountsByOwner", [
        wallet,
        {"mint": token_mint},
        {"encoding": "jsonParsed"},
    ])
    if not result:
        return 0.0

    total = 0.0
    for acc in (result.get("value") or []):
        parsed = acc.get("account", {}).get("data", {}).get("parsed", {})
        amt    = parsed.get("info", {}).get("tokenAmount", {})
        # uiAmount é null quando saldo=0 — usar 0 como fallback
        total += float(amt.get("uiAmount") or 0)
    return total


def extract_tx_signature(query_order_response: dict) -> str | None:
    """Tenta extrair a assinatura da tx Solana de uma resposta query_order do GMGN.

    GMGN não documenta onde coloca o tx hash, então tentamos múltiplos campos em ordem
    de prioridade e, como fallback, fazemos scan regex em toda a resposta JSON.
    """
    import re
    import json

    if not query_order_response:
        return None
    data = query_order_response.get("data") or query_order_response
    conf = data.get("confirmation") or {}

    # Campos nomeados — ordem de prioridade
    candidates = [
        ("confirmation.signature",    conf.get("signature")),
        ("confirmation.tx_hash",      conf.get("tx_hash")),
        ("confirmation.hash",         conf.get("hash")),
        ("confirmation.tx_signature", conf.get("tx_signature")),
        ("data.signature",            data.get("signature")),
        ("data.tx_hash",              data.get("tx_hash")),
        ("data.tx_signature",         data.get("tx_signature")),
        ("data.hash",                 data.get("hash")),
    ]
    for field, val in candidates:
        if val and isinstance(val, str) and len(val) >= 44:
            logger.debug("extract_tx_signature: encontrada em %s", field)
            return val

    # Fallback: scan regex em todo o JSON — base58 com 87-90 chars (tx sig Solana)
    try:
        raw = json.dumps(query_order_response)
        matches = re.findall(r'[1-9A-HJ-NP-Za-km-z]{87,90}', raw)
        # Exclui o próprio wallet address e mints (também são base58 longos)
        wallet = WALLET_ADDRESS
        for m in matches:
            if m != wallet and len(m) >= 87:
                logger.info("extract_tx_signature: encontrada via regex (%d chars): %s...", len(m), m[:20])
                return m
    except Exception:
        pass

    # Log de warning para diagnóstico quando state=confirmed mas sig não encontrada
    state = conf.get("state") or data.get("state") or ""
    if state in ("processed", "confirmed"):
        logger.warning(
            "extract_tx_signature: state=%s mas sig não encontrada. "
            "Keys em data: %s | Keys em confirmation: %s",
            state, list(data.keys()), list(conf.keys()),
        )
    return None
