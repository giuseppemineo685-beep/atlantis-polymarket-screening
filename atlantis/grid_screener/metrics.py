"""Turns raw Binance daily klines into the same fields the manual
screener asks a human to eyeball (volatilidad %, regimen, posicion en
rango %, liquidez, correlacion con BTC). These are transparent,
well-known technical measures, not a fitted/backtested trading signal -
this tool is decision support ("tu criterio, sistematizado"), so the
bar here is "a reasonable, named, inspectable metric" rather than the
walk-forward validation the actual trading bots require.

Kline shape (Binance klines endpoint): index 2=high, 3=low, 4=close.
"""

from __future__ import annotations

import math
import statistics
import time

VOL_WINDOW_DAYS = 14  # recent-regime window for volatility/trend
RANGE_WINDOW_DAYS = 30  # wider window for "what range would you actually set the grid to"
# Correlation needs its own, longer window: a Pearson r over only 14
# daily points has a standard error of ~0.29 (1/sqrt(n-3)) - too noisy
# to bucket alto/medio/bajo. Confirmed 2026-08-16 on the real universe:
# a 14-day window put 74% of pairs in "bajo" (implausibly low, mostly
# noise), 60 days is a real improvement in stability while still being
# short enough to reflect the current regime, not all-time beta.
CORR_WINDOW_DAYS = 60

# Liquidity buckets, calibrated 2026-08-16 from the real 24h quoteVolume
# distribution across Binance USDS-M perpetuals (rank 50 ~$36M, rank 150
# ~$6.8M, rank 250 ~$2.7M): "alto" roughly top-50-ish, "bajo" is still
# above the $2M universe floor (run_grid_screener.py drops anything
# thinner than that before it's even scored) but thin enough to flag.
LIQ_ALTO_USD = 15_000_000
LIQ_MEDIO_USD = 4_000_000

# Correlation buckets - standard "strong/moderate/weak" cutoffs for a
# Pearson r of daily log returns.
CORR_ALTO = 0.6
CORR_MEDIO = 0.3

# Efficiency Ratio (Kaufman) cutoffs for regime classification: ER is
# |net move| / sum(|daily moves|) over the window, 0 (pure chop) to 1
# (moves in a straight line every day).
ER_LEVE = 0.25
ER_FUERTE = 0.5


def _closes(klines: list) -> list[float]:
    return [float(k[4]) for k in klines]


def daily_volatility_pct(klines: list) -> float:
    """Mean daily (high-low)/close, as a percent - an ATR%-style
    measure, matching what the manual tool's own guide text asks for
    ("rango medio del dia (o ATR% diario)")."""
    window = klines[-VOL_WINDOW_DAYS:]
    vals = [
        (float(k[2]) - float(k[3])) / float(k[4]) * 100
        for k in window
        if float(k[4]) > 0
    ]
    return statistics.fmean(vals) if vals else 0.0


def efficiency_ratio(klines: list) -> float:
    closes = _closes(klines[-VOL_WINDOW_DAYS:])
    if len(closes) < 2:
        return 0.0
    net = abs(closes[-1] - closes[0])
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    return net / path if path > 0 else 0.0


def net_move_pct(klines: list) -> float:
    """Signed % move over the same window efficiency_ratio uses - tells
    a trend candidate's DIRECTION (ER alone is unsigned, just "how
    straight was the path")."""
    closes = _closes(klines[-VOL_WINDOW_DAYS:])
    if len(closes) < 2 or closes[0] == 0:
        return 0.0
    return (closes[-1] - closes[0]) / closes[0] * 100


def regimen_from_er(er: float) -> str:
    if er < ER_LEVE:
        return "rango"
    if er < ER_FUERTE:
        return "leve"
    return "fuerte"


def position_in_range_pct(klines: list) -> float:
    window = klines[-RANGE_WINDOW_DAYS:]
    highs = [float(k[2]) for k in window]
    lows = [float(k[3]) for k in window]
    close = float(window[-1][4])
    hi, lo = max(highs), min(lows)
    if hi <= lo:
        return 50.0
    return (close - lo) / (hi - lo) * 100


def bollinger_band_width_pct(klines: list, window: int = 20) -> float:
    """(upper - lower) / middle * 100 using a `window`-day SMA/stdev of
    daily closes - a rising width means volatility is EXPANDING (the
    market is waking up, possibly about to break its range), not just
    "how volatile has it been" the way daily_volatility_pct measures."""
    closes = _closes(klines[-window:])
    if len(closes) < window:
        return 0.0
    mean = statistics.fmean(closes)
    if mean == 0:
        return 0.0
    stdev = statistics.pstdev(closes)
    upper, lower = mean + 2 * stdev, mean - 2 * stdev
    return (upper - lower) / mean * 100


def volume_change_pct(klines: list) -> float:
    """Last COMPLETE day's volume vs. the mean of the 7 complete days
    before it - a spike here often precedes (or confirms) a real range
    break, not just noise. Confirmed 2026-08-16 this must drop today's
    still-forming candle first: volume is a flow measure that scales
    with elapsed time, so comparing a partial day (e.g. 11h in) against
    full-day averages always looks like a ~95% collapse regardless of
    real activity - every symbol showed that same fake drop until this
    was added."""
    complete = klines
    now_ms = int(time.time() * 1000)
    if complete and complete[-1][6] > now_ms:  # close_time still in the future
        complete = complete[:-1]

    window = complete[-8:]
    if len(window) < 8:
        return 0.0
    prior = [float(k[5]) for k in window[:-1]]
    latest = float(window[-1][5])
    baseline = statistics.fmean(prior)
    if baseline == 0:
        return 0.0
    return (latest - baseline) / baseline * 100


def liquidez_bucket(quote_volume_24h: float) -> str:
    if quote_volume_24h >= LIQ_ALTO_USD:
        return "alto"
    if quote_volume_24h >= LIQ_MEDIO_USD:
        return "medio"
    return "bajo"


def daily_log_returns(klines: list, window_days: int = CORR_WINDOW_DAYS) -> list[float]:
    closes = _closes(klines[-(window_days + 1):])
    out = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            out.append(math.log(closes[i] / closes[i - 1]))
    return out


def pearson(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    a, b = a[-n:], b[-n:]
    try:
        return statistics.correlation(a, b)
    except statistics.StatisticsError:
        return 0.0


def correlacion_bucket(corr: float) -> str:
    c = abs(corr)
    if c >= CORR_ALTO:
        return "alto"
    if c >= CORR_MEDIO:
        return "medio"
    return "bajo"
