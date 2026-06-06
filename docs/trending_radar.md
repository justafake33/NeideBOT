# GMGN Trending Radar — desenho básico de edge em paralelo

O `trending_radar.py` é um sistema **passivo/shadow** para descobrir se o GMGN Trending oferece edge antes de adaptar o NeideBOT principal.

Ele não compra, não vende e não interfere no scanner/executor. A função inicial é responder três perguntas:

1. Tokens em trending foram vistos pelo NeideBOT?
2. Se foram vistos, pararam em qual etapa: migration, bounce ou trade?
3. Se o snapshot de trending fosse uma entrada hipotética, o token teria batido TP antes de SL usando os ticks que já temos?

## Onde seria a possível entrada?

A entrada hipotética da V0 é simples e conservadora para pesquisa:

```text
entry_ts = ts_snapshot do GMGN Trending
entry_mc = market_cap retornado no snapshot
```

Isso não quer dizer que operaríamos exatamente ali em produção. É só um modelo inicial para medir se existe sinal bruto.

Depois, se houver edge, podemos testar entradas mais realistas:

- entrada quando o token aparece pela primeira vez no trending;
- entrada quando rank melhora entre snapshots;
- entrada quando trending coincide com H26;
- entrada quando trending aparece até N minutos após o fundo;
- entrada quando trending vira veto/boost para H26, e não sinal independente.

## Como o WR shadow é calculado?

Para cada snapshot salvo, o radar busca `token_ticks` futuros do mesmo mint dentro de uma janela configurável.

Defaults:

```text
TRENDING_RADAR_OUTCOME_WINDOW_MIN=60
TRENDING_RADAR_TP_PCT=0.75
TRENDING_RADAR_SL_PCT=0.25
```

Regra:

```text
win  = bate +75% antes de -25%
loss = bate -25% antes de +75%
ambiguous_intrabar = TP e SL aparecem no mesmo candle/tick; não entra no WR
open = não bate nenhum dos dois dentro da janela
no_ticks = não temos ticks futuros para esse mint
```

Isso gera um WR apenas sobre casos resolvidos:

```text
shadow_wr = wins / (wins + losses)  # ambiguous fica separado
```

Importante: `no_ticks` não é derrota. É sinal de que o NeideBOT não estava monitorando aquele token em tempo real, então a primeira métrica importante é cobertura.

## Métrica mais importante da V0: coverage

Antes de decidir se trending tem edge, precisamos saber se o NeideBOT viu ou não viu esses tokens:

```text
not_seen       -> não apareceu em migrations
migrated_only  -> apareceu em migrations, mas não virou bounce/trade
bounce_signaled -> teve sinal de bounce
traded         -> foi operado
```

E inferimos um `reject_reason` aproximado:

- `not_in_migrations`
- `old_migration_at_snapshot`
- `not_pump_exchange`
- `mc_low` / `mc_high`
- `liq_low`
- `fee_low`
- `wash_trading`
- `rug_high`
- `sniper_high`
- `rat_high`
- `smart_low`
- `eligible_or_rejected_after_signal`

A pergunta principal da V0 é:

```text
Os bons trending foram vistos, rejeitados ou invisíveis para o NeideBOT?
```

## Como rodar

Uma rodada única:

```bash
TRENDING_RADAR_RUN_ONCE=true python trending_radar.py
```

Rodando contínuo:

```bash
python trending_radar.py
```

Configurações úteis:

```text
TRENDING_RADAR_CHAIN=sol
TRENDING_RADAR_INTERVALS=1m,5m
TRENDING_RADAR_ORDER_BY=default
TRENDING_RADAR_DIRECTION=desc
TRENDING_RADAR_LIMIT=100
TRENDING_RADAR_POLL_SEC=60
TRENDING_RADAR_PLATFORMS=pool_pump_amm,Pump.fun
TRENDING_RADAR_FILTERS=not_wash_trading
TRENDING_RADAR_OUTCOME_WINDOW_MIN=60
TRENDING_RADAR_TP_PCT=0.75
TRENDING_RADAR_SL_PCT=0.25
```

## Tabelas criadas

### `gmgn_trending_snapshots`

Guarda cada token retornado pelo trending, com rank, mint, market cap, liquidez, volume, swaps, buys, sells, smart, KOL, risco e `raw_json`.

### `gmgn_trending_coverage`

Guarda a correlação com o NeideBOT e outcome shadow:

- status no scanner;
- motivo provável de rejeição/invisibilidade;
- entry hipotética;
- max runup;
- max drawdown;
- TP/SL first hit;
- `shadow_win`.

## Como decidir se vale adaptar o NeideBOT?

Após alguns dias de coleta, olhar:

1. **Coverage:** quantos trending bons nunca entraram no funil?
2. **WR resolvido:** entre snapshots com ticks, TP vem antes de SL?
3. **Runup:** mesmo quando não bate TP, existe runup capturável?
4. **Reject reasons:** filtros estão removendo tokens que depois performam?
5. **Timing:** trending aparece antes, durante ou depois do H26?

Critérios de avanço sugeridos:

```text
Se muitos bons estão not_seen -> estudar trending como discovery paralelo.
Se muitos bons estão old_migration -> estudar ampliar janela ou estratégia late-bounce.
Se H26 winners têm trending_context e losers não -> usar trending como boost/veto.
Se trending sozinho tem WR/EV fraco -> manter apenas como contexto/alerta.
```

## Próximo passo se a V0 mostrar sinal

A V1 deveria calcular features temporais:

- first_seen_trending_delta;
- rank_velocity;
- hot_level_delta;
- volume_delta;
- holder_delta;
- smart_delta;
- buy_sell_ratio;
- risk drift.

Só depois disso faria sentido discutir realtime seletivo ou execução canary.
