# NeideBOT — Roadmap

**Atualizado:** 2026-05-10  
**Estado:** Bot ativo a $1/trade · WR 15.1% (sem A.6) · aguardando WR real com filtros completos

---

## ✅ Concluído

| Item | Data | Resultado |
|------|------|-----------|
| Coleta de dados pós-migração | 2026-04 | 1175+ tokens, 1134 com recovery definido |
| H10 validada (time_to_bottom) | 2026-04 | +21.8pp — único preditor robusto |
| Backtester tick-level | 2026-05-01 | EV +14.7% (TP=100%, SL=15%, n=68) |
| Fase A — filtros A.2, A.3, A.5, A.6 | 2026-05 | WR estimado 29% pós-filtros |
| Trailing TP via condition_orders | 2026-05-09 | avg 159% vs 71% TP fixo |
| Calibração $1/trade (53 trades) | 2026-05-07/08 | WR 15.1% · friction = ATA rent |
| Auditoria on-chain (115 txs) | 2026-05-10 | Jito=$0, SL executa certo, ATA rent ~$0.20/SL |
| Bot relançado | 2026-05-10 | TRADING_ENABLED=true, CALIBRATION_MAX=999999 |
| Fix entry 50-100% acima do fundo | 2026-05-10 | `_precise_bottom_tick()` dispara `_check_bounce()` com kline close — elimina defasagem do poll trenches |
| HOT_PRECISE_SEC 30s → 5s | 2026-05-10 | Kline alinhado com trenches poll — mc_min sempre fresco na detecção |

---

## 🔜 Curto Prazo (próximas semanas)

### 0. Confirmar WR real com A.6 ativo ← **PRÓXIMO GATE**
**Problema:** A calibração de 53 trades foi feita SEM A.6 (adicionado 2026-05-09). O WR real com todos os filtros ativos (A.2+A.3+A.6) é desconhecido.

**O que medir:**
- WR ao longo de 30+ trades com filtros completos
- Frequência real de sinais válidos/dia (estimativa: 7-8/dia vs 24 brutos sem filtros)
- Confirmar se WR ≥ 20% antes de qualquer escala de size

**Ação:** Aguardar ~1 semana de dados. Não mudar nada enquanto coleta.

---

### 1. Re-entry — Segunda chance em tokens com TP anterior
**Ideia:** Se um token teve TP num bounce anterior, o projeto tem comunidade/momentum. Se cair novamente e fizer outro bounce com os mesmos filtros → entrar de novo.

**Por que é o multiplicador de frequência mais importante:**
- Adiciona 30-50% mais trades sem relaxar filtros
- Tokens com TP histórico já passaram pelo crivo de qualidade E demonstraram capacidade de bounce
- É a alavanca principal para atingir 15-20 sinais/dia (necessário para meta 1 BTC)

**Lógica:**
- Tokens com `exit_type = 'trailing_active'` ou `status = 'tp'` na tabela `trades` → lista de "tokens validados"
- Quando um desses tokens aparecer no HotMonitor novamente (nova migração ou novo dump), permitir re-entry
- Mesmo pipeline de bounce detection — `bounce_fired` é por sessão, não histórico

**Implementação:**
1. `db.get_tp_tokens()` — lista mints com pelo menos 1 TP histórico
2. Em `_check_bounce()`: se mint está na lista de TPs, logar diferente e não bloquear por `bounce_fired` histórico
3. Opcional: aumentar `CALIBRATION_AMOUNT_USD` para esses tokens (ex: 2× o size padrão)

---

### 2. Reclaim Rent automático
**Problema:** Cada SL deixa um ATA vazio com ~$0.20 preso. Atualmente requer clicar "Reclaim Rent" manualmente no GMGN.

**Implementação:**
- Após SL confirmado (TradeMonitor detecta `sl`), agendar `closeAccount` para o ATA do token via Solana RPC
- Recupera ~$0.20 automaticamente por SL trade
- Requer: `HELIUS_RPC_URL` + `WALLET_PRIVATE_KEY` (já mapeados no roadmap Helius+Jupiter)

---

### 3. Acumulação de dados H9
**Objetivo:** 30+ tokens na faixa `trigger_mc < mc_migration × 0.50` para validar se signal type 12 discrimina recovery.

**Estado:** +11.8pp na faixa <50% com n=14. Aguardando n≥30.

**Ação:** Apenas aguardar. Bot coletando sinais automaticamente a cada 5s.

---

## 📋 Médio Prazo

### 4. Escalar para $10/trade → $50/trade → $100/trade
**Gate único para cada salto:** WR ≥ 20% confirmado em ≥ 30 trades no size atual.

**Por que manter $50-100/trade como teto (não escalar mais):**
- O edge está no **volume de trades qualificados**, não no size
- A $100/trade + 10 sinais/dia + Jupiter: ~$17/trade EV → ~$62k/ano
- A $100/trade + 20 sinais/dia (com re-entry) + Jupiter: ~$124k/ano ✅ meta 1 BTC
- Acima de $200/trade: H7 (slippage) torna-se crítica — liq $10k não suporta sizes grandes

**Break-even por size:**
| Size | ATA rent | GMGN fees | Total friction | Break-even WR |
|------|----------|-----------|----------------|---------------|
| $1   | 20%      | 2%        | ~22%           | ~18%          |
| $10  | 2%       | 2%        | ~4%            | ~12%          |
| $50  | 0.4%     | 2%        | ~2.4%          | ~11%          |
| $100 | 0.2%     | 2%        | ~2.2%          | ~11%          |
| $100 (Jupiter) | 0% | 0.5% | ~0.5%       | ~9%           |

**Math para 1 BTC (meta):**
| Size | Sinais/dia | EV/trade | Ganho/dia | Ganho/ano |
|------|-----------|---------|----------|----------|
| $100 (GMGN) | 8 | $13 | $104 | ~$38k |
| $100 (GMGN) | 20 (com re-entry) | $13 | $260 | ~$95k |
| $100 (Jupiter) | 20 (com re-entry) | $17 | $340 | **~$124k ✅** |

---

### 5. Helius RPC + Jupiter Swap
**Por que:** GMGN cobra 1%×2 = 2% por round-trip + ATA rent não fechado. Jupiter = 0% platform fee + fecha ATA automaticamente.

**Complexidade:** 7/10 — ~20-25h de implementação.

**Pré-requisito:** WR confirmado a $10/trade. Não migrar infraestrutura antes de confirmar o edge.

**O que muda:**
- Novo `jupiter_client.py`: quote + swap + submit via Helius RPC
- `executor.py`: trocar `gmgn.swap()` por `jupiter_client.swap()`
- `TradeMonitor`: executar sell diretamente (sem `condition_orders`)
- ATA fechado automaticamente pelo Jupiter na venda → elimina o friction de $0.20/trade

**Friction estimada pós-migração a $100/trade:**
| Custo | Atual (GMGN) | Após (Jupiter+Helius) |
|-------|-------------|----------------------|
| Platform fee | 2% | ~0.5% (LP fee) |
| ATA rent | ~0.2% | $0 (Jupiter fecha ATA) |
| Tip/priority | ~$0.04 | ~$0.018 |
| **Total a $100** | **~$2.24 (2.2%)** | **~$0.52 (0.5%)** |

---

## 🔭 Longo Prazo

### 6. H7 — Validar liq mínima por size
Antes de escalar acima de $100/trade, confirmar que `liq_migration ≥ size × 30` previne slippage inaceitável. Regra sugerida: ao escalar size, subir `FILTER_LIQ_MIN` proporcionalmente.

### 7. H9 — Smart money signal como gate adicional
Se H9 for validada (n≥30, delta≥20pp), adicionar como filtro opcional: só entra se houver ≥1 signal type 12 com `trigger_mc < mc_migration × 0.80` durante o dump.

### 8. Multi-token monitoring ampliado
HotMonitor observa primeiros 60min pós-migração. Com re-entry ativo, expandir para detectar bounces secundários em tokens que já bouncearam — usando token_signals como trigger em vez de janela de tempo fixo.

---

## 📊 Métricas de Decisão

| Decisão | Gate |
|---------|------|
| Escalar $1 → $10 | ≥30 trades com filtros A.2+A.3+A.6 ativos, WR ≥ 20% |
| Escalar $10 → $50 | WR ≥ 20% confirmado em 20 trades a $10 |
| Escalar $50 → $100 | EV_real > 0 confirmado em 20 trades a $50 |
| Migrar GMGN → Jupiter | WR confirmado a $50+ + disponibilidade de implementação |
| Ativar re-entry | ≥5 tokens com TP histórico disponíveis + 1 semana de dados A.6 |
| Ativar H9 como gate | n≥30 na faixa <50%, delta_pp ≥ 20pp |
| Ajustar FILTER_LIQ_MIN | ao escalar size acima de $100/trade |

---

## 🧠 Princípios

- **Volume, não size.** O edge é frequência de trades qualificados. Manter $50-100/trade e maximizar sinais válidos via re-entry.
- **Dados antes de features.** Não construir re-entry antes de confirmar WR com A.6 ativo (30+ trades).
- **Escalar depois de confirmar.** Cada salto de size exige validação prévia no size menor.
- **Friction primeiro.** Jupiter/Helius é pré-requisito para $100+/trade ser viável — friction GMGN consome parte do edge a volumes altos.
- **One change at a time.** Não mudar size + infraestrutura + filtros ao mesmo tempo — impossível isolar o que funcionou.
