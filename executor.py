"""
executor.py — Fase 6-lite: execução de trades $1 para calibração de friction.

Fluxo:
  bounce_signal dispara → on_bounce() → thread _execute() →
  swap() com condition_orders embutidos (TP+SL atomicamente) →
  poll fill data (background 30s) → TradeMonitor detecta exit via token_ticks

TP/SL criados atomicamente no mesmo body do swap via condition_orders.
TradeMonitor roda em background a cada 30s verificando ticks pós-entrada
para detectar quando TP ou SL foi atingido e atualizar o status no DB.
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

PUMP_FUN_SUPPLY    = 1_000_000_000  # supply padrão Pump.fun
SPL_TOKEN_DECIMALS = int(os.getenv("SPL_TOKEN_DECIMALS", "6"))  # casas decimais do token SPL (Pump.fun = 6)


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
        else:
            logger.info("TradeExecutor em modo DRY-RUN (TRADING_ENABLED=false)")

    # ------------------------------------------------------------------
    # Entry point — chamado pelo HotMonitor._check_bounce()
    # ------------------------------------------------------------------

    def on_bounce(self, mint: str, symbol: str, mc_bottom: float,
                  mc_migration: float, elapsed_sec: int):
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
                logger.info("Calibração concluída (%d/%d) — executor pausado", n, CALIBRATION_MAX)
                return

        sol_usd = get_sol_usd()
        if not sol_usd or sol_usd <= 0:
            logger.warning("Sem preço SOL/USD — skip trade %s", symbol)
            return

        amount_sol   = CALIBRATION_AMOUNT / sol_usd
        entry_target = mc_bottom * 1.15  # referência para persistência

        try:
            trade_id = db.insert_trade({
                "mint":           mint,
                "symbol":         symbol,
                "ts_signal":      datetime.now(timezone.utc),
                "mc_bottom":      mc_bottom,
                "mc_migration":   mc_migration,
                "entry_expected": entry_target,
                "amount_sol":     amount_sol,
                "amount_usd":     CALIBRATION_AMOUNT,
                "tp_pct":         TRADE_TP_PCT,
                "sl_pct":         TRADE_SL_PCT,
                "status":         "pending",
            })
        except Exception as e:
            logger.error("insert_trade %s: %s", symbol, e)
            return

        logger.info("TRADE #%d %s — $%.2f (%.5f SOL @ $%.0f/SOL)",
                    trade_id, symbol, CALIBRATION_AMOUNT, amount_sol, sol_usd)

        threading.Thread(
            target=self._execute,
            args=(trade_id, mint, symbol, amount_sol, sol_usd, entry_target),
            daemon=True,
            name=f"trade-{trade_id}",
        ).start()

    # ------------------------------------------------------------------
    # Execução — roda em thread própria
    # ------------------------------------------------------------------

    def _execute(self, trade_id: int, mint: str, symbol: str,
                 amount_sol: float, sol_usd: float, entry_expected: float):
        ts_signal = datetime.now(timezone.utc)

        # A.4 — Persistência
        if PERSISTENCE_WAIT_SEC > 0 and entry_expected > 0:
            logger.info("TRADE #%d %s — aguardando %ds persistência (entry_ref=$%.0f)",
                        trade_id, symbol, PERSISTENCE_WAIT_SEC, entry_expected)
            time.sleep(PERSISTENCE_WAIT_SEC)
            if not self._check_persistence(mint, entry_expected, ts_signal):
                logger.info("TRADE #%d %s — cancelado: preço não manteve acima do entry em %ds",
                            trade_id, symbol, PERSISTENCE_WAIT_SEC)
                _fail(trade_id, f"persistence_fail após {PERSISTENCE_WAIT_SEC}s")
                return

        # 1. Swap com TP/SL embutidos via condition_orders
        # GMGN calcula os preços de saída sobre o fill real (price_scale = multiplicador)
        # Não precisamos do preço do fill — sem erros de "check price less than open price"
        try:
            ts_sent  = datetime.now(timezone.utc)
            swap_res = self.gmgn.swap(
                token_mint=mint,
                amount_sol=amount_sol,
                slippage_bps=SLIPPAGE_BPS,
                tp_pct=TRADE_TP_PCT,
                sl_pct=TRADE_SL_PCT,
            )
            data     = swap_res.get("data") or swap_res
            order_id = data.get("order_id") or data.get("id")
            db.update_trade(trade_id, {
                "ts_order_sent": ts_sent,
                "order_id":      order_id,
                "strategy_id":   f"embedded:TP+{TRADE_TP_PCT*100:.0f}%|SL-{TRADE_SL_PCT*100:.0f}%",
                "status":        "open",
            })
            logger.info("Swap+TP/SL #%d %s — order_id=%s (TP+%.0f%% SL-%.0f%% embutidos)",
                        trade_id, symbol, order_id, TRADE_TP_PCT * 100, TRADE_SL_PCT * 100)
        except Exception as e:
            logger.error("Swap #%d %s falhou: %s", trade_id, symbol, e)
            _fail(trade_id, str(e))
            return

        # 2. Poll fill em background — só para registrar dados reais no DB
        FILL_TIMEOUT_SEC = int(os.getenv("FILL_TIMEOUT_SEC", "30"))
        deadline = time.time() + FILL_TIMEOUT_SEC
        while time.time() < deadline:
            try:
                qr    = self.gmgn.query_order(order_id)
                qd    = qr.get("data") or qr
                conf  = qd.get("confirmation") or {}
                state = str(conf.get("state") or "").lower()
                if state in ("failed", "expired"):
                    db.update_trade(trade_id, {"error_msg": f"swap on-chain: {state}"})
                    return
                out_raw = _safe_float(qd.get("filled_output_amount"))
                if out_raw and out_raw > 0:
                    in_raw   = _safe_float(qd.get("filled_input_amount"))
                    fill_sol = (in_raw / 1e9 if in_raw and in_raw > 1000 else in_raw) or amount_sol
                    fee_raw  = _safe_float(qd.get("fee"))
                    fee_sol  = (fee_raw / 1e9 if fee_raw and fee_raw > 1000 else fee_raw)
                    db.update_trade(trade_id, {
                        "ts_order_confirmed": datetime.now(timezone.utc),
                        "tokens_received":    out_raw,
                        "entry_price_sol":    fill_sol / out_raw,
                        "entry_price_usd":    fill_sol * sol_usd,
                        "fee_sol":            fee_sol,
                        "latency_sec":        (datetime.now(timezone.utc) - ts_signal).total_seconds(),
                        "slippage_pct":       (fill_sol - amount_sol) / amount_sol,
                    })
                    logger.info("Fill #%d %s — %.0f tokens @ %.10f SOL",
                                trade_id, symbol, out_raw, fill_sol / out_raw)
                    return
            except Exception as e:
                logger.debug("poll fill #%d: %s", trade_id, e)
            time.sleep(1)

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
# TradeMonitor — detecta TP/SL via token_ticks do DB
# ------------------------------------------------------------------

MONITOR_POLL_SEC = int(os.getenv("TRADE_MONITOR_POLL_SEC", "30"))
MONITOR_MAX_AGE_H = int(os.getenv("TRADE_MONITOR_MAX_AGE_H", "4"))  # descarta trades muito antigos


class TradeMonitor:
    """
    Roda em background e detecta quando TP ou SL foi atingido para trades 'open'.
    Usa token_ticks (mc_high/mc_low em USD) coletados pelo HotMonitor/scanner.
    Compara contra entry_expected (mc_bottom * 1.15) com os thresholds tp_pct/sl_pct.
    """

    def __init__(self):
        self._stop = threading.Event()

    def start(self):
        if not TRADING_ENABLED:
            return
        threading.Thread(target=self._loop, daemon=True, name="trade-monitor").start()
        logger.info("TradeMonitor iniciado (poll=%ds max_age=%dh)", MONITOR_POLL_SEC, MONITOR_MAX_AGE_H)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._check_all()
            except Exception as e:
                logger.error("TradeMonitor._check_all: %s", e)
            time.sleep(MONITOR_POLL_SEC)

    def _check_all(self):
        trades = db.get_open_trades()
        if not trades:
            return
        sol_usd = get_sol_usd() or 0
        now = datetime.now(timezone.utc)
        for trade in trades:
            try:
                self._check_trade(trade, sol_usd, now)
            except Exception as e:
                logger.debug("TradeMonitor trade #%s: %s", trade.get("id"), e)

    def _check_trade(self, trade: dict, sol_usd: float, now: datetime):
        trade_id    = trade["id"]
        mint        = trade["mint"]
        symbol      = trade.get("symbol", mint[:8])
        entry_mc    = trade.get("entry_expected")
        tp_pct      = trade.get("tp_pct")  or TRADE_TP_PCT
        sl_pct      = trade.get("sl_pct")  or TRADE_SL_PCT
        created_at  = trade.get("created_at")
        ref_ts      = trade.get("ts_order_sent") or trade.get("ts_signal") or created_at

        if not entry_mc or entry_mc <= 0:
            return

        # Ignora trades muito antigos (sem ticks no DB)
        if created_at:
            age_h = (now - created_at).total_seconds() / 3600
            if age_h > MONITOR_MAX_AGE_H:
                logger.info("TradeMonitor #%d %s — fechando por idade (%.1fh > %dh)",
                            trade_id, symbol, age_h, MONITOR_MAX_AGE_H)
                db.update_trade(trade_id, {
                    "status":        "expired",
                    "exit_type":     "expired",
                    "exit_ts":       now,
                    "pnl_pct_real":  None,
                })
                return

        tp_mc = entry_mc * (1 + tp_pct)
        sl_mc = entry_mc * (1 - sl_pct)

        ticks = db.get_ticks_after(mint, min_ts=ref_ts)
        if not ticks:
            return

        for tick in ticks:
            high = float(tick["mc_high"] or 0)
            low  = float(tick["mc_low"]  or 0)
            tick_ts = tick["tick_ts"]

            if high >= tp_mc:
                exit_usd = tp_mc * sol_usd / entry_mc * (trade.get("amount_usd") or 1.0) if entry_mc else None
                db.update_trade(trade_id, {
                    "status":       "tp",
                    "exit_type":    "tp",
                    "exit_ts":      tick_ts,
                    "exit_price_usd": exit_usd,
                    "pnl_pct_real": tp_pct,
                })
                logger.info("TradeMonitor #%d %s — TP atingido (mc_high=$%.0f >= tp_mc=$%.0f)",
                            trade_id, symbol, high, tp_mc)
                return

            if low > 0 and low <= sl_mc:
                exit_usd = sl_mc * sol_usd / entry_mc * (trade.get("amount_usd") or 1.0) if entry_mc else None
                db.update_trade(trade_id, {
                    "status":       "sl",
                    "exit_type":    "sl",
                    "exit_ts":      tick_ts,
                    "exit_price_usd": exit_usd,
                    "pnl_pct_real": -sl_pct,
                })
                logger.info("TradeMonitor #%d %s — SL atingido (mc_low=$%.0f <= sl_mc=$%.0f)",
                            trade_id, symbol, low, sl_mc)
                return


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
