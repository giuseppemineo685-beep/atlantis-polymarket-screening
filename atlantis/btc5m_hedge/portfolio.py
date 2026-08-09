"""Core cross-side-hedge accounting. A Portfolio tracks shares and cost on
BOTH outcome tokens of one binary market; the whole bot's edge lives in
keeping guaranteed_profit = min(profit_if_up, profit_if_down) positive
(and above a safety margin, enforced in risk.py), regardless of which
side eventually resolves.

Deliberately immutable (frozen dataclass) - every "what if" question
(the optimizer's whole job) is answered by calling simulate_buy /
simulate_orderbook_buy and inspecting the RETURNED copy, never by
mutating shared state mid-search. The live/backtest loops hold the one
"real" Portfolio and just reassign their local variable to whatever a
chosen simulation returned.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

UP = "UP"
DOWN = "DOWN"


@dataclass(frozen=True)
class OrderBookLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class FillResult:
    filled_quantity: Decimal
    vwap_price: Decimal | None
    total_cost: Decimal


def fill_from_levels(quantity: Decimal, levels: list[OrderBookLevel]) -> FillResult:
    """Walk order-book levels (assumed already sorted best-price-first)
    and fill up to `quantity`, clipping to whatever depth actually exists
    - a request for more than the book can supply returns a smaller
    filled_quantity, it never fabricates fill beyond real depth."""
    remaining = quantity
    total_cost = Decimal(0)
    filled = Decimal(0)
    for level in levels:
        if remaining <= 0:
            break
        take = min(remaining, level.size)
        if take <= 0:
            continue
        total_cost += take * level.price
        filled += take
        remaining -= take
    vwap = (total_cost / filled) if filled > 0 else None
    return FillResult(filled_quantity=filled, vwap_price=vwap, total_cost=total_cost)


@dataclass(frozen=True)
class Portfolio:
    up_shares: Decimal = Decimal(0)
    down_shares: Decimal = Decimal(0)
    up_cost: Decimal = Decimal(0)
    down_cost: Decimal = Decimal(0)

    @property
    def total_cost(self) -> Decimal:
        return self.up_cost + self.down_cost

    @property
    def avg_up_cost(self) -> Decimal | None:
        return (self.up_cost / self.up_shares) if self.up_shares > 0 else None

    @property
    def avg_down_cost(self) -> Decimal | None:
        return (self.down_cost / self.down_shares) if self.down_shares > 0 else None

    @property
    def directional_imbalance(self) -> Decimal:
        return abs(self.up_shares - self.down_shares)

    def get_profit_if_up(self) -> Decimal:
        return self.up_shares - self.total_cost

    def get_profit_if_down(self) -> Decimal:
        return self.down_shares - self.total_cost

    def get_guaranteed_profit(self) -> Decimal:
        return min(self.get_profit_if_up(), self.get_profit_if_down())

    def get_worst_case_profit(self) -> Decimal:
        """Alias of get_guaranteed_profit() - same number, but this is
        the name the defensive/emergency-hedge vocabulary (2026-08-08
        spec) uses: outside MODE A (profit hedge) the goal is no longer
        "lock in a profit", it's "make the worst outcome less bad", and
        calling the exact same quantity get_guaranteed_profit() in that
        context reads backwards (there's nothing "guaranteed" being
        sought, just a smaller guaranteed loss)."""
        return self.get_guaranteed_profit()

    def get_max_loss(self) -> Decimal:
        """max(0, -worst_case_profit) - 0 whenever the worst case is
        already break-even or better, never negative."""
        return max(Decimal(0), -self.get_worst_case_profit())

    def get_guaranteed_roi(self) -> Decimal | None:
        """None (not 0) when total_cost is 0 - "no capital deployed yet"
        is a different state than "0% return on deployed capital", and
        callers (risk.py's ROI floor check) must not confuse the two."""
        if self.total_cost <= 0:
            return None
        return self.get_guaranteed_profit() / self.total_cost * Decimal(100)

    def is_locked_profit(self) -> bool:
        """True the instant BOTH outcomes are individually profitable -
        the spec's exact 'LOCKED PROFIT' display condition. Deliberately
        does not consider the safety-margin floors from risk.py (those
        gate whether the bot should keep TRADING, not whether the
        current position happens to already be hedged in raw terms)."""
        return self.get_profit_if_up() > 0 and self.get_profit_if_down() > 0

    def simulate_buy(self, side: str, quantity: Decimal, price: Decimal) -> Portfolio:
        """Pure - returns a NEW Portfolio as if `quantity` shares of
        `side` were bought at a single flat `price` (no book-walking)."""
        if quantity <= 0:
            return self
        cost = quantity * price
        if side == UP:
            return replace(self, up_shares=self.up_shares + quantity, up_cost=self.up_cost + cost)
        if side == DOWN:
            return replace(self, down_shares=self.down_shares + quantity, down_cost=self.down_cost + cost)
        raise ValueError(f"unknown side: {side!r}")

    def simulate_orderbook_buy(
        self, side: str, quantity: Decimal, levels: list[OrderBookLevel]
    ) -> tuple[Portfolio, FillResult]:
        """Same as simulate_buy but walks real order-book depth (VWAP)
        instead of assuming a single flat price - the spec's requirement
        that sizing simulation "always use real order-book depth, not
        just best ask". Returns (new_portfolio, fill_result) so callers
        can tell a full fill from a partial one (fill_result.filled_quantity
        vs the requested `quantity`)."""
        fill = fill_from_levels(quantity, levels)
        if fill.filled_quantity <= 0:
            return self, fill
        if side == UP:
            new_pf = replace(self, up_shares=self.up_shares + fill.filled_quantity, up_cost=self.up_cost + fill.total_cost)
        elif side == DOWN:
            new_pf = replace(
                self, down_shares=self.down_shares + fill.filled_quantity, down_cost=self.down_cost + fill.total_cost
            )
        else:
            raise ValueError(f"unknown side: {side!r}")
        return new_pf, fill
