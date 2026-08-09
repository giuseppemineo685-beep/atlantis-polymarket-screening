"""A single-sided directional position for one window - deliberately much
simpler than atlantis/btc5m_hedge/portfolio.py's Portfolio, which tracks
BOTH sides because hedging is the whole point there. Here there is only
ever one side (or none, if the signal didn't fire), so there is nothing
to "hedge" - the position either resolves to a full win or a full loss.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Position:
    side: str | None = None
    quantity: Decimal = Decimal(0)
    cost: Decimal = Decimal(0)

    @property
    def is_open(self) -> bool:
        return self.side is not None

    def realized_profit(self, outcome: str | None) -> Decimal:
        """0 if never opened. If opened but outcome is unknown yet
        (None), also 0 - callers must not treat "not yet resolved" as
        "broke even", this is only meant for the FINAL, resolved case."""
        if not self.is_open or outcome is None:
            return Decimal(0)
        if outcome == self.side:
            return self.quantity - self.cost
        return -self.cost
