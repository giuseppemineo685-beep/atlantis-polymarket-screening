"""Thin client for Binance USDS-M Futures public market data - no API
key needed, these are all public endpoints. Binance itself is NOT
blocked on the owner's network (unlike Polymarket, see the
atlantis-vps-ssh-access / polymarket-blocked-in-switzerland memory) but
this still runs on the VPS via cron so the screener stays fresh even
when the owner's machine is off."""

from __future__ import annotations

import time
import urllib.error
import urllib.request
import json
from dataclasses import dataclass

BASE = "https://fapi.binance.com"
HEADERS = {"User-Agent": "Mozilla/5.0"}

# Universe floor: below this 24h quoteVolume, a pair already scores
# "bajo" liquidez and gets flagged - excluding it up front just saves
# API weight on the daily-klines fetch, calibrated 2026-08-16 (see
# metrics.py's own liquidity-bucket comment for the real distribution).
MIN_QUOTE_VOLUME_USD = 2_000_000


@dataclass
class TickerInfo:
    symbol: str
    quote_volume_24h: float


def _get(path: str, retries: int = 4):
    url = f"{BASE}{path}"
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                time.sleep(2 ** attempt * 2)
                continue
            # A single bad/delisted symbol (400) shouldn't take down a
            # 250+-symbol batch run - skip it, the caller treats None
            # the same as "couldn't fetch this one, move on".
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            time.sleep(1 + attempt)
    return None


def fetch_screenable_universe() -> list[TickerInfo]:
    """USDS-M perpetuals, USDT-margined, currently trading, above the
    liquidity floor - sorted by 24h quoteVolume descending."""
    info = _get("/fapi/v1/exchangeInfo")
    if info is None:
        return []
    perpetual_usdt = {
        s["symbol"]
        for s in info["symbols"]
        if s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT" and s["status"] == "TRADING"
    }

    ticker = _get("/fapi/v1/ticker/24hr")
    if ticker is None:
        return []

    out = [
        TickerInfo(symbol=t["symbol"], quote_volume_24h=float(t["quoteVolume"]))
        for t in ticker
        if t["symbol"] in perpetual_usdt and float(t["quoteVolume"]) >= MIN_QUOTE_VOLUME_USD
    ]
    out.sort(key=lambda t: t.quote_volume_24h, reverse=True)
    return out


def fetch_daily_klines(symbol: str, days: int = 65) -> list | None:
    data = _get(f"/fapi/v1/klines?symbol={symbol}&interval=1d&limit={days}")
    if not data or not isinstance(data, list):
        return None
    return data


def fetch_current_price(symbol: str) -> str | None:
    """Last traded price (not book bid/ask - grid_trader's paper fills
    assume this quote is achievable, same simplification the backtest
    already makes with OHLC bars)."""
    data = _get(f"/fapi/v1/ticker/price?symbol={symbol}")
    if not isinstance(data, dict) or "price" not in data:
        return None
    return data["price"]


def fetch_klines_range(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Paginated fetch for any interval/date range - single-symbol
    calls only (grid_trader backtests one symbol at a time, unlike the
    universe-wide screener), so 1000-candle pages are cheap here."""
    out: list = []
    cur = start_ms
    while cur < end_ms:
        data = _get(f"/fapi/v1/klines?symbol={symbol}&interval={interval}&startTime={cur}&endTime={end_ms}&limit=1000")
        if not data or not isinstance(data, list):
            break
        out.extend(data)
        if len(data) < 1000:
            break
        cur = data[-1][0] + 1
        time.sleep(0.15)
    return out
