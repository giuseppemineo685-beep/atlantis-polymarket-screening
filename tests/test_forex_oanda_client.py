from atlantis.forex.oanda_client import candles_to_klines


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
