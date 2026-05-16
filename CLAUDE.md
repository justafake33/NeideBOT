# NeideBOT — Contexto do Projeto

## O que é
Bot autônomo de trading em Solana.
Estratégia central: detectar tokens recém-migrados (Pump.fun/Meteora DAMM v2), aguardar o dump pós-migração e comprar o fundo com filtros de qualidade.

**Timeframe de trade: minutos.** Checkpoints de 4h e 24h foram descartados — a janela relevante é a hora seguinte à migração.

Completamente independente do wallet-monitor/copy trade.

---

## Stack
- Python + Flask + PostgreSQL (Railway)
- GMGN OpenAPI — discovery, token info, execução
- Telegram Bot — alertas e controle
- Sentry — monitoramento de erros em produção
- Deploy: **Railway** (branch `main`)

---

## 🔌 GMGN API — Referência Completa

### Configuração
Base URL: `https://openapi.gmgn.ai`
Auth: header `X-APIKEY` + query params `timestamp` e `client_id`
Swap/orders: adiciona header `X-Signature` (Ed25519 sobre subpath+params+body+timestamp)

### Endpoints confirmados
| Endpoint | Método | Uso |
|----------|--------|-----|
| `/v1/trenches` | POST | Watchlist (near_completion → data.pump) e migrados (completed → data.completed) |
| `/v1/token/info/{chain}/{mint}` | GET | Dados completos do token |
| `/v1/token/security/{chain}/{mint}` | GET | Segurança: honeypot, rug_ratio, tax, holder concentration |
| `/v1/market/rank` | GET | Trending tokens |
| `/v1/market/token_kline` | GET | Candlestick OHLCV — **NÃO funciona historicamente para Meteora DAMM v2** |
| `/v1/market/token_signal` | POST | Sinais em tempo real — type 12 = SmartDegenBuy (**IMPLEMENTADO Fase 2**) |
| `/v1/trade/swap` | POST | Execução de swap (**ATIVO Fase 6**) |
| `/v1/trade/strategy/create` | POST | TP/SL automático (**ATIVO Fase 6**) |
| `/v1/trade/query_order` | GET | Status de ordem (**ATIVO Fase 6**) |

### Rate limits (leaky bucket, capacity 10)
| Endpoint | Peso | Taxa sustentada |
|----------|------|-----------------|
| trenches | 3 | ~3.3 req/s |
| token info/security | 1 | ~10 req/s |
| kline | 2 | ~5 req/s |
| token_signal | 3 | ~3.3 req/s |
| swap/strategy | 1 | ~10 req/s |

> ⚠️ IPv4 only — GMGN API não aceita IPv6. Verificar no Railway.
> ⚠️ 429 → ler `reset_at` do body e aguardar. Retry imediato estende ban em 5s.

### Mapeamento de campos — trenches

```python
# POST /v1/trenches — body v2 (formato atual)
{
  "version": "v2",
  "near_completion": {
    "filters": ["offchain", "onchain"],
    "launchpad_platform": ["Pump.fun", "letsbonk", "bonkers", ...],
    "quote_address_type": [4, 5, 3, 1, 13, 0],
    "launchpad_platform_v2": True,
    "limit": 80,
  },
  "completed": { ... mesmo formato ... }
}
# chain é query param, não body

# Response keys:
# near_completion → data["pump"]      (NÃO data["near_completion"]!)
# completed       → data["completed"]
# new_creation    → data["new_creation"]

# Campos base por token:
token["address"]                     # mint
token["symbol"]                      # nome
token["smart_degen_count"]           # smart wallets (contagem bruta)
token["renowned_count"]              # KOLs
token["sniper_count"]                # sniper wallets
token["rug_ratio"]                   # risco rug snapshot (0-1)
token["bundler_trader_amount_rate"]  # % bundlers → usar como rug_ratio no DB
token["rat_trader_amount_rate"]      # % de rat traders (filtro)
token["is_wash_trading"]             # bool — wash trading detectado pelo GMGN
token["top_10_holder_rate"]          # concentração top 10
token["bc_progress"]                 # % da bonding curve (0-99)
token["usd_market_cap"]              # MC em USD (preferir sobre market_cap)
token["market_cap"]                  # fallback de MC
token["liquidity"]                   # liquidez total (NÃO dividir por 2 no trenches)
token["total_fee"]                   # fee total em SOL (filtro de volume real)
token["complete_timestamp"]          # unix timestamp da migração
token["created_timestamp"]           # unix timestamp de criação do token
token["complete_cost_time"]          # segundos do token criado até migrar
token["total_supply"]                # supply total
token["exchange"]                    # exchange de destino (Raydium, Meteora, etc.)

# Campos Fase 1 — qualidade (chegam no mesmo trenches, zero custo extra):
token["creator_token_status"]        # "creator_hold" ou "creator_close" — dev ainda segura?
token["creator_balance_rate"]        # % do supply que o dev ainda tem
token["dev_team_hold_rate"]          # % do time total
token["suspected_insider_hold_rate"] # % de insiders identificados pelo GMGN
token["top70_sniper_hold_rate"]      # os 70 maiores snipers ainda estão dentro?
token["bluechip_owner_percentage"]   # ⚠️ NÃO está no trenches — apenas em /v1/market/rank (trending). Não coletar por token.
token["bot_degen_rate"]              # ratio de bots
token["entrapment_ratio"]            # manipulação de preço por armadilha
# lock_percent: NÃO está no trenches. Fonte: token/info["locked_ratio"] na graduação (60min).
token["burn_status"]                 # status do burn de liquidez ("none", "burn")
token["cto_flag"]                    # 1 = comunidade assumiu o projeto
token["has_at_least_one_social"]     # tem pelo menos uma rede social
token["x_user_follower"]             # seguidores no Twitter

# ⚠️ volume_1m/5m/15m NÃO chegam via trenches completed — apenas em near_completion.
# Fonte para token_ticks: kline 1m (campo "volume" por candle), back-fillado no hot-precise.

# GET /v1/token/info/sol/{mint} → body["data"]
# ⚠️ Retorna 404 para tokens Meteora DAMM v2 nas primeiras horas após migração
raw = body["data"]
mc  = float(raw["price"]) * float(raw["circulating_supply"])
liq = float(raw["liquidity"]) / 2   # double-sided → ÷2

# GET /v1/market/token_kline
# ⚠️ Meteora DAMM v2: retorna dados APENAS para os primeiros ~60min após migração.
# Resolução deve ser "1m" (string), NÃO o inteiro 1.
params = {
  "chain": "sol",
  "address": mint,
  "resolution": "1m",
  "from": from_ts,   # unix timestamp
  "to": to_ts,       # unix timestamp
}
# Response: lista de candles com campos: time (ms), open, high, low, close, volume
# volume = volume negociado naquele minuto → usado como volume_1m no token_ticks

# POST /v1/market/token_signal — **IMPLEMENTADO** (Fase 2)
# Feed GLOBAL — retorna todos os tokens com smart buys nas últimas ~8.3h (window: 30000s)
# NÃO é por token — filtramos no lado do bot por tokens ativos no HotMonitor
# signal_type=12 (SmartDegenBuy): smart money comprando em tempo real
# NÃO usar signal_type 14, 15 ou 16 — API retorna 400
#
# Body correto (groups é array de OBJETOS, não strings):
body = {"chain": "sol", "groups": [{"signal_type": [12]}]}
#
# Campos por entry na resposta:
entry["token_address"]      # mint
entry["trigger_at"]         # unix timestamp do sinal
entry["trigger_mc"]         # MC quando o smart buy foi detectado
entry["first_trigger_mc"]   # MC do primeiro sinal histórico do token
entry["signal_times"]       # contagem total de sinais acumulados
entry["smart_degen_wallets"] # lista de wallets: [{address, buy_amount, buy_timestamp}]
entry["market_cap"]         # MC atual
entry["window"]             # 30000 (segundos de lookback)
#
# Fonte: github.com/GMGNAI/gmgn-skills — src/client/OpenApiClient.ts
# Interface TokenSignalGroup: {signal_type?, mc_min?, mc_max?, trigger_mc_min?, trigger_mc_max?, ...}
```

---

## 🎯 ESTRATÉGIA CENTRAL — Post-Migration Dip

### O Padrão Real (validado com 1015+ tokens)
Quando um token completa a bonding curve no Pump.fun, ele migra para Raydium ou Meteora DAMM v2.
Os early buyers realizam lucro → dump imediato.

**O que os dados confirmaram:**
- Dump mediano: **72%** (muito mais severo que o roadmap original assumia)
- **78% dos tokens não fazem bounce** — o pico vem antes do fundo
- Fundo mediano: **13.6min** após migração
- Snapshots estáticos (smart count, rug, top10, wash, holders) **não discriminam recovery**
- **H10 CONFIRMADA**: tokens com fundo em < 25min têm 61% recovery vs 39% para > 25min (+22pp)
- O sinal de compra ativa em tempo real (signal type 12) é a hipótese central ainda pendente (H9)

### Regra de time-stop (H10 validada + refinada)
> Se um token ainda estiver caindo após **20 minutos** do sinal de bounce sem hit de TP → descartar.
> 100% dos TPs ocorrem em ≤20min após o sinal (análise de 83 bounces reais). Zero TPs entre 20-40min.
> Mediana TP: 6.1min, média 7.3min. 82% dos TPs em ≤10min.
> BOUNCE_TIME_MAX = 20min (migração → sinal). Time-stop é na detecção do sinal, não no trade.

### Pipeline de execução
```
ESTÁGIO 1 — Watchlist (bc_progress ≥ 80%)
    │
    ├─ POST /v1/trenches type=near_completion → data["pump"]
    ├─ Filtros aplicados (rug, rat, top10, smart, wash)
    ├─ Campos Fase 1 coletados do trenches (creator_status, cto_flag, etc.)
    └─ Token entra na watchlist com dados de qualidade

ESTÁGIO 2 — Migração detectada
    │
    ├─ POST /v1/trenches type=completed → data["completed"]
    ├─ Filtros de MC ($5k-$200k), liquidez (≥$10k), fee (≥1 SOL)
    ├─ mc_migration = bonding_curve_ref (MIGRATION_SOL_UNITS × SOL/USD) ou kline open ou trenches
    └─ Token entra no HotMonitor por 60 minutos

HotMonitor (janela 60min)
    ├─ push_mc (5s): rastreia mc_min, mc_max, mc_last + _check_bounce()
    ├─ hot-precise (30s): kline low/high + back-fill volume_1m nos ticks (B.4: 60→30s)
    └─ _tick (30s): graduação após 60min → salva mc_bottom, mc_peak, mc_1h

FASE 2 — coleta ativa (IMPLEMENTADO 2026-05-03)
    ├─ _poll_signals() a cada 5s: POST /v1/market/token_signal type 12
    ├─ Feed global → filtra por mints ativos no HotMonitor
    ├─ Persiste em token_signals (mint, trigger_at UNIQUE, trigger_mc, wallets_json)
    ├─ Objetivo: H9 — correlacionar trigger_mc < mc_migration com recovery=TRUE
    └─ Próximo: validar H9 com ≥100 tokens com sinais

BOUNCE DETECTOR + FASE 6 — execução real (ATIVO 2026-05-04)
    ├─ _check_bounce() chamado em cada push_mc
    ├─ Condições base: drop ≥ 30% + bounce ≥ 15% do fundo + elapsed ≤ 20min (A.5)
    ├─ Fase A — filtros ativos (ordem de aplicação):
    │       ├─ A.2: rejeita bounce_pct 20-30% (dead zone, WR 3.1%)
    │       ├─ A.3: rejeita entry_ratio 50-65% da migração (WR 0%)
    │       └─ B.3: primeira detecção elegível → backfill retroativo kline em background (sem bloquear)
    │       ~~A.1 REMOVIDO: slope pré-fundo — H13 refutada com n=153 (recovery rate flat em todas faixas)~~
    ├─ One-shot por token (bounce_fired = True após primeiro disparo)
    ├─ Persiste em bounce_signals (UNIQUE mint) + alerta Telegram
    ├─ Chama TradeExecutor.on_bounce() → thread _execute()
    └─ _execute(): A.4 (aguarda 90s + 3 ticks pós-sinal acima entry) → swap() → _wait_fill() → create_strategy()
```

---

## 🔍 ESTADO ATUAL

### Situação (2026-05-12) — BOT ATIVO $1/TRADE

**Descobertas desta sessão:**
- **pump.fun pAMM** (`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`) identificado como DEX real dos tokens migrados. `BuyEvent`/`SellEvent` com discriminador Anchor (`sha256("event:<name>")[:8]`). Pool reserves em offsets 48/56 dão preço exato.
- **Helius blind window resolvido**: `_RATE_LIMIT_SEC=2.0` bloqueava TODOS os eventos por 2s após o primeiro. V-bottoms rápidos aconteciam inteiramente nessa janela. Fix: Tier 1 (parse Anchor ~5ms) inline no `_on_message`, sem rate limit. Tier 2 (DexScreener HTTP) mantém rate limit.
- **W-bottom hipótese REFUTADA** (H21): 90% dos SLs têm `min_after / mc_bottom < 0.90` — o token vai MUITO abaixo do fundo detectado após a entrada. Não é W-bottom, é continuação de dump. TPs têm `min/bottom > 1.0` (nunca voltam ao fundo). SL ancorado no fundo resolveria apenas 10% dos SLs.
- **DB WR é inválido como métrica**: TradeMonitor usa token_ticks (30s) para inferir TP/SL. Problema: após GMGN fechar posição via condition_orders, ticks posteriores ainda detectam falsos SLs/TPs. WR do DB inflado (~34%) vs GMGN real (~15%). **GMGN é a única fonte de verdade.**
- **wr-breakdown bug corrigido**: query buscava `status='tp_hit'` mas DB salva `status='tp'`. 27 TPs reais eram ignorados, mostrando apenas 9.
- **TradeMonitor trailing exit**: adicionado `_check_trailing_exit()` — simula drawdown 30% do pico via token_ticks para detectar quando GMGN executa o trailing. Funciona para tokens com ticks disponíveis (dentro dos 60min do HotMonitor).
- **Filtros baseados em DB inválido foram adicionados E revertidos**: bounce>50%, TTB<15min, CTO block — todos implementados e revertidos na mesma sessão. Lição: nunca implementar filtro sem validação com GMGN como ground truth.

### Situação (2026-05-09) — HISTÓRICO
- **1175+ tokens** coletados no banco, **1134** com `recovery` definido
- Recovery rate baseline: **23.5%**
- **251 bounce signals** analisados: 119 válidos (47.4%) / 132 inválidos (52.6%)
- **Fase A**: A.2 ✅, A.3 ✅, A.5 ✅. A.1 slope REMOVIDO (H13 refutada). **A.4 DESATIVADO** (90s wait → entrada 53% acima do fundo, matava R:R). **A.6 NOVO** (bottom freshness ≤5min).
- **Fase B ATIVA**: ts_min bug fix, kline backfill, hot-precise 30s
- **Fase 2 ativa**: token_signals type 12 a cada 5s
- **Fase 6 calibração CONCLUÍDA**: 50 trades $1 — WR real 15.1% on-chain (8 TPs / 53 trades), perda real $14.63 (ex-transferência manual $10.85)
- **Trailing TP IMPLEMENTADO**: `profit_stop_trace` ativação=75%, drawdown=30%. SL fixo -15%.
- **H9 pendente**: threshold = trigger_mc < mc_migration × 0.80. Aguardando n≥30 na faixa <50%.
- **Bot relançado (2026-05-10)**: CALIBRATION_MAX_TRADES=999999, TRADING_ENABLED=true, $1/trade

### Resultado da Calibração — 50 trades de $1 (2026-05-07/08) — Auditoria on-chain final

| Métrica | Valor | Observação |
|---------|-------|-----------|
| Trades on-chain reais | 53 | (excl. 1 mystery = transferência manual + 4 tiny legacy) |
| TPs confirmados | 8 | WR = 15.1% |
| SLs | 44 | Retorno médio 64.6% do investido |
| WR real | 15.1% | on-chain auditado — acima do break-even de 11% (sem fees) |
| Net trading | -$14.63 | excluindo transferência manual de $10.85 |
| Jito tips | $0.00 | GMGN NÃO usa Jito |
| Base fees | $1.14 | ~$0.01/tx |
| ATA rent "perdido" | ~$8.64 | 44 × ~$0.20 por SL — recuperável via "Reclaim Rent" no GMGN |

**Diagnóstico real da perda (2026-05-10, auditoria on-chain 115 txs):**
- Jito tips = $0 — hipótese original REFUTADA
- SL executa correto em -15% — GMGN condition_orders funciona em segundos
- O "extra -13%" por SL era o **ATA rent** (~0.002 SOL/trade) não devolvido ao vender
- GMGN tem botão "Reclaim Rent" na UI da carteira → recupera ~$8.64 dos 44 SLs
- **Perda real de trading: -$5.99** (após reclaim do rent)
- **Conclusão: a estratégia é viável a $1/trade. O friction dominante é o ATA rent, não fees.**

**Análise de sinais inválidos (2026-05-09):**
- 52% dos sinais disparavam depois do pico — hipótese de latência REFUTADA (P50 atraso = 6.7min)
- Padrão real: tokens que picam em <3min pós-migração (velocity 7.5× mais rápido que válidos), dumpeiam 10-20min, sinal dispara em mini-rally tardio com fundo estagnado
- Helius resolve apenas 3.8% dos inválidos — não é latência, é estrutural
- **A.6 (bottom freshness)** resolve 92% dos inválidos: sinal só dispara se ts_min ≤ 5min atrás

**Próximo passo para religar:** escalar para $10/trade (break-even ~23%, viável).

### Campos coletados por migração

| Campo | Fonte | Descrição |
|-------|-------|-----------|
| mint | trenches | Endereço do token |
| token_nome | trenches | Símbolo |
| ts_detected | Sistema | Timestamp watchlist (bc ≥ 80%) |
| bc_progress_at_detection | trenches | % BC quando detectado |
| ts_migration | Sistema | Timestamp da migração |
| mc_migration | bonding_curve/kline/trenches | MC oficial (ver fontes abaixo) |
| mc_migration_observed | kline ou trenches | MC observado no momento da captura |
| mc_migration_bc_ref | SOL/USD × MIGRATION_SOL_UNITS | Referência teórica da bonding curve |
| mc_migration_source | "bonding_curve"/"kline"/"trenches" | Qual fonte foi usada |
| mc_peak | HotMonitor | MC máximo na janela quente |
| time_to_peak | HotMonitor | Minutos até o pico |
| pct_gain | Calculado | (mc_peak - mc_migration) / mc_migration |
| mc_bottom | HotMonitor | MC mínimo na janela quente |
| time_to_bottom | HotMonitor | Minutos até o fundo ← **discrimina recovery (H10)** |
| pct_drop | Calculado | (mc_migration - mc_bottom) / mc_migration |
| mc_1h | Checkpoint | MC 1h após migração |
| var_1h | Calculado | % vs mc_migration |
| smart_wallets | trenches | smart_degen_count |
| renowned_wallets | trenches | KOL wallets |
| sniper_wallets | trenches | Sniper wallets |
| rug_ratio | trenches | bundler_trader_amount_rate |
| top10_holder_rate | trenches | Concentração top 10 holders |
| holders_count | trenches | Total de holders |
| volume_1m/5m/15m | trenches (near_completion) / kline (completed) | Volume por janela |
| is_wash_trading | trenches | Campo nativo GMGN |
| liq_migration | trenches | Liquidez no momento da migração |
| is_honeypot | trenches | Flag de segurança |
| recovery | Calculado | mc_1h > mc_bottom × 1.3 |
| token_created_at | trenches | Timestamp de criação |
| time_to_migrate_sec | trenches | Segundos criação → migração |
| capture_delay_sec | Sistema | Segundos entre migração e captura |
| **creator_token_status** | trenches | "creator_hold" / "creator_close" — dev ainda segura? |
| **creator_balance_rate** | trenches | % do supply que o dev tem |
| **dev_team_hold_rate** | trenches | % do time total |
| **suspected_insider_rate** | trenches | % de insiders identificados |
| **top70_sniper_hold_rate** | trenches | Snipers ainda dentro? |
| **bluechip_owner_pct** | ~~trenches~~ N/A | ⚠️ Apenas em /v1/market/rank. Coluna fica NULL. |
| **bot_degen_rate** | trenches | Ratio de bots |
| **entrapment_ratio** | trenches | Manipulação de preço |
| **lock_percent** | token/info (graduação 1h) | locked_ratio do /v1/token/info |
| **burn_status** | trenches | Status do burn |
| **cto_flag** | trenches | Comunidade assumiu o projeto |
| **has_social** | trenches | Tem rede social |
| **x_follower_count** | trenches | Seguidores no Twitter |
| exchange | trenches | Raydium, Meteora, etc. |
| rat_trader_rate | trenches | % rat traders |
| total_fee_sol | trenches | Fee total em SOL |
| bc_progress_migration | trenches | % BC no momento da migração |
| complete_timestamp | trenches | Unix timestamp da migração (GMGN) |

---

## 📐 HIPÓTESES FORMAIS
Registradas ANTES de ver os dados. Não alterar retroativamente.

| # | Hipótese | Resultado |
|---|----------|-----------|
| H1 | ≥70% dos tokens caem ≥20% do ATH nos primeiros 60min | ✅ **91.4%** |
| H2 | Em tokens com recovery=TRUE, o fundo ocorre em ≤15min | ❌ **49.7%** (meta: ≥70%) |
| H3 | smart_wallets ≥3 têm recovery rate ≥15pp maior que smart<3 | ❌ **+0.2pp** — sem discriminação |
| H4 | sniper_wallets ≥30% têm recovery rate <30% | ❌ sniper alto levemente positivo (+0.7pp) |
| H5 | top10_holder_rate ≥0.80 → var_24h <-80% em ≥60% | ⚠️ descartado — 24h fora do escopo |
| H6 | Entradas entre -25% e -45% têm melhor R:R em var_4h/24h | ⚠️ descartado — 4h/24h fora do escopo |
| H7 | liq_migration <$5k gera slippage inaceitável para >$50 | ⏳ pendente validação em execução |
| H8 | is_wash_trading=TRUE tem recovery rate ≥20pp menor | ❌ **-0.1pp** — sem discriminação |
| H9 | Tokens com ≥1 signal type 12 com trigger_mc < mc_migration têm recovery rate ≥20pp maior | ⏳ **pendente** — aguardando acumulação de token_signals (~100 tokens) |
| H10 | time_to_bottom < 25min → recovery rate ≥20pp maior que time_to_bottom ≥ 25min | ✅ **+21.8pp** confirmado (61.2% vs 39.3%, n=425) |
| H11 | Deceleração de preço (d²p/dt²→0) precede o fundo em recovery tokens | ❌ **REFUTADA** — direção invertida. Recovery tokens ACELERAM no fundo (capitulação brusca), não desaceleram |
| H12 | dump_depth + time_elapsed definem zona probabilística de entrada (P(recovery) > 50%) | ⚠️ **PARCIAL** — time domina sobre depth. 3 zonas acionáveis (N≥15, rate≥60%) mas todas já cobertas por H10. Insight novo: zona 5-10min tem recovery 51% (dead zone). |
| H13 | Slope íngreme pré-fundo (≤ -0.0008 %/s) → WR de bounce ≥40pp maior que slope gradual | ❌ **REFUTADA** com n=153 (metodologia correta). Avg slope recovery TRUE=-0.003829 vs FALSE=-0.003696 (diff=0.000133, essencialmente zero). Recovery rate flat em todas as faixas (25-44%, ruído). H13 original n=17 era seleção enviesada (20.5% cobertura, ts_signal errado). A.1 removido do pipeline. |
| H14 | bounce_pct entre 20-30% é dead zone — WR inferior a 15-20% e 30%+ | ✅ **confirmado** (3.1% WR vs 18.5% e 21.1%, n=32). Dead-cat zone reforçada com n maior. |
| H15 | Entry entre 50-65% do mc_migration tem WR=0% | ✅ **confirmado** (0/21 = 0% WR, n=21 — amostra maior). Zona morta absoluta. |
| H16 | 3 ticks consecutivos acima do entry pós-sinal → WR ≥20pp maior | ✅ **+18.0pp** confirmado (28.6% vs 10.6%, n=14/66). Efeito menor que n=71 (+24.3pp) mas consistente. |
| H17 | 100% dos TPs ocorrem em ≤20min após o sinal de bounce | ✅ **confirmado** (11/11 TPs em ≤20min, 82% em ≤10min, n=83). Mediana 6.1min, média 7.3min. |
| H18 | Velocidade de subida do fundo ao sinal (bounce velocity) discrimina WR | ❌ **REFUTADA** — todas as tercis têm WR 40% flat. TTB<25min+Slow=43% vs TTB<25min+Fast=29% (contrário do esperado). Não implementar. |
| H19 | Aguardar 1-2min após sinal melhora EV vs entrada imediata | ✅ **+6.6pp WR** com 1min delay (18.8% vs 12.2%). **Coberto pelo A.4** (90s persistence wait). Sem implementação adicional necessária. |
| H20 | W-bottom (double-dip) é sinal de confirmação de suporte | ❌ **NÃO É FILTRO** — 98% dos bounces têm W-bottom, incluindo SLs. Ubíquo demais. Sem W-bottom (n=2) tem 100% WR mas amostra insuficiente. |
| H21 | SL ancorado no mc_bottom (× 0.90) resolveria maioria dos SLs | ❌ **REFUTADA** (2026-05-12, n=68) — 90% dos SLs caem muito abaixo do fundo (mediana 0.55×). TPs nunca voltam ao fundo (min/bottom > 1.0). Dynamic SL descartado. |
| H22 | Buy volume > sell volume nos 60s pós-fundo discrimina WR ≥15pp | ⏳ **DATA COLLECTION** — janela rolante ativa em HeliusWS, métricas brutas salvas em bounce_signals. Score aguarda n≥30 bounces com absorption_events > 0 e outcomes GMGN. |

### Análises de tick-features — resultados documentados

| Feature | avg recovery | avg no_rec | Conclusão |
|---------|-------------|-----------|-----------|
| delta_smart (smart_count início→fundo) | 1.07 | 2.27 | ❌ invertido — não discrimina |
| delta_renowned (KOLs início→fundo) | 0.44 | 0.65 | ❌ invertido — não discrimina |
| delta_combined (smart+KOL) | 1.51 | 2.92 | ❌ invertido — não discrimina |
| delta_holders | 15.46 | 15.31 | ❌ flat — não discrimina |
| vol_spike (volume no fundo) | null | null | ⏳ dados indisponíveis — fix aplicado |
| vol5m_slope | null | null | ⏳ dados indisponíveis — fix aplicado |
| time_to_bottom | 19.5 min | 40.2 min | ✅ **maior discriminador encontrado** |
| slope_c (2min antes do fundo) | **-0.171** %MC/s | -0.107 %MC/s | ⚡ **invertido útil** — recovery = capitulação mais brusca |

### Análise de bounce quality — resultados (2026-05-05, n=83 bounces reais)

WR base: **13.3%** (EV -3%, TP75/SL15). WR estimado pós-filtros A.2+A.3: **~29%**.

| Feature | Resultado | Status | Nota |
|---------|-----------|--------|------|
| slope pré-fundo ≤ -0.0008 | Recovery rate flat: 34-44% em todas faixas | ❌ A.1 REMOVIDO | n=153, ts_min correto. Original n=17 era seleção enviesada. Slope é característica universal dos bounces, não discrimina. |
| bounce_pct 15-20% | 18.5% WR (n=27) | ✅ faixa válida | — |
| bounce_pct 20-30% | 3.1% WR (n=32) | ✅ A.2 bloqueia | Dead zone — 39% de todos os bounces |
| bounce_pct 30-50% | 21.1% WR (n=19) | ✅ faixa válida | — |
| bounce_pct >50% | 20.0% WR (n=5) | ✅ faixa válida | n pequeno |
| entry 50-65% de mc_migration | 0% WR (n=21) | ✅ A.3 bloqueia | Zona morta absoluta |
| entry 40-50% de mc_migration | 22.2% WR (n=9) | ✅ zona válida | Bom sinal com dump profundo |
| entry >65% de mc_migration | 18.4% WR (n=49) | ✅ zona normal | Maioria dos bounces |
| 3 ticks persistentes acima entry | 28.6% vs 10.6% WR (+18pp) | ✅ A.4 implementado | n=14/66. Efeito robusto |
| 1min entry delay | 18.8% vs 12.2% WR (+6.6pp) | ✅ coberto por A.4 (90s) | Sem implementação adicional |
| bounce velocity (velocidade subida) | 40% WR flat (todas as tercis) | ❌ não discrimina | TTB+Slow=43% > TTB+Fast=29% — não implementar |
| creator_close (dev vendeu) | 15.4% vs 6.2% WR (+9.1pp) | ⏳ monitorar | n=65/16 — efeito modesto, aguardar mais dados |
| W-bottom (double-dip) | 98% prevalência — não discrimina | ❌ não é filtro | Ubíquo em TPs e SLs. A.4 trata implicitamente |
| tempo para TP hit | mediana 6.1min, 100% ≤20min | ✅ time-stop = 20min | 82% em ≤10min. Zero TPs entre 20-40min |

> **Por que 70 SLs em <2min:** bounce dispara sinal, preço sobe 15-20%, imediatamente despenca. Não é bounce — é micro-recuperação dentro de downtrend que não terminou. W-bottom nesses casos: o "W" cruza abaixo do SL floor antes de recuperar. A.4 (persistência 90s + 3 ticks) é o único filtro ativo para isso.

> **WR pós-filtros estimado:** remover A.2 (32 trades, WR 3.1%) + A.3 (21 trades, WR 0%, ~8 overlap com A.2) → ~38 trades restantes com 11 TPs = **~29% WR estimado**. Confirmar após 30+ bounces com Fase A ativa.

> **Por que delta_smart é invertido:** `smart_count` do trenches/ticks = snapshot de posições acumuladas (inclui quem comprou há dias e segura). NÃO é compra ativa. Tokens com dump longo (que não bounceam) acumulam mais smart wallets simplesmente porque o dump dura mais. O sinal correto de compra ativa é o signal type 12.

> **slope_c invertido mas útil:** recovery tokens têm dump 60% mais rápido nos 2 minutos antes do fundo (−0.171 vs −0.107). Não é deceleração — é capitulação brusca. Padrão: queda moderada → leve pausa → spike de capitulação → fundo. No-recovery: queda lenta e gradual sem spike. Caveat: análise sobre tokens com fundo ≥10min (n=49) — os de fundo rápido (H10, maior recovery) foram excluídos por densidade de ticks insuficiente.

### Backtester tick-level — resultados consolidados (2026-05-04)

**Metodologia:** entry = mc_bottom × 1.15. Caminha token_ticks cronologicamente após o fundo.
- Ticks `source=kline`: usa `mc_high` para TP e `mc_low` para SL (intra-vela). Desempate via `mc_open` vs midpoint.
- Ticks `source=trenches`: usa `mc` (close) para SL e TP.
- Fim da janela sem hit → saída no último tick com P&L real.

**Filtros:** drop ≥ 30%, time_to_bottom < 30min → 560 tokens elegíveis, **68 com ticks** (expansão em curso via kline OHLC).

| TP | SL | Win Rate | EV | Payoff | n_tp | n_sl |
|----|----|----------|----|--------|------|------|
| 15% | 15% | 39.7% | -2.5% | 1.1x | 27 | 38 |
| 30% | 15% | 33.8% | +1.1% | 2.2x | 23 | 40 |
| 40% | 15% | 32.4% | +3.7% | 2.9x | 22 | 41 |
| **75%** | **15%** | **27.9%** | **+11.5%** | **5.7x** | 19 | 43 |
| **100%** | **15%** | **23.5%** | **+14.7%** | **8.7x** | 16 | 44 |

**SL=15% domina todos os níveis de TP.** Signal estável em 3 runs consecutivos.

**Bias de cobertura:** 492/560 tokens sem ticks. Diagnóstico: recovery_rate dos sem-ticks = 57.7% (similar ao baseline 52%) — não são piores, apenas sem cobertura histórica. Kline OHLC resolvendo progressivamente.

**⚠️ O que o backtest NÃO captura:** taxas de swap, priority fee, slippage, MEV/sandwich, latência. EV_real = EV_backtest − friction_total. Friction só mensurável em live trading ($1 trades).

---

## 🏗️ ARQUITETURA ATUAL

```
HeliusWS (WebSocket — background thread, sempre ativo)
    ├─ logsSubscribe por mint (subscribe/unsubscribe via HotMonitor)
    ├─ Tier 1 inline (~5ms): parse Anchor BuyEvent/SellEvent do pAMM
    │       └─ Preço via pool_reserves (offsets 48/56) → _sol_usd_cached
    │       └─ Acumula {ts, side, sol, mc} em deque(maxlen=300) por mint [H22]
    └─ Tier 2 (DexScreener HTTP, rate-limited): fallback de preço

Scanner (poll 5s)
    │
    ├─ POST /v1/trenches near_completion → data["pump"]
    │       └─ bc ≥ 80% + filtros qualidade → Watchlist + DB
    │       └─ Campos Fase 1 coletados do trenches (creator_status, cto_flag, etc.)
    │
    ├─ POST /v1/trenches completed → data["completed"]
    │       └─ Filtros MC/liq/fee → upsert_migration
    │       └─ mc_migration = bonding_curve_ref (MIGRATION_SOL_UNITS × SOL/USD) || kline open || trenches
    │       └─ HotMonitor.add() → HeliusWS.subscribe(mint)
    │       └─ push_mc para TODOS os completed já vistos (atualiza _mc_live)
    │
    ├─ _poll_signals() — Fase 2 (a cada 5s, apenas se HotMonitor tem tokens ativos)
    │       └─ POST /v1/market/token_signal → feed global type 12 (SmartDegenBuy)
    │       └─ Filtra por tokens ativos no HotMonitor._tokens
    │       └─ Persiste matches em token_signals (ON CONFLICT DO NOTHING)
    │       └─ Ban guard: _signal_ban_until — não chama enquanto banido
    │
    └─ HotMonitor (janela 60min)
            ├─ push_mc (5s): rastreia mc_min, mc_max, mc_last, _mc_live
            │       └─ _check_bounce(): dispara bounce_signal se drop≥30% e bounce≥30%
            │               ├─ Coleta get_absorption_window(mint, 60) → H22 métricas brutas
            │               ├─ Salva em bounce_signals (sem score — coleta only)
            │               └─ TradeExecutor.on_bounce() → thread _execute() [Fase 6]
            ├─ hot-precise (5s): kline low + close (NÃO high) → mc_min e mc_peak
            │       └─ mc_peak atualizado via close × supply (high descartado — spike intra-candle)
            └─ _tick (30s): graduação após 60min → HeliusWS.unsubscribe(mint)
                    └─ kline scan 1h completo → mc_bottom (low), mc_peak (close), mc_1h
                    └─ token/info sempre chamado: fallback mc_1h + lock_percent (locked_ratio)
                    └─ Fallback final mc_1h: push_mc (mc_last)

TradeExecutor (Fase 6 — ATIVO)
    ├─ on_bounce(): verifica TRADING_ENABLED + count_trades < CALIBRATION_MAX
    ├─ insert_trade(): cria registro com status='pending'
    └─ _execute() [thread própria]:
            ├─ swap(): POST /v1/trade/swap (Ed25519 signed)
            ├─ _wait_fill(): poll query_order até filled/completed (timeout: ORDER_TIMEOUT_SEC)
            └─ condition_orders no body do swap: trailing TP (ativ. 75%, drawdown 30%) + SL fixo -15%

get_sol_usd() [gmgn_client.py]
    ├─ Cache 5min (_sol_usd_value / _sol_usd_ts)
    ├─ Fonte primária: CoinGecko API
    └─ Fallback: GMGN token/info da SOL mint
```

### Tabelas do banco
| Tabela | Conteúdo |
|--------|----------|
| `migrations` | Uma linha por token migrado — todos os campos de qualidade + checkpoints |
| `token_ticks` | Série temporal por token. `source='trenches'` (~30s) ou `source='kline'` (60s, com OHLC). |
| `token_signals` | **Fase 2** — sinais type 12 por token durante janela quente |
| `bounce_signals` | **Fase 2** — um registro por token quando bounce detector dispara (UNIQUE mint). Tem 8 colunas H22: `absorption_buy_vol`, `sell_vol`, `buy_cnt`, `sell_cnt`, `lrg_buy`, `events`, `score=NULL`, `grade=NULL`. |
| `trades` | **Fase 6** — log completo de cada trade live. Coluna `fill_source TEXT` rastreia origem do fill data (`gmgn`/`helius`/`helius_recovery`/`gmgn_recovery`). |
| `trade_absorption_ticks` | Snapshots contínuos de absorção Helius (+30s/+60s pós-bounce) por trade em monitoramento. |

### HeapScheduler
- Thread única com heap de eventos futuros
- Nunca usar `threading.Timer` por evento (não escala)
- `scheduler.schedule(delay_sec, fn, *args)`

### Fontes de dados por prioridade
| Dado | 1ª fonte | 2ª fonte | 3ª fonte |
|------|----------|----------|----------|
| mc_migration | bonding_curve_ref (MIGRATION_SOL_UNITS × SOL/USD) | kline (open no complete_ts) | trenches usd_market_cap |
| mc_bottom | kline 1m low (hot-precise) | push_mc (trenches 5s) | — |
| mc_peak | push_mc (trenches 5s) | kline 1m close (hot-precise + graduate) | — |
| mc_1h | kline (ts_mig a ts_mig+3660) | token/info | push_mc (mc_last) |
| volume_1m (token_ticks) | kline candle volume (hot-precise back-fill) | — | — |
| lock_percent | token/info locked_ratio (graduação 1h) | — | — |
| bluechip_owner_pct | ⚠️ N/A — apenas em /v1/market/rank | — | — |
| SOL/USD | CoinGecko API (cache 5min) | GMGN token/info SOL mint | — |

---

## 🔧 Variáveis de Ambiente (Railway)

```
DATABASE_URL=postgres://...
GMGN_API_KEY=xxxx
GMGN_PRIVATE_KEY=xxxx        # Ed25519 PEM — necessário para Fase 6
GMGN_WALLET_ADDRESS=xxxx     # endereço Solana da carteira — necessário para Fase 6
GMGN_HOST=https://openapi.gmgn.ai
TELEGRAM_TOKEN=xxxx
TELEGRAM_CHAT=xxxx
SENTRY_DSN=xxxx              # DSN padrão (não o obsoleto)

# Filtros do Scanner (configuráveis sem redeploy)
FILTER_RUG_MAX=0.15          # máx token["rug_ratio"] (score interno GMGN, 0-1)
FILTER_BUNDLER_MAX=0.20      # máx bundler_trader_amount_rate (% bundlers real)
FILTER_BOT_MAX=0.80          # máx bot_degen_rate (default código=0.80, não discrimina WR em live trades)
FILTER_RAT_MAX=0.20          # máx rat_trader_amount_rate
FILTER_TOP10_MAX=0.80        # máx top_10_holder_rate
FILTER_SMART_MIN=3           # mín smart_degen_count (smart=1→0% WR, smart=2→20%, smart=3→57% — dado live GMGN)
FILTER_WASH_BLOCK=true       # bloquear is_wash_trading
FILTER_MC_MIN=5000           # MC mínimo USD
FILTER_MC_MAX=200000         # MC máximo USD
FILTER_LIQ_MIN=10000         # liquidez mínima USD
FILTER_FEE_MIN=1.0           # fee mínimo em SOL
BC_MIN_WATCHLIST=80          # % BC mínimo para watchlist
MAX_MIG_AGE_SEC=600          # ignorar migrações > 10min no restart
POLL_INTERVAL_SEC=5          # intervalo do scanner
HOT_PRECISE_SEC=5            # alinhado com trenches poll — mc_min sempre fresco (era 30s)
MIGRATION_SOL_UNITS=410      # SOL teórico da bonding curve → mc_migration_bc_ref

# Bounce detector (configuráveis sem redeploy)
BOUNCE_MIN_PCT=0.30          # bounce mínimo do fundo para disparar sinal — A.7 (era 0.15; 15-20% → 9.1% WR live, n=11)
BOUNCE_DROP_MIN=0.30         # drop mínimo da migração para considerar (30%)
BOUNCE_TIME_MAX=20.0         # A.5: janela máxima em minutos (100% TPs em ≤20min)
BOTTOM_FRESHNESS_SEC=300     # A.6: máx 5min desde o último mc_min (bloqueia sinais tardios)

# Fase 6 — Execução de trades (configuráveis sem redeploy)
TRADING_ENABLED=true         # true para ativar trades reais
CALIBRATION_AMOUNT_USD=1.0   # tamanho de cada trade em USD
CALIBRATION_MAX_TRADES=999999 # sem limite prático — executor para apenas manualmente
TRADE_SL_PCT=0.25            # stop loss (testando 25% — 15% disparava em segundos por volatilidade do pAMM)
TRAILING_ACTIVATION_PCT=75   # trailing: gain% mínimo para ativar (75% = início conservador)
TRAILING_DRAWDOWN_PCT=30     # trailing: recuo máximo do pico antes de vender (30%)
TRADE_SLIPPAGE_BPS=500       # slippage máximo em basis points (5%)
ORDER_POLL_SEC=2             # intervalo de poll do fill (segundos)
ORDER_TIMEOUT_SEC=60         # timeout do fill (segundos)
# PERSISTENCE_WAIT_SEC e PERSISTENCE_MIN_TICKS REMOVIDOS — A.4 desativado (hardcoded = 0)

# Helius RPC (leitura on-chain direta — preenchimento de fill + detecção de posição fechada)
HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?api-key=xxxx  # HTTP JSON-RPC (diferente do WSS para pAMM events)
# GMGN_WALLET_ADDRESS já configurado acima — reutilizado por helius_rpc.py como WALLET_ADDRESS
```

---

## 📊 Dashboards

| URL | Descrição |
|-----|-----------|
| `/scanner` | Tabela de migrações em tempo real (auto-refresh 15s) |
| `/analise` | Backtest exploratório — timing, dump depth, filtros, R:R, cobertura Fase 1 |
| `/analise/h10` | **Teste formal H10** — time_to_bottom vs recovery rate (n=425) |
| `/analise/bottom-score` | Backtest retroativo Bottom Score (5 métricas preço/volume) — resultado negativo |
| `/analise/smart-flow` | delta_smart / delta_renowned vs recovery — resultado negativo |
| `/analise/tick-features` | Análise completa de todas as colunas token_ticks vs recovery |
| `/analise/price-decel` | Slope de preço em 3 janelas antes do fundo — H11 REFUTADA, capitulação brusca emerge |
| `/analise/dump-depth` | H12 — grid 2D pct_drop × time_to_bottom → P(recovery). 3 zonas acionáveis mas time domina. |
| `/analise/backtest` | **Backtester tick-level** — grid TP×SL com OHLC. `?mode=ticks` (padrão) ou `?mode=full`. |
| `/export/csv` | Export completo do banco |
| `/health` | Health check |
| `/analise/trailing` | **Trailing TP** — distribuição de gains válidos, intra-rally drawdown, simulação 7 configs vs TP fixo |
| `/analise/invalidos` | **Sinais tardios** — diagnóstico dos 52% de bounces inválidos, impacto Helius, filtro A.6 |
| `/analise/a6-trades` | **Retroativo A.6** — dos 50 trades reais, quantos A.6 teria bloqueado e se eram bons/ruins |
| `/analise/h9` | H9 — token_signals type 12 vs recovery rate por faixa de trigger_mc/mc_migration |
| `/analise/wr-breakdown` | **Discriminadores de WR** — cross-referencia trades com migrations/bounce_signals/H9. Suporta `?since=YYYY-MM-DD` |
| `/analise/dip-entry-sim` | **Simulação dip entry** — e se esperássemos queda X% do sinal antes de entrar? Suporta `?dip=0.05` até `?dip=0.25` |
| `/debug/signal` | Diagnóstico histórico — testa formatos do token_signal (legacy) |
| `/debug/signal-live` | **Fase 2** — chama get_token_signal(), mostra matches com HotMonitor e últimos sinais salvos |
| `/debug/executor-status` | **Diagnóstico do pipeline de trading** — TRADING_ENABLED, limite trades, HotMonitor ativo, último bounce |
| `/debug/wallet-audit` | **Auditoria on-chain** — todas as txs da wallet, Jito tips, BUY/SELL, net SOL. Usar `?rpc=HELIUS_URL` |
| `/debug/wallet-tokens` | **Forense de posições** — ATAs vazios (rent bloqueado), tokens presos, cruzamento com trades do DB |
| `/analise/wbottom` | **H21 W-bottom** — analisa se SLs tiveram retest acima de `mc_bottom × floor%`. `?floor=0.90`. REFUTADA: 90% dos SLs caem muito abaixo do fundo. |
| `/debug/bounce-list` | **Lista de bounces** — tokens com bounce ≥ X% nas últimas 48h com links DexScreener/GMGN. `?bounce=0.50&hours=48`. Auto-refresh 30s. |
| `/debug/absorption` | **H22 Absorption Debug** — janela buy/sell ao vivo por token ativo + últimos 30 bounces com ratio buy/sell e cobertura Helius. Auto-refresh 10s. Score/grade ausentes — coleta bruta apenas. |
| `/analise/post-sl-recovery` | **Post-SL Recovery** — para cada SL, max MC nos ticks pós-exit. 12% atingiriam TP (8/64). 88% continuaram dumpando. Confirma H21 estruturalmente. |
| `/analise/h22-correlation` | **H22 Correlation** — correlação entre absorção pós-fundo (Helius) e outcome TP/SL. Tabelas por bucket: absorption_events (0/1-2/3-5/6-10/≥11), buy/sell ratio (∞/≥3×/1.5-3×/etc.), concentração (largest_buy/buy_vol). Instrumento pronto antes dos dados chegarem — n=? cresce com o bot ativo. Gate: n≥30 com Helius. |
| `/debug/open-trades` | **Diagnóstico de trades abertos** — por trade `open`/`trailing_active`: entry_expected, entry_mc_real, sl_mc, tp_mc, contagem de ticks, min_low, max_high, flags sl_hit/tp_hit, resposta ao vivo do GMGN `query_order` com raw JSON. Identifica trades presos + por que TradeMonitor não detectou saída. |

---

## ⚠️ Notas Críticas de Implementação

1. **Calibração de $1/trade concluída** — friction real auditada on-chain. ATA rent (~$0.20/trade) é o principal custo, não Jito. Reclaim Rent no GMGN recupera os valores. WR 15.1% acima do break-even de 11%.
2. **Threading**: heap scheduler — nunca `threading.Timer` por evento
3. **GMGN rate limit**: trenches peso 3, kline peso 2, token info peso 1, signal peso 3. 429 → ler `reset_at` do body
4. **data["pump"]**: near_completion retorna nessa chave, não "near_completion"
5. **bc_progress ≥ 80**: entrada na watchlist | **complete_timestamp**: sentinel de migrado
6. **Timestamps em UTC** no banco — converter para BRT apenas na exibição
7. **Kline Meteora DAMM v2**: NÃO retorna dados históricos. Apenas primeiros ~60min após migração
8. **token/info**: retorna 404 para Meteora DAMM v2 nas primeiras horas. Não confiar como fonte primária
9. **Token morto**: mc cair ≥80% do mc_migration → `cancel()` no HotMonitor
10. **IPv4 only**: GMGN API não aceita IPv6
11. **is_wash_trading**: campo nativo do trenches, não calcular manualmente
12. **logging.basicConfig** deve ser chamado ANTES de `init_sentry()` para logs aparecerem
13. **_mc_live**: captura MC de TODOS os completed já vistos (sem filtro de range)
14. **Checkpoints 4h/24h removidos**: o código não agenda mais. Colunas mc_4h/mc_24h existem no banco (dados históricos) mas não são mais populadas.
15. **signal type 12**: NÃO usar signal_type 14, 15 ou 16 — API retorna 400
16. **Campos Fase 1**: chegam no poll do trenches sem custo adicional de API — nunca recalcular manualmente
17. **bluechip_owner_pct**: NÃO está no trenches — apenas em `/v1/market/rank` (trending). Coluna fica NULL, não remover do schema.
18. **lock_percent**: NÃO está no trenches — coletado como `locked_ratio` do token/info na graduação (60min após migração)
19. **token_signal é feed global**: NÃO é por token — retorna TODOS com smart buys no lookback de 30000s. Filtrar por HotMonitor._tokens no lado do bot.
20. **groups é array de objetos**: body correto = `{"chain":"sol","groups":[{"signal_type":[12]}]}`. Array de strings causa erro "invalid json body".
21. **_signal_ban_until**: ban guard no Scanner — cada chamada enquanto banido estende o ban. Checar `time.time() < _signal_ban_until` ANTES de chamar.
22. **_poll_signals skip se vazio**: não chama API se `HotMonitor._tokens` estiver vazio — economiza rate limit.
23. **token_signals tabela**: `UNIQUE (mint, trigger_at)` — `ON CONFLICT DO NOTHING` evita duplicatas no poll a cada 5s.
24. **scanner global em app.py**: `scanner` e `gmgn` são globals em app.py para acesso nas rotas de debug.
25. **volume_1m NÃO vem do trenches completed**: trenches retorna volume apenas para near_completion (bonding curve ativa). Para tokens migrados, `volume_1m` em token_ticks é back-fillado via kline 1m no `_precise_bottom_tick()` (a cada 60s). Dados históricos anteriores ao fix ficam NULL.
26. **delta_smart é snapshot, não compra ativa**: smart_count no trenches/ticks acumula wallets com qualquer posição. NÃO detecta compras novas. Sinal de compra ativa = signal type 12 (token_signals).
27. **time_to_bottom é o único preditor validado até agora**: tokens com fundo < 25-30min têm 61% recovery rate vs 39% para fundo > 25-30min (H10 confirmada, n=425, +22pp).
28. **token_ticks têm intervalo médio ~30s, não 5s**: ticks só são inseridos quando o token aparece no trenches poll com token_data. Se o token sair do feed antes do poll, o tick não é criado. Janelas de análise devem ter ≥150s de largura para capturar ≥5 pontos.
29. **Capitulação brusca ≠ deceleração**: recovery tokens têm slope_c = −0.171 %MC/s vs −0.107 em no-recovery. O padrão é queda moderada → leve pausa → spike de capitulação → fundo. Análise sobre subconjunto com fundo ≥10min (n=49) — bias de seleção contra os tokens de fundo rápido (maior recovery).
30. **Backtester usa token_ticks, NÃO mc_1h**: mc_1h é checkpoint arbitrário. O backtester correto caminha os ticks reais após o fundo e detecta qual bate primeiro — SL ou TP. `n_sl_hit` + `n_time_exit` + `n_tp_hit` somam `n_trades`. Tokens sem ticks são excluídos (`n_no_ticks`).
31. **Bias de cobertura nos ticks**: apenas ~12% dos tokens elegíveis têm ticks. Diagnóstico: recovery_rate dos sem-ticks = 57.7% (similar ao baseline). Kline OHLC resolvendo — `_precise_bottom_tick()` insere velas a cada 60s independente do feed trenches.
32. **bounce_signals tabela**: `UNIQUE (mint)` — só registra o primeiro bounce detectado por token. `ON CONFLICT DO NOTHING` em `insert_bounce_signal()`.
33. **SL=10% muito apertado**: entry = mc_bottom × 1.15, SL=10% → aciona em mc_bottom × 1.035 (3.5% acima do fundo). Qualquer ruído de mercado dispara o SL. SL=15% é o mínimo prático — exige retorno abaixo do fundo para acionar.
34. **token_ticks tem source='kline' e source='trenches'**: kline ticks têm mc_open/mc_high/mc_low (OHLC completo). Trenches ticks têm só mc (close). Backtester usa mc_high para TP e mc_low para SL nos ticks kline. Desempate quando ambos cruzados na mesma vela: mc_open vs midpoint(sl_floor, tp_target).
35. **mode=full do backtester é metodologicamente quebrado**: avg_loss=+29% (positivo) revela que mc_1h ignora SL hits mid-path. Token pode subir a mc_1h mas ter acionado SL antes. Usar apenas mode=ticks para decisões.
36. **Win rate ≥50% é critério errado para esta estratégia**: com payoff 8.7x (TP=100%, SL=15%), o break-even é win rate de 11%. Win rate de 23% gera EV=+14.7%. Exigir 50% rejeitaria uma estratégia matematicamente lucrativa. Critério correto: EV_real > friction_total.
37. **$1 trades são o walk-forward real**: backtest não captura slippage, MEV, priority fee, latência. 50 trades de $1 medem friction_total real e calibram o modelo. EV_real = EV_backtest − friction_total. Só após calibração escalar o size.
38. **Kline ticks cobrem tokens que saem do feed trenches**: após dump, token sai do trenches completed → push_mc para de chamar → sem ticks trenches. `_precise_bottom_tick()` chama kline independentemente — garante cobertura durante toda a janela quente de 60min.
39. **mc_migration usa bonding_curve_ref como 1ª fonte**: `MIGRATION_SOL_UNITS` (padrão 410) × preço SOL/USD = MC teórico da bonding curve completa. Salvo em `mc_migration_bc_ref`. `mc_migration_observed` = kline open ou trenches. `mc_migration_source` = "bonding_curve" | "kline" | "trenches".
40. **get_trenches() usa formato v2**: body inclui `version: "v2"`, `launchpad_platform` (lista extensa de plataformas), `quote_address_type`, `launchpad_platform_v2: True`, `limit: 80`. `chain` é query param.
41. **get_sol_usd() tem cache de 5min**: fonte primária CoinGecko, fallback GMGN token/info da SOL mint. Usado pelo executor para converter USD → SOL no momento do trade.
42. **TradeExecutor é thread-safe**: `on_bounce()` usa `_lock` para checar limite antes de despachar. Cada trade roda em thread própria (`trade-{id}`). Falha no fill não impede o próximo bounce.
43. **Priority fee NÃO é enviado no swap**: GMGN gerencia internamente. Enviar com unidade errada pode causar fees absurdos (ex: 100000 microlamports/CU × 300k CU = $6+ por trade).
44. **_checkpoint só aceita label="1h"**: após remoção dos checkpoints 4h/24h, o dict de labels foi simplificado. Chamar com outro label retorna sem fazer nada.
45. **rug_ratio vs bundler_trader_amount_rate são campos diferentes**: `token["rug_ratio"]` é o score de risco interno da GMGN (escala 0-1, tende a ser baixo). `token["bundler_trader_amount_rate"]` é a % real de bundlers (o campo relevante para filtrar). O filtro `FILTER_BUNDLER_MAX` checa `bundler_trader_amount_rate`; `FILTER_RUG_MAX` checa `rug_ratio`. Ambos devem estar no filtro.
46. **bot_degen_rate é um forte sinal de lixo**: tokens com >50% de bot_degen_rate tendem a ser rugs ou distribuição pura. Filtrado por `FILTER_BOT_MAX` (padrão 50%).
47. **ts_min rastreado no state do HotMonitor**: `state["ts_min"]` é atualizado em `push_mc()` (trenches) e em `_precise_bottom_tick()` (kline) sempre que um novo mc_min é detectado. Usado pelo slope filter (A.1) para queryar ticks pré-fundo. Bug original: kline path não atualizava ts_min → slope filter sempre retornava 0 ticks (janela errada). Corrigido em Fase B.
48. **slope_backfill_triggered**: flag no state do HotMonitor. Na primeira detecção de bounce elegível (A.2/A.3 passam), dispara `_backfill_slope_ticks()` em thread própria para cobertura de ticks históricos. Não bloqueia o sinal (A.1 removido — H13 refutada com n=153).
49. **Bounce velocity NÃO discrimina**: testado com n=83. Todas as tercis de velocidade (lento/médio/rápido) têm WR 40% flat. TTB<25min+Slow tem WR MAIOR que TTB<25min+Fast (43% vs 29%). Não implementar filtro de velocity.
50. **W-bottom é ubíquo (98%)**: 82/83 bounces têm double-dip. NÃO é filtro. A.4 (persistência 90s) trata implicitamente: se o W-bottom recupera antes de 90s, os ticks pós-sinal voltam acima do entry → persistência passa.
51. **WR estimado pós-filtros A.2+A.3**: remover 32 trades (dead zone 20-30%) + 21 trades (entry 50-65%, ~8 overlap) → ~38 trades restantes com 11 TPs = **~29% WR estimado**. Confirmar com 30+ bounces após Fase A ativa.
52. **Base58 deve ser verificado antes de Base64 em `_load_ed25519_key`**: o alfabeto Base58 é um subconjunto do Base64 — strings Base58 de 88 chars (Phantom export, 64 bytes) são decodificáveis por Base64, gerando ~66 bytes e seed errado. Fix: Base58 primeiro, com validação `len(decoded) in (32, 64)`. Base64 só aceita comprimentos exatos de 32 ou 64 bytes. Causa raiz do AUTH_SIGNATURE_INVALID original.
53. **TP/SL fixo é piso, não teto**: `TRADE_TP_PCT=0.75` e `TRADE_SL_PCT=0.15` são os parâmetros da calibração inicial (50 × $1). A Fase F (Exit Engine Maleável) substitui isso por saídas parciais em camadas (30% em +30%, 30% em +60%, 40% trailing) + risk engine em tempo real. Não construir saída dinâmica antes de medir friction real nos 50 trades de calibração.
54. **IP Railway muda a cada redeploy**: após cada redeploy, o IP de saída muda e a whitelist GMGN quebra → AUTH_SIGNATURE_INVALID retorna. Solução permanente: Railway Static Outbound IP (~$3/mês). Necessário antes de escalar para evitar interrupções de trading.
55. **GMGN_PRIVATE_KEY é chave de assinatura GMGN, NÃO a chave da wallet Solana**: a GMGN emite uma Ed25519 PEM key separada por conta, usada exclusivamente para assinar requests de trade. Usar a private key da wallet Solana (Base58 do Phantom) como GMGN_PRIVATE_KEY gera address errado e AUTH_SIGNATURE_INVALID. A PEM key fica no painel da conta GMGN e NÃO deriva o mesmo endereço que GMGN_WALLET_ADDRESS — isso é esperado, não é erro.
56. **create_strategy requer DUAS chamadas separadas**: uma para `take_profit` e outra para `stop_loss`. Campos obrigatórios: `order_type="limit_order"`, `sub_order_type="take_profit"/"stop_loss"`, `check_price=str(price_in_sol)`, `amount_in=str(int(tokens))`, `auto_slippage=True`, `priority_fee` e `tip_fee` (padrão "0.0001" SOL cada). Sem priority_fee/tip_fee a ordem não é aceita na rede Solana. Resultado retornado: `{"tp": {"order_id": ...}, "sl": {"order_id": ...}}`. Salvo como `strategy_id = "tp:{tp_id}|sl:{sl_id}"`.
57. **query_order: status em `confirmation.state`, não `data.status`**: o fill é detectado quando `confirmation.state` é `"processed"` ou `"confirmed"`. O campo `data.get("status")` retorna `"filled"` apenas em alguns casos mas não é confiável como trigger principal. Amounts corretos: `filled_output_amount` (tokens recebidos) e `filled_input_amount` (SOL enviado) — NÃO `out_amount`/`in_amount`.
58. **Global rate limit gate `_global_ban_until`** em `gmgn_client.py`: variável module-level compartilhada por todas as threads. Qualquer 429 em qualquer endpoint chama `_set_global_ban(reset_at)` → todas as threads bloqueiam em `_wait_global_ban()` até o ban expirar. Sem isso, threads concorrentes do HotMonitor continuam chamando API durante o ban do scanner, estendendo o ban de 73s para 127s+.
59. **`_recover_pending()` removido**: função foi removida do executor — posições antigas fechadas manualmente. `create_strategy()` também não é mais chamado no fluxo normal. TP/SL são criados atomicamente via `condition_orders` no próprio body do swap.
60. **WR live confirmado 25% em primeiros 20 trades** (2026-05-06): 5 TP hits, 15 SL hits = 25% WR. EV bruto = 5×75% − 15×15% = +3.75% por trade. Próximo a estimativa de 29%. 6 dos 15 SLs tiveram 0% de upside pós-entrada (dead-cat bounce que A.4 deveria bloquear). Confirmar com mais dados se A.4 está filtrando corretamente.
61. **H9 dados iniciais** (2026-05-06, n=119 sinais, 97 tokens únicos, 4 dias): Breakdown por faixa de `trigger_mc/mc_migration` revela reverse causality crítica. Faixa `>120% migração` tem delta +32.1pp (56% recovery, n=25) MAS é consequência, não predição — token que atingiu 120% do MC de migração JÁ fez o bounce, logo recovery=TRUE é esperado. Faixas genuinamente preditivas (sinal chega durante o dump): `<50% migração` (+11.8pp, n=14) e `50-80%` (+6.5pp, n=23). Faixa `80-100%` é negativa (-1.7pp). H9 reformulada: threshold correto é `trigger_mc < mc_migration × 0.80` — exclui momentum buyers e reverse causality. Aguardar n≥30 na faixa `<50%` para validação. Dashboard em `/analise/h9`.
62. **`/debug/trade-analysis`**: endpoint para análise post-mortem de trades. Mostra para cada trade: elapsed_sec, bounce_pct, entry_mc, peak_mc, low_mc e se atingiu TP (+75%) ou SL (-15%). Usa token_ticks após ts_signal. Sem ticks = sem dados pós-entrada.
63. **TP/SL via `condition_orders` — ✅ VALIDADO** (2026-05-06): TP e SL são embutidos no body do swap em uma única chamada à API. `price_scale` e `sell_ratio` estão em escala percentual (0-100), não decimal nem multiplicador. Valores corretos: `price_scale="75"` (TP +75%), `price_scale="15"` (SL -15%), `sell_ratio="100"` (100% da posição). Bug anterior: `"1.75"`/`"0.85"` eram lidos como 1.75%/0.85% (thresholds minúsculos que disparavam imediatamente), e `"1"` vendia apenas 1% por disparo — os 98% restantes ficavam expostos sem proteção real. `priority_fee` e `tip_fee` são obrigatórios quando `condition_orders` está presente.
64. **`/debug/test-swap`**: endpoint para teste manual de swap real. `GET /debug/test-swap?mint=<mint>&amount=<usd>` (padrão $1). Com `TRADING_ENABLED=false` exibe simulação do body sem executar. Com `TRADING_ENABLED=true` executa o swap e retorna o `order_id`. Mostra `price_scale` e `sell_ratio` exatos que serão enviados — útil para validar parâmetros antes de religar o bot.
65. **A.4 DESATIVADO** (2026-05-09): `PERSISTENCE_WAIT_SEC = 0` e `PERSISTENCE_MIN_TICKS = 0` hardcoded no executor.py (não lê env var para evitar reativação acidental). Motivo: A.4 esperava 90s após sinal → entrada ~53% acima do fundo (quase no TP). Eliminava o R:R da estratégia.
66. **A.6 — Bottom Freshness** (2026-05-09): sinal só dispara se `(now - state["ts_min"]).seconds <= BOTTOM_FRESHNESS_SEC` (padrão 300s). `ts_min` já rastreado em push_mc + _precise_bottom_tick. Bloqueia sinais de fundo "velho": tokens que picaram em <3min pós-migração e geram mini-rally tardio com fundo estagnado há 10-20min. 92% dos inválidos históricos seriam bloqueados.
67. **Trailing TP via `profit_stop_trace`** (2026-05-09): substitui `profit_stop` (TP fixo) no body do swap. Campos: `price_scale` = activation % (ex: "75" = ativa quando +75%), `drawdown_rate` = recuo máximo do pico (ex: "30" = vende se cair 30% do pico), `sell_ratio = "100"`. Simulação n=119: trailing 30% ativ.100% → avg 159.2% vs 71.3% TP fixo. Ativação conservadora em 75% para fase inicial (pouco caixa). SL fixo -15% mantido via `loss_stop`.
68. **Sinais inválidos são padrão estrutural, não latência** (2026-05-09): 52% dos bounces disparavam depois do pico. Hipótese de latência (Helius resolve) REFUTADA — P50 atraso = 6.7min, apenas 3.8% dentro da janela de 30s do GMGN. Padrão real: tokens com spike relâmpago em <3min (velocity 7.5× maior que válidos), dump de 10-20min, sinal em mini-rally tardio. Helius continua no roadmap mas como **redução de custo** (Jupiter swap), não como melhoria de sinal.
69. **TradeMonitor "trailing_active"**: com trailing TP, o TradeMonitor soft-detector marca o trade como "trailing_active" quando price atinge a activation level (tp_pct armazenado = TRAILING_ACTIVATION_PCT/100). Não é saída real — GMGN condition_orders gerencia o trailing. É apenas visibilidade de que o trailing está ativo.
70. **Inválidos A.6 não afetam os 50 trades reais**: retroativo mostrou que A.6 só teria bloqueado 1/50 trades (QUACK, que era TP). Os 50 trades executados eram todos "frescos" — o tick de menor MC estava a 0.1-4.5min do sinal. Os inválidos da análise geral são de períodos anteriores sem A.5 ativo.
71. **`_precise_bottom_tick()` agora dispara `_check_bounce()`** (2026-05-10): após processar os candles de cada token, chama `_check_bounce()` com o close da vela mais recente. Antes, kline corrigia `mc_min` mas nunca disparava bounce — o sinal só vinha do poll trenches (5s depois), quando o preço já estava 50-100% acima do fundo corrigido. Fix: bounce agora dispara com fundo preciso (kline low) e preço atual (kline close), sem esperar o trenches.
72. **HOT_PRECISE_SEC reduzido de 30s para 5s** (2026-05-10): alinhado com o poll do trenches. A cada 5s tanto trenches quanto kline rodam — mc_min é sempre o mais preciso possível quando `_check_bounce()` dispara. Rate limit: 10 tokens → 5.8 peso/s vs limite de 10. O `time.sleep(0.5)` entre tokens distribui as chamadas sem burst.
73. **Frequência filtrada real vs calibração**: calibração (48 trades/48h) foi sem A.6. Com todos os filtros ativos (A.2+A.3+A.6): frequência estimada de 7-8 sinais válidos/dia (vs 24 brutos). A.6 bloqueia 92% dos inválidos, que eram 52% do total → reduz ~47% dos sinais brutos. A.2 bloqueia mais 39%. WR sobe de 15.1% para estimado 29% em troca da menor frequência.
74. **Meta 1 BTC via volume, não via size**: estratégia correta é manter $50-100/trade e maximizar frequência de sinais válidos. A $100/trade com 8 sinais/dia e Jupiter (EV ~17%/trade): ~$136/dia → ~$50k/ano. Para atingir ~$100k/ano: re-entry (+50-80% frequência) + Jupiter/Helius + mercado ativo (~20 sinais/dia). Não escalar size acima de $100-200 — o edge é no volume de trades qualificados, não no size.
75. **TradeMonitor overwrite bug — CORRIGIDO** (2026-05-11): `get_open_trades()` incluía status `'trailing_active'`, mantendo trades em monitoramento após GMGN executar o trailing stop. Após 4h (`MONITOR_MAX_AGE_H=4`), o TradeMonitor sobrescrevia status para `'expired'` — apagando o registro de TP. Corrigido: `get_open_trades()` agora retorna apenas `status='open'`. `trailing_active` é estado terminal — GMGN gerencia a saída, código não toca mais. `wr-breakdown` trata `expired` com `pnl_pct_real > 0` como TP para dados históricos.
76. **WR real confirmado: 29% em 31 trades pós-fix (2026-05-11)**: 9 TP / 22 SL. Consistente com estimativa histórica de ~29% pós-A.2+A.3. Filtros ativos: A.2, A.3, A.5, A.6. Gate de escala $1→$10 tecnicamente atingido (WR ≥ 20% em ≥30 trades), mas novos filtros A.7 + smart≥2 recém-adicionados — aguardar 20+ trades com novos filtros antes de escalar.
77. **A.7 — Bounce mínimo 30% (2026-05-11)**: `BOUNCE_MIN_PCT` elevado de 0.15 para 0.30. Dados live (n=11 trades com 15-20% bounce): WR 9.1% — pior que qualquer outra faixa. Dead zone estende-se abaixo de 30%, não apenas 20-30% (A.2). Com A.7, A.2 torna-se redundante como safety net. WR esperado pós-filtro: ~40%.
78. **FILTER_SMART_MIN elevado para 2 (2026-05-11)**: smart=1 → 0% WR em 6/6 trades live (GOBBI, 🟥🟩, Goblin, Troll, OARPE, Birdclaw — todos SL). smart=2 → 20% WR. smart=3 → 57% WR. Contradiz análise histórica de tokens (sem discriminação), mas é consistente na execução live. Possível explicação: no token-level, smart_count acumula holders históricos; no trade-level, smart=1 pode indicar token sem demanda suficiente para bounce sustentado.
79. **Estratégia de dip entry REFUTADA (2026-05-11)**: testado aguardar queda de -5%, -10%, -15%, -20%, -25% do sinal antes de entrar. Todos os thresholds pioraram o WR. Razão: tokens com demanda real (SS, OCEAN, BIRDCLAW) nunca retrocederam o suficiente — foram direto ao TP. Quando o token SÍ retrocedeu (goingpt, OG, NOEL), continuou caindo até o SL mesmo com nova entrada mais baixa. Conclusão: o timing de entrada atual está correto. O problema era qualidade dos sinais (filtros), não quando entrar.
80. **H9 em trades live (2026-05-11, n=31 trades)**: sem sinal H9 → 24% WR (n=25). Com H9 qualquer → 50% WR (n=6, +21pp). H9 ratio < 0.80 (preditivo) → **75% WR (n=4)**. H9 ratio > 1.00 (pós-bounce) → 0% WR (n=2, reverse causality confirmada). Sinal mais forte encontrado nos dados live. Gate para adicionar como filtro: n≥15 na zona ratio<0.80. Candidato a multiplicador de size (2× quando H9 ratio<0.80 presente).
81. **FILTER_BOT_MAX default no código é 0.80 (não 0.50)**: código usa `os.getenv("FILTER_BOT_MAX", "0.80")`. O valor 0.50 no CLAUDE.md estava desatualizado. bot_degen_rate não discrimina WR em live trades — o único TP real (BIRDCLAW) tinha 77% de bots. Não alterar este filtro.
82. **pump.fun pAMM é o DEX real dos tokens migrados** (`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`). Emite `BuyEvent`/`SellEvent` com discriminador Anchor `sha256("event:<name>")[:8]`. Layout após disc(8): `timestamp(i64)@8 | base_amount@16 | quote_limit@24 | user_base@32 | user_quote@40 | pool_base@48 | pool_quote@56`. Preço = `(pool_quote/1e9) / (pool_base/1e6)`. Não confundir com Meteora CP AMM v2 (`cpamd...`) que não é usado pelos tokens migrados do pump.fun.
83. **Helius `_RATE_LIMIT_SEC=2.0` criava janela cega de 2s**: bloqueava TODOS os eventos Helius por 2s após o primeiro update. Fix: Tier 1 (parse Anchor, ~5ms) roda inline no `_on_message` sem rate limit. Tier 2 (DexScreener HTTP) mantém rate limit. `_sol_usd_cached` (module-level float) alimenta o Tier 1 sem bloquear o WebSocket callback.
84. **DB WR é inválido como métrica de desempenho**: TradeMonitor infere TP/SL via token_ticks (30s). Após GMGN fechar posição via condition_orders, ticks posteriores detectam falsos SLs/TPs. wr-breakdown mostrava 34% mas GMGN (ground truth) mostra 15%. Nunca usar DB WR para decisões de filtro — usar GMGN.
85. **wr-breakdown buscava `status='tp_hit'`** mas DB salva `status='tp'`. Fix: adicionar `'tp'` e `'sl'` ao TP_STATUSES/SL_STATUSES e ao WHERE clause. Os 27 TPs reais eram ignorados, contando apenas 9.
86. **H21 — W-bottom REFUTADA** (2026-05-12, n=68 SLs com ticks): 90% dos SLs têm `min_after / mc_bottom < 0.90` — preço vai muito abaixo do fundo após entrada (mediana ~0.55×). Não é W-bottom, é continuação de dump. TPs têm `min/bottom > 1.0` (preço nunca volta ao fundo). Ancorar SL no fundo resolveria apenas 10% dos SLs. Implementação de dynamic SL descartada.
87. **Nunca implementar filtro baseado em DB WR**: DB WR é inflado por falsos positivos do TradeMonitor. Filtros de bounce>50%, TTB<15min e CTO foram implementados E revertidos na mesma sessão porque eram baseados em análise corrompida. Gate mínimo para qualquer novo filtro: n≥30 trades confirmados no GMGN.
88. **TradeMonitor `_check_trailing_exit()`**: para trades `trailing_active`, simula trailing stop via token_ticks — rastreia peak MC após ativação e detecta queda de TRAILING_DRAWDOWN_PCT do pico. Atualiza para `status='tp'` com P&L estimado. Limitação: funciona apenas se há ticks após a ativação (dentro dos 60min do HotMonitor).
89. **`get_trailing_active_trades()`**: retorna trades `trailing_active` com `exit_ts > NOW() - 8h`. Chamado em `_check_all()` a cada 30s para detectar saídas de trailing retroativamente.
90. **FILTER_SMART_MIN=3 — próxima mudança de env var (2026-05-12)**: dado live/GMGN mostra smart=3 → 57% WR vs smart=2 → 20% WR. Discriminação +37pp com dados válidos (não DB). Mudança simples, reversível, zero linhas de código. Fazer antes de qualquer outra coisa na próxima sessão.
91. **BOTTOM_FRESHNESS_SEC=180 é baixa alavancagem agora**: os 50 trades reais tinham ts_min a 0.1-4.5min do sinal — todos já "frescos". Os 92% de inválidos bloqueados pelo A.6 histórico eram de períodos SEM A.6 ativo. Com A.6 em produção, baixar de 300s para 180s não muda quase nada no fluxo real. Não implementar até ter dados que mostrem inválidos chegando com gap 3-5min.
92. **Tiers de entrada (A+/A/B/C) são prematuros**: com ~80 trades GMGN no total, dividir em 4 buckets dá n≈20/bucket — insuficiente. Implementar scoring depois de n≥200 trades com dados limpos. Prioridade correta: acumular dados, não criar tiers agora.
93. **Detector de absorção pós-fundo via Helius — próxima grande alavanca de código**: infraestrutura já existe (`helius_ws.py` recebe `BuyEvent`/`SellEvent` do pAMM em ~5ms). Hoje usada apenas para preço. O próximo nível é acumular eventos por mint numa janela rolante de 60-90s e, quando `_check_bounce()` dispara, checar: `buy_volume_60s > sell_volume_60s × 1.3` + `buy_count_60s ≥ 3` + `preço não retornou < mc_min × 0.97`. Isso ataca o problema raiz: 90% dos SLs são dumps que continuam porque não havia comprador real no fundo. A pergunta muda de "subiu 30%?" para "quem comprou esse fundo e com que força?". Estimativa: 1-2 dias de código, começa como log/alerta antes de virar filtro. Implementar APÓS acumular n≥30 trades com smart≥3.
94. **H9 como tier de confluência, não gate obrigatório (2026-05-12)**: H9 live mostra 75% WR (n=4) em ratio<0.80 — sinal mais forte encontrado. Mas n=4 é insuficiente para gate obrigatório. Usar como tier: smart≥3 + fresh + H9<0.80 = setup A+ (candidato a 2× size). smart≥3 + fresh sem H9 = setup A (size normal). Virar gate obrigatório só com n≥30 e WR≥50% confirmado GMGN (gate do roadmap). Kill switches por regime de mercado são válidos como princípio mas complexidade prematura agora — focar em acumular dados primeiro.
95. **H22 — Coleta de absorção implementada (2026-05-12)**: `helius_ws.py` acumula `{ts, side, sol, mc}` por swap em `deque(maxlen=300)` por mint. `get_absorption_window(mint, window_sec=60)` retorna métricas brutas: `buy_vol_sol`, `sell_vol_sol`, `buy_count`, `sell_count`, `largest_buy`, `total_events`. Estas métricas são salvas em `bounce_signals` (8 colunas novas) em cada bounce. `/debug/absorption` exibe ratio buy/sell ao vivo e cobertura histórica. **Score/grade NÃO existem** — removidos deliberadamente. Princípio: coletar métricas brutas primeiro, correlacionar com outcomes GMGN (TP/SL) após n≥30, construir fórmula somente com pesos validados pelos dados. Gate para filtro: n≥30 bounces com absorption_events > 0 + WR ≥15pp maior com absorção.
96. **Score sem correlação = ilusão de precisão (2026-05-12)**: `_absorption_score()` foi removida. A fórmula era plausível (baseada em lógica de mercado) mas não validada. Calcular um score antes de ver os dados é "julgar o peixe por não subir em árvore" — o critério de avaliação é arbitrário. Só há sentido em pontuar DEPOIS de saber qual métrica discrimina TP vs SL nos dados reais. Este é o princípio central do big player: coletar → analisar → modelar, nunca o inverso.
97. **Kline `high` inflava mc_peak com spikes intra-candle (2026-05-12 — fix)**: em pools com ~79 SOL de liquidez, um único whale buy pode momentaneamente empurrar o preço AMM 150-200x antes de reverter. O kline registra esse pico como o `high` da vela (correto tecnicamente). O scanner mostrava ganhos absurdos: DIAMOND +23,034% (Pico $9M mas mc_1h = $39K = +0.0%). Fix: `_precise_bottom_tick()` e `_graduate()` agora usam `close × supply` para `mc_peak`. O `close` é o preço sustentado ao final do minuto. `mc_high` nos `token_ticks` mantido para o backtester (TP detection intra-vela continua correto). `push_mc` (trenches `usd_market_cap`) já usava close implicitamente — unificado agora.
98. **`/analise/post-sl-recovery` — diagnóstico pós-SL (2026-05-12)**: para cada SL com `exit_ts`, busca max MC nos token_ticks após a saída. Resultado: 12% dos SLs (8/64 com dados) teriam atingido o TP pós-saída. 88% continuaram dumpando — confirma H21 (W-bottom refutada): não é má sorte, é estrutural. Winners recuperam rápido (5-10min). A análise mostra que "hold" após SL seria lucrativo apenas em 12% dos casos — não justifica mudar a estratégia de saída. ⚠️ **Número suspeito**: usa `entry_expected` (mc_bottom × 1.15) e não entrada real (`entry_price_usd / tokens_received × 1B`). Pendente correção antes de usar o "12%" como base de decisão.
99. **H22 cobertura Helius: 1.7% (2026-05-13)**: `bounce_signals` tem 529 registros. Apenas **9 têm `absorption_events > 0`** (1.7% de cobertura). 520 têm NULL. Bot ativo = cobertura cresce com cada bounce. Conclusões estatísticas aguardam n≥30, mas `/analise/h22-correlation` deve ser construído agora — instrumento de medição pronto antes dos dados chegarem. Causa da baixa cobertura histórica: a maioria dos 529 bounces é anterior à implementação do Helius rolling window (2026-05-12). Os bounces novos (pós-implementação) já chegam com dados.
100. **`buy_concentration = largest_buy / buy_vol`** (Codex review 2026-05-13): diferencia "um único buyer pumpou o preço" (concentration ≈ 1.0) de "múltiplos compradores absorveram" (concentration << 1.0). `largest_buy` e `buy_vol` já existem em `bounce_signals` e `get_absorption_window()`. Campo derivado, zero schema change. Implementar junto com a correção do `/debug/absorption` (mostrar "∞" para sell_vol=0+buy_vol>0, e "no flow" para ambos=0).
101. **`/debug/executor-status` usava `db.get_conn()` que não existe** (fix 2026-05-13): o módulo db.py usa contextmanager `_cursor()`, não expõe `get_conn`. Também usava `ORDER BY created_at` mas a coluna se chama `ts_signal`. Ambos os erros eram capturados silenciosamente → "Nenhum no DB" mesmo com bounce_signals populada. Fix: psycopg2.connect(db.DATABASE_URL) direto + ORDER BY ts_signal. Mesmo padrão corrigido em `/debug/wallet-audit`.
102. **GMGN `query_order` nunca retorna `filled_output_amount`** (confirmado 2026-05-13): campo retorna consistentemente `null` mesmo quando `confirmation.state="confirmed"`. `filled_input_amount` (SOL) também ausente. `_wait_fill()` no executor detectava `state=confirmed` mas saía sem popular `entry_price_usd` → `tokens_received=None` → `tp_mc=None` → TradeMonitor nunca detectava TP. Solução: leitura on-chain via Helius (`helius_rpc.py`).
103. **`helius_rpc.py` — módulo de leitura on-chain (2026-05-13)**: novo módulo para ler fill data e verificar posições diretamente da blockchain Solana. Três funções principais: `get_swap_fill(tx_sig, token_mint)` via `getTransaction` (jsonParsed) para obter `tokens_received`/`sol_spent`/`fee_sol`; `get_token_balance(token_mint)` via `getTokenAccountsByOwner` para verificar se wallet ainda segura o token; `extract_tx_signature(query_order_response)` para extrair tx sig dos campos do query_order. Requer `HELIUS_RPC_URL` (HTTP JSON-RPC, diferente do WSS do helius_ws.py) e `GMGN_WALLET_ADDRESS`. Degradação graciosa: sem `HELIUS_RPC_URL`, todas as funções retornam `None`/`0`.
104. **`fill_source` coluna na tabela `trades`** (2026-05-13): rastreia qual fonte forneceu os dados do fill: `'gmgn'` (filled_output_amount retornou — raro), `'helius'` (getTransaction on-chain no fill loop principal), `'helius_recovery'` (Helius em `_recover_fill()` pós-timeout), `'gmgn_recovery'` (filled_output_amount em `_recover_fill()`). Schema: `ALTER TABLE trades ADD COLUMN IF NOT EXISTS fill_source TEXT`.
105. **TradeMonitor cego sem ticks (problema estrutural, 2026-05-13)**: `_check_trade()` e `_check_trailing_exit()` retornam imediatamente se `not ticks`. Token_ticks são geradas pelo HotMonitor durante a janela de 60min — quando o HotMonitor expira, os ticks param. Trades que ficam abertos mais de 60min pós-migração ficam invisíveis para o TradeMonitor. Fix: se `not ticks`, chamar `_check_position_closed()` (Helius) + `_check_via_gmgn()` como fallback.
106. **`_check_position_closed()` via Helius (2026-05-13)**: novo método no TradeMonitor. Chama `helius_rpc.get_token_balance(mint)` — se saldo=0, GMGN fechou a posição. Infere TP vs SL via última MC disponível nos ticks vs thresholds: `ratio <= (1 - sl_pct × 0.9)` → `sl`; `ratio >= (1 + tp_pct × 0.9)` → `tp`; entre os dois → `trailing_active` (GMGN gerenciando trailing). Retorna `True` se posição foi detectada como fechada. Chamado na ausência de ticks para trades `open` e `trailing_active`.
107. **`tp_mc` fallback quando `entry_mc_real=None` (2026-05-13)**: quando `filled_output_amount` não retorna e Helius não está disponível, `entry_mc_real` fica `None` → `tp_mc = entry_mc_real × (1+tp_pct)` falha com TypeError. Fix: `tp_mc = entry_mc_real × (1+tp_pct) if entry_mc_real else entry_expected × (1+tp_pct)`. `tp_from_expected` flag indica quando o fallback foi usado. A entrada real é sempre ≥ `entry_expected` por latência/slippage — o threshold do fallback é conservador (TP detectado um pouco mais tarde do que o real).
108. **`condition_orders: nenhum` em `query_order` (2026-05-13)**: GMGN não retorna status das condition_orders no endpoint `query_order` do order de compra. `_check_via_gmgn()` que tentava inferir TP/SL a partir da resposta é ineficaz como solução standalone. A abordagem correta é verificar o saldo on-chain via `get_token_balance()`. Lição: tentar inferir estado de ordens derivadas via a ordem de compra original não funciona — elas têm lifecycle independente.
109. **`/debug/open-trades` endpoint (2026-05-13)**: diagnóstico de trades presos `open`/`trailing_active`. Mostra para cada trade: token, age, entry_expected, entry_mc_real (real fill), sl_mc, tp_mc, tick count após ts_signal, min_low/max_high dos ticks, flags `sl_hit`/`tp_hit` com threshold calculado, raw JSON da resposta ao vivo do GMGN `query_order`. Permite identificar exatamente por que o TradeMonitor não detectou a saída. Essential para debugging sem acesso ao terminal Railway.
110. **`/analise/h22-correlation` CTE fix (2026-05-13)**: CTE original fazia `WHERE status IN ('tp','sl','trailing_active','expired')` → excluía trades com `status='open'` e `status='error'` do JOIN → todos os bounces com trades mostravam `outcome=NULL`. Fix: remover WHERE filter — buscar latest trade por mint independente de status. Dashboard agora mostra outcomes reais onde disponíveis.
111. **H22 cobertura 6% (32/552 bounces, 2026-05-13)**: cobertura cresceu de 1.7% (9/529) no início da sessão para 6% (32/552) ao final. 100% dos tokens com `exchange='pump'` têm cobertura Helius (FILTER_PUMP_ONLY garante isso para novos bounces). Os 520+ sem cobertura são históricos anteriores à implementação do Helius rolling window (2026-05-12). Cobertura cresce linearmente com o bot ativo — todos os novos bounces terão dados.
112. **TRADE_SL_PCT=0.25 em teste (2026-05-13)**: elevado de 0.15 para 0.25 porque SL=15% disparava em segundos após entrada. Causa suspeita: volatilidade intra-candle do pAMM em pools pequenas — uma única ordem de venda pode temporariamente empurrar o preço 15%+ antes de recuperar. Com SL=25%, o threshold é mais tolerante a ruído de mercado sem necessariamente piorar o EV (token que vai -25% dificilmente recupera). Aguardar n≥30 trades para avaliar impacto no WR e EV vs SL=15%.
113. **FILTER_PUMP_ONLY garante cobertura H22 futura**: scanner rejeita migrações onde `exchange` não contém "pump". Consequência: 100% dos tokens futuros são pump.fun pAMM — Helius WebSocket já subscrito para eles, logo `get_absorption_window()` sempre retorna dados. Eliminará a dicotomia entre bounces com/sem dados Helius após ~30 dias de operação.
114. **Problema de dados fictícios — TradeMonitor inferindo saídas (identificado 2026-05-14, sessão 5)**: o TradeMonitor calculava TP/SL usando `entry_expected = mc_bottom × 1.15` (nunca a entrada real) e `token_ticks` (snapshots MC a ~30s). Após o HotMonitor expirar (60min), os ticks paravam → trades ficavam `open`/`trailing_active` e eram marcados `expired` com `pnl_pct_real = +75%` (threshold do trailing, não lucro real). Resultado: SLs reais apareciam no banco como TP(+75%) e TPs reais apareciam como SL(-15%). O banco era ficção matemática, não realidade on-chain. WR DB inflado: ~32%. WR real blockchain-verificado: **10.1%** (14 TP / 125 SL / 139 trades auditados na sessão 5).
115. **`/debug/sell-recovery` — recuperação de saídas reais via blockchain (2026-05-14, sessão 5)**: endpoint que pagina TODAS as transações da wallet desde 2026-05-06 via Helius `getSignaturesForAddress` (cursor `before` para paginação completa, sem limite fixo), detecta sells reais (SOL delta > 0 AND token delta < 0 para o mint do trade), faz match trade ↔ sell por mint + timestamp, e atualiza `status`, `exit_type='helius_sell_recovery'`, `exit_price_usd`, `exit_ts`, `pnl_pct_real`. Resultados do DRY RUN: 573 txs escaneadas, 201 sells encontrados, 139 trades corrigidos, 0 não encontrados (cobertura 100%). `exit_type='helius_sell_recovery'` é o único campo que prova que a saída tem dado blockchain real. Streaming via `stream_with_context` + `PAD=" "*2048` para evitar timeout do Railway (run ~86s).
116. **Resultado da auditoria blockchain (DRY RUN 2026-05-14)**: WR real = **10.1%** (14 TP / 125 SL). P&L médio = **-11.4%**. 41 mudanças de status (DB errado em ~30% dos trades). 121 correções de P&L. Correções notáveis: Kevin #153 DB=SL(-25%) → Real=TP(+335.6%); Goblin #85 DB=SL(-15%) → Real=TP(+100%); HUMANITY #105 DB=SL(-15%) → Real=TP(+63%); SLEEP #174 DB=SL(-25%) → Real=TP(+49.2%); Viltrumite #308 DB=trailing_active → Real=TP(+88.6%); YILONG #232 DB=expired → Real=TP(+54.8%). Sentido oposto: SNDK, soothsayer, GOBOLINI marcados como TP(+75%) no DB eram SL reais (-20% a -30%).
117. **WR real 10.1% está abaixo do break-even de ~11% a $1/trade**: estratégia está perdendo dinheiro. SLs reais variam de -20% a -40%, mais profundos que o SL configurado (15-25%). Causas possíveis: (a) GMGN executa o SL na price action real que é mais rápida que token_ticks de 30s; (b) volatilidade do pAMM em pools pequenas empurra o preço além do threshold antes de recovery; (c) slippage no sell adiciona perda além do threshold configurado. Executar `/debug/sell-recovery` (sem `?dry=1`) para confirmar dados no banco antes de tomar decisões de calibração.
118. **Paginação completa com cursor `before` é obrigatória para auditoria histórica**: abordagem inicial `?limit=150` mostrava contagens diferentes a cada run porque a janela de transações mais recentes variava. Fix: loop `getSignaturesForAddress` com cursor `before` no último sig processado, parando quando `block_time < MAY_6_TS - 3600`. Piso hard-coded `MAY_6_TS = datetime(2026, 5, 6, tzinfo=timezone.utc).timestamp()`. Garante cobertura total desde o primeiro trade.
119. **`db._cursor()` retorna `RealDictCursor` — rows são dicts, não tuplas**: indexar com `r[0]` falha com `KeyError: 0`. Sempre usar nomes de coluna: `r['status']`, `r['mint']`, etc. Especialmente insidioso em code inline onde o erro é silenciado pelo `try/except` do generator. Padrão correto: `{r['coluna'] for r in cur.fetchall()}`, não `{r[0] for r in cur.fetchall()}`.
120. **Streaming com `stream_with_context` + padding para Railway**: endpoints de longa duração (sell-recovery escaneia 573 txs em ~86s) precisam de streaming para evitar timeout. Usar `Response(stream_with_context(generate()), content_type="text/html")`. Cada `yield` deve incluir `PAD = " " * 2048` para forçar flush em proxies. Sem padding, o browser acumula buffer e não mostra nada até o generator completar — parece timeout para o usuário.
121. **Entry correto em simulações = `mc_at_signal`, nunca `mc_min × 1.15`**: usar `mc_min × 1.15` assume compra antes do sinal disparar — inflava o WR base de 24% real para 47.8% fictício. Para bounce de 55%, entry real = `mc_min × 1.55`, não `mc_min × 1.15`. Qualquer endpoint de simulação ou backtest que use `mc_min × 1.15` como entry está matematicamente errado. Usar sempre `bounce_signals.mc_at_signal`.
122. **Benchmark correto de qualidade de sinal = token outcome, não carteira**: comparar carteira vs carteira mistura variáveis independentes (SL 15%→25%, filtros A adicionados, regime de mercado). O dataset correto é `bounce_signals + token_ticks` nos 20min pós-sinal usando `mc_at_signal` como entry. Endpoint: `/analise/sinal-puro`. Resultado com metodologia correta: WR base = **24%** (n=692 bounces, break-even ~25%).
123. **Nenhuma métrica pré-trade entrega mais de +7pp (2026-05-16, n=692)**: com entry correto (`mc_at_signal`) e dataset de token outcome: bounce 55-65% = +7pp; elapsed 0-3min = +7pp; smart >10 = -13pp (único sinal negativo forte); CTO, dev_status, top10, renowned = ±3pp (ruído). O edge não está em filtros estáticos — está na microestrutura do momento do sinal.
124. **Seller exhaustion é a hipótese central para discriminar TP vs SL (H24)**: H21 confirmou que 90% dos SLs continuam caindo muito abaixo do fundo após a entrada (mediana `min_after/mc_bottom` = 0.55×). Causa estrutural: havia sellers que não tinham terminado. Acumular sellers únicos via Helius BuyEvent/SellEvent entre `ts_migration` e `ts_min`, calcular `dump_seller_hhi` (índice Herfindahl de concentração). Se top-3 sellers > 70% do dump_vol → dump concentrado e finito → bounce tem base real. Salvar `dump_seller_count` e `dump_seller_hhi` em `bounce_signals`.
125. **Pool depth em tempo real está em `pool_quote` (offset 56 do BuyEvent)**: `pool_quote < 20 SOL` no momento do bounce significa que um sell de ~$300 move o preço 25%. Com SL=25%, qualquer ruído de pool thin dispara a saída — não é price action real, é física de AMM. Isso explica por que SL=15% "disparava em segundos": o pool era pequeno demais. Salvar `pool_quote_at_signal` em `bounce_signals` usando o último valor recebido via Helius WebSocket no momento do `_check_bounce()`.
126. **Net SOL flow = `buy_vol_sol - sell_vol_sol` (H23)**: dado já existe em `get_absorption_window()`. Mede capital real entrando vs saindo, independente de contagem de trades. Um sell de 10 SOL depois de 20 compras de 0.1 SOL = `net_flow < 0` — pool drenando apesar da maioria de trades ser compra. H22 atual (contagem de eventos) esconde essa assimetria. Gate para filtro: salvar em `bounce_signals` e correlacionar com outcomes GMGN (n≥30).
127. **Buy concentration = `largest_buy / buy_vol` (H26)**: paper arXiv 2602.14860 sobre pump.fun confirma que "fast accumulation por poucos trades" é o preditor mais forte de sucesso. Bounce com 3 buyers de 3-5 SOL é estruturalmente melhor que 30 buyers de 0.1 SOL — o primeiro indica demanda concentrada real, o segundo pode ser bots ou wash. Campo já existe em `bounce_signals` (`absorption_lrg_buy` e `absorption_buy_vol`). Falta calcular a razão e correlacionar com outcomes GMGN (gate: n≥30).
128. **Bug crítico H23: janela de absorção estava no momento errado (sessão 7, 2026-05-16)**: `get_absorption_window()` usava `time.time()` como fim da janela — ou seja, capturava os 60s ANTES do momento do sinal de bounce. Nesse momento o token já subiu 30-50%, então a janela mede compras DO BOUNCE (momentum), não absorção DO DUMP. A janela correta é `[ts_min - 60s → ts_min]` — os 60s que antecedem o FUNDO. Fix: parâmetro `window_end_ts: float = None` adicionado a `get_absorption_window()`. Scanner passa `state["ts_min"].timestamp()` como `window_end_ts`. Nunca usar `time.time()` como referência para janelas de absorção.
129. **`h23_window` — campo de tag nos bounce_signals**: coluna `TEXT` adicionada ao schema. Valor `'bounce'` = janela antiga (captura momentum pós-bounce, dado inválido para H23). Valor `'bottom'` = janela correta (captura absorção do dump). Backfill histórico: `UPDATE bounce_signals SET h23_window = 'bounce' WHERE h23_window IS NULL`. Todo bounce novo gerado pelo scanner corrigido recebe `h23_window='bottom'`. 82% dos 749 registros históricos têm `'bounce'` — preservados para análise paralela de momentum, não para H23.
130. **H23 bounce-window: correlação com bounce_pct = -0.252 (independente)**: dado que a janela bounce capta momentum, esperava-se correlação positiva alta com bounce_pct (quanto maior o bounce, mais compras). Pearson = -0.252 → correlação baixa e levemente negativa — H23 bounce não é simplesmente proxy de bounce_pct. Isso é surpreendente e sugere que mesmo a janela errada captura alguma informação estrutural independente. Não é argumento para manter — a janela bottom é metodologicamente correta.
131. **5/12 TPs históricos (42%) tinham net_sol_flow negativo**: na faixa "bom (3-10 SOL)" — 35.7% WR — todos os 14 trades já tinham bounce ≥ 55%. Mas dos 12 TPs totais, 5 tinham H23 negativo (PENNY -25 SOL, GEGE -11 SOL, PUFF -9.22 SOL, GROX -7.93 SOL, Miss -1.65 SOL). Esses tokens com bounce explosivo (>100%) frequentemente tinham H23 negativo porque os vendedores estavam realizando lucro durante o próprio bounce. Gate para filtro H23: 42% de TPs perdidos é inaceitável — confirmar que bottom-window não tem o mesmo padrão antes de implementar como gate obrigatório.
132. **`pnl_pct_real` está armazenado como decimal (0.75 = 75% ganho)**: display com `f"{pnl:+.1f}%"` mostra `0.75` como `+0.8%` na tela — parece 0.8% mas significa 80% de retorno. TPs mostrando "+0.9%" = +90% real. SLs mostrando "-0.3%" = -30% real. Trades suspeitos com status=sl e "+0.2%" = +20% (artefato de execução marginal acima do entry, não TP real). Nunca interpretar os valores literalmente como porcentagem — multiplicar por 100 para obter o retorno real.
133. **`/debug/h23-grid` — grade 2D H23 × bounce_pct**: endpoint de análise cruzada. Grid com WR por célula (linhas = faixas de net_sol_flow, colunas = faixas de bounce_pct). Tabela de simulação com 8 combinações de filtros mostrando n, WR, delta pp vs base, trades bloqueados (%) e TPs perdidos (%). Descoberta: na faixa 3-10 SOL, todos os 14 trades (TPs e SLs) já tinham bounce ≥ 55% — os dois filtros são correlacionados nessa faixa, não multiplicativos.

---

## 🔄 Estado Atual: Bot ativo a $1/trade (2026-05-16, sessão 7)

### Pipeline ativo
`TRADING_ENABLED=true`, `CALIBRATION_AMOUNT_USD=1.0`, `CALIBRATION_MAX_TRADES=999999`.
`TRADE_SL_PCT=0.25` (testando — 15% disparava em segundos por volatilidade pAMM).
`HELIUS_RPC_URL` configurado no Railway — `helius_rpc.py` ativo para fill + posição.

**Filtros ativos (ordem de aplicação no _check_bounce):**
- A.2: bloqueia bounce_pct 20-30% (redundante com A.7 mas mantido como safety net)
- A.3: bloqueia entry 50-65% do mc_migration
- A.5: bloqueia elapsed > 20min
- A.6: bloqueia fundo estagnado > 5min
- A.7: bloqueia bounce_pct < 30% (BOUNCE_MIN_PCT=0.30)
- smart≥3: FILTER_SMART_MIN=3 — **⚠️ discriminação questionável** (live: smart=1→31%, smart=3→30%, flat)
- FILTER_PUMP_ONLY: garante cobertura H22

**⚠️ WR REAL CORRIGIDO (blockchain, sessão 5 2026-05-14): 10.1%** (14 TP / 125 SL / 139 trades).
WR anterior de 32% era fictício — baseado em TradeMonitor que inferia saídas de token_ticks, nunca da blockchain.
DRY RUN do `/debug/sell-recovery`: 573 txs escaneadas, 201 sells, 139 corrigidos, 0 não encontrados.
Executar sem `?dry=1` para persistir no banco.

**Maior discriminador live encontrado: bounce_pct >70% → WR 74% (n=23, +45pp)**
Próxima mudança de env var: `BOUNCE_MIN_PCT=0.50` (teste) → `0.70` se confirmado.

**Análise trade-autopsy (`/analise/trade-autopsy`):**
- Seção 2b (pré/pós exit): usa `mc` (close), não `mc_high` — spike intra-candle eliminado
- `hit_tp_before_exit` / `hit_tp_after_exit`: flags booleanas por trade
- `spike_suspect`: max_gain alto + <5 pre_ticks = provável spike artifact
- GMGN trailing TP confirmado funcionando — mc_high era o dado contaminado

**Pendências críticas (sessão 7):**
- ✅ **`/debug/sell-recovery` executado** — 139 correções blockchain persistidas. Banco 100% verificado on-chain.
- ✅ **Bug H23 corrigido** — `get_absorption_window()` agora usa `ts_min` (fundo) em vez de `time.time()` (bounce)
- ✅ **`h23_window` tagging implementado** — histórico marcado como `'bounce'`, novos bounces com `'bottom'`
- ✅ **`/debug/h23-grid` criado** — grade 2D H23 × bounce_pct com simulação de 8 combinações de filtros
- Mudar `BOUNCE_MIN_PCT=0.50` no Railway (env var, zero código)
- Aguardar n≥30 bounces com `h23_window='bottom'` para validação real do H23
- Verificar discriminação smart_wallets após n≥50 trades com smart≥3

**Regra de ouro para novos filtros:** só implementar com n≥30 trades validados no GMGN. **Nunca usar DB WR como fonte — banco estava completamente errado antes da correção blockchain da sessão 5.**

**Próximos passos — ver ROADMAP.md**

---

## 📋 Roadmap: Helius + Jupiter Swap (médio prazo)

### Por que faz sentido (não é urgente)
A $10/trade a friction é aceitável com GMGN (~9.2% total). Helius/Jupiter reduz para ~1.1%.
Implementar DEPOIS de confirmar WR com trailing TP em $10/trade.
GMGN ainda cobra 1%×2 = 2% adicional. Jupiter elimina esse custo.

### O que muda na arquitetura

```
ATUAL:
  executor.py → gmgn.swap() → /v1/trade/swap (GMGN)
  condition_orders embutidos → GMGN gerencia TP/SL
  TradeMonitor → token_ticks (MC) → detecta exit (referência ERRADA)

NOVO:
  executor.py → jupiter.swap() → Jupiter API v6 → Helius RPC
  TP/SL: TradeMonitor já funciona → submete sell via Jupiter quando detecta exit
  Referência correta: entry_price_usd real (capturado do fill da tx Solana)
```

### Mudanças necessárias no código

**1. `gmgn_client.py` — manter apenas discovery + sinais**
- Manter: `get_trenches()`, `get_token_signal()`, `get_token_kline()`, `get_token_info()`
- Remover do executor: `gmgn.swap()` — não mais chamado para execução

**2. Novo `jupiter_client.py`**
- `get_quote(input_mint, output_mint, amount_lamports, slippage_bps)` → Jupiter `/v6/quote`
- `swap(quote_response, wallet_pubkey)` → Jupiter `/v6/swap` → retorna transaction serializada
- `submit_tx(signed_tx)` → Helius RPC `sendTransaction` com `skipPreflight=false`
- Fee: 0% plataforma Jupiter (integração direta). DEX LP fee ~0.25% (inevitável).
- Tip: configurável, padrão 0.00005 SOL (≈ 10× menor que atual)

**3. `executor.py` — trocar gmgn.swap por jupiter.swap**
- `_execute()`: chamar `jupiter_client.swap()` em vez de `gmgn.swap()`
- Fill data: capturar da confirmação da tx Solana (slot, fee, amounts do AccountInfo)
- Remover: `condition_orders` body (não existe no Jupiter)
- TP/SL: TradeMonitor detecta via ticks → chama `jupiter_client.swap()` para sell

**4. `executor.py` — TradeMonitor: corrigir referência TP/SL**
- Usar `entry_price_usd` (fill real) como referência, não `entry_expected`
- Só marca TP/SL quando `entry_price_usd` está populado
- TP threshold: `entry_price_usd × (1 + tp_pct)`
- SL threshold: `entry_price_usd × (1 - sl_pct)`

**5. Novas env vars**
```
HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?api-key=xxxx
WALLET_PRIVATE_KEY=xxxx   # Base58 da wallet Solana (para assinar txs)
JUPITER_API_URL=https://quote-api.jup.ag/v6
SOL_MINT=So11111111111111111111111111111111111111112
TIP_SOL=0.00005            # tip Jito (reduzido de 0.001 para 0.00005)
PRIORITY_FEE_MICRO_LAMPORTS=1000  # priority fee controlado (não GMGN default)
```

### Break-even esperado após migração

| Custo | Atual (GMGN) | Após (Jupiter+Helius) |
|-------|-------------|----------------------|
| Swap fee | 1% × 2 = 2% | ~0.25% × 2 = 0.5% (LP fee) |
| Tip | 0.001 SOL × 4 ≈ $0.36 | 0.00005 SOL × 4 ≈ $0.018 |
| Priority | 0.001 SOL × 4 ≈ $0.36 | controlado ~$0.04 |
| **Total a $10/trade** | **~$0.92 (9.2%)** | **~$0.11 (1.1%)** |
| Break-even WR a $10 | ~28% | ~18% |

### Ordem de implementação
1. Assinar Helius (primeiro mês $24.50)
2. Criar `jupiter_client.py` com quote + swap + submit
3. Testar swap manual via `/debug/test-swap` com Jupiter
4. Migrar `executor._execute()` para Jupiter
5. Corrigir TradeMonitor para usar `entry_price_usd` real
6. Religar bot com `TRADING_ENABLED=true` a $10/trade

---

## 🎯 KPIs de Sucesso

| Fase | Métrica | Meta | Status |
|------|---------|------|--------|
| Coleta | Migrações com bottom + mc_1h | ≥200 | ✅ 425 |
| Coleta | Cobertura campos Fase 1 | ≥80% dos tokens novos | ✅ |
| Análise | H10 validada | delta_pp ≥ 20pp | ✅ +21.8pp |
| Backtester | EV positivo com SL=15% | EV > 0 em TP ≥ 30% | ✅ confirmado (n=68) |
| Backtester | Cobertura de ticks | n_trades ≥ 200 | ⏳ kline OHLC acumulando |
| Fase A | WR pós-filtros ≥ 25% | WR ≥ 25% com 30+ bounces filtrados | ⏳ aguardando dados |
| Fase B | Cobertura slope ≥ 50% | 50% dos bounces com slope calculável | ⏳ impacto esperado |
| Fase 6-lite | Friction total medida | 53 trades on-chain auditados | ✅ CONCLUÍDA — ATA rent é o principal custo |
| Fase 6-lite | EV_real > 0 após friction | EV_real > 0 | 🟡 -$5.99 após reclaim rent — viável |
| Fase 6 | WR real confirmado | ≥15% | ✅ 15.1% on-chain (acima break-even de 11%) |
| Fase 6 | Bot ativo | TRADING_ENABLED=true | ✅ relançado 2026-05-10 a $1/trade |
| Fase 6 | Re-entry em tokens com TP | rastrear tokens com TP e monitorar novo bounce | ⏳ ROADMAP |
| H9 | Validação smart money signal | delta_pp ≥ 20pp | 🟡 +11.8pp na faixa <50% (n=14) |

---

## 🧠 Metodologia — Big Player

1. Hipóteses documentadas ANTES de ver os dados
2. Coleta de dados pura (sem viés de confirmação)
3. Análise exploratória antes de construir qualquer feature
4. Filtros construídos sobre o backtest, não sobre intuição
5. Backtest walk-forward (in-sample / out-of-sample) — nunca validar no mesmo conjunto
6. Deploy com capital mínimo como validação final

> Sardinhas reagem após a migração. Big players já estão posicionados na Watchlist desde os 80%.
> Sardinhas olham volume. Big players olham `creator_token_status`.
> Sardinhas seguem intuição. Big players seguem dados out-of-sample.
> Sardinhas constroem features. Big players constroem datasets.
> Sardinhas medem delta_smart. Big players esperam H9 com dados reais de compra ativa.
> Sardinhas confiam no EV de 63 tokens. Big players verificam os 492 que estão faltando.
> Sardinhas fazem walk-forward backtest e esperam win rate ≥50%. Big players sabem que 23% win rate com 8.7x payoff bate 50% win rate com 1x payoff.
> Sardinhas analisam. Big players traduzem análise em execução com capital mínimo para medir o que o modelo não consegue ver.
> Sardinhas definem TP fixo. Big players definem saída em camadas — realizam parcial no primeiro sinal de força e deixam o restante correr com proteção dinâmica.
