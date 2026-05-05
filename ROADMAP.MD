# NeideBOT — ROADMAP

> **Regra:** Não avance para o próximo passo sem validar o anterior com dados reais.
> **Data:** 2026-05-05

---

## Status Geral

| Fase | Nome | Status |
|------|------|--------|
| 0 | Blindagem (Sentry, logs) | ✅ Completo |
| 1 | Intelligence Layer (campos Fase 1) | ✅ Completo |
| 2 | Flow Engine (token_signals type 12) | ✅ Ativo — acumulando |
| 6-lite | Execução $1 calibração | 🔴 **BLOQUEADO** — AUTH_SIGNATURE_INVALID |
| **A** | **Bounce Detector v2** | ⏳ **PRÓXIMO — amanhã** |
| B | Flow Continuation Score | ⏳ Depende de cobertura de ticks |
| C | Smart Netflow Live (H9) | ⏳ Depende de token_signals acumulados |
| D | Score composto modular | ⏳ Após A+B+C validados |

---

## 🔴 BLOQUEIO ATIVO — Signature GMGN

**Problema:** `AUTH_SIGNATURE_INVALID` em todos os swaps.
**Causa raiz:** corpo do swap enviado correto, assinatura correta, mas GMGN_WALLET_ADDRESS precisa estar cadastrada na conta GMGN OpenAPI + IP do Railway na whitelist.

**Ação pendente do usuário:**
1. Acessar `/debug/sign-test` → verificar que wallet e chave correspondem
2. Cadastrar IP do Railway (`/debug/my-ip`) na whitelist GMGN
3. Confirmar que GMGN_WALLET_ADDRESS está registrada na conta GMGN

---

## FASE A — Bounce Detector v2 (implementar amanhã)

**Objetivo:** elevar WR de 15.5% para >35% sem mudar arquitetura.
**Base:** análise `/analise/deep-bounce` com 71 bounces reais.

### A.1 — Slope Filter (🔴 maior impacto)
**Dado:** slope íngreme (≤ -0.0008 %/s) → WR 66.7% vs slope gradual → WR 0.0% (+66.7pp)
**Implementação:** calcular slope dos últimos 2min de ticks antes do sinal em `_check_bounce()`
**Pré-requisito:** cobertura de ticks ≥ 70% (hoje: 22% — fix abaixo)

### A.2 — Bloquear dead zone bounce 20-30%
**Dado:** bounce_pct entre 20-30% tem WR 4% e EV -10% — pior zona de todas
**Implementação:** em `_check_bounce()`, rejeitar se `15% < bounce < 20%` ou `bounce >= 30%` (manter 15-20% e 30%+)
**Impacto estimado:** remove ~25/71 trades ruins sem tirar nenhum TP

### A.3 — Bloquear entry 50-65% da migração
**Dado:** quando `mc_at_signal / mc_migration` entre 50-65% → WR 0% em 15 tokens
**Implementação:** calcular ratio no momento do sinal; se 0.50 ≤ ratio ≤ 0.65 → skip
**Impacto estimado:** remove 15 trades com zero acertos

### A.4 — Persistência como confirmação antes de executar
**Dado:** 3 ticks consecutivos acima do entry → WR 36.4% vs 12.1% (+24.3pp)
**Implementação:** `TradeExecutor` aguarda 90s após sinal e verifica últimos 3 ticks antes de executar swap
**Risco:** pode perder bounces muito rápidos (V-shape) — monitorar

### A.5 — Time-stop 30min → 20min
**Dado:** 100% dos TPs ocorrem em ≤20min após o sinal. Zero TPs entre 20-30min.
**Implementação:** `BOUNCE_TIME_MAX=20.0`
**Impacto:** apenas evita entrar em tokens que já demonstraram não funcionar

---

## FASE B — Cobertura de Ticks (pré-requisito para A.1 e Flow Score)

**Problema atual:** apenas 22% dos bounces têm ticks suficientes para calcular slope.
**Causa:** após dump, token sai do feed trenches → sem ticks trenches. kline back-fill a cada 60s resolve parcialmente.

**Ações:**
- [ ] Verificar se `_precise_bottom_tick()` está inserindo kline ticks durante todo o dump (não só após detecção)
- [ ] Aumentar frequência do hot-precise de 60s para 30s (trade-off: dobra uso de rate limit kline)
- [ ] Adicionar kline retroativo no momento da detecção de bounce (buscar últimos 5min de candles 1m)

**Meta:** cobertura ≥ 70% dos bounces com slope calculável antes de implementar A.1

---

## FASE C — Flow Continuation Score

**Dependência:** cobertura de ticks resolvida (Fase B)

**O que é:** após bounce signal, verificar nos primeiros 5 ticks:
- Higher low em relação ao fundo
- Higher high em relação ao tick anterior
- Volume crescente (se disponível)

**Por que:** é a evolução natural do filtro de persistência (A.4). Não só "preço acima do entry" mas "estrutura de uptrend formando".

**Implementação:** módulo separado `flow_score.py`, chamado pelo TradeExecutor antes de executar swap.

---

## FASE D — Smart Netflow Live (H9 em tempo real)

**Dependência:** token_signals acumulados (~100 tokens com sinal durante bounce)

**O que é:** nos 3 minutos ao redor do bounce signal, houve smart buy (type 12)?
- `trigger_mc < mc_min * 1.20` → smart wallet comprou perto do fundo → muito bullish
- Múltiplos triggers na janela → acumulação real

**Status:** token_signals já coletados a cada 5s. H9 pendente de volume de dados.

---

## FASE E — Scores Compostos (após A+B+C+D validados individualmente)

Baseado nas propostas avaliadas (2026-05-05):

| Score | Componentes validados | Componentes pendentes |
|-------|----------------------|----------------------|
| **Dump Intensity** | slope (✅ +66.7pp) | volume_spike (dados insuficientes) |
| **Supply Risk** | top70_sniper_hold_rate (✅), creator_close (✅ +10pp) | bundler, insider (não validados para bounce) |
| **Buyer Quality** | token_signals type 12 (H9 pendente) | snapshot counts (❌ H3 refutou) |
| **Flow Continuation** | persistência (✅ +24.3pp) | higher lows/highs (depende cobertura) |

> ⚠️ Não construir scores com pesos arbitrários. Cada componente deve ser validado individualmente antes de combinar. Compostos prematuros diluem o sinal mais forte.

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
| Bounce signals coletados | ≥50 | ✅ 71 |
| WR live do bounce detector | ≥20% | 🔴 15.5% (precisa Fase A) |
| Slope filter cobertura | ≥70% dos bounces | 🔴 22% (precisa Fase B) |
| Swaps executando sem erro | 50 trades | 🔴 BLOQUEADO (signature) |
| EV real > 0 após friction | positivo | ⏳ depende swaps funcionando |
| H9 validada | delta_pp ≥ 20pp | ⏳ acumulando passivamente |
| token_ticks cobertura | ≥70% dos bounces | 🔴 22% |
