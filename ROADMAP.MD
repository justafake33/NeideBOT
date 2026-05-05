# NeideBOT — ROADMAP

> **Regra:** Não avance para o próximo passo sem validar o anterior com dados reais.
> **Data:** 2026-05-05 (atualizado pós-análise 83 bounces)

---

## Status Geral

| Fase | Nome | Status |
|------|------|--------|
| 0 | Blindagem (Sentry, logs) | ✅ Completo |
| 1 | Intelligence Layer (campos Fase 1) | ✅ Completo |
| 2 | Flow Engine (token_signals type 12) | ✅ Ativo — acumulando |
| **A** | **Bounce Detector v2** | ✅ **ATIVO** — aguardando 30+ bounces filtrados |
| **B** | **Cobertura de Ticks** | ✅ **ATIVO** — impacto esperado nos próximos bounces |
| 6-lite | Execução $1 calibração | 🔴 **BLOQUEADO** — AUTH_SIGNATURE_INVALID |
| C | Flow Continuation Score | ⏳ Depende de cobertura de ticks (Fase B maturando) |
| D | Smart Netflow Live (H9) | ⏳ Depende de token_signals acumulados |
| E | Score composto modular | ⏳ Após C+D validados individualmente |

---

## 🔴 BLOQUEIO ATIVO — Signature GMGN

**Problema:** `AUTH_SIGNATURE_INVALID` em todos os swaps.
**Causa raiz:** código correto (validado vs signer.ts oficial). GMGN_WALLET_ADDRESS precisa estar cadastrada + IP do Railway na whitelist.

**Ação pendente do usuário:**
1. Acessar `/debug/sign-test` → verificar que wallet e chave correspondem
2. Cadastrar IP do Railway (`/debug/my-ip`) na whitelist GMGN
3. Confirmar que GMGN_WALLET_ADDRESS está registrada na conta GMGN

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

**Status:** token_signals coletados a cada 5s. H9 pendente de volume de dados.

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

## FASE F — Execução v2 (após calibração com 50 trades funcionando)

### F.1 — Partial Take Profits
- 30% em +30% (segurança rápida)
- 30% em +60%
- 40% trailing stop (captura runners)

### F.2 — Trailing Stop
- Proteção dinâmica após TP1 atingido
- Sair se price cair >20% do pico pós-entrada

### F.3 — Risk Engine em tempo real
Monitorar após entrada:
- Smart sells via token_signals (type 12 sell side)
- Queda de liquidez > 30% → exit emergencial
- Creator vende após entrada → exit imediato

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

## KPIs atualizados (2026-05-05)

| Métrica | Meta | Status |
|---------|------|--------|
| Tokens com recovery definido | ≥1000 | ✅ 1134 |
| Bounce signals coletados | ≥50 | ✅ 83 |
| WR base do bounce detector | — | 🟡 13.3% (histórico sem filtros) |
| WR estimado pós-filtros A.2+A.3 | ≥25% | ⏳ ~29% estimado — confirmar com 30+ bounces |
| Slope filter cobertura | ≥50% | 🟡 20.5% — Fase B deve elevar |
| Swaps executando sem erro | 50 trades | 🔴 BLOQUEADO (signature) |
| EV real > 0 após friction | positivo | ⏳ depende swaps funcionando |
| H9 validada | delta_pp ≥ 20pp | ⏳ acumulando passivamente |
| H18 (velocity) | — | ❌ REFUTADA — não implementar |
| Fase A ativa + 30 bounces filtrados | — | ⏳ aguardando dados |
