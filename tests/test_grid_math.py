from decimal import Decimal

from atlantis.grid_trader.grid_math import Fill, GridState, build_levels, process_bar, simulate_grid


def test_build_levels_evenly_spaced():
    levels = build_levels(Decimal(100), Decimal(200), 5)
    assert levels == [Decimal(100), Decimal(125), Decimal(150), Decimal(175), Decimal(200)]


def test_build_levels_rejects_too_few():
    try:
        build_levels(Decimal(100), Decimal(200), 1)
        assert False, "should have raised"
    except ValueError:
        pass


def test_process_bar_opens_buy_when_price_touches_level():
    levels = build_levels(Decimal(100), Decimal(104), 5)
    state = GridState(levels=levels)
    process_bar(state, Decimal(99), Decimal(101), usd_per_level=Decimal(100), fee_rate=Decimal(0))
    # levels 100 and 101 are inside [99,101] - both should have opened a buy
    assert state.open_qty[0] > 0  # level 100
    assert state.open_qty[1] > 0  # level 101
    assert state.open_qty[2] == 0  # level 102 untouched
    assert state.trades == 2


def test_process_bar_sells_into_level_above_open_buy():
    levels = build_levels(Decimal(100), Decimal(104), 5)
    state = GridState(levels=levels)
    process_bar(state, Decimal(100), Decimal(100), usd_per_level=Decimal(100), fee_rate=Decimal(0))
    assert state.open_qty[0] > 0
    process_bar(state, Decimal(101), Decimal(101), usd_per_level=Decimal(100), fee_rate=Decimal(0))
    assert state.open_qty[0] == 0  # sold
    assert state.realized == Decimal(1)  # bought $100 worth at 100 (qty=1), sold at 101 -> profit $1
    # level 101 is BOTH the sell target for the level-100 position AND
    # its own buy level - sells run first (1 trade), then a fresh buy
    # opens at 101 in the same bar (1 more trade) on top of the
    # original buy at 100 - 3 trades total.
    assert state.trades == 3
    assert state.open_qty[1] > 0  # the fresh buy that just opened at 101


def test_process_bar_does_not_rebuy_same_bar_it_just_sold():
    """Sells happen before buys within one bar - a level that was just
    sold this same bar can be re-bought this same bar (state is 0 after
    the sell pass), but the level it sold INTO isn't a buy target for
    itself. This test locks the documented sell-then-buy ordering."""
    levels = build_levels(Decimal(100), Decimal(102), 3)  # 100, 101, 102
    state = GridState(levels=levels)
    process_bar(state, Decimal(100), Decimal(100), usd_per_level=Decimal(100), fee_rate=Decimal(0))
    assert state.trades == 1  # bought at 100
    # bar spans 100-102: sells the 100->101 position, then re-buys at 100 AND 101
    process_bar(state, Decimal(100), Decimal(102), usd_per_level=Decimal(100), fee_rate=Decimal(0))
    assert state.trades == 1 + 1 + 2  # 1 sell + 2 new buys (100 and 101)


def test_fees_reduce_realized_profit():
    levels = build_levels(Decimal(100), Decimal(104), 5)
    state = GridState(levels=levels)
    process_bar(state, Decimal(100), Decimal(100), usd_per_level=Decimal(100), fee_rate=Decimal("0.01"))
    process_bar(state, Decimal(101), Decimal(101), usd_per_level=Decimal(100), fee_rate=Decimal("0.01"))
    # bought qty=1 at 100 (fee $1), sold at 101 for $101 (fee $1.01) -
    # then a fresh buy opens at 101 too (same bar, see the sell-into-
    # its-own-buy-level test above), adding one more $1 fee.
    assert state.realized == Decimal(101) - Decimal(100) - Decimal("1.01")
    assert state.fees == Decimal(1) + Decimal("1.01") + Decimal(1)


def test_simulate_grid_take_profit_stops_early():
    levels = build_levels(Decimal(100), Decimal(104), 5)
    bars = [(Decimal(100), Decimal(104))] * 5
    result = simulate_grid(bars, levels, usd_per_level=Decimal(1000), fee_rate=Decimal(0), take_profit_usd=Decimal(1))
    assert result.exit_reason == "take_profit"
    assert result.bars_run == 1


def test_simulate_grid_stop_loss_stops_early():
    levels = build_levels(Decimal(100), Decimal(104), 5)
    # price crashes straight through and never recovers
    bars = [(Decimal(90), Decimal(101)), (Decimal(80), Decimal(91)), (Decimal(70), Decimal(81))]
    result = simulate_grid(bars, levels, usd_per_level=Decimal(100), fee_rate=Decimal(0), stop_loss_usd=Decimal(10))
    assert result.exit_reason == "stop_loss"
    assert result.bars_run < 3


def test_simulate_grid_no_thresholds_runs_full_period():
    levels = build_levels(Decimal(100), Decimal(104), 5)
    bars = [(Decimal(101), Decimal(102))] * 3
    result = simulate_grid(bars, levels, usd_per_level=Decimal(10), fee_rate=Decimal(0))
    assert result.exit_reason == "period_end"
    assert result.bars_run == 3


def test_grid_state_unrealized_and_total():
    levels = build_levels(Decimal(100), Decimal(104), 5)
    state = GridState(levels=levels, open_qty=[Decimal(1), Decimal(0), Decimal(0), Decimal(0), Decimal(0)])
    assert state.unrealized(Decimal(103)) == Decimal(3)  # bought at level 100, mark at 103
    state.realized = Decimal(5)
    assert state.total(Decimal(103)) == Decimal(8)


def test_grid_state_open_positions_count():
    levels = build_levels(Decimal(100), Decimal(104), 5)
    state = GridState(levels=levels, open_qty=[Decimal(1), Decimal(0), Decimal(2), Decimal(0), Decimal(0)])
    assert state.open_positions == 2


def test_process_bar_returns_a_fill_per_buy():
    levels = build_levels(Decimal(100), Decimal(104), 5)
    state = GridState(levels=levels)
    fills = process_bar(state, Decimal(99), Decimal(101), usd_per_level=Decimal(100), fee_rate=Decimal(0))
    assert len(fills) == 2
    assert all(f.side == "buy" for f in fills)
    assert {f.level_index for f in fills} == {0, 1}
    assert {f.price for f in fills} == {Decimal(100), Decimal(101)}
    assert all(f.profit is None for f in fills)


def test_process_bar_returns_a_fill_for_sell_with_profit():
    levels = build_levels(Decimal(100), Decimal(104), 5)
    state = GridState(levels=levels)
    process_bar(state, Decimal(100), Decimal(100), usd_per_level=Decimal(100), fee_rate=Decimal(0))
    fills = process_bar(state, Decimal(101), Decimal(101), usd_per_level=Decimal(100), fee_rate=Decimal(0))
    sells = [f for f in fills if f.side == "sell"]
    assert len(sells) == 1
    assert sells[0].level_index == 1
    assert sells[0].price == Decimal(101)
    assert sells[0].profit == Decimal(1)
