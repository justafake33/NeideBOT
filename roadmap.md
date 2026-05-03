# NeideBOT — Roadmap

> Última atualização: 2026-05-03 (sessão 2)

---

## Status atual: **Fase 2 — Coleta de Dados Ativos**

- **1015+ tokens** coletados, **425** com `recovery` definido
- Campos Fase 1 (qualidade) coletados desde deploy anterior
- Sinais type 12 (SmartDegenBuy) coletados em `token_signals` a cada 5s
- Volume fix aplicado: `volume_1m` back-fillado via kline 1m no hot-precise

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
| H7 | liq_migration <$5k → slippage inaceitável para >$50 | ⏳ PENDENTE | validar em execução |
| H8 | is_wash_trading=TRUE → recovery ≥20pp menor | ❌ REFUTADA | -0.1pp |
| H9 | ≥1 signal type 12 com trigger_mc < mc_migration → recovery ≥20pp maior | ⏳ PENDENTE | aguardando ~100 tokens |
| H10 | time_to_bottom < 25min → recovery ≥20pp maior | ✅ CONFIRMADA | **+21.8pp** (61.2% vs 39.3%, n=425) |
| H11 | Deceleração de preço antes do fundo discrimina recovery | ❌ REFUTADA | direção invertida — recovery tokens ACELERAM |
| H12 | dump_depth + time_elapsed → P(recovery) > 50% (zona de entrada probabilística) | ⏳ PENDENTE | próxima análise |

### Análises de features — resultados documentados

| Feature | avg recovery | avg no_rec | Conclusão |
|---------|-------------|-----------|-----------|
| delta_smart (início→fundo) | 1.07 | 2.27 | ❌ invertido |
| delta_renowned (KOLs) | 0.44 | 0.65 | ❌ invertido |
| delta_combined (smart+KOL) | 1.51 | 2.92 | ❌ invertido |
| delta_holders | 15.46 | 15.31 | ❌ flat |
| vol_spike / vol5m_slope | null | null | ⏳ fix aplicado, dados futuros |
| **time_to_bottom** | **19.5 min** | **40.2 min** | ✅ **maior discriminador** |
| **slope_c** (2min antes fundo) | **-0.171** %MC/s | -0.107 %MC/s | ⚡ invertido útil — capitulação brusca |

> **Capitulação brusca ≠ deceleração:** recovery tokens têm spike de queda 60% mais intenso nos 2 min antes do fundo. Não é sinal de compra — é confirmação de capitulação. Observável em tempo real via push_mc. Bias: análise apenas sobre tokens com fundo ≥10min (n=49); tokens de fundo rápido (maior recovery, H10) excluídos por densidade de ticks.

---

## Fases — Histórico e Status

### Fase 0 — Infraestrutura ✅
- Flask + PostgreSQL (Railway)
- GMGN client com rate-limit handling
- HeapScheduler (thread única, sem threading.Timer)
- Telegram alerts
- Sentry monitoring

### Fase 1 — Watchlist + Migração ✅
- Scanner poll 5s: trenches near_completion + completed
- HotMonitor: janela 60min por token (push_mc 5s, hot-precise 60s, graduação 30s)
- Tabela `migrations` com todos os campos de qualidade (Fase 1 fields do trenches)
- Tabela `token_ticks` com série temporal 5s
- Dashboard `/scanner` e `/analise` com backtest exploratório
- Filtros configuráveis via env vars

### Fase 2 — Coleta de Sinais Ativos ✅ (2026-05-03)
- `get_token_signal()` no GmgnClient: POST `/v1/market/token_signal` type 12
- `_poll_signals()` no Scanner: chama a cada 5s, filtra por HotMonitor._tokens
- Ban guard `_signal_ban_until`: não chama enquanto banido por 429
- Tabela `token_signals`: UNIQUE(mint, trigger_at), ON CONFLICT DO NOTHING
- `insert_signal_batch()` no db.py
- Dashboard `/debug/signal-live`: diagnóstico em tempo real
- Volume fix: `backfill_tick_volumes()` — kline 1m → token_ticks.volume_1m

### Fase 2.5 — Análise Estrutural (2026-05-03)
- Bottom Score (5 componentes) implementado e testado — **resultado negativo**
- delta_smart / delta_renowned analisados — **resultado negativo (invertido)**
- tick-features completo (7 variáveis) — **todos negativos ou nulos**
- H10 validada formalmente: time_to_bottom < 25min = único preditor confirmado
- Dashboards: `/analise/bottom-score`, `/analise/smart-flow`, `/analise/tick-features`, `/analise/h10`

### Fase 2.6 — Price Deceleration Analysis (2026-05-03)
- H11 (deceleração) testada com 49 tokens (fundo ≥10min) — **REFUTADA**
- Padrão real: recovery tokens têm **capitulação brusca** no fundo (slope_c −0.171 vs −0.107)
- Novo sinal candidato: magnitude do spike de queda nos 2min antes do fundo
- Dashboard: `/analise/price-decel`
- **Próximo:** H12 — modelo probabilístico por dump_depth + time_elapsed

---

## Próximas Fases

### Fase 3 — Modelo Probabilístico de Entrada ⏳ (AGORA — H12)

**Big player move:** não esperar H9 passivamente. Extrair o máximo do dataset existente (445 tokens) construindo um modelo de entrada baseado no que já está confirmado.

**H12 — Análise de Dump Depth + Time Elapsed:**
- Grid 2D: `pct_drop` (bins de 10%) × `time_elapsed` (bins de 5min)
- Para cada célula: `P(recovery | drop_depth, time_elapsed)`
- Zona de entrada ótima: célula com P(recovery) máximo E N suficiente
- Combina naturalmente com H10 (time-stop 30min = descartar coluna >30min)
- Dashboard: `/analise/dump-depth`

**Por que fazer isso AGORA:**
- Dados já existem (445 tokens com recovery, pct_drop, time_to_bottom)
- Não depende de acumulação adicional (diferente de H9)
- Resultado define a zona de entrada do sistema de execução
- Se P(recovery | drop=60-70%, time=10-20min) > 65% → temos um edge acionável

### Fase 3b — Validação H9 ⏳ (paralelo, passivo)
**Gate:** acumular ~100 tokens com pelo menos 1 sinal em `token_signals`

**O que validar:**
- Correlação: `trigger_mc < mc_migration` → `recovery = TRUE`
- Delta esperado: ≥20pp (H9)

**Decisão após H9:**
- H9 CONFIRMADA → usar como confirmador de entrada sobre o modelo H12
- H9 REFUTADA → modelo H12 sozinho como sistema de entrada

### Fase 4 — Regras de Entrada ⏳ (após H9)
**Condições candidatas (a validar):**
1. `time_to_bottom < 30min` — time-stop (H10 confirmada)
2. `≥1 signal type 12 com trigger_mc < mc_migration` — entrada ativa (H9 pendente)
3. Filtros de qualidade existentes (rug, rat, top10, wash, mc range, liq, fee)

**Sizing:**
- Entrada: $50 fixo por trade (validar H7 — slippage em liq < $5k)
- TP/SL via `/v1/trade/strategy/create`

### Fase 5 — Backtest Walk-Forward ⏳ (após H9)
- Split temporal: in-sample (tokens 1-300) / out-of-sample (tokens 301+)
- Métricas: win rate, payoff ratio, max drawdown simulado
- Gate: win rate ≥50% e payoff ≥2× out-of-sample

### Fase 6 — Execução Live ⏳ (após Fase 5)
**Dependências:**
- `GMGN_PRIVATE_KEY` configurado no Railway (Ed25519 PEM)
- Header `X-Signature` implementado no GmgnClient
- `POST /v1/trade/swap` — swap
- `POST /v1/trade/strategy/create` — TP/SL automático
- `GET /v1/trade/query_order` — status de ordem

**KPIs de go-live:**
| Métrica | Meta |
|---------|------|
| Win rate out-of-sample | ≥50% |
| Payoff ratio | ≥2× |
| Max drawdown simulado | ≤40% |
| Latência detecção → ordem | <15s |
| Divergência real vs backtest | <30% após 50 trades |

---

## Decision Gates

```
[AGORA]  H12 — Modelo probabilístico dump_depth × time_elapsed
    │    Dados: 445 tokens já no banco, zero espera
    │    /analise/dump-depth
    │
    ▼
[H12]    P(recovery | drop%, time) > 65% em alguma zona?
    │
    ├── SIM ──→ [Fase 4] Zona de entrada definida
    │               │
    │               │    (paralelo) H9 acumulando token_signals
    │               ▼
    │           H9 CONFIRMADA → signal type 12 como confirmador
    │           H9 REFUTADA   → H12 sozinho como entrada
    │               │
    │               ▼
    │           [Fase 5] Backtest walk-forward (in/out-of-sample)
    │               │
    │               ▼
    │           [Fase 6] Execução live com capital mínimo
    │
    └── NÃO ──→ [Análise] Volume spike (dados acumulando com fix)
                    └──→ time_to_bottom < 30min como gate único?
```

---

## Arquitetura de Dados — Estado Atual

| Tabela | Linhas | Completude |
|--------|--------|-----------|
| `migrations` | 1015+ | recovery definido em 425 (42%) |
| `token_ticks` | ~millions | volume_1m: NULL antes do fix, preenchido daqui em diante |
| `token_signals` | crescendo | ~100 tokens meta para H9 |

### Cobertura campos Fase 1 (nas migrações recentes)
- `creator_token_status`, `creator_balance_rate`, `dev_team_hold_rate`: ✅ coletado
- `suspected_insider_rate`, `top70_sniper_hold_rate`, `bot_degen_rate`: ✅ coletado
- `entrapment_ratio`, `burn_status`, `cto_flag`, `has_social`: ✅ coletado
- `bluechip_owner_pct`: ⚠️ sempre NULL (não disponível no trenches)
- `lock_percent`: ✅ coletado via token/info na graduação (60min)
- `volume_1m` (token_ticks): ✅ back-fill ativo via kline (dados históricos irrecuperáveis)

---

## Lições Aprendidas

1. **Snapshots não discriminam recovery**: smart_count, renowned_count, holders, rug_ratio, wash_trading — todos testados, nenhum discrimina. São filtros de qualidade, não preditores de bounce.

2. **O único preditor validado é tempo**: `time_to_bottom < 30min` (+23pp). Dumps rápidos = capitulação. Dumps lentos = distribuição.

3. **Volume é essencial mas estava faltando**: todo o backtest de volume-based features foi feito com dados nulos. Fix aplicado — dados futuros resolverão.

4. **Compra ativa ≠ posição acumulada**: delta_smart é invertido porque smart_count conta posições antigas. A hipótese central (H9) usa signal type 12 que detecta COMPRAS novas em tempo real.

5. **Metodologia > Features**: construir dataset limpo e validar hipóteses formalmente antes de implementar qualquer lógica de execução. Nenhum trade foi feito com capital antes da validação.

6. **Invertido ≠ inútil**: H11 refutou deceleração, mas revelou que recovery tokens têm capitulação mais brusca. Resultados negativos sempre ensinam algo — documentar o padrão real, não só o resultado.

7. **Não esperar passivamente**: com H10 confirmada e 445 tokens no banco, o próximo passo não é esperar H9 (semanas). É extrair máximo valor dos dados existentes agora — modelo probabilístico H12.
