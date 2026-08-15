from decimal import Decimal

from atlantis.btc5m_longshot.signal import DOWN, UP, decide

DEFAULTS = dict(entry_window_seconds=150.0, min_underdog_price=Decimal("0.05"), max_underdog_price=Decimal("0.25"))


def test_decide_too_early_in_window_no_bet():
    decision = decide(up_price=Decimal("0.15"), down_price=Decimal("0.85"), seconds_remaining=200, **DEFAULTS)
    assert not decision.should_bet
    assert decision.side is None


def test_decide_at_entry_window_boundary_is_allowed():
    decision = decide(up_price=Decimal("0.15"), down_price=Decimal("0.85"), seconds_remaining=150, **DEFAULTS)
    assert decision.should_bet


def test_decide_missing_price_no_bet():
    decision = decide(up_price=None, down_price=Decimal("0.85"), seconds_remaining=60, **DEFAULTS)
    assert not decision.should_bet
    decision2 = decide(up_price=Decimal("0.15"), down_price=None, seconds_remaining=60, **DEFAULTS)
    assert not decision2.should_bet


def test_decide_picks_the_cheaper_side_as_underdog():
    decision = decide(up_price=Decimal("0.12"), down_price=Decimal("0.88"), seconds_remaining=60, **DEFAULTS)
    assert decision.should_bet
    assert decision.side == UP

    decision2 = decide(up_price=Decimal("0.88"), down_price=Decimal("0.12"), seconds_remaining=60, **DEFAULTS)
    assert decision2.should_bet
    assert decision2.side == DOWN


def test_decide_rejects_extreme_longshot_below_min():
    """Regression guard for the real 2026-08-15 finding: prices below
    $0.05 (payouts above ~20x) are a CONFIRMED LOSING bet (as low as
    -$0.59 per dollar on a 99-sample real bucket) - must never fire,
    even though the payout looks the most tempting."""
    decision = decide(up_price=Decimal("0.015"), down_price=Decimal("0.985"), seconds_remaining=60, **DEFAULTS)
    assert not decision.should_bet
    assert decision.side is None


def test_decide_rejects_favorite_side_above_max():
    """A price above max_underdog_price means it's not a real underdog
    bet anymore (or the OTHER side already crossed into range and would
    have been picked) - must not fire on an expensive, low-payout side."""
    decision = decide(up_price=Decimal("0.60"), down_price=Decimal("0.40"), seconds_remaining=60, **DEFAULTS)
    assert not decision.should_bet


def test_decide_enters_at_real_calibrated_price_points():
    """Real 2026-08-15 buckets, all confirmed positive EV: $0.05-0.10
    (+$0.52/$), $0.10-0.15 (+$0.46/$), $0.15-0.25 (+$0.75/$)."""
    for price in (Decimal("0.07"), Decimal("0.12"), Decimal("0.20")):
        decision = decide(up_price=price, down_price=Decimal(1) - price, seconds_remaining=45, **DEFAULTS)
        assert decision.should_bet, f"expected a bet at underdog price {price}"
        assert decision.side == UP
