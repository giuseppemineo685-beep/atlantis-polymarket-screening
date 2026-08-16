"""Forex walk-forward backtest - same no-look-ahead methodology as
scripts/backtest_grid_walkforward.py (classify regime AT a past T0
using only data available then, simulate forward with take-profit/
stop-loss), reusing atlantis/grid_trader/backtest_engine.py UNCHANGED.

v1 scope, stated plainly: no position/correlation filter (that finding
was crypto-specific, not yet validated for forex - see
atlantis/forex/screener.py's own docstring), no market-wide gate (no
validated "USD strength" analog to the crypto BTC gate yet). This run
exists to get real forex numbers on the table before deciding whether
either refinement is worth building.

Usage:
  python3 scripts/backtest_forex_walkforward.py --lookback-days 30 --forward-days 30
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlantis.forex.oanda_client import candles_to_klines, fetch_candles, fetch_currency_instruments  # noqa: E402
from atlantis.grid_screener.metrics import efficiency_ratio, net_move_pct, position_in_range_pct, regimen_from_er  # noqa: E402
from atlantis.grid_trader.backtest_engine import run_flat_backtest, run_trend_backtest  # noqa: E402
from atlantis.grid_trader.flat import RANGE_WINDOW_DAYS as FLAT_WINDOW  # noqa: E402
from atlantis.grid_trader.flat import NUM_LEVELS as FLAT_NUM_LEVELS  # noqa: E402
from atlantis.grid_trader.trend import RANGE_WINDOW_DAYS as TREND_WINDOW  # noqa: E402
from atlantis.grid_trader.trend import NUM_LEVELS as TREND_NUM_LEVELS  # noqa: E402

FEE_RATE = Decimal("0.0002")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--forward-days", type=int, default=30)
    parser.add_argument("--usd-per-level", type=str, default="20")
    parser.add_argument("--take-profit-pct", type=str, default="10")
    parser.add_argument("--stop-loss-pct", type=str, default="35")
    parser.add_argument("--flat-pos-range", type=str, default="20,80")
    args = parser.parse_args()

    usd_per_level = Decimal(args.usd_per_level)
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(days=args.lookback_days)
    forward_end = t0 + timedelta(days=args.forward_days)
    pos_min, pos_max = (float(x) for x in args.flat_pos_range.split(","))

    tp_flat = usd_per_level * FLAT_NUM_LEVELS * Decimal(args.take_profit_pct) / 100
    tp_trend = usd_per_level * TREND_NUM_LEVELS * Decimal(args.take_profit_pct) / 100
    sl_flat = usd_per_level * FLAT_NUM_LEVELS * Decimal(args.stop_loss_pct) / 100
    sl_trend = usd_per_level * TREND_NUM_LEVELS * Decimal(args.stop_loss_pct) / 100

    print(f"T0 (clasificacion): {t0.date()}   ventana hacia adelante hasta: {min(forward_end, now).date()}")

    instruments = fetch_currency_instruments()
    print(f"universo: {len(instruments)} pares de moneda\n")

    daily_needed_before = max(FLAT_WINDOW, TREND_WINDOW) + 5
    # Forex trades ~5 of every 7 days (no candles on weekends), unlike
    # crypto's 24/7 markets - a calendar-day buffer sized 1:1 for
    # trading days silently comes up short (confirmed 2026-08-16: a
    # 19-calendar-day buffer yielded only 15 real candles, one short of
    # the 16 needed). Padding by 7/5 plus a few extra days covers it.
    calendar_days_buffer = daily_needed_before * 7 // 5 + 5
    results = []

    for inst in instruments:
        name = inst.name
        start_ts = int((t0 - timedelta(days=calendar_days_buffer)).timestamp())
        end_ts = int(min(forward_end, now).timestamp())
        daily_candles = fetch_candles(name, granularity="D", from_ts=start_ts, to_ts=end_ts)
        if not daily_candles:
            results.append((name, "sin_datos", None))
            continue
        daily = candles_to_klines(daily_candles)

        t0_ms = int(t0.timestamp() * 1000)
        bounds_source = [k for k in daily if k[6] < t0_ms]
        if len(bounds_source) < daily_needed_before - 3:
            results.append((name, "historial_insuficiente", None))
            continue

        er = efficiency_ratio(bounds_source)
        regimen = regimen_from_er(er)
        move = net_move_pct(bounds_source)
        pos_pct = position_in_range_pct(bounds_source)

        if regimen == "rango":
            if not (pos_min <= pos_pct <= pos_max):
                results.append((name, f"flat_descartado (pos={pos_pct:.0f}%)", None))
                continue
            hourly_candles = fetch_candles(name, granularity="H1", from_ts=int(t0.timestamp()), to_ts=end_ts)
            if not hourly_candles:
                results.append((name, "flat/sin_horarias", None))
                continue
            hourly = candles_to_klines(hourly_candles)
            res = run_flat_backtest(bounds_source, hourly, usd_per_level, FEE_RATE, tp_flat, sl_flat)
            results.append((name, "flat", res))

        elif regimen == "fuerte" and move >= 0:
            hourly_candles = fetch_candles(name, granularity="H1", from_ts=int(t0.timestamp()), to_ts=end_ts)
            if not hourly_candles:
                results.append((name, "trend/sin_horarias", None))
                continue
            hourly = candles_to_klines(hourly_candles)
            res = run_trend_backtest(daily, hourly, t0, args.forward_days, usd_per_level, FEE_RATE, tp_trend, sl_trend)
            results.append((name, "trend_long", res))

        elif regimen == "fuerte" and move < 0:
            results.append((name, "trend_short_no_soportado", None))
        else:
            results.append((name, "no_califica (leve)", None))

    print(f"{'Instrumento':<12}{'Estrategia':<28}{'Resultado':<50}")
    for name, strategy, res in results:
        if res is None:
            print(f"{name:<12}{strategy:<28}{'-':<50}")
        elif strategy == "flat":
            print(
                f"{name:<12}{strategy:<28}"
                f"pnl=${res.realized_profit + res.unrealized_profit:>8.2f}  "
                f"trades={res.trades:<5} maxDD=${res.max_drawdown:>7.2f}  "
                f"salida={res.exit_reason} (bar {res.bars_run})"
            )
        else:
            print(
                f"{name:<12}{strategy:<28}"
                f"pnl=${res.total_pnl:>8.2f}  trades={res.trades:<5} "
                f"maxDD(dia)=${res.worst_day_dd:>7.2f}  dias={res.days_run:<3} salida={res.exit_reason}"
            )

    califican = [r for r in results if r[2] is not None]
    print(f"\n{len(califican)}/{len(results)} calificaron y se backtestearon")


if __name__ == "__main__":
    main()
