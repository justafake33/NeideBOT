# NeideBOT — Roadmap

**Atualizado:** 2026-05-30 (sessão 18)
**Estado:** Bot PARADO para captação de microdados (V2 Fase 1) · `TRADING_ENABLED=false` · branch ativo: `claude/nifty-hamilton-tQORb`

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
| **`helius_rpc.py` — fill on-chain + detecção de posição** | 2026-05-13 | Novo módulo. `get_swap_fill(tx_sig, mint)` via `getTransaction` lê fill real da blockchain. `get_token_balance(mint)` via `getTokenAccountsByOwner` detecta posição fechada (balance=0). `extract_tx_signature(query_order_resp)` extrai tx sig de múltiplos campos. Requer `HELIUS_RPC_URL` no Railway. |
| **TradeMonitor `_check_position_closed()` via Helius** | 2026-05-13 | Resolve cegueira estrutural: quando `not ticks`, chama `get_token_balance()` — balance=0 → GMGN fechou posição → infere TP/SL via ratio da última MC vs thresholds. Elimina trades presos `open` horas após fechar. |
| **`fill_source` coluna em `trades`** | 2026-05-13 | Rastreia origem do fill data: `gmgn`/`helius`/`helius_recovery`/`gmgn_recovery`. ALTER TABLE IF NOT EXISTS. |
| **`tp_mc` fallback quando `entry_mc_real=None`** | 2026-05-13 | GMGN nunca retorna `filled_output_amount` → `entry_mc_real=None` → `tp_mc=None` → TP nunca detectado. Fix: `tp_mc = entry_expected × (1+tp_pct)` quando real não disponível. Flag `tp_from_expected` indica quando fallback foi usado. |
| **`/debug/open-trades` endpoint** | 2026-05-13 | Diagnóstico de trades presos. Mostra entry_expected, entry_mc_real, sl_mc, tp_mc, tick count, min/max, flags hit, raw JSON do query_order ao vivo. Identifica exatamente por que TradeMonitor não detectou saída. |
| **Fix `/analise/h22-correlation` CTE** | 2026-05-13 | CTE excluía `status='open'` → todos os bounces com trades mostravam `outcome=NULL`. Fix: sem WHERE filter — latest trade por mint independente de status. |
| **H22 cobertura: 6% (32/552)** | 2026-05-13 | Cresceu de 1.7% (9/529). 100% dos tokens pump.fun novos terão cobertura via FILTER_PUMP_ONLY + Helius WS. Histórico anterior à implementação ficará sem dados. |
| **`/debug/absorption` — 3 summary boxes H22** | 2026-05-13 | Adicionados boxes: "H22 ticks (holding)", "Trades monitorados", "Mints distintos" — counts da tabela `trade_absorption_ticks`. |
| **Trade Autopsy `/analise/trade-autopsy`** | 2026-05-13 | Dashboard de causa-raiz dos SLs: dead_from_start/fast_dump/bounce_then_fail/near_tp/slow_bleed. Seção pré/pós exit com max_gain_before/after_exit, hit_tp_before/after_exit (boolean), spike_suspect flag. |
| **Fix mc_high → mc para trajetória** | 2026-05-13 | `tick_pre` e `tick_post` CTEs agora usam `tt.mc` (close), não `mc_high`. mc_high é inflado por spikes intra-candle do pAMM — um único trade em pool pequena pode temporariamente empurrar AMM price 200-300x. Causa confirmada de falsos "trailing_falhou". |
| **GMGN trailing TP confirmado funcionando** | 2026-05-13 | Verificação manual no gráfico dos 16 casos "trailing_falhou": HUMANITY +56%, Kevin +311%, SLEEP +41%, DrawPoorly +46% foram exits lucrativos reais. Os outros 11 (RSDM, RBC, SPM, TROGE, OARPE, MEMES, LEXAHOUSE, TECH, Talulah, Trending, VIRUS) eram SL corretos — mc_high capturou spikes que nunca existiram como price action real. |
| **DB fix: 4 trailing TPs corrigidos** | 2026-05-13 | HUMANITY #105, Kevin #153, SLEEP #174, DrawPoorly #157 corrigidos para `status='tp'` com PnL real. TradeMonitor não detectava trailing exit após janela HotMonitor de 60min. WR corrigido: 38→42 TP, 94→90 SL, 29%→32%. |
| **`/debug/trailing-review`** | 2026-05-13 | Lista todos os "trailing_falhou" com timestamps BRT, ticks (confiança), max_gain_before_exit (close), exit esperado (pico×0.70), links DexScreener+GMGN para verificação manual. |
| **SL <1min diagnostic no trade-autopsy** | 2026-05-13 | Bucket `<1min` separado na velocidade do SL. Box diagnóstico explica dois subtipos: real fast dump (max_gain≈0%) vs spike artifact (max_gain muito alto, poucos ticks). |
| **bounce_pct >70% → WR 74% confirmado** | 2026-05-13 | n=23 trades, +45pp vs WR base. Gradiente claro: 30-40%→12%, 40-55%→23%, 55-70%→40%, >70%→74%. Maior discriminador encontrado nos dados live. |
| **smart_wallets: zero discriminação live** | 2026-05-13 | smart=1→31%, smart=2→32%, smart=3→30%, smart=5+→19%. Flat ou negativo com smart alto. Contradiz análise anterior (n=6 para smart=1). FILTER_SMART_MIN=3 pode ser rebaixado. |
| **`/debug/sell-recovery` — recuperação blockchain completa** | 2026-05-14 | Pagina TODAS as txs da wallet desde 06-05-2026 via Helius `getSignaturesForAddress` (cursor `before`, sem limite fixo). Detecta sells reais (SOL+ AND token−). Match trade ↔ sell por mint+timestamp. Atualiza `exit_type='helius_sell_recovery'`, `exit_price_usd`, `pnl_pct_real`. Streaming via `stream_with_context`. |
| **Auditoria blockchain — banco completamente reescrito** | 2026-05-14 | DRY RUN: 573 txs, 201 sells, **139 trades corrigidos** (0 não encontrados). WR real = **10.1%** vs 32% do DB. P&L médio = -11.4%. 41 status errados, 121 P&L errados. Kevin +335% era SL no DB. SNDK era TP no DB mas SL real. |
| **Problema de dados fictícios documentado e resolvido** | 2026-05-14 | TradeMonitor usava `entry_expected × (1±tp/sl_pct)` e token_ticks — nunca blockchain. Após HotMonitor 60min expirar, ticks paravam → trades marcados `expired` com pnl=+75%. `exit_type='helius_sell_recovery'` é a única fonte de verdade válida para P&L. |
| **Bug crítico H23 — janela errada corrigida** | 2026-05-16 | `get_absorption_window()` usava `time.time()` (momento do bounce) como fim da janela → capturava compras DO bounce, não absorção DO dump. Fix: parâmetro `window_end_ts` + scanner passa `ts_min.timestamp()`. Janela correta: `[ts_min - 60s → ts_min]`. 82% dos 749 bounces históricos tinham janela errada. |
| **`h23_window` tagging** | 2026-05-16 | Campo `TEXT` em `bounce_signals`. `'bounce'` = janela antiga (momentum). `'bottom'` = janela correta (absorção). Backfill histórico com `'bounce'`. Novos bounces com `'bottom'`. Endpoints `/debug/h23-wr?window=bottom` e `/debug/h23-grid` propagam o filtro. |
| **Correlação H23 × bounce_pct = -0.252** | 2026-05-16 | Pearson -0.252 → H23 não é proxy de bounce_pct. Informação independente. Zona 3-10 SOL: WR 35.7% (+16pp, n=14). Mas 5/12 TPs (42%) tinham net_sol_flow negativo → não pode ser gate obrigatório com janela bounce. Aguardar n≥30 com `h23_window='bottom'`. |
| **`/debug/h23-window`, `/debug/h23-grid`** | 2026-05-16 | Dois novos endpoints de diagnóstico. h23-window: diagnóstico do gap entre ts_min e ts_signal por token. h23-grid: grade 2D H23 × bounce_pct com WR por célula e simulação de 8 combinações de filtros (n, WR, delta, trades bloqueados, TPs perdidos). |
| **Auditoria completa de bugs de dados de entrada** | 2026-05-17 | 18 bugs identificados via agente especializado. Causa raiz confirmada: kline `low` do candle de transição bonding curve→pool (elapsed_sec < 90s) continha preços pré-migração → mc_min fictício → sinais em tokens sem dump real. Caso Napoleon: 56 ticks com mínimo $33.8k, mas bounce_signals.mc_min=$21.6k. Portfolio: ~$50 de 1 SOL. |
| **Bug 1 CRÍTICO corrigido: `ts_migration` on-chain** | 2026-05-17 | `_handle_completed` usava `datetime.now()` como ts_migration. Token migrado 18min antes entrava com elapsed_sec≈0 → A.5/A.6 inúteis → bounce disparava imediatamente. Fix: `ts_mig_real = datetime.fromtimestamp(complete_timestamp)`. Corrige cascade de Bugs 1, 2 e 4 em uma linha. |
| **Bug 3 CRÍTICO corrigido: race condition `bounce_fired`** | 2026-05-17 | `push_mc` e `_precise_bottom_tick` rodavam concorrentemente e podiam double-fire o mesmo bounce (dois trades, não um). Fix: check-and-set atômico sob `_lock` antes do ponto-de-não-retorno em `_check_bounce()`. |
| **Bugs 5/6 ALTO corrigidos: filtro elapsed_sec < 90 em `_graduate` e `_backfill_slope_ticks`** | 2026-05-17 | Mesmo candle de transição afetava graduação (mc_bottom corrompido no banco) e backfill histórico de ticks (dados errados para análise de slope). Todos os três pontos de leitura kline (precise, graduate, backfill) agora consistentes. |
| **A.9 — Filtro de pool depth Helius (MIN_POOL_SOL=10)** | 2026-05-18 | `logsSubscribe(mentions=[mint])` captura QUALQUER tx que mencione o mint — incluindo secondary pools invisíveis nos gráficos. Evento espúrio identificado: pool com 0.83 SOL produzia preço $8,178 que nunca apareceu no DexScreener. Causa: pool do pAMM secundário (MEV, thin pool) com AMM math válido mas completamente separado da pool primária. Fix: armazenar `pool_quote_sol` por evento no deque + filtrar `get_min_mc_in_window()` por `pool_quote_sol >= MIN_POOL_SOL`. Threshold derivado da bonding curve: k = 79 SOL × 200M tokens → pior caso absoluto (100% tokens vendidos) = 15.8 SOL. Com nosso filtro MC ≥ $5k: mínimo da pool primária = 21.5 SOL. Todos os 356 trades históricos tinham pool estimada ≥ 38.6 SOL. MIN_POOL_SOL=10 rejeita secondary pools (0.83 SOL, gap 46×) com margem segura abaixo do mínimo real. |
| **Fix Helius window bug (A.9 segunda parte)** | 2026-05-18 | `_precise_bottom_tick()` buscava janela Helius com `candle_start = ts_mig + max(0, elapsed-60)`. Para o primeiro candle válido (elapsed=90s), isso dava `ts_mig+30` — ainda dentro da janela de transição bonding curve→pool onde preços não correspondem a trades reais. Fix: `max(90, elapsed-60)` garante que a janela Helius nunca começa antes de ts_mig+90s. |
| **`/debug/pool-depth`** | 2026-05-18 | Endpoint que mostra pool_quote_sol estimado via AMM math para cada trade histórico. Valida o threshold MIN_POOL_SOL. Histograma: 0 trades <10 SOL, 9 trades em 20-40 SOL, 346 em 40-80 SOL, 1 em 80+ SOL — confirma que o threshold de 10 SOL é extremamente conservador e seguro. |
| **k-gate INIT SKIP → rejeita + DROP REJECT → limpa e rejeita** | 2026-05-19 | Duas correções no k-gate em `helius_ws.py`. Antes: INIT SKIP passava o evento (sem k_established), DROP REJECT fazia reset para k_raw e passava. Resultado: eventos de secondary pool chegavam como dados válidos. Fix: INIT SKIP → `return`; DROP REJECT → `pop(mint)` + `return`. Commit `9c25f6b`. |
| **`_K_INIT_TOLERANCE = 0.80` — causa raiz do mc_bottom tardio** | 2026-05-19 | Tolerância anterior (0.60) definia `k_init_min = 2.51e25`. Pools com ~100 trades de fee acumulados têm k ≈ 1.767e25 — abaixo do mínimo. TODOS os eventos Helius desses mints eram INIT SKIP'd → bot cego → mc_min vinha de kline/trenches com lag de 2-4min. Para tokens com fundo em t=2min, sinal disparava em t=4:40 com bounce já +208%. Fix: tolerância 0.80 → `k_init_min = 1.253e25` — cobre até ~130 trades de fee. Secondary pools (0.83 SOL, k≈1.19e25) continuam bloqueadas. Commit `28ba86b`. **Hardcoded — zero env var necessária.** |
| **`BOUNCE_MAX_PCT` guard bug corrigido** | 2026-05-19 | Guard `< 1.0` desativava o filtro para qualquer threshold ≥ 100% (ex: `1.50 < 1.0 = False`). Setar `BOUNCE_MAX_PCT=1.50` no Railway **não tinha efeito**. Fix: `< 9.99`. Agora `BOUNCE_MAX_PCT=1.50` bloqueia bounces tardios >150%. Commit `9bbdaf2`. **Env var necessária: `BOUNCE_MAX_PCT=1.50`.** |
| **trailing_active fill recovery** | 2026-05-19 | `_recover_fill` só era chamado para trades `open`. Trades que transicionavam para `trailing_active` sem `entry_price_usd` nunca tinham fill recuperado. Fix: loop `get_trailing_active_trades()` agora tenta `_recover_fill` antes de `_check_trailing_exit`. Commit `aced744`. |
| **Diagnóstico ZUCC — case study de entrada tardia** | 2026-05-19 | Real bottom: $13K em t=2min após migração. Sinal disparou em t=4:40 com entrada a $40K (bounce 208%). Causa: k=1.767e25 < k_init_min=2.51e25 → INIT SKIP → Helius cego → kline 1-2min candle fechou, GMGN indexou, bot processou → $13K capturado correto mas com 2-3min de lag. Preço já estava a $40K. BOUNCE_MAX_PCT desativado (guard bug) → sinal disparou. Com fixes deployados: Helius teria visto fundo em tempo real, sinal teria disparado a ~$17K (30% bounce de $13K). |
| **erpc pipeline fix — try/except + `_erpc_tx_count` + early-return logging** | 2026-05-21 | Após deploy do erpc (sessão 12), zero logs de price/k-gate em produção por 6+ minutos. Causa: exceptions silenciosas swalladas pelo websocket-client + zero logging nos early-returns. Fix: try/except no `_on_message` + counter `_erpc_tx_count` (log primeiras 3 + cada 500ª) + warning em cada early-return. Commit `a48cb3b`. Resultado: em 0.05s após o fix, `tx #1 recebida` + k-gate ESTABLISHED + `Fundo(erpc)` funcionando. |
| **Pool-account learning por intersecção de 5 TXs** | 2026-05-21 | Seleção anterior (primeira TX, primeiro candidato) escolhia contas globais (vaults, fee accounts) antes do pool state PDA. Fix: acumular conjuntos de candidatos de 5 TXs diferentes e computar intersecção. ATAs de usuários são eliminados (variam por TX). Pool-specific accounts sobrevivem. Confirmado: `pool-account CONFIRMED GXyYU3xE: pool=CNa2muAa... (interseção de 5 TXs, 7 candidatos)` em ~300ms. Commit `70cd997`. |
| **Log spam `skip não-pAMM` → debug level** | 2026-05-21 | WARNING aparecia a cada poucos segundos para transactions de aggregators que invocam múltiplos programas. Comportamento esperado — não é bug. Fix: `logger.warning` → `logger.debug`. Commit `70cd997`. |
| **Primeiro token com dados limpos: JOB (DMircbtE)** | 2026-05-21 00:41 UTC | Marco zero da era de dados limpos. mc_migration=$35,412, smart=3. Todos os dados de mc_min coletados APÓS este timestamp são confiáveis (sem candle de transição, sem ts_migration bug, sem race condition). |
| **Patch mc_min drift — 3 correções ortogonais** | 2026-05-26 | (1) kline removido do write path de mc_min — erpc é fonte única; (2) k-gate INCREASE REJECT → RESET quando pool não confirmada — secondary pool hijack reversível; (3) PDA pre-seed em `_derive_async` — pool account confirmada por criptografia, sem learning window. Commits `fc21240` + `837db0c`. Auditoria: 100% PASS nos 7 critérios. |
| **mc_min_true com txid_proof** | 2026-05-26 | `helius_rpc.get_pool_bottom()`: `getSignaturesForAddress(pool_pda)` + parse Anchor dos logMessages de cada tx → mc_min com assinatura Solana como prova. Thread `_run_bottom_truth` disparada na graduação (async, não bloqueia trade). `helius_ws.get_pool_pda(mint)` expõe PDA confirmado. Commit `ae65a9c`. |
| **bottom_error_pct + bottom_quality** | 2026-05-26 | `bottom_error_pct = (mc_min_decision - mc_min_true) / mc_min_true`. Positivo = bot viu fundo mais alto. `bottom_quality`: good (≤5%), ok (5-15%), bad (>15%). 6 novas colunas em `migrations`. Após primeiro ciclo de graduações (60min pós-deploy), banco terá métrica objetiva de confiança na captura de fundo. Commit `ae65a9c`. |
| **Phantom fix — ALT tx bypass corrigido** | 2026-05-26 | `_tier1_process` validava pool account com `if accounts:` — lista vazia (transações ALT/versioned) era falsy → check pulado → secondary pool contaminava mc_min. Fix: `if known_pool and accounts is not None: if not accounts or known_pool not in accounts: return False`. Helius mode (`accounts=None`) não afetado — validação só aplica quando lista de accounts está disponível (erpc mode). Commits `16a512b` + `037a06c`. |
| **Auditoria bottom capture n=15 — CONCLUÍDA** | 2026-05-26 | Comparação manual bot mc_min vs candles de 1 segundo DexScreener em 15 trades. 10/15 (67%) precisos ≤3%. 3/15 (20%) limitação de learning window (WHY +22.5%, CTO +19.7%, RAMBO +3.7% — tradeoff intencional contra phantoms). 2/15 (13%) phantoms MILLION -33%, Multipad -57% — causados pelo ALT tx bypass, agora corrigido. **Marco zero definitivo: 2026-05-26.** Todos os dados de mc_min a partir desta data são confiáveis. |
| **C1 — sl_ref fix (TradeMonitor)** | 2026-05-28 | `TradeMonitor._check_trade()` calculava SL threshold com `entry_expected = mc_bottom × 1.15` — para bounces altos (~95%), este valor ficava 41% abaixo da entrada real, fazendo o threshold nunca ser atingido. Fix: `sl_ref = entry_mc_real or trade.get("mc_at_signal") or entry_mc`. `mc_at_signal` é o MC no momento do sinal — proxy muito mais próximo da entrada real. Commit `f962915`. |
| **C2 — HeliusWS injetado no TradeExecutor** | 2026-05-28 | TradeExecutor não tinha acesso ao feed erpc em tempo real. Adicionado `self._helius = None` no `__init__` + método `set_helius()` (mesmo padrão do TradeMonitor). Em `app.py`: `executor.set_helius(hot._helius)` após a injeção no monitor. Habilita leitura de pool_quote em tempo real no momento do bounce (C3). Commit `f962915`. |
| **C3 — Pool depth gate no bounce** | 2026-05-28 | Pool rasa = bounce é ruído de AMM, não demanda real. Em pool de 10 SOL, uma única venda de ~$300 move o preço 25% — SL de 25% dispara por física, não por price action. Gate adicionado em `_check_bounce()` antes do bloco atômico `bounce_fired`: lê `_pool_quote_last[mint]` in-memory via `get_pool_quote(mint)` (zero latência). Se `pool_depth < POOL_MIN_SOL_BOUNCE` → sinal rejeitado. Env var `POOL_MIN_SOL_BOUNCE=10`. Commit `f962915`. |
| **C4 — ALT PDA fallback** | 2026-05-28 | Melhoria do phantom fix (commit 037a06c). Transações ALT (`accounts=[]`) eram sempre rejeitadas — bot perdia fundos reais que aconteciam em transações versioned (WHY +22.5%, CTO +19.7%, RAMBO +3.7% na auditoria n=15). Fix: quando `accounts=[]` E `_pool_pda[mint] == known_pool` → aceita (PDA é matematicamente garantido por `sha256(seeds + program_id)`). Sem PDA → ainda rejeita. Helius mode (`accounts=None`) → preservado sem validação. Commit `f962915`. |
| **V2 Fase 1 — micro_absorption_snapshots** | 2026-05-30 | Sistema de captação de microdados de fluxo de ordens ao redor do fundo. Tabela `micro_absorption_snapshots` (~97 colunas) com snapshots a cada 2s durante os 60s pós-fundo. Janelas PRE (5/10/15/30s), RISE (1/3/5/10s, com guard de completude), LIVE (5/10s). Concentrações nomeadas por janela (`pre_5s_buy_concentration` etc.). `candidate_flags` JSONB. `_build_window_features()` como utilitário puro sem I/O. `_maybe_record_micro_snapshot()` com rate limit 2s/token. Endpoint `/debug/micro-absorption-snapshots`. Bot parado (`TRADING_ENABLED=false`) para acumulação. Branch `claude/nifty-hamilton-tQORb`. Commits `d368b40` + `c9ae4d7` + `c42ba75`. |

> ⚠️ **WR REAL CORRIGIDO (2026-05-14 sessão 5):** banco tinha dados fictícios — TradeMonitor inferia saídas de MC snapshots, não da blockchain. Auditoria via Helius revelou WR real de **10.1%** (14 TP / 125 SL / 139 trades). WR anterior de 32% era inflado por falsos TPs. `/debug/sell-recovery` executado: 139 correções persistidas.
> ⚠️ **DADOS HISTÓRICOS SUSPEITOS (sessão 9):** mc_min de todos os tokens antes do fix (elapsed_sec < 90) pode estar corrompido pelo candle de transição. Análises de WR baseadas em `mc_min` histórico (bounce_pct, entry_ratio, drop_from_mig) devem ser tratadas com cautela.
> ⚠️ **MARCO ZERO DEFINITIVO: 2026-05-26 (sessão 16):** Phantom fix (ALT tx bypass) + auditoria manual n=15 confirmam que mc_min capturado a partir desta data é confiável. Trades anteriores (2026-05-21 a 2026-05-25) tinham erpc operacional mas bug ALT tx ainda ativo — mc_min potencialmente contaminado por secondary pools em transações versioned.

---

## 🔬 V2 — Reestruturação de Microestrutura

### Estado atual (2026-05-30)

**Bot PARADO** · `TRADING_ENABLED=false` · Branch: `claude/nifty-hamilton-tQORb`

O sistema de trading foi pausado para acumulação de dados de microestrutura. A ideia central é que filtros estáticos (smart count, bounce_pct, elapsed, creator_status) entregam no máximo +7pp de WR. O edge real está em **quem comprou o fundo e com que força** — e só conseguimos responder isso com dados de fluxo de ordens em granularidade de segundos.

### Fase 1 — Micro Absorption Snapshots ✅ CONCLUÍDA (2026-05-30)

**O que foi implementado:**
- Tabela `micro_absorption_snapshots` (~97 colunas) — série temporal a cada 2s por token durante os 60s após o fundo
- Janelas PRE (5/10/15/30s antes do fundo), RISE (1/3/5/10s após o fundo), LIVE (5/10s recentes)
- Guard de completude: janelas RISE com `bottom_age_sec < X` retornam `NULL` com `zero_type='incomplete'`
- Concentrações nomeadas por janela: `pre_5s_buy_concentration`, `rise_10s_buy_concentration`, etc.
- `candidate_flags` JSONB para flags arbitrárias (seller exhaustion, pool depth, etc.)
- `_build_window_features()` — utilitário puro sem I/O, facilita testes e reutilização
- Rate limiter: máx 1 snapshot por 2s por token, apenas dentro dos 60s pós-fundo
- Thread-safe: `last_micro_snapshot_ts` setado ANTES do dispatch para evitar burst concorrente
- Endpoint `/debug/micro-absorption-snapshots` — view HTML com células coloridas, ⏳ para RISE incompleto, suporte a `?format=json|csv`
- Falha silenciosa: try/except em `_record_micro_absorption_snapshot` e `insert_micro_absorption_snapshot` — qualquer erro de coleta não atinge o scanner

**Commits:** `d368b40` (foundation) · `c9ae4d7` (rise guards + named concentrations + silent insert) · `c42ba75` (fix NameError timezone)

**Travas de segurança (NUNCA violar em fases futuras):**
- Lógica de trading (`_check_bounce`, executor, swap, TP/SL, trailing) → intocável
- Tabelas existentes (`migrations`, `bounce_signals`, `trades`, `token_ticks`, `token_signals`) → sem DELETE/ALTER destrutivo
- Env vars de trading (`TRADING_ENABLED`, `BOUNCE_MIN_PCT`, `TRADE_SL_PCT`, etc.) → sem alteração por código de research
- Falha de coleta → log silencioso, nunca exceção propagada

---

### Fase 2 — Micro Absorption Lab ⏳ STANDBY

**O que fazer quando n≥30 snapshots com outcomes GMGN confirmados:**
1. Abrir `/debug/micro-absorption-snapshots` + `/analise/h22-correlation` — ver padrões emergentes
2. Calcular WR por combinação PRE/RISE/LIVE (positivo vs negativo vs nulo)
3. Identificar qual janela discrimina mais TP vs SL
4. Calcular correlações: `net_pre_5s × net_rise_5s`, `buy_concentration_pre × rise_events`, etc.
5. Documentar achados em CLAUDE.md antes de qualquer código novo

**Gate:** n≥30 bounces com `micro_absorption_snapshots` + outcomes GMGN (confirmados via `/debug/sell-recovery`)

---

### Fase 3 — Shadow Entry Signals ⏳ STANDBY

**O que fazer após Fase 2 identificar ≥1 janela discriminatória:**
- Implementar `shadow_entry_signals` — log de todos os sinais que passariam pelo filtro de absorção, sem executar
- Comparar WR hipotético do shadow vs WR real da execução atual
- Validar que o filtro não elimina mais de 40% dos bounces válidos (preserva frequência mínima)

**Gate:** Fase 2 com ≥1 métrica mostrando ≥15pp de discriminação (WR maior com absorção vs sem)

---

### Fase 4 — EntryEngineV2 ⏳ STANDBY

**O que fazer após Shadow mostrar melhora robusta:**
- Adicionar gate de absorção em `_check_bounce()` (condição adicional, não substitui filtros existentes)
- `ABSORPTION_GATE_ENABLED` env var — desligável sem redeploy
- Começar com o filtro mais conservador identificado na Fase 2

**Gate:** Shadow Entry com n≥50 e WR hipotético ≥25pp melhor que baseline, com ≤40% de bounces bloqueados

---

### Fase 5 — ExecutionEngineV2 Dry-run ⏳ STANDBY

**O que fazer após Fase 4 estabilizar:**
- `TRADING_ENABLED=true` com `CALIBRATION_AMOUNT_USD=1.0`
- n=30 trades reais com novos filtros
- Comparar WR com período pré-V2 (ajustado por regime de mercado)

**Gate:** Fase 4 ativa e estável por ≥7 dias

---

### Fase 6 — ExecutionEngineV2 Real Scout ⏳ STANDBY

**O que fazer após Fase 5 confirmar WR ≥25% em ≥30 trades:**
- Escalar para $10/trade
- Implementar ENTRY_MAX_BOUNCE_PCT (cancela trade se preço já subiu muito desde o sinal)
- Avaliar migração GMGN → Jupiter quando WR ≥30%

**Gate:** Fase 5 com WR ≥25% confirmado GMGN em ≥30 trades

---

## 🔮 Visão Futura do Projeto

### Próxima grande alavanca de código: seller exhaustion (H24)

A hipótese central não testada: **90% dos SLs são tokens onde os sellers NÃO tinham terminado quando entramos.** O fundo detectado não era o fundo real — havia pressão de venda residual não visível nos snapshots de 5s.

Com `micro_absorption_snapshots`, podemos medir diretamente:
- `pre_30s_sell` — quanta pressão de venda chegou nos 30s antes do fundo
- `rise_5s_buy_concentration` — bounce concentrado (poucos buyers grandes) vs disperso (muitos pequenos)
- `pre_5s_net` vs `rise_5s_net` — mudança de regime entre dump e bounce

Se `pre_30s_sell > threshold` e `rise_5s_buy_concentration < 0.3` → muitos sellers, pouca concentração de demanda → provavelmente não é o fundo real.

### Jupiter + Helius (quando WR ≥30% confirmado)

Migrar execução para Jupiter elimina a friction mais significativa:
- GMGN platform fee: 1%+1% = 2% → Jupiter LP fee: ~0.25%+0.25% = 0.5%
- ATA rent por SL: ~$0.20/SL → Jupiter fecha ATA no sell automaticamente
- Break-even a $10/trade: GMGN ~12% WR → Jupiter ~9% WR

Implementar DEPOIS de WR confirmado ≥30% em ≥30 trades a $10. Não antes.

### Re-entry em tokens com TP histórico

Tokens que fizeram bounce uma vez têm maior probabilidade de fazer de novo (comunidade estabelecida, liquidez comprovada). Implementar re-entry flag em `_check_bounce()` — `bounce_fired` não bloqueia re-entry se token tem TP anterior no DB.

### Score de confluência (longo prazo, n≥200 trades GMGN limpos)

Só faz sentido com dados suficientes por bucket. Com n<200, qualquer tier tem <50 amostras — ruído, não sinal. Prioridade: acumular dados, não modelar prematuramente.

---

## 🔜 Curto Prazo

---

## 🎯 Plano de Entrada Precisa — 4 Fases (sessão 14, 2026-05-24)

> **Diagnóstico atual (31 trades, $1/trade, WR=29%):**
> - Problema 1: mc_min com variação residual de 5-10% acima do fundo real on-chain (lag de captura — erpc recebe evento em ~5ms mas mc_min só é atualizado no próximo ciclo de 5s)
> - Problema 2: latência 1-3s do GMGN HTTP entre detecção e fill → preço move +5-15% entre `_check_bounce()` e a execução real
> - Contexto atual: filtros frouxos propositalmente para coletar mais trades → WR=29% é FLOOR, não teto
> - Trailing TP é crítico: responsável por 89% do lucro — TP fixo 50% teria resultado em -$1.83

### Fase 0 — Validar captura do fundo ← ✅ CONCLUÍDA (sessão 16, 2026-05-26)

**Resultado:** mc_min capturado pelo bot corresponde ao fundo on-chain real.

**Auditoria manual (n=15 trades, candles de 1 segundo DexScreener):**
- 10/15 (67%) precisos — erro ≤ 3%
- 3/15 (20%) limitação de learning window — tradeoff intencional (previne phantoms durante os primeiros ~300ms de pool confirmation)
- 2/15 (13%) phantoms — identificados e corrigidos (MILLION, Multipad — bug ALT tx, commit 037a06c)

**Phantom fix deployado:** `accounts is not None` guard em `_tier1_process`. Transações ALT com `accounts=[]` corretamente rejeitadas. Modo Helius (`accounts=None`) preservado.

**Marco zero definitivo: 2026-05-26.** Todos os dados de mc_min coletados a partir desta data são confiáveis e podem ser usados para análise de filtros e WR.

**Infraestrutura de validação contínua ativa:**
- `mc_min_true` preenchido async via `helius_rpc.get_pool_bottom()` após cada graduação
- `bottom_error_pct` e `bottom_quality` persistidos em `migrations`
- Monitorar: `SELECT bottom_quality, COUNT(*), AVG(bottom_error_pct)*100 FROM migrations WHERE mc_min_true IS NOT NULL GROUP BY bottom_quality;`

---

### Fase 1 — ENTRY_MAX_BOUNCE_PCT (após Fase 0) ← PRÓXIMO CÓDIGO

**Problema atacado:** bot detecta bounce em 30% do fundo via erpc (~5ms), mas o GMGN HTTP add 1-3s de latência para executar o swap. Nesse intervalo o preço já se moveu +5-15% — a compra acontece significativamente acima do ponto de detecção.

**Solução:** Se no momento da execução o preço corrente já estiver acima de `mc_at_signal × (1 + ENTRY_MAX_BOUNCE_PCT)`, cancelar o trade (não executar o swap).

**Implementação:**
```python
# executor.py — _execute(), antes de chamar swap()
if ENTRY_MAX_BOUNCE_PCT > 0:
    current_mc = helius_ws.get_latest_mc(mint)  # preço do erpc em tempo real
    if current_mc and current_mc > bounce_signal['mc_at_signal'] * (1 + ENTRY_MAX_BOUNCE_PCT):
        logger.warning(f"ENTRY_CANCEL: price moved {(current_mc/bounce_signal['mc_at_signal']-1)*100:.1f}% > {ENTRY_MAX_BOUNCE_PCT*100:.0f}% limit")
        db.update_trade_status(trade_id, 'cancelled')
        return
```

**Env var:** `ENTRY_MAX_BOUNCE_PCT=0.10` (cancela se preço já subiu >10% desde o sinal)

**Calibração:** Começar em 0.10. Se WR sobe mas frequência cai demais, tentar 0.15.

**Gate para ativar:** Fase 0 concluída (30 trades limpos).

---

### Fase 2 — Escala de Capital (após Fase 1 confirmada)

**Regra:** Um salto de cada vez. Só escalar quando WR for confirmado no size atual.

| Gate WR (GMGN, ≥30 trades) | Size alvo | Break-even WR | ATA rent % |
|-----------------------------|-----------|---------------|------------|
| WR ≥ 25% (meta atual) | $10/trade | ~12% | 2% |
| WR ≥ 28% (a $10) | $25/trade | ~11% | 0.8% |
| WR ≥ 30% (a $25) | $50/trade | ~11% | 0.4% |
| WR ≥ 35% (a $50) | $100/trade | ~11% | 0.2% |

**Nota:** a $1/trade o ATA rent (~$0.20/SL) representa 20% do investimento — o principal custo. A $10/trade cai para 2% — barreira de escala mínima para a estratégia ser lucrativa estruturalmente.

---

### Fase 3 — ATA Rent Reclaim

**Problema:** Cada SL deixa ATA (Associated Token Account) vazio com ~$0.20 de rent preso. A $1/trade com SL=75%, são ~$0.15/trade em rent bloqueado (75% das vezes).

**Solução curto prazo (manual):** 
- Toda segunda-feira: acessar GMGN wallet → botão "Reclaim Rent"
- Recupera ~$0.20 por SL acumulado

**Solução médio prazo (automático):**
```python
# Após cada SL confirmado (status='sl'):
# helius_rpc.close_ata(mint, wallet) → closeAccount instruction → SOL devolvido
```
Requer `WALLET_PRIVATE_KEY` configurado (já necessário para Fase 4).

**Gate para automatizar:** Fase 2 ativa (trades a $10+, onde vale o custo de implementação).

---

### Fase 4 — Jupiter Swap (após WR ≥ 30% confirmado)

**Por que:** GMGN cobra 1%×2 = 2% platform fee + não fecha ATA automaticamente. Jupiter = 0% platform fee + fecha ATA no sell → elimina o ATA rent problem.

**Impacto a $10/trade:**

| Custo | GMGN atual | Jupiter + Helius |
|-------|------------|-----------------|
| Platform fee | 2% (1%+1%) | 0% |
| LP fee (DEX) | ~0.25%+0.25% | ~0.25%+0.25% |
| ATA rent | ~$0.15/trade | ~$0 (fecha no sell) |
| Tip/priority | ~$0.04 | ~$0.02 |
| **Total a $10** | **~$0.92 (9.2%)** | **~$0.11 (1.1%)** |
| Break-even WR | ~28% | ~18% |

**Gate para iniciar:** WR ≥ 30% confirmado GMGN em ≥30 trades a $10+.

**Pré-requisito técnico:** `WALLET_PRIVATE_KEY` (Base58 da wallet Solana) para assinar txs diretamente.

**O que muda no código:** `executor.py` chama `jupiter_client.swap()` em vez de `gmgn.swap()`. TradeMonitor detecta exit via ticks → chama `jupiter_client.swap()` para sell. TP/SL gerenciados internamente (sem `condition_orders` do GMGN).

---

### 0. DEPLOY DAS CORREÇÕES ← ✅ CONCLUÍDO (sessão 13, 2026-05-21)

Deploy completo do branch `claude/audit-neidenbot-purchases-8k0Tn` em produção.
`TRADING_ENABLED=true`, `ERPC_API_KEY` configurado, `BOUNCE_MAX_PCT=1.50` ativo.
Bot rodando com dados limpos desde 00:41 UTC 2026-05-21.

---

### 0b. BOUNCE_MIN_PCT → 0.50 ← OPCIONAL após n≥10 trades limpos

**Por quê:** bounce_pct >70% tem WR 74% (n=23, +45pp). Mas esses dados são com mc_min corrompido. Aguardar n≥10 trades pós-fix para confirmar o gradiente persiste com dados limpos.

---

### 1. BOUNCE_MIN_PCT → 0.50 (ou 0.70) ← CONFIRMAÇÃO APÓS N≥30 TRADES LIMPOS

**Por quê:** bounce_pct >70% tem WR 74% (n=23, +45pp). Gradiente claro nos dados live.
Mudança de env var, zero código. Reduz frequência mas aumenta WR drasticamente.

| Threshold | WR | n | Frequência estimada |
|-----------|-----|---|---------------------|
| ≥30% (atual) | 29% | 132 | baseline |
| ≥50% | ~40% | ~46 | ~35% da frequência atual |
| ≥70% | 74% | ~23 | ~17% da frequência atual |

**Tradeoff:** a $1/trade com WR 74%, EV/trade = +49% vs +4% atual. Mesmo com 1/6 das operações, o resultado financeiro melhora.

**Gate:** Testar BOUNCE_MIN_PCT=0.50 primeiro. Aguardar n≥20 trades para confirmar WR ≥40%. Depois avaliar 0.70.

**Ação:** Mudar Railway env var `BOUNCE_MIN_PCT=0.50` agora. Zero risco de regredir — só reduz frequência.

---

### 1. FILTER_SMART_MIN=3 ← **✅ CONCLUÍDO (2026-05-12)**

`FILTER_SMART_MIN=3` ativo em Railway. smart=1→0% WR, smart=2→20%, smart=3→57% (dado live GMGN).

**Gate ativo:** Acumular n≥30 trades com smart≥3 antes de qualquer outra mudança de filtro.

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

**⚠️ COBERTURA HELIUS — DIAGNÓSTICO (2026-05-13, fim da sessão 3):**
```sql
-- bounce_signals: total=552, com_helius=32 (6%), sem_helius=~520
-- Cresceu de 1.7% (9/529) no início da sessão para 6% (32/552) ao final
```
552 bounces registrados. **32 têm `absorption_events > 0`** (6% de cobertura). Restantes têm NULL.
**Causa confirmada**: 100% dos tokens `exchange='pump'` têm cobertura Helius. Os 520 sem dados são tokens de exchanges anteriores (pré-FILTER_PUMP_ONLY) ou períodos antes da implementação do rolling window (pré-2026-05-12). Com FILTER_PUMP_ONLY ativo, TODOS os novos tokens serão pump.fun pAMM → 100% cobertura futura.

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
| H23 | `net_sol_flow = buy_vol_sol - sell_vol_sol > threshold` discrimina WR | 🟡 **COLETA ATIVA** — implementado e corrigido. Bug de janela (time.time vs ts_min) descoberto e corrigido. h23_window='bottom' para novos bounces. Bounce-window (n=61): zona 3-10 SOL tem WR 35.7% (+16pp), mas 42% dos TPs tinham H23 negativo. Gate: n≥30 com h23_window='bottom' + outcomes GMGN. |
| H24 | Seller exhaustion (top-3 sellers > 70% do dump_vol) → WR maior | ⏳ **PENDENTE IMPLEMENTAÇÃO** — hipótese central pós H21. Requer acumular sellers únicos via Helius entre ts_migration e ts_min. |
| H25 | Pool depth `pool_quote < 20 SOL` → SL é ruído de pool thin, não price action | ⏳ **PENDENTE IMPLEMENTAÇÃO** — dado em offset 56 do BuyEvent já chega via Helius. Falta salvar em bounce_signals. |
| H26 | Buy concentration `largest_buy / buy_vol` alta → WR maior | ⏳ **PENDENTE CORRELAÇÃO** — campo calculável dos dados existentes. Gate: n≥30 com outcomes GMGN. |

---

## 🔬 Fase de Microestrutura (sessão 7 — estado atual)

### Princípio desta fase
> **Tudo é coleta de informações. Os dados mostram a verdade.**
> Nenhuma hipótese é confirmada antes de n≥30 bounces com outcomes GMGN.
> Nenhum filtro é implementado antes de confirmação nos dados.
> Construir instrumentos de medição → deixar acumular → analisar → só então modelar.

### ✅ Infraestrutura de coleta implementada (sessão 7)
- **Três janelas de fluxo por bounce** (`bounce_signals`):
  - `absorption_*` + `net_sol_flow` = PRÉ `[ts_min−60s → ts_min]` — absorção do dump
  - `rise_*` = RISE `[ts_min → ts_signal]` — quem comprou durante a subida?
  - `p60s_*` = PÓS `[ts_signal → ts_signal+60s]` — demanda sustentando?
- **`/debug/flow-timeline`**: tabela com as três janelas + outcome por bounce
- **`h23_window` tagging**: `'bottom'` para todos os novos bounces pós-deploy

### ⏳ Aguardando dados (não tocar até ter n≥30)
- H22 (absorção pós-fundo) — gate: n≥30 com `absorption_events > 0` + outcomes GMGN
- H23 (net_sol_flow pré-fundo) — gate: n≥30 com `h23_window='bottom'` + outcomes GMGN
- Rise window — gate: n≥30 com `rise_events > 0` + outcomes GMGN
- PÓS window — gate: n≥30 com `p60s_events > 0` + outcomes GMGN
- H24 (seller exhaustion) — ⏳ pendente implementação de acumulação de sellers únicos
- H25 (pool depth) — ⏳ pendente salvar `pool_quote_at_signal` no bounce

### Próxima análise (quando n≥30 bottom bounces com outcomes)
1. Abrir `/debug/flow-timeline` — ver os padrões que emergiram
2. Calcular WR por combinação PRÉ/RISE/PÓS (positivo/negativo/nulo)
3. Identificar qual janela discrimina mais
4. Só então pensar em score ou filtro

### Descobertas desta fase (observadas, não implementadas como filtro)
- H23 bounce-window: zona 3–10 SOL tem WR 35.7% (+12pp) mas 56% dos TPs têm H23 negativo — não pode ser gate binário
- bounce_pct >70%: WR 74% (n=23, +45pp) — maior discriminador encontrado, mas já coberto por BOUNCE_MIN_PCT
- Pearson H23 × bounce_pct = -0.085 → informação independente, porém fraca isoladamente
- Tokens com bounce >100% frequentemente têm H23 negativo (profit-taking durante a subida)

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
16. **GMGN nunca retorna dados de fill no query_order**: `filled_output_amount` e `filled_input_amount` chegam consistentemente como `null` mesmo com `state=confirmed`. Não é bug transitório — é comportamento padrão da GMGN API. Toda a lógica que dependia desses campos estava silenciosamente falhando. Solução: ler fill diretamente da blockchain via Helius `getTransaction`. A blockchain é a única fonte de verdade — qualquer intermediário pode não retornar o que você precisa.
17. **TradeMonitor era cego após 60min de janela quente**: dependia de `token_ticks` gerados pelo HotMonitor. Quando HotMonitor expira, ticks param → `if not ticks: return` → trade fica `open` para sempre. Trades de menos de $1 fechavam em minutos mas o sistema não sabia. Fix correto: verificar posição on-chain via `get_token_balance()`, não tentar inferir de dados que param de chegar.
18. **`condition_orders` na query_order é vazia**: GMGN não retorna status das condition_orders (TP/SL) quando se consulta a ordem de compra. `_check_via_gmgn()` que tentava ler esse campo é ineficaz. As condition_orders têm lifecycle independente da ordem de compra — precisariam de uma query separada com order_id do TP/SL (não exposto pela API). Blockchain é o único caminho confiável.
19. **SL=15% vs volatilidade pAMM**: em pools com liquidez pequena (~79 SOL), uma única transação de venda pode temporariamente empurrar o preço 15-20% abaixo antes do próximo buy reequilibrar. GMGN monitora price com granularidade alta → SL=15% dispara nesses spikes antes de qualquer recuperação. Testando SL=25% como threshold mais robusto a ruído. Aguardar n≥30 trades para validar EV vs SL=15%.
20. **mc_high em token_ticks não pode ser usado para trajectory analysis**: mesma raiz que o mc_peak inflado (nota 97 do CLAUDE.md). Em pools pAMM com ~79 SOL de liquidez, uma única transação de compra pode empurrar o preço AMM 200-300% por <1 candle antes de reverter. O `kline high` registra esse pico como real. Para análise de "quanto o token subiu enquanto segurava", usar apenas `mc` (close). `mc_high` só é válido para o backtester (detectar se TP foi atingido intra-vela — onde um spike real pode ter trigado a condition_order).
21. **GMGN trailing TP funciona corretamente**: verificação manual de 16 casos "trailing_falhou" mostrou que todos os casos com price action real (muitos ticks pré-exit) foram ou (a) exits lucrativos reais mal classificados no DB, ou (b) SL corretos onde mc_high estava inflado. Zero casos de falha real do trailing GMGN. O DB é que estava errado — TradeMonitor não capturava trailing exits após o HotMonitor expirar.
22. **WR corrigido: 32% (42/132)**: 4 trades classificados como `sl` eram na verdade trailing TPs (HUMANITY +56%, Kevin +311%, SLEEP +41%, DrawPoorly +46%). TradeMonitor era cego após 60min porque `if not ticks: return`. Com a correção via `get_token_balance()` os novos trades serão classificados corretamente. Para dados históricos: usar `/debug/fix-trailing-outcomes` + verificação manual.
23. **smart_wallets não discrimina WR em dados live**: smart=1→31%, smart=3→30%, smart=5+→19% (plano ou negativo). Contradiz análise anterior de n=6. FILTER_SMART_MIN=3 é conservador demais — pode estar rejeitando tokens válidos. Revisar após n≥50 trades com smart≥3 acumulados.
24. **bounce_pct >70% é o maior discriminador live**: WR 74% (n=23) vs 29% base (+45pp). Gradiente claro e monotônico (12% / 23% / 40% / 74%). Mecanismo plausível: bounce mais forte = mais compradores reais = mais sustentação. BOUNCE_MIN_PCT=0.50 é a mudança de maior impacto disponível sem código.
25. **Pipeline silencioso: instrumentar antes de refatorar**: quando um pipeline não produz logs, a hipótese errada é "o problema é arquitetural". A hipótese correta é "exceptions estão sendo swalladas silenciosamente ou não há logging nos pontos de descarte". Adicionar try/except + counter de eventos + logs em cada early-return resolve em minutos o que poderia ser dias de refactoring.
26. **Pool-account por intersecção elimina ATAs de usuários**: ATAs são mint-específicos mas usuário-específicos — cada swapper tem um ATA diferente. A intersecção de accountKeys de 5 TXs elimina qualquer conta que não apareça em TODOS os swaps, deixando apenas pool state PDA e vaults. Não requer conhecimento prévio do endereço do pool.
27. **Primeiro token limpo é o marco zero**: definir explicitamente o timestamp a partir do qual os dados são confiáveis (JOB 2026-05-21 00:41 UTC) cria um separador claro entre dados históricos corrompidos e dados novos. Qualquer análise de WR, bounce_pct ou filtros deve usar apenas dados após esse timestamp.
25. **Todo o histórico de WR do DB era ficção (sessão 5)**: TradeMonitor nunca acessou a blockchain. Usava `entry_expected = mc_bottom × 1.15` (não entrada real), `token_ticks` (parava após 60min), e aplicava `pnl_pct = +TRAILING_ACTIVATION_PCT` quando o trade expirava — sem verificar se o GMGN realmente saiu com lucro. A blockchain (via Helius `getTransaction`) é a única fonte que não mente. `exit_type='helius_sell_recovery'` no campo `exit_type` é o único indicador de dado confiável.
26. **Paginação determinística com cursor `before` vs janela deslizante**: qualquer endpoint de histórico deve paginar com cursor `before` último item processado, parando em timestamp floor. `?limit=N` sem cursor retorna janela deslizante — contagem muda a cada execução à medida que novas txs chegam. Para auditoria histórica, cursor é obrigatório.
27. **`db._cursor()` usa `RealDictCursor` — nunca indexar por posição**: ficar indexando `r[0]`, `r[1]` em código que usa esse cursor falha com `KeyError: 0`. Padrão do projeto: sempre usar `r['coluna']`. O erro é silenciado por `try/except` em generators → difícil de debugar. Verificar com `isinstance(row, dict)` em caso de dúvida.
28. **A janela temporal de uma métrica define o que ela mede (sessão 7)**: `net_sol_flow` em `[bounce-60s → bounce]` = momentum (quem comprou enquanto o preço subia). `net_sol_flow` em `[ts_min-60s → ts_min]` = absorção (quem segurou o dump). São métricas completamente diferentes. Sempre definir `window_end_ts` explicitamente em funções de janela rolante. Nunca usar `time.time()` como referência para métricas de absorção — o "agora" no contexto de análise de fundo é `ts_min`, não o momento do sinal.
29. **Tokens de bounce explosivo (>100%) frequentemente têm H23 negativo (sessão 7)**: durante um bounce de 200%, os early holders realizam lucro → `sell_vol > buy_vol` na janela do fundo. Isso não indica token ruim — indica que a absorção aconteceu mais cedo, fora da janela de 60s. H23 como gate binário bloquearia 42% dos TPs (5/12 com bounce >100%). Usar H23 como tier de confluência (A+ com H23 bottom positivo, A sem H23), nunca como gate obrigatório.
30. **`pnl_pct_real` é decimal no banco (sessão 7)**: armazenado como `0.75` para 75% de ganho. Display com `f"{pnl:+.1f}%"` mostra `+0.8%` que parece 0.8% mas é 80%. Qualquer novo endpoint deve usar `f"{pnl*100:+.1f}%"` para exibir corretamente. Trades "suspeitos" mostrando `+0.2%` com status=sl são artefatos de execução marginal acima do entry (exit price ≈ entry price), não TPs reais.
31. **O candle de transição bonding curve → pool é o pior dado do pipeline (sessão 9)**: quando um token migra, o GMGN kline registra um ou dois candles cobrindo a transição. O `low` desses candles é o preço DENTRO da bonding curve (muito menor que o MC de migração) — não é preço de pool real. Qualquer leitura de `low` sem filtrar `elapsed_sec < 90` vai capturar esse preço fictício como mc_min. Regra: em QUALQUER loop que lê kline de um token migrado, a PRIMEIRA linha após o check de elapsed deve ser `if elapsed_sec < 90: continue`. Sem essa linha, mc_min fica 40-80% abaixo do real → drop_from_mig inflado → bounce_pct inflado → sinal falso em token que nunca dumpou.
32. **Race condition em bounce_fired é bug de correctness, não performance (sessão 9)**: o guard `if state.get("bounce_fired"): return` na linha 208 de `_check_bounce` não é atômico — é apenas uma leitura. Em Python, o GIL garante atomicidade de operações simples mas NÃO de check-then-act. Entre o check e o set, outra thread pode passar pelo mesmo check. Em caso de double-fire: dois trades abertos no mesmo token, dois condition_orders criados, custo dobrado e risco dobrado. Fix correto: adquirir o lock, re-checar dentro do lock, fazer o set dentro do lock, sair do lock. Nunca confiar no GIL para exclusão mútua em lógica de negócio.
33. **`ts_migration` errado invalidava todos os filtros baseados em tempo (sessão 9)**: A.5 (max 20min), A.6 (freshness 5min) e elapsed_sec no bounce_signal todos dependem de `ts_migration` como anchor. Com `ts_migration = datetime.now()` (detecção), um token migrado há 15min entrava com `elapsed_sec = 0` → A.5 nunca bloqueava → A.6 calculava freshness do "fundo" como 0s (sempre passa). Os filtros temporais existiam no código mas eram neutralizados pela referência errada. Lição: qualquer timestamp derivado de evento externo deve usar o timestamp DO EVENTO (on-chain), nunca o momento de detecção pelo sistema.
