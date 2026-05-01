# NeideBOT — Contexto do Projeto

## O que é
Bot autônomo de trading em Solana.
Estratégia central: detectar tokens recém-migrados (Pump.fun/Meteora DAMM v2), aguardar o dump pós-migração e comprar o fundo com filtros de qualidade.

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
| `/v1/trade/swap` | POST | Execução de swap (Fase 2+) |
| `/v1/trade/strategy/create` | POST | TP/SL automático (Fase 2+) |
| `/v1/trade/query_order` | GET | Status de ordem (Fase 2+) |

### Rate limits (leaky bucket, capacity 10)
| Endpoint | Peso | Taxa sustentada |
|----------|------|-----------------|
| trenches | 3 | ~3.3 req/s |
| token info/security | 1 | ~10 req/s |
| kline | 2 | ~5 req/s |
| swap/strategy | 1 | ~10 req/s |

> ⚠️ IPv4 only — GMGN API não aceita IPv6. Verificar no Railway.
> ⚠️ 429 → ler `reset_at` do body e aguardar. Retry imediato estende ban em 5s.

### Mapeamento de campos confirmado

```python
# POST /v1/trenches — body example
{
  "chain": "sol",
  "type": ["near_completion", "completed"],
  "filters": {}
}

# Response keys:
# near_completion → data["pump"]      (NÃO data["near_completion"]!)
# completed       → data["completed"]
# new_creation    → data["new_creation"]

# Campos por token no trenches:
token["address"]                  # mint
token["symbol"]                   # nome
token["smart_degen_count"]        # smart wallets
token["renowned_count"]           # KOLs
token["sniper_count"]             # sniper wallets
token["rug_ratio"]                # risco rug (0-1)
token["bundler_trader_amount_rate"]  # % bundlers (usar para rug_ratio no DB)
token["rat_trader_amount_rate"]   # % de rat traders (filtro)
token["is_wash_trading"]          # bool — wash trading detectado pelo GMGN
token["top_10_holder_rate"]       # concentração top 10
token["bc_progress"]              # % da bonding curve (0-99)
token["usd_market_cap"]           # MC em USD (preferir sobre market_cap)
token["market_cap"]               # fallback de MC
token["liquidity"]                # liquidez total (NÃO dividir por 2 no trenches)
token["total_fee"]                # fee total em SOL (filtro de volume real)
token["complete_timestamp"]       # unix timestamp da migração
token["created_timestamp"]        # unix timestamp de criação do token
token["complete_cost_time"]       # segundos do token criado até migrar
token["total_supply"]             # supply total
token["exchange"]                 # exchange de destino (Raydium, Meteora, etc.)

# GET /v1/token/info/sol/{mint} → body["data"]
# ⚠️ Retorna 404 para tokens Meteora DAMM v2 nas primeiras horas após migração
raw = body["data"]
mc  = float(raw["price"]) * float(raw["circulating_supply"])
liq = float(raw["liquidity"]) / 2   # double-sided → ÷2
wts = raw.get("wallet_tags_stat") or {}
smart_wallets    = wts.get("smart_wallets")
renowned_wallets = wts.get("renowned_wallets")
sniper_wallets   = wts.get("sniper_wallets")
stat = raw.get("stat") or {}
rug_ratio         = float(stat.get("top_bundler_trader_percentage") or 0)
volume_5m         = float(stat.get("volume_5m") or 0)
top10_holder_rate = float(stat.get("top_10_holder_rate") or 0)
holders_count = int(raw.get("holder_count") or 0)
is_honeypot   = raw.get("is_honeypot")
bc_progress   = raw.get("bc_progress")

# GET /v1/market/token_kline
# ⚠️ Meteora DAMM v2: retorna dados APENAS para os primeiros ~60min após migração.
# Não funciona para consultas históricas (4h, 24h atrás).
# Resolução deve ser "1m" (string), NÃO o inteiro 1.
params = {
  "chain": "sol",
  "address": mint,
  "resolution": "1m",
  "from": from_ts,   # unix timestamp
  "to": to_ts,       # unix timestamp
}
# Response: lista de candles com campos: time (ms), open, high, low, close, volume
```

---

## 🎯 ESTRATÉGIA CENTRAL — Post-Migration Dip

### O Padrão
Quando um token completa a bonding curve no Pump.fun, ele migra para Raydium ou Meteora DAMM v2.
Nesse momento os early buyers realizam lucro → dump imediato.
Se o token tem fundamentos reais, esse dump é temporário e o preço recupera.

### Pipeline de execução (Fase 2+)
```
ESTÁGIO 1 — Watchlist (bc_progress ≥ 80%)
    │
    ├─ POST /v1/trenches type=near_completion → data["pump"]
    ├─ Filtros aplicados (rug, rat, top10, smart, wash)
    └─ Token entra na watchlist com dados de qualidade

ESTÁGIO 2 — Migração detectada
    │
    ├─ POST /v1/trenches type=completed → data["completed"]
    ├─ Filtros de MC ($5k-$200k), liquidez (≥$10k), fee (≥1 SOL)
    ├─ MC preciso via kline no complete_timestamp (fallback: trenches)
    ├─ Token entra no HotMonitor por 60 minutos
    │
    ├─ HotMonitor rastreia fundo (mc_min) e pico (mc_max) a cada 5s
    ├─ kline preciso a cada 60s (hot-precise thread)
    │
    └─ Checkpoints: 1h (graduação) → 4h → 24h

FASE 2 — Execução (BOT_ATIVO=true)
    ├─ Aguarda dump ≥ DUMP_THRESHOLD% do mc_migration
    ├─ Aplica filtros de qualidade
    ├─ POST /v1/trade/swap
    └─ TP/SL via POST /v1/trade/strategy/create
```

---

## 🔍 FASE 1 — Coleta de Dados (em andamento)

### Estado atual (2026-05-01)
- **790 tokens** coletados no banco
- **719** com `mc_bottom` (91%)
- **341** com `mc_1h` (43%)
- **0** com `mc_4h` / `mc_24h` — fix deployado em 2026-05-01
- `recovery` calculado para 341 tokens

### Por que mc_4h = 0?
O kline do GMGN **não retorna dados históricos** para tokens Meteora DAMM v2 — funciona apenas para os primeiros ~60min após migração. Após isso, a única fonte confiável é o `trenches` poll (5s). A solução implementada:
1. `_mc_live`: dict que captura o último MC do trenches para todos os tokens vistos
2. Fallback em `_checkpoint`: kline (estreito) → kline (range completo) → token/info → `_mc_live`
3. Para checkpoints futuros, `_mc_live` tem o último valor do trenches (atualizado a cada 5s)

### Campos coletados por migração

| Campo | Fonte | Descrição |
|-------|-------|-----------|
| mint | trenches | Endereço do token |
| token_nome | trenches | Símbolo |
| ts_detected | Sistema | Timestamp watchlist (bc ≥ 80%) |
| bc_progress_at_detection | trenches | % BC quando detectado |
| ts_migration | Sistema | Timestamp da migração |
| mc_migration | kline ou trenches | MC no momento da migração |
| mc_peak | HotMonitor | MC máximo na janela quente |
| time_to_peak | HotMonitor | Minutos até o pico |
| pct_gain | Calculado | (mc_peak - mc_migration) / mc_migration |
| mc_bottom | HotMonitor | MC mínimo na janela quente |
| time_to_bottom | HotMonitor | Minutos até o fundo |
| pct_drop | Calculado | (mc_migration - mc_bottom) / mc_migration |
| mc_1h | Checkpoint | MC 1h após migração |
| var_1h | Calculado | % vs mc_migration |
| mc_4h | Checkpoint | MC 4h após migração |
| var_4h | Calculado | % vs mc_migration |
| mc_24h | Checkpoint | MC 24h após migração |
| var_24h | Calculado | % vs mc_migration |
| smart_wallets | trenches | Smart wallets (smart_degen_count) |
| renowned_wallets | trenches | KOL wallets |
| sniper_wallets | trenches | Sniper wallets |
| rug_ratio | trenches | bundler_trader_amount_rate |
| top10_holder_rate | trenches | Concentração top 10 holders |
| holders_count | trenches | Total de holders |
| volume_1m/5m/15m | trenches | Volume por janela |
| is_wash_trading | trenches | Campo nativo GMGN |
| liq_migration | trenches | Liquidez no momento da migração |
| is_honeypot | trenches | Flag de segurança |
| recovery | Calculado | mc_1h > mc_bottom × 1.3 |
| token_created_at | trenches | Timestamp de criação |
| time_to_migrate_sec | trenches | Segundos criação → migração |
| capture_delay_sec | Sistema | Segundos entre migração e captura |

---

## 📐 HIPÓTESES FORMAIS
Registradas ANTES de ver os dados. Não alterar retroativamente.

**H1** — ≥70% dos tokens caem ≥20% do ATH nos primeiros 60min.
**H2** — Em tokens com recovery=TRUE, o fundo ocorre em ≤15min.
**H3** — Tokens com smart_wallets ≥3 têm recovery rate ≥ 15pp maior que smart<3.
**H4** — Tokens com sniper_wallets/holders ≥30% têm recovery rate < 30%.
**H5** — Tokens com top10_holder_rate ≥0.80 têm var_24h < -80% em ≥60% dos casos.
**H6** — Entradas entre -25% e -45% do mc_migration têm melhor risco/retorno em var_4h e var_24h.
**H7** — Tokens com liq_migration < $5.000 geram slippage inaceitável para posições > $50.
**H8** — Tokens com is_wash_trading=TRUE têm recovery rate ≥20pp menor que FALSE.

---

## 🏗️ ARQUITETURA ATUAL (Fase 1)

```
Scanner (poll 5s)
    │
    ├─ POST /v1/trenches near_completion → data["pump"]
    │       └─ bc ≥ 80% + filtros qualidade → Watchlist + DB
    │
    ├─ POST /v1/trenches completed → data["completed"]
    │       └─ Filtros MC/liq/fee → upsert_migration
    │       └─ mc_migration via kline (fallback: trenches)
    │       └─ HotMonitor.add()
    │       └─ push_mc para TODOS os completed já vistos (atualiza _mc_live)
    │
    └─ HotMonitor (janela 60min)
            ├─ push_mc (5s): rastreia mc_min, mc_max, mc_last, _mc_live
            ├─ hot-precise (60s): kline low/high para precisão
            ├─ _tick (30s): graduação após 60min
            │       └─ kline scan 1h completo → mc_bottom, mc_peak, mc_1h
            │       └─ Fallbacks 1h: token/info → push_mc (mc_last)
            │       └─ Agenda checkpoints 4h e 24h no HeapScheduler
            │
            ├─ _checkpoint(4h/24h):
            │       └─ kline estreito (±2min) → kline completo (desde mig) → token/info → _mc_live
            │
            └─ startup_backfill (ao reiniciar):
                    └─ Tokens na janela: re-add ao _tokens
                    └─ Tokens passados: agendamento/execução imediata de checkpoints ausentes
```

### HeapScheduler
- Thread única com heap de eventos futuros
- Nunca usar `threading.Timer` por evento (não escala)
- `scheduler.schedule(delay_sec, fn, *args)`

### Fontes de dados por prioridade
| Dado | 1ª fonte | 2ª fonte | 3ª fonte | 4ª fonte |
|------|----------|----------|----------|-----------|
| mc_migration | kline (open no complete_ts) | trenches usd_market_cap | — | — |
| mc_bottom / mc_peak | kline 1m (hot-precise) | push_mc (trenches 5s) | — | — |
| mc_1h | kline (ts_mig a ts_mig+3660) | token/info | push_mc (mc_last) | — |
| mc_4h / mc_24h | kline estreito | kline completo | token/info | **_mc_live** |

---

## 🔧 Variáveis de Ambiente (Railway)

```
DATABASE_URL=postgres://...
GMGN_API_KEY=xxxx
GMGN_PRIVATE_KEY=xxxx        # Ed25519 PEM — necessário na Fase 2
GMGN_HOST=https://openapi.gmgn.ai
TELEGRAM_TOKEN=xxxx
TELEGRAM_CHAT=xxxx
SENTRY_DSN=xxxx              # DSN padrão (não o obsoleto)

# Filtros do Scanner (configuráveis sem redeploy)
FILTER_RUG_MAX=0.15          # máx bundler_trader_amount_rate
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
HOT_PRECISE_SEC=60           # intervalo kline hot-precise
```

---

## 📊 Dashboard `/scanner`

URL: `/scanner` (auto-refresh 15s)
Export CSV: `/export/csv`
Health: `/health`

**Colunas:** Data BRT | Token | BC% | T/Migração | MC Mig | Pico | Ganho | T/Pico | Fundo | Queda | T/Fundo | 1h | 4h | 24h | Smart | Sniper | Rug% | Top10 | Wash | HP | Recovery

Mostra até 2000 linhas. Horário BRT (UTC-3). MC em USD formatado.

---

## ⚠️ Notas Críticas de Implementação

1. **Não executar capital antes do backtest** — mínimo 200 migrações com dados completos
2. **Threading**: heap scheduler — nunca `threading.Timer` por evento
3. **GMGN rate limit**: trenches peso 3, kline peso 2, token info peso 1. 429 → ler `reset_at` do body
4. **data["pump"]**: near_completion retorna nessa chave, não "near_completion"
5. **bc_progress ≥ 80**: entrada na watchlist | **complete_timestamp**: sentinel de migrado
6. **Timestamps em UTC** no banco — converter para BRT apenas na exibição
7. **Kline Meteora DAMM v2**: NÃO retorna dados históricos. Fallback obrigatório: `_mc_live`
8. **token/info**: retorna 404 para Meteora DAMM v2 nas primeiras horas. Não confiar como fonte primária
9. **Token morto**: mc cair ≥80% do mc_migration → `cancel()` no HotMonitor
10. **IPv4 only**: GMGN API não aceita IPv6
11. **is_wash_trading**: campo nativo do trenches, não calcular manualmente
12. **logging.basicConfig** deve ser chamado ANTES de `init_sentry()` para logs aparecerem
13. **_mc_live**: captura MC de TODOS os completed já vistos (sem filtro de range) — fonte para checkpoints
14. **startup_backfill**: janela de 26h (24h + 2h buffer) para recuperar checkpoints após restart

---

## 📋 ROADMAP

### ✅ Fase 0 — Infraestrutura (concluído)
- [x] Cliente GMGN (X-APIKEY, timestamp, client_id, rate limit)
- [x] Scanner: trenches near_completion → Watchlist
- [x] Scanner: trenches completed → HotMonitor
- [x] HeapScheduler (thread única, heap de eventos)
- [x] DB PostgreSQL com schema completo
- [x] Dashboard `/scanner` com auto-refresh
- [x] Telegram alertas de migração
- [x] Sentry (DSN padrão, LoggingIntegration, env Railway)
- [x] Export CSV `/export/csv`

### 🔄 Fase 1 — Coleta de Dados (em andamento)
**Objetivo:** ≥200 migrações com mc_bottom + mc_1h + mc_4h + mc_24h preenchidos

**Estado:** 790 tokens, 341 com mc_1h, 0 com mc_4h (fix deployado 2026-05-01)

**Pendente:**
- [ ] Confirmar checkpoints 4h/24h funcionando via `_mc_live` (verificar logs após deploy)
- [ ] Atingir ≥200 tokens com todos os checkpoints completos
- [ ] Rodar query de qualidade dos dados:
  ```sql
  SELECT COUNT(*) FILTER (WHERE mc_bottom IS NOT NULL
    AND mc_1h IS NOT NULL AND mc_4h IS NOT NULL AND mc_24h IS NOT NULL) AS completos
  FROM migrations WHERE ts_migration IS NOT NULL;
  ```

### 📊 Fase 2 — Backtest Walk-Forward
**Pré-requisito:** ≥200 migrações completas

**Tarefas:**
- [ ] Split 70/30 in-sample / out-of-sample (por data)
- [ ] Testar H1–H8 individualmente
- [ ] Identificar combinações de filtros com melhor win rate
- [ ] Definir DUMP_THRESHOLD ótimo (H6)
- [ ] Calcular payoff ratio por combinação de filtros
- [ ] Documentar resultados ANTES de tocar no executor

**Query base para backtest:**
```sql
SELECT
  pct_drop, smart_wallets, sniper_wallets, top10_holder_rate,
  rug_ratio, is_wash_trading, liq_migration,
  var_1h, var_4h, var_24h, recovery,
  time_to_bottom, time_to_peak,
  token_created_at, time_to_migrate_sec
FROM migrations
WHERE mc_migration IS NOT NULL
  AND mc_bottom IS NOT NULL
  AND mc_1h IS NOT NULL
ORDER BY ts_migration ASC;
```

### 🤖 Fase 3 — Scoring de Qualidade (ML leve)
**Pré-requisito:** Backtest validado

**Tarefas:**
- [ ] Feature engineering: score composto de qualidade (0-100)
  - smart_wallets (peso alto)
  - is_wash_trading (peso alto negativo)
  - rug_ratio (peso negativo)
  - time_to_migrate_sec (tokens que demoram mais tendem a ser mais sólidos)
  - top10_holder_rate (concentração)
- [ ] Modelo simples: logistic regression ou threshold scoring
- [ ] Validar score out-of-sample antes de usar em produção

### 💰 Fase 4 — Execução com Capital Mínimo
**Pré-requisito:** win rate out-of-sample ≥ 50%, payoff ≥ 2×

**Tarefas:**
- [ ] Implementar POST /v1/trade/swap
- [ ] Implementar POST /v1/trade/strategy/create (TP/SL)
- [ ] `BOT_ATIVO=true`, `BOT_VALOR_USD=10`
- [ ] Dashboard: colunas "Comprado" e "Resultado" ativas
- [ ] 50 trades reais como validação final
- [ ] Latência alvo: detecção → ordem < 15s

### 📈 Fase 5 — Otimização e Escala
- [ ] Walk-forward contínuo (re-treino mensal)
- [ ] Ajuste dinâmico de posição por confiança do score
- [ ] Alertas Telegram para trades executados e resultados
- [ ] Painel de P&L em tempo real no dashboard

---

## 🎯 KPIs de Sucesso

| Fase | Métrica | Meta |
|------|---------|------|
| Fase 1 | Migrações com dados completos | ≥200 |
| Fase 1 | Campos completos | mc_migration, mc_bottom, mc_1h, mc_4h, mc_24h |
| Fase 2 | Win rate out-of-sample | ≥50% |
| Fase 2 | Payoff ratio | ≥2× |
| Fase 2 | Max drawdown simulado | ≤40% |
| Fase 4 | Latência detecção → ordem | <15s |
| Fase 4 | Divergência real vs backtest | <30% após 50 trades |

---

## 🧠 Metodologia — Big Player

1. Hipóteses documentadas ANTES de ver os dados
2. Coleta de dados pura (sem viés de confirmação)
3. Backtest walk-forward (in-sample / out-of-sample) — nunca validar no mesmo conjunto
4. Filtros construídos sobre o backtest, não sobre intuição
5. Deploy com capital mínimo como validação final

> Sardinhas reagem após a migração. Big players já estão posicionados na Watchlist desde os 80%.
> Sardinhas olham volume. Big players olham `is_wash_trading`.
> Sardinhas seguem intuição. Big players seguem dados out-of-sample.
