"""GMGN Filtered Trending — coletor passivo (research-only).

Busca periodicamente a lista de Trending da GMGN (/v1/market/rank) por timeframe,
aplica a faixa operacional da hipótese (MC/liquidez/fee) do lado do bot e cruza
com o universo do scanner (migrations recentes). Salva labels de demanda.

PASSIVO por design:
  - não compra, não vende, não aciona executor
  - não altera scanner principal / H26 / H27
  - NUNCA consulta GMGN por mint individual — só busca a lista por timeframe/profile
  - cruzamento scanner ∩ trending acontece localmente

Profiles iniciais (faixa da hipótese):
  migration_band_25_35k_5m : tf=5m, MC 25k-35k, liq>5k, fee>1 SOL
  migration_band_25_35k_1h : tf=1h, MC 25k-35k, liq>5k, fee>1 SOL

⚠️ Limitação de filtro (ver docs/gmgn_filtered_trending.md):
  /v1/market/rank não expõe nomes de query param confiáveis para min/max de
  MC/liq/fee. Por isso o filtro é aplicado CLIENT-SIDE sobre o top-100 retornado.
  O campo de fee usado é `gas_fee` (unidade incerta — tratado como best-effort;
  o gate de fee autoritativo é `migrations.total_fee_sol` na interseção).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import gmgn_filtered_trending_store as store
from gmgn_client import GmgnClient, RateLimitError

logger = logging.getLogger("gmgn_filtered_trending")


def _float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def _int(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except Exception:
        return None


def _csv(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default).strip()
    return [p.strip() for p in raw.split(",") if p.strip()]


class FilteredTrendingCollector:
    """Coletor de Trending filtrado por profile, cruzando com o scanner."""

    def __init__(self, gmgn: GmgnClient):
        self.gmgn = gmgn
        self.chain = os.getenv("GMGN_FILTERED_TRENDING_CHAIN", "sol")
        self.timeframes = _csv("GMGN_FILTERED_TRENDING_TIMEFRAMES", "5m,1h")
        self.poll_sec = int(os.getenv("GMGN_FILTERED_TRENDING_POLL_SEC", "60"))
        self.order_by = os.getenv("GMGN_FILTERED_TRENDING_ORDER_BY", "default")
        self.mc_min = float(os.getenv("GMGN_FILTERED_TRENDING_MC_MIN", "25000"))
        self.mc_max = float(os.getenv("GMGN_FILTERED_TRENDING_MC_MAX", "35000"))
        self.liq_min = float(os.getenv("GMGN_FILTERED_TRENDING_LIQ_MIN", "5000"))
        self.fee_min = float(os.getenv("GMGN_FILTERED_TRENDING_FEE_MIN", "1.0"))
        # janela do universo do scanner para a interseção (migrations recentes)
        self.scanner_window_min = int(os.getenv("GMGN_FILTERED_TRENDING_SCANNER_WINDOW_MIN", "60"))
        self.dedup_sec = int(os.getenv("GMGN_FILTERED_TRENDING_DEDUP_SEC", "600"))
        # salvar também tokens GMGN fora do scanner? (diagnóstico, não entra na hipótese)
        self.keep_outside = os.getenv("GMGN_FILTERED_TRENDING_KEEP_OUTSIDE", "false").lower() == "true"
        self.run_once = os.getenv("GMGN_FILTERED_TRENDING_RUN_ONCE", "false").lower() == "true"
        # gate de fee client-side é frouxo por padrão (unidade de gas_fee incerta)
        self.apply_fee_filter = os.getenv("GMGN_FILTERED_TRENDING_APPLY_FEE_FILTER", "false").lower() == "true"
        self._ban_until = 0.0

    def _profile_name(self, timeframe: str) -> str:
        mc_lo = int(self.mc_min // 1000)
        mc_hi = int(self.mc_max // 1000)
        return f"migration_band_{mc_lo}_{mc_hi}k_{timeframe}"

    def _in_band(self, mc: float | None, liq: float | None, fee: float | None) -> bool:
        if mc is None or mc < self.mc_min or mc > self.mc_max:
            return False
        if liq is None or liq < self.liq_min:
            return False
        if self.apply_fee_filter and (fee is None or fee < self.fee_min):
            return False
        return True

    def _normalize(self, item: dict, idx: int) -> dict | None:
        mint = item.get("address") or item.get("token_address") or item.get("mint")
        if not mint:
            return None
        return {
            "mint": mint,
            "symbol": item.get("symbol"),
            "rank": _int(item.get("rank")) or idx,
            "market_cap": _float(item.get("market_cap") or item.get("usd_market_cap")),
            "liquidity": _float(item.get("liquidity")),
            # gas_fee é o campo de fee no market/rank (unidade incerta — best-effort)
            "total_fee_sol": _float(item.get("gas_fee") or item.get("total_fee")),
            "volume_window": _float(item.get("volume")),
            "txs_window": _int(item.get("swaps")),
            "buys_window": _int(item.get("buys")),
            "sells_window": _int(item.get("sells")),
            "holders": _int(item.get("holder_count")),
            "open_timestamp": _int(item.get("open_timestamp")),
            "raw": item,
        }

    def _token_age_minutes(self, open_ts: int | None) -> float | None:
        if not open_ts:
            return None
        return max(0.0, (time.time() - open_ts) / 60.0)

    def collect_cycle(self) -> dict:
        """Um ciclo: para cada timeframe, busca trending, filtra, cruza, salva."""
        scanner_universe = store.get_scanner_universe(self.scanner_window_min)
        scanner_mints = set(scanner_universe.keys())

        totals = {"profiles": 0, "gmgn_rows": 0, "in_band": 0,
                  "crossed_scanner": 0, "labels_saved": 0, "outside_saved": 0}

        for timeframe in self.timeframes:
            profile = self._profile_name(timeframe)
            try:
                rows_raw = self.gmgn.get_market_trending(
                    chain=self.chain, interval=timeframe,
                    limit=100, order_by=self.order_by, direction="desc",
                )
            except RateLimitError:
                raise
            except Exception as e:
                logger.warning("[%s] market/rank falhou: %s", profile, e)
                continue

            gmgn_count = in_band = crossed = 0
            to_save: list[dict] = []
            for idx, item in enumerate(rows_raw, start=1):
                if not isinstance(item, dict):
                    continue
                gmgn_count += 1
                norm = self._normalize(item, idx)
                if not norm:
                    continue
                if not self._in_band(norm["market_cap"], norm["liquidity"], norm["total_fee_sol"]):
                    continue
                in_band += 1

                in_scanner = norm["mint"] in scanner_mints
                if not in_scanner and not self.keep_outside:
                    continue
                if in_scanner:
                    crossed += 1

                uni = scanner_universe.get(norm["mint"], {})
                mig_age = None
                if uni.get("ts_migration"):
                    mig_age = int((datetime.now(timezone.utc) - uni["ts_migration"]).total_seconds())

                to_save.append({
                    "mint": norm["mint"],
                    "profile_name": profile,
                    "timeframe": timeframe,
                    "chain": self.chain,
                    "order_by": self.order_by,
                    "rank_in_filtered_trending": norm["rank"],
                    "symbol": norm["symbol"],
                    "market_cap": norm["market_cap"],
                    "liquidity": norm["liquidity"],
                    "total_fee_sol": norm["total_fee_sol"],
                    "volume_window": norm["volume_window"],
                    "txs_window": norm["txs_window"],
                    "buys_window": norm["buys_window"],
                    "sells_window": norm["sells_window"],
                    "holders": norm["holders"],
                    "token_age_minutes": self._token_age_minutes(norm["open_timestamp"]),
                    "scanner_status_at_seen": uni.get("status") if in_scanner else "outside_scanner",
                    "scanner_mc_at_seen": uni.get("mc_migration") if in_scanner else None,
                    "migration_age_sec": mig_age,
                    "outside_scanner": not in_scanner,
                    "bounce_ts": uni.get("bounce_ts") if in_scanner else None,
                    "raw": norm["raw"],
                })

            saved = store.insert_labels(to_save, dedup_sec=self.dedup_sec)
            outside_saved = sum(1 for r in to_save if r["outside_scanner"])
            totals["profiles"] += 1
            totals["gmgn_rows"] += gmgn_count
            totals["in_band"] += in_band
            totals["crossed_scanner"] += crossed
            totals["labels_saved"] += saved
            totals["outside_saved"] += outside_saved
            logger.info(
                "[%s] gmgn=%d in_band=%d scanner_universe=%d crossed=%d labels_saved=%d",
                profile, gmgn_count, in_band, len(scanner_mints), crossed, saved,
            )

        # Recalcula outcomes para labels que maturaram (Fase 4) — bounded
        try:
            oc = store.refresh_outcomes(max_labels=int(os.getenv("GMGN_FILTERED_TRENDING_OUTCOME_BATCH", "500")))
            totals["outcomes"] = oc
        except Exception as e:
            logger.warning("refresh_outcomes falhou: %s", e)

        logger.info("FilteredTrending ciclo: %s", totals)
        return totals

    def start(self) -> None:
        logger.info(
            "FilteredTrendingCollector iniciado chain=%s timeframes=%s band=MC[%g-%g] liq>%g fee>%g "
            "poll=%ds scanner_window=%dmin dedup=%ds keep_outside=%s apply_fee_filter=%s",
            self.chain, self.timeframes, self.mc_min, self.mc_max, self.liq_min, self.fee_min,
            self.poll_sec, self.scanner_window_min, self.dedup_sec, self.keep_outside, self.apply_fee_filter,
        )
        store.ensure_schema()
        while True:
            try:
                self.collect_cycle()
            except RateLimitError as e:
                wait = max(1.0, e.reset_at - time.time()) + 1
                logger.warning("GMGN rate limit no FilteredTrending; aguardando %.1fs", wait)
                time.sleep(wait)
            except Exception as e:
                logger.exception("FilteredTrending erro: %s", e)
            if self.run_once:
                return
            time.sleep(self.poll_sec)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    store.ensure_schema()
    FilteredTrendingCollector(GmgnClient()).start()


if __name__ == "__main__":
    main()
