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


def _post(path: str, body: dict, retries: int = 4) -> dict | None:
    """NOT a mirror of `_get`'s retry behavior on purpose: an order POST
    isn't safely retriable on a timeout/connection error - the order may
    have already reached OANDA, and a blind retry risks a real duplicate
    order (the same class of incident already documented in
    atlantis/polymarket/clob_client.py's own `_parse_order_response`
    docstring). Only 429 (rate limit, request never reached the matching
    engine) is retried. Any other HTTP error still reads and returns
    OANDA's JSON body (usually an orderRejectTransaction with a real
    rejectReason) instead of discarding it like `_get` does."""
    url = f"{_base_url()}{path}"
    payload = json.dumps(body).encode()
    for attempt in range(retries):
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={**_headers(), "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt * 2)
                continue
            try:
                return json.loads(e.read())
            except (ValueError, OSError):
                return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return None
    return None


@dataclass
class InstrumentInfo:
    name: str  # e.g. "EUR_USD"
    display_name: str
    pip_location: int
    margin_rate: Decimal
    trade_units_precision: int  # decimal places allowed for order units, typically 0 for FX
    minimum_trade_size: Decimal


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
            trade_units_precision=int(i["tradeUnitsPrecision"]),
            minimum_trade_size=Decimal(i["minimumTradeSize"]),
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


@dataclass(frozen=True)
class OrderResult:
    success: bool
    filled_units: Decimal | None  # signed, actual units OANDA filled
    fill_price: Decimal | None
    reject_reason: str | None
    raw_response: dict | None
    error: str | None


def round_units(units: Decimal, precision: int, minimum_trade_size: Decimal) -> Decimal:
    """Rounds a theoretical unit quantity to what OANDA will actually
    accept. A nonzero quantity is never allowed to round down to literal
    zero (that would make real orders silently impossible on any pair
    priced above usd_per_level, e.g. JPY-quoted pairs) - it's floored up
    to `minimum_trade_size` instead, sign preserved. Zero stays zero -
    this never invents a trade."""
    if units == 0:
        return Decimal(0)
    quantum = Decimal(1).scaleb(-precision) if precision > 0 else Decimal(1)
    rounded = units.quantize(quantum)
    sign = Decimal(1) if units > 0 else Decimal(-1)
    if abs(rounded) < minimum_trade_size:
        return sign * minimum_trade_size
    return rounded


def _parse_order_response(data: dict | None) -> OrderResult:
    """Never raises, and never returns success=True on ambiguous or
    malformed input - an unclear result must read as a failure, not be
    guessed into a success (same principle as the Polymarket client's
    own _parse_order_response)."""
    if not isinstance(data, dict):
        return OrderResult(False, None, None, None, None, error="respuesta_invalida")
    fill = data.get("orderFillTransaction")
    if isinstance(fill, dict) and "price" in fill and "units" in fill:
        try:
            return OrderResult(
                True, filled_units=Decimal(fill["units"]), fill_price=Decimal(fill["price"]),
                reject_reason=None, raw_response=data, error=None,
            )
        except (ArithmeticError, ValueError, TypeError):
            return OrderResult(False, None, None, None, data, error="fill_no_parseable")
    reject = data.get("orderRejectTransaction") or data.get("orderCancelTransaction")
    if isinstance(reject, dict):
        reason = reject.get("rejectReason") or reject.get("reason") or "desconocido"
        return OrderResult(False, None, None, reject_reason=reason, raw_response=data, error=None)
    return OrderResult(False, None, None, None, data, error="sin_fill_ni_reject_en_la_respuesta")


def place_market_order(instrument: str, units: Decimal) -> OrderResult:
    """Submits a REAL market order - practice (demo) account only, never
    live. `units` must already be rounded by the caller via round_units
    using that instrument's real precision/minimum; this function stays
    mechanical, like fetch_candles."""
    env = os.getenv("OANDA_ENV", "practice")
    if env != "practice":
        return OrderResult(False, None, None, None, None, error=f"bloqueado: OANDA_ENV={env}, solo 'practice' permitido")
    account_id = os.getenv("OANDA_ACCOUNT_ID")
    if not account_id:
        raise RuntimeError("OANDA_ACCOUNT_ID no esta seteado - falta source .env.forex")
    body = {
        "order": {
            "type": "MARKET", "instrument": instrument, "units": str(units),
            "timeInForce": "FOK", "positionFill": "DEFAULT",
        }
    }
    data = _post(f"/v3/accounts/{account_id}/orders", body)
    return _parse_order_response(data)


def fetch_open_position(instrument: str) -> Decimal | None:
    """Net units currently held for `instrument` per OANDA itself - the
    broker's own number, not local bookkeeping. Returns None (never 0)
    if the fetch/parse fails so callers can tell "confirmed flat" apart
    from "couldn't confirm"."""
    account_id = os.getenv("OANDA_ACCOUNT_ID")
    if not account_id:
        raise RuntimeError("OANDA_ACCOUNT_ID no esta seteado - falta source .env.forex")
    data = _get(f"/v3/accounts/{account_id}/positions/{instrument}")
    if not data or "position" not in data:
        return None
    pos = data["position"]
    try:
        long_units = Decimal(pos["long"]["units"])
        short_units = Decimal(pos["short"]["units"])
    except (KeyError, ArithmeticError, ValueError, TypeError):
        return None
    return long_units + short_units  # short units are already negative in OANDA's own convention
