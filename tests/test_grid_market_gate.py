from atlantis.grid_trader.market_gate import btc_market_ok


def _kline(close: float, i: int) -> list:
    # Binance kline shape: [open_time, open, high, low, close, volume, close_time, ...]
    return [i, str(close), str(close), str(close), str(close), "1", i]


def test_blocks_on_strong_sustained_downtrend():
    # Straight-line decline from 100 to 80 over 14 days - efficiency
    # ratio should be near 1.0 (net move == total path), well past the
    # "fuerte" cutoff, net negative.
    klines = [_kline(100 - i, i) for i in range(15)]
    ok, reason = btc_market_ok(klines)
    assert ok is False
    assert "bajista" in reason


def test_allows_on_sustained_uptrend():
    klines = [_kline(80 + i, i) for i in range(15)]
    ok, reason = btc_market_ok(klines)
    assert ok is True


def test_allows_on_choppy_rangebound_data():
    # oscillates, no persistent direction
    prices = [100, 102, 99, 101, 100, 103, 98, 101, 100, 102, 99, 101, 100, 102, 100]
    klines = [_kline(p, i) for i, p in enumerate(prices)]
    ok, reason = btc_market_ok(klines)
    assert ok is True


def test_allows_when_insufficient_history():
    klines = [_kline(100, i) for i in range(3)]
    ok, reason = btc_market_ok(klines)
    assert ok is True
    assert "suficiente" in reason
