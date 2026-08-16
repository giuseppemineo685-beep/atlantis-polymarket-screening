"""Backtest either grid strategy against real Binance hourly price
data. No look-ahead: bounds for any given day/period only ever use
klines strictly before that period.

Usage:
  python3 scripts/backtest_grid.py flat HYPEUSDT --days 30 --usd-per-level 20
  python3 scripts/backtest_grid.py trend HUSDT --days 30 --usd-per-level 20
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

from atlantis.grid_screener.binance_client import fetch_daily_klines, fetch_klines_range  # noqa: E402
from atlantis.grid_trader.flat import NUM_LEVELS as FLAT_NUM_LEVELS  # noqa: E402
from atlantis.grid_trader.flat import RANGE_WINDOW_DAYS as FLAT_WINDOW  # noqa: E402
from atlantis.grid_trader.flat import compute_flat_grid_bounds  # noqa: E402
from atlantis.grid_trader.grid_math import build_levels, simulate_grid  # noqa: E402
from atlantis.grid_trader.trend import NUM_LEVELS as TREND_NUM_LEVELS  # noqa: E402
from atlantis.grid_trader.trend import RANGE_WINDOW_DAYS as TREND_WINDOW  # noqa: E402
from atlantis.grid_trader.trend import compute_trend_grid_bounds  # noqa: E402

FEE_RATE = Decimal("0.0004")  # 0.04% taker, typical Binance USDS-M futures rate


def _hourly_bars(klines: list) -> list[tuple[Decimal, Decimal]]:
    return [(Decimal(k[3]), Decimal(k[2])) for k in klines]  # (low, high)


def backtest_flat(symbol: str, days: int, usd_per_level: Decimal) -> None:
    now = datetime.now(timezone.utc)
    backtest_start = now - timedelta(days=days)

    daily_klines = fetch_daily_klines(symbol, days=days + FLAT_WINDOW + 5)
    if not daily_klines:
        print(f"{symbol}: no se pudo obtener klines diarias")
        return
    daily_by_close_time = [(k[6], k) for k in daily_klines]
    bounds_source = [k for close_t, k in daily_by_close_time if close_t < int(backtest_start.timestamp() * 1000)]
    if len(bounds_source) < FLAT_WINDOW:
        print(f"{symbol}: no hay suficiente historial previo al inicio del backtest para fijar el rango")
        return

    lower, upper = compute_flat_grid_bounds(bounds_source)
    levels = build_levels(lower, upper, FLAT_NUM_LEVELS)
    print(f"{symbol} FLAT: rango [{lower:.6f}, {upper:.6f}], {FLAT_NUM_LEVELS} niveles")

    hourly = fetch_klines_range(symbol, "1h", int(backtest_start.timestamp() * 1000), int(now.timestamp() * 1000))
    if not hourly:
        print(f"{symbol}: no se pudo obtener velas horarias")
        return
    bars = _hourly_bars(hourly)
    print(f"{symbol}: {len(bars)} velas horarias ({days} dias)")

    result = simulate_grid(bars, levels, usd_per_level, FEE_RATE)
    _print_result(symbol, "FLAT", days, usd_per_level, result)


def backtest_trend(symbol: str, days: int, usd_per_level: Decimal) -> None:
    now = datetime.now(timezone.utc)
    backtest_start = now - timedelta(days=days)

    daily_klines = fetch_daily_klines(symbol, days=days + TREND_WINDOW + 5)
    if not daily_klines:
        print(f"{symbol}: no se pudo obtener klines diarias")
        return

    hourly = fetch_klines_range(symbol, "1h", int(backtest_start.timestamp() * 1000), int(now.timestamp() * 1000))
    if not hourly:
        print(f"{symbol}: no se pudo obtener velas horarias")
        return

    total_realized = Decimal(0)
    total_fees = Decimal(0)
    total_trades = 0
    worst_day_dd = Decimal(0)
    day_reports = []

    day_start = backtest_start.replace(hour=0, minute=0, second=0, microsecond=0)
    if day_start < backtest_start:
        day_start += timedelta(days=1)

    while day_start < now:
        day_end = day_start + timedelta(days=1)
        bounds_source = [k for k in daily_klines if k[6] < int(day_start.timestamp() * 1000)]
        if len(bounds_source) < TREND_WINDOW:
            day_start = day_end
            continue

        lower, upper = compute_trend_grid_bounds(bounds_source, "long")
        levels = build_levels(lower, upper, TREND_NUM_LEVELS)

        day_bars_raw = [k for k in hourly if day_start.timestamp() * 1000 <= k[0] < day_end.timestamp() * 1000]
        if not day_bars_raw:
            day_start = day_end
            continue
        day_bars = _hourly_bars(day_bars_raw)

        result = simulate_grid(day_bars, levels, usd_per_level, FEE_RATE)
        day_total = result.realized_profit + result.unrealized_profit  # unrealized = marked to market at day close
        total_realized += day_total
        total_fees += result.total_fees
        total_trades += result.trades
        worst_day_dd = min(worst_day_dd, result.max_drawdown)
        day_reports.append((day_start.date(), day_total, result.trades))

        day_start = day_end

    print(f"{symbol} TREND: {len(day_reports)} dias simulados, re-anclado diario, direccion=long")
    print(f"  pnl total (marcado a mercado cada dia): ${total_realized:.2f}")
    print(f"  comisiones totales: ${total_fees:.2f}")
    print(f"  trades totales: {total_trades}")
    print(f"  peor drawdown en un solo dia: ${worst_day_dd:.2f}")
    wins = sum(1 for _, pnl, _ in day_reports if pnl > 0)
    print(f"  dias positivos: {wins}/{len(day_reports)}")


def _print_result(symbol: str, strategy: str, days: int, usd_per_level: Decimal, result) -> None:
    total = result.realized_profit + result.unrealized_profit
    print(f"  realizado: ${result.realized_profit:.2f}")
    print(f"  no realizado (posiciones abiertas al final): ${result.unrealized_profit:.2f}")
    print(f"  total: ${total:.2f}")
    print(f"  comisiones: ${result.total_fees:.2f}")
    print(f"  trades: {result.trades}  posiciones abiertas al final: {result.open_positions}")
    print(f"  max drawdown: ${result.max_drawdown:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("strategy", choices=["flat", "trend"])
    parser.add_argument("symbol")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--usd-per-level", type=str, default="20")
    args = parser.parse_args()

    usd_per_level = Decimal(args.usd_per_level)
    if args.strategy == "flat":
        backtest_flat(args.symbol.upper(), args.days, usd_per_level)
    else:
        backtest_trend(args.symbol.upper(), args.days, usd_per_level)


if __name__ == "__main__":
    main()
