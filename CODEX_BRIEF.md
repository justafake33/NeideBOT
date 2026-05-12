# NeideBOT — Briefing para Análise Externa

**Repositório:** https://github.com/justafake33/neidebot  
**Branch de desenvolvimento:** `claude/initial-setup-gnyqM`  
**Data:** 2026-05-12

---

## O que é este projeto

Bot autônomo de trading em Solana. Estratégia: detectar tokens que acabaram de migrar da bonding curve do Pump.fun para um DEX (pump.fun pAMM), aguardar o dump pós-migração, e comprar o bounce do fundo.

**Infraestrutura construída (funcional, em produção):**
- Scanner de tokens em tempo real via GMGN OpenAPI (poll 5s)
- Detecção de bounce com múltiplos filtros
- Execução de trades via GMGN swap com trailing TP + SL embutidos
- WebSocket Helius para preço em tempo real (~5ms via pump.fun pAMM events)
- PostgreSQL com 1175+ tokens históricos
- Dashboards de análise em Flask

---

## Resultado real (on-chain, GMGN como fonte de verdade)

**WR: ~15%** (GMGN, ~80 trades executados a $1 cada)

| Parâmetro atual | Valor |
|---|---|
| Size por trade | $1 |
| SL | -25% da entrada |
| TP | Trailing: ativa em +75%, drawdown 30% do pico |
| WR real (GMGN) | ~15% |
| Melhor trade | Kevin +345% |
| Resultado líquido | Negativo (margem apertada) |

**Break-even com parâmetros atuais:**
- SL=25%, avg trailing gain ~100-150%: WR mínimo para EV>0 ≈ 20%
- Estamos em 15% → **EV levemente negativo**

---

## A estratégia em detalhe

```
1. Token completa bonding curve no Pump.fun → migra para pAMM
2. Early buyers realizam lucro → dump de 30-80% em minutos
3. Bot detecta o fundo (menor MC) e aguarda bounce ≥ 30% do fundo
4. Entra no trade com trailing TP (ativa +75%) e SL fixo (-25%)
5. Janela total: migração → sinal ≤ 20min (filtro A.5)
```

**Filtros ativos (todos validados em live trades):**
- A.2: bloqueia bounce 20-30% (WR 3.1% histórico)
- A.3: bloqueia entry 50-65% do MC de migração (WR 0%)
- A.5: bloqueia elapsed > 20min desde migração
- A.6: bloqueia fundo "velho" > 5min (ts_min freshness)
- A.7: bounce mínimo ≥ 30%
- smart_degen_count ≥ 2

---

## O que já foi tentado e FALHOU

| Tentativa | Resultado | Por quê falhou |
|---|---|---|
| SL=15% | Muitos SLs falsos (W-bottom) | Ruído de mercado abaixo do fundo detectado |
| SL=25% | Mais largo mas WR caiu | Break-even agora exige 20% WR |
| A.4 (persistência 90s após sinal) | Entrada 53% acima do fundo | Matava todo R:R |
| Bounce > 50% filter | Revertido | Baseado em DB WR inválido |
| TTB < 15min filter | Revertido | Baseado em DB WR inválido |
| CTO block filter | Revertido | Baseado em DB WR inválido |
| H13 (slope pré-fundo) | Refutado n=153 | Recovery rate flat em todas as faixas |
| W-bottom anchor SL | Refutado | 90% dos SLs caem muito abaixo do fundo (mediana 0.55×) |
| Dip entry (-5% a -25% do sinal) | Piora em todas as faixas | Tokens que subiram não voltaram; os que voltaram continuaram caindo |

---

## O que funciona (validado)

| Achado | Dado | Status |
|---|---|---|
| H10: TTB < 25min → recovery 61% vs 39% | n=425 tokens históricos | ✅ Robusto |
| Trailing TP supera TP fixo | Sim: avg 159% vs 71% (simulação n=119) | ✅ Em uso |
| Helius inline parse | Preço em ~5ms via pump.fun pAMM events | ✅ Em produção |
| H9 (smart money signal type 12) | +11.8pp na faixa trigger<50% migração (n=14) | ⏳ Amostra pequena |

---

## O problema central (diagnóstico honesto)

**1. WR de 15% é insuficiente com SL=25%**

Para EV positivo com SL=25%:
- Se avg TP = 75%: WR mínimo = 25/(25+75) = **25%**
- Se avg TP = 150% (trailing): WR mínimo = 25/(25+150) = **14.3%** — estamos no limite

**2. Entrada está longe do fundo real**

O sinal dispara quando bounce ≥ 30% do fundo detectado. Mas o GMGN demora alguns segundos para executar. Resultado: entramos a ~30-50% acima do fundo. Isso comprime o R:R e faz o SL disparar em ruídos normais de mercado.

**3. 90% dos SLs são dumps que continuam (não W-bottoms)**

Análise de 68 SLs com ticks disponíveis:
- 90% têm preço caindo para 0.20-0.85× mc_bottom após entrada
- Isso não é correção antes de subir — é continuação do dump
- Esses tokens não tinham comprador real no fundo

**4. DB WR é inválido (arquitetural)**

TradeMonitor infere TP/SL via token_ticks (30s) — após GMGN fechar posição, ticks posteriores geram falsos registros. DB mostra 34% WR mas GMGN mostra 15%. **Todo discriminador calculado via DB é suspeito.**

---

## Arquitetura técnica (arquivos principais)

```
scanner.py      — Poll GMGN trenches, HotMonitor, bounce detector, signal poll
executor.py     — TradeExecutor (swap), TradeMonitor (detecta exit via ticks)
helius_ws.py    — WebSocket Helius: parse pump.fun pAMM events inline
gmgn_client.py  — Cliente GMGN: trenches, token_signal, swap, query_order
db.py           — PostgreSQL: migrations, token_ticks, bounce_signals, trades
app.py          — Flask: dashboards de análise (/analise/*, /debug/*)
```

**Tabelas relevantes:**
- `migrations`: 1175+ tokens migrados com ~40 campos de qualidade
- `token_ticks`: série temporal de preço por token (30s trenches + 1min kline)
- `bounce_signals`: registro de cada sinal disparado
- `trades`: log de cada trade executado (outcomes do DB são inválidos — usar GMGN)
- `token_signals`: sinais H9 (smart money type 12) por token

**Dashboards disponíveis (em produção no Railway):**
- `/analise/wr-breakdown` — discriminadores de WR (atenção: DB inválido)
- `/analise/wbottom` — análise H21 do retest do fundo
- `/analise/h9` — smart money signal vs recovery
- `/debug/bounce-list` — bounces recentes com links DexScreener
- `/debug/wallet-audit` — auditoria on-chain

---

## A pergunta central para análise externa

> Com esta infraestrutura (detecção de migração em tempo real, preço em ~5ms via Helius, 1175+ tokens históricos, GMGN API com execução), **o que está faltando para gerar lucro real?**

Hipóteses abertas que ainda não foram testadas com dados suficientes:

1. **A estratégia de pós-migração tem edge real?** Ou 15% WR é o teto estrutural desse padrão?

2. **O problema é o SINAL ou a EXECUÇÃO?** Se entrássemos exatamente no fundo (hipotético), qual seria o WR? O W-bottom analysis sugere que 90% dos SLs são tokens que nunca iam bounçar — isso é seleção de sinal, não execução.

3. **Existe uma estratégia melhor com a mesma infraestrutura?** Exemplos:
   - Comprar na migração (bc_progress = 100%) antes do dump, com SL apertado
   - Usar H9 (smart money) como filtro obrigatório — 75% WR em n=4 (amostra pequena)
   - Focar apenas em tokens com TTB < 5min (dump de capitulação real)
   - Combinar smart_degen_count ≥ 3 + creator_hold (dev ainda segura) como pré-requisito

4. **O SL é o problema ou é o sintoma?** 90% dos SLs caem abaixo do fundo — isso significa que o fundo que detectamos não é o fundo real nesses tokens. Como identificar ANTES da entrada se o fundo é real ou temporário?

5. **Existe uma métrica de qualidade do bounce que discrimine?** Volume no candle de bounce? Velocidade de subida? Número de compradores distintos no HotMonitor?

---

## O que Codex pode fazer

1. **Ler o código completo** — entender a arquitetura end-to-end
2. **Analisar os dados históricos** — 1175 tokens com campos de qualidade completos
3. **Propor hipóteses não testadas** — o que os dados sugerem que não foi explorado?
4. **Identificar bugs ou ineficiências** — existe algo no pipeline que está ativamente prejudicando o WR?
5. **Recomendar a próxima hipótese formal** — o que testar com os próximos 30 trades?

**Pergunta direta:** Com acesso ao repositório e aos dados, o que você mudaria primeiro?

---

## Contexto de mercado

- Tokens Pump.fun migram quando bonding curve completa (~$69k em compras)
- Dump pós-migração mediano: 72% do MC de migração
- Fundo mediano: 13.6 minutos após migração
- 78% dos tokens não fazem bounce real (recovery = mc_1h > mc_bottom × 1.3)
- Janela de oportunidade: minutos, não horas

