#!/usr/bin/env python3
"""
Teste ERPC transactionSubscribe
Verifica se eventos do pAMM chegam parseáveis antes de integrar no bot.

Execução:
    python test_erpc.py
"""
import asyncio
import websockets
import json
import base64
import struct
import hashlib
from datetime import datetime

API_KEY = "e5b9b243-9188-41b4-a59f-596119afda77"
PAMM    = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
WSS_URL = f"wss://edge.erpc.global?api-key={API_KEY}"

# Discriminadores Anchor: sha256("event:BuyEvent")[:8]
def _disc(name: str) -> bytes:
    return hashlib.sha256(f"event:{name}".encode()).digest()[:8]

DISC_BUY  = _disc("BuyEvent")
DISC_SELL = _disc("SellEvent")

def parse_event(log_line: str) -> dict | None:
    """Tenta parsear BuyEvent ou SellEvent de uma linha de log Anchor."""
    if "Program data:" not in log_line:
        return None
    try:
        b64 = log_line.split("Program data:")[-1].strip()
        raw = base64.b64decode(b64)
        if len(raw) < 64:
            return None
        disc = raw[:8]
        if disc not in (DISC_BUY, DISC_SELL):
            return None
        side       = "BUY " if disc == DISC_BUY else "SELL"
        pool_base  = struct.unpack_from("<Q", raw, 48)[0]   # tokens na pool
        pool_quote = struct.unpack_from("<Q", raw, 56)[0]   # SOL na pool (lamports)
        if pool_base == 0:
            return None
        price_sol = (pool_quote / 1e9) / (pool_base / 1e6)
        return {
            "side":       side,
            "pool_base":  pool_base,
            "pool_quote": pool_quote,
            "price_sol":  price_sol,
            "pool_sol":   pool_quote / 1e9,
        }
    except Exception:
        return None

async def main():
    print("=" * 60)
    print("ERPC transactionSubscribe — Teste de Eventos pAMM")
    print("=" * 60)
    print(f"Endpoint : {WSS_URL[:55]}...")
    print(f"Programa : {PAMM}")
    print(f"Aguardando eventos... (Ctrl+C para parar)\n")

    try:
        async with websockets.connect(WSS_URL, ping_interval=30) as ws:
            sub = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "transactionSubscribe",
                "params": [
                    {
                        "vote":           False,
                        "failed":         False,
                        "accountInclude": [PAMM],
                    },
                    {
                        "commitment":               "processed",
                        "encoding":                 "base64",
                        "transactionDetails":       "full",
                        "showRewards":              False,
                        "maxSupportedTransactionVersion": 0,
                    }
                ]
            }
            await ws.send(json.dumps(sub))

            count       = 0
            tx_total    = 0
            parse_ok    = 0
            parse_fail  = 0

            async for raw_msg in ws:
                data = json.loads(raw_msg)

                # Debug: mostrar primeiras 5 mensagens brutas
                if tx_total + parse_ok + parse_fail < 5:
                    print(f"[DEBUG] mensagem bruta: {str(raw_msg)[:300]}")

                # Confirmação da subscrição
                if "result" in data and isinstance(data.get("result"), int):
                    print(f"✅ Subscrito com ID: {data['result']}")
                    print(f"{'─'*60}\n")
                    continue

                # Erros da API
                if "error" in data:
                    print(f"❌ Erro da API: {data['error']}")
                    continue

                if data.get("method") != "transactionNotification":
                    print(f"[DEBUG] método desconhecido: {data.get('method')} | keys: {list(data.keys())}")
                    continue

                tx_total += 1
                result = data["params"]["result"]
                sig    = result.get("signature", "?")
                meta   = result.get("transaction", {}).get("meta", {})
                logs   = meta.get("logMessages", [])

                if not logs:
                    parse_fail += 1
                    continue

                events = [e for e in (parse_event(l) for l in logs) if e]

                if not events:
                    parse_fail += 1
                    continue

                parse_ok += 1
                count += 1

                ts = datetime.now().strftime("%H:%M:%S")
                for ev in events:
                    print(
                        f"[{ts}] #{count:04d} | {ev['side']} | "
                        f"price={ev['price_sol']:.8f} SOL | "
                        f"pool={ev['pool_sol']:.2f} SOL | "
                        f"sig={sig[:20]}..."
                    )

                # Resumo a cada 50 eventos parseados
                if count % 50 == 0:
                    pct = parse_ok / tx_total * 100 if tx_total else 0
                    print(f"\n  📊 Stats: {tx_total} txs recebidas | "
                          f"{parse_ok} parseadas ({pct:.0f}%) | "
                          f"{parse_fail} sem evento pAMM\n")

    except KeyboardInterrupt:
        print(f"\n\n{'='*60}")
        print(f"Teste encerrado.")
        print(f"Total txs recebidas : {tx_total}")
        print(f"Eventos parseados   : {parse_ok} ✅")
        print(f"Sem evento pAMM     : {parse_fail}")
        if tx_total:
            print(f"Taxa de parse       : {parse_ok/tx_total*100:.0f}%")
        print(f"{'='*60}")

if __name__ == "__main__":
    asyncio.run(main())
