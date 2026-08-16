"""Shared grid-fill simulation engine used by both the flat-market and
trend grid strategies (atlantis/grid_trader/flat.py,
atlantis/grid_trader/trend.py) and by their backtests. Pure math, no
network calls - takes a price path, returns fills/PnL.

Simplification, stated plainly: real grids fill tick-by-tick; this only
sees OHLC bars. Per bar, SELLS on already-open positions are resolved
first, THEN new BUYS are opened - at most one round trip per level per
bar. A bar that (in reality) swept down through a buy level and back up
through the sell level above it in one continuous move will show up as
a buy this bar and a sell next bar, not both in the same bar - this
UNDERSTATES trade frequency/profit slightly on fast-moving bars,
conservative in the same direction as every other backtest caveat
already documented in this repo (prices-history snapshots, momentum
granularity, etc.) - never the direction that would make a strategy
look better than it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


def build_levels(lower: Decimal, upper: Decimal, num_levels: int) -> list[Decimal]:
    if num_levels < 2:
        raise ValueError("num_levels must be >= 2")
    step = (upper - lower) / (num_levels - 1)
    return [lower + step * i for i in range(num_levels)]


@dataclass
class GridSimResult:
    realized_profit: Decimal
    unrealized_profit: Decimal
    total_fees: Decimal
    trades: int
    open_positions: int
    max_drawdown: Decimal  # worst realized+unrealized dip from its own running peak
    bars_run: int = 0
    exit_reason: str = "period_end"  # "period_end" | "take_profit" | "stop_loss"


def simulate_grid(
    bars: list[tuple[Decimal, Decimal]],
    levels: list[Decimal],
    usd_per_level: Decimal,
    fee_rate: Decimal,
    take_profit_usd: Decimal | None = None,
    stop_loss_usd: Decimal | None = None,
) -> GridSimResult:
    """`bars`: chronological (low, high) per bar. `levels`: ascending
    grid price levels. Classic long-only grid: buy resting at level i,
    sell into level i+1 - the level ABOVE the top level is never a buy
    target (nothing to sell into), so the top level only ever acts as a
    sell target for the level below it.

    `take_profit_usd`: if set, stops the FIRST bar total (realized +
    marked-to-market unrealized) equals or exceeds this - the bot
    doesn't sit on a position for the rest of the window once its
    target is hit, matching the "no lo mantengamos 30 dias" requirement.

    `stop_loss_usd`: if set (a POSITIVE magnitude), stops the first bar
    total drops to or below -stop_loss_usd. Real tradeoff, stated
    plainly: a tight stop-loss cuts the AIOUSDT/ACEUSDT-style breakdowns
    short, but ALSO cuts positions that were merely mid-drawdown and
    would have recovered (VELVETUSDT hit -18.2% before closing at
    +10.1% in the 2026-07 backtest) - there's no threshold that avoids
    both failure modes, see docs/GRID_TRADER_STRATEGIES.md for the
    real-data comparison across thresholds before picking one."""
    n = len(levels)
    open_qty: list[Decimal] = [Decimal(0)] * n
    realized = Decimal(0)
    fees = Decimal(0)
    trades = 0
    equity_curve: list[Decimal] = []
    last_close = levels[len(levels) // 2] if levels else Decimal(0)
    exit_reason = "period_end"
    bars_run = 0

    for low, high in bars:
        bars_run += 1
        # 1) sells on already-open positions from prior bars
        for i in range(n - 1):
            if open_qty[i] > 0:
                sell_level = levels[i + 1]
                if low <= sell_level <= high:
                    qty = open_qty[i]
                    proceeds = qty * sell_level
                    cost = qty * levels[i]
                    fee = proceeds * fee_rate
                    realized += proceeds - cost - fee
                    fees += fee
                    open_qty[i] = Decimal(0)
                    trades += 1

        # 2) new buys into levels not currently holding a position
        for i in range(n - 1):
            if open_qty[i] == 0:
                level = levels[i]
                if low <= level <= high:
                    qty = usd_per_level / level
                    fee = usd_per_level * fee_rate
                    open_qty[i] = qty
                    fees += fee
                    trades += 1

        last_close = (low + high) / 2
        unrealized_now = sum(qty * (last_close - levels[i]) for i, qty in enumerate(open_qty))
        total_now = realized + unrealized_now
        equity_curve.append(total_now)

        if take_profit_usd is not None and total_now >= take_profit_usd:
            exit_reason = "take_profit"
            break
        if stop_loss_usd is not None and total_now <= -stop_loss_usd:
            exit_reason = "stop_loss"
            break

    unrealized = sum(qty * (last_close - levels[i]) for i, qty in enumerate(open_qty))

    peak = Decimal("-Infinity")
    max_dd = Decimal(0)
    for eq in equity_curve:
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)

    return GridSimResult(
        bars_run=bars_run,
        exit_reason=exit_reason,
        realized_profit=realized,
        unrealized_profit=unrealized,
        total_fees=fees,
        trades=trades,
        open_positions=sum(1 for q in open_qty if q > 0),
        max_drawdown=max_dd,
    )
