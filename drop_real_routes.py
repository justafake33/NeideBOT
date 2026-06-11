"""Rotas de análise do drop real e backtest de entrada no bounce."""

from datetime import timezone, timedelta

from flask import Blueprint, request

import db


drop_real_bp = Blueprint("drop_real", __name__)

@drop_real_bp.route("/analise/drop-real")
def analise_drop_real():
    """
    Analisa o drop real das migrations e simula entrada no bounce do fundo.
    A tabela histórica destaca tokens que subiram ≥20%, enquanto o backtest de
    WR usa a base completa para estimar o melhor BOUNCE_DROP_MIN.
    Pico medido nos token_ticks/migrations; fundo medido combinando token_ticks
    com mc_bottom/time_to_bottom gravados pelo pipeline erpc/kline em migrations.
    """
    import psycopg2
    import psycopg2.extras
    hours_arg = request.args.get("hours")
    hours = int(hours_arg) if hours_arg else None
    entry_pct = float(request.args.get("entry", "0.10"))
    tp_pct = float(request.args.get("tp", "0.75"))
    sl_pct = float(request.args.get("sl", "0.15"))
    min_sample = int(request.args.get("min_sample", "10"))
    live_hours = int(request.args.get("live_hours", "24"))
    BRT   = timezone(timedelta(hours=-3))
    time_filter_sql = "AND m.ts_migration >= NOW() - %(interval)s::interval" if hours else ""

    try:
        if not db.DATABASE_URL:
            return "<pre style='color:red'>DATABASE_URL não configurado.</pre>", 500

        conn = psycopg2.connect(db.DATABASE_URL)
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SET statement_timeout TO '15s'")
        cur.execute("""
        WITH base AS (
            SELECT m.mint, m.token_nome, m.mc_migration, m.ts_migration,
                   m.mc_peak, m.time_to_peak, m.mc_bottom, m.time_to_bottom,
                   EXISTS (SELECT 1 FROM bounce_signals bs WHERE bs.mint = m.mint) AS had_bounce
            FROM migrations m
            WHERE 1=1
              {time_filter_sql}
              AND m.mc_migration > 0
        ),
        tick_points AS (
            SELECT tt.mint,
                   tt.tick_ts,
                   EXTRACT(EPOCH FROM (tt.tick_ts - b.ts_migration)) / 60.0 AS elapsed_min,
                   GREATEST(
                       COALESCE(NULLIF(tt.mc_high, 0), 0),
                       COALESCE(NULLIF(tt.mc, 0), 0)
                   ) AS mc_high_used,
                   COALESCE(NULLIF(tt.mc_low, 0), NULLIF(tt.mc, 0)) AS mc_low_used,
                   COALESCE(NULLIF(tt.source, ''), 'token_ticks') AS tick_source
            FROM token_ticks tt
            JOIN base b ON b.mint = tt.mint
            WHERE tt.tick_ts BETWEEN b.ts_migration + INTERVAL '90 seconds'
                                 AND b.ts_migration + INTERVAL '70 minutes'
              AND (
                   GREATEST(COALESCE(NULLIF(tt.mc_high, 0), 0), COALESCE(NULLIF(tt.mc, 0), 0)) > 0
                   OR COALESCE(NULLIF(tt.mc_low, 0), NULLIF(tt.mc, 0)) > 0
              )
        ),
        tick_stats AS (
            SELECT tp.mint,
                   MAX(tp.mc_high_used) AS mc_peak_ticks,
                   MIN(tp.mc_low_used)  AS mc_min_ticks,
                   COUNT(*) AS n_ticks
            FROM tick_points tp
            GROUP BY tp.mint
        ),
        early_tick_bottom AS (
            SELECT DISTINCT ON (tp.mint)
                   tp.mint,
                   tp.mc_low_used AS mc_bottom_used,
                   tp.elapsed_min AS time_to_bottom,
                   'ticks:' || tp.tick_source AS bottom_source
            FROM tick_points tp
            WHERE tp.elapsed_min <= 20
              AND tp.mc_low_used > 0
            ORDER BY tp.mint, tp.mc_low_used ASC, tp.tick_ts ASC
        ),
        bottom_candidates AS (
            SELECT etb.mint, etb.mc_bottom_used, etb.time_to_bottom, etb.bottom_source
            FROM early_tick_bottom etb
            UNION ALL
            SELECT b.mint,
                   b.mc_bottom AS mc_bottom_used,
                   b.time_to_bottom,
                   'erpc/kline:migrations.mc_bottom' AS bottom_source
            FROM base b
            WHERE b.mc_bottom > 0
              AND (b.time_to_bottom IS NULL OR b.time_to_bottom BETWEEN 0 AND 20)
        ),
        bottom_choice AS (
            SELECT DISTINCT ON (bc.mint)
                   bc.mint, bc.mc_bottom_used, bc.time_to_bottom, bc.bottom_source
            FROM bottom_candidates bc
            WHERE bc.mc_bottom_used > 0
            ORDER BY bc.mint, bc.mc_bottom_used ASC, bc.time_to_bottom NULLS LAST
        ),
        peak_ts AS (
            SELECT DISTINCT ON (tp.mint) tp.mint,
                   tp.elapsed_min AS ttp_ticks
            FROM tick_points tp
            JOIN tick_stats tst ON tst.mint = tp.mint
                AND tp.mc_high_used = tst.mc_peak_ticks
            ORDER BY tp.mint, tp.tick_ts ASC
        ),
        scored AS (
            SELECT b.mint, b.token_nome, b.mc_migration, b.ts_migration, b.had_bounce,
                   NULLIF(GREATEST(COALESCE(tst.mc_peak_ticks, 0), COALESCE(NULLIF(b.mc_peak, 0), 0)), 0) AS mc_peak_ticks,
                   tst.mc_min_ticks, tst.n_ticks,
                   bc.mc_bottom_used AS mc_min_early,
                   bc.time_to_bottom,
                   bc.bottom_source,
                   CASE
                       WHEN COALESCE(NULLIF(b.mc_peak,0),0) >= COALESCE(tst.mc_peak_ticks,0)
                       THEN b.time_to_peak
                       ELSE pt.ttp_ticks
                   END AS time_to_peak
            FROM base b
            LEFT JOIN tick_stats    tst ON tst.mint = b.mint
            LEFT JOIN bottom_choice bc  ON bc.mint  = b.mint
            LEFT JOIN peak_ts       pt  ON pt.mint  = b.mint
        )
        SELECT s.mint, s.token_nome, s.mc_migration, s.ts_migration, s.had_bounce,
               s.mc_peak_ticks, s.mc_min_ticks, s.n_ticks,
               s.mc_min_early, s.time_to_bottom, s.bottom_source,
               s.time_to_peak,
               CASE WHEN s.mc_peak_ticks > 0 AND s.mc_migration > 0
                    THEN (s.mc_peak_ticks - s.mc_migration) / s.mc_migration
                    ELSE NULL END AS gain_pct,
               CASE WHEN s.mc_min_early IS NOT NULL AND s.mc_migration > 0
                    THEN (s.mc_migration - s.mc_min_early) / s.mc_migration
                    ELSE NULL END AS drop_pct
        FROM scored s
        ORDER BY
            CASE WHEN s.n_ticks IS NULL THEN 1 ELSE 0 END,
            (s.mc_migration - COALESCE(s.mc_min_early, s.mc_migration)) / NULLIF(s.mc_migration, 0) ASC
    """.format(time_filter_sql=time_filter_sql), {"interval": f"{hours} hours" if hours else None})
        all_rows = cur.fetchall()
        rows = [r for r in all_rows if r.get("gain_pct") is None or (r.get("gain_pct") or 0) >= 0.20]

        sim_rows = [r for r in all_rows if r.get("mc_min_early") and r.get("time_to_bottom") is not None]
        sim_mints = list({r["mint"] for r in sim_rows})
        ticks_by_mint = {}
        if sim_mints:
            ts_floor = min(r["ts_migration"] for r in sim_rows if r.get("ts_migration"))
            ts_ceil = max(r["ts_migration"] for r in sim_rows if r.get("ts_migration")) + timedelta(minutes=70)
            cur.execute("""
                SELECT mint, tick_ts, elapsed_sec, mc, mc_open, mc_high, mc_low, source
                FROM token_ticks
                WHERE mint = ANY(%s)
                  AND tick_ts BETWEEN %s AND %s
                ORDER BY mint, tick_ts
            """, (sim_mints, ts_floor, ts_ceil))
            for t in cur.fetchall():
                ticks_by_mint.setdefault(t["mint"], []).append(dict(t))

        cur.execute("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'tp') AS tp,
                COUNT(*) FILTER (WHERE status = 'sl') AS sl,
                COUNT(*) FILTER (WHERE status = 'trailing_active') AS trailing_active,
                COUNT(*) FILTER (WHERE status = 'open') AS open,
                COUNT(*) FILTER (WHERE status = 'expired') AS expired,
                COUNT(*) FILTER (WHERE status NOT IN ('tp','sl','trailing_active','open','expired')) AS other,
                AVG(config_bounce_drop_min) AS avg_drop_cfg,
                AVG(config_bounce_min_pct) AS avg_bounce_cfg,
                AVG(drop_from_mig_signal) AS avg_drop_signal,
                AVG(bounce_pct_signal) AS avg_bounce_signal,
                AVG(CASE WHEN mc_bottom > 0 AND mc_at_signal > 0
                         THEN (mc_at_signal - mc_bottom) / mc_bottom END) AS avg_signal_above_bottom
            FROM trades
            WHERE created_at >= NOW() - %(live_interval)s::interval
        """, {"live_interval": f"{live_hours} hours"})
        live_stats = dict(cur.fetchone() or {})

        cur.close()
        conn.close()
    except Exception as e:
        import traceback
        try:
            cur.close(); conn.close()
        except Exception:
            pass
        return f"<pre style='color:red'>{traceback.format_exc()}</pre>"

    # ── Simulação de WR por drop mínimo com entrada no bounce de 10% ───────────
    def _tick_high_low(t):
        high = t.get("mc_high") or t.get("mc") or 0
        low = t.get("mc_low") or t.get("mc") or 0
        open_mc = t.get("mc_open") or t.get("mc") or 0
        close_mc = t.get("mc") or 0
        return float(high or 0), float(low or 0), float(open_mc or 0), float(close_mc or 0)

    def _simulate_entry(row):
        mint = row["mint"]
        bottom = float(row.get("mc_min_early") or 0)
        drop = row.get("drop_pct")
        ts_mig = row.get("ts_migration")
        ttb = row.get("time_to_bottom")
        ticks = ticks_by_mint.get(mint, [])
        if not bottom or drop is None or ts_mig is None or ttb is None:
            return {"row": row, "drop_pct": drop, "status": "sem_fundo", "pnl": None}

        bottom_ts = ts_mig + timedelta(minutes=float(ttb))
        end_ts = ts_mig + timedelta(minutes=70)
        entry_mc = bottom * (1 + entry_pct)
        tp_mc = entry_mc * (1 + tp_pct)
        sl_mc = entry_mc * (1 - sl_pct)

        post_bottom = [t for t in ticks if bottom_ts < t.get("tick_ts") <= end_ts]
        entry_ts = None
        entry_elapsed = None
        for t in post_bottom:
            high, _low, _open, close = _tick_high_low(t)
            if high >= entry_mc:
                entry_ts = t["tick_ts"]
                entry_elapsed = t.get("elapsed_sec")
                break

        if entry_ts is None:
            last_mc = None
            if post_bottom:
                *_, last_mc = _tick_high_low(post_bottom[-1])
            return {
                "row": row, "drop_pct": drop, "status": "sem_entry", "entry_mc": entry_mc,
                "pnl": (last_mc / entry_mc - 1) if last_mc and entry_mc else None,
            }

        post_entry = [t for t in ticks if entry_ts < t.get("tick_ts") <= end_ts]
        max_mc = entry_mc
        for t in post_entry:
            high, low, open_mc, close = _tick_high_low(t)
            max_mc = max(max_mc, high)
            tp_hit = high >= tp_mc
            sl_hit = low <= sl_mc
            if tp_hit and sl_hit:
                midpoint = (tp_mc + sl_mc) / 2
                if open_mc >= midpoint:
                    return {"row": row, "drop_pct": drop, "status": "tp", "entry_mc": entry_mc, "entry_elapsed": entry_elapsed, "pnl": tp_pct}
                return {"row": row, "drop_pct": drop, "status": "sl", "entry_mc": entry_mc, "entry_elapsed": entry_elapsed, "pnl": -sl_pct}
            if tp_hit:
                return {"row": row, "drop_pct": drop, "status": "tp", "entry_mc": entry_mc, "entry_elapsed": entry_elapsed, "pnl": tp_pct}
            if sl_hit:
                return {"row": row, "drop_pct": drop, "status": "sl", "entry_mc": entry_mc, "entry_elapsed": entry_elapsed, "pnl": -sl_pct}

        if post_entry:
            *_, last_mc = _tick_high_low(post_entry[-1])
            pnl = (last_mc / entry_mc - 1) if last_mc and entry_mc else None
        else:
            pnl = None
        return {
            "row": row, "drop_pct": drop, "status": "aberto", "entry_mc": entry_mc,
            "entry_elapsed": entry_elapsed, "pnl": pnl,
            "max_gain": (max_mc / entry_mc - 1) if entry_mc else None,
        }

    sim_results = [_simulate_entry(r) for r in sim_rows]

    def _summarize_results(items):
        n_tokens = len(items)
        entries = [x for x in items if x["status"] in ("tp", "sl", "aberto")]
        tp_n = sum(1 for x in entries if x["status"] == "tp")
        sl_n = sum(1 for x in entries if x["status"] == "sl")
        open_n = sum(1 for x in entries if x["status"] == "aberto")
        no_entry_n = sum(1 for x in items if x["status"] == "sem_entry")
        resolved = tp_n + sl_n
        wr_resolved = tp_n / resolved if resolved else None
        wr_entries = tp_n / len(entries) if entries else None
        pnl_vals = [x.get("pnl") for x in entries if x.get("pnl") is not None]
        ev = sum(pnl_vals) / len(pnl_vals) if pnl_vals else None
        return {
            "tokens": n_tokens,
            "entries": len(entries),
            "tp": tp_n,
            "sl": sl_n,
            "open": open_n,
            "no_entry": no_entry_n,
            "resolved": resolved,
            "wr_resolved": wr_resolved,
            "wr_entries": wr_entries,
            "ev": ev,
        }

    drop_thresholds = [0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60, 0.70, 0.80]
    threshold_stats = []
    for th in drop_thresholds:
        subset = [x for x in sim_results if x.get("drop_pct") is not None and x["drop_pct"] >= th]
        st = _summarize_results(subset)
        st["drop_min"] = th
        threshold_stats.append(st)

    bucket_edges = [0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60, 0.80, 9.99]
    bucket_stats = []
    for lo, hi in zip(bucket_edges, bucket_edges[1:]):
        subset = [x for x in sim_results if x.get("drop_pct") is not None and lo <= x["drop_pct"] < hi]
        st = _summarize_results(subset)
        st["lo"] = lo
        st["hi"] = hi
        bucket_stats.append(st)

    best_drop = max(
        (s for s in threshold_stats if s["entries"] >= min_sample),
        key=lambda s: ((s["ev"] if s["ev"] is not None else -999), (s["wr_entries"] if s["wr_entries"] is not None else -1), s["entries"]),
        default=None,
    )

    overall_sim = _summarize_results(sim_results)

    # ── Classifica cada token em 5 categorias ────────────────────────────────
    CAT_UP    = "subiu_direto"   # nunca caiu abaixo da migração (drop <= 0)
    CAT_LEVE  = "dip_leve"       # caiu 1–15%
    CAT_MEDIO = "dip_medio"      # caiu 15–30% ← zona de oportunidade
    CAT_FORTE = "dip_forte"      # caiu >30% ← estratégia atual captura esses
    CAT_SEM   = "sem_dados"      # sem fundo na janela (nem token_ticks nem erpc/kline)

    def categorize(drop_pct):
        if drop_pct is None:              return CAT_SEM
        if drop_pct <= 0:                 return CAT_UP
        if drop_pct < 0.15:               return CAT_LEVE
        if drop_pct < 0.30:               return CAT_MEDIO
        return CAT_FORTE

    cats = {c: [] for c in [CAT_UP, CAT_LEVE, CAT_MEDIO, CAT_FORTE, CAT_SEM]}
    for r in rows:
        cats[categorize(r["drop_pct"])].append(r)

    total_com_dados = len(rows) - len(cats[CAT_SEM])
    total_all       = len(rows)

    def avg_gain(lst):
        g = [r["gain_pct"] for r in lst if (r.get("gain_pct") or 0) > 0]
        return sum(g) / len(g) if g else None

    # ── Conclusão em linguagem simples ───────────────────────────────────────
    n_up    = len(cats[CAT_UP])
    n_leve  = len(cats[CAT_LEVE])
    n_medio = len(cats[CAT_MEDIO])
    n_forte = len(cats[CAT_FORTE])
    n_sem   = len(cats[CAT_SEM])

    pct = lambda n: f"{n/total_com_dados*100:.0f}%" if total_com_dados else "—"

    # Quantos o bot PODERIA pegar com drop menor
    capturavel = n_leve + n_medio   # 15-30% capturável com BOUNCE_DROP_MIN=0.15

    conclusao_html = f"""
<b>📊 O que aconteceu com {total_all} tokens que subiram ≥20%:</b><br><br>
<span style='color:#f87171'>🚀 {n_up} tokens ({pct(n_up)}) subiram direto</span> — nunca caíram abaixo do preço de migração. O bot não consegue pegar esses: quando detecta, o token já está no topo.<br>
<span style='color:#fbbf24'>📉 {n_leve} tokens ({pct(n_leve)}) caíram 1–15% antes de subir</span> — dip muito pequeno. Capturar esses exigiria BOUNCE_DROP_MIN=0.05 (arriscado, muitos falsos positivos).<br>
<span style='color:#4ade80'>📉 {n_medio} tokens ({pct(n_medio)}) caíram 15–30% antes de subir</span> — <b>ZONA DE OPORTUNIDADE.</b> Baixar BOUNCE_DROP_MIN de 0.30 para 0.15 capturaria esses.<br>
<span style='color:#60a5fa'>📉 {n_forte} tokens ({pct(n_forte)}) caíram >30% antes de subir</span> — a estratégia atual JÁ deveria capturar esses. Se não capturou, é outro problema (A.5, A.6, erpc, pool).<br>
{"<span style='color:#8b949e'>⚠️ " + str(n_sem) + " tokens sem fundo na janela de 20min (nem token_ticks nem erpc/kline via mc_bottom).</span><br>" if n_sem else ""}
<br>
<b>Ação recomendada:</b> {'Baixar <code>BOUNCE_DROP_MIN</code> de 0.30 para 0.15 adicionaria <b>' + str(capturavel) + ' tokens</b> à janela de compra (' + pct(capturavel) + ' dos vencedores). Testar com os dados limpos atuais (n≥30).' if capturavel > 5 else 'Poucos tokens na zona 15–30%. Manter BOUNCE_DROP_MIN=0.30 por ora.'}
"""

    # ── HTML ─────────────────────────────────────────────────────────────────
    h = """<!DOCTYPE html><html><head>
<meta charset='utf-8'><title>Drop Real — NeideBOT</title>
<style>
  body{background:#0d1117;color:#e6edf3;font-family:-apple-system,sans-serif;font-size:13px;padding:20px;margin:0}
  h2{color:#58a6ff;margin:0 0 4px;font-size:20px}
  .sub{color:#8b949e;font-size:11px;margin:0 0 16px}
  .box{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px 20px;margin-bottom:20px;line-height:1.9}
  .cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}
  .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 18px;min-width:150px;text-align:center}
  .card .v{font-size:28px;font-weight:700}
  .card .l{font-size:11px;color:#8b949e;margin-top:2px}
  .card .g{font-size:11px;margin-top:4px}
  h3{color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:.06em;margin:20px 0 8px}
  table{border-collapse:collapse;width:100%}
  th{background:#161b22;color:#8b949e;padding:7px 10px;text-align:left;position:sticky;top:0;font-size:11px;border-bottom:1px solid #30363d}
  td{padding:5px 10px;border-bottom:1px solid #21262d;white-space:nowrap}
  tr:hover td{background:#161b22}
  a{color:#58a6ff;text-decoration:none}
  .dim{color:#475569}
  .sep{background:#0d1117;color:#58a6ff;font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;padding:6px 10px;border-bottom:1px solid #21262d}
  code{background:#21262d;padding:1px 5px;border-radius:3px;font-size:12px}
</style></head><body>
"""
    period_label = f"últimas {hours}h" if hours else "todo o histórico"
    h += f"<h2>📉 Shadow WR por drop real — não é WR real dos trades</h2>"
    h += f"<p class='sub'>Migrations de {period_label}; tabela histórica abaixo filtra tokens que <b>subiram ≥20%</b> · "
    h += f"{total_all} tokens vencedores na tabela histórica · base WR: {overall_sim['tokens']} tokens com fundo · "
    h += f"entry bounce={entry_pct:.0%} · TP={tp_pct:.0%} · SL={sl_pct:.0%} · "
    h += f"<a href='?'>todos</a> · <a href='?hours=24'>24h</a> · <a href='?hours=48'>48h</a> · <a href='?hours=72'>72h</a></p>"

    def _pct_html(v):
        return "—" if v is None else f"{v*100:.1f}%"

    def _ev_html(v):
        if v is None:
            return "—"
        cls = "#4ade80" if v > 0 else "#f87171" if v < 0 else "#8b949e"
        return f"<span style='color:{cls}'>{v*100:+.1f}%</span>"

    best_txt = "Sem amostra suficiente para recomendar drop."
    if best_drop:
        best_txt = (
            f"Melhor BOUNCE_DROP_MIN no shadow desta amostra: <b>{best_drop['drop_min']:.0%}</b> "
            f"(entries={best_drop['entries']}, TP={best_drop['tp']}, SL={best_drop['sl']}, "
            f"WR entradas={_pct_html(best_drop['wr_entries'])}, EV={_ev_html(best_drop['ev'])})."
        )

    resolved_live = (live_stats.get("tp") or 0) + (live_stats.get("sl") or 0)
    live_wr_resolved = (live_stats.get("tp") or 0) / resolved_live if resolved_live else None
    live_wr_all = (live_stats.get("tp") or 0) / (live_stats.get("total") or 0) if live_stats.get("total") else None
    h += "<h3>Trades reais recentes — comparação com o shadow</h3>"
    h += "<div class='box'>"
    h += f"Últimas {live_hours}h: <b>{live_stats.get('total') or 0} trades reais</b> · "
    h += f"TP={live_stats.get('tp') or 0} · SL={live_stats.get('sl') or 0} · trailing_active={live_stats.get('trailing_active') or 0} · open={live_stats.get('open') or 0} · expired={live_stats.get('expired') or 0} · "
    h += f"WR real resolvido={_pct_html(live_wr_resolved)} · WR real / total={_pct_html(live_wr_all)}.<br>"
    h += f"Config/sinal médios: drop_cfg={_pct_html(live_stats.get('avg_drop_cfg'))} · bounce_cfg={_pct_html(live_stats.get('avg_bounce_cfg'))} · drop_sinal={_pct_html(live_stats.get('avg_drop_signal'))} · bounce_sinal={_pct_html(live_stats.get('avg_bounce_signal'))} · entrada acima do fundo={_pct_html(live_stats.get('avg_signal_above_bottom'))}."
    h += "<br><b>Importante:</b> a tabela abaixo é um <u>shadow backtest idealizado</u>. Ela não usa fill real, slippage, fees, latência GMGN, Helius gate, nem trailing real; por isso pode ficar muito acima do WR real."
    h += "</div>"

    h += f"<h3>Shadow backtest por drop mínimo — entrada quando bounce chega a +{entry_pct:.0%} do fundo</h3>"
    h += "<div class='box'>"
    h += f"<b>{best_txt}</b><br>"
    h += f"Base: {overall_sim['tokens']} tokens com fundo calculável · {overall_sim['entries']} entradas acionadas por bounce ≥ {entry_pct:.0%} · "
    h += f"TP={overall_sim['tp']} · SL={overall_sim['sl']} · abertos/sem TP-SL={overall_sim['open']} · sem entry={overall_sim['no_entry']} · "
    h += f"WR entradas={_pct_html(overall_sim['wr_entries'])} · WR resolvido={_pct_html(overall_sim['wr_resolved'])} · EV={_ev_html(overall_sim['ev'])}."
    h += "</div>"

    h += "<table style='margin-bottom:20px'><tr><th>Drop mínimo</th><th>Tokens</th><th>Entradas shadow</th><th>TP shadow</th><th>SL shadow</th><th>Aberto shadow</th><th>Sem entry</th><th>Shadow WR entradas</th><th>Shadow WR TP/SL</th><th>EV shadow</th></tr>"
    for st in threshold_stats:
        cls = "green" if (st['wr_entries'] or 0) >= 0.30 else "yellow" if (st['wr_entries'] or 0) >= 0.20 else "red"
        best_mark = " ⭐" if best_drop and st['drop_min'] == best_drop['drop_min'] else ""
        h += "<tr>"
        h += f"<td><b>{st['drop_min']:.0%}{best_mark}</b></td>"
        h += f"<td>{st['tokens']}</td><td>{st['entries']}</td><td style='color:#4ade80'>{st['tp']}</td><td style='color:#f87171'>{st['sl']}</td><td>{st['open']}</td><td class='dim'>{st['no_entry']}</td>"
        h += f"<td class='{cls}'>{_pct_html(st['wr_entries'])}</td><td>{_pct_html(st['wr_resolved'])}</td><td>{_ev_html(st['ev'])}</td>"
        h += "</tr>"
    h += "</table>"

    h += "<h3>Shadow WR por faixa real de drop</h3>"
    h += "<table style='margin-bottom:20px'><tr><th>Faixa de drop</th><th>Tokens</th><th>Entradas shadow</th><th>TP shadow</th><th>SL shadow</th><th>Aberto shadow</th><th>Sem entry</th><th>Shadow WR entradas</th><th>Shadow WR TP/SL</th><th>EV shadow</th></tr>"
    for st in bucket_stats:
        label = f"{st['lo']:.0%}–{st['hi']:.0%}" if st['hi'] < 9 else f"≥{st['lo']:.0%}"
        cls = "green" if (st['wr_entries'] or 0) >= 0.30 else "yellow" if (st['wr_entries'] or 0) >= 0.20 else "red"
        h += "<tr>"
        h += f"<td><b>{label}</b></td><td>{st['tokens']}</td><td>{st['entries']}</td><td style='color:#4ade80'>{st['tp']}</td><td style='color:#f87171'>{st['sl']}</td><td>{st['open']}</td><td class='dim'>{st['no_entry']}</td>"
        h += f"<td class='{cls}'>{_pct_html(st['wr_entries'])}</td><td>{_pct_html(st['wr_resolved'])}</td><td>{_ev_html(st['ev'])}</td>"
        h += "</tr>"
    h += "</table>"

    h += "<p class='sub'>Metodologia SHADOW: para cada token com fundo, a entrada hipotética ocorre no primeiro tick após o fundo cujo high/MC toca fundo × " + f"{1+entry_pct:.2f}" + ". TP/SL são avaliados nos ticks seguintes até 70min pós-migração; velas kline usam high/low e ticks simples usam mc. Isto NÃO é o trade real: o bot real dispara em scanner.py com BOUNCE_DROP_MIN/BOUNCE_MIN_PCT, compra pelo GMGN, registra mc_at_signal/fill e o executor usa SL/TP/trailing sobre a entrada real/proxy.</p>"

    h += f"<div class='box'>{conclusao_html}</div>"

    # Cards de resumo
    cat_cfg = [
        (CAT_UP,    "#f87171", "🚀 Subiu direto",    "Nunca caiu abaixo da migração"),
        (CAT_LEVE,  "#fbbf24", "📉 Dip 1–15%",        "Dip pequeno (difícil capturar)"),
        (CAT_MEDIO, "#4ade80", "📉 Dip 15–30%",       "Oportunidade — BOUNCE=0.15"),
        (CAT_FORTE, "#60a5fa", "📉 Dip >30%",         "Bot deveria ter comprado"),
        (CAT_SEM,   "#475569", "❓ Sem dados",         "Spikaram antes dos ticks"),
    ]
    h += "<div class='cards'>"
    for cat, color, label, desc in cat_cfg:
        n   = len(cats[cat])
        ag  = avg_gain(cats[cat])
        pct_s = f"{n/total_all*100:.0f}%" if total_all else "—"
        h += f"<div class='card'>"
        h += f"<div class='v' style='color:{color}'>{n}</div>"
        h += f"<div class='l'>{label}</div>"
        h += f"<div class='g' style='color:#8b949e'>{pct_s} do total</div>"
        if ag is not None:
            h += f"<div class='g' style='color:#4ade80'>avg gain {ag:+.0%}</div>"
        h += f"<div class='g dim' style='font-size:10px'>{desc}</div>"
        h += "</div>"
    h += "</div>"

    # Tabela individual por categoria
    def token_rows(lst, cat_color):
        out = ""
        for r in lst:
            ts    = r.get("ts_migration")
            dt    = ts.astimezone(BRT).strftime("%d/%m %H:%M") if ts else "—"
            nome  = r.get("token_nome") or r["mint"][:8]
            mc_m  = r.get("mc_migration") or 0
            mc_mn = r.get("mc_min_early") or 0
            drop  = r.get("drop_pct")
            peak  = r.get("mc_peak_ticks") or 0
            gain  = r.get("gain_pct") or 0
            ttp   = r.get("time_to_peak")
            bsrc  = r.get("bottom_source") or "—"
            mint  = r["mint"]
            gmgn  = f"https://gmgn.ai/sol/token/{mint}"
            dex   = f"https://dexscreener.com/solana/{mint}"
            gc    = "#4ade80" if gain >= 0.5 else "#fbbf24" if gain >= 0.2 else "#94a3b8"
            out += "<tr>"
            out += f"<td><a href='{gmgn}' target='_blank'>{nome}</a></td>"
            out += f"<td class='dim'>{dt}</td>"
            out += f"<td>${mc_m:,.0f}</td>"
            if drop is None:
                out += f"<td class='dim'>—</td><td class='dim'>sem fundo</td>"
            elif drop <= 0:
                out += f"<td class='dim'>—</td><td style='color:{cat_color}'>subiu direto</td>"
            else:
                out += f"<td>${mc_mn:,.0f}</td><td style='color:{cat_color}'>{drop:.0%} de queda</td>"
            out += f"<td class='dim'>{bsrc}</td>"
            out += f"<td style='color:{gc}'>{gain:+.0%}</td>"
            out += f"<td>{ttp:.1f}m</td>" if ttp is not None else "<td class='dim'>—</td>"
            out += f"<td><a href='{dex}' target='_blank'>dex</a></td>"
            out += "</tr>"
        return out

    h += "<table><tr><th>Token</th><th>Hora</th><th>MC na migração</th><th>MC mínimo</th><th>Queda</th><th>Fonte fundo</th><th>Subida total</th><th>⏱ pico</th><th></th></tr>"

    for cat, color, label, _ in cat_cfg:
        lst = cats[cat]
        if not lst:
            continue
        ag = avg_gain(lst)
        ag_s = f" · avg {ag:+.0%}" if ag is not None else ""
        h += f"<tr class='sep'><td colspan='9'>{label} — {len(lst)} tokens{ag_s}</td></tr>"
        h += token_rows(lst, color)

    h += "</table>"
    h += "</body></html>"
    return h
