from decimal import Decimal

from atlantis.btc5m_longshot.position import Position
from atlantis.btc5m_longshot.signal import DOWN, UP


def test_empty_position_realizes_zero_regardless_of_outcome():
    pos = Position()
    assert pos.realized_profit(UP) == Decimal(0)
    assert pos.realized_profit(DOWN) == Decimal(0)
    assert not pos.is_open


def test_position_wins_pays_shares_minus_cost_high_payout():
    # $1 bet at $0.12 (an 8.3x payout underdog) -> ~8.33 shares
    pos = Position(side=UP, quantity=Decimal("8.3333"), cost=Decimal("1"))
    assert pos.realized_profit(UP) == Decimal("8.3333") - Decimal("1")


def test_position_loses_full_cost():
    pos = Position(side=UP, quantity=Decimal("8.3333"), cost=Decimal("1"))
    assert pos.realized_profit(DOWN) == Decimal("-1")


def test_position_unresolved_outcome_is_zero_not_a_loss():
    pos = Position(side=DOWN, quantity=Decimal("4"), cost=Decimal("1"))
    assert pos.realized_profit(None) == Decimal(0)
