# GMGN Filtered Trending — Hipótese de demanda (research-only)

Sistema **passivo** que testa: *"Entre os tokens que o scanner já capturou na
faixa de migração, aqueles que aparecem no GMGN Trending filtrado pela nossa zona
operacional têm maior chance de bounce?"*

Não compra, não vende, não toca executor/scanner/H26/H27. Apenas rotula
(`label`) tokens do universo do scanner que aparecem no Trending filtrado e mede
o resultado com `token_ticks`.

Universo da análise: **scanner universe ∩ GMGN filtered trending set**.

---

## Fase 0 — Investigação da API de Trending

### Endpoint
`GET /v1/market/rank` (mesmo que o `TrendingRadar` existente já usa em produção).

A aba "Trending" da UI da GMGN é servida por este endpoint. Ele já é chamado
hoje via `GmgnClient.get_market_trending()` e retorna a lista que aparece na UI.

### Método e auth
- **GET**, headers padrão da OpenAPI: `X-APIKEY` + query params `timestamp` e
  `client_id` (sem assinatura Ed25519 — não é endpoint de trade).
- Peso de rate limit: **1** (~20 req/s sustentado). Barato.

### Query params confirmados
| Param | Tipo | Notas |
|-------|------|-------|
| `chain` | string | `sol` / `bsc` / `base` / `eth` |
| `interval` | string | `1m` / `5m` / `1h` / `6h` / `24h` — **timeframe da aba** |
| `limit` | int | default 100, **máx 100** |
| `order_by` | string | `default` / `marketcap` / `liquidity` / `volume` / `swaps` / `smart_degen_count` / `gas_fee` / ... |
| `direction` | string | `asc` / `desc` (default `desc`) |
| `filter` | string (repetível) | tags de segurança chain-specific (ex.: `renounced`, `frozen`) |
| `platform` | string (repetível) | filtro de launchpad |

### ⚠️ Filtros de faixa (MC/liq/fee) — limitação importante
A skill `gmgn-market` documenta filtros de faixa server-side como **flags de CLI**
(`--min-marketcap`, `--max-marketcap`, `--min-liquidity`, `--min-volume`,
`--min-gas_fee`, etc.), mas **não expõe os nomes reais dos query params HTTP**.
O `OpenApiClient.ts` apenas faz spread de um objeto `extra` na query, sem mapear
nomes. Ou seja: não há confirmação dos nomes (`min_marketcap`? `marketcap_min`?
`minMarketcap`?).

**Decisão de design:** o filtro de faixa é aplicado **client-side** sobre o
top-100 retornado. É robusto (não depende de adivinhar nomes de param) e correto.
Custo: o cap de 100 é por timeframe — se a faixa 25k–35k cair fora do top-100 de
hotness, alguns tokens podem não aparecer. Para a hipótese isso é aceitável,
porque cruzamos com o universo do scanner de qualquer forma (a ausência no
trending **não** é tratada como rejeição — é o grupo de controle B).

### Campos da resposta (raiz: `data.rank`, lista de itens)
Por token, disponíveis e usados:
- `address` (mint), `symbol`, `name`, `rank`, `hot_level`
- `price`, `market_cap`, `liquidity`, `volume`, `history_highest_market_cap`
- `swaps`, `buys`, `sells`, `holder_count`, **`gas_fee`**
- `open_timestamp`, `creation_timestamp`, `launchpad_platform`, `exchange`
- segurança: `rug_ratio`, `is_wash_trading`, `top_10_holder_rate`,
  `top70_sniper_hold_rate`, `sniper_count`, `bundler_rate`, `bot_degen_rate`
- smart money: `smart_degen_count`, `renowned_count`, `bluechip_owner_percentage`

**Fee:** não há campo "total fees em SOL" explícito. O mais próximo é `gas_fee`
(unidade incerta — pode ser USD). Por isso o gate de fee client-side é **opt-in**
(`GMGN_FILTERED_TRENDING_APPLY_FEE_FILTER=false` por padrão). O gate de fee
**autoritativo** é `migrations.total_fee_sol` (já coletado do trenches, em SOL),
aplicado na faixa do grupo de controle.

### Paginação
Não há cursor/offset — o `limit` (≤100) é o teto. O filtro de faixa, quando feito
client-side, é aplicado **depois** dos 100.

### Automatizável sem browser?
**Sim.** Nenhum headless é necessário — é uma chamada HTTP autenticada por API
key, já em uso no projeto. Não precisamos da UI.

### Resumo Fase 0
- Endpoint: `GET /v1/market/rank` ✅
- Params claros: `chain`, `interval`, `limit`, `order_by`, `direction`, `filter`, `platform` ✅
- Params de faixa MC/liq/fee server-side: **não confirmados** → filtro client-side ✅
- Resposta contém mint/symbol/MC/liq/volume/txs/holders ✅ (fee só via `gas_fee`, unidade incerta)
- Automatizável sem browser ✅

---

## Arquitetura (Fases 1–6)

```
FilteredTrendingCollector (thread no worker, GMGN_FILTERED_TRENDING_ENABLED=true)
  loop a cada POLL_SEC:
    1. store.get_scanner_universe(window_min)        # migrations recentes (local)
    2. para cada timeframe (5m, 1h):
         gmgn.get_market_trending(interval=tf, limit=100)
         → normaliza → filtra faixa (MC/liq[/fee]) client-side
         → interseção local com mints do scanner
         → store.insert_labels(...)  (dedup por mint+profile / DEDUP_SEC)
    3. store.refresh_outcomes(batch)                 # outcome via token_ticks
```

### Arquivos
| Arquivo | Papel |
|---------|-------|
| `gmgn_filtered_trending.py` | Coletor passivo (`FilteredTrendingCollector`) |
| `gmgn_filtered_trending_store.py` | Schema, insert, outcome (token_ticks), relatório/controle |
| `gmgn_filtered_trending_routes.py` | `/debug/gmgn-filtered-trending` (HTML + JSON) |
| `app.py` | registra blueprint + inicia coletor sob env flag |

### Tabelas
| Tabela | Conteúdo |
|--------|----------|
| `gmgn_scanner_trending_labels` | 1 linha por aparição (mint, profile, timeframe, rank, MC, liq, fee, volume, txs, holders, contexto do scanner, derivados) |
| `gmgn_scanner_trending_outcomes` | outcome por label via `token_ticks` (runup/drawdown por janela, hit flags, tempos, relação com bounce). `no_ticks` ≠ loss. |

### Profiles iniciais
- `migration_band_25_35k_5m` — tf 5m, MC 25k–35k, liq>5k, fee>1 SOL
- `migration_band_25_35k_1h` — tf 1h, MC 25k–35k, liq>5k, fee>1 SOL

### Dedup
Máx. 1 label por `(mint, profile)` a cada `DEDUP_SEC` (default 600s). A primeira
aparição é sempre preservada (`first_seen_for_mint_profile=TRUE`).

### Rate limit
2 chamadas `market/rank` por ciclo (peso 1 cada). Em 60s ≈ desprezível. Usa a
`GMGN_SCANNER_API_KEY` (mesmo client do scanner), bloqueando junto no ban global.

---

## Grupo de controle (Fase 5)
- **Grupo A** — migrations na faixa operacional que apareceram em ≥1 label.
- **Grupo B** — migrations na mesma faixa que **não** apareceram em nenhum label.

Outcome de ambos é ancorado na migração (`entry=mc_migration`, `ts=ts_migration`)
sobre `token_ticks` → comparação apples-to-apples. `no_ticks` é excluído (não vira
loss).

## Endpoint
`/debug/gmgn-filtered-trending` — HTML (default) ou `?format=json`.
Params: `since=YYYY-MM-DD`, `days=N`, `refresh=0|1`, `batch=N`.

## Fase 7 — uso futuro (NÃO implementado)
O relatório gera apenas uma **recomendação textual** (context / boost / veto /
gestão) se houver edge ≥ 10pp com n≥20 por grupo. Nenhuma mudança de
compra/venda/estratégia é aplicada nesta entrega.
