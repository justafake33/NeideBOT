# NeideBOT — Roadmap

**Atualizado:** 2026-06-25 (sessão 23)
**Estado:** Bot ATIVO · `TRADING_ENABLED=true` · branch ativo: `claude/magical-cerf-3wjq5o` · **Modo Helius** (erpc desativado — 0 créditos)
**Infra Railway:** ~$6/mês (welcoming-miracle + intelligent-spirit PostgreSQL) · Solo desde 2026-06-22

---

## ✅ Concluído

| Item | Data | Resultado |
|------|------|-----------|
| Coleta de dados pós-migração | 2026-04 | 1175+ tokens, backtester tick-level, H10 validada (+21.8pp) |
| Fase A — filtros A.2/A.3/A.5/A.6 | 2026-05 | Trailing TP via condition_orders — avg 159% vs 71% TP fixo |
| Calibração $1/trade (53 trades) | 2026-05-08 | WR 15.1% on-chain · ATA rent = principal custo |
| Helius Tier 1 inline (pump.fun pAMM) | 2026-05-12 | `pAMMBay6...` BuyEvent/SellEvent — preço em ~5ms via pool reserves offsets 48/56 |
| H22 coleta de absorção | 2026-05-12 | deque(maxlen=300) por mint · 8 colunas em bounce_signals · score deliberadamente ausente |
| FILTER_PUMP_ONLY=true | 2026-05-13 | 100% cobertura futura Helius — rejeita migrações sem "pump" no exchange |
| helius_rpc.py — fill on-chain | 2026-05-13 | `get_swap_fill()` + `get_token_balance()` — blockchain como fonte de verdade |
| /debug/sell-recovery — auditoria blockchain | 2026-05-14 | 573 txs, 139 trades corrigidos · **WR real = 10.1%** (vs 32% do DB) |
| Bug 1 — ts_migration usa complete_timestamp | 2026-05-17 | `datetime.now()` → `datetime.fromtimestamp(complete_timestamp)` — A.5/A.6 funcionam |
| Bug 3 — bounce_fired atômico sob _lock | 2026-05-17 | Check-and-set atômico previne double-fire de trades |
| Bugs 5/6 — elapsed_sec < 90 em kline | 2026-05-17 | Candle de transição bonding curve→pool removido de graduate e backfill |
| A.9 — MIN_POOL_SOL=10 (secondary pools) | 2026-05-18 | pool_quote_sol >= MIN_POOL_SOL filtra pools espúrias invisíveis no DexScreener |
| k-gate fixes + _K_INIT_TOLERANCE=0.80 | 2026-05-19 | Cobre pools com ~130 trades de fee acumulados · secondary pools (<1.253e25) bloqueadas |
| BOUNCE_MAX_PCT guard fix (< 9.99) | 2026-05-19 | Guard anterior (`< 1.0`) desativava filtro para qualquer threshold ≥ 100% |
| erpc global transactionSubscribe | 2026-05-21 | Dual-mode erpc/Helius · pool-account por intersecção de 5 TXs · try/except + counter |
| Phantom fix — ALT tx bypass | 2026-05-26 | `accounts is not None` guard · erpc ALT (`accounts=[]`) rejeito · Helius (`None`) preservado |
| Patch mc_min drift — 3 correções | 2026-05-26 | kline removido do write path de mc_min · k-gate INCREASE REJECT→RESET · PDA pre-seed |
| mc_min_true com txid_proof | 2026-05-26 | `helius_rpc.get_pool_bottom()` async na graduação · bottom_error_pct + bottom_quality |
| **Marco zero definitivo: 2026-05-26** | 2026-05-26 | Auditoria n=15: 67% precisos ≤3%, 20% learning window (intencional), 13% phantoms corrigidos |
| C1/C2/C3/C4 — fixes de execução | 2026-05-28 | sl_ref usa mc_at_signal · HeliusWS no executor · pool depth gate no bounce · ALT PDA fallback |
| V2 Fase 1 — micro_absorption_snapshots | 2026-05-30 | Tabela ~97 colunas · snapshots 2s por 60s pós-fundo · janelas PRE/RISE/LIVE |
| Sell exhaustion detector | 2026-06-02 | sell_exhaustion_candidates · ratio=sell_5s/sell_5_15s < 0.25 = sellers pararam |
| Análise H23 com dados limpos (n=138) | 2026-06-06 | 86% bounces com net_sol_flow negativo (estrutural) · gate H23_NET_MIN implementado (desativado) |
| Novo banco DB + db_retention.py | 2026-06-08 | beautiful-heart 100% full → intelligent-spirit · retenção auto a cada 6h com alertas Telegram |
| Análise de filtros — sessão 21 | 2026-06-11 | BOUNCE_DROP_MIN: 0.30→0.20 · TRADE_MAX_ELAPSED_SEC: 300→900 · BOTTOM_FRESHNESS_SEC: 180→600 |
| Priority lane swap + GMGN_SCANNER_API_KEY | 2026-06-12 | swap/query_order bypass global ban · scanner com key dedicada independente |
| Análise PRÉ+RISE via token_ticks (n=61) | 2026-06-12 | RISE net<0 → rec75=9%, sl25=91% · RISE gate implementado (RISE_NET_MIN=0) |
| erpc 401 → Helius fallback | 2026-06-22 | 0 créditos erpc · WS_RPC_URL adicionada · custos ~$80→~$6/mês · sócio saiu |

> ⚠️ **WR REAL (2026-05-14):** banco tinha dados fictícios — TradeMonitor inferia saídas de MC snapshots, não blockchain. Auditoria: WR real = **10.1%** (14/139). `/debug/sell-recovery` aplicado.
> ⚠️ **DADOS HISTÓRICOS SUSPEITOS:** mc_min de tokens antes do fix (elapsed_sec < 90) corrompido pelo candle de transição bonding curve→pool. Análises de bounce_pct/drop histórico devem ser tratadas com cautela.
> ⚠️ **MARCO ZERO DEFINITIVO: 2026-05-26.** mc_min confiável a partir desta data. Trades 2026-05-21 a 2026-05-25: bug ALT tx ainda ativo (mc_min potencialmente contaminado).

---

## 🔬 V2 — Reestruturação de Microestrutura

### Estado atual (2026-06-25)

**Bot ATIVO** · `TRADING_ENABLED=true` · Branch: `claude/magical-cerf-3wjq5o` · **Modo Helius** (erpc desativado)

Filtros recalibrados na sessão 21. Gate H23 (`H23_NET_MIN`) desativado (n=13, insuficiente). RISE gate (`RISE_NET_MIN=0`) ativo — aguardando n≥30 bounces para avaliar impacto. Operação solo desde 2026-06-22.

**Implicação do modo Helius**: gap de ~10-30s entre detecção do token e início do feed WS. V-bottoms muito rápidos (<30s) podem ser perdidos antes do subscribe — kline hot-precise (5s) e trenches (5s) cobrem como fallback.

**Filtros ativos (Railway) pós-sessão 21/22:**
- `BOUNCE_DROP_MIN=0.20` (era 0.30 — bloqueava rec75 de 38-46%)
- `TRADE_MAX_ELAPSED_SEC=900` (era 300 — bloqueava 74% dos sinais)
- `BOTTOM_FRESHNESS_SEC=600` (era 180 — cortava zona 3-10min com 33% rec75)
- `RISE_NET_MIN=0` (sessão 22 — RISE net < 0 → rec75=9%, sl25=91%)

A ideia central da V2: filtros estáticos entregam no máximo +7pp de WR. O edge real está em **quem comprou o fundo e com que força** — fluxo de ordens em granularidade de segundos via Helius pAMM.

### Fase 1 — Micro Absorption Snapshots ✅ CONCLUÍDA (2026-05-30)

Tabela `micro_absorption_snapshots` (~97 colunas) · snapshots a cada 2s por token durante os 60s após o fundo · janelas PRE/RISE/LIVE · guard de completude em RISE · concentrações nomeadas · `candidate_flags` JSONB · thread-safe com `last_micro_snapshot_ts` · endpoint `/debug/micro-absorption-snapshots`.

**Travas de segurança (NUNCA violar):**
- `_check_bounce`, executor, swap, TP/SL, trailing → intocável
- Tabelas existentes → sem DELETE/ALTER destrutivo
- Falha de coleta → log silencioso, nunca exceção propagada

### Fase 2 — Micro Absorption Lab ⏳ STANDBY

**Gate:** n≥30 bounces com `micro_absorption_snapshots` + outcomes GMGN (confirmados via `/debug/sell-recovery`)

Quando gate atingido: abrir `/debug/micro-absorption-snapshots` + `/analise/h22-correlation` → calcular WR por combinação PRE/RISE/LIVE → identificar janela que mais discrimina TP vs SL → documentar antes de qualquer código novo.

### Fase 3 — Shadow Entry Signals ⏳ STANDBY

**Gate:** Fase 2 com ≥1 métrica mostrando ≥15pp de discriminação

Implementar `shadow_entry_signals` — log de sinais que passariam pelo filtro de absorção, sem executar. Validar que não elimina mais de 40% dos bounces válidos.

### Fase 4 — EntryEngineV2 ⏳ STANDBY

**Gate:** Shadow Entry com n≥50 e WR hipotético ≥25pp melhor que baseline, com ≤40% de bounces bloqueados

Adicionar gate de absorção em `_check_bounce()` como condição adicional. `ABSORPTION_GATE_ENABLED` env var.

### Fase 5 — ExecutionEngineV2 Dry-run ⏳ STANDBY

**Gate:** Fase 4 ativa e estável por ≥7 dias

`TRADING_ENABLED=true` com `CALIBRATION_AMOUNT_USD=1.0` · n=30 trades reais · comparar WR com período pré-V2.

### Fase 6 — ExecutionEngineV2 Real Scout ⏳ STANDBY

**Gate:** Fase 5 com WR ≥25% confirmado GMGN em ≥30 trades

Escalar para $10/trade · implementar ENTRY_MAX_BOUNCE_PCT · avaliar migração GMGN → Jupiter.

---

## 🔮 Visão Futura

### Seller exhaustion (H24) — próxima grande alavanca

90% dos SLs = tokens onde sellers NÃO tinham terminado quando entramos. Com `micro_absorption_snapshots`:
- `pre_30s_sell` — pressão de venda nos 30s antes do fundo
- `rise_5s_buy_concentration` — bounce concentrado vs disperso
- `pre_5s_net` vs `rise_5s_net` — mudança de regime dump→bounce

Se `pre_30s_sell > threshold` e `rise_5s_buy_concentration < 0.3` → sellers ativos → não é o fundo real.

### Jupiter + Helius (quando WR ≥30% confirmado)

GMGN platform fee: 1%+1% = 2% → Jupiter LP fee: ~0.25%+0.25% = 0.5%. ATA rent por SL: ~$0.20 → $0 (Jupiter fecha ATA). Break-even a $10/trade: GMGN ~12% → Jupiter ~9%. Implementar DEPOIS de WR ≥30% em ≥30 trades a $10.

### Re-entry em tokens com TP histórico

Tokens com TP anterior demonstraram bounce real. `bounce_fired` não bloqueia re-entry se token tem TP no DB. Opcional: 2× size para esses tokens. Gate: WR estável em 30+ trades antes de implementar.

### Score de confluência (longo prazo, n≥200 trades limpos)

Com n<200, qualquer bucket tem <50 amostras — ruído, não sinal. Prioridade: acumular dados.

---

## 🔜 Curto Prazo

### Fase 0 — Validar captura do fundo ✅ CONCLUÍDA (sessão 16, 2026-05-26)

Auditoria manual n=15: 10/15 precisos (≤3%), 3/15 learning window (tradeoff intencional), 2/15 phantoms corrigidos (MILLION, Multipad — bug ALT tx). `mc_min_true` preenchido async via `helius_rpc.get_pool_bottom()`. `bottom_error_pct` e `bottom_quality` persistidos em `migrations`.

### Fase 1 — ENTRY_MAX_BOUNCE_PCT ← PRÓXIMO CÓDIGO

**Problema:** latência 1-3s do GMGN HTTP → preço move +5-15% entre `_check_bounce()` e o fill.

**Solução:** Se preço corrente > `mc_at_signal × (1 + ENTRY_MAX_BOUNCE_PCT)` → cancelar trade.

```python
# executor.py — _execute(), antes de chamar swap()
if ENTRY_MAX_BOUNCE_PCT > 0:
    current_mc = helius_ws.get_latest_mc(mint)
    if current_mc and current_mc > bounce_signal['mc_at_signal'] * (1 + ENTRY_MAX_BOUNCE_PCT):
        db.update_trade_status(trade_id, 'cancelled')
        return
```

**Env var:** `ENTRY_MAX_BOUNCE_PCT=0.10` · **Gate:** Fase 0 concluída (já passou).

### Fase 2 — Escala de Capital

| Gate WR (GMGN, ≥30 trades) | Size alvo | Break-even WR | ATA rent % |
|-----------------------------|-----------|---------------|------------|
| WR ≥ 25% (meta atual) | $10/trade | ~12% | 2% |
| WR ≥ 28% (a $10) | $25/trade | ~11% | 0.8% |
| WR ≥ 30% (a $25) | $50/trade | ~11% | 0.4% |
| WR ≥ 35% (a $50) | $100/trade | ~11% | 0.2% |

A $1/trade, ATA rent (~$0.20/SL) = 20% do investimento. A $10 = 2%. Barreira mínima para estratégia lucrativa estruturalmente.

### Fase 3 — ATA Rent Reclaim

**Curto prazo (manual):** toda segunda-feira → GMGN wallet → "Reclaim Rent" (~$0.20/SL).

**Médio prazo (automático):** `closeAccount` via Solana RPC após cada SL confirmado. Requer `WALLET_PRIVATE_KEY`. **Gate:** Fase 2 ativa ($10+).

### Fase 4 — Jupiter Swap (WR ≥30% confirmado)

| Custo | GMGN atual | Jupiter + Helius |
|-------|------------|-----------------|
| Platform fee | 2% (1%+1%) | 0% |
| LP fee (DEX) | ~0.5% | ~0.5% |
| ATA rent | ~$0.15/trade | ~$0 (fecha no sell) |
| **Total a $10** | **~9.2%** | **~1.1%** |
| Break-even WR | ~28% | ~18% |

**Gate:** WR ≥30% confirmado GMGN em ≥30 trades a $10+. Requer `WALLET_PRIVATE_KEY` (Base58).

---

## 📋 Médio Prazo

### 7. Escalar $1 → $10 → $50 → $100

**Gate único para cada salto:** WR ≥20% confirmado em ≥30 trades GMGN no size atual.

| Size | ATA rent | GMGN fees | Break-even WR |
|------|----------|-----------|---------------|
| $1   | 20%      | 2%        | ~18%          |
| $10  | 2%       | 2%        | ~12%          |
| $100 | 0.2%     | 2%        | ~11%          |
| $100 (Jupiter) | 0% | 0.5% | ~9%        |

### 8. Helius RPC + Jupiter Swap

GMGN cobra 1%×2 + ATA rent. Jupiter = 0% platform fee + fecha ATA automaticamente. Pré-requisito: WR confirmado em $10/trade. Não migrar antes.

### 9. Score de confluência e sizing por tier

Pré-requisito: n≥200 trades GMGN limpos. Exemplo futuro:
| Tier | Condições | Ação |
|------|-----------|------|
| A+ | smart≥3 + absorção confirmada + H9<0.80 | 2× size |
| A  | smart≥3 + absorção confirmada | 1× size normal |
| B  | smart≥3 sem absorção | min-size / coleta |

---

## 📊 Métricas de Decisão

| Decisão | Gate |
|---------|------|
| Qualquer novo filtro | n≥30 trades confirmados GMGN, discriminação ≥10pp |
| Absorção pós-fundo como filtro | n≥30 com/sem absorção, WR ≥15pp maior com absorção |
| H9 como tier A+ (2× size) | n≥15 em ratio<0.80 com WR ≥50% GMGN |
| H9 como gate obrigatório | n≥30, WR ≥50% confirmado GMGN |
| Escalar $1 → $10 | WR ≥20% em ≥30 trades (GMGN) com filtros atuais |
| Escalar $10 → $50 | WR ≥20% em ≥30 trades a $10 (GMGN) |
| Migrar GMGN → Jupiter | WR confirmado a $50+ |
| Score/tiers completo | n≥200 trades GMGN limpos |

---

## 🧠 Princípios

1. **GMGN é a única fonte de verdade.** DB WR é inválido — TradeMonitor infere, GMGN executa.
2. **Dados antes de filtros.** Nunca implementar filtro baseado em análise do DB. n≥30 GMGN primeiro.
3. **A pergunta certa não é "subiu 30%?" — é "tem comprador real absorvendo o sell pressure?"** Helius pAMM events respondem isso.
4. **WR alto vem de seleção, não de stop.** 90% dos SLs são tokens que continuaram dumpando. Foco: não comprar o primeiro bounce falso.
5. **Volume qualificado, não size.** Edge está em frequência de trades qualificados. Teto ~$100-200/trade.
6. **Escalar depois de confirmar.** Cada salto de size exige validação no size menor.
7. **One change at a time.** Não mudar size + infraestrutura + filtros simultaneamente.
8. **Reverta rápido.** Filtro adicionado sem dados válidos → reverter imediatamente.
9. **Tiers e scoring são long game.** Com n<200 trades, qualquer bucket é ruído.

---

## 🗃️ Hipóteses — Placar

| # | Hipótese | Status | Resultado |
|---|----------|--------|-----------|
| H1 | ≥70% dos tokens caem ≥20% do ATH nos primeiros 60min | ✅ | 91.4% |
| H2 | Fundo em ≤15min em tokens com recovery (≥70%) | ❌ | 49.7% |
| H3 | smart_wallets ≥3 → recovery ≥15pp maior | ❌ | +0.2pp (token-level) |
| H4 | sniper ≥30% → recovery <30% | ❌ | levemente positivo |
| H7 | liq <$5k → slippage inaceitável >$50 | ⏳ | pendente validação |
| H8 | wash_trading → recovery ≥20pp menor | ❌ | -0.1pp |
| H9 | signal type 12 trigger<80% mig → recovery ≥20pp maior | 🟡 | +11.8pp histórico (n=14) · 75% WR live (n=4) |
| H10 | time_to_bottom < 25min → recovery ≥20pp maior | ✅ | **+21.8pp** (n=425) |
| H11 | Deceleração pré-fundo discrimina recovery | ❌ | invertido — recovery = capitulação brusca |
| H12 | dump_depth + time_elapsed → zona P(recovery)>50% | ⚠️ | parcial — time domina |
| H13 | Slope íngreme pré-fundo → WR ≥40pp maior | ❌ | flat em n=153 |
| H14 | bounce 20-30% = dead zone | ✅ | WR 3.1% (n=32) |
| H15 | Entry 50-65% mig → WR=0% | ✅ | 0/21 |
| H16 | 3 ticks acima entry → WR ≥20pp maior | ✅ | +18pp (A.4 removido — entrada muito alta) |
| H17 | 100% TPs em ≤20min após sinal | ✅ | 11/11 ≤20min · A.5=20min |
| H18 | Bounce velocity discrimina WR | ❌ | flat em todos os tercis |
| H19 | 1-2min delay melhora EV | ✅ | +6.6pp (A.4 removido por outras razões) |
| H20 | W-bottom = sinal de confirmação | ❌ | 98% prevalência — ubíquo |
| H21 | SL ancorado no fundo resolve maioria dos SLs | ❌ | **REFUTADA** — 90% dos SLs caem abaixo de 0.55× o fundo |
| H22 | Buy vol > sell vol nos 60s pós-fundo discrimina WR ≥15pp | ⏳ | coleta ativa · gate: n≥30 outcomes GMGN |
| H23 | net_sol_flow > threshold discrimina WR | 🟡 | 86% negativos (estrutural) · positivo (0-10 SOL): WR 67% n=13 · gate: n≥30 |
| H24 | Seller exhaustion (top-3 sellers >70% dump_vol) → WR maior | ⏳ | pendente implementação |
| H25 | pool_quote <20 SOL → SL é ruído de AMM | 🟡 | pool>90 SOL em decision_entry: WR=17.5%, EV=-11.4% — NÃO usar como positivo |
| H26 | Buy concentration (largest_buy/buy_vol) alta → WR maior | ⏳ | pendente correlação — dados já existem |

---

## 🔧 Lições Aprendidas

1. **DB WR é lixo como métrica**: TradeMonitor infere, não observa. GMGN executa. `exit_type='helius_sell_recovery'` é o único indicador de dado confiável.
2. **Nunca filtrar com dados corrompidos**: bounce>50% e TTB<15min pareciam 50% WR mas eram artefato de falsos TPs. Filtros implementados e revertidos na mesma sessão.
3. **pump.fun pAMM, não Meteora**: tokens migrados do pump.fun vão para `pAMMBay6...`. `logsSubscribe(mentions=[mint])` é extensão proprietária do Helius — providers padrão (dRPC, etc.) não suportam.
4. **O candle de transição bonding curve→pool corrompeu tudo**: qualquer loop que lê kline sem `if elapsed_sec < 90: continue` captura preços pré-migração como mc_min. Regra absoluta: essa linha é a primeira após o check de elapsed em qualquer loop kline de token migrado.
5. **ts_migration deve ser on-chain, nunca `datetime.now()`**: token detectado 15min após migração entrava com elapsed_sec≈0 → A.5/A.6 inúteis → filtros temporais neutralizados.
6. **Race condition em bounce_fired é bug de correctness**: check-and-set deve ser atômico sob `_lock`. GIL não garante exclusão mútua em lógica check-then-act. Double-fire = dois trades no mesmo token.
7. **Kline `high` inflava mc_peak com spikes intra-candle**: em pools com ~79 SOL, um único trade pode empurrar o AMM price 200x por <1 candle. Usar `close × supply` para mc_peak. `mc_high` mantido nos token_ticks apenas para o backtester.
8. **Pipeline silencioso: instrumentar antes de refatorar**: adicionar try/except + counter de eventos + logs em cada early-return revela exceptions silenciosas em segundos, sem refactoring.
9. **GMGN nunca retorna `filled_output_amount`**: comportamento padrão, não bug transitório. Ler fill da blockchain via Helius `getTransaction`. A blockchain não mente.
10. **A janela temporal define o que a métrica mede**: `net_sol_flow` em `[bounce-60s → bounce]` = momentum (quem comprou durante a subida). `net_sol_flow` em `[ts_min-60s → ts_min]` = absorção do dump. São métricas opostas. Sempre definir `window_end_ts` explicitamente.
11. **Score sem correlação = ilusão de precisão**: `_absorption_score()` removida deliberadamente. Coletar métricas brutas → correlacionar com outcomes → SÓ ENTÃO construir fórmula com pesos que os dados justificam.
12. **`accounts is not None` é a guarda correta**: `if accounts:` — lista vazia é falsy → secondary pools em transações ALT/versioned passavam. `accounts=None` (modo Helius) não tem dados de account. Regra: validação de pool só quando `accounts is not None` e é uma lista.
13. **`db._cursor()` retorna RealDictCursor — nunca indexar por posição**: `r[0]` falha com `KeyError: 0`. Sempre `r['coluna']`. Silenciado por `try/except` em generators → difícil de debugar.
14. **`pnl_pct_real` é decimal no banco**: `0.75` = 75% de ganho. Display: `f"{pnl*100:+.1f}%"`, nunca `f"{pnl:+.1f}%"`.
15. **Cobertura de infraestrutura ≠ dados disponíveis**: implementar H22 não significa ter dados úteis. Verificar `SELECT COUNT(*) FROM bounce_signals WHERE absorption_events > 0` antes de qualquer análise de correlação.
