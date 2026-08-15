"""A single-sided position for one window - same shape as
atlantis/btc5m_momentum/position.py's Position (kept as its own copy,
not a shared import, so this package stays independent of momentum's -
they are peer strategies, not layered on each other)."""

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
        if not self.is_open or outcome is None:
            return Decimal(0)
        if outcome == self.side:
            return self.quantity - self.cost
        return -self.cost
