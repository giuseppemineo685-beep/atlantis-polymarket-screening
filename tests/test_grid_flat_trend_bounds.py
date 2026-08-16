from decimal import Decimal

from atlantis.grid_trader.flat import RANGE_WINDOW_DAYS as FLAT_WINDOW
from atlantis.grid_trader.flat import compute_flat_grid_bounds
from atlantis.grid_trader.trend import BUY_SIDE_FRACTION, compute_trend_grid_bounds


def _kline(i: int, low: float, high: float, close: float) -> list:
    return [i, str(close), str(high), str(low), str(close), "1", i]


def test_flat_bounds_use_high_low_of_last_window():
    klines = [_kline(i, low=90 + i, high=110 + i, close=100 + i) for i in range(FLAT_WINDOW + 5)]
    lower, upper = compute_flat_grid_bounds(klines)
    window = klines[-FLAT_WINDOW:]
    expected_lower = min(Decimal(k[3]) for k in window)
    expected_upper = max(Decimal(k[2]) for k in window)
    assert lower == expected_lower
    assert upper == expected_upper


def test_trend_bounds_skew_more_room_below_price():
    klines = [_kline(i, low=50 + i, high=150 - i if i < 15 else 100, close=100) for i in range(20)]
    lower, upper = compute_trend_grid_bounds(klines, "long")
    price = Decimal(str(klines[-1][4]))
    below = price - lower
    above = upper - price
    # BUY_SIDE_FRACTION of the width should sit below price
    assert below > above


def test_trend_bounds_reject_short_direction():
    klines = [_kline(i, low=90, high=110, close=100) for i in range(20)]
    try:
        compute_trend_grid_bounds(klines, "short")
        assert False, "should have raised"
    except ValueError:
        pass
