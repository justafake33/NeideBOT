# Prompt para ChatGPT — NeideBOT Edge Discovery

---

## CONTEXTO: O QUE É ESSE BOT

Sou um bot autônomo de trading em Solana que foca em tokens recém-migrados do Pump.fun para o pAMM (programa `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`).

**Estratégia central**: quando um token completa a bonding curve do Pump.fun, ele migra para um pool AMM. Os early buyers realizam lucro → dump imediato e severo (~72% mediano). Minha aposta é detectar o fundo desse dump e comprar o bounce.

**Timeframe**: a janela relevante são os primeiros ~20 minutos após a migração. 100% dos TPs históricos ocorreram em ≤20 minutos após o sinal de bounce.

**Resultado real (dados on-chain)**: WR ~29% com trailing TP ativo, EV marginalmente positivo a $1/trade. WR blockchain real de 10.1% foi medido em período anterior com dados corrompidos — esses dados não são válidos.

---

## INSTRUÇÕES

Quero que você pense como um bot lucrativo de memecoins na Solana. Não como analista, não como pesquisador — como o código que roda em produção com capital real, em tempo real, competindo contra MEV bots, snipers, market makers e outros algoritmos.

Analise tudo que está descrito abaixo e responda:

**1. Onde estão os edges que eu não estou vendo?**
**2. O que os bots lucrativos de memecoins fazem que eu não faço?**
**3. Que dados já existem no meu pipeline que eu coletou mas não usa para decisão?**
**4. Que sinais externos eu poderia consumir mas não consomo?**
**5. Qual seria sua arquitetura de decisão se você fosse construir esse bot do zero hoje?**

---

## ARQUITETURA ATUAL

### Stack
- Python + PostgreSQL (Railway)
- GMGN OpenAPI — discovery, execução de trades
- Helius/erpc WebSocket — eventos on-chain em tempo real (~5ms latência)
- Telegram Bot — alertas

### Fluxo de dados

```
erpc global transactionSubscribe (pAMM)
  → parse BuyEvent/SellEvent (discriminador Anchor sha256[:8])
  → extrai pool_base, pool_quote → preço = pool_quote/pool_base
  → filtra por pool válida (k-gate + MIN_POOL_SOL=10)
  → acumula rolling deque(300 eventos) por mint

Scanner (5s poll da API GMGN)
  → near_completion: tokens com bonding curve >= 80% → watchlist
  → completed: tokens migrados → HotMonitor (janela 60min)
  → _poll_signals: SmartDegenBuy type 12 a cada 5s

HotMonitor (por token, janela 60min)
  → push_mc (5s): MC atual do trenches
  → hot-precise (5s): kline 1m para low/high
  → _check_bounce(): dispara sinal quando condições satisfeitas
  → _graduate (60min): checkpoint final

TradeExecutor (a cada bounce)
  → swap via GMGN API
  → trailing TP (ativação 100%, drawdown 30%) + SL fixo 25%
  → monitora fill via Helius RPC
```

---

## SINAIS QUE EU COLETO

### Do trenches GMGN (estáticos, no momento da migração):
- `smart_degen_count` — wallets classificadas como "smart" pelo GMGN
- `renowned_count` — KOLs
- `sniper_count` / `top70_sniper_hold_rate` — snipers ainda dentro
- `bundler_trader_amount_rate` — % de bundlers
- `rat_trader_amount_rate` — % de rat traders
- `bot_degen_rate` — % de bots
- `rug_ratio` — score de risco GMGN
- `top_10_holder_rate` — concentração
- `creator_token_status` — "creator_hold" ou "creator_close"
- `creator_balance_rate` — % do dev
- `entrapment_ratio` — manipulação de preço
- `burn_status` — liquidez queimada?
- `cto_flag` — comunidade assumiu?
- `is_wash_trading` — wash trading?
- `x_user_follower` — seguidores no Twitter
- `has_at_least_one_social`
- `total_fee_sol` — fee total acumulada (proxy de volume real)
- `bc_progress` — % da bonding curve
- `complete_cost_time` — segundos criação → migração

### Do erpc (em tempo real, por swap event):
- `pool_base` e `pool_quote` — reservas do pool → preço instantâneo
- `sol_amount` — SOL transacionado no swap
- `side` — buy ou sell
- `wallet` — endereço do trader (quando disponível)
- `pool_quote_sol` — profundidade do pool em SOL

### Do kline GMGN (1 minuto de atraso):
- OHLCV de cada candle de 1 minuto
- Disponível apenas ~60 primeiros minutos após migração

### SmartDegenBuy signal type 12 (GMGN):
- `trigger_at` — timestamp do sinal
- `trigger_mc` — MC quando detectado
- `first_trigger_mc` — MC do primeiro sinal histórico
- `signal_times` — quantas vezes o sinal disparou
- `smart_degen_wallets` — lista de wallets

---

## FILTROS ATIVOS (ordem de aplicação no _check_bounce)

| Filtro | Condição | Motivação |
|--------|----------|-----------|
| A.5 | elapsed desde migração ≤ 20 min | 100% TPs ocorrem em ≤20min |
| A.6 | último mc_min ≤ 3 min atrás | bloqueia sinais com fundo "velho" |
| A.7 | bounce ≥ 30% do fundo | morto < 30% = sem demanda real |
| A.8 | MC atual < mc_migration | só compra abaixo do ponto de migração |
| C3 | pool_depth ≥ 10 SOL | pool rasa = ruído de AMM, não price action |
| FILTER_SMART_MIN | smart_count ≥ 2 | smart=1 → 0% WR histórico |
| FILTER_WASH_BLOCK | is_wash_trading = false | |
| FILTER_PUMP_ONLY | exchange = pump | garante cobertura Helius |
| FILTER_MC_MIN/MAX | $5k – $200k | |
| FILTER_LIQ_MIN | liquidez ≥ $10k | |
| FILTER_FEE_MIN | fee total ≥ 1 SOL | proxy de volume mínimo real |
| BOUNCE_MAX_PCT | bounce ≤ 100% | bloqueia entradas tardias (bot chegou atrasado) |

---

## HIPÓTESES PENDENTES (dados coletados, ainda não validados)

**H22**: buy_volume > sell_volume nos 60s antes do fundo discrimina TP vs SL?
- Dado: `absorption_buy_vol`, `absorption_sell_vol` em `bounce_signals`
- Status: coletando, n < 30 com outcomes

**H23**: net SOL flow (`buy_vol - sell_vol`) na janela do fundo prediz recovery?
- Dado: `net_sol_flow` em `bounce_signals`
- Status: inversão surpreendente — >5 SOL de compra = 45.5% WR via token outcome, mas 0% WR em trades reais (bot entra tarde, token já subiu)

**H24**: seller concentration (HHI) durante o dump prediz se o dump vai acabar?
- Dado: `dump_seller_hhi`, `dump_seller_count` em `bounce_signals`
- Status: n < 30 por bucket

**H25**: pool depth no momento do sinal afeta o outcome?
- Dado: `pool_quote_at_signal` em `bounce_signals`
- Status: pool > 90 SOL mostrou WR alto via token outcome, mas 0% em trades reais (mesmo problema de latência do H23)

**H26**: buyer concentration (`largest_buy / total_buy`) durante o bounce discrimina TP vs SL?
- Dado: `buy_concentration` em `bounce_signals`
- Status: n < 30

---

## PROBLEMAS CONHECIDOS / LIMITAÇÕES

1. **Latência de entrada de 1-5 segundos** entre o bounce detector disparar e o fill do GMGN. Em tokens rápidos, o preço pode subir 10-30% nesse intervalo.

2. **Fundo às vezes detectado 1-2 minutos tarde**: o fundo acontece num evento on-chain único; o erpc captura em ~5ms, mas o mc_min confirmado pode chegar com lag de 1-2 candles de kline (60-120s).

3. **SL de 25% dispara cedo demais** em pools pequenas: matemática de AMM — em pool de 10-15 SOL, uma venda de ~$300 move o preço 25%, disparando o SL antes de qualquer price action real.

4. **Bot compra pós-absorção**: quando há absorção forte (>5 SOL de compras no fundo), o token já subiu quando o sinal dispara. O H23 confirmou isso: ótimo sinal para o token, terrível para nossa entrada.

5. **Sem re-entry**: se o bot entra, leva SL, e o token depois vai a +300%, não há mecanismo de re-entry.

6. **Sem sizing dinâmico**: todos os trades são $1. Não há multiplicador para setups de maior qualidade.

7. **Execução via GMGN**: friction de ~9.2% total ($0.92 por trade de $10). Jupiter/Helius direto reduziria para ~1.1%.

---

## DADOS DISPONÍVEIS MAS NÃO USADOS PARA DECISÃO

1. **Lista de wallets individuais** nos SmartDegenBuy signals: quem comprou, quando, quanto. Nunca correlacionado com outcomes.

2. **Sell exhaustion detector** (experimental): ratio de sells nos últimos 5s vs 5-15s antes do fundo. Quando sellers param (ratio < 0.25), bounce foi real em teste preliminar (n=1 token: GROK). Implementado, mas research-only.

3. **`micro_absorption_snapshots`**: série temporal de microdados a cada 2s durante 60s após o fundo. Janelas PRE/RISE/LIVE com buy/sell/net/events/concentrações. Nunca usada para decisão.

4. **`dump_seller_hhi`**: índice de concentração dos vendedores durante o dump. Se dump foi feito por 1-2 wallets grandes, quando eles terminarem o dump acabou. Se foi distribuído, pode continuar.

5. **`creator_token_status`**: dev ainda segura (+9pp WR histórico, mas dado corrompido na época).

6. **`first_trigger_mc` do signal type 12**: o primeiro MC em que smart wallets compraram. Se muito abaixo do atual, já realizaram lucro.

7. **`complete_cost_time`**: tempo criação → migração. Tokens que migraram em <10 min tendem a ter comunidades mais orgânicas.

---

## RESULTADO ECONÔMICO ATUAL

- WR real confirmado GMGN: ~29% (dados limpos pós-2026-05-26, n=31)
- Break-even WR a $1/trade com GMGN: ~28%
- Break-even WR a $10/trade com GMGN: ~28%
- Break-even WR a $10/trade com Jupiter: ~18%
- Payoff ratio atual: ~2.49x (avg win $0.70 / avg loss $0.28)
- Trailing TP é crítico: sem ele o EV é negativo

---

## PERGUNTAS ESPECÍFICAS PARA VOCÊ RESPONDER

### A) Edges de Microestrutura
1. Dado que tenho events on-chain em ~5ms via erpc, como eu deveria estar usando a **sequência de eventos** (não apenas o volume agregado) para detectar o fundo?
2. O que a **posição relativa dos swaps no bloco** diz sobre o tipo de buyer? Leader vs follower?
3. Como detectar **"absorption real"** vs **"bounce de market maker"** que reverte?

### B) Edges de Timing
4. Dado que fundo mediano = 13.6 minutos após migração, e eu monitoro desde a migração, que **padrões temporais** eu deveria identificar antes do fundo?
5. O H23 mostra que quando há >5 SOL de compras no fundo, o token vai bem mas minha entrada é tarde. Qual seria a **estratégia de entrada antecipada** matematicamente correta?
6. Como calcular o **"momento ótimo de entrada"** não como trigger reativo (sinal de bounce), mas como trigger preditivo?

### C) Edges de Informação
7. Tenho a lista de wallets das SmartDegenBuy signals. Como **classificar essas wallets** em tempo real para saber se são alpha ou ruído?
8. O que os **on-chain program logs** do pump.fun emitem que eu não estou parsando? (além de BuyEvent/SellEvent)
9. Como usar o **`complete_cost_time`** (tempo de vida na bonding curve) como sinal preditivo?

### D) Edges de Execução
10. Dado que minha latência de entrada é 1-5s e o mercado move 10-30% nesse intervalo, qual é a **estratégia de sizing/price limit** correta para essa fricção?
11. **Limit order vs market order**: vale a pena colocar uma limit order no preço atual e esperar ser executado, vs market order imediata?
12. Como deveria ser o **sizing dinâmico** baseado na qualidade do setup (H22-H26 metrics)?

### E) Arquitetura de Decisão
13. Se você construísse esse bot do zero hoje, qual seria o **pipeline de decisão completo** nos ~5 segundos entre "evento on-chain detectado" e "ordem enviada"?
14. Que **features de ordem de livro** (orderbook) eu poderia inferir apenas com os eventos pAMM sem acesso ao orderbook completo?
15. Dado que 90% dos meus SLs continuam caindo muito abaixo do fundo após minha entrada (mediana 0.55× do fundo), o que isso diz sobre o **critério de seleção** que preciso mudar?

### F) O que os melhores bots de memecoins fazem diferente
16. Os bots de snipe profissionais no pump.fun operam em qual timeframe de decisão? Como eles gerenciam o risco intra-posição?
17. Existe uma estratégia de **multi-token hedging** que faz sentido nesse contexto (20+ trades simultâneos em diferentes tokens)?
18. Como os bots que são lucrativos no pump.fun pAMM **definem seus SL/TP** — por preço fixo, por volatilidade realizada, por pool depth, ou por flow?

---

## DADOS TÉCNICOS ADICIONAIS

### Matemática do pAMM
- Pool inicializa com ~79 SOL + 200M tokens (k ≈ 6.265×10²⁵)
- k decai lentamente por fees (1.25% por swap) → após 100 swaps, k ≈ 1.77×10²⁵
- Preço = pool_quote_SOL / pool_base_tokens
- Em pool de 10 SOL: um sell de ~$300 move o preço 25% (física de AMM)
- Em pool de 80 SOL: o mesmo sell move ~3%

### Perfis de token que eu vejo
1. **Velocity dump** (<3min pós-migração): early buyers vendem tudo imediatamente. Sem bounce real. Bot detecta mal.
2. **Gradual accumulation** (5-15min): dump progressivo com compradores absorvendo. Bounce após esgotamento de sellers. Melhor perfil histórico.
3. **Whiplash** (1-2min): spike rápido, dump imediato para abaixo da migração, bounce. Fundo às vezes antes da janela de 90s que o bot ignora.
4. **Dead cat** (qualquer tempo): bounce de 20-40% sem continuação. 90% dos SLs.

### Distribuição de outcomes histórica (dados limpos, n=31)
- TP hits: 29% (9/31)
- SL hits: 71% (22/31)
- Mediana de tempo até TP: ~6 minutos após sinal
- 90% dos SLs: preço continua caindo muito abaixo do fundo

---

Seja direto. Me dê edges concretos, não princípios genéricos. Se você identificar algo que eu deveria implementar esta semana para melhorar o WR, me diga exatamente o que, como e por quê.
