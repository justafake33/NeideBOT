# Auditoria comparativa: bots open source de memecoins vs NeideBOT

Data da análise: 2026-06-06.

## Escopo e metodologia

Esta auditoria compara o NeideBOT com repositórios públicos de bots Solana/Pump.fun/memecoin encontrados no GitHub. O objetivo é estudar arquitetura e ideias, não operar bots externos.

Restrições aplicadas:

- Nenhum bot externo foi executado.
- Nenhum `npm install`, `pip install`, `cargo run`, `uv sync` ou binário externo foi rodado.
- A tentativa de `git clone --depth 1` em `/tmp/memecoin_repos` falhou por limitação de rede do ambiente (`CONNECT tunnel failed, response 403`), então a leitura foi feita via páginas públicas do GitHub, arquivos raw disponíveis e GitExtract/DeepWiki quando úteis.
- Como alguns repositórios pequenos têm código mínimo, README promocional ou arquivos sensíveis, as conclusões abaixo diferenciam “ideia útil” de “base confiável”.

Fontes principais lidas:

- `chainstacklabs/pumpfun-bonkfun-bot`: <https://github.com/chainstacklabs/pumpfun-bonkfun-bot>
- `PUMPFUN-max/pumpfun-bot`: <https://github.com/PUMPFUN-max/pumpfun-bot>
- `TreeCityWes/Pump-Fun-Trading-Bot-Solana`: <https://github.com/TreeCityWes/Pump-Fun-Trading-Bot-Solana>
- `AgentAashik/Meme-Coin-Trading-Bot-`: <https://github.com/AgentAashik/Meme-Coin-Trading-Bot->
- `sayedadrianodv/Banana-bot`: <https://github.com/sayedadrianodv/Banana-bot>
- `lonelybeanz/solana-bot`: <https://github.com/lonelybeanz/solana-bot>
- `FLOCK4H/Mew-X`: <https://github.com/FLOCK4H/Mew-X>

Observação: `solsniperr/sol-sniper-bot` apareceu na busca inicial, mas a página não ficou acessível de forma confiável durante esta rodada. Em vez de inferir comportamento sem leitura suficiente, ele foi substituído por `TreeCityWes/Pump-Fun-Trading-Bot-Solana`, que é público e diretamente relevante a Pump.fun.

## Resumo do que o NeideBOT já faz bem

O NeideBOT já está mais sofisticado do que a maioria dos repositórios pequenos analisados em cinco frentes:

1. **Estratégia específica pós-migração**: o foco não é comprar qualquer lançamento; é observar tokens que completaram bonding curve, migraram para pAMM/PumpSwap, dumpam e só então entram em bounce.
2. **Qualidade de filtros**: usa sinais GMGN como `rug_ratio`, bundler, bot degen, sniper hold, rat trader, top 10 holders, smart degens e wash trading antes de aceitar um token.
3. **Telemetria e aprendizado histórico**: possui PostgreSQL, dashboards Flask, armazenamento de ticks/sinais/trades e hipóteses documentadas em `CODEX_BRIEF.md`.
4. **Preço realtime pAMM**: tem parser Helius/eRPC para eventos de swap Pump.fun pAMM e mede absorção/fluxo via janelas locais.
5. **Execução protegida por GMGN**: usa swap GMGN com `condition_orders` para trailing TP e SL embutidos no corpo da ordem, evitando parte da fragilidade de exits puramente locais.

## Matriz comparativa

| Capacidade | NeideBOT | Repositórios externos | Diagnóstico |
|---|---|---|---|
| Detecção de migração Pump.fun/PumpSwap | Sim, via GMGN trenches e Helius pAMM | Chainstack tem listeners próprios; Mew-X tem Pump.fun/Pump.fun AMM | NeideBOT é forte, mas depende bastante de GMGN para discovery. |
| Sniping em token recém-criado / slot 0 | Não é foco | PUMPFUN-max, Chainstack, Mew-X | Possível lacuna estratégica, mas desalinhada com tese atual de bounce pós-migração. |
| Múltiplas fontes de discovery | Principalmente GMGN + Helius/eRPC | Chainstack usa logs/block/geyser/PumpPortal; Banana fala em múltiplas fontes | Lacuna real: redundância de discovery e auditoria de divergência entre fontes. |
| Execução própria on-chain | Não, delega GMGN | Chainstack/Mew-X constroem instruções; PUMPFUN-max spamma TXs | Lacuna opcional: GMGN reduz complexidade, mas cria dependência, latência e caixa-preta. |
| Priority fee/tip dinâmica | Parcial, env fixo em GMGN | Chainstack/Mew-X destacam priority fee dinâmica/SWQoS/Jito/Blox/etc. | Lacuna importante para latência e fill quality. |
| Estratégia de partial exits/moonbag | Trailing TP/SL full position via GMGN | TreeCityWes vende 50%, depois 75% do restante e mantém moonbag | Lacuna de gestão de posição: saída em camadas pode melhorar payoff de caudas. |
| Creator/dev analytics | Pouco explícito | Mew-X usa creators, CEX funding, volume, high MC e dev-alone checks | Lacuna relevante para separar bounce orgânico de dev/bundler dump. |
| Filtros de atividade no slot inicial | Não | Mew-X usa TIZ/DBF/Digged; PUMPFUN-max mira entry rápida | Lacuna para features pre-migration ou contexto de launch. |
| Alert-only / human-in-the-loop | Telegram básico | Banana é alert-first, com mensagens e milestones | NeideBOT poderia ter modo shadow/alerta mais rico para validar hipóteses sem operar. |
| Rate limiting/retry de RPC próprio | GMGN client tem tratamento de rate limit; Helius WS reconecta | Chainstack documenta token bucket, retries e sessão compartilhada | Lacuna moderada: formalizar budgets de chamada por provedor e métrica de throttling. |
| Testnet/sim/paper mode explícito | Há análise/backfill, mas modo operacional sim não parece central | AgentAashik documenta testnet; Mew-X tem `MODE=sim/trade` | Lacuna de segurança operacional e validação paralela. |
| Monitoramento de fill/exit real | Sim, GMGN query + recover fill | Pequenos repos são frágeis; Chainstack tem position manager | NeideBOT é forte, mas o briefing aponta divergência DB vs GMGN que ainda precisa governança. |

## Leitura por repositório

### 1. Chainstack `pumpfun-bonkfun-bot`

O Chainstack é o repositório mais útil para comparação técnica. Ele não depende de APIs de terceiros para o core, organiza a lógica por plataformas (`pump_fun`, `lets_bonk`), tem IDLs vendored de Pump.fun/PumpSwap, listeners por logs/block/geyser/PumpPortal e configs YAML por bot.

Pontos que o NeideBOT já cobre melhor:

- Tese pós-migração mais específica e empiricamente documentada.
- Banco e dashboards focados em hipóteses reais de WR/EV.
- Absorção pAMM e fluxo de SOL conectados ao trigger de bounce.
- Execução com TP/SL atômicos via GMGN, enquanto muitos bots externos deixam exit local ou genérico.

O que podemos estar negligenciando:

- **Discovery redundante**: Chainstack mantém vários listeners para comparar velocidade e cobertura. NeideBOT depende muito de GMGN trenches para entrada na watchlist e deveria medir divergência GMGN vs logsSubscribe/blockSubscribe/geyser.
- **IDL/account-list governance**: Chainstack registra que upgrades Pump.fun mudaram contas exigidas e que IDL incompleta pode mascarar erro real. Mesmo usando GMGN, NeideBOT parseia pAMM e deriva PDA; portanto deveria versionar explicitamente IDL/upstream commit e ter testes de regressão com transações recentes.
- **Rate limiting formal**: Chainstack documenta token bucket, retries 429 e sessão compartilhada. NeideBOT tem tratamento de rate limit no GMGN, mas não há um budget cross-provider formal para GMGN/Helius/eRPC/DexScreener.
- **Execução de fallback**: GMGN é cômodo, mas é dependência única para trade. Uma rota própria mínima para venda de emergência ou unwind poderia reduzir risco operacional.

### 2. PUMPFUN-max `pumpfun-bot`

É um exemplo educacional simples em Rust: monitora moedas novas, compra, espera 10 segundos e vende; menciona spam de transações e dependência da qualidade do nó.

Pontos que o NeideBOT já cobre melhor:

- Não faz spam cego em launch; espera uma configuração pós-migração com filtros.
- Tem telemetria, banco e análise de resultados.
- Usa SL/TP e gate por Helius/absorção em vez de uma pausa fixa.

O que podemos estar negligenciando:

- **Métrica explícita de time-to-fill e slippage efetiva**: mesmo que NeideBOT não faça spam de TX, o problema central é entrar 30%-50% acima do fundo. Devemos medir `ts_signal -> ts_order_sent -> ts_fill`, preço esperado vs fill, prioridade/tip e congestionamento.
- **Controle de envio sob congestionamento**: o bot simples tenta compensar latência com spam; NeideBOT deveria compensar de forma mais limpa com prioridade dinâmica, quote freshness e cancelamento se o fill esperado ficar fora da janela.

### 3. TreeCityWes `Pump-Fun-Trading-Bot-Solana`

O repo usa scraping/Selenium e estratégia simples de market cap/bonding curve: realiza parcial em +25%, realiza outra parte em novo +25%, stop de -10%, vende grande parte em determinado progresso de bonding curve e mantém moonbag.

Pontos que o NeideBOT já cobre melhor:

- Não depende de scraping de UI como fonte crítica.
- Entra pós-migração, não em bonding curve inicial.
- Filtros e telemetria são muito mais ricos.

O que podemos estar negligenciando:

- **Saídas parciais e moonbag**: NeideBOT tende a tratar posição como 100% trailing/SL. Para memecoin, a distribuição de retornos tem cauda longa; realizar uma parte cedo e deixar moonbag pode melhorar expectativa sem aumentar WR.
- **Gestão por estágios**: em vez de um único trailing activation/drawdown, testar: `sell 30%-50% em +X`, mover stop para break-even, deixar restante em trailing largo.
- **Backtest de payoff, não só WR**: simular o impacto em EV de partial exits usando ticks Helius/GMGN limpos.

### 4. AgentAashik `Meme-Coin-Trading-Bot-`

O código é muito pequeno e didático: DEX Screener a cada minuto, filtros por liquidez/volume/idade, Jupiter quote/swap, WebSocket de preço e PostgreSQL.

Pontos que o NeideBOT já cobre melhor:

- O scanner GMGN/pAMM é mais alinhado a Pump.fun do que polling genérico de DEX Screener.
- Filtros de smart money/bundler/sniper/wash/rug são superiores a filtros básicos de liquidez/volume/idade.
- A execução GMGN com condition orders é mais robusta que swap Jupiter simplificado.

O que podemos estar negligenciando:

- **Fonte externa independente de validação**: mesmo que DexScreener seja lento para entrada, pode ser útil como validador de liquidez/pair/price para detectar inconsistência GMGN/Helius.
- **Modo testnet/paper explícito**: o README foca em testnet. NeideBOT deveria ter `TRADE_MODE=paper|live` ou shadow mode padronizado, persistindo “ordens que teriam sido feitas” com fill model.
- **Schema mínimo de auditoria**: apesar de simples, o repo lembra que cada decisão deveria ter dados de filtro, quote, trade e outcome rastreáveis em uma tabela limpa separada do outcome corrompido.

### 5. Banana-bot

O README promete alertas em tempo real, agregação de sinais de múltiplas plataformas, filtros de market cap/volume/holders/idade/top-10, rug avoidance e mensagens Telegram. O código raw disponível é placeholder, então o valor está mais na ideia de produto/operacional do que na implementação.

Pontos que o NeideBOT já cobre melhor:

- Implementação real, com scanner, executor, monitor, DB e Helius.
- Filtros on-chain/off-chain mais específicos.
- Não é apenas alerting; fecha loop de execução e outcome.

O que podemos estar negligenciando:

- **Alert-first para pesquisa**: um modo Telegram mais rico poderia enviar candidatos rejeitados/aceitos com motivo, gráfico compacto, links e tags de hipótese. Isso ajudaria a validar filtros sem aumentar trades reais.
- **Rejeição explícita de unknown/out-of-range**: Banana rejeita top-10 desconhecido/out-of-range. NeideBOT hoje passa alguns filtros se campo está `None`; deveríamos testar se “unknown” é sinal negativo.
- **Milestone/PnL cards**: menos crítico, mas útil para revisão humana rápida de trades e caudas vencedoras.

### 6. lonelybeanz `solana-bot`

Repo em Go, descrito como sniper/copytrading/arbitragem demo. O README fala em streams via BlockRazor, node dedicado ou gRPC para push de transações, e serviço de envio de ordens.

Pontos que o NeideBOT já cobre melhor:

- Estratégia e filtros são muito mais explícitos.
- Há persistência e análise de outcome.
- Helius/eRPC já cobre parte do stream realtime.

O que podemos estar negligenciando:

- **Copytrading/whale-following**: NeideBOT usa `smart_degen_count` e GMGN token_signal, mas não parece manter uma base própria de wallets vencedoras/ruins, nem copiar/contrariar flows por wallet.
- **Separação ingestão vs envio de ordens**: o projeto Go sugere pipeline de stream e serviço de order routing. NeideBOT pode se beneficiar de módulos mais isolados: discovery, signal, quote/fill, risk, execution, audit.
- **gRPC/vendor diversity**: se eRPC/Helius falhar ou atrasar, não há evidência de comparação contínua com outro stream.

### 7. FLOCK4H `Mew-X`

Mew-X é pequeno em reputação, mas o README lista várias ideias relevantes: Rust, PostgreSQL, Pump.fun/Pump.fun AMM, SWQoS, Blox/Jito/Helius/Temporal/ZeroSlot/NextBlock, durable nonce, gRPC/WebSocket, creator analytics, slot-0, dip em token migrado, filtros configuráveis, avoid duplicates e cálculo de priority fee.

Pontos que o NeideBOT já cobre melhor:

- Estratégia de dip pós-migração está mais documentada e mensurada.
- Usa dados GMGN de qualidade prontos, sem precisar reconstruir tudo on-chain.
- Tem dashboards e histórico de hipóteses.

O que podemos estar negligenciando:

- **Creator analytics**: Mew-X considera creators financiados por CEX, volume histórico, maior market cap histórico, número de mints, dev holdings e se o dev está “sozinho” no slot. Essa é talvez a lacuna mais interessante para NeideBOT.
- **Filtros do slot/launch como contexto do pós-migração**: mesmo entrando depois da migração, features do nascimento do token podem explicar se o bounce tem comprador real ou é continuação de dump.
- **Priority fee e SWQoS dinâmicos**: NeideBOT usa fee/tip env em GMGN, mas não parece adaptar por líder/slot/congestionamento/urgência do sinal.
- **Modo `sim|trade` mutável**: útil para shadow testing da mesma lógica em produção sem capital.

## Principais lacunas/negligências encontradas

### Prioridade 1 — Discovery redundante e observabilidade de latência

NeideBOT deveria medir, em tabela própria, para cada token/candidato:

- quando GMGN detectou near completion/completed;
- quando logsSubscribe/geyser/eRPC viu mint/migração/swap;
- quando DexScreener ou outra fonte indexou o par;
- diferença entre `ts_signal`, `ts_order_sent`, `ts_order_accepted`, `ts_fill` e `ts_first_exit_condition`;
- preço/MC no sinal vs quote vs fill.

Motivo: o briefing diz que a entrada real está 30%-50% acima do fundo. Sem uma decomposição de latência/fill, não sabemos se o problema é trigger atrasado, GMGN quote, GMGN execução, prioridade, slippage ou congestão.

### Prioridade 2 — Partial exits/moonbag e payoff engineering

Comparar, com os ticks limpos, pelo menos estes modelos:

1. Atual: 100% trailing após ativação.
2. Parcial conservador: vender 50% em +35% ou +50%, stop do restante em break-even, trailing no restante.
3. Moonbag: vender 70%-90% em primeiro alvo e manter 10%-30% com trailing largo.
4. Time stop: se não atingir +X em N segundos após entrada, vender antes do SL.

Motivo: com WR baixo, melhorar a captura da cauda pode ser tão importante quanto filtrar mais entradas.

### Prioridade 3 — Creator/dev analytics

Criar uma tabela `creator_profiles`/`wallet_profiles` com:

- creator wallet;
- funding source aproximada, se disponível;
- SOL recebido antes do launch;
- histórico de mints;
- taxa de tokens que migraram;
- maior MC histórico;
- comportamento do dev após migração;
- concentração inicial/dev holding;
- score de creator ruim/bom.

Motivo: Mew-X enfatiza creator analytics e “dev alone/bundler” como edge. NeideBOT tem filtros agregados GMGN, mas não parece aprender reputação própria de wallets.

### Prioridade 4 — Unknown-is-risk como hipótese

Hoje, filtros como rug/top10/sniper só bloqueiam quando há valor e o valor excede threshold. Vale testar se `None`/unknown em top10, holders, liquidity, rug, sniper e pool quote é sinal negativo.

Motivo: Banana-bot declara rejeitar unknown/out-of-range; em microcaps, ausência de dado pode indicar indexação ruim, contrato/pair novo demais ou fonte falhando.

### Prioridade 5 — Priority fee/tip dinâmicos e cancelamento por quote freshness

Implementar auditoria e eventual política:

- fee/tip por urgência do sinal;
- slippage/fill guard por diferença sinal→quote;
- cancelamento se fill previsto ultrapassar `max_entry_above_bottom`;
- registro do congestionamento/slot/leader quando possível.

Motivo: Chainstack e Mew-X tratam propagação/priority/SWQoS como assunto central; NeideBOT usa GMGN, mas ainda sofre se entra longe do fundo.

### Prioridade 6 — Shadow/paper mode operacional

Adicionar um modo que roda a pipeline completa, grava candidato, quote simulado, fill model e exit simulado, mas não envia ordem. Isso permitiria testar filtros novos por dias antes de ativar capital.

Motivo: vários repos educacionais destacam testnet/sim; NeideBOT já tem histórico, mas a divergência DB vs GMGN mostra que o outcome simulado precisa ser formalmente separado do outcome executado.

### Prioridade 7 — IDL/versioning de parser pAMM

Versionar explicitamente as premissas do parser pAMM:

- upstream IDL commit/hash;
- layout esperado de `BuyEvent`/`SellEvent`;
- fixtures de transações recentes;
- teste que falha se offsets mudarem;
- nota operacional para upgrades Pump.fun.

Motivo: Chainstack documenta mudanças de conta/program upgrade e erro enganoso por IDL incompleta. NeideBOT parseia eventos inline; se layout muda, o sinal pode degradar silenciosamente.

## Conclusão honesta

O NeideBOT não parece estar negligenciando “mais filtros genéricos”. Ele já tem filtros melhores que os repos pequenos. As negligências mais prováveis são de **infraestrutura de edge**:

1. decompor latência/fill;
2. redundância de fontes;
3. profile próprio de creator/wallet;
4. saída parcial/moonbag;
5. fee/tip dinâmica;
6. shadow mode limpo;
7. testes/versionamento do parser pAMM.

A recomendação de implementação é começar por medição, não por trocar a estratégia:

1. Criar `execution_latency_audit` e registrar todos os timestamps/preços sinal→fill.
2. Criar simulação de partial exits sobre ticks limpos.
3. Criar coleta mínima de creator wallet e histórico por token.
4. Testar hipótese `unknown-is-risk` em dados históricos.
5. Só depois mexer em execução/fee dinâmica ou adicionar fallback on-chain próprio.

## O que um big player faria

Um big player não trataria isso como “achar o filtro mágico”. Ele trataria como uma operação de microestrutura, dados, risco e execução. O playbook provável seria:

### 1. Separar pesquisa, simulação, shadow e produção

- **Research**: gera hipóteses em notebook/script offline, sem tocar capital.
- **Backtest limpo**: usa somente dados disponíveis no instante da decisão, evitando vazamento de futuro.
- **Shadow mode**: roda o bot em tempo real, grava sinais e fills simulados, mas não envia ordem.
- **Canary live**: opera com size mínimo, limites diários e kill switch.
- **Scale-up gradual**: só aumenta size depois de estabilidade estatística e operacional.

Para o NeideBOT, isso significa que qualquer hipótese nova deve passar por uma fila: `backtest -> shadow -> canary -> live`, com tabelas separadas para outcome simulado e outcome GMGN real.

### 2. Medir primeiro a latência do dinheiro, não só a latência do preço

Um player grande decomporia cada trade em timestamps auditáveis:

- `ts_discovery_source`: quando cada fonte viu o token/migração.
- `ts_signal`: quando a estratégia decidiu comprar.
- `ts_quote_request` e `ts_quote_response`: quanto demorou para cotar.
- `ts_order_sent`, `ts_order_accepted`, `ts_fill`: caminho real da ordem.
- `mc_bottom`, `mc_signal`, `mc_quote`, `mc_fill`: degradação de preço em cada etapa.
- `slot`, `priority_fee`, `tip`, `slippage_bps`, `route`, `provider`: contexto de execução.

Se a entrada está 30%-50% acima do fundo, um big player perguntaria: isso vem do sinal, da quote, da fila do provedor, da prioridade, do slippage, ou de liquidez insuficiente? Sem essa decomposição, trocar filtro é tentativa e erro.

### 3. Ter múltiplas fontes e medir divergência, não escolher uma fonte “favorita”

O big player não dependeria de uma única fonte de discovery. Ele compararia:

- GMGN trenches/completed.
- Helius/eRPC logs ou transaction stream.
- Geyser/grpc privado, se disponível.
- DexScreener/GeckoTerminal apenas como validação lenta.
- Parser próprio de Pump.fun/PumpSwap para eventos críticos.

A decisão não precisa usar todas as fontes, mas o sistema precisa gravar qual fonte viu primeiro, qual atrasou, qual falhou e qual gerou falso positivo. Isso transforma “GMGN está atrasando?” em métrica objetiva.

### 4. Construir reputação própria de wallets e creators

Um player institucional tentaria sair do nível “token tem smart_degen_count >= 2” e construiria inteligência própria:

- histórico do creator;
- funding source do creator/dev;
- quantos tokens anteriores migraram;
- maior MC histórico dos tokens do creator;
- comportamento do dev depois da migração;
- concentração inicial e wallets relacionadas;
- carteiras que compram fundos bons vs carteiras que aparecem em rugs;
- wallets que vendem antes de dumps recorrentes.

A tese pós-migração do NeideBOT depende de identificar comprador real no fundo. Perfil próprio de wallets pode separar bounce orgânico de continuação de dump melhor do que filtros agregados.

### 5. Tratar execução como produto separado da estratégia

Um big player teria um módulo de execução independente, com políticas explícitas:

- quote freshness máxima;
- `max_entry_above_bottom`;
- slippage dinâmico por liquidez/volatilidade;
- priority fee/tip dinâmicos;
- cancelamento se a ordem não for preenchida dentro de uma janela;
- fallback de unwind/venda emergencial;
- comparação GMGN vs rota própria apenas em shadow até provar vantagem.

Mesmo se GMGN continuar sendo o executor principal, o NeideBOT deveria auditar se GMGN está entregando fill competitivo no momento exato em que a estratégia precisa entrar.

### 6. Otimizar payoff, não apenas win rate

Em memecoins, a distribuição é assimétrica: muitos -20%/-30%, poucos +200%/+500%. Um big player testaria gestão de posição em camadas:

- vender 30%-50% em alvo curto para reduzir risco;
- mover stop do restante para break-even;
- deixar moonbag pequeno para caudas longas;
- trailing mais largo só na fração remanescente;
- time-stop se não houver continuação após entrada.

O objetivo não é só aumentar WR. É melhorar EV, reduzir drawdown e preservar exposição a outliers.

### 7. Criar controles de risco e kill switches de verdade

Um player grande teria limites automáticos como:

- perda máxima diária;
- número máximo de trades por janela;
- drawdown máximo por versão de estratégia;
- bloqueio se GMGN/Helius/eRPC divergir além de um limite;
- bloqueio se slippage mediano subir;
- bloqueio se fill delay passar de X segundos;
- bloqueio por anomalia de mercado ou RPC.

Isso evita que um bug de parser, uma mudança no Pump.fun, uma degradação do RPC ou um período de mercado ruim drene capital antes de revisão humana.

### 8. Versionar dados, estratégia e decisões

Cada trade deveria carregar:

- `strategy_version`;
- `git_sha`;
- parâmetros efetivos;
- features disponíveis no momento;
- motivo de aceite/rejeição;
- fonte de preço usada;
- executor usado;
- snapshot de liquidez/holders/rug/smart/sniper;
- resultado GMGN real separado de resultado simulado.

Sem isso, o time não consegue responder: “essa perda veio da tese, da versão, da fonte, do executor ou do mercado?”.

### 9. Fazer adversarial review do próprio bot

Um big player assumiria que o mercado adapta. Ele procuraria:

- tokens criados para enganar filtros de smart money;
- wash trading que imita absorção;
- creators que simulam histórico bom;
- bots que vendem exatamente quando outros snipers entram;
- mudanças de layout/eventos Pump.fun;
- dependências externas comprometidas;
- leakage de wallet/estratégia.

A pergunta deixa de ser “o token parece bom?” e vira “como alguém exploraria o nosso critério de entrada?”.

### 10. Próxima jogada mais racional para o NeideBOT

A resposta prática é não reescrever tudo. Um big player provavelmente faria nesta ordem:

1. **Instrumentar `execution_latency_audit`** para entender preço sinal→fill.
2. **Rodar shadow mode real-time** para hipóteses novas, sem capital.
3. **Simular partial exits/moonbag** usando ticks limpos.
4. **Criar perfil mínimo de creator/wallet** para todos os migrados.
5. **Testar unknown-is-risk** em histórico limpo.
6. **Adicionar kill switches por slippage/fill delay/drawdown diário**.
7. **Só depois** mexer em execução própria, priority fee dinâmica ou múltiplas rotas.

Em resumo: um big player compraria menos “sinal” e mais **certeza operacional**. Ele tentaria transformar cada perda em uma causa mensurável antes de mudar a tese principal.

## Pesquisa nova no GitHub: `solana bot gmgn`

Data da pesquisa: 2026-06-06.

### Como pesquisei

Consultas usadas:

- `site:github.com GMGN Solana bot`
- `site:github.com gmgn solana trading bot`
- `site:github.com GMGN API Solana bot`
- GitHub Topics: `gmgn` e `gmgnai`

Não clonei nem executei repositórios externos nesta rodada. A pesquisa foi feita por páginas públicas do GitHub e READMEs acessíveis no navegador. A busca mostra que o ecossistema GMGN open source recente está mais forte em **SDK/CLI/agent skills, scoring, copy-trading e wallet intelligence** do que em bots autônomos maduros de pós-migração como o NeideBOT.

### Achados mais relevantes

| Repositório | Linguagem / status aparente | O que faz | Valor para o NeideBOT | Risco / cuidado |
|---|---|---|---|---|
| `GMGNAI/gmgn-skills` | TypeScript, repo oficial/semioficial da GMGN, ~315 stars na página consultada | Skills/CLI para consultar tokens, Trenches, holders, top traders, Smart Money, KOL, insider, bundler, swaps, limit orders, TP/SL e “cooking orders” | É a referência mais importante para paridade com a superfície GMGN atual: comandos oficiais, quote/swap, order query, sell por percentual, gas-price, multi-swap, rate limit e novas rotas | Não copiar cegamente fluxo de agent skills para produção; usar como referência de API/CLI e comportamento esperado |
| `QuantProcessing/gmgm-sdk-py` | Python, MIT, pequeno, não oficial | SDK Python para GMGN OpenAPI, portado da superfície TypeScript oficial, com 27 métodos, auth normal/crítica, request signing, rate-limit retry e verificação de live swap | Útil para auditar se nosso `gmgn_client.py` está incompleto ou divergente da superfície oficial, especialmente em signing, anti-MEV, fee fields e rate-limit | Não oficial; deve ser usado como comparação, não como dependência automática |
| `ChipaDevTeam/GmGnAPI` | Python, MIT, ~26 stars | Cliente Python para WebSocket GMGN, com canais de new pools, launches, pair updates, stats, social info, wallet trades, limit orders, reconexão, filtros, export e alertas | Ideia útil: canal WS GMGN como fonte redundante ao `trenches` polling e ao Helius/eRPC, além de export/monitoring padronizados | README tem sinais promocionais/referral; validar código antes de qualquer uso |
| `LW-ARTS/trenchkit` | TypeScript, MIT, pequeno mas bem posicionado | Puxa ~500 trending tokens GMGN, aplica hard filters, faz pesquisa por vários endpoints, score de 6 dimensões e shortlist de 3-5 tokens | Forte inspiração para `shadow shortlist`: registrar score, motivos de aprovação/rejeição e recibo auditável por token | Foco em trending, não em pós-migração; pode gerar sinais tardios para nossa tese |
| `alsk1992/smartsniffer` | Rust, MIT, pequeno | Copy-trader de Smart Money/KOL via GMGN, cluster detection, TP/SL ladders, backtester, dry-run default, Prometheus metrics e JSONL append-only | Excelente referência conceitual: cluster de carteiras, tiers, cooldown, backtest por `signals.jsonl`, observabilidade e segurança operacional | Poucos stars; não validar como base pronta. Usar ideias de arquitetura e logs |
| `bigdata5911/gmgnai-scraping` / forks similares | Docs/scripts, scraping de endpoints web GMGN | Documenta endpoints de ranking/trending GMGN web: age, liquidez, MC, holders, smartmoney, swaps, volume, mudanças 1m/5m/1h, filtros honeypot/verified/renounced | Útil para enriquecer `unknown-is-risk` e comparar OpenAPI vs web ranking/trending | Endpoints web podem quebrar, Cloudflare/ToS; não devem substituir OpenAPI oficial |
| `vybenetwork/solana-top-traders-wallets-and-tokens-api` | TypeScript, ~215 stars | Não é GMGN client, mas aparece no tópico; referência de top traders, trades, token metadata, top holders e UI de wallet intelligence | Forte para a lacuna de `wallet_profiles`/`creator_profiles`: top traders por token, contexto de trades, holders e labels de programas | Exige API externa; complementar, não substituto de GMGN/Helius |
| `SolanaTrader/gmgn-solana-bot` | README-only aparente | Mais análise/promocional da plataforma GMGN do que código real de bot | Baixo valor técnico; serve só como sinal de que há muito conteúdo superficial com “GMGN bot” no nome | Não considerar como bot open source estudável |
| `DankBot42069/Trending-meme-coin-sniper` e similares | Python/scraping, baixo sinal de qualidade | Scrape de trending GMGN e compra/venda por força de tendência | Ideia: tendência GMGN pode virar feature de validação; não é estratégia principal | Alto risco; o próprio snippet alerta que não deve ser rodado como está |

### O que mudou na leitura depois da busca GMGN-specific

A pesquisa anterior já apontava que o NeideBOT está mais maduro que a maioria dos bots pequenos. A pesquisa focada em GMGN muda a prioridade em três pontos:

1. **Paridade com GMGN oficial virou prioridade maior**: `gmgn-skills` e `gmgm-sdk-py` sugerem que a superfície GMGN atual é maior que o que usamos hoje. Precisamos auditar `gmgn_client.py` contra a CLI/SDK atual: quote, order query, gas-price, sell percentual, multi-swap, anti-MEV, fee fields, strategy orders e rate-limit behavior.
2. **Smart Money precisa virar cluster próprio, não só contador**: `smartsniffer` reforça que `smart_degen_count >= 2` é pouco. O edge pode estar em cluster/tier/EMA de carteiras, KOL overlay, cooldowns e logs append-only de todos os sinais.
3. **Trending/Trenches GMGN pode ser usado como “score auxiliar”**: `trenchkit` não compete com a tese pós-migração, mas mostra um workflow auditável: hard filters -> burst de endpoints -> score multidimensional -> shortlist com recibo. Isso é útil para shadow mode e rejeições explicáveis.

### Lacunas GMGN-specific para o NeideBOT

#### 1. Auditoria de paridade com `gmgn-cli` / OpenAPI atual

Criar um checklist comparando nosso `gmgn_client.py` com a superfície GMGN atual:

- endpoints de token/trenches/kline/signals que já usamos;
- quote pré-trade sem submissão;
- query order/fill/status com campos completos;
- gas price / fee hints;
- market orders, limit orders e strategy orders;
- sell por percentual de posição;
- multi-wallet/multi-swap, apenas para estudo ou shadow;
- anti-MEV e priority/tip/fee fields;
- comportamento de rate-limit: `X-RateLimit-Reset`, retry curto e bloqueio em swap.

Resultado esperado: um documento ou teste de contrato que diga “suportado / não suportado / irrelevante / perigoso”.

#### 2. Quote-before-buy como métrica obrigatória

Hoje a tese sabe que a entrada real fica longe do fundo, mas precisamos diferenciar sinal atrasado de execução ruim. O padrão GMGN CLI/SDK sugere separar quote e swap:

- registrar quote antes da ordem;
- comparar `mc_signal` vs `mc_quote` vs `mc_fill`;
- cancelar se `quote_entry_above_bottom` exceder threshold;
- salvar route/provider/fee/slippage efetivos;
- comparar quote GMGN com preço Helius pAMM no mesmo instante.

Isso deveria entrar em `execution_latency_audit` antes de alterar filtros.

#### 3. Rate-limit budget por rota GMGN

`smartsniffer` explicita pesos/cadência por rota; `gmgn-skills` documenta leaky bucket e reset. NeideBOT deveria ter orçamento próprio por rota:

- `trenches`: frequência e peso;
- `token_signal`: frequência;
- `token_info`/security: só quando necessário;
- `kline`: backfill e análise, não bloquear live;
- `swap`: nunca retry agressivo;
- `query_order`: cadence enquanto posição aberta.

Sem isso, otimizações novas podem roubar budget do caminho crítico.

#### 4. `signals.jsonl` append-only para pesquisa e accountability

Além do PostgreSQL, copiar a ideia de log append-only:

- cada candidato aceito/rejeitado vira uma linha JSON;
- inclui features, score, reject_reason, versão, fonte, quote, decisão;
- fácil de versionar, auditar com `jq`, publicar parcial e comparar branches;
- evita cherry-picking de “sinais bonitos”.

Isso é especialmente útil para shadow mode.

#### 5. Cluster/tier de Smart Money e KOL

Em vez de `smart_degen_count` isolado:

- agrupar compras Smart Money/KOL por janela curta;
- medir wallets distintas, reputação, PnL histórico e overlap;
- classificar em tiers (`weak`, `strong`, `very_strong`);
- bloquear wallets que historicamente entram em rugs ou pumps sem continuidade;
- adicionar cooldown para não disparar múltiplos sinais no mesmo cluster.

Isso conversa diretamente com a hipótese central: “há comprador real no fundo?”.

#### 6. Score auxiliar de trending, sem virar chase de trending

Usar ranking GMGN/trending como feature contextual, não como gatilho principal:

- posição no trending 1m/5m/1h;
- mudança de volume e swaps;
- holder_count delta;
- smartmoney delta;
- liquidez/MC trend;
- se o token aparece em trending apenas depois do bounce, marcar como atraso.

Isso pode ajudar a separar bounce orgânico de bounce fraco, mas não deve substituir o detector pós-migração.

### Repositórios que eu priorizaria para leitura meticulosa nesta trilha GMGN

1. `GMGNAI/gmgn-skills`: referência de API/CLI e comportamento oficial.
2. `QuantProcessing/gmgm-sdk-py`: comparação Python direta com nosso `gmgn_client.py`.
3. `alsk1992/smartsniffer`: ideias de cluster, dry-run, TP/SL ladders, logs e Prometheus.
4. `LW-ARTS/trenchkit`: scoring auditável e shortlist com motivos.
5. `ChipaDevTeam/GmGnAPI`: possível fonte WS GMGN redundante, se o código confirmar o README.
6. `vybenetwork/*`: inspiração para `wallet_profiles` e creator/wallet intelligence, embora não seja GMGN.

### Recomendação prática pós-pesquisa

A próxima mudança de maior ROI não é adicionar outro bot, e sim criar uma sprint “GMGN parity + observability”:

1. Fazer `docs/audits/gmgn-api-parity.md` comparando `gmgn_client.py` com `gmgn-skills` e `gmgm-sdk-py`.
2. Implementar `execution_latency_audit` com quote-before-buy e campos `mc_signal`, `mc_quote`, `mc_fill`.
3. Adicionar log append-only `signals.jsonl` para shadow/live/reject.
4. Transformar `smart_degen_count` em `smart_cluster_score` com tiers e cooldowns.
5. Testar trending/trenches score apenas como feature auxiliar, nunca como gatilho isolado.
