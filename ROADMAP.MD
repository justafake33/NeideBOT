# NeideBOT — ROADMAP

> **Regra:** Não avance para o próximo passo sem validar o anterior com dados reais.

---

## Prioridade prática (ordem real)

| # | Fase | Passo |
|---|------|-------|
| 1 | 0 | Sentry |
| 2 | 0 | Logs estruturados |
| 3 | 1 | Holders API |
| 4 | 1 | Security API |
| 5 | 2 | Smart Flow |
| 6 | 3 | Timing Engine |
| 7 | 4 | Score modular |
| 8 | 5 | Cluster Engine |
| 9 | 6 | Execution Engine |
| 10 | 7 | Risk Engine |
| 11 | 8 | Arquitetura separada |
| 12 | 9 | Dataset research |

---

## FASE 0 — Blindagem

**Objetivo:** garantir estabilidade antes de crescer.

### 0.1 — Sentry
- [x] Criar `monitoring.py`
- [x] Configurar `SENTRY_DSN`
- [x] Capturar exceptions: scanner, scheduler, hot monitor, executor

→ Qualquer crash vira evento rastreável.

### 0.2 — Logs estruturados
- [x] Scanner: token encontrado / aprovado / reprovado / motivo
- [x] HotMonitor: fundo detectado / rug detectado / recuperação detectada
- [ ] Executor: entrada / TP / SL executados *(Fase 6)*

→ Entender comportamento em produção.

### 0.3 — Backup de produção
- [ ] ~~Branch `stable-production`~~ *(descartado — Railway tem rollback nativo)*

---

## FASE 1 — Intelligence Layer

**Objetivo:** melhorar qualidade da seleção.

### 1.1 — Holders API
Buscar holders no momento de 90% BC.

Salvar:
- `smart_supply_pct` — % do supply em mãos smart
- `insider_hold_pct` — % insider
- `whale_concentration` — concentração de whales

→ Filtrar bombas ruins.

### 1.2 — Security API
Salvar:
- `honeypot_risk`
- `mint_authority`
- `freeze_authority`
- `lp_lock`

→ Reduzir rugs.

### 1.3 — Smart Wallet Quality
Hoje: contagem bruta (`smart_degen_count`).

Novo — separar por:
- Wallet age
- Wallet ROI
- Wallet consistency
- Wallet size

Criar: **Smart Quality Score**

→ Smart wallet deixa de ser métrica rasa.

---

## FASE 2 — Flow Engine

**Objetivo:** sair de snapshot e entrar em fluxo contínuo.

### 2.1 — Smart Flow Tracking
Monitorar compras e vendas de smart wallets em tempo real.

Métrica: **Smart Flow Delta** = compras − vendas

→ Detectar absorção real.

### 2.2 — KOL Flow
Monitorar wallets influentes.

Pergunta: quem relevante entrou?

→ Antecipação narrativa.

### 2.3 — Whale Flow
Monitorar wallets grandes.

Pergunta: whales acumulando ou distribuindo?

→ Entender pressão futura.

---

## FASE 3 — Timing Engine

**Objetivo:** melhorar ponto de entrada.

### 3.1 — Entry Zone Dinâmica
Hoje: fixo em −30%.
Novo: zona entre −25% e −40%.

→ Flexibilidade por contexto.

### 3.2 — Dump Velocity
Medir velocidade da queda.

Pergunta: queda acelerando ou desacelerando?

→ Evitar faca caindo.

### 3.3 — Bounce Strength
Medir força da reação inicial.

→ Confirmação de fundo.

### 3.4 — Seller Exhaustion
Sinais: volume cai + queda desacelera + smart absorvendo.

→ Entrada mais precisa.

---

## FASE 4 — Score Engine

**Objetivo:** formalizar a decisão em score modular.

### 4.1 — Quality Score
Base: holders, security, distribution.

### 4.2 — Flow Score
Base: smart flow, KOL flow, whale flow.

### 4.3 — Risk Score
Base: rug ratio, concentração, liquidez.

### 4.4 — Timing Score
Base: velocity, bounce strength, seller exhaustion.

→ Score composto, modular e auditável.

---

## FASE 5 — Cluster Engine

**Objetivo:** parar de tratar todos os tokens iguais.

| Cluster | Perfil |
|---------|--------|
| 5.1 | High smart / Low sniper |
| 5.2 | High sniper / Low smart |
| 5.3 | High concentration |
| 5.4 | High liquidity |

Cada cluster: entrada diferente, risco diferente, saída diferente.

---

## FASE 6 — Execution Engine

**Objetivo:** melhorar execução de ordens.

### 6.1 — Condition Orders
Entrar com TP e SL já embutidos.

→ Menos risco operacional.

### 6.2 — Partial Take Profits: 
exemplo:
25% em +20%
25% em +40%
50% trailing stop


→ Maximizar runners.

### 6.3 — Trailing Stop
Proteção dinâmica de posição.

→ Capturar pumps longos.

---

## FASE 7 — Risk Engine

**Objetivo:** proteger capital após entrada.

Monitorar:
- 7.1 — Smart sells
- 7.2 — Whale sells
- 7.3 — Redução de liquidez
- 7.4 — Emergency exit (saída antecipada)

→ Sair antes do mercado perceber.

---

## FASE 8 — Arquitetura

**Objetivo:** escalar sem acoplamento.

### 8.1 — Separar processos
Hoje: Flask + scanner num processo.
Novo: web / scanner / monitor / executor separados.

### 8.2 — RAM Cache
Decisão em RAM. Persistência em banco.

### 8.3 — FSM (Finite State Machine)
Estados por token: WATCHLIST → PREQUALIFIED → MIGRATED → DUMPING
→ ENTRY_ZONE → POSITION_OPEN → EXITED


---

## FASE 9 — Dataset Próprio

**Objetivo:** construir edge irreproduzível.

Coletar 1000+ migrações completas.

Responder:
- Qual cluster reverte melhor?
- Qual dump é saudável?
- Qual smart flow realmente funciona?

> Esse dataset é o ativo principal. Não tem atalho.
