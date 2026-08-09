from dataclasses import replace
from decimal import Decimal

from atlantis.btc5m_hedge.config import OptimizerConfig, RiskConfig
from atlantis.btc5m_hedge.optimizer import choose_action, equalizing_quantity, hedge_buy_range
from atlantis.btc5m_hedge.portfolio import DOWN, UP, OrderBookLevel, Portfolio


def permissive_risk() -> RiskConfig:
    return RiskConfig(
        max_total_exposure_usd=Decimal(1000),
        max_exposure_per_market_usd=Decimal(1000),
        max_directional_imbalance_shares=Decimal(100000),
        minimum_guaranteed_profit_usd=Decimal("0.50"),
        minimum_guaranteed_roi_pct=Decimal("1"),
        max_order_size_usd=Decimal(1000),
        minimum_time_remaining_seconds=10,
        stop_new_entries_seconds_before_resolution=5,
        min_fill_ratio=Decimal("0.5"),
        max_forced_hedge_loss_usd=Decimal(1000),
    )


def default_optimizer_cfg(
    *, max_opening_order_usd: Decimal = Decimal(1000), cheap_open_threshold: Decimal = Decimal(0)
) -> OptimizerConfig:
    return OptimizerConfig(
        candidate_order_sizes_usd=(Decimal(1), Decimal(5), Decimal(10)),
        max_opening_order_usd=max_opening_order_usd,
        cheap_open_threshold=cheap_open_threshold,
        imbalance_penalty_lambda=Decimal("0.01"),
        objective="guaranteed_profit",
    )


def test_hedge_buy_range_matches_spec_example():
    """UP already 100 shares costing $31 (U=100, C=31). Buying DOWN at
    0.60: x_min=(31-0)/0.4=77.5, x_max=(100-31)/0.6=115 - the spec's own
    100-share purchase (edge $9) sits inside [77.5, 115]."""
    result = hedge_buy_range(buying_side_shares=Decimal(0), other_side_shares=Decimal(100), total_cost=Decimal(31), price=Decimal("0.60"))
    assert result.feasible is True
    assert result.x_min_for_target_positive == Decimal("77.5")
    assert result.x_max_for_other_positive == Decimal("115")


def test_hedge_buy_range_infeasible_when_price_too_high():
    # Buying DOWN at 0.95 when there's barely any room left (U close to C)
    result = hedge_buy_range(buying_side_shares=Decimal(0), other_side_shares=Decimal(31), total_cost=Decimal(31), price=Decimal("0.95"))
    assert result.x_max_for_other_positive == Decimal(0)
    assert result.feasible is False


def test_equalizing_quantity_balances_shares():
    assert equalizing_quantity(buying_side_shares=Decimal(0), other_side_shares=Decimal(100)) == Decimal(100)
    assert equalizing_quantity(buying_side_shares=Decimal(60), other_side_shares=Decimal(100)) == Decimal(40)
    # already ahead - nothing to buy, never a negative quantity
    assert equalizing_quantity(buying_side_shares=Decimal(120), other_side_shares=Decimal(100)) == Decimal(0)


def test_choose_action_buys_when_combined_price_is_cheap():
    portfolio = Portfolio().simulate_buy(UP, Decimal(100), Decimal("0.31"))
    up_levels = [OrderBookLevel(Decimal("0.31"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.60"), Decimal(1000))]

    decision = choose_action(
        portfolio=portfolio,
        up_levels=up_levels,
        down_levels=down_levels,
        seconds_remaining=120,
        optimizer_cfg=default_optimizer_cfg(),
        risk_cfg=permissive_risk(),
    )

    assert decision.candidate is not None
    assert decision.candidate.side == DOWN
    assert decision.candidate.new_portfolio.get_guaranteed_profit() > portfolio.get_guaranteed_profit()


def test_choose_action_opens_first_leg_from_empty_portfolio_when_pair_is_cheap():
    """Regression test for a real bug found live 2026-08-08: from an
    EMPTY portfolio, buying either side ALONE always makes
    guaranteed_profit go from exactly 0 to negative (the untouched side's
    profit becomes -cost), so delta_guaranteed_profit > 0 can never be
    true for a first leg no matter how cheap the pair is. The bot sat at
    64/64 WAIT across several windows the arb-shadow tool independently
    confirmed had real sub-$1 combined pricing. The fix: also accept a
    candidate whose PROJECTED completed-hedge profit (buying the other
    side too, at its current price) clears the safety margin."""
    up_levels = [OrderBookLevel(Decimal("0.31"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.60"), Decimal(1000))]

    decision = choose_action(
        portfolio=Portfolio(),
        up_levels=up_levels,
        down_levels=down_levels,
        seconds_remaining=120,
        optimizer_cfg=default_optimizer_cfg(),
        risk_cfg=permissive_risk(),
    )

    assert decision.candidate is not None
    assert decision.candidate.delta_guaranteed_profit <= 0  # confirms this really is the "opening" path
    assert decision.candidate.projected_guaranteed_profit >= permissive_risk().minimum_guaranteed_profit_usd


def test_cheap_open_does_not_refire_on_a_tied_position():
    """Regression test for a real cycling bug found live 2026-08-08:
    once up_shares == down_shares (a tied, already-hedged position),
    cheap_open_threshold must NOT let either side open more, even if its
    price is under the threshold - a tie is already covered, so buying
    more of one side is a fresh directional bet, not a completion.
    Without this guard, a falling UP price kept re-qualifying as "cheap"
    every time DOWN caught up to re-tie it, cycling 5 times in 13
    seconds and inflating cost from $0.50 to $15.38 while BTC trended
    hard one way, without ever reaching a positive guaranteed_profit."""
    portfolio = Portfolio().simulate_buy(UP, Decimal("1.52"), Decimal("0.33")).simulate_buy(
        DOWN, Decimal("1.52"), Decimal("0.86")
    )
    assert portfolio.up_shares == portfolio.down_shares  # tied, per the bug's own trace

    up_levels = [OrderBookLevel(Decimal("0.21"), Decimal(1000))]  # still "cheap" by the 0.40 threshold
    down_levels = [OrderBookLevel(Decimal("0.82"), Decimal(1000))]

    decision = choose_action(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels, seconds_remaining=200,
        optimizer_cfg=default_optimizer_cfg(cheap_open_threshold=Decimal("0.40")), risk_cfg=permissive_risk(),
    )

    assert decision.candidate is None


def test_opens_toward_locked_profit_does_not_refire_on_a_tied_position():
    """Regression test for a real cycling bug found live 2026-08-08 (the
    same one as the cheap-open version above, but via the OTHER opening
    path): once tied, opens_toward_locked_profit must not let either side
    open more, even if the projection at today's other-side price still
    looks profitable - the projection can't see that a moment later,
    when the completing order fires, the price has moved. Confirmed live:
    every UP buy in one window fired while already tied to DOWN - each
    round completed at a small profit, but DOWN kept getting more
    expensive each time (BTC trending hard one way), inflating cost from
    $0.50 to $26.88 chasing a moving target, and the final round never
    fully re-tied before the window closed - the lagging side won,
    -$0.50 overall despite every individual step having looked fine."""
    portfolio = Portfolio().simulate_buy(UP, Decimal("20.24"), Decimal("0.31")).simulate_buy(
        DOWN, Decimal("20.24"), Decimal("0.68")
    )
    assert portfolio.up_shares == portfolio.down_shares  # tied
    assert portfolio.get_guaranteed_profit() > 0  # already locked, per the user's own observation

    up_levels = [OrderBookLevel(Decimal("0.21"), Decimal(1000))]  # cheap enough to look tempting either way
    down_levels = [OrderBookLevel(Decimal("0.70"), Decimal(1000))]

    decision = choose_action(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels, seconds_remaining=200,
        optimizer_cfg=default_optimizer_cfg(), risk_cfg=permissive_risk(),
    )

    assert decision.candidate is None


def test_choose_action_caps_opening_size_but_not_closing_size():
    """max_opening_order_usd only limits OPENING trades (projection-
    based, from empty) - a candidate that already improves TODAY's
    guaranteed_profit (closing/topping up) is safer and may still use the
    full candidate_order_sizes_usd range. Added 2026-08-08 after a $10
    opening leg got caught by a few-seconds BTC price move before its
    completing order could fire, turning a projected +$0.50 into a
    locked -$6.16 loss - smaller opening size limits exposure to that
    same risk per attempt."""
    up_levels = [OrderBookLevel(Decimal("0.31"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.60"), Decimal(1000))]
    # A small enough profit floor that even a $1 opening candidate clears
    # it at this price pair (edge = 1 - 0.91 = 9%, so $1 -> ~$0.09) -
    # isolates the size cap as the thing under test, not the profit floor.
    lenient_risk = replace(permissive_risk(), minimum_guaranteed_profit_usd=Decimal("0.05"))

    # Tight cap: from an empty portfolio, only the $1 candidate should be
    # eligible for the opening/projection path - $5 and $10 exceed it.
    decision = choose_action(
        portfolio=Portfolio(),
        up_levels=up_levels,
        down_levels=down_levels,
        seconds_remaining=120,
        optimizer_cfg=default_optimizer_cfg(max_opening_order_usd=Decimal("1")),
        risk_cfg=lenient_risk,
    )
    assert decision.candidate is not None
    assert decision.candidate.fill.total_cost <= Decimal("1")

    # Same tight cap, but now closing an almost-complete hedge (delta > 0
    # path) - a larger order must still be allowed through.
    portfolio = Portfolio().simulate_buy(UP, Decimal(100), Decimal("0.31"))
    decision = choose_action(
        portfolio=portfolio,
        up_levels=up_levels,
        down_levels=down_levels,
        seconds_remaining=120,
        optimizer_cfg=default_optimizer_cfg(max_opening_order_usd=Decimal("1")),
        risk_cfg=lenient_risk,
    )
    assert decision.candidate is not None
    assert decision.candidate.side == DOWN
    assert decision.candidate.fill.total_cost > Decimal("1")


def test_choose_action_does_not_deepen_an_already_leading_side():
    """Regression test for a real bug found live 2026-08-08: a position
    close to fully hedged (UP already ahead of DOWN) kept getting MORE UP
    bought at a great price via the opening/projection path, deepening
    the imbalance instead of topping up DOWN - guaranteed_profit went
    from -$0.14 to -$3.14 in two more polls. Buying more of the side
    that's already ahead can never raise guaranteed_profit today (the
    trailing side is the binding constraint), so the projection path must
    not justify it - only a genuine delta improvement can."""
    # UP already leads: 28 shares vs DOWN's 21, at real cost. DOWN's ask
    # (0.30) is legitimately good, but UP's ask (0.05) is even cheaper in
    # isolation - the bug picked the cheap UP top-up instead of the DOWN
    # top-up needed to actually close the gap.
    portfolio = Portfolio().simulate_buy(UP, Decimal(28), Decimal("0.10")).simulate_buy(DOWN, Decimal(21), Decimal("0.30"))
    assert portfolio.up_shares > portfolio.down_shares  # UP is the leading side

    up_levels = [OrderBookLevel(Decimal("0.05"), Decimal(1000))]  # very cheap - tempting to over-buy
    down_levels = [OrderBookLevel(Decimal("0.30"), Decimal(1000))]

    decision = choose_action(
        portfolio=portfolio,
        up_levels=up_levels,
        down_levels=down_levels,
        seconds_remaining=120,
        optimizer_cfg=default_optimizer_cfg(),
        risk_cfg=permissive_risk(),
    )

    assert decision.candidate is not None
    assert decision.candidate.side == DOWN  # must top up the lagging side, never the leading one


def test_choose_action_waits_when_no_edge_exists():
    # Up+Down asks sum to exactly 1.00 with no positions yet - any buy on
    # either side alone just makes the OTHER side's profit more negative
    # with no way to improve guaranteed_profit, since nothing is held on
    # the other leg yet at 1.00 fair value.
    up_levels = [OrderBookLevel(Decimal("0.50"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.50"), Decimal(1000))]

    decision = choose_action(
        portfolio=Portfolio(),
        up_levels=up_levels,
        down_levels=down_levels,
        seconds_remaining=120,
        optimizer_cfg=default_optimizer_cfg(),
        risk_cfg=permissive_risk(),
    )

    assert decision.candidate is None


def test_choose_action_rejects_when_too_close_to_resolution():
    portfolio = Portfolio().simulate_buy(UP, Decimal(100), Decimal("0.31"))
    up_levels = [OrderBookLevel(Decimal("0.31"), Decimal(1000))]
    down_levels = [OrderBookLevel(Decimal("0.60"), Decimal(1000))]

    decision = choose_action(
        portfolio=portfolio,
        up_levels=up_levels,
        down_levels=down_levels,
        seconds_remaining=2,  # below both minimum_time_remaining_seconds and the entry cutoff
        optimizer_cfg=default_optimizer_cfg(),
        risk_cfg=permissive_risk(),
    )

    assert decision.candidate is None
    assert "riesgo" in decision.reason
