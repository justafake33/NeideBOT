"""
executor.py — Fase 6-lite: execução de trades $1 para calibração de friction.

Fluxo:
  bounce_signal dispara → on_bounce() → thread _execute() →
  swap() → wait_fill() → create_strategy() → log em trades table

Ativado apenas com TRADING_ENABLED=true no Railway.
Limite: CALIBRATION_MAX_TRADES trades (padrão 50).
"""
import logging
import os
import threading
import time
from datetime import datetime, timezone

import db
from gmgn_client import GmgnClient, get_sol_usd

logger = logging.getLogger(__name__)

TRADING_ENABLED    = os.getenv("TRADING_ENABLED",         "false").lower() == "true"
CALIBRATION_AMOUNT = float(os.getenv("CALIBRATION_AMOUNT_USD",  "1.0"))
CALIBRATION_MAX    = int(os.getenv(  "CALIBRATION_MAX_TRADES",  "50"))
TRADE_TP_PCT       = float(os.getenv("TRADE_TP_PCT",            "0.75"))
TRADE_SL_PCT       = float(os.getenv("TRADE_SL_PCT",            "0.15"))
SLIPPAGE_BPS       = int(os.getenv(  "TRADE_SLIPPAGE_BPS",      "500"))  # 5% max slippage
ORDER_POLL_SEC     = int(os.getenv(  "ORDER_POLL_SEC",           "2"))
ORDER_TIMEOUT_SEC  = int(os.getenv(  "ORDER_TIMEOUT_SEC",        "60"))

# A.4 — Persistência: aguarda N segundos e verifica que os últimos K ticks estão acima do entry.
# 3 ticks consecutivos acima do entry → WR 36.4% vs 12.1% (+24.3pp).
PERSISTENCE_WAIT_SEC   = int(os.getenv("PERSISTENCE_WAIT_SEC",   "90"))   # espera antes de verificar
PERSISTENCE_MIN_TICKS  = int(os.getenv("PERSISTENCE_MIN_TICKS",  "3"))    # ticks mínimos acima do entry


class TradeExecutor:
    """
    Executor conectado ao bounce_signal detector.
    Thread-safe — on_bounce() pode ser chamado de qualquer thread do scanner.
    """

    def __init__(self, gmgn: GmgnClient):
        self.gmgn  = gmgn
        self._lock = threading.Lock()
        if TRADING_ENABLED:
            logger.info(
                "TradeExecutor ativo — TP=%.0f%% SL=%.0f%% amount=$%.2f max=%d trades",
                TRADE_TP_PCT * 100, TRADE_SL_PCT * 100, CALIBRATION_AMOUNT, CALIBRATION_MAX,
            )
            threading.Thread(target=self._recover_pending, daemon=True, name="trade-recover").start()
        else:
            logger.info("TradeExecutor em modo DRY-RUN (TRADING_ENABLED=false)")

    def _recover_pending(self):
        """
        Na inicialização, recupera trades com order_id mas sem strategy_id.
        Casos: fill_pending, error por timeout, open sem estratégia.
        Re-tenta criar TP/SL automaticamente.
        """
        time.sleep(5)  # aguarda DB e GMGN estarem prontos
        try:
            pending = db.get_trades_without_strategy()
        except Exception as e:
            logger.error("_recover_pending: db error %s", e)
            return
        if not pending:
            return
        logger.info("_recover_pending: %d trades sem estratégia — tentando recuperar", len(pending))
        for trade in pending:
            trade_id   = trade["id"]
            order_id   = trade.get("order_id")
            mint       = trade["mint"]
            symbol     = trade.get("symbol", mint[:8])
            amount_sol = trade.get("amount_sol", 0)
            sol_usd    = trade.get("entry_price_usd") / amount_sol if trade.get("entry_price_usd") and amount_sol else 0
            if not order_id:
                continue
            try:
                result = self.gmgn.query_order(order_id)
                data   = result.get("data") or result
                status = str(data.get("status") or "").lower()
                logger.info("_recover_pending #%d %s → status='%s'", trade_id, symbol, status)
                if status not in ("filled", "completed", "success", "done"):
                    continue
                tokens_received = _safe_float(data.get("out_amount"))
                entry_price_sol = _safe_float(trade.get("entry_price_sol"))
                if not tokens_received or not entry_price_sol:
                    logger.warning("_recover_pending #%d: tokens ou entry_price ausentes", trade_id)
                    continue
                strat_res   = self.gmgn.create_strategy(
                    token_mint=mint,
                    tokens_amount=tokens_received,
                    entry_price_sol=entry_price_sol,
                    tp_pct=TRADE_TP_PCT,
                    sl_pct=TRADE_SL_PCT,
                )
                strat_data  = strat_res.get("data") or strat_res
                strategy_id = strat_data.get("strategy_id") or strat_data.get("id")
                db.update_trade(trade_id, {"strategy_id": strategy_id, "status": "open", "error_msg": None})
                logger.info("_recover_pending #%d %s — estratégia criada: %s", trade_id, symbol, strategy_id)
            except Exception as e:
                logger.error("_recover_pending #%d %s: %s", trade_id, symbol, e)

    # ------------------------------------------------------------------
    # Entry point — chamado pelo HotMonitor._check_bounce()
    # ------------------------------------------------------------------

    def on_bounce(self, mint: str, symbol: str, mc_bottom: float,
                  mc_migration: float, elapsed_sec: int):
        """
        Chamado quando bounce_signal dispara.
        Verifica limites, cria registro e despacha thread de execução.
        """
        if not TRADING_ENABLED:
            logger.debug("DRY-RUN bounce %s mc_bottom=$%.0f", symbol, mc_bottom)
            return

        with self._lock:
            try:
                n = db.count_trades()
            except Exception as e:
                logger.error("count_trades: %s", e)
                return
            if n >= CALIBRATION_MAX:
                logger.info(
                    "Calibração concluída (%d/%d) — executor pausado", n, CALIBRATION_MAX
                )
                return

        sol_usd = get_sol_usd()
        if not sol_usd or sol_usd <= 0:
            logger.warning("Sem preço SOL/USD — skip trade %s", symbol)
            return

        amount_sol     = CALIBRATION_AMOUNT / sol_usd
        entry_expected = mc_bottom * 1.15   # entry target = 15% acima do fundo

        try:
            trade_id = db.insert_trade({
                "mint":           mint,
                "symbol":         symbol,
                "ts_signal":      datetime.now(timezone.utc),
                "mc_bottom":      mc_bottom,
                "mc_migration":   mc_migration,
                "entry_expected": entry_expected,
                "amount_sol":     amount_sol,
                "amount_usd":     CALIBRATION_AMOUNT,
                "tp_pct":         TRADE_TP_PCT,
                "sl_pct":         TRADE_SL_PCT,
                "status":         "pending",
            })
        except Exception as e:
            logger.error("insert_trade %s: %s", symbol, e)
            return

        logger.info(
            "TRADE #%d %s — $%.2f (%.5f SOL @ $%.0f/SOL) entry=$%.0f",
            trade_id, symbol, CALIBRATION_AMOUNT, amount_sol, sol_usd, entry_expected,
        )

        threading.Thread(
            target=self._execute,
            args=(trade_id, mint, symbol, amount_sol, sol_usd, entry_expected),
            daemon=True,
            name=f"trade-{trade_id}",
        ).start()

    # ------------------------------------------------------------------
    # Execução — roda em thread própria
    # ------------------------------------------------------------------

    def _execute(self, trade_id: int, mint: str, symbol: str,
                 amount_sol: float, sol_usd: float, entry_expected: float = 0.0):
        ts_signal = datetime.now(timezone.utc)

        # A.4 — Persistência: aguarda 90s e verifica que os últimos 3 ticks APÓS o sinal estão acima do entry.
        # 3 ticks consecutivos acima do entry → WR 36.4% vs 12.1% sem o filtro (+24.3pp).
        if PERSISTENCE_WAIT_SEC > 0 and entry_expected > 0:
            logger.info("TRADE #%d %s — aguardando %ds para verificar persistência (entry=$%.0f)",
                        trade_id, symbol, PERSISTENCE_WAIT_SEC, entry_expected)
            time.sleep(PERSISTENCE_WAIT_SEC)
            if not self._check_persistence(mint, entry_expected, ts_signal):
                logger.info("TRADE #%d %s — cancelado: preço não manteve acima do entry em %ds",
                            trade_id, symbol, PERSISTENCE_WAIT_SEC)
                _fail(trade_id, f"persistence_fail: preço abaixo do entry após {PERSISTENCE_WAIT_SEC}s")
                return

        # 1. Swap
        try:
            ts_sent  = datetime.now(timezone.utc)
            swap_res = self.gmgn.swap(
                token_mint=mint,
                amount_sol=amount_sol,
                slippage_bps=SLIPPAGE_BPS,
            )
            data     = swap_res.get("data") or swap_res
            order_id = data.get("order_id") or data.get("id")

            db.update_trade(trade_id, {
                "ts_order_sent": ts_sent,
                "order_id":      order_id,
                "status":        "open",
            })
            logger.info("Swap #%d %s — order_id=%s", trade_id, symbol, order_id)
        except Exception as e:
            logger.error("Swap #%d %s falhou: %s", trade_id, symbol, e)
            _fail(trade_id, str(e))
            return

        # 2. Aguarda confirmação on-chain (fixo) + tenta obter dados do fill
        SWAP_CONFIRM_SEC = 30
        logger.info("Swap #%d %s — aguardando %ds confirmação on-chain", trade_id, symbol, SWAP_CONFIRM_SEC)
        time.sleep(SWAP_CONFIRM_SEC)

        # Tenta obter dados reais do fill via query_order — não bloqueia se não vier
        tokens_received  = None
        entry_price_sol  = None
        fee_sol          = None
        slippage_pct     = None
        fill_sol         = amount_sol  # fallback: assume executou pelo valor enviado

        try:
            qr       = self.gmgn.query_order(order_id)
            qd       = qr.get("data") or qr
            status   = str(qd.get("status") or "").lower()
            logger.info("query_order #%d %s → status='%s' data=%s", trade_id, symbol, status, qd)

            # confirmation.state: "processed"|"confirmed" = OK, "failed"|"expired" = cancelado
            confirmation = qd.get("confirmation") or {}
            conf_state   = str(confirmation.get("state") or "").lower()
            if conf_state in ("failed", "expired"):
                logger.warning("Swap #%d %s cancelado (confirmation.state=%s)", trade_id, symbol, conf_state)
                _fail(trade_id, f"swap cancelado: {conf_state}")
                return

            # filled_output_amount = tokens recebidos | filled_input_amount = SOL gasto
            out_raw = _safe_float(qd.get("filled_output_amount"))
            if out_raw and out_raw > 0:
                tokens_received = out_raw
            in_raw = _safe_float(qd.get("filled_input_amount"))
            if in_raw:
                fill_sol = in_raw / 1e9 if in_raw > 1000 else in_raw
                slippage_pct = (fill_sol - amount_sol) / amount_sol if amount_sol else None
            fee_raw = _safe_float(qd.get("fee"))
            if fee_raw:
                fee_sol = fee_raw / 1e9 if fee_raw > 1000 else fee_raw
        except Exception as e:
            logger.warning("query_order #%d %s: %s — usando fallbacks", trade_id, symbol, e)

        ts_confirmed = datetime.now(timezone.utc)
        latency_sec  = (ts_confirmed - ts_signal).total_seconds()

        if tokens_received and fill_sol:
            entry_price_sol = fill_sol / tokens_received

        db.update_trade(trade_id, {
            "ts_order_confirmed": ts_confirmed,
            "tokens_received":    tokens_received,
            "entry_price_sol":    entry_price_sol,
            "entry_price_usd":    fill_sol * sol_usd if fill_sol else None,
            "fee_sol":            fee_sol,
            "latency_sec":        latency_sec,
            "slippage_pct":       slippage_pct,
            "status":             "strategy_pending",
        })
        logger.info(
            "Fill #%d %s — tokens=%s latência=%.1fs slippage=%s",
            trade_id, symbol,
            f"{tokens_received:.0f}" if tokens_received else "desconhecido",
            latency_sec,
            f"{slippage_pct*100:.2f}%" if slippage_pct else "n/a",
        )
        # 3. Cria estratégia TP/SL
        # Se não temos tokens_received, não conseguimos criar estratégia por quantidade.
        # Loga como open sem strategy — _recover_pending vai tentar novamente no próximo restart.
        if not tokens_received or tokens_received <= 0:
            logger.warning("Trade #%d %s — tokens_received desconhecido, strategy pendente", trade_id, symbol)
            db.update_trade(trade_id, {"status": "open", "error_msg": "tokens_received indisponível — aguardando recover"})
            return

        try:
            strat_res   = self.gmgn.create_strategy(
                token_mint=mint,
                tokens_amount=tokens_received,
                entry_price_sol=entry_price_sol or 0,
                tp_pct=TRADE_TP_PCT,
                sl_pct=TRADE_SL_PCT,
            )
            tp_id = strat_res.get("tp", {}).get("order_id")
            sl_id = strat_res.get("sl", {}).get("order_id")
            # strategy_id guarda ambos como "tp:{id}|sl:{id}"
            strategy_id = f"tp:{tp_id}|sl:{sl_id}"
            db.update_trade(trade_id, {
                "strategy_id": strategy_id,
                "status":      "open",
                "error_msg":   None,
            })
            logger.info(
                "Estratégia #%d %s — TP=%s SL=%s (TP=+%.0f%% SL=-%.0f%%)",
                trade_id, symbol, tp_id, sl_id, TRADE_TP_PCT * 100, TRADE_SL_PCT * 100,
            )
        except Exception as e:
            logger.error("create_strategy #%d %s: %s", trade_id, symbol, e)
            db.update_trade(trade_id, {
                "status":    "open",
                "error_msg": f"create_strategy: {e}",
            })

    def _check_persistence(self, mint: str, entry_expected: float, ts_signal) -> bool:
        """
        A.4: Verifica que os ticks APÓS ts_signal (exclusive) estão acima do entry_expected.
        Filtra por min_ts=ts_signal para excluir ticks do dump que precedem o sinal.
        Retorna True se PERSISTENCE_MIN_TICKS ticks pós-sinal estão todos acima do entry,
        ou se não há ticks suficientes (fallback conservador: aprova).
        """
        try:
            ticks = db.get_recent_ticks(mint, min_ts=ts_signal)
        except Exception as e:
            logger.warning("_check_persistence %s: db error %s — aprovando por falta de dados", mint[:8], e)
            return True

        if len(ticks) < PERSISTENCE_MIN_TICKS:
            logger.debug("_check_persistence %s: apenas %d ticks pós-sinal — aprovando por falta de dados",
                         mint[:8], len(ticks))
            return True

        recent = ticks[-PERSISTENCE_MIN_TICKS:]
        all_above = all(mc >= entry_expected for _, mc in recent)
        logger.debug("_check_persistence %s: %d/%d ticks pós-sinal acima do entry ($%.0f) — %s",
                     mint[:8], sum(1 for _, mc in recent if mc >= entry_expected),
                     len(recent), entry_expected, "OK" if all_above else "FAIL")
        return all_above

    def _wait_fill_extended(self, order_id: str, extra_sec: int = 270) -> dict | None:
        """Continua polling após timeout inicial. Retorna None se não confirmar."""
        deadline = time.time() + extra_sec
        while time.time() < deadline:
            try:
                result = self.gmgn.query_order(order_id)
                data   = result.get("data") or result
                confirmation = data.get("confirmation") or {}
                status = str(confirmation.get("state") or data.get("state") or "").lower()
                if status in ("processed", "confirmed"):
                    return result
                if status in ("failed", "expired"):
                    logger.error("query_order extended %s state=%s", order_id, status)
                    return None
            except Exception as e:
                logger.debug("query_order extended %s: %s", order_id, e)
            time.sleep(ORDER_POLL_SEC)
        return None

    def _wait_fill(self, order_id: str) -> dict:
        """Poll query_order até status filled/completed ou timeout."""
        if not order_id:
            raise ValueError("order_id vazio — não é possível monitorar fill")
        deadline = time.time() + ORDER_TIMEOUT_SEC
        last_status = ""
        while time.time() < deadline:
            try:
                result = self.gmgn.query_order(order_id)
                data   = result.get("data") or result
                confirmation = data.get("confirmation") or {}
                status = str(confirmation.get("state") or data.get("state") or "").lower()
                if status != last_status:
                    logger.info("query_order %s → state='%s' data=%s", order_id[:16], status, data)
                    last_status = status
                if status in ("processed", "confirmed"):
                    return result
                if status in ("failed", "expired"):
                    raise RuntimeError(f"Ordem {order_id} state={status}")
            except RuntimeError:
                raise
            except Exception as e:
                logger.info("query_order %s erro: %s", order_id[:16], e)
            time.sleep(ORDER_POLL_SEC)
        raise TimeoutError(f"Ordem {order_id} não confirmada em {ORDER_TIMEOUT_SEC}s — último status='{last_status}'")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _fail(trade_id: int, msg: str):
    try:
        db.update_trade(trade_id, {"status": "error", "error_msg": msg[:500]})
    except Exception as e:
        logger.error("update_trade fail #%d: %s", trade_id, e)


def _safe_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except Exception:
        return None
