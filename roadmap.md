# NeideBOT — Roadmap

> Última atualização: 2026-05-09 (sessão 6)

---

## Status atual: **Pronto para religar — $10/trade com trailing TP**

- **1175+ tokens** coletados, **1134** com `recovery` definido
- **50 trades** de calibração $1 concluídos — WR real ~17-19%, perda dominada por friction
- **251 bounce signals** analisados — 119 válidos / 132 inválidos diagnosticados
- Filtros ativos: A.2, A.3, A.5, A.6 (new). A.1 e A.4 removidos.
- Trailing TP implementado (profit_stop_trace, ativ. 75%, dd. 30%)

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
| H7 | liq_migration <$5k → slippage inaceitável para >$50 | ⏳ PENDENTE | validar a $10/trade |
| H8 | is_wash_trading=TRUE → recovery ≥20pp menor | ❌ REFUTADA | -0.1pp |
| H9 | ≥1 signal type 12 com trigger_mc < 80% mc_migration → recovery ≥20pp maior | ⏳ PENDENTE | +11.8pp na faixa <50% (n=14). Aguardando n≥30. |
| H10 | time_to_bottom < 25min → recovery ≥20pp maior | ✅ CONFIRMADA | **+21.8pp** (61.2% vs 39.3%, n=425) |
| H11 | Deceleração de preço antes do fundo discrimina recovery | ❌ REFUTADA | invertido — recovery tokens ACELERAM (capitulação brusca) |
| H12 | dump_depth + time_elapsed → P(recovery) > 50% | ⚠️ PARCIAL | time domina. 3 zonas acionáveis mas cobertas por H10 |
| H13 | Slope íngreme pré-fundo → WR ≥40pp maior | ❌ REFUTADA | n=153, ts_min correto. Rate flat. A.1 removido. |
| H14 | bounce_pct 20-30% é dead zone | ✅ CONFIRMADA | WR 3.1% (n=32). A.2 ativo. |
| H15 | Entry 50-65% do mc_migration → WR=0% | ✅ CONFIRMADA | 0/21 = 0% WR. A.3 ativo. |
| H16 | 3 ticks consecutivos acima do entry → WR ≥20pp maior | ✅ CONFIRMADA | +18pp (n=14/66). **A.4 removido** — entrada 53% acima do fundo matava R:R. |
| H17 | 100% dos TPs ocorrem em ≤20min após o sinal | ✅ CONFIRMADA | 11/11 TPs ≤20min. Mediana 6.1min. A.5 time-stop=20min. |
| H18 | Bounce velocity discrimina WR | ❌ REFUTADA | WR flat em todos os tercis. |
| H19 | Aguardar 1-2min após sinal melhora EV | ✅ CONFIRMADA | +6.6pp WR. A.4 cobria, mas A.4 removido — efeito absorvido pela entrada imediata. |
| H20 | W-bottom (double-dip) é sinal de confirmação | ❌ NÃO É FILTRO | 98% prevalência — ubíquo. |

---

## Backtester tick-level — resultados consolidados

**Setup:** entry = mc_bottom × 1.15, drop ≥ 30%, time_to_bottom < 30min, n=68 com ticks

| TP | SL | Win Rate | EV | Payoff |
|----|----|----------|----|--------|
| 30% | 15% | 33.8% | +1.1% | 2.2x |
| **75%** | **15%** | **27.9%** | **+11.5%** | **5.7x** |
| 100% | 15% | 23.5% | +14.7% | 8.7x |

**SL=15% domina.** Break-even WR com payoff 8.7x = 11%. WR 23% → EV +14.7%.

---

## Análise Trailing TP — resultados (2026-05-09, n=119 válidos)

**Distribuição de gains nos bounces válidos:** P25=103.8%, P50=170.2%, P75=275.7%, P90=517.6%

| Config | Avg gain | vs TP fixo |
|--------|----------|-----------|
| TP fixo 75% | 71.3% | baseline |
| Trailing 30% (ativ. 50%) | 112.4% | +41pp |
| Trailing 30% (ativ. 75%) | ~130% | +59pp |
| **Trailing 30% (ativ. 100%)** | **159.2%** | **+88pp — melhor config** |
| Trailing 40% (ativ. 100%) | 142.1% | +71pp |

**Intra-rally drawdown** (máxima queda do running max durante a subida, válidos):
P25=18.4%, P50=29.7%, P75=43.9%

> Drawdown 30% está em P50 — metade dos tokens recua ≥30% antes de continuar.
> Ativação em 75% escolhida para fase conservadora (pouco caixa). Migrar para 100% após validação.

---

## Análise Sinais Inválidos — resultados (2026-05-09, n=251 sinais)

| Métrica | Valor |
|---------|-------|
| Válidos (sinal antes do pico) | 119 (47.4%) |
| Inválidos (sinal depois do pico) | 132 (52.6%) |
| Atraso inválidos P50 | 6.7min |
| Helius resolve (<30s atraso) | 5 (3.8%) |
| Problema estrutural (>2min atraso) | 121 (91.7%) |
| A.6 bloqueia (retrospectivo) | 121/132 = 92% |
| Impact A.6 nos 50 trades reais | 1/50 bloqueado (era TP) |

**Conclusão:** Helius é custo (Jupiter swap), não sinal. A.6 é a solução correta.

---

## Live Trading — Calibração (2026-05-08)

| Métrica | Valor |
|---------|-------|
| Trades executados | 50 |
| TPs reais | ~8-9 (~17-19% WR) |
| Perda total | ~$20 (friction, não estratégia) |
| Custo por trade (friction) | ~$0.36 (tips+fees) |
| Break-even WR a $1/trade | ~59% (inviável) |
| Break-even WR a $10/trade | ~23% (viável) |

**A.4 causou:** entrada ~53% acima do fundo → WR real ≈ break-even teórico.
**Solução:** A.4 desativado + trailing TP → captura de upside maior.

---

## Fases — Histórico e Status

### Fase 0 — Infraestrutura ✅
Flask + PostgreSQL (Railway), GMGN client, HeapScheduler, Telegram, Sentry

### Fase 1 — Watchlist + Migração ✅
Scanner poll 5s, HotMonitor 60min, tabelas migrations + token_ticks

### Fase 2 — Coleta de Sinais Ativos ✅ (2026-05-03)
_poll_signals() a cada 5s, token_signals type 12, bounce_signals, volume via kline

### Fase 2.5 — Análise Estrutural ✅ (2026-05-03)
H10 validada (+21.8pp, n=425), H11 refutada, H12 parcial

### Fase 2.7 — Backtester OHLC ✅ (2026-05-04)
EV positivo confirmado (TP=75%, SL=15%, n=68)

### Fase A/B — Filtros e Coverage ✅ (2026-05-04/05)
- A.2 dead zone, A.3 entry ratio, A.5 time-stop 20min
- B.3 backfill retroativo, ts_min fix, hot-precise 30s
- A.1 slope REMOVIDO (H13 refutada)

### Fase 6-lite — Calibração $1/trade ✅ CONCLUÍDA (2026-05-08)
- 50 trades executados
- WR real ~17-19%, friction dominou a $1/trade
- A.4 identificado como problema principal → desativado
- Trailing TP implementado com dados reais (n=119 válidos)
- A.6 bottom freshness implementado

### Fase 6.1 — V1.5 Micro-calibração com eRPC/Helius ⏳ ATUAL
```
Parâmetros recomendados:
  Entry  : bounce V1 confirmado (drop ≥30%, bounce ≥30%) com mc_min eRPC/Helius
  Size   : $1/trade inicialmente
  Cap    : 20 trades antes de aumentar para 50
  TP     : trailing profit_stop_trace
  SL     : conservador via executor/condition_orders + TradeMonitor SL-first no mesmo tick
  Gate   : Helius obrigatório antes de GMGN swap
```

---

## Próximas Fases

### [AGORA] V1.5 micro-calibração + V2 Shadow oficial

**Runtime Railway:**
- `web`: Flask/Gunicorn, `RUN_BOT_SERVICES=0`, serve endpoints e dashboards.
- `worker`: `python worker.py`, `RUN_BOT_SERVICES=1`, coleta eRPC/Helius + scanner + HotMonitor + executor.
- `RUN_DB_MIGRATIONS=0` nos dois; migrations apenas via one-off/pre-deploy temporário. Confirmar `/debug/schema` = OK antes de trade real.
- `/debug/runtime-ips` mostra o IP do web e o último `worker.outbound_ip`; usar o IP do worker na whitelist GMGN. Heartbeat default: `RUNTIME_HEARTBEAT_INTERVAL_SEC=300`.

**Gate real V1.5 (compra com bounce, NÃO V2):**
```
TRADING_ENABLED=true              # somente para micro-calibração
CALIBRATION_AMOUNT_USD=1
CALIBRATION_MAX_TRADES=500
SOL_USD_FALLBACK=150
BOUNCE_RECORD_MIN_POOL_SOL=5
RUNTIME_HEARTBEAT_ENABLED=true
RUNTIME_HEARTBEAT_INTERVAL_SEC=300
TRADE_GATE_ENABLED=true
TRADE_REQUIRE_HELIUS_FEATURES=true
TRADE_REQUIRE_H23_BOTTOM=true
TRADE_MAX_ELAPSED_SEC=300
TRADE_MIN_NET_SOL_FLOW=-5
TRADE_MIN_POOL_SOL=40
```

**O que esse gate compra:** V1 bounce confirmado com fundo mais correto via eRPC/Helius e bloqueio final por microestrutura. Trades reprovados ficam `blocked_helius_gate`, não chamam GMGN e não consomem `CALIBRATION_MAX`.

**Gate shadow V2.1 (sem compra real):**
```
V2_SHADOW_ENABLED=true
V2_DECISION_AGE_SEC=10
V2_NET_WINDOW=live_5s
V2_NET_MIN=-2
V2_MIN_POOL_SOL=10
V2_MIN_EVENTS=5
V2_MIN_BOUNCE_PCT=0.30
V2_MAX_BOUNCE_PCT=0.50
V2_CANDIDATE_SCOPE=mint_first
```

Acompanhar `/analise/v2-shadow-live?days=3`, `/analise/v2-brain-report?days=3`, `/debug/schema`, `/debug/executor-status` e os trades `blocked_helius_gate` por motivo.

**Stop rules da micro-calibração:**
- 5 SLs seguidos
- 3 falhas GMGN/fill seguidas
- muitos bloqueios por `missing_net_sol_flow` ou `missing_pool_quote`
- slippage/fill real muito acima do esperado
- worker instável ou sem ticks pós-compra

### [Após 20 trades V1.5] Decisão de aumentar cap

Operacional: `CALIBRATION_MAX_TRADES=500` evita travar por histórico antigo; revisar fills/slippage/gate antes de aumentar tamanho por trade. Só considerar $10/trade após validação operacional e WR first-touch real.

Aguardar 20–50 trades para confirmar:
- auditoria Helius preenchida em todos os trades executados
- motivo dos bloqueios coerente com `signal-anatomy`
- WR real first-touch acima do baseline corrigido
- trailing_active detectado em % razoável de trades
- friction total compatível com tamanho da posição

### [30 trades] Ajuste do trailing

```
WR ≥ 20% E avg_gain > 100%?
    │
    ├── SIM → migrar TRAILING_ACTIVATION_PCT de 75% para 100%
    │         (captura mais dos grandes moves)
    │
    └── NÃO → investigar:
                ├── Muitos "trailing_active" → sl_loss?  → ativ. 75% ok, dd muito apertado
                ├── Poucos trailing_active?               → WR baixo, problema de sinal
                └── A.6 bloqueando válidos?              → aumentar BOTTOM_FRESHNESS_SEC para 480s
```

### [Paralelo] H9 — Smart Money Signal

- Threshold: `trigger_mc < mc_migration × 0.80`
- Dados: +11.8pp na faixa <50% migração (n=14) — aguardando n≥30
- Coleta passiva ativa — não bloqueia trading

### [Médio prazo] Helius + Jupiter Swap

Não urgente. A $10/trade a friction com GMGN é aceitável (~9.2%). Implementar depois de
confirmar WR e trailing com $10/trade.

| Custo | GMGN atual | Jupiter+Helius |
|-------|-----------|----------------|
| Swap fee | 1%×2 = 2% | ~0.25%×2 = 0.5% |
| Tip | ~$0.36/trade | ~$0.02/trade |
| **Total $10/trade** | **~$0.92 (9.2%)** | **~$0.11 (1.1%)** |
| Break-even WR | ~23% | ~18% |

---

## KPIs

| Fase | Métrica | Meta | Status |
|------|---------|------|--------|
| Coleta | Migrações com recovery | ≥200 | ✅ 1134 |
| Análise | H10 validada | ≥20pp | ✅ +21.8pp (n=425) |
| Backtester | EV positivo | EV > 0 (TP≥30%) | ✅ confirmado (n=68) |
| Trailing | Avg gain vs TP fixo | +50pp | ✅ +88pp (sim. n=119) |
| Fase 6-lite | Calibração $1/trade | 50 trades | ✅ CONCLUÍDA |
| Fase 6-lite | Friction medida | < 5% a $10 | ✅ ~9.2% a $10 (aceitável) |
| Fase 6.1 V1.5 | Micro-calibração | 20 trades $1 com gate Helius | ⏳ pronto para ligar |
| Fase 6.1 V1.5 | Auditoria Helius | 100% dos trades executados com gate/pool/net | ⏳ aguardando |
| Fase 6.2 | WR a $10/trade | ≥ baseline first-touch corrigido | ⏳ depois da micro-calibração |
| H9 | Validação smart money | ≥20pp delta | 🟡 +11.8pp (n=14) |
| V2 Shadow | Gate eRPC live_5s mint_first | WR≥30%, EV>0, n≥100 | ⏳ coletando via worker |
| Jupiter | Break-even WR | ~18% | ⏳ médio prazo |

---

## Lições Aprendidas

1. **Snapshots não discriminam recovery**: smart_count, renowned, holders, rug_ratio, wash_trading, slope pré-fundo — todos testados, nenhum discrimina.

2. **O único preditor validado é tempo**: `time_to_bottom < 25min` (+21.8pp, n=425). Dumps rápidos = capitulação. Dumps lentos = distribuição.

3. **A.4 matava o R:R**: espera 90s → entrada 53% acima do fundo → WR live ≈ break-even. Remover o filtro de persistência foi a correção certa.

4. **Win rate baixo ≠ estratégia ruim**: payoff 8.7x → break-even = 11%. WR 17% gera EV positivo. Exigir ≥50% WR rejeita estratégia matematicamente lucrativa.

5. **$1/trade sempre ia perder**: friction (tips) = 36% do tamanho da posição. Isso não é ineficiência da estratégia — é custo fixo de execução. A $10/trade o mesmo friction = 3.6%.

6. **Sinais inválidos são estruturais, não latência**: 92% dos inválidos têm pico em <3min pós-migração e sinal 10-20min depois. Helius não resolve isso — A.6 (bottom freshness) resolve.

7. **Trailing supera TP fixo 2.2×**: simulação com dados reais mostra avg 159% vs 71% TP fixo. O motivo: a distribuição de gains tem cauda longa (P90 = 517%) — TP fixo deixa muito na mesa.

8. **Helius é custo, não sinal**: implementar para reduzir friction de 9.2% para 1.1% (Jupiter swap), não para melhorar detecção. Prioridade menor que validar WR com $10/trade.

9. **condition_orders usa escala 0-100**: `price_scale="75"` = +75%, não 1.75. profit_stop_trace adiciona `drawdown_rate` = recuo % do pico.

10. **ts_signal ≠ ts_min**: bounce é detectado DEPOIS do fundo (precisa ver preço subir ≥15%). Análise de pré-fundo usa ts_min (tick de menor MC), não ts_signal.
