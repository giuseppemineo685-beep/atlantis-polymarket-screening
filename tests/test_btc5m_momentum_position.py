from decimal import Decimal

from atlantis.btc5m_momentum.position import Position
from atlantis.btc5m_momentum.signal import DOWN, UP


def test_empty_position_realizes_zero_regardless_of_outcome():
    pos = Position()
    assert pos.realized_profit(UP) == Decimal(0)
    assert pos.realized_profit(DOWN) == Decimal(0)
    assert not pos.is_open


def test_position_wins_pays_shares_minus_cost():
    pos = Position(side=UP, quantity=Decimal("3.8534"), cost=Decimal("2"))
    assert pos.realized_profit(UP) == Decimal("3.8534") - Decimal("2")


def test_position_loses_full_cost():
    pos = Position(side=UP, quantity=Decimal("3.8534"), cost=Decimal("2"))
    assert pos.realized_profit(DOWN) == Decimal("-2")


def test_position_unresolved_outcome_is_zero_not_a_loss():
    """None means "not resolved yet" - must not be silently treated as a
    loss (same principle as atlantis/btc5m_hedge/logger.py's own
    realized_outcome handling)."""
    pos = Position(side=DOWN, quantity=Decimal("4"), cost=Decimal("2"))
    assert pos.realized_profit(None) == Decimal(0)
