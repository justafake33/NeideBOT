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
    ├─ Fase A — filtros adicionais (ordem de aplicação):
    │       ├─ A.2: rejeita bounce_pct 20-30% (dead zone, WR 3.1%)
    │       ├─ A.3: rejeita entry_ratio 50-65% da migração (WR 0%)
    │       ├─ B.3: primeira detecção elegível → backfill retroativo kline + aguarda 5s
    │       └─ A.1: slope pré-fundo ≤ -0.0008 %/s (WR 66.7% vs 0.0%). Fallback se < 3 ticks.
    ├─ One-shot por token (bounce_fired = True após primeiro disparo)
    ├─ Persiste em bounce_signals (UNIQUE mint) + alerta Telegram
    ├─ Chama TradeExecutor.on_bounce() → thread _execute()
    └─ _execute(): A.4 (aguarda 90s + 3 ticks pós-sinal acima entry) →
                   A.6 (cancela se preço atual > entry_expected × 1.20) →
                   swap() com condition_orders (TP+SL atômico)
```

---

## 🔍 ESTADO ATUAL — Coleta de Dados

### Situação (2026-05-05)
- **1175+ tokens** coletados no banco, **1134** com `recovery` definido (baseline correto após fix dead tokens)
- Recovery rate baseline: **23.5%** (não 52% — corrigido após marcar tokens mortos como FALSE)
- **83 bounce signals** disparados em produção — analisados em `/analise/deep-bounce` (12 seções)
- **Bounce detector WR atual: 13.3%** (EV -3% bruto) — filtros Fase A devem elevar para ~29% (estimado)
- **Fase A ATIVA**: slope filter, dead zone, entry ratio, persistência, time-stop 20min
- **Fase B ATIVA**: ts_min bug fix, kline 5min window, backfill retroativo, hot-precise 30s
- **Fase 2 ativa**: sinais type 12 coletados em `token_signals` a cada 5s
- **Fase 6 BLOQUEADA**: `AUTH_SIGNATURE_INVALID` — problema de configuração GMGN (wallet/IP), não de código
- **Cobertura de ticks slope**: 20.5% (17/83) — Fase B deve elevar para ≥50% nos próximos bounces

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
| H13 | Slope íngreme pré-fundo (≤ -0.0008 %/s) → WR de bounce ≥40pp maior que slope gradual | ✅ **+66.7pp** confirmado (66.7% vs 0.0%, n=17 com dados, cobertura 20.5%). Slope avg TP=-0.0035, SL=-0.0003. |
| H14 | bounce_pct entre 20-30% é dead zone — WR inferior a 15-20% e 30%+ | ✅ **confirmado** (3.1% WR vs 18.5% e 21.1%, n=32). Dead-cat zone reforçada com n maior. |
| H15 | Entry entre 50-65% do mc_migration tem WR=0% | ✅ **confirmado** (0/21 = 0% WR, n=21 — amostra maior). Zona morta absoluta. |
| H16 | 3 ticks consecutivos acima do entry pós-sinal → WR ≥20pp maior | ✅ **+18.0pp** confirmado (28.6% vs 10.6%, n=14/66). Efeito menor que n=71 (+24.3pp) mas consistente. |
| H17 | 100% dos TPs ocorrem em ≤20min após o sinal de bounce | ✅ **confirmado** (11/11 TPs em ≤20min, 82% em ≤10min, n=83). Mediana 6.1min, média 7.3min. |
| H18 | Velocidade de subida do fundo ao sinal (bounce velocity) discrimina WR | ❌ **REFUTADA** — todas as tercis têm WR 40% flat. TTB<25min+Slow=43% vs TTB<25min+Fast=29% (contrário do esperado). Não implementar. |
| H19 | Aguardar 1-2min após sinal melhora EV vs entrada imediata | ✅ **+6.6pp WR** com 1min delay (18.8% vs 12.2%). **Coberto pelo A.4** (90s persistence wait). Sem implementação adicional necessária. |
| H20 | W-bottom (double-dip) é sinal de confirmação de suporte | ❌ **NÃO É FILTRO** — 98% dos bounces têm W-bottom, incluindo SLs. Ubíquo demais. Sem W-bottom (n=2) tem 100% WR mas amostra insuficiente. |

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
| slope pré-fundo ≤ -0.0008 | **66.7% vs 0.0% WR** (+66.7pp) | ✅ A.1 implementado | n=17, cobertura 20.5%. Slope avg TP=-0.0035, SL=-0.0003 |
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

> **Por que 70 SLs em <2min:** bounce dispara sinal, preço sobe 15-20%, imediatamente despenca. Não é bounce — é micro-recuperação dentro de downtrend que não terminou. O slope filter (A.1) resolve isso: capitulação real gera spike abrupto de venda antes do fundo. W-bottom nesses casos: o "W" cruza abaixo do SL floor antes de recuperar.

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
Scanner (poll 5s)
    │
    ├─ POST /v1/trenches near_completion → data["pump"]
    │       └─ bc ≥ 80% + filtros qualidade → Watchlist + DB
    │       └─ Campos Fase 1 coletados do trenches (creator_status, cto_flag, etc.)
    │
    ├─ POST /v1/trenches completed → data["completed"]
    │       └─ Filtros MC/liq/fee → upsert_migration
    │       └─ mc_migration = bonding_curve_ref (MIGRATION_SOL_UNITS × SOL/USD) || kline open || trenches
    │       └─ HotMonitor.add()
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
            │       └─ _check_bounce(): dispara bounce_signal se drop≥30% e bounce≥15%
            │               └─ TradeExecutor.on_bounce() → thread _execute() [Fase 6]
            ├─ hot-precise (60s): kline low/high + backfill_tick_volumes()
            └─ _tick (30s): graduação após 60min
                    └─ kline scan 1h completo → mc_bottom, mc_peak, mc_1h
                    └─ token/info sempre chamado: fallback mc_1h + lock_percent (locked_ratio)
                    └─ Fallback final mc_1h: push_mc (mc_last)

TradeExecutor (Fase 6 — ATIVO)
    ├─ on_bounce(): verifica TRADING_ENABLED + count_trades < CALIBRATION_MAX
    ├─ insert_trade(): cria registro com status='pending'
    └─ _execute() [thread própria]:
            ├─ swap(): POST /v1/trade/swap (Ed25519 signed)
            ├─ _wait_fill(): poll query_order até filled/completed (timeout: ORDER_TIMEOUT_SEC)
            └─ create_strategy(): POST /v1/trade/strategy/create com TP/SL

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
| `bounce_signals` | **Fase 2** — um registro por token quando bounce detector dispara (UNIQUE mint) |
| `trades` | **Fase 6** — log completo de cada trade live ($1/trade calibração) |

### HeapScheduler
- Thread única com heap de eventos futuros
- Nunca usar `threading.Timer` por evento (não escala)
- `scheduler.schedule(delay_sec, fn, *args)`

### Fontes de dados por prioridade
| Dado | 1ª fonte | 2ª fonte | 3ª fonte |
|------|----------|----------|----------|
| mc_migration | bonding_curve_ref (MIGRATION_SOL_UNITS × SOL/USD) | kline (open no complete_ts) | trenches usd_market_cap |
| mc_bottom / mc_peak | kline 1m (hot-precise) | push_mc (trenches 5s) | — |
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
FILTER_BOT_MAX=0.50          # máx bot_degen_rate
FILTER_RAT_MAX=0.20          # máx rat_trader_amount_rate
FILTER_TOP10_MAX=0.80        # máx top_10_holder_rate
FILTER_SMART_MIN=1           # mín smart_degen_count
FILTER_WASH_BLOCK=true       # bloquear is_wash_trading
FILTER_MC_MIN=5000           # MC mínimo USD
FILTER_MC_MAX=200000         # MC máximo USD
FILTER_LIQ_MIN=10000         # liquidez mínima USD
FILTER_FEE_MIN=1.0           # fee mínimo em SOL
BC_MIN_WATCHLIST=80          # % BC mínimo para watchlist
MAX_MIG_AGE_SEC=600          # ignorar migrações > 10min no restart
POLL_INTERVAL_SEC=5          # intervalo do scanner
HOT_PRECISE_SEC=30           # intervalo kline hot-precise (B.4: era 60s)
MIGRATION_SOL_UNITS=410      # SOL teórico da bonding curve → mc_migration_bc_ref

# Bounce detector (configuráveis sem redeploy)
BOUNCE_MIN_PCT=0.15          # bounce mínimo do fundo para disparar sinal (15%)
BOUNCE_DROP_MIN=0.30         # drop mínimo da migração para considerar (30%)
BOUNCE_TIME_MAX=20.0         # A.5: janela máxima em minutos (era 30min — 100% TPs em ≤20min)
BOUNCE_SLOPE_MIN=-0.0008     # A.1: slope pré-fundo máximo (mais negativo = mais íngreme = bom)
BOUNCE_SLOPE_MIN_TICKS=3     # A.1: ticks mínimos para ativar o slope filter

# Fase 6 — Execução de trades (configuráveis sem redeploy)
TRADING_ENABLED=false        # true para ativar trades reais
CALIBRATION_AMOUNT_USD=1.0   # tamanho de cada trade em USD
CALIBRATION_MAX_TRADES=50    # limite total de trades de calibração
TRADE_TP_PCT=0.75            # take profit (75%)
TRADE_SL_PCT=0.15            # stop loss (15%)
TRADE_SLIPPAGE_BPS=500       # slippage máximo em basis points (5%)
ORDER_POLL_SEC=2             # intervalo de poll do fill (segundos)
ORDER_TIMEOUT_SEC=30         # timeout do fill (segundos)
PERSISTENCE_WAIT_SEC=90      # A.4: segundos de espera antes de verificar persistência
PERSISTENCE_MIN_TICKS=3      # A.4: ticks pós-sinal acima do entry para confirmar
ENTRY_MAX_OVERSHOOT=0.20     # A.6: cancela se preço atual > entry_expected × 1.20 (compra 20% acima do planejado)
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
| `/debug/signal` | Diagnóstico histórico — testa formatos do token_signal (legacy) |
| `/debug/signal-live` | **Fase 2** — chama get_token_signal(), mostra matches com HotMonitor e últimos sinais salvos |

---

## ⚠️ Notas Críticas de Implementação

1. **Não escalar capital antes de 50 trades de calibração** — friction real (slippage, MEV, latência) só mensurável em live trading
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
48. **slope_backfill_triggered**: flag no state do HotMonitor. Na primeira detecção de bounce elegível (A.2/A.3 passam), dispara `_backfill_slope_ticks()` em thread própria e retorna sem disparar o sinal. No segundo poll (5s depois), slope filter roda com dados completos do kline retroativo.
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
| Fase 6-lite | Friction total medida | 50 trades de $1 | 🟡 20/50 — WR 25% (5 TP, 15 SL) |
| Fase 6-lite | EV_real > 0 após friction | EV_real > 0 | ⏳ acumulando |
| Fase 6 | Win rate live | ≥20% (break-even = 11% com payoff 8.7x) | 🟡 25% (n=20) |
| Fase 6 | Payoff ratio live | ≥3× | ⏳ |
| Fase 6 | Latência detecção → ordem | ≤15s | ⏳ |
| Fase 6 | Divergência real vs backtest | ≤30% após 50 trades | ⏳ |
| Fase 6 | Max drawdown | ≤40% do capital alocado | ⏳ |
| H9 | Validação smart money signal | delta_pp ≥ 20pp | 🟡 +11.8pp na faixa <50% (n=14) — reverse causality em >120%. Threshold correto: <80% migração |

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
