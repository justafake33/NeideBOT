"""
Resolve pool address e preço atual via DexScreener para tokens Solana.

Usado pelo HeliusWS para saber qual pool address subscrever e para
buscar o preço real-time quando um swap é detectado.
"""
import logging
import threading

import requests

logger = logging.getLogger(__name__)

_cache: dict[str, str | None] = {}  # mint → pool_address (None = sem pool)
_lock  = threading.Lock()

_DEXSCREENER_BASE = "https://api.dexscreener.com"
_TIMEOUT = 5.0


def get_pool_address(mint: str) -> str | None:
    """Retorna o pool address mais líquido para este mint no Solana."""
    with _lock:
        if mint in _cache:
            return _cache[mint]

    pool = None
    try:
        resp = requests.get(
            f"{_DEXSCREENER_BASE}/token-pairs/v1/solana/{mint}",
            timeout=_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        pairs = resp.json().get("pairs") or []

        if pairs:
            pairs.sort(
                key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0),
                reverse=True,
            )
            pool = pairs[0].get("pairAddress") or None
            liq  = float((pairs[0].get("liquidity") or {}).get("usd") or 0)
            logger.info("pool_resolver %s → %s (liq=$%.0f)", mint[:8], (pool or "?")[:8], liq)
        else:
            logger.debug("pool_resolver %s: sem pares no DexScreener", mint[:8])

    except Exception as e:
        logger.debug("pool_resolver %s: %s", mint[:8], e)

    with _lock:
        _cache[mint] = pool
    return pool


def get_price_usd(mint: str) -> float | None:
    """
    Preço atual em USD do token via DexScreener.
    Latência típica: 1-3s após o swap on-chain.
    """
    try:
        resp = requests.get(
            f"{_DEXSCREENER_BASE}/token-pairs/v1/solana/{mint}",
            timeout=3.0,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        pairs = resp.json().get("pairs") or []
        if not pairs:
            return None
        pairs.sort(
            key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0),
            reverse=True,
        )
        price_str = pairs[0].get("priceUsd")
        return float(price_str) if price_str else None
    except Exception as e:
        logger.debug("get_price_usd %s: %s", mint[:8], e)
        return None
