from decimal import Decimal

from atlantis.btc5m_hedge.portfolio import DOWN, UP, OrderBookLevel, Portfolio


def test_worked_example_100_shares_each_side():
    """UP 100 @ 0.31, DOWN 100 @ 0.60 - cost 91, payout 100 whichever
    side wins, guaranteed_profit = 9 (the spec's own worked example)."""
    pf = Portfolio().simulate_buy(UP, Decimal(100), Decimal("0.31")).simulate_buy(DOWN, Decimal(100), Decimal("0.60"))

    assert pf.total_cost == Decimal("91")
    assert pf.get_profit_if_up() == Decimal("9")
    assert pf.get_profit_if_down() == Decimal("9")
    assert pf.get_guaranteed_profit() == Decimal("9")
    assert pf.is_locked_profit() is True


def test_worked_example_fractional_shares():
    """UP 3.2 shares costing $1 total, DOWN 4 shares costing $2 total -
    cost 3, profit_if_up=0.2, profit_if_down=1, guaranteed=0.2 (the
    spec's second worked example, using simulate_buy with an implied
    flat average price per side)."""
    up_avg_price = Decimal("1") / Decimal("3.2")
    down_avg_price = Decimal("2") / Decimal("4")
    pf = Portfolio().simulate_buy(UP, Decimal("3.2"), up_avg_price).simulate_buy(DOWN, Decimal("4"), down_avg_price)

    assert pf.total_cost == Decimal("3")
    assert pf.get_profit_if_up() == Decimal("0.2")
    assert pf.get_profit_if_down() == Decimal("1")
    assert pf.get_guaranteed_profit() == Decimal("0.2")


def test_empty_portfolio_has_no_avg_cost_and_no_roi():
    pf = Portfolio()
    assert pf.avg_up_cost is None
    assert pf.avg_down_cost is None
    assert pf.get_guaranteed_roi() is None  # no capital deployed - not the same as 0% ROI
    assert pf.get_guaranteed_profit() == Decimal(0)
    assert pf.is_locked_profit() is False  # 0 > 0 is false on both sides


def test_one_sided_position_is_not_locked_profit():
    pf = Portfolio().simulate_buy(UP, Decimal(100), Decimal("0.31"))
    assert pf.get_profit_if_up() == Decimal("69")
    assert pf.get_profit_if_down() == Decimal("-31")
    assert pf.get_guaranteed_profit() == Decimal("-31")
    assert pf.is_locked_profit() is False


def test_simulate_buy_does_not_mutate_original():
    pf = Portfolio()
    pf2 = pf.simulate_buy(UP, Decimal(10), Decimal("0.5"))
    assert pf.up_shares == Decimal(0)
    assert pf2.up_shares == Decimal(10)
    assert pf is not pf2


def test_avg_cost_reflects_multiple_fills_at_different_prices():
    pf = Portfolio().simulate_buy(UP, Decimal(100), Decimal("0.20")).simulate_buy(UP, Decimal(100), Decimal("0.40"))
    assert pf.up_shares == Decimal(200)
    assert pf.up_cost == Decimal("60")
    assert pf.avg_up_cost == Decimal("0.30")


def test_simulate_orderbook_buy_walks_multiple_levels_vwap():
    levels = [
        OrderBookLevel(Decimal("0.40"), Decimal("20")),
        OrderBookLevel(Decimal("0.42"), Decimal("30")),
        OrderBookLevel(Decimal("0.45"), Decimal("50")),
    ]
    pf, fill = Portfolio().simulate_orderbook_buy(UP, Decimal(100), levels)
    expected_cost = Decimal("20") * Decimal("0.40") + Decimal("30") * Decimal("0.42") + Decimal("50") * Decimal("0.45")
    assert fill.filled_quantity == Decimal(100)
    assert fill.total_cost == expected_cost
    assert pf.up_cost == expected_cost
    assert pf.up_shares == Decimal(100)


def test_simulate_orderbook_buy_partial_fill_when_depth_insufficient():
    levels = [OrderBookLevel(Decimal("0.50"), Decimal("10"))]
    pf, fill = Portfolio().simulate_orderbook_buy(DOWN, Decimal(100), levels)
    assert fill.filled_quantity == Decimal(10)
    assert fill.total_cost == Decimal("5")
    assert pf.down_shares == Decimal(10)


def test_simulate_orderbook_buy_no_liquidity_returns_same_portfolio():
    pf_before = Portfolio()
    pf_after, fill = pf_before.simulate_orderbook_buy(UP, Decimal(100), [])
    assert fill.filled_quantity == Decimal(0)
    assert fill.vwap_price is None
    assert pf_after == pf_before
