from decimal import Decimal

import scripts.run_forex_grid_trader_paper as bot
from atlantis.forex.config import ForexTraderConfig
from atlantis.forex.oanda_client import InstrumentInfo, OrderResult
from atlantis.grid_trader.grid_math import build_levels
from atlantis.grid_trader.position_store import Position


def _config(tmp_path, execute_real_orders: bool) -> ForexTraderConfig:
    return ForexTraderConfig(
        usd_per_level=Decimal(100), num_levels=5, take_profit_pct=Decimal(1000), stop_loss_pct=Decimal(1000),
        flat_pos_min=Decimal(20), flat_pos_max=Decimal(80), max_concurrent_positions=999,
        scan_interval_seconds=900, poll_interval_seconds=30, fee_rate=Decimal(0),
        execute_real_orders=execute_real_orders,
        screener_snapshot_path=tmp_path / "snapshot.csv",
        positions_path=tmp_path / "positions.json",
        log_path=tmp_path / "log.csv",
    )


def _position(**overrides) -> Position:
    levels = build_levels(Decimal(100), Decimal(104), 5)
    defaults = dict(
        symbol="EUR_USD", strategy="flat", levels=levels,
        open_qty=[Decimal(1), Decimal(0), Decimal(0), Decimal(0), Decimal(0)],
        realized=Decimal(0), fees=Decimal(0), trades=1, opened_at="2026-01-01 00:00:00 UTC",
        entry_price=Decimal(100), take_profit_usd=Decimal(100000), stop_loss_usd=Decimal(100000),
        last_price=Decimal(101),
    )
    defaults.update(overrides)
    return Position(**defaults)


def test_manage_position_execute_real_orders_false_matches_current_behavior(tmp_path, monkeypatch):
    """A single price tick to 101 (matching test_grid_math.py's own
    sell-into-open-buy-level scenario) sells the level-0 position into
    101, then immediately re-buys at 101 - 2 fills, no real orders."""
    monkeypatch.setattr(bot, "fetch_current_price", lambda symbol: "101")

    def _fail_if_called(*a, **k):
        raise AssertionError("place_market_order must not be called when execute_real_orders is False")
    monkeypatch.setattr(bot, "place_market_order", _fail_if_called)

    pos = _position()
    config = _config(tmp_path, execute_real_orders=False)
    keep = bot.manage_position(pos, config, instruments={})

    assert keep is True
    assert pos.open_qty[0] == Decimal(0)  # sold
    assert pos.open_qty[1] > 0  # rebought at the same level it sold into
    assert pos.realized == Decimal(1)

    log_text = config.log_path.read_text()
    assert "FILL_SELL" in log_text
    assert "FILL_BUY" in log_text
    assert "_REAL" not in log_text


def test_manage_position_rolls_back_trailing_fills_on_real_order_failure(tmp_path, monkeypatch):
    """Same sell-then-rebuy scenario, but with real orders on: the sell
    succeeds for real, the rebuy fails - the rebuy must be rolled back
    (level 1 stays empty), while the successful sell's effect stands."""
    monkeypatch.setattr(bot, "fetch_current_price", lambda symbol: "101")

    calls = []

    def _fake_place_market_order(instrument, units):
        calls.append(units)
        if len(calls) == 1:
            return OrderResult(True, filled_units=Decimal(1), fill_price=Decimal(101),
                                reject_reason=None, raw_response=None, error=None)
        return OrderResult(False, None, None, "INSUFFICIENT_MARGIN", None, None)

    monkeypatch.setattr(bot, "place_market_order", _fake_place_market_order)

    pos = _position()
    config = _config(tmp_path, execute_real_orders=True)
    info = InstrumentInfo(
        name="EUR_USD", display_name="EUR/USD", pip_location=-4, margin_rate=Decimal("0.02"),
        trade_units_precision=0, minimum_trade_size=Decimal(1),
    )
    keep = bot.manage_position(pos, config, instruments={"EUR_USD": info})

    assert keep is True
    assert len(calls) == 2  # both fills attempted; execution stopped after the 2nd failed
    assert pos.open_qty[0] == Decimal(0)  # real sell succeeded, stands
    assert pos.open_qty[1] == Decimal(0)  # real rebuy failed, rolled back (not left dangling)
    assert pos.realized == Decimal(1)  # the successful sell's profit stands

    log_text = config.log_path.read_text()
    assert "FILL_SELL_REAL" in log_text
    assert "WARN_ORDEN_FALLIDA" in log_text
    assert "FILL_BUY_REAL" not in log_text
    # default _position() has no trade_ids override -> __post_init__
    # defaults them to [None]*5, so this exercises the FIFO-fallback
    # path, not close_trade - document that explicitly.
    assert "WARN_SIN_TRADE_ID" in log_text


def test_close_path_keeps_position_when_broker_position_unconfirmed(tmp_path, monkeypatch):
    """No price movement (no new fills) - realized is pre-set above
    take_profit so the close path triggers immediately. If OANDA's own
    position can't be confirmed, the local position must NOT be
    dropped - a still-real position must never be silently forgotten."""
    monkeypatch.setattr(bot, "fetch_current_price", lambda symbol: "102.5")
    monkeypatch.setattr(bot, "fetch_open_position", lambda instrument: None)

    def _fail_if_called(*a, **k):
        raise AssertionError("place_market_order must not be called if broker position can't be confirmed")
    monkeypatch.setattr(bot, "place_market_order", _fail_if_called)

    pos = _position(
        open_qty=[Decimal(0)] * 5, last_price=Decimal("102.5"),
        realized=Decimal(500), take_profit_usd=Decimal(100),
    )
    config = _config(tmp_path, execute_real_orders=True)
    keep = bot.manage_position(pos, config, instruments={})

    assert keep is True  # kept, not dropped
    log_text = config.log_path.read_text()
    assert "WARN_CIERRE_SIN_CONFIRMAR" in log_text
    assert "CLOSE_REAL" not in log_text
    assert "\nEUR_USD,flat,CLOSE," not in log_text  # the final CLOSE row never gets written


def test_close_path_flattens_and_drops_when_broker_confirms_position(tmp_path, monkeypatch):
    """Broker reports a real net position still open - it must be
    flattened with one real order before the local position is dropped."""
    monkeypatch.setattr(bot, "fetch_current_price", lambda symbol: "102.5")
    monkeypatch.setattr(bot, "fetch_open_position", lambda instrument: Decimal(7))

    flatten_calls = []

    def _fake_place_market_order(instrument, units):
        flatten_calls.append(units)
        return OrderResult(True, filled_units=-Decimal(7), fill_price=Decimal("102.5"),
                            reject_reason=None, raw_response=None, error=None)

    monkeypatch.setattr(bot, "place_market_order", _fake_place_market_order)

    pos = _position(
        open_qty=[Decimal(0)] * 5, last_price=Decimal("102.5"),
        realized=Decimal(500), take_profit_usd=Decimal(100),
    )
    config = _config(tmp_path, execute_real_orders=True)
    keep = bot.manage_position(pos, config, instruments={})

    assert keep is False
    assert flatten_calls == [Decimal(-7)]  # flattened the opposite of the broker's reported 7
    log_text = config.log_path.read_text()
    assert "CLOSE_REAL" in log_text


def test_sell_with_known_trade_id_closes_by_id_and_corrects_realized(tmp_path, monkeypatch):
    """Same sell-then-rebuy scenario, but with a trade_id on record for
    level 0: the sell must go through close_trade (not a generic sell),
    and the real realized_pl (0.5) must REPLACE the theoretical value
    (1, from grid_math) in pos.realized - the actual fix for the
    original bug (our logged profit didn't match the real account)."""
    monkeypatch.setattr(bot, "fetch_current_price", lambda symbol: "101")

    close_calls = []

    def fake_close_trade(trade_id):
        close_calls.append(trade_id)
        return OrderResult(True, filled_units=Decimal(-1), fill_price=Decimal(101),
                            reject_reason=None, raw_response=None, error=None,
                            trade_id=None, realized_pl=Decimal("0.5"))

    buy_calls = []

    def fake_place_market_order(instrument, units):
        buy_calls.append(units)
        return OrderResult(True, filled_units=Decimal(1), fill_price=Decimal(101),
                            reject_reason=None, raw_response=None, error=None,
                            trade_id="9999", realized_pl=Decimal(0))

    monkeypatch.setattr(bot, "close_trade", fake_close_trade)
    monkeypatch.setattr(bot, "place_market_order", fake_place_market_order)

    pos = _position(trade_ids=["7001", None, None, None, None])
    config = _config(tmp_path, execute_real_orders=True)
    info = InstrumentInfo(
        name="EUR_USD", display_name="EUR/USD", pip_location=-4, margin_rate=Decimal("0.02"),
        trade_units_precision=0, minimum_trade_size=Decimal(1),
    )
    keep = bot.manage_position(pos, config, instruments={"EUR_USD": info})

    assert keep is True
    assert close_calls == ["7001"]  # closed the SPECIFIC trade, not a generic FIFO sell
    assert len(buy_calls) == 1  # the rebuy at level 1 still goes through place_market_order
    assert pos.trade_ids[0] is None  # cleared after the close
    assert pos.trade_ids[1] == "9999"  # recorded from the rebuy
    assert pos.realized == Decimal("0.5")  # real pl (0.5), not the theoretical value (1)

    log_text = config.log_path.read_text()
    assert "WARN_SIN_TRADE_ID" not in log_text  # trade_id was known - no fallback needed


def test_trade_doesnt_exist_on_close_does_not_rollback_and_continues_batch(tmp_path, monkeypatch):
    """The broker saying a trade is already gone must NOT be treated
    like an ordinary order failure - rolling back would make local
    bookkeeping claim an open position that provably isn't there. The
    fill stays as process_bar already left it, and the batch continues
    to the next fill instead of aborting."""
    monkeypatch.setattr(bot, "fetch_current_price", lambda symbol: "101")
    monkeypatch.setattr(
        bot, "close_trade",
        lambda trade_id: OrderResult(False, None, None, "TRADE_DOESNT_EXIST", None, error=None),
    )

    buy_calls = []

    def fake_place_market_order(instrument, units):
        buy_calls.append(units)
        return OrderResult(True, filled_units=Decimal(1), fill_price=Decimal(101),
                            reject_reason=None, raw_response=None, error=None,
                            trade_id="9999", realized_pl=Decimal(0))

    monkeypatch.setattr(bot, "place_market_order", fake_place_market_order)

    pos = _position(trade_ids=["7001", None, None, None, None])
    config = _config(tmp_path, execute_real_orders=True)
    info = InstrumentInfo(
        name="EUR_USD", display_name="EUR/USD", pip_location=-4, margin_rate=Decimal("0.02"),
        trade_units_precision=0, minimum_trade_size=Decimal(1),
    )
    keep = bot.manage_position(pos, config, instruments={"EUR_USD": info})

    assert keep is True
    assert pos.open_qty[0] == Decimal(0)  # left as process_bar set it - NOT rolled back
    assert pos.trade_ids[0] is None  # cleared even though the close itself failed
    assert len(buy_calls) == 1  # batch continued past the failure - the rebuy still happened
    assert pos.open_qty[1] > 0
    assert pos.realized == Decimal(1)  # uncorrected (no real pl available) - theoretical value stands

    log_text = config.log_path.read_text()
    assert "WARN_TRADE_YA_CERRADO" in log_text
    assert "WARN_ORDEN_FALLIDA" not in log_text


def test_close_path_keeps_position_when_flatten_order_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "fetch_current_price", lambda symbol: "102.5")
    monkeypatch.setattr(bot, "fetch_open_position", lambda instrument: Decimal(7))
    monkeypatch.setattr(
        bot, "place_market_order",
        lambda instrument, units: OrderResult(False, None, None, "INSUFFICIENT_MARGIN", None, None),
    )

    pos = _position(
        open_qty=[Decimal(0)] * 5, last_price=Decimal("102.5"),
        realized=Decimal(500), take_profit_usd=Decimal(100),
    )
    config = _config(tmp_path, execute_real_orders=True)
    keep = bot.manage_position(pos, config, instruments={})

    assert keep is True  # flatten failed - do not drop a still-real position
    log_text = config.log_path.read_text()
    assert "WARN_CIERRE_SIN_CONFIRMAR" in log_text
