# NeideBOT — Roadmap

**Atualizado:** 2026-05-10  
**Estado:** Bot ativo a $1/trade · WR 15.1% · ATA rent = principal friction

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

---

## 🔜 Curto Prazo (próximas semanas)

### 1. Re-entry — Segunda chance em tokens com TP anterior
**Ideia:** Se um token teve TP (+X%) num bounce anterior, o projeto tem comunidade/momentum. Se cair novamente e fizer outro bounce com os mesmos filtros → entrar de novo.

**Lógica:**
- Tokens com `exit_type = 'trailing_active'` ou `status = 'tp'` na tabela `trades` → lista de "tokens validados"
- Quando um desses tokens aparecer no HotMonitor novamente (nova migração ou novo dump), elevar prioridade
- Mesmo pipeline de bounce detection, mas sem o filtro de `bounce_fired = True` por sessão — permitir re-entry

**Implementação:**
1. `db.get_tp_tokens()` — lista mints com pelo menos 1 TP histórico
2. Em `_check_bounce()`: se mint está na lista de TPs, logar diferente e não penalizar filtros mais apertados
3. Opcional: aumentar `CALIBRATION_AMOUNT_USD` para esses tokens (ex: 2× o size padrão)

**Por que faz sentido:** tokens com TP já passaram pelo crivo de qualidade E demonstraram capacidade de bounce. Base racional, não FOMO.

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

### 4. Escalar para $10/trade
**Gate:** Confirmar WR estável em ≥20 trades com filtros A.2/A.3/A.5/A.6 ativos.

**Por que ainda $1:** ATA rent é $0.20 independente do size. A $1/trade = 20% do trade. A $10/trade = 2%. Mas antes de escalar, confirmar que o WR pós-filtros realmente é 25-29% como estimado.

**Break-even por size:**
| Size | ATA rent | GMGN fees | Total friction | Break-even WR |
|------|----------|-----------|----------------|---------------|
| $1   | 20%      | 2%        | ~22%           | ~18%          |
| $5   | 4%       | 2%        | ~6%            | ~14%          |
| $10  | 2%       | 2%        | ~4%            | ~12%          |

---

### 5. Helius RPC + Jupiter Swap
**Por que:** GMGN cobra 1%×2 = 2% por round-trip + ATA rent não fechado. Jupiter = 0% platform fee + fecha ATA automaticamente.

**Complexidade:** 7/10 — ~20-25h de implementação.

**Pré-requisito:** Confirmar WR a $10/trade antes de migrar infraestrutura.

**O que muda:**
- Novo `jupiter_client.py`: quote + swap + submit via Helius RPC
- `executor.py`: trocar `gmgn.swap()` por `jupiter_client.swap()`
- `TradeMonitor`: executar sell diretamente (sem `condition_orders`)
- ATA fechado automaticamente pelo Jupiter na venda → elimina o friction de $0.20/trade

**Friction estimada pós-migração a $10/trade:**
| Custo | Atual (GMGN) | Após (Jupiter+Helius) |
|-------|-------------|----------------------|
| Platform fee | 2% | ~0.5% (LP fee) |
| ATA rent | ~2% | $0 (Jupiter fecha ATA) |
| Tip/priority | ~$0.10 | ~$0.04 |
| **Total a $10** | **~$0.60 (6%)** | **~$0.09 (0.9%)** |

---

## 🔭 Longo Prazo

### 6. Multi-token monitoring ampliado
Atualmente o HotMonitor observa os primeiros 60min pós-migração. Com dados suficientes, expandir para detectar bounces secundários (2h-6h) em tokens que já bouncearam uma vez.

### 7. H9 — Smart money signal como gate adicional
Se H9 for validada (n≥30, delta≥20pp), adicionar como filtro opcional: só entra se houver ≥1 signal type 12 com `trigger_mc < mc_migration × 0.80` durante o dump.

### 8. Position sizing dinâmico
Base: size fixo por trade. Evolução: aumentar size em tokens com:
- TP histórico (re-entry — item 1)
- H9 confirmado (signal type 12 no dump)
- time_to_bottom < 15min (zona de recovery 61%)

---

## 📊 Métricas de Decisão

| Decisão | Gate |
|---------|------|
| Escalar $1 → $5 | ≥30 trades com filtros A ativos, WR ≥ 20% |
| Escalar $5 → $10 | EV_real > 0 confirmado em 30 trades |
| Migrar GMGN → Jupiter | WR confirmado a $10 + disponibilidade de implementação |
| Ativar re-entry | ≥5 tokens com TP histórico disponíveis + validação manual |
| Ativar H9 como gate | n≥30 na faixa <50%, delta_pp ≥ 20pp |

---

## 🧠 Princípios

- **Dados antes de features.** Não construir re-entry antes de ter 10+ tokens com TP para validar o padrão.
- **Escalar depois de confirmar.** Cada salto de size exige validação prévia no size menor.
- **Friction primeiro.** Antes de otimizar sinais, garantir que a execução não consome o edge.
- **One change at a time.** Não mudar size + infraestrutura + filtros ao mesmo tempo — impossível isolar o que funcionou.
