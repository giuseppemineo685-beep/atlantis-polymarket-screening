"""Shared backtest drivers for both strategies - factored out so the
single-symbol ad-hoc tool (scripts/backtest_grid.py) and the walk-
forward classify-then-simulate tool (scripts/backtest_grid_walkforward.py)
don't duplicate the daily-reanchor loop."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from atlantis.grid_trader.flat import NUM_LEVELS as FLAT_NUM_LEVELS
from atlantis.grid_trader.flat import RANGE_WINDOW_DAYS as FLAT_WINDOW
from atlantis.grid_trader.flat import compute_flat_grid_bounds
from atlantis.grid_trader.grid_math import GridSimResult, build_levels, simulate_grid
from atlantis.grid_trader.market_gate import btc_market_ok
from atlantis.grid_trader.trend import NUM_LEVELS as TREND_NUM_LEVELS
from atlantis.grid_trader.trend import RANGE_WINDOW_DAYS as TREND_WINDOW
from atlantis.grid_trader.trend import compute_trend_grid_bounds


def _hourly_bars(klines: list) -> list[tuple[Decimal, Decimal]]:
    return [(Decimal(k[3]), Decimal(k[2])) for k in klines]


def run_flat_backtest(
    daily_klines_before_start: list, hourly_bars_klines: list,
    usd_per_level: Decimal, fee_rate: Decimal, take_profit_usd: Decimal | None,
    stop_loss_usd: Decimal | None = None,
) -> GridSimResult | None:
    if len(daily_klines_before_start) < FLAT_WINDOW:
        return None
    lower, upper = compute_flat_grid_bounds(daily_klines_before_start)
    levels = build_levels(lower, upper, FLAT_NUM_LEVELS)
    bars = _hourly_bars(hourly_bars_klines)
    return simulate_grid(bars, levels, usd_per_level, fee_rate, take_profit_usd=take_profit_usd, stop_loss_usd=stop_loss_usd)


@dataclass
class TrendWalkforwardResult:
    total_pnl: Decimal
    total_fees: Decimal
    trades: int
    days_run: int
    worst_day_dd: Decimal
    exit_reason: str  # "take_profit" | "stop_loss" | "period_end" | "no_data"
    days_blocked_by_btc: int = 0


def run_trend_backtest(
    daily_klines_all: list, hourly_klines_all: list, start: datetime, forward_days: int,
    usd_per_level: Decimal, fee_rate: Decimal, take_profit_usd: Decimal | None,
    stop_loss_usd: Decimal | None = None, btc_daily_klines_all: list | None = None,
) -> TrendWalkforwardResult:
    """Daily re-anchor loop (see atlantis/grid_trader/trend.py's own
    docstring for the stated simplification: each day is simulated
    independently and marked to market at its own close, not carried
    forward tick-by-tick into the next day's rebuilt grid).

    `btc_daily_klines_all`: if given, each day is skipped entirely
    (contributes $0, no new grid opened) when BTC itself is in a strong
    downtrend that day - see market_gate.btc_market_ok."""
    day_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    if day_start < start:
        day_start += timedelta(days=1)
    end = start + timedelta(days=forward_days)

    total = Decimal(0)
    total_fees = Decimal(0)
    total_trades = 0
    worst_day_dd = Decimal(0)
    days_run = 0
    days_blocked_by_btc = 0
    exit_reason = "period_end"

    while day_start < end:
        day_end = day_start + timedelta(days=1)
        bounds_source = [k for k in daily_klines_all if k[6] < int(day_start.timestamp() * 1000)]
        if len(bounds_source) < TREND_WINDOW:
            day_start = day_end
            continue

        if btc_daily_klines_all is not None:
            btc_bounds = [k for k in btc_daily_klines_all if k[6] < int(day_start.timestamp() * 1000)]
            ok, _reason = btc_market_ok(btc_bounds)
            if not ok:
                days_blocked_by_btc += 1
                day_start = day_end
                continue

        lower, upper = compute_trend_grid_bounds(bounds_source, "long")
        levels = build_levels(lower, upper, TREND_NUM_LEVELS)

        day_bars_raw = [
            k for k in hourly_klines_all
            if day_start.timestamp() * 1000 <= k[0] < day_end.timestamp() * 1000
        ]
        if not day_bars_raw:
            day_start = day_end
            continue

        result = simulate_grid(_hourly_bars(day_bars_raw), levels, usd_per_level, fee_rate)
        day_total = result.realized_profit + result.unrealized_profit
        total += day_total
        total_fees += result.total_fees
        total_trades += result.trades
        worst_day_dd = min(worst_day_dd, result.max_drawdown)
        days_run += 1

        if take_profit_usd is not None and total >= take_profit_usd:
            exit_reason = "take_profit"
            day_start = day_end
            break
        if stop_loss_usd is not None and total <= -stop_loss_usd:
            exit_reason = "stop_loss"
            day_start = day_end
            break

        day_start = day_end

    if days_run == 0:
        exit_reason = "no_data"

    return TrendWalkforwardResult(
        total_pnl=total, total_fees=total_fees, trades=total_trades,
        days_run=days_run, worst_day_dd=worst_day_dd, exit_reason=exit_reason,
        days_blocked_by_btc=days_blocked_by_btc,
    )
