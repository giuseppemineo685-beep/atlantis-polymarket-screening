"""BTC 'Up or Down 5m' paper-trading experiment - 4 independent entry
strategies (A/B/C/D) tested against the same price samples. Paper only,
$1/signal, deliberately standalone: no import of live_intents or anything
from the live-execution path - this vertical has no wallets to vet and
nothing here should ever be able to touch the real-money order queue
(same isolation rule scripts/run_screening_and_notify_esports.py uses).

Unlike the wallet-copying verticals, a window opens and resolves within
the same script invocation - by the time a 5-minute window closes we
already hold the final spot-vs-target sample, so there's no cross-cycle
"still open" state to track, and no state/notified_signals-style dedupe
file (every window is an inherently new one-shot event).

Invoked every minute by cron (wrapped in `flock -n` at the crontab level,
same pattern as run_live_execution.py). Most invocations are a near-
instant no-op: crontab can only express 1-minute granularity, but the
strategies need price samples every few seconds in the final ~60s before
each 5-minute boundary, so this script checks how close the next boundary
is and only does real work when within reach of it - looping internally
with time.sleep() until the boundary passes, then evaluating and exiting.

UNVERIFIED AS OF 2026-08-03 (no network access during initial writing):
`find_current_btc5m_market()` and `extract_strike_price()` below are a
best-effort guess at Polymarket's gamma-api shape for these recurring
markets, never confirmed against a live example. Confirm both against a
real market (see docs/ or the plan doc) before trusting this in
production - see the inline TODOs.
"""

from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRADE_LOG_PATH = ROOT / "outputs" / "trade_log_btc5m.csv"

TRADE_LOG_FIELDS = [
    "window_slug",
    "strategy",
    "direction",
    "entry_price",
    "target_price",
    "spot_price_at_entry",
    "spot_price_at_close",
    "stake_usd",
    "status",
    "pct_return",
    "date_opened",
    "date_closed",
]

STAKE_USD = Decimal("1")
WINDOW_SECONDS = 5 * 60
LOOKAHEAD_SECONDS = 65  # start polling once within this many seconds of a close
SAMPLE_INTERVAL_SECONDS = 4
SPOT_PRICE_URL = "https://api.coinbase.com/v2/prices/BTC-USD/spot"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


@dataclass(frozen=True)
class Sample:
    seconds_to_close: float
    spot_price: Decimal | None
    target_price: Decimal | None
    up_quote: Decimal | None
    down_quote: Decimal | None


@dataclass(frozen=True)
class MarketInfo:
    condition_id: str
    slug: str
    up_token_id: str
    down_token_id: str
    target_price: Decimal | None


def seconds_to_next_boundary(now: datetime | None = None) -> float:
    """Seconds until the next 5-minute-aligned UTC boundary (:00, :05, ...).

    TODO: confirm these windows are actually UTC-aligned, not ET-aligned -
    the owner's screenshot showed "Aug 3, 5:05-5:10PM ET" as the display
    label, which doesn't by itself confirm the underlying boundary is a
    round 5-minute mark in UTC vs. in US Eastern time. If it's ET-aligned,
    this function needs a timezone shift before use.
    """
    now = now or datetime.now(timezone.utc)
    seconds_into_hour = now.minute * 60 + now.second + now.microsecond / 1e6
    seconds_into_window = seconds_into_hour % WINDOW_SECONDS
    return WINDOW_SECONDS - seconds_into_window


def fetch_spot_price() -> Decimal | None:
    """Independent BTC/USD reference (Coinbase's public, unauthenticated
    spot endpoint) - used as a proxy for whatever internal reference price
    Polymarket resolves these markets against. Public exchange spot prices
    track each other within milliseconds in practice, but this is a proxy,
    not a guaranteed match to Polymarket's own resolution source."""
    req = urllib.request.Request(
        SPOT_PRICE_URL, headers={"Accept": "application/json", "User-Agent": "atlantis-btc5m/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
        return Decimal(str(data["data"]["amount"]))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, InvalidOperation):
        return None


def extract_strike_price(market: dict) -> Decimal | None:
    """UNVERIFIED - guess at where the strike/"price to beat" lives in the
    gamma-api market object. Tries a few plausible field names; returns
    None (never guesses wrong) if none parse, in which case strategies
    that need a target price (A, C) simply won't fire for that window."""
    for key in ("strikePrice", "targetPrice", "priceToBeat", "resolutionPrice"):
        value = market.get(key)
        if value in (None, ""):
            continue
        try:
            return Decimal(str(value))
        except InvalidOperation:
            continue
    return None


def find_current_btc5m_market() -> MarketInfo | None:
    """UNVERIFIED - best-effort discovery of the currently-open "BTC Up or
    Down 5m" market via gamma-api. Tries a couple of plausible tag_slug
    guesses (mirroring atlantis/services/esports_traders.py's category
    discovery pattern) and filters open events by title. Returns None on
    any failure - callers must treat that as "skip this window", never
    fabricate a market."""
    gamma_base = "https://gamma-api.polymarket.com"
    for tag_slug in ("bitcoin", "crypto", "crypto-prices"):
        url = f"{gamma_base}/events?tag_slug={tag_slug}&closed=false&limit=50"
        req = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": "atlantis-btc5m/0.1"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                events = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
        if not isinstance(events, list):
            continue
        for event in events:
            title = str(event.get("title", ""))
            if "up or down" not in title.lower() and "5m" not in title.lower():
                continue
            for market in event.get("markets", []):
                tokens = market.get("clobTokenIds") or market.get("tokens")
                outcomes = market.get("outcomes")
                if not tokens or not outcomes:
                    continue
                try:
                    if isinstance(tokens, str):
                        tokens = json.loads(tokens)
                    if isinstance(outcomes, str):
                        outcomes = json.loads(outcomes)
                except (json.JSONDecodeError, TypeError):
                    continue
                if len(tokens) != 2 or len(outcomes) != 2:
                    continue
                up_idx = 0 if str(outcomes[0]).lower().startswith("up") else 1
                down_idx = 1 - up_idx
                return MarketInfo(
                    condition_id=str(market.get("conditionId", "")),
                    slug=str(market.get("slug", event.get("slug", ""))),
                    up_token_id=str(tokens[up_idx]),
                    down_token_id=str(tokens[down_idx]),
                    target_price=extract_strike_price(market),
                )
    return None


def fetch_clob_quotes(condition_id: str, up_token_id: str, down_token_id: str) -> tuple[Decimal | None, Decimal | None]:
    """Same live-snapshot shape already used 3x elsewhere in this repo
    (live_execution.py::get_market_resolution, generate_dashboard.py::
    fetch_live_price, run_screening_and_notify.py::get_market_price_info) -
    GET clob.polymarket.com/markets/{condition_id} -> {"tokens": [...]}."""
    url = f"https://clob.polymarket.com/markets/{condition_id}"
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "atlantis-btc5m/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            market = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None, None
    if not market:
        return None, None
    up_price = down_price = None
    for token in market.get("tokens", []):
        tid = token.get("token_id")
        try:
            price = Decimal(str(token.get("price"))) if token.get("price") is not None else None
        except InvalidOperation:
            price = None
        if tid == up_token_id:
            up_price = price
        elif tid == down_token_id:
            down_price = price
    return up_price, down_price


# --- Strategies -------------------------------------------------------
# Each takes the full ordered (oldest -> newest) sample list for one
# window and returns (direction, entry_price) if it would have entered,
# or None if its condition never fired. Pure functions, no shared state -
# all 4 run independently over the same sample list every window.

def strategy_a_lag_arbitrage(samples: list[Sample]) -> tuple[str, Decimal] | None:
    candidates = [s for s in samples if 30 <= s.seconds_to_close <= 40]
    if not candidates:
        return None
    s = candidates[-1]
    if s.spot_price is None or s.target_price is None:
        return None
    if s.spot_price > s.target_price and s.up_quote is not None and s.up_quote < Decimal("0.20"):
        return "UP", s.up_quote
    if s.spot_price < s.target_price and s.down_quote is not None and s.down_quote < Decimal("0.20"):
        return "DOWN", s.down_quote
    return None


def strategy_b_momentum(samples: list[Sample]) -> tuple[str, Decimal] | None:
    candidates = [s for s in samples if 30 <= s.seconds_to_close <= 40]
    priced = [s for s in samples if s.spot_price is not None]
    if not candidates or len(priced) < 2:
        return None
    s = candidates[-1]
    if s.spot_price is None:
        return None
    earliest, latest = priced[0], priced[-1]
    if latest.spot_price > earliest.spot_price and s.up_quote is not None:
        return "UP", s.up_quote
    if latest.spot_price < earliest.spot_price and s.down_quote is not None:
        return "DOWN", s.down_quote
    return None


def strategy_c_spike_fade(samples: list[Sample]) -> tuple[str, Decimal] | None:
    candidates = [s for s in samples if s.seconds_to_close <= 15]
    priced = [s.spot_price for s in samples if s.spot_price is not None]
    if not candidates or len(priced) < 3:
        return None
    s = candidates[-1]
    if s.spot_price is None:
        return None
    avg = sum(priced) / len(priced)
    threshold = avg * Decimal("0.0005")  # 5bps - a starting guess, tune once data exists
    deviation = s.spot_price - avg
    if deviation > threshold and s.down_quote is not None:
        return "DOWN", s.down_quote  # spiked up -> bet it fades back down
    if deviation < -threshold and s.up_quote is not None:
        return "UP", s.up_quote  # spiked down -> bet it fades back up
    return None


def strategy_d_cheap_blind(samples: list[Sample]) -> tuple[str, Decimal] | None:
    candidates = [s for s in samples if 20 <= s.seconds_to_close <= 40]
    if not candidates:
        return None
    s = candidates[-1]
    threshold = Decimal("0.15")
    if s.up_quote is not None and s.up_quote < threshold:
        return "UP", s.up_quote
    if s.down_quote is not None and s.down_quote < threshold:
        return "DOWN", s.down_quote
    return None


STRATEGIES = {
    "A_lag_arbitrage": strategy_a_lag_arbitrage,
    "B_momentum": strategy_b_momentum,
    "C_spike_fade": strategy_c_spike_fade,
    "D_cheap_blind": strategy_d_cheap_blind,
}


def append_trade_log_rows(rows: list[dict]) -> None:
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not TRADE_LOG_PATH.exists()
    with TRADE_LOG_PATH.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_LOG_FIELDS)
        if is_new:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def collect_samples(market: MarketInfo) -> list[Sample]:
    samples: list[Sample] = []
    while True:
        seconds_left = seconds_to_next_boundary()
        if seconds_left > WINDOW_SECONDS - 1:
            seconds_left -= WINDOW_SECONDS  # just crossed the boundary
        spot = fetch_spot_price()
        up_q, down_q = fetch_clob_quotes(market.condition_id, market.up_token_id, market.down_token_id)
        samples.append(
            Sample(
                seconds_to_close=seconds_left,
                spot_price=spot,
                target_price=market.target_price,
                up_quote=up_q,
                down_quote=down_q,
            )
        )
        if seconds_left <= 2:
            break
        time.sleep(SAMPLE_INTERVAL_SECONDS)
    return samples


def evaluate_and_log(market: MarketInfo, samples: list[Sample]) -> None:
    priced = [s for s in samples if s.spot_price is not None and s.target_price is not None]
    if not priced:
        print(f"btc5m: no priced samples for {market.slug}, skipping window")
        return
    final = priced[-1]
    winner = "UP" if final.spot_price > final.target_price else "DOWN"
    now = _now()
    rows = []
    for name, fn in STRATEGIES.items():
        result = fn(samples)
        if result is None:
            continue
        direction, entry_price = result
        won = direction == winner
        pct_return = (
            str((Decimal(1) / entry_price - 1) * 100) if won and entry_price > 0 else "-100"
        )
        rows.append(
            {
                "window_slug": market.slug,
                "strategy": name,
                "direction": direction,
                "entry_price": str(entry_price),
                "target_price": str(final.target_price) if final.target_price is not None else "",
                "spot_price_at_entry": str(samples[0].spot_price) if samples[0].spot_price else "",
                "spot_price_at_close": str(final.spot_price),
                "stake_usd": str(STAKE_USD),
                "status": "WIN" if won else "LOSS",
                "pct_return": pct_return,
                "date_opened": now,
                "date_closed": now,
            }
        )
    if rows:
        append_trade_log_rows(rows)
        print(f"btc5m: logged {len(rows)} strategy result(s) for {market.slug}")
    else:
        print(f"btc5m: no strategy fired for {market.slug}")


def main() -> None:
    seconds_left = seconds_to_next_boundary()
    if seconds_left > LOOKAHEAD_SECONDS:
        return  # cheap no-op - most cron ticks land here

    market = find_current_btc5m_market()
    if market is None:
        print("btc5m: could not discover current market this cycle, skipping")
        return

    samples = collect_samples(market)
    evaluate_and_log(market, samples)


if __name__ == "__main__":
    main()
