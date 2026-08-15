from decimal import Decimal

from atlantis.btc5m_momentum.signal import DOWN, UP, compute_momentum_pct, decide, evaluate_signal


def test_compute_momentum_pct_positive_move():
    # Real sample scale: BTC ~$65,000, a $5.40 move over 3 minutes.
    assert compute_momentum_pct(Decimal("65005.40"), Decimal("65000.00")) == Decimal("5.40") / Decimal("65000.00")


def test_compute_momentum_pct_negative_move():
    pct = compute_momentum_pct(Decimal("64994.60"), Decimal("65000.00"))
    assert pct < 0


def test_compute_momentum_pct_zero_lookback_is_degenerate_not_a_crash():
    assert compute_momentum_pct(Decimal("100"), Decimal("0")) == Decimal(0)


def test_evaluate_signal_below_threshold_returns_none():
    # Real data: below-median moves showed 50.7% accuracy - no edge, must not fire.
    assert evaluate_signal(Decimal("0.00003"), min_pct_move=Decimal("0.00008"), long_momentum_pct=Decimal("0.001")) is None
    assert evaluate_signal(Decimal("-0.00003"), min_pct_move=Decimal("0.00008"), long_momentum_pct=Decimal("-0.001")) is None


def test_evaluate_signal_favors_up_on_positive_momentum():
    signal = evaluate_signal(Decimal("0.0002"), min_pct_move=Decimal("0.00008"), long_momentum_pct=Decimal("0.001"))
    assert signal is not None
    assert signal.side == UP


def test_evaluate_signal_favors_down_on_negative_momentum():
    signal = evaluate_signal(Decimal("-0.0002"), min_pct_move=Decimal("0.00008"), long_momentum_pct=Decimal("-0.001"))
    assert signal is not None
    assert signal.side == DOWN


def test_evaluate_signal_rejects_when_short_and_long_trend_disagree():
    """Regression test for the real 2026-08-12 failure day: BTC grinding
    up slowly (positive long-term trend) with many 3-minute dips along
    the way (negative short momentum) - each dip fired a DOWN signal
    that lost against the underlying uptrend over and over. Once the
    short signal disagrees with the 20-min trend, it must not fire."""
    assert evaluate_signal(Decimal("-0.0002"), min_pct_move=Decimal("0.00008"), long_momentum_pct=Decimal("0.001")) is None
    assert evaluate_signal(Decimal("0.0002"), min_pct_move=Decimal("0.00008"), long_momentum_pct=Decimal("-0.001")) is None


def test_evaluate_signal_fires_when_short_and_long_trend_agree():
    signal = evaluate_signal(Decimal("0.0002"), min_pct_move=Decimal("0.00008"), long_momentum_pct=Decimal("0.0015"))
    assert signal is not None
    assert signal.side == UP


def test_decide_no_signal_means_no_bet():
    decision = decide(None, Decimal("0.50"), max_entry_price=Decimal("0.55"))
    assert not decision.should_bet
    assert decision.side is None


def test_decide_no_liquidity_means_no_bet():
    signal = evaluate_signal(Decimal("0.0002"), min_pct_move=Decimal("0.00008"), long_momentum_pct=Decimal("0.001"))
    decision = decide(signal, None, max_entry_price=Decimal("0.55"))
    assert not decision.should_bet


def test_decide_refuses_when_favored_side_already_too_expensive():
    """Regression guard for the real 2026-08-09 finding: the market
    normally barely reprices on momentum (~$0.51-0.52), but if it ever
    DOES move the favored side past max_entry_price, the edge this
    strategy is built on is gone and it must not enter anyway."""
    signal = evaluate_signal(Decimal("0.0002"), min_pct_move=Decimal("0.00008"), long_momentum_pct=Decimal("0.001"))
    decision = decide(signal, Decimal("0.70"), max_entry_price=Decimal("0.55"))
    assert not decision.should_bet
    assert decision.side is None


def test_decide_enters_at_the_real_calibrated_price_point():
    """UP @ $0.519, the real average entry price observed for strong
    momentum in the 2026-08-09 sample - must clear the default
    max_entry_price of $0.55."""
    signal = evaluate_signal(Decimal("0.0002"), min_pct_move=Decimal("0.00008"), long_momentum_pct=Decimal("0.001"))
    decision = decide(signal, Decimal("0.519"), max_entry_price=Decimal("0.55"))
    assert decision.should_bet
    assert decision.side == UP
