"""Walk-forward grid-strategy classification test - the RIGHT way to
validate this, per the owner's 2026-08-16 correction: classify each
symbol's regime AS OF a point ~30 days ago (using ONLY data available
up to that point, no look-ahead), assign it whichever strategy its
OWN regime at that time qualified it for (flat if rango, trend-long if
fuerte+uptrend, skipped if leve or fuerte+downtrend since trend.py is
long-only), then simulate forward from that point with a take-profit
exit instead of holding the full window regardless of performance.

This replaced an earlier, wrong approach that picked "whatever looks
best RIGHT NOW" and backtested an arbitrary trailing window - that
either tested a stale classification against a regime that had already
changed (the flat/HYPEUSDT case) or cherry-picked today's most extreme
mover for the trend case (HUSDT, a token mid-pump), which is a
systematically biased sample, not a fair test of the method.

Two-pass since 2026-08-16 (owner's own observation: "invertir en 47 de
100 es mucho, quizas tomar los mejores?"): pass 1 classifies EVERY
symbol and scores flat candidates with the full manual-screener rubric
(atlantis/grid_screener/scoring.py - volatility, position in range,
liquidity, BTC correlation), ranks trend candidates by efficiency
ratio; only the top-ranked survivors go through the expensive hourly-
bar backtest in pass 2. Concentrates capital in higher-quality setups
instead of spreading thin across every symbol that merely clears the
regime bar.

Usage:
  python3 scripts/backtest_grid_walkforward.py --lookback-days 30 --forward-days 30 --universe-size 100
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlantis.grid_screener.binance_client import fetch_klines_range, fetch_screenable_universe  # noqa: E402
from atlantis.grid_screener.metrics import (  # noqa: E402
    CORR_WINDOW_DAYS,
    correlacion_bucket,
    daily_log_returns,
    daily_volatility_pct,
    efficiency_ratio,
    liquidez_bucket,
    net_move_pct,
    pearson,
    position_in_range_pct,
    regimen_from_er,
)
from atlantis.grid_screener.scoring import Candidate, evaluar  # noqa: E402
from atlantis.grid_trader.backtest_engine import run_flat_backtest, run_trend_backtest  # noqa: E402
from atlantis.grid_trader.flat import RANGE_WINDOW_DAYS as FLAT_WINDOW  # noqa: E402
from atlantis.grid_trader.flat import NUM_LEVELS as FLAT_NUM_LEVELS  # noqa: E402
from atlantis.grid_trader.market_gate import btc_market_ok  # noqa: E402
from atlantis.grid_trader.trend import RANGE_WINDOW_DAYS as TREND_WINDOW  # noqa: E402
from atlantis.grid_trader.trend import NUM_LEVELS as TREND_NUM_LEVELS  # noqa: E402

FEE_RATE = Decimal("0.0004")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=30, help="hace cuanto se clasifica el regimen (T0)")
    parser.add_argument("--forward-days", type=int, default=30, help="cuanto simular hacia adelante desde T0")
    parser.add_argument("--universe-size", type=int, default=20, help="cuantos simbolos probar (top liquidez)")
    parser.add_argument("--usd-per-level", type=str, default="20")
    parser.add_argument("--take-profit-pct", type=str, default="10", help="%% del capital de referencia (usd_per_level * num_niveles)")
    parser.add_argument(
        "--stop-loss-pct", type=str, default="35",
        help="%% del capital de referencia. Default 35 - calibrado 2026-08-16 contra jun/jul reales: "
             "mas ajustado (20%%) corta recuperaciones reales (AKEUSDT, VELVETUSDT) sin mejorar el mes malo "
             "mucho mas que esto. Pasar '' o 0 para desactivarlo.",
    )
    parser.add_argument("--no-btc-gate", action="store_true", help="desactiva el filtro de regimen de BTC (por defecto esta activo)")
    parser.add_argument(
        "--flat-min-score", type=int, default=0,
        help="score minimo (rubrica del screener manual, 0-100) - default 0 (desactivado): el score compuesto "
             "no mostro correlacion real con el resultado en may/jun/jul 2026 (r=+0.03), ver flat-pos-range/"
             "flat-max-corr para los filtros que SI mostraron señal real.",
    )
    parser.add_argument(
        "--flat-pos-range", type=str, default="20,80",
        help="rango de posicion-en-el-rango-de-30d (%%) permitido, 'min,max'. Default 20,80 - calibrado "
             "2026-08-16: posiciones extremas (<20 o >80) promediaron -1.67%% ROI vs +1.62%% en el medio, "
             "n=121 sobre may/jun/jul reales.",
    )
    parser.add_argument(
        "--flat-max-corr", type=str, default="0.5",
        help="correlacion absoluta maxima con BTC permitida (0-1). Default 0.5 - calibrado 2026-08-16 sobre "
             "may/jun/jul (ROI 0.69%%->2.66%%, n=121->61) y CONFIRMADO en abril como holdout genuino "
             "(ROI 6.26%%->10.2%%, win rate 86.7%%->100%%, n=30->9). Ver docs/GRID_TRADER_STRATEGIES.md.",
    )
    parser.add_argument("--trend-top-n", type=int, default=10, help="cuantos candidatos de tendencia tomar como maximo, ordenados por fuerza (ER)")
    parser.add_argument("--csv-out", type=str, default=None, help="si se pasa, agrega (append) las filas de flat con sus features crudas + resultado a este CSV, para analisis de correlacion")
    parser.add_argument("--trend-csv-out", type=str, default=None, help="igual que --csv-out pero para los candidatos de tendencia")
    args = parser.parse_args()

    usd_per_level = Decimal(args.usd_per_level)
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(days=args.lookback_days)
    forward_end = t0 + timedelta(days=args.forward_days)
    if forward_end > now:
        print(f"aviso: forward-days ({args.forward_days}) se pasa de 'ahora', se recorta a lo disponible")

    print(f"T0 (clasificacion): {t0.date()}   ventana hacia adelante hasta: {min(forward_end, now).date()}")

    universe = fetch_screenable_universe()
    top = universe[: args.universe_size]
    print(f"universo: top {len(top)} por liquidez de {len(universe)} totales")

    daily_needed_before = max(FLAT_WINDOW, TREND_WINDOW, CORR_WINDOW_DAYS) + 5
    tp_flat = usd_per_level * FLAT_NUM_LEVELS * Decimal(args.take_profit_pct) / 100
    tp_trend = usd_per_level * TREND_NUM_LEVELS * Decimal(args.take_profit_pct) / 100
    stop_loss_enabled = bool(args.stop_loss_pct) and Decimal(args.stop_loss_pct or 0) > 0
    sl_flat = usd_per_level * FLAT_NUM_LEVELS * Decimal(args.stop_loss_pct) / 100 if stop_loss_enabled else None
    sl_trend = usd_per_level * TREND_NUM_LEVELS * Decimal(args.stop_loss_pct) / 100 if stop_loss_enabled else None
    use_btc_gate = not args.no_btc_gate
    sl_label = f"{args.stop_loss_pct}%" if stop_loss_enabled else "desactivado"
    pos_min, pos_max = (float(x) for x in args.flat_pos_range.split(","))
    max_corr = float(args.flat_max_corr)
    print(
        f"take-profit: {args.take_profit_pct}%  stop-loss: {sl_label}  gate de BTC: {'activo' if use_btc_gate else 'desactivado'}  "
        f"flat-min-score: {args.flat_min_score}  flat-pos-range: {pos_min}-{pos_max}%  flat-max-corr: {max_corr}  "
        f"trend-top-n: {args.trend_top_n}\n"
    )

    start_ms_global = int((t0 - timedelta(days=daily_needed_before)).timestamp() * 1000)
    end_ms_global = int(min(forward_end, now).timestamp() * 1000)
    t0_ms = int(t0.timestamp() * 1000)

    btc_daily = fetch_klines_range("BTCUSDT", "1d", start_ms_global, end_ms_global)
    btc_bounds_at_t0 = [k for k in btc_daily if k[6] < t0_ms]
    btc_ok_at_t0, btc_reason = btc_market_ok(btc_bounds_at_t0) if use_btc_gate else (True, "gate desactivado")
    print(f"BTC en T0: {btc_reason}")
    btc_returns = daily_log_returns(btc_bounds_at_t0)

    # --- Pass 1: classify + score every symbol, no hourly fetch yet ---
    flat_candidates = []  # (symbol, bounds_source, score, verdict)
    trend_candidates = []  # (symbol, daily, er)
    other_results = []  # (symbol, label, None) for the final report

    for t in top:
        symbol = t.symbol
        start_ms = int((t0 - timedelta(days=daily_needed_before)).timestamp() * 1000)
        daily = fetch_klines_range(symbol, "1d", start_ms, end_ms_global)
        time.sleep(0.1)
        if not daily:
            other_results.append((symbol, "sin_datos", None))
            continue

        bounds_source = [k for k in daily if k[6] < t0_ms]
        if len(bounds_source) < daily_needed_before - 3:
            other_results.append((symbol, "historial_insuficiente", None))
            continue

        er = efficiency_ratio(bounds_source)
        regimen = regimen_from_er(er)
        move = net_move_pct(bounds_source)

        if regimen == "rango":
            vol_pct = daily_volatility_pct(bounds_source)
            pos_pct = position_in_range_pct(bounds_source)
            liquidez = liquidez_bucket(t.quote_volume_24h)
            returns = daily_log_returns(bounds_source)
            corr_value = pearson(returns, btc_returns)
            correlacion = correlacion_bucket(corr_value)
            candidate = Candidate(
                symbol=symbol, vol_pct=vol_pct, regimen=regimen, pos_pct=pos_pct,
                liquidez=liquidez, correlacion=correlacion,
            )
            ev = evaluar(candidate)
            features = {
                "vol_pct": vol_pct, "pos_pct": pos_pct, "liquidez": liquidez,
                "quote_volume_24h": t.quote_volume_24h, "corr_value": corr_value, "er": er,
            }
            in_pos_range = pos_min <= pos_pct <= pos_max
            in_corr_range = abs(corr_value) < max_corr
            if ev.score >= args.flat_min_score and in_pos_range and in_corr_range:
                flat_candidates.append((symbol, bounds_source, ev.score, ev.verdict, features))
            else:
                reasons = []
                if ev.score < args.flat_min_score:
                    reasons.append(f"score={ev.score}")
                if not in_pos_range:
                    reasons.append(f"pos={pos_pct:.0f}%")
                if not in_corr_range:
                    reasons.append(f"corr={corr_value:.2f}")
                other_results.append((symbol, f"flat_descartado ({', '.join(reasons)})", None))

        elif regimen == "fuerte" and move >= 0:
            vol_pct = daily_volatility_pct(bounds_source)
            pos_pct = position_in_range_pct(bounds_source)
            liquidez = liquidez_bucket(t.quote_volume_24h)
            returns = daily_log_returns(bounds_source)
            corr_value = pearson(returns, btc_returns)
            trend_features = {
                "vol_pct": vol_pct, "pos_pct": pos_pct, "liquidez": liquidez,
                "quote_volume_24h": t.quote_volume_24h, "corr_value": corr_value,
                "er": er, "move_pct": move,
            }
            trend_candidates.append((symbol, daily, er, trend_features))

        elif regimen == "fuerte" and move < 0:
            other_results.append((symbol, "trend_short_no_soportado", None))
        else:
            other_results.append((symbol, "no_califica (leve)", None))

    flat_candidates.sort(key=lambda c: c[2], reverse=True)
    trend_candidates.sort(key=lambda c: c[2], reverse=True)
    trend_candidates = trend_candidates[: args.trend_top_n]

    print(f"\ncandidatos flat con score>={args.flat_min_score}: {len(flat_candidates)}")
    print(f"candidatos trend (top {args.trend_top_n} por ER): {len(trend_candidates)}\n")

    # --- Pass 2: only the survivors get the expensive hourly backtest ---
    results = list(other_results)
    csv_rows = []
    csv_rows_trend = []

    for symbol, bounds_source, score, verdict, features in flat_candidates:
        if not btc_ok_at_t0:
            results.append((symbol, f"bloqueado_btc_bajista (score={score})", None))
            continue
        hourly = fetch_klines_range(symbol, "1h", t0_ms, end_ms_global)
        time.sleep(0.1)
        if not hourly:
            results.append((symbol, "flat/sin_horarias", None))
            continue
        res = run_flat_backtest(bounds_source, hourly, usd_per_level, FEE_RATE, tp_flat, sl_flat)
        results.append((symbol, f"flat (score={score})", res))
        if args.csv_out and res is not None:
            pnl = float(res.realized_profit + res.unrealized_profit)
            csv_rows.append({
                "t0": t0.date().isoformat(), "symbol": symbol, "score": score, "verdict": verdict,
                "vol_pct": features["vol_pct"], "pos_pct": features["pos_pct"],
                "liquidez": features["liquidez"], "quote_volume_24h": features["quote_volume_24h"],
                "corr_value": features["corr_value"], "er": features["er"],
                "pnl": pnl, "roi_pct": pnl / float(usd_per_level * FLAT_NUM_LEVELS) * 100,
                "exit_reason": res.exit_reason,
            })

    for symbol, daily, er, trend_features in trend_candidates:
        hourly = fetch_klines_range(symbol, "1h", t0_ms, end_ms_global)
        time.sleep(0.1)
        if not hourly:
            results.append((symbol, "trend/sin_horarias", None))
            continue
        res = run_trend_backtest(
            daily, hourly, t0, args.forward_days, usd_per_level, FEE_RATE, tp_trend, sl_trend,
            btc_daily_klines_all=btc_daily if use_btc_gate else None,
        )
        results.append((symbol, f"trend_long (ER={er:.2f})", res))
        if args.trend_csv_out and res is not None:
            cap_trend_ref = float(usd_per_level * TREND_NUM_LEVELS)
            csv_rows_trend.append({
                "t0": t0.date().isoformat(), "symbol": symbol,
                "vol_pct": trend_features["vol_pct"], "pos_pct": trend_features["pos_pct"],
                "liquidez": trend_features["liquidez"], "quote_volume_24h": trend_features["quote_volume_24h"],
                "corr_value": trend_features["corr_value"], "er": trend_features["er"],
                "move_pct": trend_features["move_pct"],
                "pnl": float(res.total_pnl), "roi_pct": float(res.total_pnl) / cap_trend_ref * 100,
                "days_run": res.days_run, "days_blocked_by_btc": res.days_blocked_by_btc,
                "exit_reason": res.exit_reason,
            })

    print(f"{'Simbolo':<12}{'Estrategia':<28}{'Resultado':<50}")
    for symbol, strategy, res in results:
        if res is None:
            print(f"{symbol:<12}{strategy:<28}{'-':<50}")
        elif strategy.startswith("flat"):
            print(
                f"{symbol:<12}{strategy:<28}"
                f"pnl=${res.realized_profit + res.unrealized_profit:>8.2f}  "
                f"trades={res.trades:<5} maxDD=${res.max_drawdown:>7.2f}  "
                f"salida={res.exit_reason} (bar {res.bars_run})"
            )
        else:
            print(
                f"{symbol:<12}{strategy:<28}"
                f"pnl=${res.total_pnl:>8.2f}  trades={res.trades:<5} "
                f"maxDD(dia)=${res.worst_day_dd:>7.2f}  dias={res.days_run:<3} "
                f"dias_bloqueados_btc={res.days_blocked_by_btc:<3} salida={res.exit_reason}"
            )

    califican = [r for r in results if r[2] is not None]
    print(f"\n{len(califican)}/{len(results)} calificaron y se backtestearon")

    if args.csv_out and csv_rows:
        path = Path(args.csv_out)
        is_new = not path.exists()
        fields = ["t0", "symbol", "score", "verdict", "vol_pct", "pos_pct", "liquidez", "quote_volume_24h", "corr_value", "er", "pnl", "roi_pct", "exit_reason"]
        with path.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            if is_new:
                writer.writeheader()
            writer.writerows(csv_rows)
        print(f"agregadas {len(csv_rows)} filas a {path}")

    if args.trend_csv_out and csv_rows_trend:
        path = Path(args.trend_csv_out)
        is_new = not path.exists()
        fields = [
            "t0", "symbol", "vol_pct", "pos_pct", "liquidez", "quote_volume_24h",
            "corr_value", "er", "move_pct", "pnl", "roi_pct", "days_run",
            "days_blocked_by_btc", "exit_reason",
        ]
        with path.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            if is_new:
                writer.writeheader()
            writer.writerows(csv_rows_trend)
        print(f"agregadas {len(csv_rows_trend)} filas a {path}")


if __name__ == "__main__":
    main()
