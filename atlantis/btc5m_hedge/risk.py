"""Risk gates. Two distinct kinds of check on purpose:

1. check_candidate() - is this ORDER safe to place at all (exposure
   caps, imbalance cap, order size cap, liquidity/fill-ratio, time-to-
   resolution)? This must NOT depend on the resulting guaranteed_profit
   already clearing its safety margin - the very first order in a window
   always starts from guaranteed_profit deeply negative (you've paid for
   one side and hold nothing on the other yet), so gating on that floor
   would make the bot unable to ever open a position.

2. meets_safety_margin() - has the CURRENT position (not a candidate)
   cleared the profit/ROI floor with enough room for fees, slippage and
   partial fills? This is what the optimizer/paper loop use to decide
   the position is "good enough" (LOCKED PROFIT with margin), not
   whether a given order is allowed to be placed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from atlantis.btc5m_hedge.config import RiskConfig
from atlantis.btc5m_hedge.portfolio import DOWN, UP, FillResult, Portfolio


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str = ""


def check_candidate(
    *,
    side: str,
    requested_quantity: Decimal,
    fill: FillResult,
    portfolio_before: Portfolio,
    portfolio_after: Portfolio,
    seconds_remaining: float,
    risk: RiskConfig,
) -> RiskDecision:
    if seconds_remaining < risk.entry_cutoff_seconds:
        return RiskDecision(False, f"quedan {seconds_remaining:.1f}s, por debajo del corte de entradas ({risk.entry_cutoff_seconds}s)")

    if fill.filled_quantity <= 0:
        return RiskDecision(False, "sin liquidez disponible en el book")

    fill_ratio = fill.filled_quantity / requested_quantity if requested_quantity > 0 else Decimal(0)
    if fill_ratio < risk.min_fill_ratio:
        return RiskDecision(False, f"fill ratio {fill_ratio:.0%} por debajo del minimo ({risk.min_fill_ratio:.0%})")

    if fill.total_cost > risk.max_order_size_usd:
        return RiskDecision(False, f"orden de ${fill.total_cost:.2f} excede max_order_size_usd (${risk.max_order_size_usd})")

    if portfolio_after.total_cost > risk.max_total_exposure_usd:
        return RiskDecision(
            False, f"exposicion total ${portfolio_after.total_cost:.2f} excederia el limite (${risk.max_total_exposure_usd})"
        )

    per_side_cost = portfolio_after.up_cost if side == UP else portfolio_after.down_cost
    if per_side_cost > risk.max_exposure_per_market_usd:
        return RiskDecision(
            False,
            f"exposicion en {side} ${per_side_cost:.2f} excederia el limite por lado (${risk.max_exposure_per_market_usd})",
        )

    if portfolio_after.directional_imbalance > risk.max_directional_imbalance_shares:
        return RiskDecision(
            False,
            f"desbalance {portfolio_after.directional_imbalance:.2f} shares excederia el limite "
            f"({risk.max_directional_imbalance_shares})",
        )

    return RiskDecision(True)


def meets_safety_margin(portfolio: Portfolio, risk: RiskConfig) -> bool:
    """Both outcomes profitable AND with enough margin for fees/slippage/
    partial fills/price moves - the spec's minimum_profit / minimum_ROI
    safety margin, checked against the CURRENT position, not a candidate
    order."""
    guaranteed_profit = portfolio.get_guaranteed_profit()
    if guaranteed_profit < risk.minimum_guaranteed_profit_usd:
        return False
    roi = portfolio.get_guaranteed_roi()
    if roi is None or roi < risk.minimum_guaranteed_roi_pct:
        return False
    return True
