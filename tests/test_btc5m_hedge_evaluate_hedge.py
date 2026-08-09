"""The 4 worked test cases from the 2026-08-08 MODE A/B/C spec, plus
coverage for generate_lagging_side_candidates (the share/percent-based
candidate generator MODE B/C search over)."""

from decimal import Decimal

from atlantis.btc5m_hedge.config import HedgeTimingConfig, OptimizerConfig, RiskConfig
from atlantis.btc5m_hedge.optimizer import (
    DEFENSIVE_HEDGE,
    EMERGENCY_HEDGE,
    PROFIT_HEDGE,
    evaluate_hedge,
    generate_lagging_side_candidates,
)
from atlantis.btc5m_hedge.portfolio import DOWN, UP, OrderBookLevel, Portfolio

DEFAULT_SHARE_QUANTITIES = (Decimal(1), Decimal(2), Decimal(5), Decimal(10), Decimal(20), Decimal(25), Decimal(50), Decimal(75), Decimal(100))
DEFAULT_IMBALANCE_FRACTIONS = (Decimal("0.25"), Decimal("0.5"), Decimal("0.75"), Decimal("1.0"))


def hedge_timing(**overrides) -> HedgeTimingConfig:
    base = dict(
        profit_hedge_only_seconds=120.0,
        defensive_hedge_start_seconds=90.0,
        emergency_hedge_start_seconds=30.0,
        min_worst_case_improvement_pct=Decimal("0.20"),
        defensive_share_quantities=DEFAULT_SHARE_QUANTITIES,
        defensive_imbalance_fractions=DEFAULT_IMBALANCE_FRACTIONS,
        price_runaway_window_samples=45,
        price_runaway_min_move=Decimal("0.10"),
        price_runaway_max_pullback=Decimal("0.03"),
    )
    base.update(overrides)
    return HedgeTimingConfig(**base)


def risk(**overrides) -> RiskConfig:
    base = dict(
        max_total_exposure_usd=Decimal(1000),
        max_exposure_per_market_usd=Decimal(1000),
        max_directional_imbalance_shares=Decimal(100000),
        minimum_guaranteed_profit_usd=Decimal("0.50"),
        minimum_guaranteed_roi_pct=Decimal("1"),
        max_order_size_usd=Decimal(1000),
        minimum_time_remaining_seconds=10.0,
        stop_new_entries_seconds_before_resolution=5.0,
        min_fill_ratio=Decimal("0.5"),
        max_forced_hedge_loss_usd=Decimal(1000),  # permissive by default - see tests exercising the floor itself
    )
    base.update(overrides)
    return RiskConfig(**base)


def optimizer(**overrides) -> OptimizerConfig:
    base = dict(
        candidate_order_sizes_usd=(Decimal(1), Decimal(5), Decimal(10)),
        max_opening_order_usd=Decimal(1000),
        cheap_open_threshold=Decimal(0),  # disabled by default - see test_opens_on_cheap_price_alone for its own test
        imbalance_penalty_lambda=Decimal("0.01"),
        objective="guaranteed_profit",
    )
    base.update(overrides)
    return OptimizerConfig(**base)


def test_opens_on_cheap_price_alone_without_an_instant_profitable_pair():
    """Regression test for a real gap found live 2026-08-08: with
    max_opening_order_usd=$2 and minimum_guaranteed_profit_usd=$0.50
    (the pre-fix default), opening required a ~25% combined edge, which
    essentially never occurs - the bot went 3.5+ hours without opening
    anything while the owner watched UP trade at 37-42c on Polymarket's
    own book. UP@0.35 with DOWN@0.70 (sum=1.05, NOT profitable even
    projected - opens_toward_locked_profit alone would reject this) must
    still open via cheap_open_threshold, mirroring how the real wallet
    actually trades: buy the cheap side on its own merits, lean on
    MODE B/C to complete or damage-control later."""
    up_levels = [OrderBookLevel(Decimal("0.35"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.70"), Decimal(1000))]

    decision = evaluate_hedge(
        portfolio=Portfolio(), up_levels=up_levels, down_levels=down_levels, seconds_remaining=200,
        optimizer_cfg=optimizer(cheap_open_threshold=Decimal("0.40")), risk_cfg=risk(),
        hedge_timing_cfg=hedge_timing(),
    )

    assert decision.candidate is not None
    assert decision.candidate.side == UP
    assert decision.candidate.fill.vwap_price <= Decimal("0.40")
    # Confirms this really did NOT come from the profitable-projection
    # path - the resulting position is still guaranteed-negative.
    assert decision.candidate.new_portfolio.get_guaranteed_profit() < 0


def test_case_1_full_profit_hedge():
    """100 UP @ 0.40, DOWN @ 0.47 -> full hedge 100 DOWN, MODE:
    PROFIT_HEDGE, profit both outcomes = +13."""
    portfolio = Portfolio().simulate_buy(UP, Decimal(100), Decimal("0.40"))
    up_levels = [OrderBookLevel(Decimal("0.40"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.47"), Decimal(1000))]

    decision = evaluate_hedge(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels, seconds_remaining=200,
        optimizer_cfg=optimizer(), risk_cfg=risk(), hedge_timing_cfg=hedge_timing(),
    )

    assert decision.hedge_mode == PROFIT_HEDGE
    assert decision.candidate.side == DOWN
    assert decision.candidate.fill.filled_quantity == Decimal(100)
    new_pf = decision.candidate.new_portfolio
    assert new_pf.get_profit_if_up() == Decimal(13)
    assert new_pf.get_profit_if_down() == Decimal(13)
    assert new_pf.get_guaranteed_profit() == Decimal(13)


def test_case_2_defensive_hedge_reduces_max_loss():
    """100 UP @ 0.40, DOWN @ 0.60, 60s remaining - no full profitable
    hedge exists (break-even at best), but a big-enough affordable
    partial hedge (e.g. 50 DOWN: cost 70, profit_if_up +30, profit_if_down
    -20) cuts max loss from 40 to 20 (50%, clears the 20% bar) - MODE B
    should fire. max_order_size_usd is generous here specifically so the
    50-share example itself is affordable (see test_case_3 for the
    opposite: a tight order cap making every affordable hedge too small
    to be worth it)."""
    portfolio = Portfolio().simulate_buy(UP, Decimal(100), Decimal("0.40"))
    up_levels = [OrderBookLevel(Decimal("0.40"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.60"), Decimal(1000))]

    # Confirm the spec's own worked numbers for the 50-share example,
    # independent of what the optimizer actually picks.
    manual_pf, manual_fill = portfolio.simulate_orderbook_buy(DOWN, Decimal(50), down_levels)
    assert manual_fill.total_cost == Decimal(30)
    assert manual_pf.total_cost == Decimal(70)
    assert manual_pf.get_profit_if_up() == Decimal(30)
    assert manual_pf.get_profit_if_down() == Decimal(-20)

    decision = evaluate_hedge(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels, seconds_remaining=60,
        optimizer_cfg=optimizer(), risk_cfg=risk(max_order_size_usd=Decimal(100)), hedge_timing_cfg=hedge_timing(),
    )

    assert decision.hedge_mode == DEFENSIVE_HEDGE
    assert decision.candidate.side == DOWN
    # Whatever the optimizer actually chose must be AT LEAST as good as
    # the spec's own 50-share example (new_max_loss <= 20).
    assert decision.new_max_loss <= Decimal(20)
    assert decision.loss_reduction_pct >= Decimal("0.20")


def test_case_3_waits_when_only_affordable_hedges_are_too_small():
    """100 UP @ 0.40, DOWN @ 0.80, 100s remaining - a tight
    max_order_size_usd (10) means only small DOWN buys (1/2/5/10 shares)
    are affordable, and none of them cut max loss by the required
    fraction. 100s also sits in the 90-120s sub-band, which doubles the
    bar (20% -> 40%) per this repo's "mejora mucho" interpretation -
    together, nothing clears it. Expected: WAIT."""
    portfolio = Portfolio().simulate_buy(UP, Decimal(100), Decimal("0.40"))
    up_levels = [OrderBookLevel(Decimal("0.40"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.80"), Decimal(1000))]

    decision = evaluate_hedge(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels, seconds_remaining=100,
        optimizer_cfg=optimizer(), risk_cfg=risk(max_order_size_usd=Decimal(10)), hedge_timing_cfg=hedge_timing(),
    )

    assert decision.candidate is None
    assert decision.hedge_mode == "WAIT"


def test_case_4_emergency_hedge_maximizes_worst_case():
    """100 UP @ 0.40, DOWN @ 0.70, 15s remaining -> EMERGENCY_HEDGE.
    profit_if_up(x) = 60 - 0.7x and profit_if_down(x) = 0.3x - 40 cross
    (i.e. min(...) is maximized) exactly at x=100 for these numbers - the
    optimizer must find that by search, not by being hardcoded to always
    buy the full imbalance (a different price would move the optimum
    quantity, per hedge_buy_range/equalizing_quantity's own derivation)."""
    portfolio = Portfolio().simulate_buy(UP, Decimal(100), Decimal("0.40"))
    up_levels = [OrderBookLevel(Decimal("0.40"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.70"), Decimal(1000))]

    decision = evaluate_hedge(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels, seconds_remaining=15,
        optimizer_cfg=optimizer(), risk_cfg=risk(max_order_size_usd=Decimal(100)), hedge_timing_cfg=hedge_timing(),
    )

    assert decision.hedge_mode == EMERGENCY_HEDGE
    assert decision.candidate.side == DOWN
    assert decision.candidate.fill.filled_quantity == Decimal(100)
    assert decision.new_worst_case_profit == Decimal(-10)


def test_emergency_hedge_never_worsens_an_already_tied_locked_position():
    """Regression test for a real case found live 2026-08-08: a tied
    position already sitting at +$0.10 guaranteed (an 8%+ edge) kept
    getting bought into by MODE C anyway, ending at -$1.48 twelve orders
    later. Buying more of a tied position's tie-break side provably
    LOWERS worst_case_profit for any price > 0 (see optimizer.py's own
    comment on the algebra) - MODE C having "no profit floor" must not
    be read as "no floor against making things worse than doing
    nothing"."""
    portfolio = Portfolio().simulate_buy(UP, Decimal("1.25"), Decimal("0.40")).simulate_buy(
        DOWN, Decimal("1.25"), Decimal("0.52")
    )
    assert portfolio.up_shares == portfolio.down_shares
    assert portfolio.get_guaranteed_profit() > 0  # +$0.10, per the real trace

    up_levels = [OrderBookLevel(Decimal("0.40"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.52"), Decimal(1000))]

    decision = evaluate_hedge(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels, seconds_remaining=15,
        optimizer_cfg=optimizer(), risk_cfg=risk(), hedge_timing_cfg=hedge_timing(),
    )

    assert decision.candidate is None
    assert decision.hedge_mode == "WAIT"


def test_emergency_hedge_never_opens_a_position_from_empty():
    """Regression test for a real case found live 2026-08-08: MODE C
    opened a position from a completely empty portfolio with ~28s left
    while the market was moving hard in one direction, needing 12
    alternating 1-share buys just to keep shares tied and closing
    guaranteed-negative (-$0.35) even though each individual buy
    technically improved the worst case versus the position opening
    itself had just created. With nothing at risk yet
    (current_worst_case_profit == 0), there's nothing for MODE C to
    defend - it should WAIT, not start a hedge this late against a
    moving target."""
    up_levels = [OrderBookLevel(Decimal("0.59"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.43"), Decimal(1000))]

    decision = evaluate_hedge(
        portfolio=Portfolio(), up_levels=up_levels, down_levels=down_levels, seconds_remaining=15,
        optimizer_cfg=optimizer(), risk_cfg=risk(max_order_size_usd=Decimal(100)), hedge_timing_cfg=hedge_timing(),
    )

    assert decision.candidate is None
    assert decision.hedge_mode == "WAIT"


def test_price_runaway_grabs_a_partial_recovery_that_the_normal_bars_would_reject():
    """Regression test for the real 2026-08-09 case that motivated adding
    price_runaway: window btc-updown-5m-1786263300 opened DOWN 7.692308
    shares @ $0.39 ($3.00, guaranteed=-$3.00), then UP climbed with almost
    no pullback. By the time detect_price_runaway's 45-sample window
    would have confirmed the trend (~193s remaining, UP @ $0.89), tying
    the position would only reach guaranteed=-$2.15 - worse than both
    MODE B's doubled 40% loss-reduction bar (this only clears ~28%) and
    MODE C's max_forced_hedge_loss_usd floor ($0.45). Without
    price_runaway, evaluate_hedge must still WAIT (unchanged default
    behavior - it doesn't know a trend is happening). With
    price_runaway=True, it must grab this partial recovery anyway,
    because -$2.15 beats letting the still-open $3.00 stake ride into a
    likely total loss (which is exactly how this window actually ended
    live: -$3.00)."""
    portfolio = Portfolio().simulate_buy(DOWN, Decimal("7.692307692307692307692307692"), Decimal("0.39"))
    assert portfolio.get_guaranteed_profit() == Decimal("-3")

    up_levels = [OrderBookLevel(Decimal("0.89"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.39"), Decimal(1000))]
    generous_risk = risk(max_order_size_usd=Decimal(100), max_forced_hedge_loss_usd=Decimal("0.45"))

    without_runaway = evaluate_hedge(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels, seconds_remaining=193,
        optimizer_cfg=optimizer(), risk_cfg=generous_risk, hedge_timing_cfg=hedge_timing(),
    )
    assert without_runaway.candidate is None
    assert without_runaway.hedge_mode == "WAIT"

    with_runaway = evaluate_hedge(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels, seconds_remaining=193,
        optimizer_cfg=optimizer(), risk_cfg=generous_risk, hedge_timing_cfg=hedge_timing(),
        price_runaway=True,
    )
    assert with_runaway.candidate is not None
    assert with_runaway.hedge_mode == EMERGENCY_HEDGE
    new_guaranteed = with_runaway.candidate.new_portfolio.get_guaranteed_profit()
    assert Decimal("-2.2") < new_guaranteed < Decimal("-2.1")  # ~-$2.15, beats -$3.00 but well short of the normal floor


def test_emergency_hedge_refuses_expensive_insurance_and_leaves_position_unhedged():
    """Regression test for a real case found live 2026-08-08 (window
    btc-updown-5m-1786218600): DOWN opened at $0.39 (cost $0.50, 1.282051
    shares, guaranteed=-$0.50), then with 29s left the only UP liquidity
    was $0.999 - MODE C bought it anyway because it was technically an
    improvement (-$0.50 -> -$0.4987), a near-total loss "insurance"
    trade. The open DOWN leg alone had an expected value around +$0.14 if
    just left unhedged for the coin flip (profit_if_down = +0.78,
    profit_if_up = -0.50). With a realistic max_forced_hedge_loss_usd
    ($0.15), no quantity of $0.999 UP can ever bring guaranteed_profit
    above -$0.3906 (the best achievable is the full equalizing buy, which
    reproduces the exact real -$0.4987) - MODE C must refuse all of them
    and WAIT instead of paying for that insurance."""
    portfolio = Portfolio().simulate_buy(DOWN, Decimal("1.282051282051282051282051282"), Decimal("0.39"))
    assert portfolio.get_guaranteed_profit() == Decimal("-0.5")

    up_levels = [OrderBookLevel(Decimal("0.999"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.39"), Decimal(1000))]

    decision = evaluate_hedge(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels, seconds_remaining=29,
        optimizer_cfg=optimizer(), risk_cfg=risk(max_order_size_usd=Decimal(100), max_forced_hedge_loss_usd=Decimal("0.15")),
        hedge_timing_cfg=hedge_timing(),
    )

    assert decision.candidate is None
    assert decision.hedge_mode == "WAIT"


def test_defensive_hedge_refuses_expensive_insurance_and_leaves_position_unhedged():
    """Regression test for a real case found live 2026-08-08 (window
    btc-updown-5m-1786217100): DOWN opened at $0.32 (cost $0.50,
    guaranteed=-$0.50), then with 88s left the only UP liquidity was
    $0.93 - MODE B bought the full equalizing quantity because it cut
    max_loss by 21.9% (clears the 20% bar), locking guaranteed=-$0.3906 -
    a real loss, not a "reduced but still fine" outcome. With a realistic
    max_forced_hedge_loss_usd ($0.15), the best achievable result at this
    price (the full tie, -$0.3906) still breaches the floor, so MODE B
    must refuse every candidate and WAIT rather than pay for it."""
    portfolio = Portfolio().simulate_buy(DOWN, Decimal("1.5625"), Decimal("0.32"))
    assert portfolio.get_guaranteed_profit() == Decimal("-0.5")

    up_levels = [OrderBookLevel(Decimal("0.93"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.32"), Decimal(1000))]

    decision = evaluate_hedge(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels, seconds_remaining=88,
        optimizer_cfg=optimizer(), risk_cfg=risk(max_order_size_usd=Decimal(100), max_forced_hedge_loss_usd=Decimal("0.15")),
        hedge_timing_cfg=hedge_timing(),
    )

    assert decision.candidate is None
    assert decision.hedge_mode == "WAIT"


def test_generate_lagging_side_candidates_only_targets_lagging_side():
    portfolio = Portfolio().simulate_buy(UP, Decimal(100), Decimal("0.40"))
    up_levels = [OrderBookLevel(Decimal("0.40"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.60"), Decimal(1000))]

    candidates = generate_lagging_side_candidates(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels,
        hedge_timing_cfg=hedge_timing(), optimizer_cfg=optimizer(),
    )

    assert candidates  # non-empty
    assert all(c.side == DOWN for c in candidates)  # never the already-leading UP side


def test_generate_lagging_side_candidates_includes_configured_sizes_and_equalizing_quantity():
    portfolio = Portfolio().simulate_buy(UP, Decimal(100), Decimal("0.40"))
    up_levels = [OrderBookLevel(Decimal("0.40"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.60"), Decimal(1000))]

    candidates = generate_lagging_side_candidates(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels,
        hedge_timing_cfg=hedge_timing(), optimizer_cfg=optimizer(),
    )
    quantities = {c.requested_quantity for c in candidates}

    for expected in (Decimal(1), Decimal(10), Decimal(50), Decimal(100)):  # from defensive_share_quantities
        assert expected in quantities
    assert Decimal(25) in quantities  # 0.25 * imbalance(100)
    assert Decimal(100) in quantities  # also the equalizing_quantity full-close


def test_generate_lagging_side_candidates_empty_when_no_liquidity():
    portfolio = Portfolio().simulate_buy(UP, Decimal(100), Decimal("0.40"))
    candidates = generate_lagging_side_candidates(
        portfolio=portfolio, up_levels=[], down_levels=[],
        hedge_timing_cfg=hedge_timing(), optimizer_cfg=optimizer(),
    )
    assert candidates == []
