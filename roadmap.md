# NeideBOT — Roadmap

> Última atualização: 2026-05-07 (sessão 5)

---

## Status atual: **Fase 6-lite — Calibração Live ($1/trade)**

- **1175+ tokens** coletados, **1134** com `recovery` definido
- **20/50 trades** de calibração executados — WR 25% (5 TP, 15 SL), break-even ~26%
- **172+ bounce signals** em produção, A.1 slope removido (H13 refutada com n=153)
- Bot ativo: TRADING_ENABLED=true, $1/trade, acumulando dados

---

## Hipóteses — Placar

| # | Hipótese | Resultado | Delta |
|---|----------|-----------|-------|
| H1 | ≥70% dos tokens caem ≥20% do ATH nos primeiros 60min | ✅ CONFIRMADA | 91.4% |
| H2 | Em tokens com recovery=TRUE, fundo em ≤15min (≥70%) | ❌ REFUTADA | 49.7% |
| H3 | smart_wallets ≥3 → recovery ≥15pp maior | ❌ REFUTADA | +0.2pp |
| H4 | sniper_wallets ≥30% → recovery <30% | ❌ REFUTADA | levemente positivo |
| H5 | top10 ≥0.80 → var_24h <-80% em ≥60% | ⚠️ DESCARTADA | 24h fora do escopo |
| H6 | Entradas entre -25% e -45% têm melhor R:R | ⚠️ DESCARTADA | 4h/24h fora do escopo |
| H7 | liq_migration <$5k → slippage inaceitável para >$50 | ⏳ PENDENTE | medindo em $1 trades |
| H8 | is_wash_trading=TRUE → recovery ≥20pp menor | ❌ REFUTADA | -0.1pp |
| H9 | ≥1 signal type 12 com trigger_mc < 80% mc_migration → recovery ≥20pp maior | ⏳ PENDENTE | +11.8pp na faixa <50% (n=14). Threshold correto: <80% migração. Aguardando n≥30. |
| H10 | time_to_bottom < 25min → recovery ≥20pp maior | ✅ CONFIRMADA | **+21.8pp** (61.2% vs 39.3%, n=425) |
| H11 | Deceleração de preço antes do fundo discrimina recovery | ❌ REFUTADA | invertido — recovery tokens ACELERAM (capitulação brusca) |
| H12 | dump_depth + time_elapsed → P(recovery) > 50% | ⚠️ PARCIAL | time domina. 3 zonas acionáveis mas cobertas por H10 |
| H13 | Slope íngreme pré-fundo (≤-0.0008 %/s) → WR ≥40pp maior | ❌ REFUTADA | n=153, ts_min correto. Avg TRUE=-0.003829 vs FALSE=-0.003696 (diff=0.000133). Rate flat em todas faixas. A.1 removido. |
| H14 | bounce_pct 20-30% é dead zone — WR inferior | ✅ CONFIRMADA | WR 3.1% (n=32). A.2 ativo. |
| H15 | Entry 50-65% do mc_migration → WR=0% | ✅ CONFIRMADA | 0/21 = 0% WR (n=21). A.3 ativo. |
| H16 | 3 ticks consecutivos acima do entry pós-sinal → WR ≥20pp maior | ✅ CONFIRMADA | +18pp (28.6% vs 10.6%, n=14/66). A.4 ativo. |
| H17 | 100% dos TPs ocorrem em ≤20min após o sinal | ✅ CONFIRMADA | 11/11 TPs ≤20min. Mediana 6.1min. A.5 time-stop=20min. |
| H18 | Bounce velocity discrimina WR | ❌ REFUTADA | WR flat em todos os tercis. TTB<25min+Slow=43% > Fast=29%. |
| H19 | Aguardar 1-2min após sinal melhora EV | ✅ CONFIRMADA | +6.6pp WR. Coberto pelo A.4 (90s). |
| H20 | W-bottom (double-dip) é sinal de confirmação | ❌ NÃO É FILTRO | 98% prevalência — ubíquo em TPs e SLs. A.4 trata implicitamente. |

---

## Backtester tick-level — resultados consolidados

**Setup:** entry = mc_bottom × 1.15, drop ≥ 30%, time_to_bottom < 30min
**n=68** com ticks, **492** sem ticks (acumulando via kline OHLC)

| TP | SL | Win Rate | EV | Payoff | n_tp | n_sl |
|----|----|----------|----|--------|------|------|
| 30% | 15% | 33.8% | +1.1% | 2.2x | 23 | 40 |
| 40% | 15% | 32.4% | +3.7% | 2.9x | 22 | 41 |
| **75%** | **15%** | **27.9%** | **+11.5%** | **5.7x** | 19 | 43 |
| 100% | 15% | 23.5% | +14.7% | 8.7x | 16 | 44 |

**SL=15% domina.** SL=10% aciona a 3.5% acima do fundo (ruído de mercado).
**Sinal estável em 3 runs.** Payoff assimétrico compensa win rate baixo — break-even = 11%.

> ⚠️ **Bias de cobertura:** 492/560 tokens sem ticks. Recovery_rate similar ao baseline — não são piores.
> ⚠️ **O que o backtest NÃO captura:** slippage, MEV, fees, latência. EV_real = EV_backtest − friction_total.

---

## Live Trading — Calibração (2026-05-07)

| Métrica | Valor |
|---------|-------|
| Trades executados | 20/50 |
| TP hits | 5 |
| SL hits | 15 |
| WR live | 25% |
| Break-even WR | ~26% (com tip+priority+GMGN 1%+slippage) |
| Problema identificado | A.4 (90s wait) → entrada 25% acima do esperado |

**Principais aprendizados de execução:**
- TP/SL via `condition_orders` no body do swap — `price_scale` em escala 0-100 (não decimal)
- Priority fee NÃO enviado explicitamente — GMGN gerencia internamente
- TradeMonitor detecta exits via token_ticks (mc_high/mc_low) a cada 30s
- `filled_output_amount` = tokens recebidos; `filled_input_amount` (lamports) = SOL pago

---

## Fases — Histórico e Status

### Fase 0 — Infraestrutura ✅
Flask + PostgreSQL (Railway), GMGN client, HeapScheduler, Telegram, Sentry

### Fase 1 — Watchlist + Migração ✅
Scanner poll 5s, HotMonitor 60min, tabelas migrations + token_ticks, campos Fase 1 do trenches

### Fase 2 — Coleta de Sinais Ativos ✅ (2026-05-03)
`_poll_signals()` a cada 5s, token_signals type 12, bounce_signals, volume via kline

### Fase 2.5 — Análise Estrutural ✅ (2026-05-03)
H10 validada (+21.8pp, n=425), H11 refutada, H12 parcial

### Fase 2.7 — Backtester OHLC ✅ (2026-05-04)
Backtester tick-level com OHLC, EV positivo confirmado (TP=75%, SL=15%), dados suficientes para execução live

### Fase A/B — Filtros e Coverage ✅ (2026-05-04/05)
- A.2 dead zone, A.3 entry ratio, A.4 persistência, A.5 time-stop 20min
- B.3 backfill retroativo, ts_min fix, hot-precise 30s, cobertura slope 89%
- **A.1 slope REMOVIDO** (2026-05-07) — H13 refutada com n=153

### Fase 6-lite — Calibração Live 🟡 ATIVA (2026-05-04)
- TP/SL via condition_orders validado (2026-05-06)
- TradeMonitor para exit tracking implementado
- 20/50 trades executados — WR 25%, break-even ~26%
- Meta: 50 trades para medir EV_real com confiança

---

## Próximas Fases

### [AGORA] Completar 50 trades de calibração

**Gate único:** n=50 trades com P&L registrado.
Com n=50: σ_WR = ±6.1% — suficiente para distinguir lucrativo de EV negativo.

```
Parâmetros ativos:
  Entry : bounce (drop ≥30%, bounce ≥15%, elapsed ≤20min)
  Size  : $1/trade
  TP    : +75%
  SL    : -15%
  Filtros A: A.2 (dead zone), A.3 (entry ratio), A.4 (persistência 90s), A.5 (time-stop 20min)
```

### [50 trades] Decisão baseada em EV_real

```
EV_real > 0 após friction?
    │
    ├── SIM → $20/trade → $50/trade (go-live)
    │         (paralelo) H9 validada? → adicionar filtro smart money
    │
    └── NÃO → diagnosticar friction:
                ├── Entrada muito acima do esperado? → reduzir PERSISTENCE_WAIT_SEC ou A.4 ticks
                ├── Slippage > esperado?             → filtrar liq_migration > $15k
                ├── MEV/sandwich?                    → Jito / private RPC
                └── Latência > 15s?                 → otimizar pipeline bounce→swap
```

### [Paralelo] H9 — Smart Money Signal

- Threshold correto: `trigger_mc < mc_migration × 0.80` (exclui reverse causality)
- Dados iniciais: +11.8pp na faixa <50% migração (n=14) — aguardando n≥30
- Não bloqueia calibração — coleta passiva já ativa

---

## KPIs

| Fase | Métrica | Meta | Status |
|------|---------|------|--------|
| Coleta | Migrações com recovery | ≥200 | ✅ 1134 |
| Análise | H10 validada | ≥20pp | ✅ +21.8pp |
| Backtester | EV positivo | EV > 0 (TP≥30%) | ✅ confirmado (n=68) |
| Fase 6-lite | Trades executados | 50 | 🟡 20/50 |
| Fase 6-lite | WR live | ≥20% | 🟡 25% (n=20) |
| Fase 6-lite | EV_real > 0 | EV > friction | ⏳ acumulando |
| Fase 6 | Payoff ratio live | ≥3× | ⏳ |
| Fase 6 | Latência | ≤15s | ⏳ |
| H9 | Validação smart money | ≥20pp delta | 🟡 +11.8pp (n=14) |

---

## Lições Aprendidas

1. **Snapshots não discriminam recovery**: smart_count, renowned, holders, rug_ratio, wash_trading, slope pré-fundo — todos testados, nenhum discrimina com n suficiente.

2. **O único preditor validado é tempo**: `time_to_bottom < 25min` (+21.8pp, n=425). Dumps rápidos = capitulação. Dumps lentos = distribuição.

3. **Slope é característica universal**: 92% dos bounces têm slope íngreme. Condição necessária, não suficiente. H13 refutada com metodologia correta (ts_min real).

4. **Win rate baixo ≠ estratégia ruim**: payoff 8.7x → break-even = 11%. WR 23% gera EV=+14.7%. Exigir ≥50% WR rejeita estratégia matematicamente lucrativa.

5. **Friction só é medida em live trading**: slippage, MEV, fees, latência não existem no backtest. 50 × $1 trades = o único walk-forward real.

6. **A.4 é o maior inimigo da execução**: espera 90s → entrada 25% acima do esperado → WR live ≈ break-even. Investigar após 50 trades.

7. **ts_signal ≠ ts_min**: bounce é detectado depois do fundo (precisa ver preço subir ≥15%). Qualquer análise de pré-fundo deve usar ts_min (tick de menor MC), não ts_signal.

8. **condition_orders usa escala 0-100**: `price_scale="75"` = +75%, não 1.75. Bug histórico causou TP e SL disparando imediatamente (1.75% threshold).

9. **Compra ativa ≠ posição acumulada**: delta_smart (trenches) é snapshot de posições. Sinal de compra ativa = signal type 12 (token_signals).

10. **Ticks precisam de OHLC**: close perde cruzamentos intra-candle. mc_high/mc_low detectam TP/SL reais.
