# NeideBOT — Contexto do Projeto

## O que é
Bot autônomo de trading em Solana.
Estratégia central: detectar tokens recém-migrados (Pump.fun → Raydium), aguardar o dump pós-migração e comprar o fundo com filtros de qualidade.

Completamente independente do wallet-monitor/copy trade.

---

## Stack
- Python + Flask + PostgreSQL (Railway)
- GMGN API — discovery, token info, execução
- GMGN Agent API — execução de swaps com TP/SL automático (condition_orders)
- Telegram Bot — alertas e controle
- Deploy: **Railway**

---

## 🎯 ESTRATÉGIA CENTRAL — Post-Migration Dip

### O Padrão
Quando um token completa a bonding curve no Pump.fun, ele migra para Raydium.
Nesse momento os early buyers realizam lucro → dump imediato.
Se o token tem fundamentos reais, esse dump é temporário e o preço recupera.

### Pipeline de execução
```
GMGN API polling
    │
    ├─ Token detectado migrando (bc_progress → 100%)
    │
    ├─ Registra MC de migração (= ATH do momento)
    │
    ├─ Monitora evolução de preço pós-migração
    │
    ├─ Queda ≥ DUMP_THRESHOLD% do ATH de migração?
    │       └─ NÃO → continua monitorando (timeout: MAX_MONITOR_MINUTOS)
    │       └─ SIM → aplica filtros de qualidade
    │
    ├─ Filtros passaram?
    │       └─ NÃO → descarta
    │       └─ SIM → executa compra via GMGN Agent API
    │
    └─ TP/SL configurado automaticamente (condition_orders)
```

---

## 🔍 FASE 1 — Coleta de Dados (ANTES de executar capital)

### Objetivo
Responder empiricamente às hipóteses antes de construir o executor.
Mínimo: **200 migrações observadas** com dados completos.

### O que coletar por migração
| Campo | Fonte | Descrição |
|-------|-------|-----------|
| `mint` | GMGN graduating | Endereço do token |
| `ts_migration` | Detecção | Timestamp da migração |
| `mc_migration` | GMGN token info | MC no momento da migração (ATH) |
| `mc_bottom` | Monitoramento | MC mínimo nos primeiros 30min |
| `time_to_bottom_min` | Calculado | Minutos da migração até o fundo |
| `pct_drop` | Calculado | `(mc_migration - mc_bottom) / mc_migration` |
| `mc_1h` | Checkpoint | MC 1h após migração |
| `mc_4h` | Checkpoint | MC 4h após migração |
| `mc_24h` | Checkpoint | MC 24h após migração |
| `var_1h` | Calculado | Variação % vs mc_migration |
| `var_4h` | Calculado | Variação % vs mc_migration |
| `var_24h` | Calculado | Variação % vs mc_migration |
| `smart_wallets` | GMGN wallet_tags_stat | Smart wallets holding no momento da migração |
| `renowned_wallets` | GMGN wallet_tags_stat | KOL wallets |
| `sniper_wallets` | GMGN wallet_tags_stat | Sniper wallets |
| `rug_ratio` | GMGN stat.top_bundler_trader_percentage | Proxy de risco bundler |
| `top10_holder_rate` | GMGN stat | Concentração top 10 holders |
| `holders_count` | GMGN raw.holder_count | Holders no momento da migração |
| `volume_5m` | GMGN stat.volume_5m | Volume 5min antes da migração |
| `liq_migration` | GMGN liquidity / 2 | Liquidez single-sided |
| `is_honeypot` | GMGN raw | Flag de segurança |
| `recovery` | Calculado | Bool: mc_1h > mc_bottom × 1.3 (recuperou ≥30% do fundo) |

### Tabela `migrations`
```sql
CREATE TABLE migrations (
    id              SERIAL PRIMARY KEY,
    mint            TEXT UNIQUE,
    token_nome      TEXT,
    ts_migration    TIMESTAMP,
    mc_migration    FLOAT,
    mc_bottom       FLOAT,
    time_to_bottom  FLOAT,
    pct_drop        FLOAT,
    mc_1h           FLOAT,  var_1h  FLOAT,
    mc_4h           FLOAT,  var_4h  FLOAT,
    mc_24h          FLOAT,  var_24h FLOAT,
    smart_wallets   INT,
    renowned_wallets INT,
    sniper_wallets  INT,
    rug_ratio       FLOAT,
    top10_holder_rate FLOAT,
    holders_count   INT,
    volume_5m       FLOAT,
    liq_migration   FLOAT,
    is_honeypot     BOOL,
    recovery        BOOL,
    comprado        BOOL DEFAULT FALSE,
    resultado_pct   FLOAT
);
```

---

## 📐 HIPÓTESES FORMAIS

> Registradas ANTES de ver os dados. Não alterar retroativamente.
> Testar em dados coletados APÓS a data de registro.

### H1 — Existe um dump sistemático pós-migração
**Hipótese**: ≥70% dos tokens caem ≥20% do ATH de migração nos primeiros 30min.
**Critério de confirmação**: proporção ≥70% com ≥200 migrações observadas.

### H2 — O fundo ocorre dentro de uma janela previsível
**Hipótese**: em tokens com recovery=TRUE, o fundo (mc_bottom) ocorre em ≤15min após a migração.
**Critério de confirmação**: mediana de time_to_bottom ≤15min no subgrupo recovery=TRUE.
**Implicação operacional**: janela de monitoramento de 15min é suficiente para capturar o fundo.

### H3 — Smart money prediz recovery
**Hipótese**: tokens com smart_wallets ≥3 no momento da migração têm recovery rate significativamente maior.
**Evidência de suporte**: no copy trade (wallet-monitor), SD≥3 → retorno médio +12.1% vs SD=0 → -14.8% (H1 do wallet-monitor).
**Critério de confirmação**: recovery rate SD≥3 ≥ recovery rate SD<3 + 15pp com ≥50 tokens em cada grupo.

### H4 — Sniper ratio alto prediz não-recuperação
**Hipótese**: tokens com sniper_wallets/holders_count ≥ 30% não recuperam o dump (saem os snipers = sell pressure constante).
**Critério de confirmação**: recovery rate sniper≥30% < 30% com ≥40 tokens nessa faixa.

### H5 — Concentração de holders prediz rug
**Hipótese**: tokens com top10_holder_rate ≥ 0.80 (80% do supply nos top 10) têm rug rate ≥ 60%.
**Critério de confirmação**: proporção de var_24h < -0.80 (queda ≥80% em 24h) ≥ 60% nesse grupo.

### H6 — Existe uma janela ótima de entrada (% de queda)
**Hipótese**: entradas entre -25% e -45% do mc_migration têm melhor relação risco/retorno que entradas em -10% ou -60%.
**Critério de confirmação**: retorno médio var_4h e var_24h é máximo para tokens com pct_drop entre 25%–45%.

### H7 — Liquidez mínima é necessária para executabilidade
**Hipótese**: tokens com liq_migration < $5.000 geram slippage inaceitável para posições > $50.
**Critério de confirmação**: verificar empiricamente — simular compra de $50 com liq < $5k vs > $10k.

---

## 🔬 O QUE JÁ SABEMOS (do wallet-monitor)

### GMGN API — Mapeamento de campos
```python
raw = _gmgn_token_info(mint)  # retorna body["data"]

mc = float(raw["price"]) * float(raw["circulating_supply"])  # market_cap não existe
liq = float(raw["liquidity"]) / 2                            # sempre double-sided → ÷2

wts = raw.get("wallet_tags_stat") or {}
smart_wallets   = wts.get("smart_wallets")
renowned_wallets= wts.get("renowned_wallets")
sniper_wallets  = wts.get("sniper_wallets")

stat = raw.get("stat") or {}
rug_ratio        = float(stat.get("top_bundler_trader_percentage") or 0)
volume_5m        = float(stat.get("volume_5m") or 0)
top10_holder_rate= float(stat.get("top_10_holder_rate") or 0)

holders_count = int(raw.get("holder_count") or 0)
is_honeypot   = raw.get("is_honeypot")
bc_progress   = raw.get("bc_progress")  # 0–99 bonding curve; 99 = já migrado
```

### GMGN Agent API — Execução
```python
# Ed25519 private key (GMGN_PRIVATE_KEY env var)
# Latência observada em produção: ~9s do sinal até confirmação
# TP/SL automático via condition_orders
# Endpoint: POST /v1/tx/sol/swap
```

### Lições do copy trade aplicáveis aqui
| Lição | Aplicação no NeideBOT |
|-------|----------------------|
| SD≥3 prediz retorno positivo | Filtro obrigatório: smart_wallets ≥ 3 |
| `bc_progress = 99` é sentinel de migrado | Detectar migração via bc_progress ≥ 99 |
| GMGN liquidity é double-sided | Sempre dividir por 2 |
| `market_cap` não existe no endpoint | Calcular price × circulating_supply |
| Threading.Timer esgota threads | Usar heap scheduler para monitoramento assíncrono |
| Dados validados em out-of-sample | Backtest antes de executar capital |

---

## 🔭 ENDPOINTS GMGN A DESCOBRIR

Endpoints conhecidos (confirmados no wallet-monitor):
- `GET /v1/token/info/{chain}/{mint}` — dados de token ✅

Endpoints a mapear para o NeideBOT:
- Tokens se graduando (bc_progress próximo de 100) — **a pesquisar**
- New pairs Raydium (tokens recém-migrados) — **a pesquisar**
- Trending/trenches — **a pesquisar**

> Estratégia: usar `GET /debug_gmgn` do wallet-monitor como referência,
> e explorar os endpoints da interface web do GMGN via DevTools (Network tab).

---

## 🏗️ ARQUITETURA (Fase 1 — Coleta pura, sem execução)

```
Scanner (thread ou polling)
    │
    ├─ Chama GMGN graduating/new_pairs a cada 30s
    ├─ Detecta tokens com bc_progress ≥ 99 ou novos no Raydium
    ├─ Registra mc_migration + todos os campos de contexto
    │
    └─ Dispatcher (heap scheduler — 1 thread, igual wallet-monitor)
            ├─ Timer +5min  → registra mc atual
            ├─ Timer +15min → registra mc atual (captura fundo scalper)
            ├─ Timer +30min → registra mc atual
            ├─ Timer +1h    → registra mc_1h
            ├─ Timer +4h    → registra mc_4h
            └─ Timer +24h   → registra mc_24h + calcula recovery
```

**Fase 2** (após backtest): adicionar camada de decisão + executor.

---

## 🔧 Variáveis de Ambiente (Railway)

```
DATABASE_URL=postgres://...
GMGN_API_KEY=xxxx
GMGN_PRIVATE_KEY=xxxx        # Ed25519 — só necessário na Fase 2 (execução)
TELEGRAM_TOKEN=xxxx
TELEGRAM_CHAT=xxxx

# Parâmetros de estratégia (configuráveis sem redeploy)
DUMP_THRESHOLD=0.30          # % de queda do ATH para considerar entrada (ex: 30%)
MAX_MONITOR_MINUTOS=30       # timeout de monitoramento por token
SMART_WALLETS_MIN=3          # filtro mínimo de smart money
SNIPER_MAX_PCT=0.30          # filtro máximo de sniper ratio
TOP10_MAX=0.80               # filtro máximo de concentração holders
LIQ_MIN_USD=5000             # liquidez mínima para execução
BOT_ATIVO=false              # NÃO ativar até backtest concluído
BOT_VALOR_USD=10             # valor por trade quando ativado
```

---

## 📊 Dashboard `/scanner`

Tabela de migrações observadas:
**Colunas**: Data | Token | MC migração | Fundo | Queda% | Tempo p/ fundo | 1h | 4h | 24h | SmDegen | Sniper | Rug% | Top10 | Recovery | Comprado | Resultado

Horário: BRT (UTC-3). MC em USD formatado.

---

## ⚠️ Notas Críticas de Implementação

- **Não executar capital antes do backtest** — mínimo 200 migrações observadas
- **Threading**: usar heap scheduler (lição do wallet-monitor) — nunca `threading.Timer` por evento
- **GMGN rate limit**: stagger de 2–3s entre chamadas em lote
- **bc_progress = 99** é sentinel de "já migrado" — não confundir com bc_progress em progresso
- **Timestamps em UTC no banco** — converter para BRT apenas na exibição
- **Liquidez double-sided**: GMGN retorna total, usar `÷ 2` para liquidez real (single-sided)
- **Token morto**: se mc cair ≥80% do mc_migration antes de 1h → cancelar timers restantes (token rugged)

---

## 📋 Fases de Desenvolvimento

### Fase 1 — Scanner + Coleta (agora)
- [ ] Mapear endpoints GMGN de tokens graduando
- [ ] Implementar scanner (polling de novas migrações)
- [ ] Implementar heap scheduler para checkpoints de MC
- [ ] Dashboard `/scanner` para visualizar dados
- [ ] Coletar ≥200 migrações com dados completos

### Fase 2 — Backtest Walk-Forward
- Pré-requisito: ≥200 migrações com mc_bottom, time_to_bottom, mc_1h, mc_4h, mc_24h populados
- Split: 70% in-sample / 30% out-of-sample
- Testar hipóteses H1–H7 isoladamente e combinadas
- Definir DUMP_THRESHOLD, SMART_WALLETS_MIN, filtros ótimos com base nos dados

### Fase 3 — Execução com Capital Mínimo
- Pré-requisito: backtest validado (retorno out-of-sample > baseline, payoff ≥ 2×)
- Deploy com BOT_ATIVO=true e BOT_VALOR_USD baixo ($5–10/trade)
- 50 trades reais como "out-of-sample definitivo"
- Se retorno divergir >30% do backtest → revisar

---

## 🎯 KPIs de Sucesso

### Fase 1 (coleta)
| Métrica | Meta |
|---------|------|
| Migrações observadas | ≥200 |
| Campos completos por migração | mc_migration, mc_bottom, time_to_bottom, mc_1h, mc_4h, mc_24h |
| Recovery rate geral (baseline) | documentar sem filtrar |

### Fase 2 (backtest)
| Métrica | Critério de aprovação |
|---------|----------------------|
| Win rate out-of-sample | ≥ 50% |
| Payoff ratio out-of-sample | ≥ 2× |
| Max drawdown curva de equity | ≤ 40% |

### Fase 3 (produção)
| Métrica | Meta |
|---------|------|
| Latência detecção → ordem | < 15s |
| Divergência real vs backtest | < 30% após 50 trades |

---

## 🧠 Metodologia — Big Player

A mesma do wallet-monitor:
1. Hipóteses documentadas **ANTES** de ver os dados
2. Coleta de dados
3. Backtest walk-forward (in-sample / out-of-sample)
4. Filtros construídos sobre o backtest, não sobre intuição
5. Deploy com capital mínimo como validação final

**O erro que mata a maioria dos sistemas**: minerar dados → encontrar padrão → "validar" no mesmo conjunto = ilusão de edge. O NeideBOT não comete esse erro.
