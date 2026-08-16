"""Shared grid-fill engine used by both the flat-market and trend grid
strategies (atlantis/grid_trader/flat.py, atlantis/grid_trader/trend.py),
by their backtests, AND by the live paper-trading bot
(scripts/run_grid_trader_paper.py). `process_bar` is the one place fill
logic lives - `simulate_grid` (batch, for backtests) and the live poll
loop both call it, so backtest and live can never drift apart the way
momentum's backtest briefly did earlier this session (1-min vs 1-sec
granularity computing different momentum_pct for the same window).

Simplification, stated plainly: real grids fill tick-by-tick; this only
sees (low, high) pairs per step (an OHLC bar in backtests, or the
[min(last_price, current_price), max(...)] span between two live polls).
Per step, SELLS on already-open positions are resolved first, THEN new
BUYS are opened - at most one round trip per level per step. A step
that (in reality) swept down through a buy level and back up through
the sell level above it in one continuous move will show up as a buy
this step and a sell the next, not both at once - this UNDERSTATES
trade frequency/profit slightly on fast moves, conservative in the same
direction as every other backtest caveat already documented in this
repo (prices-history snapshots, momentum granularity, etc.), never the
direction that would make a strategy look better than it is.
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
class GridState:
    """Mutable position state for one grid - the SAME shape whether it
    was just built (fresh, all zero) or reconstructed from a live
    position's persisted state on bot restart."""
    levels: list[Decimal]
    open_qty: list[Decimal] = field(default_factory=list)
    realized: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    trades: int = 0

    def __post_init__(self) -> None:
        if not self.open_qty:
            self.open_qty = [Decimal(0)] * len(self.levels)

    def unrealized(self, mark_price: Decimal) -> Decimal:
        return sum(qty * (mark_price - self.levels[i]) for i, qty in enumerate(self.open_qty))

    def total(self, mark_price: Decimal) -> Decimal:
        return self.realized + self.unrealized(mark_price)

    @property
    def open_positions(self) -> int:
        return sum(1 for q in self.open_qty if q > 0)


def process_bar(state: GridState, low: Decimal, high: Decimal, usd_per_level: Decimal, fee_rate: Decimal) -> None:
    """Mutates `state` in place: sells on already-open positions first,
    then opens new buys - see module docstring for why this order and
    what it approximates."""
    n = len(state.levels)

    for i in range(n - 1):
        if state.open_qty[i] > 0:
            sell_level = state.levels[i + 1]
            if low <= sell_level <= high:
                qty = state.open_qty[i]
                proceeds = qty * sell_level
                cost = qty * state.levels[i]
                fee = proceeds * fee_rate
                state.realized += proceeds - cost - fee
                state.fees += fee
                state.open_qty[i] = Decimal(0)
                state.trades += 1

    for i in range(n - 1):
        if state.open_qty[i] == 0:
            level = state.levels[i]
            if low <= level <= high:
                qty = usd_per_level / level
                fee = usd_per_level * fee_rate
                state.open_qty[i] = qty
                state.fees += fee
                state.trades += 1


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
    state = GridState(levels=list(levels))
    equity_curve: list[Decimal] = []
    last_close = levels[len(levels) // 2] if levels else Decimal(0)
    exit_reason = "period_end"
    bars_run = 0

    for low, high in bars:
        bars_run += 1
        process_bar(state, low, high, usd_per_level, fee_rate)

        last_close = (low + high) / 2
        total_now = state.total(last_close)
        equity_curve.append(total_now)

        if take_profit_usd is not None and total_now >= take_profit_usd:
            exit_reason = "take_profit"
            break
        if stop_loss_usd is not None and total_now <= -stop_loss_usd:
            exit_reason = "stop_loss"
            break

    peak = Decimal("-Infinity")
    max_dd = Decimal(0)
    for eq in equity_curve:
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)

    return GridSimResult(
        bars_run=bars_run,
        exit_reason=exit_reason,
        realized_profit=state.realized,
        unrealized_profit=state.unrealized(last_close),
        total_fees=state.fees,
        trades=state.trades,
        open_positions=state.open_positions,
        max_drawdown=max_dd,
    )
