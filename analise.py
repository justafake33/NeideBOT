"""
analise.py — Backtest exploratório sobre dados reais do banco NeideBOT.

Foco: trade de minutos (compra no fundo, saída antes de 1h).

Uso:
    DATABASE_URL=postgres://... python analise.py

Perguntas respondidas:
    1. Timing  — quando o fundo se forma?
    2. Dump    — qual profundidade de queda associa-se a bounce real?
    3. Filtros — smart/rug/top10/wash realmente funcionam?
    4. R:R     — qual o risco/retorno esperado de comprar no fundo?
"""

import os
import sys
import statistics
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("Erro: DATABASE_URL não definida.")


@contextmanager
def cursor():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def pct(n, total):
    return f"{n/total*100:.1f}%" if total else "—"

def fmt_pct(v):
    if v is None: return "—"
    return f"{v*100:+.1f}%"

def percentiles(values, ps=(25, 50, 75, 90)):
    if not values:
        return {p: None for p in ps}
    s = sorted(values)
    n = len(s)
    result = {}
    for p in ps:
        idx = (p / 100) * (n - 1)
        lo, hi = int(idx), min(int(idx) + 1, n - 1)
        frac = idx - lo
        result[p] = s[lo] + frac * (s[hi] - s[lo])
    return result

def mean(values):
    return statistics.mean(values) if values else None

def sep(char="─", n=70):
    print(char * n)


# ──────────────────────────────────────────────────────────────────────────────
# Carga de dados
# ──────────────────────────────────────────────────────────────────────────────

def load_data():
    with cursor() as cur:
        cur.execute("""
            SELECT
                mint, token_nome,
                mc_migration, mc_peak, time_to_peak, pct_gain,
                mc_bottom,    time_to_bottom, pct_drop,
                mc_1h,        var_1h,
                smart_wallets, renowned_wallets, sniper_wallets,
                rug_ratio, top10_holder_rate, is_wash_trading,
                liq_migration, recovery,
                capture_delay_sec
            FROM migrations
            WHERE ts_migration IS NOT NULL
            ORDER BY ts_migration DESC
        """)
        return [dict(r) for r in cur.fetchall()]


# ──────────────────────────────────────────────────────────────────────────────
# Seção 1 — Visão geral do dataset
# ──────────────────────────────────────────────────────────────────────────────

def section_overview(rows):
    sep("═")
    print("SEÇÃO 1 — VISÃO GERAL DO DATASET")
    sep("═")

    total        = len(rows)
    has_bottom   = [r for r in rows if r["mc_bottom"] is not None]
    has_peak     = [r for r in rows if r["mc_peak"]   is not None]
    has_1h       = [r for r in rows if r["mc_1h"]     is not None]
    recovered    = [r for r in rows if r["recovery"] is True]
    not_recov    = [r for r in rows if r["recovery"] is False]
    pending      = [r for r in rows if r["recovery"] is None]
    complete     = [r for r in rows if r["mc_bottom"] is not None and r["mc_1h"] is not None]

    print(f"Total de migrações coletadas : {total}")
    print(f"Com mc_bottom (fundo)        : {len(has_bottom)} ({pct(len(has_bottom), total)})")
    print(f"Com mc_peak   (pico)         : {len(has_peak)}   ({pct(len(has_peak), total)})")
    print(f"Com mc_1h                    : {len(has_1h)}   ({pct(len(has_1h), total)})")
    print(f"Dataset completo (bottom+1h) : {len(complete)}   ({pct(len(complete), total)})")
    print()
    print(f"Recovery = TRUE              : {len(recovered)}   ({pct(len(recovered), len(has_1h))} dos com 1h)")
    print(f"Recovery = FALSE             : {len(not_recov)}  ({pct(len(not_recov), len(has_1h))} dos com 1h)")
    print(f"Pendente (sem 1h ainda)      : {len(pending)}")

    delays = [r["capture_delay_sec"] for r in rows if r["capture_delay_sec"] is not None]
    if delays:
        p = percentiles(delays)
        print()
        print(f"Delay de captura (mig→bot)   : p50={p[50]:.0f}s  p75={p[75]:.0f}s  p90={p[90]:.0f}s")

    print()


# ──────────────────────────────────────────────────────────────────────────────
# Seção 2 — Timing: quando o fundo e o pico se formam?
# ──────────────────────────────────────────────────────────────────────────────

def section_timing(rows):
    sep("═")
    print("SEÇÃO 2 — TIMING (quando o fundo e o pico se formam?)")
    sep("═")

    has_bottom = [r for r in rows if r["time_to_bottom"] is not None]
    has_peak   = [r for r in rows if r["time_to_peak"]   is not None]
    both       = [r for r in rows if r["time_to_bottom"] is not None
                                 and r["time_to_peak"]   is not None]

    ttb_vals = [r["time_to_bottom"] for r in has_bottom]
    ttp_vals = [r["time_to_peak"]   for r in has_peak]

    print(f"Tokens com time_to_bottom: {len(has_bottom)}")
    if ttb_vals:
        p = percentiles(ttb_vals)
        print(f"  p25={p[25]:.1f}min  p50={p[50]:.1f}min  p75={p[75]:.1f}min  p90={p[90]:.1f}min")
        under5  = sum(1 for v in ttb_vals if v <= 5)
        under10 = sum(1 for v in ttb_vals if v <= 10)
        under20 = sum(1 for v in ttb_vals if v <= 20)
        print(f"  Fundo em ≤5min  : {under5}  ({pct(under5, len(ttb_vals))})")
        print(f"  Fundo em ≤10min : {under10} ({pct(under10, len(ttb_vals))})")
        print(f"  Fundo em ≤20min : {under20} ({pct(under20, len(ttb_vals))})")

    print()
    print(f"Tokens com time_to_peak: {len(has_peak)}")
    if ttp_vals:
        p = percentiles(ttp_vals)
        print(f"  p25={p[25]:.1f}min  p50={p[50]:.1f}min  p75={p[75]:.1f}min  p90={p[90]:.1f}min")

    print()
    if both:
        # Tokens onde o pico vem DEPOIS do fundo → bounce real
        bounce = [r for r in both if r["time_to_peak"] > r["time_to_bottom"]]
        no_bounce = [r for r in both if r["time_to_peak"] <= r["time_to_bottom"]]
        print(f"Padrão de bounce (pico APÓS fundo) : {len(bounce)} / {len(both)} ({pct(len(bounce), len(both))})")
        print(f"Sem bounce (pico ANTES do fundo)   : {len(no_bounce)} / {len(both)} ({pct(len(no_bounce), len(both))})")

        if bounce:
            delta_vals = [r["time_to_peak"] - r["time_to_bottom"] for r in bounce]
            p = percentiles(delta_vals)
            print(f"  Duração do bounce (fundo→pico): p50={p[50]:.1f}min  p75={p[75]:.1f}min  p90={p[90]:.1f}min")

    print()


# ──────────────────────────────────────────────────────────────────────────────
# Seção 3 — Dump depth e associação com bounce
# ──────────────────────────────────────────────────────────────────────────────

def section_dump(rows):
    sep("═")
    print("SEÇÃO 3 — PROFUNDIDADE DO DUMP vs BOUNCE (H1, H6)")
    sep("═")

    has_drop = [r for r in rows if r["pct_drop"] is not None and r["mc_bottom"] is not None]

    drop_vals = [r["pct_drop"] for r in has_drop]
    if drop_vals:
        p = percentiles(drop_vals)
        print(f"Distribuição de pct_drop ({len(drop_vals)} tokens):")
        print(f"  p25={p[25]*100:.0f}%  p50={p[50]*100:.0f}%  p75={p[75]*100:.0f}%  p90={p[90]*100:.0f}%")

        over20 = sum(1 for v in drop_vals if v >= 0.20)
        print(f"  Caíram ≥20% (H1) : {over20} ({pct(over20, len(drop_vals))}) — meta H1: ≥70%")

    print()

    # Buckets de dump com taxa de recovery e R:R (bottom→1h)
    buckets = [
        ("0–20%",   0.00, 0.20),
        ("20–40%",  0.20, 0.40),
        ("40–60%",  0.40, 0.60),
        ("60–80%",  0.60, 0.80),
        (">80%",    0.80, 9.99),
    ]

    print(f"{'Queda':>10} {'N':>5} {'Recovery%':>10} {'Bounce%':>10} {'R:R med (bot→1h)':>18} {'WinRate(1h>bot)':>16}")
    sep()

    for label, lo, hi in buckets:
        group = [r for r in has_drop if lo <= r["pct_drop"] < hi]
        if not group:
            continue

        n = len(group)

        # Recovery (mc_1h > mc_bottom * 1.3)
        recov_n = sum(1 for r in group if r["recovery"] is True)

        # Bounce pattern (peak after bottom)
        bounce_n = sum(1 for r in group
                       if r["time_to_peak"] is not None
                       and r["time_to_bottom"] is not None
                       and r["time_to_peak"] > r["time_to_bottom"])
        bounce_denom = sum(1 for r in group
                           if r["time_to_peak"] is not None
                           and r["time_to_bottom"] is not None)

        # R:R de bottom → mc_1h
        rr_vals = [(r["mc_1h"] / r["mc_bottom"] - 1)
                   for r in group
                   if r["mc_1h"] and r["mc_bottom"] and r["mc_bottom"] > 0]
        rr_med = percentiles(rr_vals, (50,))[50] if rr_vals else None
        win_n  = sum(1 for v in rr_vals if v > 0)

        recov_str  = pct(recov_n, n)
        bounce_str = pct(bounce_n, bounce_denom) if bounce_denom else "—"
        rr_str     = f"{rr_med*100:+.1f}%" if rr_med is not None else "—"
        win_str    = pct(win_n, len(rr_vals)) if rr_vals else "—"

        print(f"{label:>10} {n:>5} {recov_str:>10} {bounce_str:>10} {rr_str:>18} {win_str:>16}")

    print()


# ──────────────────────────────────────────────────────────────────────────────
# Seção 4 — Validação dos filtros (H3, H4, H5, H8)
# ──────────────────────────────────────────────────────────────────────────────

def section_filters(rows):
    sep("═")
    print("SEÇÃO 4 — VALIDAÇÃO DOS FILTROS (H3, H4, H5, H8)")
    sep("═")

    scored = [r for r in rows if r["recovery"] is not None]

    def compare(label, group_a, label_a, group_b, label_b):
        ra = sum(1 for r in group_a if r["recovery"] is True)
        rb = sum(1 for r in group_b if r["recovery"] is True)
        pct_a = f"{ra/len(group_a)*100:.1f}%" if group_a else "—"
        pct_b = f"{rb/len(group_b)*100:.1f}%" if group_b else "—"
        diff = (ra/len(group_a) - rb/len(group_b)) * 100 if group_a and group_b else None
        diff_str = f"{diff:+.1f}pp" if diff is not None else "—"
        print(f"  {label}")
        print(f"    {label_a:35s}: {len(group_a):4d} tokens | recovery={pct_a}  ({ra}/{len(group_a)})")
        print(f"    {label_b:35s}: {len(group_b):4d} tokens | recovery={pct_b}  ({rb}/{len(group_b)})")
        print(f"    Diferença: {diff_str}")
        print()

    # H3 — smart_wallets
    smart_3p = [r for r in scored if r["smart_wallets"] is not None and r["smart_wallets"] >= 3]
    smart_lt3 = [r for r in scored if r["smart_wallets"] is not None and r["smart_wallets"] < 3]
    compare("H3 — Smart wallets ≥3 vs <3 (meta: +15pp)", smart_3p, "smart ≥ 3", smart_lt3, "smart < 3")

    # H4 — sniper/holders (usando sniper_wallets count como proxy)
    sniper_hi = [r for r in scored if r["sniper_wallets"] is not None and r["sniper_wallets"] >= 3]
    sniper_lo = [r for r in scored if r["sniper_wallets"] is not None and r["sniper_wallets"] < 3]
    compare("H4 — Sniper ≥3 vs <3 (meta: recovery <30%)", sniper_hi, "sniper ≥ 3", sniper_lo, "sniper < 3")

    # H5 — top10_holder_rate
    top10_hi = [r for r in scored if r["top10_holder_rate"] is not None and r["top10_holder_rate"] >= 0.80]
    top10_lo = [r for r in scored if r["top10_holder_rate"] is not None and r["top10_holder_rate"] < 0.80]
    compare("H5 — Top10 ≥80% vs <80%", top10_hi, "top10 ≥ 80%", top10_lo, "top10 < 80%")

    # H8 — wash trading
    wash_y = [r for r in scored if r["is_wash_trading"] is True]
    wash_n = [r for r in scored if r["is_wash_trading"] is False]
    compare("H8 — Wash trading vs limpo (meta: -20pp)", wash_y, "wash=TRUE", wash_n, "wash=FALSE")

    # Rug ratio
    rug_hi = [r for r in scored if r["rug_ratio"] is not None and r["rug_ratio"] > 0.15]
    rug_lo = [r for r in scored if r["rug_ratio"] is not None and r["rug_ratio"] <= 0.15]
    compare("Rug ratio >15% vs ≤15%", rug_hi, "rug > 15%", rug_lo, "rug ≤ 15%")


# ──────────────────────────────────────────────────────────────────────────────
# Seção 5 — R:R simulado (trade de minutos: compra no fundo, saída no 1h)
# ──────────────────────────────────────────────────────────────────────────────

def section_rr(rows):
    sep("═")
    print("SEÇÃO 5 — R:R SIMULADO (entrada no fundo, saída proxy = mc_1h)")
    sep("═")

    tradeable = [
        r for r in rows
        if r["mc_bottom"] is not None
        and r["mc_1h"]    is not None
        and r["mc_bottom"] > 0
        and r["pct_drop"]  is not None
    ]

    if not tradeable:
        print("Dados insuficientes para simulação.")
        print()
        return

    gains = [(r["mc_1h"] / r["mc_bottom"] - 1) for r in tradeable]
    wins  = [g for g in gains if g > 0]
    loss  = [g for g in gains if g <= 0]

    p_gains = percentiles(gains)
    win_rate = len(wins) / len(gains)

    print(f"Universo (bottom + 1h presentes): {len(tradeable)} tokens")
    print()
    print(f"Win rate (mc_1h > mc_bottom)    : {win_rate*100:.1f}%  ({len(wins)}/{len(gains)})")
    print(f"Gain médio (winners)            : {mean(wins)*100:+.1f}%" if wins else "Gain médio: —")
    print(f"Loss média (losers)             : {mean(loss)*100:+.1f}%" if loss else "Loss média: —")
    print()
    print(f"Distribuição do R:R (bottom→1h):")
    print(f"  p25={p_gains[25]*100:+.1f}%  p50={p_gains[50]*100:+.1f}%  p75={p_gains[75]*100:+.1f}%  p90={p_gains[90]*100:+.1f}%")

    # Payoff ratio simples
    avg_win  = mean(wins)  if wins else None
    avg_loss = abs(mean(loss)) if loss else None
    if avg_win and avg_loss:
        print(f"\nPayoff ratio (avg_win / avg_loss): {avg_win/avg_loss:.2f}×")
        expected = win_rate * avg_win - (1 - win_rate) * avg_loss
        print(f"EV por trade                     : {expected*100:+.1f}%")

    # Mesma simulação filtrando apenas tokens com dump ≥20%
    deep = [r for r in tradeable if r["pct_drop"] >= 0.20]
    if deep:
        print()
        print(f"Subgrupo: dump ≥20% ({len(deep)} tokens)")
        gains_d = [(r["mc_1h"] / r["mc_bottom"] - 1) for r in deep]
        wins_d  = [g for g in gains_d if g > 0]
        loss_d  = [g for g in gains_d if g <= 0]
        wr_d    = len(wins_d) / len(gains_d)
        print(f"  Win rate : {wr_d*100:.1f}%")
        p_d = percentiles(gains_d)
        print(f"  p50={p_d[50]*100:+.1f}%  p75={p_d[75]*100:+.1f}%  p90={p_d[90]*100:+.1f}%")
        if wins_d and loss_d:
            aw = mean(wins_d); al = abs(mean(loss_d))
            ev = wr_d * aw - (1 - wr_d) * al
            print(f"  Payoff   : {aw/al:.2f}×  |  EV: {ev*100:+.1f}%")

    print()


# ──────────────────────────────────────────────────────────────────────────────
# Seção 6 — H2: tempo até o fundo em tokens com recovery=TRUE
# ──────────────────────────────────────────────────────────────────────────────

def section_h2(rows):
    sep("═")
    print("SEÇÃO 6 — H2: fundo ocorre em ≤15min nos tokens com recovery?")
    sep("═")

    recov_ok = [r for r in rows
                if r["recovery"] is True and r["time_to_bottom"] is not None]
    recov_no = [r for r in rows
                if r["recovery"] is False and r["time_to_bottom"] is not None]

    if recov_ok:
        p_ok = percentiles([r["time_to_bottom"] for r in recov_ok])
        under15 = sum(1 for r in recov_ok if r["time_to_bottom"] <= 15)
        print(f"Recovery=TRUE  ({len(recov_ok)} tokens):")
        print(f"  time_to_bottom — p25={p_ok[25]:.1f}min  p50={p_ok[50]:.1f}min  p75={p_ok[75]:.1f}min")
        print(f"  Fundo em ≤15min: {under15} ({pct(under15, len(recov_ok))}) — meta H2: ≥70%")

    if recov_no:
        p_no = percentiles([r["time_to_bottom"] for r in recov_no])
        under15 = sum(1 for r in recov_no if r["time_to_bottom"] <= 15)
        print(f"\nRecovery=FALSE ({len(recov_no)} tokens):")
        print(f"  time_to_bottom — p25={p_no[25]:.1f}min  p50={p_no[50]:.1f}min  p75={p_no[75]:.1f}min")
        print(f"  Fundo em ≤15min: {under15} ({pct(under15, len(recov_no))})")

    print()


# ──────────────────────────────────────────────────────────────────────────────
# Seção 7 — Melhores combinações de filtros (scan simples)
# ──────────────────────────────────────────────────────────────────────────────

def section_filter_combos(rows):
    sep("═")
    print("SEÇÃO 7 — COMBINAÇÕES DE FILTROS (win rate real vs baseline)")
    sep("═")

    base = [r for r in rows if r["mc_bottom"] is not None and r["mc_1h"] is not None and r["mc_bottom"] > 0]
    if not base:
        print("Dados insuficientes.")
        print()
        return

    def wr(group):
        if not group: return None, 0
        wins = sum(1 for r in group if r["mc_1h"] > r["mc_bottom"])
        return wins / len(group), len(group)

    baseline_wr, baseline_n = wr(base)
    print(f"Baseline (todos os tokens com bottom+1h): {baseline_wr*100:.1f}% win rate  (n={baseline_n})")
    print()
    print(f"{'Filtro':45s} {'n':>5} {'WinRate':>9} {'Delta':>8}")
    sep()

    combos = [
        ("smart ≥ 1",           lambda r: (r["smart_wallets"] or 0) >= 1),
        ("smart ≥ 2",           lambda r: (r["smart_wallets"] or 0) >= 2),
        ("smart ≥ 3",           lambda r: (r["smart_wallets"] or 0) >= 3),
        ("rug ≤ 10%",           lambda r: r["rug_ratio"] is not None and r["rug_ratio"] <= 0.10),
        ("rug ≤ 15%",           lambda r: r["rug_ratio"] is not None and r["rug_ratio"] <= 0.15),
        ("top10 < 70%",         lambda r: r["top10_holder_rate"] is not None and r["top10_holder_rate"] < 0.70),
        ("top10 < 80%",         lambda r: r["top10_holder_rate"] is not None and r["top10_holder_rate"] < 0.80),
        ("wash=FALSE",          lambda r: r["is_wash_trading"] is False),
        ("dump ≥ 20%",          lambda r: (r["pct_drop"] or 0) >= 0.20),
        ("dump ≥ 30%",          lambda r: (r["pct_drop"] or 0) >= 0.30),
        ("dump 20–60%",         lambda r: 0.20 <= (r["pct_drop"] or 0) < 0.60),
        ("smart≥2 + wash=FALSE",lambda r: (r["smart_wallets"] or 0) >= 2 and r["is_wash_trading"] is False),
        ("smart≥2 + dump≥20%",  lambda r: (r["smart_wallets"] or 0) >= 2 and (r["pct_drop"] or 0) >= 0.20),
        ("smart≥1 + top10<80% + wash=FALSE",
                                 lambda r: (r["smart_wallets"] or 0) >= 1
                                       and r["top10_holder_rate"] is not None and r["top10_holder_rate"] < 0.80
                                       and r["is_wash_trading"] is False),
        ("smart≥2 + rug≤15% + dump≥20%",
                                 lambda r: (r["smart_wallets"] or 0) >= 2
                                       and r["rug_ratio"] is not None and r["rug_ratio"] <= 0.15
                                       and (r["pct_drop"] or 0) >= 0.20),
    ]

    for label, fn in combos:
        group = [r for r in base if fn(r)]
        w, n = wr(group)
        if w is None:
            print(f"  {label:43s} {'—':>5} {'—':>9} {'—':>8}")
            continue
        delta = (w - baseline_wr) * 100
        delta_str = f"{delta:+.1f}pp"
        print(f"  {label:43s} {n:>5} {w*100:>8.1f}% {delta_str:>8}")

    print()


# ──────────────────────────────────────────────────────────────────────────────
# Seção 8 — Resumo executivo
# ──────────────────────────────────────────────────────────────────────────────

def section_summary(rows):
    sep("═")
    print("SEÇÃO 8 — RESUMO EXECUTIVO")
    sep("═")

    has_bottom = [r for r in rows if r["mc_bottom"] is not None]
    has_1h     = [r for r in rows if r["mc_1h"] is not None]
    tradeable  = [r for r in rows if r["mc_bottom"] is not None and r["mc_1h"] is not None and r["mc_bottom"] > 0]

    print(f"Dataset útil para análise de minutos: {len(tradeable)} tokens (bottom + 1h)")
    print()

    if not tradeable:
        print("Dados insuficientes — aguardar mais migrações completas.")
        return

    gains = [(r["mc_1h"] / r["mc_bottom"] - 1) for r in tradeable]
    wr = sum(1 for g in gains if g > 0) / len(gains)

    print(f"Win rate base (bottom→1h): {wr*100:.1f}%")
    print()
    print("Próximos passos sugeridos pelos dados:")
    print()

    p_ttb = percentiles([r["time_to_bottom"] for r in has_bottom if r["time_to_bottom"] is not None])
    if p_ttb[50]:
        print(f"  → Fundo mediano em {p_ttb[50]:.1f}min após migração.")
        print(f"    Timing engine deve monitorar ativamente os primeiros {p_ttb[75]:.0f}min.")

    drop_vals = [r["pct_drop"] for r in has_bottom if r["pct_drop"] is not None]
    if drop_vals:
        p_drop = percentiles(drop_vals)
        print(f"  → Dump mediano: {p_drop[50]*100:.0f}%. Zona de entrada candidata: >{p_drop[25]*100:.0f}% de queda.")

    if wr >= 0.5:
        print(f"  → Win rate ≥50% sugere edge positivo. Priorizar Execution Engine (Fase 6).")
    else:
        print(f"  → Win rate <50%: revisar filtros antes de avançar para execução.")

    print()


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    print()
    sep("═")
    print("NeideBOT — ANÁLISE EXPLORATÓRIA DE DADOS (trade de minutos)")
    sep("═")
    print()

    print("Carregando dados do banco...", flush=True)
    rows = load_data()
    print(f"  {len(rows)} registros carregados.")
    print()

    section_overview(rows)
    section_timing(rows)
    section_dump(rows)
    section_filters(rows)
    section_rr(rows)
    section_h2(rows)
    section_filter_combos(rows)
    section_summary(rows)


if __name__ == "__main__":
    main()
