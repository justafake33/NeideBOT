# NeideBOT — Contexto do Projeto

## O que é
Bot autônomo de trading em Solana.
Estratégia central: detectar tokens recém-migrados (Pump.fun → Raydium), aguardar o dump pós-migração e comprar o fundo com filtros de qualidade.

Completamente independente do wallet-monitor/copy trade.

---

## Stack
- Python + Flask + PostgreSQL (Railway)
- GMGN OpenAPI — discovery, token info, execução
- Telegram Bot — alertas e controle
- Deploy: **Railway**

---

## 🔌 GMGN API — Referência Completa

### Configuração
Base URL: https://openapi.gmgn.ai
Auth: header X-APIKEY + query params timestamp e client_id
Swap/orders: adiciona header X-Signature (Ed25519 sobre subpath+params+body+timestamp)

### Endpoints confirmados
| Endpoint | Método | Uso |
|----------|--------|-----|
| `/v1/trenches` | POST | Watchlist (near_completion → data.pump) e migrados (completed → data.completed) |
| `/v1/token/info/{chain}/{mint}` | GET | Dados completos do token |
| `/v1/token/security/{chain}/{mint}` | GET | Segurança: honeypot, rug_ratio, tax, holder concentration |
| `/v1/market/rank` | GET | Trending tokens |
| `/v1/market/token_kline` | GET | Candlestick OHLCV |
| `/v1/trade/swap` | POST | Execução de swap |
| `/v1/trade/strategy/create` | POST | TP/SL automático (condition orders) |
| `/v1/trade/query_order` | GET | Status de ordem |
### Rate limits (leaky bucket, capacity 10)
| Endpoint | Peso | Taxa sustentada |
|----------|------|-----------------|
| trenches | 3 | ~3.3 req/s |
| token info/security | 1 | ~10 req/s |
| swap/strategy | 1 | ~10 req/s |
> ⚠️ IPv4 only — verificar no Railway (IPv6 gera 401/403)
### Mapeamento de campos confirmado
```python
# POST /v1/trenches — body example
{
  "chain": "sol",
  "type": ["near_completion", "completed"],
  "filters": {
    "min_smart_degen_count": 1,
    "max_rug_ratio": 0.3
  }
}
# Response keys:
# near_completion → data["pump"]     (NÃO data["near_completion"]!)
# completed       → data["completed"]
# new_creation    → data["new_creation"]
# Campos por token no trenches:
token["address"]            # mint
token["symbol"]             # nome
token["smart_degen_count"]  # smart wallets
token["renowned_count"]     # KOLs
token["rug_ratio"]          # risco rug (0-1)
token["is_wash_trading"]    # bool — wash trading detectado pelo GMGN
token["bundler_rate"]       # % de supply em bundlers
token["rat_trader_amount_rate"]  # % de rat traders
token["volume_1h"]          # volume 1h em USD
token["bc_progress"]        # % da bonding curve (0-99)
# GET /v1/token/info/sol/{mint} → body["data"]
raw = body["data"]
mc  = float(raw["price"]) * float(raw["circulating_supply"])
liq = float(raw["liquidity"]) / 2   # sempre double-sided → ÷2
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
🎯 ESTRATÉGIA CENTRAL — Post-Migration Dip
O Padrão
Quando um token completa a bonding curve no Pump.fun, ele migra para Raydium.
Nesse momento os early buyers realizam lucro → dump imediato.
Se o token tem fundamentos reais, esse dump é temporário e o preço recupera.

Pipeline de execução
ESTÁGIO 1 — Watchlist (bc_progress ≥ 90%)
    │
    ├─ POST /v1/trenches type=near_completion → data["pump"]
    ├─ Token entra no radar
    ├─ PRÉ-COLETA dados de qualidade (token info + security)
    │   (smart_wallets, sniper_ratio, top10, holders, rug_ratio,
    │    is_wash_trading, volume_1m, volume_5m, volume_15m)
    ├─ Já sabe se passa nos filtros ANTES da migração
    └─ Aguarda gatilho de migração (bc_progress = 99)
ESTÁGIO 2 — Migração detectada (bc_progress = 99)
    │
    ├─ POST /v1/trenches type=completed → data["completed"]
    │   OU polling do token até bc_progress = 99
    ├─ Já tem dados → zero latência de coleta
    ├─ Registra mc_migration (= ATH do momento)
    ├─ Monitora evolução de preço pós-migração
    │
    ├─ Queda ≥ DUMP_THRESHOLD% do ATH de migração?
    │       └─ NÃO → continua monitorando (timeout: MAX_MONITOR_MINUTOS)
    │       └─ SIM → aplica filtros de qualidade
    │
    ├─ Filtros passaram?
    │       └─ NÃO → descarta
    │       └─ SIM → executa via POST /v1/trade/swap
    │
    └─ TP/SL via POST /v1/trade/strategy/create (condition_orders)
🔍 FASE 1 — Coleta de Dados (ANTES de executar capital)
Objetivo
Responder empiricamente às hipóteses antes de construir o executor.
Mínimo: 200 migrações observadas com dados completos.

O que coletar por migração
Campo	Fonte	Descrição
mint	trenches	Endereço do token
ts_detected	Sistema	Timestamp de entrada na watchlist (bc ≥ 90%)
bc_progress_at_detection	trenches	% da BC quando detectado
ts_migration	Sistema	Timestamp da migração (bc = 99)
mc_migration	token info	MC no momento da migração (ATH)
mc_bottom	Monitoramento	MC mínimo nos primeiros 30min
time_to_bottom_min	Calculado	Minutos da migração até o fundo
pct_drop	Calculado	(mc_migration - mc_bottom) / mc_migration
mc_1h	Checkpoint	MC 1h após migração
mc_4h	Checkpoint	MC 4h após migração
mc_24h	Checkpoint	MC 24h após migração
var_1h	Calculado	Variação % vs mc_migration
var_4h	Calculado	Variação % vs mc_migration
var_24h	Calculado	Variação % vs mc_migration
smart_wallets	token info	Smart wallets holding
renowned_wallets	token info	KOL wallets
sniper_wallets	token info	Sniper wallets
rug_ratio	token info	stat.top_bundler_trader_percentage
top10_holder_rate	token info	stat.top_10_holder_rate
holders_count	token info	raw.holder_count
volume_1m	token info	stat.volume_1m
volume_5m	token info	stat.volume_5m
volume_15m	token info	stat.volume_15m
is_wash_trading	trenches	Campo nativo GMGN — wash trading detectado
liq_migration	token info	liquidity / 2 (single-sided)
is_honeypot	token security	Flag de segurança
recovery	Calculado	Bool: mc_1h > mc_bottom × 1.3
Tabela migrations
CREATE TABLE migrations (
    id                        SERIAL PRIMARY KEY,
    mint                      TEXT UNIQUE,
    token_nome                TEXT,
    ts_detected               TIMESTAMP,
    bc_progress_at_detection  FLOAT,
    ts_migration              TIMESTAMP,
    mc_migration              FLOAT,
    mc_bottom                 FLOAT,
    time_to_bottom            FLOAT,
    pct_drop                  FLOAT,
    mc_1h                     FLOAT,  var_1h  FLOAT,
    mc_4h                     FLOAT,  var_4h  FLOAT,
    mc_24h                    FLOAT,  var_24h FLOAT,
    smart_wallets             INT,
    renowned_wallets          INT,
    sniper_wallets            INT,
    rug_ratio                 FLOAT,
    top10_holder_rate         FLOAT,
    holders_count             INT,
    volume_1m                 FLOAT,
    volume_5m                 FLOAT,
    volume_15m                FLOAT,
    is_wash_trading           BOOL,
    liq_migration             FLOAT,
    is_honeypot               BOOL,
    recovery                  BOOL,
    comprado                  BOOL DEFAULT FALSE,
    resultado_pct             FLOAT
);
📐 HIPÓTESES FORMAIS
Registradas ANTES de ver os dados. Não alterar retroativamente.

H1 — Existe um dump sistemático pós-migração
Hipótese: ≥70% dos tokens caem ≥20% do ATH de migração nos primeiros 30min.
Critério: proporção ≥70% com ≥200 migrações observadas.

H2 — O fundo ocorre dentro de uma janela previsível
Hipótese: em tokens com recovery=TRUE, o fundo ocorre em ≤15min após a migração.
Critério: mediana de time_to_bottom ≤15min no subgrupo recovery=TRUE.

H3 — Smart money prediz recovery
Hipótese: tokens com smart_wallets ≥3 têm recovery rate significativamente maior.
Evidência de suporte: wallet-monitor → SD≥3: +12.1% médio vs SD=0: -14.8%.
Critério: recovery rate SD≥3 ≥ recovery rate SD<3 + 15pp com ≥50 tokens em cada grupo.

H4 — Sniper ratio alto prediz não-recuperação
Hipótese: tokens com sniper_wallets/holders_count ≥ 30% não recuperam o dump.
Critério: recovery rate sniper≥30% < 30% com ≥40 tokens nessa faixa.

H5 — Concentração de holders prediz rug
Hipótese: tokens com top10_holder_rate ≥ 0.80 têm rug rate ≥ 60%.
Critério: proporção de var_24h < -0.80 ≥ 60% nesse grupo.

H6 — Existe uma janela ótima de entrada (% de queda)
Hipótese: entradas entre -25% e -45% do mc_migration têm melhor risco/retorno.
Critério: retorno médio var_4h e var_24h é máximo para pct_drop entre 25%–45%.

H7 — Liquidez mínima é necessária para executabilidade
Hipótese: tokens com liq_migration < $5.000 geram slippage inaceitável para posições > $50.
Critério: verificar empiricamente via simulação.

H8 — Wash trading discrimina não-recuperação
Hipótese: tokens com is_wash_trading=TRUE têm recovery rate significativamente menor.
Lógica: volume fabricado com carteiras próprias → sell pressure artificial pós-migração. GMGN já detecta nativamente via fee/volume ratio interno.
Critério: recovery rate is_wash_trading=TRUE < recovery rate is_wash_trading=FALSE − 20pp com ≥40 tokens em cada grupo.

🏗️ ARQUITETURA (Fase 1 — Coleta pura, sem execução)
Scanner (polling a cada 30s)
    │
    ├─ POST /v1/trenches type=near_completion → data["pump"]
    │       └─ Tokens com bc_progress ≥ 90% → Watchlist
    │       └─ Pré-coleta: GET /v1/token/info + /v1/token/security
    │
    ├─ POST /v1/trenches type=completed → data["completed"]
    │       └─ Novos tokens migrados → promove da Watchlist (ou novo)
    │       └─ Registra mc_migration
    │
    └─ Dispatcher (heap scheduler — 1 thread)
            ├─ Timer +5min  → GET token info → registra mc atual
            ├─ Timer +15min → GET token info → registra mc atual
            ├─ Timer +30min → GET token info → registra mc atual
            ├─ Timer +1h    → GET token info → registra mc_1h
            ├─ Timer +4h    → GET token info → registra mc_4h
            └─ Timer +24h   → GET token info → registra mc_24h + calcula recovery
🔧 Variáveis de Ambiente (Railway)
DATABASE_URL=postgres://...
GMGN_API_KEY=xxxx
GMGN_PRIVATE_KEY=xxxx        # Ed25519 PEM — só necessário na Fase 2
GMGN_HOST=https://openapi.gmgn.ai
TELEGRAM_TOKEN=xxxx
TELEGRAM_CHAT=xxxx
# Parâmetros de estratégia
DUMP_THRESHOLD=0.30
MAX_MONITOR_MINUTOS=30
SMART_WALLETS_MIN=3
SNIPER_MAX_PCT=0.30
TOP10_MAX=0.80
LIQ_MIN_USD=5000
BOT_ATIVO=false
BOT_VALOR_USD=10
📊 Dashboard /scanner
Colunas: Data | Token | BC% detec | MC migração | Fundo | Queda% | T/fundo | 1h | 4h | 24h | SmDegen | Sniper | Rug% | Top10 | WashTrade | Recovery | Comprado | Resultado

Horário: BRT (UTC-3). MC em USD formatado.

⚠️ Notas Críticas de Implementação
Não executar capital antes do backtest — mínimo 200 migrações
Threading: heap scheduler — nunca threading.Timer por evento
GMGN rate limit: trenches peso 3 (~3.3 req/s), token info peso 1 (~10 req/s). Stagger 2–3s entre chamadas em lote. 429 → ler reset_at do body, não fazer retry imediato (estende ban 5s)
data["pump"]: near_completion retorna nessa chave, não "near_completion"
bc_progress = 99: sentinel de migrado
bc_progress ≥ 90: sentinel de entrada na Watchlist
Timestamps em UTC no banco — converter para BRT apenas na exibição
Liquidez double-sided: GMGN retorna total → usar ÷ 2
Token morto: mc cair ≥80% do mc_migration antes de 1h → cancelar timers (rugged)
IPv4 only: GMGN API não aceita IPv6 — verificar configuração Railway
is_wash_trading: campo nativo do trenches, não calcular manualmente
📋 Fases de Desenvolvimento
Fase 1 — Scanner + Coleta (agora)
não concluído
Implementar cliente GMGN (X-APIKEY, timestamp, client_id)
não concluído
Scanner: POST /v1/trenches near_completion → Watchlist
não concluído
Scanner: POST /v1/trenches completed → Monitor Ativo
não concluído
Pré-coleta: GET /v1/token/info + /v1/token/security por token na Watchlist
não concluído
Heap scheduler para checkpoints de MC (5min, 15min, 30min, 1h, 4h, 24h)
não concluído
Dashboard /scanner
não concluído
Telegram alertas
não concluído
Coletar ≥200 migrações com dados completos
Fase 2 — Backtest Walk-Forward
Pré-requisito: ≥200 migrações com mc_bottom, time_to_bottom, mc_1h, mc_4h, mc_24h
Split 70/30 in-sample / out-of-sample
Testar H1–H8 isoladas e combinadas
Definir thresholds ótimos via dados
Fase 3 — Execução com Capital Mínimo
Pré-requisito: backtest validado (win rate ≥ 50%, payoff ≥ 2×)
POST /v1/trade/swap + POST /v1/trade/strategy/create (TP/SL)
BOT_ATIVO=true com BOT_VALOR_USD=$5–10
50 trades reais como validação final
🎯 KPIs de Sucesso
Fase 1
Métrica	Meta
Migrações observadas	≥200
Campos completos	mc_migration, mc_bottom, time_to_bottom, mc_1h, mc_4h, mc_24h
Recovery rate baseline	documentar sem filtrar
Fase 2
Métrica	Critério
Win rate out-of-sample	≥ 50%
Payoff ratio	≥ 2×
Max drawdown	≤ 40%
Fase 3
Métrica	Meta
Latência detecção → ordem	< 15s
Divergência real vs backtest	< 30% após 50 trades
🧠 Metodologia — Big Player
Hipóteses documentadas ANTES de ver os dados
Coleta de dados
Backtest walk-forward (in-sample / out-of-sample)
Filtros construídos sobre o backtest, não sobre intuição
Deploy com capital mínimo como validação final
Mentalidade Big Player em cada decisão:

Sardinhas reagem após a migração. Big players já estão posicionados na Watchlist desde os 90%.
Sardinhas olham volume. Big players olham is_wash_trading — o que é mais difícil de maquiar.
Sardinhas seguem intuição. Big players seguem dados out-of-sample.
O erro que mata a maioria: minerar dados → encontrar padrão → "validar" no mesmo conjunto = ilusão de edge. O NeideBOT não comete esse erro.
