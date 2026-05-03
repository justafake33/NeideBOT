# NeideBOT — Roadmap

> Última atualização: 2026-05-03

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

### Análises de tick-features — resultados negativos (2026-05-03)

| Feature | avg recovery | avg no_rec | Conclusão |
|---------|-------------|-----------|-----------|
| delta_smart (início→fundo) | 1.07 | 2.27 | ❌ invertido |
| delta_renowned (KOLs) | 0.44 | 0.65 | ❌ invertido |
| delta_combined (smart+KOL) | 1.51 | 2.92 | ❌ invertido |
| delta_holders | 15.46 | 15.31 | ❌ flat |
| vol_spike (volume no fundo) | null | null | ⏳ fix aplicado, dados futuros |
| vol5m_slope | null | null | ⏳ fix aplicado, dados futuros |
| **time_to_bottom** | **19.5 min** | **40.2 min** | ✅ **maior discriminador** |

> **Por que delta_smart é invertido:** `smart_count` é snapshot cumulativo de posições — não detecta compras ativas. Tokens com dump longo acumulam mais smart wallets simplesmente porque o dump dura mais. Sinal de compra ativa = signal type 12 (token_signals).

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

---

## Próximas Fases

### Fase 3 — Validação H9 ⏳ (próxima)
**Gate:** acumular ~100 tokens com pelo menos 1 sinal em `token_signals`

**O que validar:**
- Correlação: `trigger_mc < mc_migration` → `recovery = TRUE`
- Delta esperado: ≥20pp (H9)
- Análise: timing do sinal vs time_to_bottom

**Decisão após H9:**
- H9 CONFIRMADA → avançar para Fase 6 (execução)
- H9 REFUTADA → revisar estratégia de entrada; candidatos alternativos:
  - Volume spike pós-fundo (dados disponíveis após volume fix)
  - AMM price impact como proxy de liquidez absortiva
  - Combinação time_to_bottom < 30min como time-stop suficiente

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
[AGORA]  Acumulação de token_signals
    │    Meta: ~100 tokens com sinais
    │
    ▼
[H9]     Validar: trigger_mc < mc_migration → recovery ≥20pp?
    │
    ├── SIM (+20pp) ──→ [Fase 4] Regras de entrada + sizing
    │                        │
    │                        ▼
    │                   [Fase 5] Backtest walk-forward
    │                        │
    │                        ▼
    │                   [Fase 6] Execução com capital mínimo
    │
    └── NÃO ──────→ [Análise] Volume spike pós-fundo (dados disponíveis pós volume-fix)
                         └──→ [Revisão] time_to_bottom sozinho como gate suficiente?
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
