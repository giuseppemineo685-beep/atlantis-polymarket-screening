"""OANDA v20 REST API client - practice (paper) environment only.
Public market-data endpoints (candles, pricing, instrument list) plus
the account-scoped instrument listing (needs the token but no real
capital - a practice account is a free demo, not a funded account).

`candles_to_klines` converts OANDA's candle shape into the SAME
[open_time_ms, open, high, low, close, volume, close_time_ms] list
shape Binance klines use throughout atlantis/grid_screener/metrics.py
and atlantis/grid_trader/ - so ALL of that asset-agnostic math (
efficiency_ratio, regimen_from_er, position_in_range_pct,
daily_volatility_pct, net_move_pct, the grid fill engine, take-profit/
stop-loss, the walk-forward backtest) is reused UNCHANGED for forex,
not reimplemented.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal

ENV_BASES = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}


def _base_url() -> str:
    env = os.getenv("OANDA_ENV", "practice")
    return ENV_BASES[env]


def _headers() -> dict:
    token = os.getenv("OANDA_API_TOKEN")
    if not token:
        raise RuntimeError("OANDA_API_TOKEN no esta seteado - falta source .env.forex")
    return {"Authorization": f"Bearer {token}", "Accept-Datetime-Format": "UNIX"}


def _get(path: str, retries: int = 4):
    url = f"{_base_url()}{path}"
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=_headers())
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt * 2)
                continue
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            time.sleep(1 + attempt)
    return None


@dataclass
class InstrumentInfo:
    name: str  # e.g. "EUR_USD"
    display_name: str
    pip_location: int
    margin_rate: Decimal


def fetch_currency_instruments() -> list[InstrumentInfo]:
    """Real currency pairs only (excludes OANDA's CFD/METAL instrument
    types) - matches what "forex" means in the owner's own request."""
    account_id = os.getenv("OANDA_ACCOUNT_ID")
    if not account_id:
        raise RuntimeError("OANDA_ACCOUNT_ID no esta seteado - falta source .env.forex")
    data = _get(f"/v3/accounts/{account_id}/instruments")
    if not data or "instruments" not in data:
        return []
    return [
        InstrumentInfo(
            name=i["name"], display_name=i["displayName"],
            pip_location=int(i["pipLocation"]), margin_rate=Decimal(i["marginRate"]),
        )
        for i in data["instruments"]
        if i["type"] == "CURRENCY"
    ]


def fetch_candles(instrument: str, granularity: str, count: int | None = None,
                   from_ts: int | None = None, to_ts: int | None = None) -> list | None:
    """`granularity`: OANDA codes, e.g. 'D' (daily), 'H1' (hourly),
    'M1' (1-minute). Either pass `count` (most recent N candles) or a
    [from_ts, to_ts] unix-second range, not both (matches the OANDA API
    itself - mixing them is invalid there too)."""
    params = [f"granularity={granularity}", "price=M"]  # M = midpoint pricing
    if count is not None:
        params.append(f"count={count}")
    else:
        if from_ts is not None:
            params.append(f"from={from_ts}")
        if to_ts is not None:
            params.append(f"to={to_ts}")
    data = _get(f"/v3/instruments/{instrument}/candles?{'&'.join(params)}")
    if not data or "candles" not in data:
        return None
    return data["candles"]


def candles_to_klines(candles: list) -> list:
    """OANDA candle -> Binance-kline-shaped list. Volume here is tick
    count (OANDA doesn't report notional volume), still directionally
    useful for the volume_change_pct check even if not $-comparable
    across instruments the way liquidez_bucket's crypto thresholds are -
    see atlantis/forex/metrics.py for why liquidity is handled
    differently for forex."""
    klines = []
    for c in candles:
        if not c.get("complete", True):
            continue  # skip the still-forming current candle, same as live bot's own convention
        ts_ms = int(float(c["time"]) * 1000)
        mid = c["mid"]
        klines.append([ts_ms, mid["o"], mid["h"], mid["l"], mid["c"], str(c.get("volume", 0)), ts_ms])
    return klines


def fetch_current_price(instrument: str) -> str | None:
    """Latest candle close (mid) as a proxy for current price - same
    simplification the crypto bot's fetch_current_price makes (last
    traded/mid price, not a live bid/ask spread quote)."""
    candles = fetch_candles(instrument, granularity="M1", count=2)
    if not candles:
        return None
    complete = [c for c in candles if c.get("complete")]
    latest = complete[-1] if complete else candles[-1]
    return latest["mid"]["c"]
