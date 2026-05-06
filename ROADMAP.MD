# NeideBOT — ROADMAP

> **Regra:** Não avance para o próximo passo sem validar o anterior com dados reais.
> **Data:** 2026-05-06 (atualizado — 20 trades executados, bugs críticos de execução corrigidos)

---

## Status Geral

| Fase | Nome | Status |
|------|------|--------|
| 0 | Blindagem (Sentry, logs) | ✅ Completo |
| 1 | Intelligence Layer (campos Fase 1) | ✅ Completo |
| 2 | Flow Engine (token_signals type 12) | ✅ Ativo — 98 sinais acumulados |
| **A** | **Bounce Detector v2** | ✅ **ATIVO** — aguardando 30+ bounces filtrados |
| **B** | **Cobertura de Ticks** | ✅ **ATIVO** — impacto esperado nos próximos bounces |
| 6-lite | Execução $1 calibração | 🟡 **20/50 trades** — WR 25% (5 TP, 15 SL) |
| C | Flow Continuation Score | ⏳ Depende de cobertura de ticks (Fase B maturando) |
| D | Smart Netflow Live (H9) | ⏳ +11.8pp faixa <50% mig. (n=14) — reverse causality identificada em >120% |
| E | Score composto modular | ⏳ Após C+D validados individualmente |

---

## ✅ BLOQUEIO RESOLVIDO — Signature GMGN (2026-05-05/06)

**Causa raiz 1:** `_load_ed25519_key` tentava Base64 antes de Base58. String Base58 de 88 chars (Phantom export) é decodificável por Base64, gerando seed errado → wallet errada → assinatura inválida.
**Fix 1:** Base58 movido antes do Base64 + validação de comprimento (32 ou 64 bytes).

**Causa raiz 2:** `GMGN_PRIVATE_KEY` era a private key da wallet Solana (Base58). GMGN exige chave PEM Ed25519 separada emitida pela própria GMGN — disponível no painel da conta.
**Fix 2:** Usuário obteve a PEM key correta do painel GMGN.

**IP:** Railway IP cadastrado na whitelist GMGN.
**Keypair:** `DJVwyki5hTqMSLVaSyS9MXo5pGVbx4qEowV3BDtqvEgy` ✅ CORRESPONDE.

> ⚠️ **IP muda a cada redeploy Railway.** Usuário atualiza manualmente após cada deploy. Solução permanente: Railway Static Outbound IP (~$3/mês).

> ⚠️ **IP muda a cada redeploy Railway.** Solução permanente: Railway Static Outbound IP (~$3/mês). Necessário antes de escalar.

---

## ✅ FASE A — Bounce Detector v2 (IMPLEMENTADO)

**Objetivo:** elevar WR de 13.3% para >25% sem mudar arquitetura.
**Base:** análise `/analise/deep-bounce` com 83 bounces reais.
**WR estimado pós-filtros:** ~29% (38 trades restantes, 11 TPs intactos).

### A.1 — Slope Filter ✅
**Dado:** slope íngreme (≤ -0.0008 %/s) → WR 66.7% vs slope gradual → WR 0.0% (+66.7pp, n=17)
**Slope avg:** TP = -0.0035 %/s, SL = -0.0003 %/s
**Implementação:** `_check_slope()` mede slope 2min ANTES do fundo (`max_ts=ts_min`). Fallback pass se < 3 ticks.
**Cobertura atual:** 20.5% (17/83). Fase B vai elevar.

### A.2 — Bloquear dead zone bounce 20-30% ✅
**Dado:** bounce_pct 20-30% → WR 3.1% (n=32, 39% de todos os bounces!)
**Implementação:** `if 0.20 <= bounce_pct < 0.30: return` (sem setar bounce_fired)
**Impacto:** remove 32 trades ruins. Permite disparo posterior se preço sobe para 30%+.

### A.3 — Bloquear entry 50-65% da migração ✅
**Dado:** `mc_at_signal / mc_migration` entre 50-65% → WR 0% (n=21)
**Implementação:** `if 0.50 <= entry_ratio <= 0.65: return` (sem setar bounce_fired)
**Impacto:** remove 21 trades com zero acertos.

### A.4 — Persistência como confirmação antes de executar ✅
**Dado:** 3 ticks pós-sinal acima do entry → WR 28.6% vs 10.6% (+18pp, n=14/66)
**Implementação:** `TradeExecutor._execute()` aguarda 90s, verifica ticks após `ts_signal` (não antes)
**W-bottom (98%):** compatível — dip rápido + recuperação = ticks voltam acima do entry antes de 90s

### A.5 — Time-stop 20min ✅
**Dado:** 100% dos TPs em ≤20min (mediana 6.1min, 82% em ≤10min). Zero TPs em 20-40min.
**Implementação:** `BOUNCE_TIME_MAX=20.0`

### Não implementado (dados invalidaram)
- **Bounce velocity:** todas as tercis têm WR 40% flat. TTB+Slow=43% > TTB+Fast=29%. H18 REFUTADA.
- **Entry delay explícito:** +6.6pp com 1min, coberto pelo A.4 (90s wait). H19 coberta.

---

## ✅ FASE B — Cobertura de Ticks (IMPLEMENTADO)

**Problema:** 22% dos bounces tinham ticks suficientes para slope.
**Causa raiz identificada:** `_precise_bottom_tick()` atualizava `mc_min` mas NÃO `ts_min` no state → slope filter queryava janela errada (ts_migration - 120s) → 0 ticks → fallback pass sem filtrar.

### B.1 — Fix `ts_min` no caminho kline ✅
`inner["ts_min"] = candle_dt` quando kline detecta novo mc_min. Causa raiz do 22%.

### B.2 — Janela kline 2min → 5min ✅
`from_ts=now_ts - 300` (era 120). Mais candles por chamada, cobre gaps.

### B.3 — Backfill retroativo na primeira detecção elegível ✅
`_backfill_slope_ticks()`: busca kline desde ts_migration até now na primeira vez que bounce passa A.2/A.3. Slope filter roda no próximo poll (5s depois) com dados garantidos.

### B.4 — HOT_PRECISE_SEC 60s → 30s ✅
Dobra frequência de coleta kline. Impacto < 15% do rate limit mesmo com 10 tokens ativos.

**Meta:** cobertura ≥ 50% dos novos bounces com slope calculável.

---

## FASE C — Flow Continuation Score

**Dependência:** Fase B maturando (≥50% cobertura)

**O que é:** após bounce signal, verificar nos primeiros 5 ticks pós-entrada:
- Higher low em relação ao fundo
- Higher high em relação ao tick anterior
- Volume crescente (se disponível)

**Por que:** evolução do A.4. Não só "preço acima do entry" mas "estrutura de uptrend formando".

**Implementação:** módulo separado `flow_score.py`, chamado pelo TradeExecutor antes do swap.

**Pré-requisito:** validar que coverage de ticks pós-sinal ≥ 50% antes de implementar.

---

## FASE D — Smart Netflow Live (H9 em tempo real)

**Dependência:** token_signals acumulados (~100 tokens com sinal durante bounce)

**O que é:** nos 3 minutos ao redor do bounce signal, houve smart buy (type 12)?
- `trigger_mc < mc_min * 1.20` → smart wallet comprou perto do fundo → bullish
- Múltiplos triggers na janela → acumulação real

**Status:** 119 sinais acumulados (97 tokens únicos, 4 dias).

**Resultado parcial (2026-05-06):**
| Faixa trigger_mc/mc_migration | n | Recovery rate | Delta | Interpretação |
|-------------------------------|---|---------------|-------|---------------|
| < 50% migração | 14 | 35.7% | **+11.8pp** | ✅ Sinal genuíno — smart money no dump |
| 50-80% migração | 23 | 30.4% | +6.5pp | ⏳ Fraco — aguardar mais dados |
| 80-100% migração | 18 | 22.2% | -1.7pp | ❌ Negativo — zona de armadilha |
| > 120% migração | 25 | 56.0% | +32.1pp | ⚠️ **Reverse causality** — token já fez bounce quando sinal chega |
| Baseline (sem sinal) | 1190 | 23.9% | — | — |

**⚠️ Reverse causality em `>120%`:** token que atingiu 120% do mc_migration já fez o bounce → recovery=TRUE é esperado. Não é predição, é consequência. GME (4.83×), SueaAuan (2.01×): smart money comprou no pico, não no fundo.

**H9 reformulada:** threshold correto é `trigger_mc < mc_migration × 0.80`. Exclui momentum buyers pós-recovery. Aguardar n≥30 na faixa `<50%` antes de implementar como filtro.

---

## FASE E — Scores Compostos (após C+D validados)

> ⚠️ Não construir scores com pesos arbitrários. Cada componente deve ser validado individualmente. Compostos prematuros diluem o sinal mais forte.

| Score | Componentes validados | Componentes pendentes |
|-------|----------------------|----------------------|
| **Dump Intensity** | slope (✅ +66.7pp) | volume_spike (dados insuficientes) |
| **Supply Risk** | creator_close (✅ +9.1pp, n=65) | top70_sniper, bundler, insider (não validados para bounce) |
| **Buyer Quality** | token_signals type 12 (H9 pendente) | snapshot counts (❌ H3 refutou) |
| **Flow Continuation** | persistência (✅ +18pp) | higher lows/highs (depende cobertura B) |

---

## FASE F — Exit Engine Maleável (após calibração com 50 trades funcionando)

> **Filosofia:** TP/SL fixo é estratégia de sardinha — define o teto do ganho antes de saber o que o mercado vai fazer.
> Big player usa saída em camadas: realiza parcial no primeiro sinal de força, deixa o restante correr com proteção dinâmica.
> O objetivo não é "acertar o TP" — é **maximizar o EV por trade** capturando os runners sem perder a proteção.

**Pré-requisito:** 50 trades de calibração concluídos (friction medida, sistema estável).

### F.1 — Partial Take Profits
Dividir posição em 3 partes antes do swap:
- **30% em +30%** — saída rápida, cobre o custo do trade, psicologia estabilizada
- **30% em +60%** — segunda realização, lucro garantido mesmo se reverter
- **40% trailing stop** — a parte que captura os runners (+200%, +500%)

**Por que:** dos 83 bounces analisados, a mediana de TP foi +75% — mas os TPs reais (11) tiveram distribuição larga. Fixar 75% deixa dinheiro na mesa nos runners e não protege nos casos que revertem antes de chegar lá.

### F.2 — Trailing Stop dinâmico
- Ativa após TP1 atingido (+30%)
- Floor inicial: entry price (não perde o capital)
- A cada novo high: floor sobe para `high × 0.80` (-20% do pico)
- Sai automaticamente quando price cruza o floor

**Diferença para SL fixo:** SL fixo aciona em posição absoluta (-15% do entry). Trailing protege lucro acumulado — se subiu +100%, sai com +80% no pior caso, não com -15%.

### F.3 — Risk Engine em tempo real
Monitorar posição aberta a cada 5s via token_signals e kline:
- **Smart sell detectado** (type 12 sell side, volume ≥ X) → exit parcial imediato
- **Queda de liquidez > 30%** em 2 ticks consecutivos → exit emergencial total
- **Creator vende após entrada** (creator_token_status muda para "creator_close") → exit imediato
- **Sem novo high por N ticks** após TP1 → considerar saída antecipada

### F.4 — Position Sizing dinâmico (após Fase F.1-F.3 validadas)
- Score composto (Fase E) → define tamanho relativo da posição
- Trade de alta convicção (slope + persistência + H9) → size maior
- Trade de convicção média (apenas A.2/A.3/A.5 passam) → size mínimo
- Cap absoluto por trade independente do score

---

## FASE G — Arquitetura (quando volume aumentar)

### G.1 — Separar processos
Scanner / Monitor / Executor em processos separados (hoje tudo no Flask)

### G.2 — FSM por token
```
WATCHLIST → MIGRATED → DUMPING → BOUNCE_DETECTED → CONFIRMING → POSITION_OPEN → EXITED
```

### G.3 — IP estático Railway
Hoje: IP muda a cada redeploy → quebra whitelist GMGN
Solução: Railway Static Outbound IP (~$3/mês) — necessário antes de escalar

---

## ✅ BUGS CRÍTICOS CORRIGIDOS — Execução (2026-05-06)

### create_strategy — reescrita completa
**Problema:** 4 bugs impediam a criação de TP/SL. `order_type` errado, `sub_order_type` ausente, `condition_orders` inválido, `priority_fee`/`tip_fee` ausentes.
**Fix:** duas chamadas separadas (`take_profit` + `stop_loss`), baseadas no repositório oficial GMGN. `strategy_id = "tp:{tp_id}|sl:{sl_id}"`.

### _wait_fill — campos errados
**Problema:** bot checava `data.get("status")` e `out_amount`/`in_amount` que não existem.
**Fix:** status em `confirmation.state` ("processed"/"confirmed"). Amounts: `filled_output_amount`/`filled_input_amount`.

### Rate limit global
**Problema:** threads concorrentes do HotMonitor chamavam API durante ban do scanner → ban de 73s virava 127s+.
**Fix:** `_global_ban_until` module-level em `gmgn_client.py` — todas as threads bloqueiam no mesmo gate.

### _recover_pending no startup
**Fix:** ao iniciar com `TRADING_ENABLED=true`, busca trades com `order_id` mas sem `strategy_id` e cria TP/SL automaticamente.

---

## ⚠️ PENDENTE — Validar TP/SL no próximo trade

Verificar que `strategy_id` é salvo como `"tp:XXX|sl:YYY"` no banco após próximo bounce.
Se NULL: checar logs da thread `trade-{id}` e `/debug/sign-test`.

---

## KPIs atualizados (2026-05-06)

| Métrica | Meta | Status |
|---------|------|--------|
| Tokens com recovery definido | ≥1000 | ✅ 1175+ |
| Bounce signals coletados | ≥50 | ✅ 83+ |
| WR base do bounce detector | — | 🟡 13.3% (histórico sem filtros) |
| WR live pós-filtros (Fase A ativa) | ≥25% | 🟡 25% (5/20 trades, n pequeno) |
| WR estimado pós-filtros A.2+A.3 | ≥25% | ⏳ ~29% estimado — confirmar com 30+ bounces |
| Slope filter cobertura | ≥50% | 🟡 20.5% — Fase B deve elevar |
| Swaps executando sem erro | 50 trades | 🟡 20/50 executados |
| TP/SL criados automaticamente | todos os trades | ⚠️ validar no próximo trade |
| EV real > 0 após friction | positivo | ⏳ acumulando |
| H9 validada | delta_pp ≥ 20pp | 🟡 +3.8pp (n=98) — acumulando |
| H18 (velocity) | — | ❌ REFUTADA — não implementar |
| Fase A ativa + 30 bounces filtrados | — | ⏳ aguardando dados |
