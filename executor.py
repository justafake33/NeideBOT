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

A.4 (persistência 90s) DESATIVADO: entrada imediata ao sinal.
Motivo: A.4 atrasava a entrada em ~90s, capturando o preço 53% acima do fundo
— quase no TP — eliminando o R:R da estratégia.
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
TRADE_SL_PCT       = float(os.getenv("TRADE_SL_PCT",            "0.15"))
SLIPPAGE_BPS       = int(os.getenv(  "TRADE_SLIPPAGE_BPS",      "500"))  # 5% max slippage
ORDER_POLL_SEC     = int(os.getenv(  "ORDER_POLL_SEC",           "2"))
ORDER_TIMEOUT_SEC  = int(os.getenv(  "ORDER_TIMEOUT_SEC",        "60"))

# Trailing TP — substitui TP fixo (TRADE_TP_PCT removido)
# TRAILING_ACTIVATION_PCT: gain mínimo para ativar o trailing (padrão 100% = dobra)
# TRAILING_DRAWDOWN_PCT:   recuo máximo do pico antes de vender (padrão 30%)
# Simulação (n=119 válidos): trailing 30% ativ. 100% → avg 159.2% vs TP fixo 71.3%
TRAILING_ACTIVATION_PCT = float(os.getenv("TRAILING_ACTIVATION_PCT", "100"))  # %
TRAILING_DRAWDOWN_PCT   = float(os.getenv("TRAILING_DRAWDOWN_PCT",   "30"))   # %

# A.4 — Persistência: aguarda N segundos e verifica que os últimos K ticks estão acima do entry.
# 3 ticks consecutivos acima do entry → WR 36.4% vs 12.1% (+24.3pp).
PERSISTENCE_WAIT_SEC   = 0   # A.4 desativado — entrada imediata ao sinal
PERSISTENCE_MIN_TICKS  = 0

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
                "TradeExecutor ativo — trailing(ativ=%.0f%% dd=%.0f%%) SL=%.0f%% amount=$%.2f max=%d",
                TRAILING_ACTIVATION_PCT, TRAILING_DRAWDOWN_PCT, TRADE_SL_PCT * 100,
                CALIBRATION_AMOUNT, CALIBRATION_MAX,
            )
        else:
            logger.info("TradeExecutor em modo DRY-RUN (TRADING_ENABLED=false)")

    # ------------------------------------------------------------------
    # Entry point — chamado pelo HotMonitor._check_bounce()
    # ------------------------------------------------------------------

    def on_bounce(self, mint: str, symbol: str, mc_bottom: float,
                  mc_migration: float, elapsed_sec: int):
        if not TRADING_ENABLED:
            logger.info("DRY-RUN bounce %s mc_bottom=$%.0f (TRADING_ENABLED=false)", symbol, mc_bottom)
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
                "tp_pct":         TRAILING_ACTIVATION_PCT / 100,  # activation level — sem TP fixo
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
        # 1. Swap com TP/SL embutidos via condition_orders
        # GMGN calcula os preços de saída sobre o fill real (price_scale = multiplicador)
        # Não precisamos do preço do fill — sem erros de "check price less than open price"
        try:
            ts_sent  = datetime.now(timezone.utc)
            swap_res = self.gmgn.swap(
                token_mint=mint,
                amount_sol=amount_sol,
                slippage_bps=SLIPPAGE_BPS,
                trailing_activation_pct=TRAILING_ACTIVATION_PCT,
                trailing_drawdown_pct=TRAILING_DRAWDOWN_PCT,
                sl_pct=TRADE_SL_PCT,
            )
            data     = swap_res.get("data") or swap_res
            order_id = data.get("order_id") or data.get("id")
            # Extrai IDs dos condition_orders (TP/SL) se retornados pelo GMGN.
            # Formato pode variar — logamos tudo para descobrir a estrutura real.
            co_list = data.get("condition_orders") or []
            co_ids  = "|".join(str(co.get("order_id") or co.get("id") or "") for co in co_list if co)
            strategy_label = f"trailing:ativ{TRAILING_ACTIVATION_PCT:.0f}%|dd{TRAILING_DRAWDOWN_PCT:.0f}%|SL-{TRADE_SL_PCT*100:.0f}%"
            if co_ids:
                strategy_label = co_ids  # substitui label por IDs reais se disponíveis
            logger.debug("Swap #%d condition_orders raw: %s", trade_id, str(co_list)[:500])
            db.update_trade(trade_id, {
                "ts_order_sent": ts_sent,
                "order_id":      order_id,
                "strategy_id":   strategy_label,
                "status":        "open",
            })
            logger.info(
                "Swap+trailing #%d %s — order_id=%s (ativ=%.0f%% dd=%.0f%% SL-%.0f%%)",
                trade_id, symbol, order_id,
                TRAILING_ACTIVATION_PCT, TRAILING_DRAWDOWN_PCT, TRADE_SL_PCT * 100,
            )
        except Exception as e:
            logger.error("Swap #%d %s falhou: %s", trade_id, symbol, e)
            _fail(trade_id, str(e))
            return

        # 2. Poll fill em background — registra dados reais (slippage, fee, tokens, latência)
        # Timeout de 120s: Solana pode demorar até 60s em congestionamento
        FILL_TIMEOUT_SEC = int(os.getenv("FILL_TIMEOUT_SEC", "120"))
        deadline = time.time() + FILL_TIMEOUT_SEC
        while time.time() < deadline:
            try:
                qr    = self.gmgn.query_order(order_id)
                qd    = qr.get("data") or qr
                conf  = qd.get("confirmation") or {}
                state = str(conf.get("state") or "").lower()
                if state in ("failed", "expired"):
                    db.update_trade(trade_id, {"status": "error", "error_msg": f"swap on-chain: {state}"})
                    return
                out_raw = _safe_float(qd.get("filled_output_amount"))
                if out_raw and out_raw > 0:
                    in_raw   = _safe_float(qd.get("filled_input_amount"))
                    # in_raw pode estar em lamports (>1000) ou SOL
                    fill_sol = (in_raw / 1e9 if in_raw and in_raw > 1000 else in_raw) or amount_sol
                    fee_raw  = _safe_float(qd.get("fee"))
                    fee_sol  = (fee_raw / 1e9 if fee_raw and fee_raw > 1000 else fee_raw) or 0.0
                    # slippage: diferença entre SOL real gasto e SOL intencionado
                    slippage = (fill_sol - amount_sol) / amount_sol if amount_sol else 0.0
                    db.update_trade(trade_id, {
                        "ts_order_confirmed": datetime.now(timezone.utc),
                        "tokens_received":    out_raw,
                        "entry_price_sol":    fill_sol / out_raw,
                        "entry_price_usd":    fill_sol * sol_usd,  # USD gasto real
                        "fee_sol":            fee_sol,
                        "latency_sec":        (datetime.now(timezone.utc) - ts_signal).total_seconds(),
                        "slippage_pct":       slippage,
                    })
                    logger.info("Fill #%d %s — %.0f tokens @ %.10f SOL | slip=%.2f%% fee=%.6f SOL lat=%.1fs",
                                trade_id, symbol, out_raw, fill_sol / out_raw,
                                slippage * 100, fee_sol,
                                (datetime.now(timezone.utc) - ts_signal).total_seconds())
                    return
            except Exception as e:
                logger.debug("poll fill #%d: %s", trade_id, e)
            time.sleep(2)

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
        self._stop  = threading.Event()
        self._helius = None  # injetado via set_helius() antes de start()

    def set_helius(self, helius):
        self._helius = helius

    def start(self):
        if not TRADING_ENABLED:
            return
        threading.Thread(target=self._loop, daemon=True, name="trade-monitor").start()
        logger.info("TradeMonitor iniciado (poll=%ds max_age=%dh helius=%s)",
                    MONITOR_POLL_SEC, MONITOR_MAX_AGE_H,
                    "ON" if self._helius else "OFF")

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._check_all()
            except Exception as e:
                logger.error("TradeMonitor._check_all: %s", e)
            time.sleep(MONITOR_POLL_SEC)

    def _recover_fill(self, trade: dict, sol_usd: float) -> bool:
        """Re-busca fill data do GMGN para trades com entry_price_usd=NULL.

        O loop de fill em _execute() pode expirar antes de GMGN popular
        filled_output_amount (race condition: state=confirmed mas amounts ainda NULL).
        Recupera esses dados retroativamente a cada ciclo do TradeMonitor.
        Retorna True se fill foi recuperado.
        """
        order_id = trade.get("order_id")
        if not order_id:
            return False
        trade_id   = trade["id"]
        amount_sol = trade.get("amount_sol") or 0
        symbol     = trade.get("symbol", trade["mint"][:8])
        try:
            res    = self.gmgn.query_order(order_id)
            qd     = res.get("data") or res
            out_raw = _safe_float(qd.get("filled_output_amount"))
            if not out_raw or out_raw <= 0:
                logger.info(
                    "_recover_fill #%d %s — filled_output_amount ausente. order_id=%s keys=%s",
                    trade_id, symbol, order_id[:16], list(qd.keys()),
                )
                return False
            in_raw   = _safe_float(qd.get("filled_input_amount"))
            fill_sol = (in_raw / 1e9 if in_raw and in_raw > 1000 else in_raw) or amount_sol
            fee_raw  = _safe_float(qd.get("fee"))
            fee_sol  = (fee_raw / 1e9 if fee_raw and fee_raw > 1000 else fee_raw) or 0.0
            slippage = (fill_sol - amount_sol) / amount_sol if amount_sol else 0.0
            db.update_trade(trade_id, {
                "tokens_received": out_raw,
                "entry_price_sol": fill_sol / out_raw,
                "entry_price_usd": fill_sol * sol_usd,
                "fee_sol":         fee_sol,
                "slippage_pct":    slippage,
            })
            logger.info(
                "TradeMonitor _recover_fill #%d %s — %.0f tokens @ %.10f SOL (fill recuperado retroativamente)",
                trade_id, symbol, out_raw, fill_sol / out_raw,
            )
            return True
        except Exception as e:
            logger.debug("_recover_fill #%d: %s", trade_id, e)
            return False

    def _check_all(self):
        sol_usd = get_sol_usd() or 0
        now = datetime.now(timezone.utc)
        trades = db.get_open_trades()
        for trade in trades:
            # Recupera fill data se ausente — _execute() pode ter expirado antes de GMGN popular os amounts
            if not trade.get("entry_price_usd") and trade.get("order_id"):
                try:
                    recovered = self._recover_fill(trade, sol_usd)
                    if recovered:
                        # Re-lê o trade com fill atualizado antes de checar TP/SL
                        fresh = db.get_trade_by_id(trade["id"])
                        if fresh:
                            trade = fresh
                except Exception as e:
                    logger.debug("TradeMonitor recover_fill #%s: %s", trade.get("id"), e)
            try:
                self._check_trade(trade, sol_usd, now)
            except Exception as e:
                logger.debug("TradeMonitor trade #%s: %s", trade.get("id"), e)
            try:
                self._collect_absorption_tick(trade)
            except Exception as e:
                logger.debug("TradeMonitor absorption #%s: %s", trade.get("id"), e)
        for trade in db.get_trailing_active_trades():
            try:
                self._check_trailing_exit(trade, sol_usd, now)
            except Exception as e:
                logger.debug("TradeMonitor trailing #%s: %s", trade.get("id"), e)
            try:
                self._collect_absorption_tick(trade)
            except Exception as e:
                logger.debug("TradeMonitor absorption trailing #%s: %s", trade.get("id"), e)

    def _collect_absorption_tick(self, trade: dict):
        """Coleta fluxo buy/sell do Helius pAMM enquanto o trade está aberto.
        Dados brutos apenas — zero decisão. Cruzar com outcome (TP/SL) depois.
        Janela rolante de 30s: snapshot do que está acontecendo AGORA no mercado.
        Não insere se mint não está subscrito no Helius — zeros seriam indistinguíveis
        de "subscrito mas sem eventos", corrompendo a série temporal.
        """
        if not self._helius:
            return
        mint = trade.get("mint")
        if not mint:
            return
        # Só coleta se o mint está sendo monitorado pelo Helius.
        # Tokens fora da janela quente (>60min) não estão subscritos — sem dados reais.
        if mint not in getattr(self._helius, "_swap_events", {}):
            return
        trade_id = trade.get("id")
        w = self._helius.get_absorption_window(mint, window_sec=30)
        db.insert_trade_absorption_tick({
            "trade_id":   trade_id,
            "mint":       mint,
            "buy_vol":    w.get("buy_vol_sol",  0) or 0,
            "sell_vol":   w.get("sell_vol_sol", 0) or 0,
            "buy_count":  w.get("buy_count",    0) or 0,
            "sell_count": w.get("sell_count",   0) or 0,
            "events":     w.get("total_events", 0) or 0,
            "lrg_buy":    w.get("largest_buy",  0) or 0,
        })

    def _check_trade(self, trade: dict, sol_usd: float, now: datetime):
        trade_id        = trade["id"]
        mint            = trade["mint"]
        symbol          = trade.get("symbol", mint[:8])
        entry_mc        = trade.get("entry_expected")
        tp_pct          = trade.get("tp_pct") or (TRAILING_ACTIVATION_PCT / 100)
        sl_pct          = trade.get("sl_pct") or TRADE_SL_PCT
        created_at      = trade.get("created_at")
        ref_ts          = trade.get("ts_order_sent") or trade.get("ts_signal") or created_at
        entry_price_usd = trade.get("entry_price_usd")   # preço real de fill (USD gasto)
        tokens_received = trade.get("tokens_received")   # tokens recebidos no fill

        if not entry_mc or entry_mc <= 0:
            return

        # Calcula MC real de entrada a partir do fill, se disponível.
        # entry_price_usd = SOL_gasto × SOL/USD. price_per_token = entry_price_usd / tokens.
        # MC = price_per_token × supply (sempre 1B em tokens Pump.fun).
        entry_mc_real = None
        if entry_price_usd and tokens_received and tokens_received > 0 and sol_usd > 0:
            price_per_token_usd = entry_price_usd / tokens_received
            entry_mc_real = price_per_token_usd * 1_000_000_000

        # Trades muito antigos sem ticks → marca expired
        if created_at:
            age_h = (now - created_at).total_seconds() / 3600
            if age_h > MONITOR_MAX_AGE_H:
                logger.info("TradeMonitor #%d %s — expired (%.1fh)", trade_id, symbol, age_h)
                db.update_trade(trade_id, {"status": "expired", "exit_type": "expired", "exit_ts": now})
                return

        # SL usa entry_mc real se disponível; fallback para entry_expected.
        # TP/trailing só dispara com entry_mc real — entry_expected × 1.75 é baixo
        # demais para trades antigos (entrada real era 30-100% acima do esperado),
        # gerando falsos positivos que mostram +75% quando o real foi -15%.
        sl_ref  = entry_mc_real or entry_mc
        sl_mc   = sl_ref * (1 - sl_pct)
        tp_mc   = entry_mc_real * (1 + tp_pct) if entry_mc_real else None

        ticks = db.get_ticks_after(mint, min_ts=ref_ts)
        if not ticks:
            # Sem ticks (token fora da janela quente >60min) — pergunta direto ao GMGN.
            self._check_via_gmgn(trade, sol_usd, now)
            return

        for tick in ticks:
            high    = float(tick["mc_high"] or 0)
            low     = float(tick["mc_low"]  or 0)
            tick_ts = tick["tick_ts"]

            # TP/trailing: só detecta se temos o preço real de fill.
            # Sem entry_mc_real, tp_mc é None e este bloco nunca executa.
            if tp_mc and high >= tp_mc:
                self._record_exit(trade, "trailing_active", tp_pct, tick_ts, sol_usd)
                return
            if low > 0 and low <= sl_mc:
                self._record_exit(trade, "sl", -sl_pct, tick_ts, sol_usd)
                return

    def _check_via_gmgn(self, trade: dict, sol_usd: float, now: datetime) -> bool:
        """Fallback para trades sem token_ticks: consulta query_order direto no GMGN.

        Chama quando não há ticks (token fora da janela quente >60min).
        Verifica condition_orders do GMGN para detectar se TP/SL executou.
        Retorna True se atualizou o trade.
        """
        order_id = trade.get("order_id")
        if not order_id:
            return False

        trade_id   = trade["id"]
        symbol     = trade.get("symbol", trade["mint"][:8])
        amount_usd = trade.get("amount_usd") or 1.0
        fee_sol    = trade.get("fee_sol") or 0.0
        fill_sol   = trade.get("amount_sol") or 0.0
        fee_frac   = (fee_sol / fill_sol) if fill_sol > 0 else 0.0

        try:
            result = self.gmgn.query_order(order_id)
            data   = result.get("data") or result
            logger.debug("_check_via_gmgn #%d raw: %s", trade_id, str(data)[:400])

            # Condition orders no response do buy order (formato a confirmar em produção)
            co_list = data.get("condition_orders") or []
            for co in co_list:
                co_status = str(co.get("state") or co.get("status") or "").lower()
                co_type   = str(co.get("order_type") or co.get("type") or "").lower()
                if co_status not in ("filled", "completed", "triggered", "executed"):
                    continue

                is_tp = "profit" in co_type or "trailing" in co_type
                is_sl = "loss" in co_type or "stop" in co_type
                if not is_tp and not is_sl:
                    continue

                exit_type = "trailing" if is_tp else "sl"
                pnl_guess = TRAILING_ACTIVATION_PCT / 100 if is_tp else -TRADE_SL_PCT

                # Tenta calcular P&L real a partir do fill do condition order
                exit_raw = _safe_float(co.get("filled_input_amount") or co.get("out_amount"))
                exit_sol = (exit_raw / 1e9 if exit_raw and exit_raw > 1000 else exit_raw) or 0
                exit_usd = exit_sol * sol_usd if exit_sol > 0 else amount_usd * (1 + pnl_guess)
                pnl_real = (exit_usd / amount_usd) - 1 - fee_frac if amount_usd else pnl_guess

                status = "tp" if is_tp else "sl"
                db.update_trade(trade_id, {
                    "status":         status,
                    "exit_type":      exit_type,
                    "exit_ts":        now,
                    "exit_price_usd": exit_usd,
                    "pnl_pct_real":   pnl_real,
                })
                logger.info(
                    "TradeMonitor GMGN-poll #%d %s → %s | type=%s pnl=%.1f%%",
                    trade_id, symbol, status.upper(), co_type, pnl_real * 100,
                )
                return True

            # condition_orders vazio — estrutura desconhecida ou ainda pendentes.
            # Loga data completo uma vez por hora para diagnóstico.
            created_at = trade.get("created_at")
            if created_at:
                age_min = (now - created_at).total_seconds() / 60
                if int(age_min) % 60 == 0:
                    logger.info("_check_via_gmgn #%d sem condition_orders na resposta: %s",
                                trade_id, str(data)[:300])

        except Exception as e:
            logger.debug("_check_via_gmgn #%d: %s", trade_id, e)

        return False

    def _check_trailing_exit(self, trade: dict, sol_usd: float, now: datetime):
        """
        Para trades em trailing_active, simula o trailing stop via token_ticks:
        rastreia o pico de MC após ativação e detecta quando cai TRAILING_DRAWDOWN_PCT do pico.
        Atualiza status para 'tp' com P&L real estimado.
        """
        trade_id        = trade["id"]
        mint            = trade["mint"]
        symbol          = trade.get("symbol", mint[:8])
        entry_price_usd = trade.get("entry_price_usd")
        tokens_received = trade.get("tokens_received")
        amount_usd      = trade.get("amount_usd") or 1.0
        fee_sol         = trade.get("fee_sol") or 0.0
        fill_sol        = trade.get("amount_sol") or 0.0
        fee_frac        = (fee_sol / fill_sol) if fill_sol > 0 else 0.0

        if not entry_price_usd or not tokens_received or tokens_received <= 0:
            return
        entry_mc_real = (entry_price_usd / tokens_received) * 1_000_000_000

        # Referência: ts_order_sent ou exit_ts (quando trailing_active foi setado)
        ref_ts = trade.get("ts_order_sent") or trade.get("exit_ts") or trade.get("ts_signal")
        ticks  = db.get_ticks_after(mint, min_ts=ref_ts)
        if not ticks:
            self._check_via_gmgn(trade, sol_usd, now)
            return

        activation_mc = entry_mc_real * (1 + TRAILING_ACTIVATION_PCT / 100)
        peak_mc       = activation_mc
        drawdown_pct  = TRAILING_DRAWDOWN_PCT / 100

        for tick in ticks:
            high    = float(tick.get("mc_high") or tick.get("mc") or 0)
            low     = float(tick.get("mc_low")  or tick.get("mc") or 0)
            tick_ts = tick["tick_ts"]

            if high > peak_mc:
                peak_mc = high

            trailing_exit_mc = peak_mc * (1 - drawdown_pct)
            if low > 0 and low <= trailing_exit_mc:
                pnl_pct  = (trailing_exit_mc / entry_mc_real) - 1
                pnl_real = pnl_pct - fee_frac
                db.update_trade(trade_id, {
                    "status":         "tp",
                    "exit_type":      "trailing",
                    "exit_ts":        tick_ts,
                    "exit_price_usd": amount_usd * (1 + pnl_pct),
                    "pnl_pct_real":   pnl_real,
                })
                logger.info(
                    "TradeMonitor #%d %s — TRAILING EXIT | peak=$%.0f exit=$%.0f pnl=%.1f%%",
                    trade_id, symbol, peak_mc, trailing_exit_mc, pnl_real * 100,
                )
                return

    def _record_exit(self, trade: dict, exit_type: str, pnl_target: float,
                     exit_ts, sol_usd: float):
        """
        Registra saída no DB.

        exit_type interno: "trailing_active" | "sl"
        status gravado: "trailing_active" (TP detectado, GMGN gerencia trailing)
                        "sl" (SL detectado via token_ticks)
        P&L: pnl_target bruto − fee_frac. Negativo para SL, positivo para trailing.
        Para trailing_active, pnl_pct_real é o nível de ativação (+75%), não o exit real.
        O exit real depende do trailing do GMGN — pode ser maior ou menor.
        """
        trade_id   = trade["id"]
        symbol     = trade.get("symbol", trade["mint"][:8])
        amount_usd = trade.get("amount_usd") or 1.0

        # P&L líquido: deduz fee sobre o capital investido
        fee_sol  = trade.get("fee_sol") or 0.0
        fill_sol = trade.get("amount_sol") or 0.0
        fee_frac = (fee_sol / fill_sol) if fill_sol > 0 else 0.0
        pnl_real = pnl_target - fee_frac

        exit_usd = amount_usd * (1 + pnl_target)

        db.update_trade(trade_id, {
            "status":         exit_type,
            "exit_type":      exit_type,
            "exit_ts":        exit_ts,
            "exit_price_usd": exit_usd,
            "pnl_pct_real":   pnl_real,
        })
        logger.info(
            "TradeMonitor #%d %s — %s | pnl=%.1f%% (bruto=%.1f%% fee=%.3f%%)",
            trade_id, symbol, exit_type.upper(),
            pnl_real * 100, pnl_target * 100, fee_frac * 100,
        )


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
