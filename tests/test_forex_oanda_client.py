from decimal import Decimal

from atlantis.forex.oanda_client import OrderResult, _parse_order_response, candles_to_klines, round_units


def test_candles_to_klines_shape_matches_binance_convention():
    candles = [
        {"complete": True, "volume": 1000, "time": "1700000000.000000000",
         "mid": {"o": "1.1000", "h": "1.1050", "l": "1.0980", "c": "1.1020"}},
    ]
    klines = candles_to_klines(candles)
    assert len(klines) == 1
    k = klines[0]
    assert k[0] == 1700000000000  # open_time_ms
    assert k[1] == "1.1000"  # open
    assert k[2] == "1.1050"  # high
    assert k[3] == "1.0980"  # low
    assert k[4] == "1.1020"  # close
    assert k[5] == "1000"  # volume
    assert k[6] == 1700000000000  # close_time_ms


def test_candles_to_klines_skips_incomplete_candle():
    candles = [
        {"complete": True, "volume": 1000, "time": "1700000000.000000000",
         "mid": {"o": "1.1", "h": "1.1", "l": "1.1", "c": "1.1"}},
        {"complete": False, "volume": 5, "time": "1700086400.000000000",
         "mid": {"o": "1.2", "h": "1.2", "l": "1.2", "c": "1.2"}},
    ]
    klines = candles_to_klines(candles)
    assert len(klines) == 1
    assert klines[0][0] == 1700000000000


def test_candles_to_klines_empty_list():
    assert candles_to_klines([]) == []


def test_round_units_normal_rounding_above_minimum():
    # EUR_USD-style: precision 0, minimum 1 - well above the minimum
    assert round_units(Decimal("19.4"), precision=0, minimum_trade_size=Decimal(1)) == Decimal(19)
    assert round_units(Decimal("-19.6"), precision=0, minimum_trade_size=Decimal(1)) == Decimal(-20)


def test_round_units_floors_nonzero_to_minimum_not_zero():
    # USD_JPY-style: theoretical qty rounds to 0 whole units - must floor
    # up to the minimum instead of silently making the order impossible.
    assert round_units(Decimal("0.13"), precision=0, minimum_trade_size=Decimal(1)) == Decimal(1)
    assert round_units(Decimal("-0.13"), precision=0, minimum_trade_size=Decimal(1)) == Decimal(-1)


def test_round_units_zero_stays_zero():
    assert round_units(Decimal(0), precision=0, minimum_trade_size=Decimal(1)) == Decimal(0)


def test_parse_order_response_success_fill():
    data = {
        "orderFillTransaction": {"price": "1.13027", "units": "19", "tradeOpened": {"tradeID": "6368", "units": "19"}},
        "lastTransactionID": "6368",
    }
    result = _parse_order_response(data)
    assert result.success is True
    assert result.filled_units == Decimal(19)
    assert result.fill_price == Decimal("1.13027")
    assert result.reject_reason is None


def test_parse_order_response_reject():
    data = {"orderRejectTransaction": {"rejectReason": "INSUFFICIENT_MARGIN"}}
    result = _parse_order_response(data)
    assert result.success is False
    assert result.reject_reason == "INSUFFICIENT_MARGIN"
    assert result.filled_units is None


def test_parse_order_response_malformed_input_never_succeeds():
    assert _parse_order_response(None).success is False
    assert _parse_order_response({}).success is False
    assert _parse_order_response({"orderFillTransaction": {"units": "19"}}).success is False  # missing price
    assert _parse_order_response("not a dict").success is False
