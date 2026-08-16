"""Trend grid: asymmetric range (more room below current price than
above, for a long-biased uptrend grid), re-anchored daily using the
trailing N-day high/low - the range chases the trend instead of
sitting still.

v1 scope, stated plainly: LONG only. A short-biased mirror (more room
above price, sell-first/buy-back-lower fills) needs a second, mirrored
fill engine in grid_math.py - scoped out to ship a validated long-only
version first rather than double the surface area before any real
backtest exists.

Daily re-anchoring mechanic, stated plainly: this v1 does NOT migrate
open positions between one day's grid and the next day's rebuilt one
(that needs real position-tracking bookkeeping across rebuilds). Instead
each day is simulated independently: any position still open at that
day's last bar is marked to market at that bar's close (counted as
realized for the day), then the next day starts flat with a freshly
rebuilt, re-anchored grid. This is a real simplification of what a
continuously-running trend grid would do, not a hidden one - see
scripts/backtest_grid_trend.py for exactly where this happens.
"""

from __future__ import annotations

from decimal import Decimal

RANGE_WINDOW_DAYS = 14
NUM_LEVELS = 20
BUY_SIDE_FRACTION = Decimal("0.7")  # 70% of the range sits below current price


def compute_trend_grid_bounds(daily_klines: list, direction: str) -> tuple[Decimal, Decimal]:
    if direction != "long":
        raise ValueError("solo se soporta direccion 'long' en esta version de trend.py")

    window = daily_klines[-RANGE_WINDOW_DAYS:]
    hi = max(Decimal(str(k[2])) for k in window)
    lo = min(Decimal(str(k[3])) for k in window)
    price = Decimal(str(window[-1][4]))
    width = hi - lo
    lower = price - width * BUY_SIDE_FRACTION
    upper = price + width * (1 - BUY_SIDE_FRACTION)
    return lower, upper
