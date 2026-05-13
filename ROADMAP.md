# NeideBOT — Roadmap

**Atualizado:** 2026-05-12 (sessão 2)
**Estado:** Bot ativo a $1/trade · WR **~15% (GMGN ground truth)** · filtros A.2/A.3/A.5/A.6/A.7/smart≥3 ativos · H22 em coleta · DB WR inválido — usar apenas GMGN para métricas

---

## ✅ Concluído

| Item | Data | Resultado |
|------|------|-----------|
| Coleta de dados pós-migração | 2026-04 | 1175+ tokens, 1134 com recovery definido |
| H10 validada (time_to_bottom) | 2026-04 | +21.8pp (n=425) — único preditor robusto histórico |
| Backtester tick-level | 2026-05-01 | EV +14.7% (TP=100%, SL=15%, n=68) |
| Fase A — filtros A.2, A.3, A.5, A.6 | 2026-05 | WR estimado 29% pós-filtros (baseado em DB — ver nota abaixo) |
| Trailing TP via condition_orders | 2026-05-09 | avg 159% vs 71% TP fixo (simulação n=119) |
| Calibração $1/trade (53 trades) | 2026-05-07/08 | WR 15.1% on-chain · friction = ATA rent |
| Auditoria on-chain (115 txs) | 2026-05-10 | Jito=$0, SL executa certo, ATA rent ~$0.20/SL |
| Bot relançado | 2026-05-10 | TRADING_ENABLED=true, sem limite de trades |
| Helius inline parse (Tier 1) | 2026-05-12 | Elimina janela cega de 2s do rate limit — preço em ~5ms via pump.fun pAMM |
| pump.fun pAMM identificado | 2026-05-12 | `pAMMBay6...` — BuyEvent/SellEvent com pool reserves offsets 48/56 |
| A.7 — BOUNCE_MIN_PCT=0.30 | 2026-05-11 | bounce 15-20% → 9.1% WR live (n=11) — dead zone |
| FILTER_SMART_MIN=2→3 | 2026-05-12 | smart=1→0% WR, smart=2→20%, smart=3→57% (dado live GMGN) |
| Fix TradeMonitor overwrite bug | 2026-05-11 | `get_open_trades()` só retorna `status='open'` |
| Fix wr-breakdown status bug | 2026-05-12 | Query buscava `tp_hit` mas DB salva `tp` — 27 TPs ignorados |
| TradeMonitor trailing exit detection | 2026-05-12 | `_check_trailing_exit()` simula drawdown 30% via token_ticks |
| H21 W-bottom REFUTADA | 2026-05-12 | 90% dos SLs caem muito abaixo do fundo (mediana 0.55×). Não é W-bottom, é dump que continua. |
| Filtros bounce>50%/TTB<15min/CTO | 2026-05-12 | **IMPLEMENTADOS E REVERTIDOS** — baseados em DB WR inválido. Lição aprendida. |
| /analise/wbottom | 2026-05-12 | Dashboard H21 — retest do fundo após entrada |
| /debug/bounce-list | 2026-05-12 | Lista bounces ≥ X% com links DexScreener/GMGN, auto-refresh 30s |
| CODEX_BRIEF.md | 2026-05-12 | Briefing para análise externa — diagnóstico completo + hipóteses abertas |
| Análise big player (síntese ChatGPT + sessão) | 2026-05-12 | Identificado detector de absorção pós-fundo como próxima grande alavanca |
| **H22 — Coleta de absorção implementada** | 2026-05-12 | `helius_ws.py`: deque(maxlen=300) por mint acumula `{ts, side, sol, mc}` de cada BuyEvent/SellEvent do pAMM. `get_absorption_window(mint, 60)` expõe buy_vol, sell_vol, counts, ratio, largest_buy. `bounce_signals` recebe 8 campos de absorção. `/debug/absorption` mostra dados ao vivo + histórico. Score/grade **não calculados** — coleta de métricas brutas apenas. |
| **H22 — Score removido (dados antes de fórmula)** | 2026-05-12 | `_absorption_score()` deletada do scanner.py. Score não existe sem correlação real com TP/SL. Construir fórmula APÓS n≥30 bounces com `absorption_events > 0` e outcomes GMGN confirmados. |
| **FILTER_PUMP_ONLY=true** | 2026-05-13 | Scanner rejeita migrações onde `exchange` não contém "pump". Garante que todos os futuros bounces sejam H22-capturáveis via Helius pAMM. `exchange` propagado por HotMonitor → bounce_signals. |
| **`/analise/h22-correlation`** | 2026-05-13 | Dashboard H22: 3 tabelas (absorption_events buckets, buy/sell ratio, buy_concentration). Instrumento de medição pronto. Gate: n≥30 com Helius data. |
| **Fix mc_peak inflado (kline high → close)** | 2026-05-12 | Kline `high` incluía spikes intra-candle de transações em pools com baixa liquidez — um único whale buy pode empurrar o AMM price 200x por <1s. Scanner mostrava ganhos de 5000-25000% (ex: DIAMOND $9M peak mas mc_1h = $39K). Fix: `_precise_bottom_tick()` e `_graduate()` agora usam `close × supply` para mc_peak. `mc_high` nos token_ticks mantido para o backtester. |
| **`/analise/post-sl-recovery`** | 2026-05-12 | Para cada SL, busca o max MC nos token_ticks após exit_ts. 12% dos SLs (8/64 com dados) teriam atingido o TP depois. 88% são dumps genuínos que continuaram. ⚠️ Número pendente de correção: usa `entry_expected` (mc_bottom × 1.15), não entrada real (`entry_price_usd / tokens_received × 1B`). |

> ⚠️ **Nota sobre WR histórico**: O DB WR (chegou a mostrar 34%) é inválido. TradeMonitor infere TP/SL via token_ticks e gera falsos positivos após GMGN fechar a posição. **GMGN = 15% WR = ground truth.**

---

## 🔜 Curto Prazo

### 1. FILTER_SMART_MIN=3 ← **IMEDIATO (env var Railway, zero código)**

**Por quê:** Dado live/GMGN mais limpo disponível. smart=3 → 57% WR vs smart=2 → 20% WR. Discriminação +37pp. Zero linhas de código, efeito imediato, reversível.

**Como:** Railway → FILTER_SMART_MIN=3 (sem redeploy necessário).

**Gate:** Acumular n≥30 trades com smart≥3 antes de qualquer outra mudança de código.

**Tradeoff:** Menor frequência de sinais. Aceitar — a estratégia vive de qualidade, não de volume bruto.

---

### 2. Acumular n≥30 trades GMGN com smart≥3 ← **AGORA (paralelo ao bot)**

**Por quê:** Todos os discriminadores do DB são suspeitos. Precisamos de n≥30 outcomes confirmados via GMGN com os filtros atuais antes de qualquer nova mudança de código.

**Como medir:** Cruzar `order_id` de cada trade com GMGN para obter outcome real. Ou usar `/debug/wallet-audit` para reconciliar txs on-chain.

**Gate para qualquer novo filtro:** n≥30 outcomes GMGN mostrando discriminação ≥10pp.

**Não mudar nada enquanto coleta.**

---

### 3. Detector de absorção pós-fundo via Helius pAMM ← **✅ IMPLEMENTADO (fase coleta)**

**Status:** Infraestrutura de coleta ativa. Score/filtro aguardam dados.

**O que foi implementado:**
- `helius_ws.py`: deque(maxlen=300) por mint acumula `{ts, side, sol, mc}` de cada BuyEvent/SellEvent do pAMM
- `get_absorption_window(mint, window_sec=60)` → retorna `{buy_vol, sell_vol, buy_count, sell_count, largest_buy, total_events}`
- `bounce_signals` table: 8 colunas novas (`absorption_buy_vol`, `sell_vol`, `buy_cnt`, `sell_cnt`, `lrg_buy`, `events`, `score=NULL`, `grade=NULL`)
- `/debug/absorption`: mostra janela ao vivo por token + últimos 30 bounces com ratio buy/sell
- `/analise/post-sl-recovery`: para cada SL, busca max MC nos ticks após exit (12% teriam atingido TP)

**Score/grade: deliberadamente removidos.** Construir fórmula sem correlação com outcomes é julgar o peixe por não subir em árvore. Coletar → correlacionar → só então pontuar.

**⚠️ COBERTURA HELIUS — DIAGNÓSTICO (2026-05-13):**
```sql
-- bounce_signals: total=529+, com_helius=23+, sem_helius=~520
-- Cobertura real: ~4.3% e crescendo (era 1.7% no início da sessão 2026-05-13)
```
529+ bounces registrados. **23+ têm `absorption_events > 0`** (cresceu de 9 durante a sessão). Restantes têm NULL.
Causa provável: Helius subscreve o mint quando o token entra no HotMonitor, mas os BuyEvent/SellEvent do pAMM chegam com baixa taxa para a maioria dos tokens — muitos bounces disparam em janelas onde poucos swaps ocorreram pela subscrição Helius.

**Implicação direta:** Tarefa B (h22-correlation) é prematura. Com n=9 observações, qualquer correlação é ruído.

**Próximos passos desta hipótese:**
1. ✅ Acumular eventos por mint (done)
2. ✅ Salvar métricas brutas por bounce (done)
3. ⏳ Diagnósticar por que cobertura é 1.7% — Helius não está chegando para a maioria dos tokens?
4. ⏳ Acumular n≥30 bounces com `absorption_events > 0` + outcomes GMGN confirmados
5. Análise: qual métrica discrimina TP vs SL? (ratio? total_events? largest_buy?)
6. Construir score baseado na correlação real encontrada
7. Ativar como filtro se WR ≥15pp maior com absorção confirmada

**Gate para virar filtro:** n≥30 bounces com dados Helius + WR ≥15pp maior no grupo com absorção vs sem absorção (GMGN como fonte).

---

### 4. Correções analíticas pendentes (Codex review 2026-05-13)

**A — `/analise/post-sl-recovery`: usar entrada real** ← OBRIGATÓRIO antes de confiar no "12%"

Hoje usa `entry_expected = mc_bottom × 1.15`. Deve usar `(entry_price_usd / tokens_received) × 1B` quando disponível (já salvo no banco). Entrada real pode ser 30-50% acima do esperado por latência e slippage — muda o TP/SL threshold e pode alterar o número de "SLs que teriam TP". Fórmula já existe em `/analise/wbottom`.

Também: separar `sl` de `expired`. `expired` com `pnl_pct_real ≥ 0` não é SL real.

**B — `buy_concentration = largest_buy / buy_vol`** ← Zero custo (dados já existem)

`largest_buy` e `buy_vol` já estão em `bounce_signals`. Expor concentração no `get_absorption_window()` e no `/debug/absorption`. Diferencia "um whale isolado pumpou" de "múltiplos compradores absorveram". Não requer schema change.

**C — Display `/debug/absorption`: "∞" vs "no flow"**

`sell_vol=0` com `buy_vol>0` = sinal mais bullish (ninguém quis vender). Mostrar "—" para os dois casos esconde informação. `buy_vol>0 e sell_vol=0` → "∞". `ambos=0` → "no flow".

**D — Snapshots pós-bounce +30s/+60s** ← BAIXA prioridade até cobertura Helius ser resolvida

Com 1.7% de cobertura real, snapshots pós-bounce também virão zerados para 98% dos tokens. Implementar depois que cobertura subir para nível útil.

**E — `/analise/h22-correlation`** ← ✅ IMPLEMENTADO (2026-05-13)

Dashboard mostra correlação H22 → outcome com n atual. 3 tabelas: por `absorption_events` bucket, por buy/sell ratio, por `buy_concentration` (largest_buy/buy_vol). Detalha últimos bounces com dados Helius. Conclusões só valem com n≥30 — instrumento pronto para o instante que o gate for atingido.

---

### 5. H9 como tier de confluência (não gate obrigatório)

**Por quê:** H9 live mostra 75% WR (n=4) em ratio<0.80 — sinal mais forte encontrado. Mas n=4 é insuficiente para bloquear trades sem H9.

**Como usar enquanto coleta dados:**
- smart≥3 + fresh + H9 ratio<0.80 = **Setup A+** → candidato a 2× size após validação
- smart≥3 + fresh sem H9 = **Setup A** → size normal
- H9 ratio>1.00 = reverse causality confirmada — ignorar (token já bounçou antes do sinal)

**Gate para 2× size em A+:** n≥15 em ratio<0.80 com WR ≥50% GMGN.
**Gate para gate obrigatório:** n≥30 e WR ≥50% confirmado GMGN.

---

### 5. Re-entry — Segunda chance em tokens com TP anterior

**Por quê:** Maior multiplicador de frequência sem relaxar filtros. Tokens com TP histórico demonstraram bounce real.

**Lógica:**
- `db.get_tp_tokens()` — mints com ≥1 TP histórico
- Em `_check_bounce()`: se mint tem TP histórico, `bounce_fired` não bloqueia re-entry
- Opcional: 2× size para esses tokens

**Gate:** Confirmar WR estável em 30+ trades antes de implementar.

---

### 6. Reclaim Rent automático

**Problema:** Cada SL deixa ATA vazio com ~$0.20 preso.

**Implementação:** Após SL, `closeAccount` via Solana RPC.
**Pré-requisito:** `HELIUS_RPC_URL` + `WALLET_PRIVATE_KEY`.

---

## 📋 Médio Prazo

### 7. Escalar para $10/trade → $50/trade → $100/trade

**Gate único para cada salto:** WR ≥ 20% confirmado em ≥30 trades no GMGN no size atual.

**Break-even por size:**
| Size | ATA rent | GMGN fees | Total friction | Break-even WR |
|------|----------|-----------|----------------|---------------|
| $1   | 20%      | 2%        | ~22%           | ~18%          |
| $10  | 2%       | 2%        | ~4%            | ~12%          |
| $50  | 0.4%     | 2%        | ~2.4%          | ~11%          |
| $100 | 0.2%     | 2%        | ~2.2%          | ~11%          |
| $100 (Jupiter) | 0% | 0.5% | ~0.5%       | ~9%           |

---

### 8. Helius RPC + Jupiter Swap

**Por que:** GMGN cobra 1%×2 = 2% + ATA rent. Jupiter = 0% platform fee + fecha ATA automaticamente.

**Pré-requisito:** WR confirmado em $10/trade. Não migrar infraestrutura antes.

**Friction estimada pós-migração a $100/trade:**
| Custo | Atual (GMGN) | Após (Jupiter+Helius) |
|-------|-------------|----------------------|
| Platform fee | 2% | ~0.5% (LP fee) |
| ATA rent | ~0.2% | $0 (Jupiter fecha ATA) |
| **Total a $100** | **~$2.24 (2.2%)** | **~$0.52 (0.5%)** |

---

### 9. Score de confluência e sizing por tier (longo prazo)

**Pré-requisito:** n≥200 trades GMGN com dados limpos. Sem isso, qualquer bucket tem <50 amostras — insuficiente para decisão.

**Exemplo de tiers (depois dos dados):**
| Tier | Condições | Ação |
|------|-----------|------|
| A+ | smart≥3 + absorção confirmada + H9<0.80 | 2× size |
| A  | smart≥3 + absorção confirmada | 1× size normal |
| B  | smart≥3 sem absorção | min-size / coleta |
| C  | smart=2 + H9 | paper / não operar |

---

## 📊 Métricas de Decisão

| Decisão | Gate |
|---------|------|
| Qualquer novo filtro | n≥30 trades confirmados GMGN, discriminação ≥10pp |
| Absorção pós-fundo como log | Implementar na próxima sessão de código |
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
3. **A pergunta certa não é "subiu 30%?" — é "quem comprou esse fundo?"** Filtros de preço são proxy ruim para presença de comprador real. A grande alavanca é medir fluxo de ordens pós-fundo via Helius pAMM.
4. **WR alto vem de seleção, não de stop.** 90% dos SLs são tokens que continuaram dumpando — não há SL mágico que resolva isso. Foco: não comprar o primeiro bounce falso.
5. **Volume qualificado, não size.** Edge está em frequência de trades qualificados. Teto ~$100-200/trade.
6. **Escalar depois de confirmar.** Cada salto de size exige validação no size menor.
7. **One change at a time.** Não mudar size + infraestrutura + filtros simultaneamente.
8. **Reverta rápido.** Filtro adicionado sem dados válidos → reverter imediatamente. Sem apego.
9. **Tiers e scoring são long game.** Com n<200 trades, qualquer bucket é ruído. Acumular dados primeiro.

---

## 🗃️ Hipóteses — Placar

| # | Hipótese | Resultado |
|---|----------|-----------|
| H1 | ≥70% dos tokens caem ≥20% do ATH nos primeiros 60min | ✅ 91.4% |
| H2 | Fundo em ≤15min em tokens com recovery (≥70%) | ❌ 49.7% |
| H3 | smart_wallets ≥3 → recovery ≥15pp maior | ❌ +0.2pp (snapshot token-level — no trade-level smart≥3 discrimina) |
| H4 | sniper ≥30% → recovery <30% | ❌ levemente positivo |
| H7 | liq <$5k → slippage inaceitável >$50 | ⏳ pendente |
| H8 | wash_trading → recovery ≥20pp menor | ❌ -0.1pp |
| H9 | signal type 12 trigger<80% mig → recovery ≥20pp maior | 🟡 +11.8pp histórico (n=14) · 75% WR live (n=4 ratio<0.80) |
| H10 | time_to_bottom < 25min → recovery ≥20pp maior | ✅ **+21.8pp** (n=425) |
| H11 | Deceleração pré-fundo discrimina recovery | ❌ invertido — recovery = capitulação brusca |
| H12 | dump_depth + time_elapsed → zona P(recovery)>50% | ⚠️ parcial — time domina |
| H13 | Slope íngreme pré-fundo → WR ≥40pp maior | ❌ flat em n=153 |
| H14 | bounce 20-30% = dead zone | ✅ WR 3.1% (n=32) — A.2 ativo |
| H15 | Entry 50-65% mig → WR=0% | ✅ 0/21 — A.3 ativo |
| H16 | 3 ticks acima entry → WR ≥20pp maior | ✅ +18pp — A.4 removido (entrada muito alta) |
| H17 | 100% TPs em ≤20min após sinal | ✅ 11/11 ≤20min — A.5=20min |
| H18 | Bounce velocity discrimina WR | ❌ flat em todos os tercis |
| H19 | 1-2min delay melhora EV | ✅ +6.6pp — coberto por A.4, mas A.4 removido |
| H20 | W-bottom = sinal de confirmação | ❌ 98% prevalência — ubíquo |
| H21 | SL ancorado no fundo (mc_bottom × 90%) resolveria a maioria dos SLs | ❌ **REFUTADA** — 90% dos SLs caem abaixo de 0.90× o fundo (mediana 0.55×) |
| H22 | Buy volume > sell volume nos 60s pós-fundo discrimina WR ≥15pp | ⏳ **DATA COLLECTION** — janela rolante ativa, métricas brutas salvas em bounce_signals. Score aguarda n≥30 outcomes GMGN. |

---

## 🔧 Lições Aprendidas

1. **DB WR é lixo como métrica**: TradeMonitor infere, não observa. GMGN executa.
2. **Nunca filtrar com dados corrompidos**: bounce>50% e TTB<15min pareciam 50% WR mas eram artefato de falsos TPs.
3. **pump.fun pAMM, não Meteora**: tokens migrados do pump.fun vão para `pAMMBay6...`, não `cpamd...`.
4. **Helius blind window era o gap real**: 2s de rate limit entre eventos = V-bottom inteiro perdido.
5. **W-bottom não existe nos SLs**: tokens que viram SL continuam caindo muito abaixo do fundo. O fundo que detectamos não é o fundo real nesses casos.
6. **O único preditor robusto histórico ainda é H10 (TTB)**: tudo mais ou é ruído ou precisa de mais dados GMGN.
7. **smart_count em ticks é snapshot, não compra ativa**: não discrimina no token-level. No trade-level smart≥3 discrimina fortemente — smart=3 → 57% WR vs smart=2 → 20%.
8. **A pergunta errada gera filtros errados**: perguntar "subiu 30%?" detecta padrão de preço, não intenção. A pergunta certa é "tem comprador real absorvendo o sell pressure?" — Helius pAMM events são a infraestrutura para responder isso.
9. **Dip entry não funciona**: tokens que foram ao TP nunca retrocederam o suficiente; tokens que retrocederam continuaram caindo. O timing atual está correto — o problema é qualidade do sinal.
10. **Tiers e scoring são prematuros**: com n<200 trades GMGN limpos, qualquer bucket tem amostra fraca. Acumular dados primeiro, modelar depois.
11. **BOTTOM_FRESHNESS_SEC=180 é baixa alavancagem**: live trades já têm ts_min a 0.1-4.5min do sinal. A.6 a 300s já resolve o problema em produção. Não apertar sem evidência de inválidos chegando com gap 3-5min nos logs atuais.
12. **Score sem dados é ilusão de precisão**: fórmula calculável ≠ fórmula útil. `_absorption_score()` foi removida porque não havia correlação validada com TP/SL. Coletar métricas brutas → analisar discriminação → SÓ ENTÃO construir score com pesos que os dados justificam.
13. **Kline `high` inflava mc_peak com spikes intra-candle**: em pools com 79 SOL de liquidez, um único trade pode temporariamente empurrar o preço AMM 200x. O kline registra isso como o `high` da vela mesmo que o close volte ao normal. Fix: usar `close × supply` para mc_peak (push_mc já usa close implicitamente). `mc_high` mantido nos token_ticks para o backtester (TP detection intra-vela).
14. **Cobertura de infraestrutura ≠ dados disponíveis**: implementar H22 (coleta de absorção) não significa ter dados. Com 529 bounces e apenas 9 `absorption_events > 0` (1.7%), toda análise de correlação é prematura. Verificar cobertura ANTES de construir dashboard analítico. Pergunta certa: "quantos bounces têm dados reais?" — não "a infraestrutura está implementada?".
15. **`entry_expected` ≠ entrada real**: o executor calcula `entry_target = mc_bottom × 1.15` antes do swap. A entrada real pode ser 30-50%+ acima por latência e slippage. Qualquer análise de TP/SL threshold que use `entry_expected` pode estar errada para cima ou para baixo. Sempre usar `(entry_price_usd / tokens_received) × 1B` quando disponível — já salvo no banco desde Fase 6.
