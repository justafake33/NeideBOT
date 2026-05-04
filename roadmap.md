# NeideBOT — Roadmap

> Última atualização: 2026-05-04 (sessão 3)

---

## Status atual: **Fase 2.7 — Backtester OHLC + Kline Ticks**

- **1015+ tokens** coletados, **425** com `recovery` definido
- Backtester tick-level operacional: TP=75%, SL=15% → EV=+11.5% | TP=100%, SL=15% → EV=+14.7%
- Kline ticks com OHLC (mc_open/mc_high/mc_low) implementados — cobertura expandindo
- Bounce detector ativo em produção: `bounce_signals` populando, alertas Telegram ao vivo
- Sinais type 12 coletados em `token_signals` (H9 acumulando passivamente)

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
| H7 | liq_migration <$5k → slippage inaceitável para >$50 | ⏳ PENDENTE | será medido em $1 trades |
| H8 | is_wash_trading=TRUE → recovery ≥20pp menor | ❌ REFUTADA | -0.1pp |
| H9 | ≥1 signal type 12 com trigger_mc < mc_migration → recovery ≥20pp maior | ⏳ PENDENTE | acumulando passivamente |
| H10 | time_to_bottom < 25min → recovery ≥20pp maior | ✅ CONFIRMADA | **+21.8pp** (61.2% vs 39.3%, n=425) |
| H11 | Deceleração de preço antes do fundo discrimina recovery | ❌ REFUTADA | invertido — recovery tokens ACELERAM (capitulação brusca) |
| H12 | dump_depth + time_elapsed → P(recovery) > 50% | ⚠️ PARCIAL | time domina. 3 zonas acionáveis mas cobertas por H10 |

### Backtester tick-level — resultados consolidados

**Setup:** entry = mc_bottom × 1.15, drop ≥ 30%, time_to_bottom < 30min
**n=68** com ticks (expandindo via kline OHLC), **492** sem ticks (acumulando)

| TP | SL | Win Rate | EV | Payoff | n_tp | n_sl |
|----|----|----------|----|--------|------|------|
| 15% | 15% | 39.7% | -2.5% | 1.1x | 27 | 38 |
| 30% | 15% | 33.8% | +1.1% | 2.2x | 23 | 40 |
| 40% | 15% | 32.4% | +3.7% | 2.9x | 22 | 41 |
| 50% | 15% | 27.9% | +4.5% | 3.8x | 19 | 43 |
| **75%** | **15%** | **27.9%** | **+11.5%** | **5.7x** | 19 | 43 |
| **100%** | **15%** | **23.5%** | **+14.7%** | **8.7x** | 16 | 44 |

**SL=15% domina em todos os TP.** SL=10% aciona a 3.5% acima do fundo (ruído). SL=15% exige retorno abaixo do próprio fundo para disparar.

**Sinal estável em 3 runs consecutivos.** Direção confirmada: payoff assimétrico compensa win rate baixo.

> ⚠️ **Bias de cobertura:** 492/560 tokens sem ticks. Os sem ticks têm recovery_rate similar (57.7%) ao baseline (52%) — não são piores, apenas sem cobertura histórica de ticks. Kline OHLC resolvendo progressivamente.

> ⚠️ **O que o backtest NÃO captura:** taxas de swap, priority fee, slippage, MEV/sandwich, latência de execução. EV_real = EV_backtest - friction_total. Friction só mensurável em live trading.

---

## Fases — Histórico e Status

### Fase 0 — Infraestrutura ✅
- Flask + PostgreSQL (Railway), GMGN client, HeapScheduler, Telegram, Sentry

### Fase 1 — Watchlist + Migração ✅
- Scanner poll 5s, HotMonitor 60min, tabelas migrations + token_ticks
- Campos Fase 1 (qualidade) coletados do trenches

### Fase 2 — Coleta de Sinais Ativos ✅ (2026-05-03)
- `_poll_signals()` a cada 5s: token_signals com type 12 (SmartDegenBuy)
- `bounce_signals`: detector de bounce em tempo real, alertas Telegram
- Volume fix: `backfill_tick_volumes()` via kline 1m

### Fase 2.5 — Análise Estrutural ✅ (2026-05-03)
- H10 validada formalmente (+21.8pp, n=425)
- H11 refutada: recovery = capitulação brusca, não deceleração
- H12 parcial: time domina sobre dump depth

### Fase 2.7 — Backtester OHLC + Kline Ticks ✅ (2026-05-04)
- Backtester tick-level com OHLC: mc_open/mc_high/mc_low por vela
- Kline ticks inseridos a cada 60s no `_precise_bottom_tick()` — cobertura independente do feed trenches
- Desempate intra-vela: mc_open vs midpoint SL/TP quando ambos cruzados
- Sinal confirmado: TP=75-100%, SL=15%, EV positivo estável
- **Conclusão:** dados suficientes para iniciar execução live com capital mínimo

---

## Próximas Fases

### Fase 6-lite — Calibração Live com $1/trade ⏳ (AGORA)

**Por que pular Fase 4 e 5 do roadmap original:**
- Fase 4 (regras de entrada) já está definida pelo backtester — não precisa de H9 para começar
- Fase 5 (walk-forward) tinha critério errado: **win rate ≥50% é inválido para estratégias assimétricas.** Com payoff 8.7x, o break-even é win rate de 11%. Exigir 50% rejeitaria uma estratégia matematicamente lucrativa.
- **$1 trades são o walk-forward real** — medem friction que nenhum backtest consegue capturar

**Objetivo:** medir friction total real, não gerar lucro.

**Regras de entrada (fixas para calibração):**
```
Entry trigger : bounce_signals dispara (drop ≥ 30%, bounce ≥ 15%, elapsed ≤ 30min)
Size          : $1 por trade (50 trades de calibração)
TP            : +75%
SL            : −15%
Execução      : POST /v1/trade/swap → POST /v1/trade/strategy/create
```

**O que medir em cada trade:**
- Entry esperado (mc_bottom × 1.15) vs fill real → slippage
- Fees totais (priority + swap fee)
- Latência: ts_bounce_signal → ts_order_confirmed
- P&L real vs P&L esperado pelo backtest

**Dependências técnicas:**
1. `GMGN_PRIVATE_KEY` configurado no Railway (Ed25519 PEM)
2. Header `X-Signature` no GmgnClient (assina subpath+params+body+timestamp)
3. `POST /v1/trade/swap` com slippage config
4. `POST /v1/trade/strategy/create` para TP/SL automático
5. `GET /v1/trade/query_order` para monitorar status
6. Tabela `trades` no banco para log completo de cada trade

### Fase 6 — Scale Up ⏳ (após 50 trades de calibração)

**Gate único:** `EV_real > 0` após friction, calculado sobre os 50 trades de calibração.

```
friction_total = EV_backtest - EV_real_$1trades
EV_real_escala = EV_backtest - friction_total

Se EV_real_escala > 0:
  → $20/trade (50 trades)
  → validar: EV_real_$20 ≈ EV_real_$1 (friction não escala?)
  → $50/trade (go-live)

Se EV_real_escala ≤ 0:
  → diagnosticar: slippage? MEV? latência? liq_migration baixa?
  → aplicar filtro específico e re-testar
```

**KPIs corretos para estratégia assimétrica:**
| Métrica | Meta | Racional |
|---------|------|---------|
| Win rate | ≥20% | Break-even com 8.7x payoff = 11% — meta conservadora |
| Payoff ratio | ≥3× | Confirmado no backtest (5.7-8.7x) |
| EV por trade | > friction_total | Único critério que importa |
| Latência | ≤15s | Detecção → ordem confirmada |
| Divergência real vs backtest | ≤30% após 50 trades | Calibração aceita |
| Max drawdown | ≤40% do capital alocado | Stop total |

### Fase 6b — H9 como filtro adicional ⏳ (paralelo passivo)

**Gate:** ~100 tokens com sinais em `token_signals`

- H9 CONFIRMADA (+20pp) → adicionar `trigger_mc < mc_migration` como filtro de entrada
- H9 REFUTADA → manter sistema sem filtro de smart money
- Não bloqueia o início dos $1 trades — roda em paralelo

---

## Decision Gates

```
[AGORA] Fase 6-lite — $1 trades (calibração de friction)
    │   Implementar: X-Signature, swap, strategy/create, tabela trades
    │   50 trades com bounce_signals → medir EV_real
    │
    ▼
[50 trades] EV_real > 0 após friction?
    │
    ├── SIM → $20/trade → $50/trade (go-live)
    │           │
    │           │  (paralelo) H9 acumulando
    │           │
    │           ├── H9 CONFIRMADA → adicionar filtro smart money
    │           └── H9 REFUTADA   → sistema sem filtro (já lucrativo)
    │
    └── NÃO → diagnosticar friction:
                ├── Slippage > esperado? → filtrar liq_migration > $15k
                ├── MEV/sandwich?        → Jito / private RPC
                ├── Latência > 15s?      → otimizar pipeline bounce→swap
                └── Priority fee alto?   → ajustar priority_fee config
```

---

## Arquitetura de Dados — Estado Atual

| Tabela | Linhas | Completude |
|--------|--------|-----------|
| `migrations` | 1015+ | recovery definido em 425 (42%) |
| `token_ticks` | crescendo | OHLC via kline: cobertura expandindo de 12% para ~70-80% |
| `token_signals` | crescendo | ~100 tokens meta para H9 |
| `bounce_signals` | crescendo | populando em produção |
| `trades` | 0 | a criar na Fase 6-lite |

---

## Lições Aprendidas

1. **Snapshots não discriminam recovery**: smart_count, renowned_count, holders, rug_ratio, wash_trading — todos testados, nenhum discrimina.

2. **O único preditor validado é tempo**: `time_to_bottom < 30min` (+22pp). Dumps rápidos = capitulação. Dumps lentos = distribuição.

3. **Volume estava faltando**: fix aplicado via kline. Dados futuros preenchidos.

4. **Compra ativa ≠ posição acumulada**: delta_smart é invertido. Sinal correto = signal type 12.

5. **Metodologia > Features**: dataset limpo e hipóteses formais antes de qualquer execução.

6. **Invertido ≠ inútil**: H11 revelou capitulação brusca (slope_c −0.171 vs −0.107).

7. **Win rate baixo ≠ estratégia ruim**: com payoff 8.7x, win rate de 23% gera EV=+14.7%. Exigir ≥50% win rate é o critério errado para estratégias assimétricas.

8. **Walk-forward backtest ≠ validação real**: $1 trades capturam friction (slippage, MEV, fees, latência) que nenhum backtest consegue modelar. São mais valiosos que mais análise.

9. **Ticks precisam de OHLC**: só o close perde cruzamentos intra-candle de SL/TP. mc_high/mc_low detectam eventos reais que o close mascara.

10. **Caminho importa mais que endpoint**: mode=full (migrations apenas) mostrou avg_loss=+29% porque ignora o path. Token pode subir a mc_1h mas ter acionado SL no meio. Tick-level é o único modelo válido.
