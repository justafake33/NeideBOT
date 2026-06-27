# NeideBOT — Contexto do Projeto


## O que é:
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
> Se um token ainda estiver caindo após **20 minutos** da migração sem bounce → descartar.
> BOUNCE_TIME_MAX = 20min (migração → sinal). Time-stop é na detecção do sinal, não no trade.

### Pipeline de execução
```
ESTÁGIO 1 — Watchlist (bc_progress ≥ 80%)
    │
    ├─ POST /v1/trenches type=near_completion → data["pump"]
    ├─ Filtros aplicados (rug, rat, top10, smart, wash)
    ├─ Campos Fase 1 coletados do trenches (creator_status, cto_flag, etc.)
    └─ Token entra na watchlist com dados de qualidade
       └─ HeliusWS.subscribe(mint) disparado aqui — sub ativa ANTES da migração

ESTÁGIO 2 — Migração detectada
    │
    ├─ POST /v1/trenches type=completed → data["completed"]
    ├─ Filtros de MC ($5k-$200k), liquidez (≥$10k), fee (≥1 SOL)
    ├─ mc_migration = bonding_curve_ref (MIGRATION_SOL_UNITS × SOL/USD) ou kline open ou trenches
    └─ Token entra no HotMonitor por 60 minutos

HotMonitor (janela 60min)
    ├─ push_mc (5s): rastreia mc_min, mc_max, mc_last + _check_bounce()
    ├─ hot-precise (5s): kline low + close → mc_min (NÃO high) + volume_1m back-fill
    │       ⚠️ kline NÃO escreve mc_min_decision — erpc é a fonte única de mc_min em tempo real
    └─ _tick (30s): graduação após 60min → HeliusWS.unsubscribe(mint)
            └─ kline scan 1h completo → mc_bottom (low), mc_peak (close), mc_1h
            └─ mc_min_true preenchido async via helius_rpc.get_pool_bottom() após graduação

FASE 2 — coleta ativa (IMPLEMENTADO 2026-05-03)
    ├─ _poll_signals() a cada 5s: POST /v1/market/token_signal type 12
    ├─ Feed global → filtra por mints ativos no HotMonitor
    ├─ Persiste em token_signals (mint, trigger_at UNIQUE, trigger_mc, wallets_json)
    └─ Ban guard: _signal_ban_until — não chama enquanto banido

BOUNCE DETECTOR + FASE 6 — execução real (ATIVO)
    ├─ _check_bounce() chamado em cada push_mc e _precise_bottom_tick
    ├─ Condições base: drop ≥ BOUNCE_DROP_MIN + bounce ≥ BOUNCE_MIN_PCT + elapsed ≤ 20min
    ├─ Filtros ativos (ordem de aplicação):
    │       ├─ A.5: elapsed > BOUNCE_TIME_MAX → rejeita
    │       ├─ A.6: fundo estagnado > BOTTOM_FRESHNESS_SEC → rejeita
    │       ├─ A.7: bounce_pct < BOUNCE_MIN_PCT → rejeita
    │       ├─ A.8: bounce_pct > BOUNCE_MAX_PCT → rejeita (entradas tardias)
    │       ├─ C3: pool depth < POOL_MIN_SOL_BOUNCE → rejeita (secondary pool / pool rasa)
    │       └─ RISE gate: RISE net flow < RISE_NET_MIN → rejeita (rec75=9% com RISE<0)
    ├─ One-shot por token: check-and-set atômico sob _lock (bounce_fired)
    ├─ Persiste em bounce_signals (UNIQUE mint) + alerta Telegram
    └─ TradeExecutor.on_bounce() → thread _execute() → swap() → _wait_fill()
            └─ condition_orders no body do swap: trailing TP (ativ. 75%, drawdown 30%) + SL fixo
```

### Resumo simplificado do pipeline (6 passos)

1. **Watchlist** — Scanner vê tokens com ≥80% da bonding curve preenchida → filtra qualidade (rug, bundlers, wash, snipers) → subscreve feed Helius em tempo real.
2. **Migração detectada** — Token completa bonding curve e aparece como `completed` no trenches → filtros de MC ($5k-$85k), liquidez (≥$10k) → salva no banco → começa a monitorar por 60 minutos. Também captura: velocidade de migração (`time_to_migrate_sec`) e se estava em trending do Pump.fun (`trending_at_migration`).
3. **Rastreamento do fundo** — HotMonitor atualiza MC a cada 5s via Helius (eventos de compra/venda on-chain em ~5ms) + kline 1m de fallback. Registra MC mínimo, máximo e último a cada momento.
4. **Detecção do bounce** — Se o token caiu ≥30% e subiu ≥10% do fundo em menos de 20 minutos → sinal de bounce disparado. Verifica também se a pool tem liquidez suficiente (≥20 SOL).
5. **Execução do trade** — Swap via GMGN API (entrada imediata) com stop-loss fixo (-25%) e trailing TP (ativa em +75%, vende se recuar 30% do pico).
6. **Análise pós-trade** — Outcome registrado no banco · WR acumulado via `/analise/wr-breakdown` · fundos reais verificados on-chain via Helius (`mc_min_true`).

---

## 🔍 ESTADO ATUAL

### Bot ATIVO (sessão 23, 2026-06-22)

**Branch:** `claude/magical-cerf-3wjq5o`
**Modo WebSocket:** Helius logsSubscribe per-mint (erpc.global desativado — 0 créditos)
**Railway:** ~$6/mês (`welcoming-miracle` web+worker + `intelligent-spirit` PostgreSQL)
**Operação:** Solo desde 2026-06-22 (sócio saiu)
**Banco:** Novo PostgreSQL criado 07/06/2026 — dados anteriores não disponíveis no banco ativo

**⚠️ Marco zero de dados confiáveis: 2026-05-26** — Phantom fix (ALT tx bypass) + auditoria n=15 confirmam mc_min capturado a partir desta data como confiável. WR histórico do DB é fictício (TradeMonitor inferia, nunca observava blockchain). Único WR confiável: auditoria on-chain via `/debug/sell-recovery`.

**⚠️ Modo Helius:** gap de ~10-30s entre detecção do token e início do feed WS. Fundos em V-bottom muito rápido (<30s pós-migração) podem ser perdidos. Kline hot-precise (5s) e trenches (5s) cobrem como fallback. Impacto real medido: 3/15 trades históricos afetados.

**Pipeline ativo:**
`TRADING_ENABLED=true` · `CALIBRATION_AMOUNT_USD=1.0` · `CALIBRATION_MAX_TRADES=5000000`
`TRADE_SL_PCT=0.25` · `FILTER_SMART_MIN=1` · `BOUNCE_MIN_PCT=0.10` · `BOUNCE_MAX_PCT=1.00`
`MIN_POOL_SOL=10` · `BOUNCE_DROP_MIN=0.30` · `TRADE_MAX_ELAPSED_SEC=900` · `BOTTOM_FRESHNESS_SEC=600`
`POOL_MIN_SOL_BOUNCE=20` · `TRADE_MIN_POOL_SOL=40` · `FILTER_PUMP_ONLY=true`
`RISE_NET_MIN` **não configurado (gate desativado)** · `GMGN_SCANNER_API_KEY` (pendente configurar no Railway)

**Resultado período 2026-05-21 a 2026-05-24 (31 trades, $1/trade):**
WR: **29%** (9 TPs / 22 SLs) confirmado GMGN · Net P/L: **+$0.12** · Payoff 2.49×

**Correções críticas deployadas:**
- ✅ ts_migration usa complete_timestamp on-chain (não datetime.now())
- ✅ bounce_fired check-and-set atômico sob _lock (sem double-fire)
- ✅ elapsed_sec < 90 em todos os loops kline (candle de transição)
- ✅ K_INIT_TOLERANCE = 0.80 hardcoded (cobre pools com ~130 trades de fee)
- ✅ BOUNCE_MAX_PCT guard corrigido (< 9.99, não < 1.0)
- ✅ Phantom fix ALT tx (accounts is not None guard)
- ✅ C1 sl_ref = entry_mc_real or mc_at_signal or entry_expected
- ✅ C3 pool depth gate no bounce (POOL_MIN_SOL_BOUNCE=10)
- ✅ Priority lane para swap (bypass global ban)
- ✅ API key separada para scanner (GMGN_SCANNER_API_KEY)

### Campos coletados por migração

| Campo | Fonte | Descrição |
|-------|-------|-----------|
| mint | trenches | Endereço do token |
| token_nome | trenches | Símbolo |
| ts_detected | Sistema | Timestamp watchlist (bc ≥ 80%) |
| ts_migration | trenches (complete_timestamp) | Timestamp on-chain da migração |
| mc_migration | bonding_curve/kline/trenches | MC oficial |
| mc_migration_bc_ref | SOL/USD × MIGRATION_SOL_UNITS | Referência teórica da bonding curve |
| mc_migration_source | "bonding_curve"/"kline"/"trenches" | Qual fonte foi usada |
| mc_bottom | HotMonitor (erpc) | MC mínimo na janela quente |
| mc_min_true | helius_rpc.get_pool_bottom() | Fundo real on-chain com txid_proof |
| bottom_error_pct | Calculado | (mc_min_decision - mc_min_true) / mc_min_true |
| bottom_quality | Calculado | "good" (≤5%), "ok" (5-15%), "bad" (>15%) |
| mc_peak | push_mc (close) | MC máximo na janela quente |
| mc_1h | kline / token/info / push_mc | MC 1h após migração |
| time_to_bottom | HotMonitor | Minutos até o fundo |
| smart_wallets | trenches | smart_degen_count |
| rug_ratio | trenches | bundler_trader_amount_rate |
| top10_holder_rate | trenches | Concentração top 10 holders |
| is_wash_trading | trenches | Campo nativo GMGN |
| liq_migration | trenches | Liquidez no momento da migração |
| creator_token_status | trenches | "creator_hold" / "creator_close" |
| creator_balance_rate | trenches | % do supply que o dev tem |
| lock_percent | token/info (graduação 1h) | locked_ratio do /v1/token/info |
| bluechip_owner_pct | N/A | ⚠️ Apenas em /v1/market/rank. Coluna fica NULL. |
| exchange | trenches | Raydium, Meteora, pump, etc. |
| complete_timestamp | trenches | Unix timestamp da migração (GMGN) |

---

## 📐 HIPÓTESES FORMAIS

⚠️ Todos os resultados H1-H21 são baseados em dados históricos com mc_bottom corrompido — ver ROADMAP.md para contexto.

| # | Hipótese | Status | Resultado curto |
|---|----------|--------|-----------------|
| H1 | ≥70% dos tokens caem ≥20% do ATH nos primeiros 60min | ✅ | 91.4% — direção verdadeira, % exato incerto |
| H2 | Em tokens com recovery=TRUE, fundo ocorre em ≤15min | ❌ | 49.7% (meta: ≥70%) — dados corrompidos |
| H3 | smart_wallets ≥3 → recovery ≥15pp maior | ❌ | +0.2pp — não discrimina (snapshot, não compra ativa) |
| H4 | sniper ≥30% → recovery <30% | ❌ | levemente positivo — dados corrompidos |
| H7 | liq_migration <$5k → slippage inaceitável >$50 | ⏳ | pendente validação em execução |
| H8 | is_wash_trading=TRUE → recovery ≥20pp menor | ❌ | -0.1pp — não discrimina |
| H9 | signal type 12 trigger_mc < 80% mig → recovery ≥20pp maior | ❌ ANTICORRELADO | Com H9: 6.7% WR (n=17 bounces). Sem H9: 26.7% WR (n=111). H9 prediz FALHA, não sucesso. NÃO usar como gate positivo. Avaliar como gate NEGATIVO (bloquear). Live trades: com H9 WR 16.7%, sem H9 WR 18.4%. |
| H10 | time_to_bottom < 25min → recovery ≥20pp maior | ✅ | +21.8pp (61.2% vs 39.3%, n=425) — único preditor robusto |
| H11 | Deceleração de preço precede fundo em recovery tokens | ❌ | Invertido — recovery = capitulação brusca |
| H12 | dump_depth + time_elapsed → zona P(recovery)>50% | ⚠️ | Parcial — time domina sobre depth |
| H13 | Slope íngreme pré-fundo → WR ≥40pp maior | ❌ | Flat em n=153. A.1 removido. |
| H14 | bounce_pct 20-30% = dead zone | ✅ | 3.1% WR (n=32) — A.2 mantido como safety net |
| H15 | Entry 50-65% mc_migration → WR=0% | ✅ | 0/21 — A.3 mantido como safety net |
| H16 | 3 ticks acima entry → WR ≥20pp maior | ✅ | +18pp — A.4 removido (entrada muito alta) |
| H17 | 100% TPs em ≤20min após sinal | ✅ | 11/11 ≤20min. BOUNCE_TIME_MAX=20min. |
| H18 | Bounce velocity discrimina WR | ❌ | Flat em todos os tercis |
| H19 | 1-2min delay melhora EV vs entrada imediata | ✅ | +6.6pp — coberto por A.4, A.4 removido |
| H20 | W-bottom = sinal de confirmação de suporte | ❌ | 98% prevalência — ubíquo demais |
| H21 | SL ancorado no mc_bottom (×0.90) resolveria maioria dos SLs | ❌ | 90% dos SLs caem abaixo de 0.55× o fundo. Dynamic SL descartado. |
| H22 | Buy volume > sell volume nos 60s pós-fundo discrimina WR ≥15pp | ⏳ | DATA COLLECTION — janela rolante ativa, métricas brutas em bounce_signals |
| H23 | net_sol_flow pré-fundo > threshold discrimina WR | 🟡 | COLETA ATIVA — janela corrigida (ts_min, não time.time). 86% bounces têm net<0. Gate H23_NET_MIN deployado mas desativado (n insuficiente) |
| H24 | Seller exhaustion (top-3 > 70% dump_vol) → WR maior | ⏳ | PENDENTE — hipótese central pós H21 |
| H25 | Pool depth < 20 SOL → SL é ruído de AMM | 🟡 | pool>90 SOL NÃO é sinal positivo (WR=17.5% em decision_entry). Pool<10 SOL = ruído = C3 válido. |
| H26 | Buy concentration (largest_buy/buy_vol) alta → WR maior | ⏳ | PENDENTE CORRELAÇÃO — campo calculável dos dados existentes |
| H27 | trending_at_migration=TRUE → recovery ≥15pp maior | 🟡 PRELIMINAR | WR 30.3%, EV +5.3% (n=46 bounces, apenas 2 dias: 2026-06-26/27). TP50/SL25 ótimo: WR 50%, EV +12.5%. mig<60s → WR 0% (runners). smart≥3+trending → WR 60%. Best combo: trending+smart≥1+elapsed<10min → WR 38.9%, EV +13.9% (n=22). Cobertura 13% das migrações. Aguardar n≥100 (≈2 semanas). |
| H28 | time_to_migrate_sec < 60 → manipulação ou runner absoluto (WR diferente) | 🟡 CONFIRMADO NEGATIVO (trending) | Dentro de trending=TRUE: mig<60s → WR 0% (n=7). mig 300-900s → WR 60% (n=8). Bloquear time_to_migrate_sec < 60 como pré-filtro quando trending gate ativo. |

---

## 🏗️ ARQUITETURA ATUAL

```
HeliusWS (WebSocket — background thread, sempre ativo)
    ├─ Modo ATUAL: Helius logsSubscribe por mint (erpc desativado)
    ├─ Modo FUTURO: erpc global transactionSubscribe (quando WR ≥20% confirmado)
    ├─ Tier 1 inline (~5ms): parse Anchor BuyEvent/SellEvent do pAMM
    │       └─ Preço via pool_reserves (offsets 48/56) → _sol_usd_cached
    │       └─ K-gate: filtra secondary pools (K_INIT_TOLERANCE=0.80)
    │       └─ A.9: pool_quote_sol >= MIN_POOL_SOL (filtra pools rasas)
    │       └─ Acumula {ts, side, sol, mc, pool_quote_sol} em deque(maxlen=300) por mint [H22]
    └─ Tier 2 (DexScreener HTTP, rate-limited): fallback de preço

Scanner (poll 5s)
    │
    ├─ POST /v1/trenches near_completion → data["pump"]
    │       └─ bc ≥ 80% + filtros qualidade → Watchlist + DB
    │       └─ Campos Fase 1 coletados do trenches (creator_status, cto_flag, etc.)
    │       └─ HeliusWS.subscribe(mint) — PRÉ-subscrição aqui, não na migração
    │
    ├─ POST /v1/trenches completed → data["completed"]
    │       └─ Filtros MC/liq/fee → upsert_migration
    │       └─ mc_migration = bonding_curve_ref (MIGRATION_SOL_UNITS × SOL/USD) || kline open || trenches
    │       └─ HotMonitor.add() com ts_migration = datetime.fromtimestamp(complete_timestamp)
    │       └─ push_mc para TODOS os completed já vistos (atualiza _mc_live)
    │
    ├─ _poll_signals() — Fase 2 (a cada 5s, apenas se HotMonitor tem tokens ativos)
    │       └─ POST /v1/market/token_signal → feed global type 12 (SmartDegenBuy)
    │       └─ Filtra por tokens ativos no HotMonitor._tokens
    │       └─ Persiste matches em token_signals (ON CONFLICT DO NOTHING)
    │       └─ Ban guard: _signal_ban_until — checar ANTES de chamar
    │
    └─ HotMonitor (janela 60min)
            ├─ push_mc (5s): rastreia mc_min, mc_max, mc_last, _mc_live
            │       └─ _check_bounce(): filtros A.5/A.6/A.7/A.8/C3/RISE + bounce_fired atômico
            │               ├─ Coleta get_absorption_window(mint, 60, window_end_ts=ts_min) → H22
            │               ├─ Salva em bounce_signals (UNIQUE mint)
            │               └─ TradeExecutor.on_bounce() → thread _execute() [Fase 6]
            ├─ hot-precise (5s): kline low + close (NÃO high)
            │       └─ mc_peak: close × supply (high descartado — spike intra-candle)
            │       └─ Chama _check_bounce() com close mais recente
            │       └─ Janela Helius: max(90, elapsed-60) — nunca antes de ts_mig+90s
            └─ _tick (30s): graduação após 60min → HeliusWS.unsubscribe(mint)
                    └─ kline scan 1h completo → mc_bottom (low, skip elapsed_sec<90), mc_1h
                    └─ token/info sempre chamado: fallback mc_1h + lock_percent (locked_ratio)
                    └─ _run_bottom_truth async: helius_rpc.get_pool_bottom() → mc_min_true + txid

TradeExecutor (Fase 6 — ATIVO)
    ├─ on_bounce(): verifica TRADING_ENABLED + count_trades < CALIBRATION_MAX
    ├─ insert_trade(): cria registro com status='pending'
    └─ _execute() [thread própria]:
            ├─ swap(): POST /v1/trade/swap (Ed25519 signed) — priority=True (bypass global ban)
            ├─ _wait_fill(): poll query_order (priority=True) até state=confirmed (timeout: ORDER_TIMEOUT_SEC)
            ├─ helius_rpc.get_swap_fill(tx_sig, mint) → tokens_received, sol_spent (fill on-chain)
            └─ condition_orders no body do swap:
                    trailing TP (price_scale="75", drawdown_rate="30", sell_ratio="100")
                    SL fixo (price_scale=str(TRADE_SL_PCT*100), sell_ratio="100")

get_sol_usd() [gmgn_client.py]
    ├─ Cache 5min (_sol_usd_value / _sol_usd_ts)
    ├─ Fonte primária: CoinGecko API
    └─ Fallback: GMGN token/info da SOL mint
```

### Tabelas do banco
| Tabela | Conteúdo |
|--------|----------|
| `migrations` | Uma linha por token migrado — todos os campos de qualidade + checkpoints + mc_min_true/bottom_quality |
| `token_ticks` | Série temporal por token. `source='trenches'` (~30s) ou `source='kline'` (com OHLC). |
| `token_signals` | **Fase 2** — sinais type 12 por token durante janela quente |
| `bounce_signals` | **Fase 2** — um registro por token quando bounce detector dispara (UNIQUE mint). 8 colunas H22: `absorption_buy_vol`, `sell_vol`, `buy_cnt`, `sell_cnt`, `lrg_buy`, `events`, `score=NULL`, `grade=NULL`. Também: `h23_window`, `trending_at_bounce`. |
| `trades` | **Fase 6** — log completo de cada trade live. Coluna `fill_source TEXT` rastreia origem do fill data. |
| `trade_absorption_ticks` | Snapshots contínuos de absorção Helius (+30s/+60s pós-bounce) por trade em monitoramento. |
| `micro_absorption_snapshots` | **V2 Fase 1** — série temporal de microdados de fluxo por token, capturados a cada 2s durante os 60s após o fundo. ~97 colunas: janelas PRE/RISE/LIVE. Research-only. |
| `sell_exhaustion_candidates` | Snapshots de sell velocity a cada 5s por token durante 120s pós-fundo. Research-only. |

### HeapScheduler
- Thread única com heap de eventos futuros
- Nunca usar `threading.Timer` por evento (não escala)
- `scheduler.schedule(delay_sec, fn, *args)`

### Fontes de dados por prioridade
| Dado | 1ª fonte | 2ª fonte | 3ª fonte |
|------|----------|----------|----------|
| mc_migration | bonding_curve_ref (MIGRATION_SOL_UNITS × SOL/USD) | kline (open no complete_ts) | trenches usd_market_cap |
| mc_min (real-time) | erpc/Helius Tier 1 (k-gate + A.9) | kline 1m low (hot-precise) | push_mc (trenches 5s) |
| mc_min_true | helius_rpc.get_pool_bottom() (async pós-graduação) | — | — |
| mc_peak | push_mc (trenches close) | kline 1m close (hot-precise + graduate) | — |
| mc_1h | kline (ts_mig a ts_mig+3660) | token/info | push_mc (mc_last) |
| volume_1m (token_ticks) | kline candle volume (hot-precise back-fill) | — | — |
| lock_percent | token/info locked_ratio (graduação 1h) | — | — |
| bluechip_owner_pct | ⚠️ N/A — apenas em /v1/market/rank | — | — |
| SOL/USD | CoinGecko API (cache 5min) | GMGN token/info SOL mint | — |

---

## 🔧 Variáveis de Ambiente (Railway)

```
DATABASE_URL=postgres://...
GMGN_API_KEY=xxxx            # key de TRADE (swap/query_order) — nunca bloqueada pelo scanner
GMGN_SCANNER_API_KEY=xxxx    # key dedicada ao scanner/discovery — rate limit separado. Configurar em web E worker.
GMGN_PRIVATE_KEY=xxxx        # Ed25519 PEM — chave de assinatura GMGN (NÃO a private key da wallet Solana)
GMGN_WALLET_ADDRESS=xxxx     # endereço Solana da carteira — necessário para Fase 6
GMGN_HOST=https://openapi.gmgn.ai
TELEGRAM_TOKEN=xxxx
TELEGRAM_CHAT=xxxx
SENTRY_DSN=xxxx              # DSN padrão (não o obsoleto)

# WebSocket — modo de operação
HELIUS_API_KEY=xxxx          # Helius free tier (logsSubscribe per-mint) — modo atual
# ERPC_API_KEY=xxxx          # erpc.global (transactionSubscribe global) — desativado (0 créditos)
# WS_RPC_URL=wss://...       # URL WSS customizada (QuickNode, etc.) — usa padrão Helius se ausente

# Filtros do Scanner (configuráveis sem redeploy)
FILTER_RUG_MAX=0.15          # máx token["rug_ratio"] (score interno GMGN, 0-1)
FILTER_BUNDLER_MAX=0.20      # máx bundler_trader_amount_rate (% bundlers real)
FILTER_BOT_MAX=0.60          # máx bot_degen_rate
FILTER_SNIPER_MAX=0.10       # máx top70_sniper_hold_rate (rígido — snipers que ainda seguram)
FILTER_RAT_MAX=0.20          # máx rat_trader_amount_rate
FILTER_TOP10_MAX=0.70        # máx top_10_holder_rate
FILTER_SMART_MIN=1           # mín smart_degen_count (1 = pelo menos 1 smart viu o token)
FILTER_WASH_BLOCK=true       # bloquear is_wash_trading
FILTER_MC_MIN=5000           # MC mínimo USD
FILTER_MC_MAX=85000          # MC máximo USD
FILTER_LIQ_MIN=10000         # liquidez mínima USD
FILTER_PUMP_ONLY=true        # só tokens Pump.fun — descarta Meteora/Raydium direto
BC_MIN_WATCHLIST=80          # % BC mínimo para watchlist
MAX_MIG_AGE_SEC=600          # ignorar migrações > 10min no restart
POLL_INTERVAL_SEC=5          # intervalo do scanner
HOT_PRECISE_SEC=5            # alinhado com trenches poll
MIGRATION_SOL_UNITS=410      # SOL teórico da bonding curve → mc_migration_bc_ref
MIN_POOL_SOL=10              # filtro A.9 — pool mínima para eventos Helius (secondary pool filter)

# Bounce detector (configuráveis sem redeploy)
BOUNCE_MIN_PCT=0.10          # bounce mínimo do fundo para disparar sinal
BOUNCE_MAX_PCT=1.00          # bounce máximo — bloqueia entradas tardias (guard: < 9.99)
BOUNCE_DROP_MIN=0.30         # drop mínimo da migração para considerar
BOUNCE_TIME_MAX=20.0         # A.5: janela máxima em minutos
BOTTOM_FRESHNESS_SEC=600     # A.6: máx 10min desde o último mc_min
POOL_MIN_SOL_BOUNCE=20       # C3: pool depth mínima no momento do bounce
BOUNCE_RECORD_MIN_POOL_SOL=5 # pool mínima para registrar bounce_signal (menor que C3)
# RISE_NET_MIN=0             # RISE gate — NÃO configurado no Railway (desativado). Ver /analise/pre-rise-ticks para avaliar.
# H23_NET_MIN=0              # H23 gate (DESATIVADO — n insuficiente). Adicionar no Railway para ativar.
TRADE_MAX_ELAPSED_SEC=900    # descarta token se elapsed_to_bottom > 15min

# Fase 6 — Execução de trades (configuráveis sem redeploy)
TRADING_ENABLED=true         # true para ativar trades reais
CALIBRATION_AMOUNT_USD=1.0   # tamanho de cada trade em USD
CALIBRATION_MAX_TRADES=5000000
TRADE_SL_PCT=0.25            # stop loss
TRAILING_ACTIVATION_PCT=75   # trailing: gain% mínimo para ativar
TRAILING_DRAWDOWN_PCT=30     # trailing: recuo máximo do pico antes de vender
TRADE_SLIPPAGE_BPS=500       # slippage máximo em basis points (5%)
ORDER_POLL_SEC=2             # intervalo de poll do fill (segundos)
ORDER_TIMEOUT_SEC=60         # timeout do fill (segundos)
TRADE_MIN_NET_SOL_FLOW=-999  # gate H23 legado no executor — manter em -999 (desativado)

# Gate executor — checagens finais antes de executar swap
TRADE_GATE_ENABLED=true               # habilita o gate final do executor
TRADE_REQUIRE_HELIUS_FEATURES=true    # exige helius_context, pool_quote e net_sol_flow preenchidos
TRADE_REQUIRE_H23_BOTTOM=true         # exige h23_window="bottom" — sempre true na prática (hardcoded)
TRADE_MIN_POOL_SOL=40                 # pool mínima no momento do bounce para executar swap
SOL_USD_FALLBACK=65                   # fallback de preço SOL/USD se CoinGecko falhar

# Helius RPC (leitura on-chain direta — fill data + detecção de posição fechada)
HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?api-key=xxxx  # HTTP JSON-RPC (diferente do WSS)
# GMGN_WALLET_ADDRESS já configurado acima — reutilizado por helius_rpc.py como WALLET_ADDRESS

# Railway web/worker split
RUN_BOT_SERVICES=0  # web: Flask/Gunicorn apenas, sem scanner
RUN_BOT_SERVICES=1  # worker: scanner + HotMonitor + HeliusWS

# Retenção de dados
RETENTION_TOKEN_TICKS_DAYS=30
RETENTION_MICRO_DAYS=60
RETENTION_TRENDING_DAYS=14
RETENTION_DRY_RUN=true       # simular sem deletar
```

---

## 📊 Dashboards

| URL | Descrição |
|-----|-----------|
| `/scanner` | Tabela de migrações em tempo real (auto-refresh 15s) |
| `/analise` | Backtest exploratório — timing, dump depth, filtros, R:R, cobertura Fase 1 |
| `/analise/h10` | Teste formal H10 — time_to_bottom vs recovery rate (n=425) |
| `/analise/backtest` | Backtester tick-level — grid TP×SL com OHLC. `?mode=ticks` (padrão) ou `?mode=full`. |
| `/analise/trailing` | Trailing TP — distribuição de gains, intra-rally drawdown, simulação 7 configs |
| `/analise/h9` | H9 — token_signals type 12 vs recovery rate por faixa de trigger_mc/mc_migration |
| `/analise/wr-breakdown` | Discriminadores de WR — cross-referencia trades com migrations/bounce_signals/H9. `?since=YYYY-MM-DD` |
| `/analise/dip-entry-sim` | Simulação dip entry — e se esperássemos queda X% do sinal antes de entrar? |
| `/analise/sinal-puro` | WR base por token outcome (entry=mc_at_signal, n=692 bounces) |
| `/analise/post-sl-recovery` | Post-SL Recovery — para cada SL, max MC nos ticks pós-exit |
| `/analise/h22-correlation` | H22 Correlation — absorção pós-fundo vs outcome TP/SL. Gate: n≥30 com Helius. |
| `/analise/pre-rise-ticks` | Análise PRÉ+RISE via token_ticks — `?since=2026-06-12` para isolar pós-RISE gate |
| `/analise/bounce-funnel` | Funil de conversão migrações → bottoms → bounces → trades (n_days do banco real) |
| `/analise/filter-ranges` | rec75 por faixa de threshold para cada filtro principal |
| `/analise/filtros-simples` | Linguagem simples — explica cada filtro, taxa de bloqueio, se está ajudando ou atrapalhando |
| `/analise/filter-impact` | Resumo do impacto dos filtros ativos sobre rec75 e frequência |
| `/analise/grid-search` | Grid search de parâmetros de filtro vs WR/frequência |
| `/analise/quality-grid` | **Grid de filtros de qualidade** — WR real (TP/SL) por combinação de bundler/sniper/rug/bot/top10/smart. Responde: relaxar threshold melhora ou piora WR? Config Railway destacada em azul. `?since=YYYY-MM-DD` |
| `/analise/h9-gate` | **H9 como gate** — Seção 1: trades reais com/sem H9 ativo. Seção 2: simulação sobre todos os bounces com H9 gate + combinações com H10 e H27. Confirma anticorrelação. |
| `/analise/trending-deep` | **Análise profunda de trending** — Cobertura, TP/SL grid 6×4, 6 dimensões internas (smart, elapsed, mig_speed, bounce, drop, pool), combinações, tabela individual por token. `?since=YYYY-MM-DD` |
| `/analise/v2-shadow-live` | V2.1 Shadow Live — gate ativo sobre micro_absorption_snapshots. Dry-run only. |
| `/analise/v2-brain-report` | V2 Brain Report — grid cartesiano. `mode=fast` (padrão) ou `mode=full`. |
| `/debug/signal-live` | Fase 2 — chama get_token_signal(), mostra matches com HotMonitor |
| `/debug/executor-status` | Pipeline de trading — TRADING_ENABLED, HotMonitor ativo, último bounce |
| `/debug/wallet-audit` | Auditoria on-chain — todas as txs da wallet, BUY/SELL, net SOL. `?rpc=HELIUS_URL` |
| `/debug/wallet-tokens` | Forense de posições — ATAs vazios (rent bloqueado), tokens presos |
| `/debug/bounce-list` | Lista bounces ≥ X% com links DexScreener/GMGN. `?bounce=0.50&hours=48`. |
| `/debug/absorption` | H22 Absorption — janela buy/sell ao vivo por token ativo + últimos 30 bounces |
| `/debug/open-trades` | Diagnóstico trades abertos — entry, sl_mc, tp_mc, tick count, raw GMGN query_order |
| `/debug/sell-recovery` | Recuperação blockchain — pagina txs da wallet via Helius, corrige DB. `?dry=1` |
| `/debug/micro-absorption-snapshots` | V2 Fase 1 — visualiza micro_absorption_snapshots. `?format=html\|json\|csv` |
| `/debug/h23-wr` | H23 WR por faixa de net_sol_flow. `?since=2026-05-26` para dados limpos. |
| `/debug/sell-exhaustion` | Sell exhaustion detector — ratio sell_5s/sell_5_15s por token |
| `/debug/test-swap` | Teste manual de swap. `?mint=<mint>&amount=<usd>`. Com TRADING_ENABLED=false simula. |
| `/analise/migration-quality` | H27/H28 — trending_at_migration (pump.fun 1m) + velocidade de migração vs rec75. `?since=YYYY-MM-DD&min_ticks=N` |
| `/export/csv` | Export completo do banco |
| `/health` | Health check |

---

## ⚠️ Notas Críticas de Implementação

1. **bc_progress ≥ 80**: entrada na watchlist + HeliusWS.subscribe(mint) aqui. **complete_timestamp**: sentinel de migrado. Por isso a subscrição Helius já está ativa quando a migração é detectada — sem gap de subscrição.

2. **volume_1m NÃO vem do trenches completed**: trenches retorna volume apenas para near_completion. Para tokens migrados, `volume_1m` em token_ticks é back-fillado via kline 1m no `_precise_bottom_tick()`. Dados anteriores ao fix ficam NULL.

3. **bluechip_owner_pct NÃO está no trenches**: apenas em `/v1/market/rank` (trending). Coluna fica NULL — não remover do schema.

4. **lock_percent NÃO está no trenches**: coletado como `locked_ratio` do token/info na graduação (60min após migração).

5. **is_wash_trading**: campo nativo do trenches, não calcular manualmente.

6. **token/info**: retorna 404 para Meteora DAMM v2 nas primeiras horas. Não confiar como fonte primária de mc.

7. **Kline Meteora DAMM v2**: NÃO retorna dados históricos. Apenas primeiros ~60min após migração. Resolução deve ser `"1m"` (string), NÃO o inteiro 1.

8. **token_signal é feed global**: NÃO é por token — retorna TODOS com smart buys no lookback de 30000s. Filtrar por HotMonitor._tokens no lado do bot. NÃO usar signal_type 14, 15 ou 16 — API retorna 400.

9. **groups é array de objetos**: body correto = `{"chain":"sol","groups":[{"signal_type":[12]}]}`. Array de strings causa erro "invalid json body".

10. **_signal_ban_until**: ban guard no Scanner — cada chamada enquanto banido estende o ban. Checar `time.time() < _signal_ban_until` ANTES de chamar.

11. **token_signals tabela**: `UNIQUE (mint, trigger_at)` — `ON CONFLICT DO NOTHING` evita duplicatas no poll a cada 5s.

12. **pump.fun pAMM** (`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`): DEX real dos tokens migrados. `BuyEvent`/`SellEvent` com discriminador Anchor `sha256("event:<name>")[:8]`. Layout após disc(8): `timestamp(i64)@8 | base_amount@16 | quote_limit@24 | user_base@32 | user_quote@40 | pool_base@48 | pool_quote@56`. Preço = `(pool_quote/1e9) / (pool_base/1e6)`. Não confundir com Meteora CP AMM v2 (`cpamd...`).

13. **rug_ratio vs bundler_trader_amount_rate são campos diferentes**: `token["rug_ratio"]` é score interno GMGN (0-1, tende a ser baixo). `token["bundler_trader_amount_rate"]` é a % real de bundlers. `FILTER_BUNDLER_MAX` checa `bundler_trader_amount_rate`; `FILTER_RUG_MAX` checa `rug_ratio`. Ambos no filtro.

14. **ts_migration usa complete_timestamp on-chain**: `ts_mig_real = datetime.fromtimestamp(complete_timestamp, tz=utc)`. NUNCA usar `datetime.now()`. Token detectado 18min após migração entraria com elapsed_sec≈0, neutralizando A.5 e A.6.

15. **bounce_fired check-and-set atômico**: `with self._lock: if live.get("bounce_fired"): return; live["bounce_fired"] = True`. `push_mc` e `_precise_bottom_tick` são threads concorrentes — sem lock, double-fire é possível mesmo com GIL.

16. **elapsed_sec < 90 em TODOS os loops kline**: candle de transição bonding curve→pool tem `low` com preços pré-migração (da bonding curve, muito mais baixos). Filtrar em `_precise_bottom_tick`, `_graduate`, `_backfill_slope_ticks` e `_run_bottom_truth`. Sem esse filtro, mc_min fica 40-80% abaixo do real.

17. **K_INIT_TOLERANCE = 0.80 hardcoded** em `helius_ws.py`. Cobre pools com k degradado por ~130 trades de fee. Secondary pools (0.83 SOL, k≈1.19e25) continuam bloqueadas abaixo do mínimo com margem segura.

18. **MIN_POOL_SOL=10 para A.9**: pool primária pós-migração mínima absoluta = 15.8 SOL. Secondary pools vistas em produção tinham 0.83 SOL — gap de 46×. `pool_quote_sol` armazenado no deque de eventos; `get_min_mc_in_window()` filtra por `pool_quote_sol >= MIN_POOL_SOL`.

19. **BOUNCE_MAX_PCT guard**: guard correto é `< 9.99` (default = desativado). Guard antigo `< 1.0` significava que `BOUNCE_MAX_PCT=1.50` não tinha efeito nenhum no Railway.

20. **condition_orders price_scale e sell_ratio em percentual (não decimal)**: `price_scale="75"` (TP +75%), `price_scale="15"` (SL -15%), `sell_ratio="100"` (100% da posição). `"1.75"` seria lido como 1.75% — threshold minúsculo. `priority_fee` e `tip_fee` obrigatórios quando `condition_orders` está presente.

21. **query_order: status em `confirmation.state`, não `data.status`**: fill detectado quando `state` é `"processed"` ou `"confirmed"`. `filled_output_amount` retorna consistentemente `null` — usar `helius_rpc.get_swap_fill()` para fill real on-chain.

22. **fill_source coluna em `trades`**: rastreia origem do fill: `'gmgn'`/`'helius'`/`'helius_recovery'`/`'gmgn_recovery'`. `exit_type='helius_sell_recovery'` é o único indicador de dado P&L confiável.

23. **sl_ref = entry_mc_real or mc_at_signal or entry_expected** (C1): `entry_expected = mc_bottom × 1.15` ficava 41% abaixo da entrada real para bounces altos, fazendo o SL threshold nunca ser atingido no TradeMonitor.

24. **Railway web/worker split**: `web` roda Flask/Gunicorn com `RUN_BOT_SERVICES=0`; `worker` roda `worker.py` com `RUN_BOT_SERVICES=1`. Evita que loops do scanner congelem `/analise/*`.

25. **GMGN_SCANNER_API_KEY**: scanner/HotMonitor/TrendingRadar usam key dedicada. Executor usa `GMGN_API_KEY`. Rate limit completamente isolado. Configurar em **web E worker** no Railway.

26. **swap() e query_order() têm priority=True**: bypass do `_wait_global_ban()`. 429 do scanner não atrasa execução do trade.

27. **db._cursor() retorna RealDictCursor**: rows são dicts, não tuplas. Indexar com `r[0]` falha com `KeyError: 0`. Sempre usar `r['colname']`.

28. **Streaming com stream_with_context + padding**: endpoints de longa duração precisam de `Response(stream_with_context(generate()), content_type="text/html")`. Cada `yield` deve incluir `PAD = " " * 2048` para forçar flush em proxies. Sem padding, o browser não mostra nada até completar.

29. **pnl_pct_real é decimal no banco**: `0.75` = 75% de ganho. Display com `f"{pnl:+.1f}%"` mostra `+0.8%`. Sempre multiplicar por 100: `f"{pnl*100:+.1f}%"`.

30. **get_absorption_window() usa window_end_ts=ts_min.timestamp()**: nunca `time.time()`. A janela correta é `[ts_min - 60s → ts_min]` (absorção DO dump). `time.time()` capturaria compras DO bounce (momentum) — métricas completamente diferentes.

31. **H23_NET_MIN gate deployado mas desativado**: código em `scanner.py` verifica se env var existe. Sem `H23_NET_MIN` no Railway = desativado. Adicionar `H23_NET_MIN=0` para bloquear net_flow negativo. Dados atuais: 86% dos bounces têm net<0, n=13 positivos — insuficiente para gate.

32. **RISE gate desativado no Railway**: `RISE_NET_MIN` não está configurado → gate não executa (código só roda se env var existe). Dados históricos (n=61): RISE<0 → rec75=9%, sl25=91%. Para reativar: adicionar `RISE_NET_MIN=0` no Railway. Avaliar via `/analise/pre-rise-ticks?since=2026-06-12`.

33. **GMGN_PRIVATE_KEY é chave de assinatura GMGN, NÃO a chave da wallet Solana**: a GMGN emite uma Ed25519 PEM key separada. A PEM key NÃO deriva o mesmo endereço que GMGN_WALLET_ADDRESS — isso é esperado.

34. **Threading**: heap scheduler — nunca `threading.Timer` por evento. `scheduler.schedule(delay_sec, fn, *args)`.

35. **Global rate limit gate `_global_ban_until`** em `gmgn_client.py`: variável module-level. Qualquer 429 chama `_set_global_ban(reset_at)` → todas as threads bloqueiam. swap/query_order têm `priority=True` e ignoram o ban.

36. **H9 INVERTIDO (confirmado 2026-06-27)**: n=128 bounces + n=50 trades live confirmam SmartDegenBuy ANTES do bounce é sinal negativo. Com H9: WR 6.7%. Sem H9: WR 26.7%. O valor "75% WR n=4" no CLAUDE.md era ruído de amostra pequena. Hipótese: smart money entra no dump, vende no bounce — o bot fica do lado errado. NÃO implementar H9 como gate positivo.

37. **Filtros de qualidade não discriminam WR (confirmado 2026-06-27)**: análise `/analise/quality-grid` com curvas marginais 0→100% mostra teto de WR em ~25.3% independente de combinação de bundler/sniper/rug/bot/top10/smart. Railway `FILTER_BUNDLER_MAX=0.20` é o PIOR ponto da curva (WR 11.1%, n=9). Recomendado: `FILTER_BUNDLER_MAX=0.50`. Sniper/top10 são quase no-ops (maioria NULL no DB).

38. **trending_at_migration é o primeiro sinal com EV positivo (preliminar 2026-06-27)**: cobertura 13% das migrações. WR 30.3%, EV +5.3% (TP75/SL25). TP50/SL25 ótimo: WR 50%, EV +12.5%. mig<60s bloqueia (WR 0%). Dados de apenas 2 dias — aguardar n≥100 bounces trending antes de ativar gate.

---

## 🎯 KPIs de Sucesso

| Fase | Métrica | Meta | Status |
|------|---------|------|--------|
| Coleta | Migrações com bottom + mc_1h | ≥200 | ✅ 425+ (histórico) |
| Coleta | Cobertura campos Fase 1 | ≥80% dos tokens novos | ✅ |
| Análise | H10 validada | delta_pp ≥ 20pp | ✅ +21.8pp |
| Fase A | WR pós-filtros ≥ 25% | ≥25% com 30+ bounces | ⏳ acumulando dados limpos |
| Fase 6-lite | Friction total medida | 53 trades on-chain | ✅ ATA rent ~$0.20/SL |
| Fase 6 | WR real confirmado | ≥20% | ⏳ 29% (dados 2026-05-21 a 2026-05-24, filtros frouxos) |
| Fase 6 | Bot ativo | TRADING_ENABLED=true | ✅ |
| V2 | micro_absorption_snapshots coletando | ≥30 com outcomes GMGN | ⏳ |
| Escala | $1 → $10/trade | WR ≥20% em ≥30 trades | ⏳ |
