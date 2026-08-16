"""Futures-specific risk data - funding rate, open interest, long/short
positioning, book spread. None of this existed in the original
grid_screener (which only looked at price/volume) - added 2026-08-16
for the "Screen 2" market-quality pass on a short list of already
marketplace-picked bots (see atlantis/grid_screener/deep_dive.py).

All confirmed against real responses 2026-08-16 before writing this -
none of these shapes were guessed.
"""

from __future__ import annotations

from decimal import Decimal

from atlantis.grid_screener.binance_client import _get


def fetch_funding_rate_pct(symbol: str) -> Decimal | None:
    data = _get(f"/fapi/v1/premiumIndex?symbol={symbol}")
    if not isinstance(data, dict) or "lastFundingRate" not in data:
        return None
    return Decimal(data["lastFundingRate"]) * 100


def fetch_open_interest_change_pct(symbol: str, hours: int = 24) -> Decimal | None:
    """% change in open interest over the last `hours`, using hourly
    snapshots - a big recent swing (either direction) means leveraged
    positioning is being built or unwound fast, which is exactly the
    kind of setup that can blow through a grid's range."""
    data = _get(f"/futures/data/openInterestHist?symbol={symbol}&period=1h&limit={hours + 1}")
    if not data or not isinstance(data, list) or len(data) < 2:
        return None
    first = Decimal(data[0]["sumOpenInterest"])
    last = Decimal(data[-1]["sumOpenInterest"])
    if first == 0:
        return None
    return (last - first) / first * 100


def fetch_long_short_ratio(symbol: str) -> Decimal | None:
    """Global account long/short ratio - >1 means more accounts net
    long than short. Far from 1 in either direction is a "crowded
    trade" signal (squeeze/reversal risk), not a directional
    prediction on its own."""
    data = _get(f"/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=1h&limit=1")
    if not data or not isinstance(data, list):
        return None
    try:
        return Decimal(data[-1]["longShortRatio"])
    except (KeyError, IndexError, TypeError):
        return None


def fetch_spread_pct(symbol: str) -> Decimal | None:
    data = _get(f"/fapi/v1/depth?symbol={symbol}&limit=5")
    if not isinstance(data, dict) or not data.get("bids") or not data.get("asks"):
        return None
    best_bid = Decimal(data["bids"][0][0])
    best_ask = Decimal(data["asks"][0][0])
    mid = (best_bid + best_ask) / 2
    if mid == 0:
        return None
    return (best_ask - best_bid) / mid * 100
