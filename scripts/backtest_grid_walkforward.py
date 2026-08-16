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

Usage:
  python3 scripts/backtest_grid_walkforward.py --lookback-days 30 --forward-days 30 --universe-size 20
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlantis.grid_screener.binance_client import fetch_klines_range, fetch_screenable_universe  # noqa: E402
from atlantis.grid_screener.metrics import efficiency_ratio, net_move_pct, regimen_from_er  # noqa: E402
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

    daily_needed_before = max(FLAT_WINDOW, TREND_WINDOW) + 5
    tp_flat = usd_per_level * FLAT_NUM_LEVELS * Decimal(args.take_profit_pct) / 100
    tp_trend = usd_per_level * TREND_NUM_LEVELS * Decimal(args.take_profit_pct) / 100
    stop_loss_enabled = bool(args.stop_loss_pct) and Decimal(args.stop_loss_pct or 0) > 0
    sl_flat = usd_per_level * FLAT_NUM_LEVELS * Decimal(args.stop_loss_pct) / 100 if stop_loss_enabled else None
    sl_trend = usd_per_level * TREND_NUM_LEVELS * Decimal(args.stop_loss_pct) / 100 if stop_loss_enabled else None
    use_btc_gate = not args.no_btc_gate
    sl_label = f"{args.stop_loss_pct}%" if stop_loss_enabled else "desactivado"
    print(f"take-profit: {args.take_profit_pct}%  stop-loss: {sl_label}  gate de BTC: {'activo' if use_btc_gate else 'desactivado'}\n")

    start_ms_global = int((t0 - timedelta(days=daily_needed_before)).timestamp() * 1000)
    end_ms_global = int(min(forward_end, now).timestamp() * 1000)
    btc_daily = fetch_klines_range("BTCUSDT", "1d", start_ms_global, end_ms_global)
    t0_ms = int(t0.timestamp() * 1000)
    btc_bounds_at_t0 = [k for k in btc_daily if k[6] < t0_ms]
    btc_ok_at_t0, btc_reason = btc_market_ok(btc_bounds_at_t0) if use_btc_gate else (True, "gate desactivado")
    print(f"BTC en T0: {btc_reason}\n")

    results = []
    for t in top:
        symbol = t.symbol
        start_ms = int((t0 - timedelta(days=daily_needed_before)).timestamp() * 1000)
        end_ms = int(min(forward_end, now).timestamp() * 1000)
        daily = fetch_klines_range(symbol, "1d", start_ms, end_ms)
        time.sleep(0.1)
        if not daily:
            results.append((symbol, "sin_datos", None))
            continue

        bounds_source = [k for k in daily if k[6] < t0_ms]
        if len(bounds_source) < daily_needed_before - 3:
            results.append((symbol, "historial_insuficiente", None))
            continue

        er = efficiency_ratio(bounds_source)
        regimen = regimen_from_er(er)
        move = net_move_pct(bounds_source)

        if regimen == "rango":
            if not btc_ok_at_t0:
                results.append((symbol, "bloqueado_btc_bajista", None))
                continue
            hourly = fetch_klines_range(symbol, "1h", t0_ms, end_ms)
            time.sleep(0.1)
            if not hourly:
                results.append((symbol, "flat/sin_horarias", None))
                continue
            res = run_flat_backtest(bounds_source, hourly, usd_per_level, FEE_RATE, tp_flat, sl_flat)
            results.append((symbol, "flat", res))

        elif regimen == "fuerte" and move >= 0:
            hourly = fetch_klines_range(symbol, "1h", t0_ms, end_ms)
            time.sleep(0.1)
            if not hourly:
                results.append((symbol, "trend/sin_horarias", None))
                continue
            res = run_trend_backtest(
                daily, hourly, t0, args.forward_days, usd_per_level, FEE_RATE, tp_trend, sl_trend,
                btc_daily_klines_all=btc_daily if use_btc_gate else None,
            )
            results.append((symbol, "trend_long", res))

        elif regimen == "fuerte" and move < 0:
            results.append((symbol, "trend_short_no_soportado", None))
        else:
            results.append((symbol, "no_califica (leve)", None))

    print(f"{'Simbolo':<12}{'Estrategia':<22}{'Resultado':<50}")
    for symbol, strategy, res in results:
        if res is None:
            print(f"{symbol:<12}{strategy:<22}{'-':<50}")
        elif strategy == "flat":
            print(
                f"{symbol:<12}{strategy:<22}"
                f"pnl=${res.realized_profit + res.unrealized_profit:>8.2f}  "
                f"trades={res.trades:<5} maxDD=${res.max_drawdown:>7.2f}  "
                f"salida={res.exit_reason} (bar {res.bars_run})"
            )
        else:
            print(
                f"{symbol:<12}{strategy:<22}"
                f"pnl=${res.total_pnl:>8.2f}  trades={res.trades:<5} "
                f"maxDD(dia)=${res.worst_day_dd:>7.2f}  dias={res.days_run:<3} "
                f"dias_bloqueados_btc={res.days_blocked_by_btc:<3} salida={res.exit_reason}"
            )

    califican = [r for r in results if r[2] is not None]
    print(f"\n{len(califican)}/{len(results)} calificaron para alguna estrategia")


if __name__ == "__main__":
    main()
