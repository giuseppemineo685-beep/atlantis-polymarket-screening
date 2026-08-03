"""BTC 'Up or Down 5m' paper-trading experiment - 4 independent entry
strategies (A/B/C/D) tested against the same price samples. Paper only,
$1/signal, deliberately standalone: no import of live_intents or anything
from the live-execution path - this vertical has no wallets to vet and
nothing here should ever be able to touch the real-money order queue
(same isolation rule scripts/run_screening_and_notify_esports.py uses).

Unlike the wallet-copying verticals, a window opens and resolves within
the same script invocation - by the time a 5-minute window closes we
already hold the final spot-vs-target sample, so there's no cross-cycle
"still open" trade status to track.

Invoked every minute by cron (wrapped in `flock -n` at the crontab level,
same pattern as run_live_execution.py). Every invocation does one cheap
thing (detect a new window, record its opening reference price) and then,
only near a close, does the expensive thing (poll every few seconds until
the boundary passes). Crontab can only express 1-minute granularity, but
the strategies need price samples every few seconds in the final ~60s
before each 5-minute boundary, so this script checks how close the next
boundary is and only loops internally (`time.sleep`) when within reach.

Verified 2026-08-04 against a live market (a real high-volume bot wallet's
trade history led here) - Polymarket's own gamma-api event object for one
of these markets:
- Title: "Bitcoin Up or Down - August 3, 6:15PM-6:20PM ET"
- Slug: "btc-updown-5m-1785795300" (1785795300 == 2026-08-03 22:15:00 UTC,
  the window's OPENING instant - these windows are 5-min-UTC-aligned,
  :00/:05/:10/..., not something separately ET-aligned).
- Resolution rule (from the event description): "This market will resolve
  to 'Up' if the Bitcoin price at the end of the time range ... is
  greater than or equal to the price at the beginning of that range.
  Otherwise, it will resolve to 'Down'." Resolution source is Chainlink's
  BTC/USD data stream (data.chain.link/streams/btc-usd), not a generic
  exchange spot price - there is NO separate strike/target field to fetch;
  the target IS the window's own opening price, which this script has to
  record itself (state/btc5m_window.json) since it can't be looked up
  after the fact. Coinbase's public spot endpoint is used as a proxy for
  Chainlink's stream (tracks within milliseconds in practice, but is not
  a guaranteed tick-for-tick match).
"""

from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRADE_LOG_PATH = ROOT / "outputs" / "trade_log_btc5m.csv"
WINDOW_STATE_PATH = ROOT / "state" / "btc5m_window.json"

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
LOOKAHEAD_SECONDS = 65  # start the tight polling loop once within this many seconds of a close
STALE_START_SECONDS = 90  # if we first see a window this late into it, its target price is unusable
SAMPLE_INTERVAL_SECONDS = 4
SPOT_PRICE_URL = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"


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


def current_window_start(now: datetime | None = None) -> datetime:
    """Floor `now` to the current 5-minute-aligned UTC boundary."""
    now = now or datetime.now(timezone.utc)
    epoch = int(now.timestamp())
    floored = epoch - (epoch % WINDOW_SECONDS)
    return datetime.fromtimestamp(floored, tz=timezone.utc)


def slug_for_window(window_start: datetime) -> str:
    return f"btc-updown-5m-{int(window_start.timestamp())}"


def fetch_spot_price() -> Decimal | None:
    """Independent BTC/USD reference (Coinbase's public, unauthenticated
    spot endpoint) - a proxy for Chainlink's BTC/USD stream, which is
    Polymarket's actual resolution source for these markets (confirmed via
    the event description, see module docstring). Public exchange spot
    prices track a Chainlink aggregate closely in practice, but this is a
    proxy, not a guaranteed match."""
    req = urllib.request.Request(
        SPOT_PRICE_URL, headers={"Accept": "application/json", "User-Agent": "atlantis-btc5m/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
        return Decimal(str(data["data"]["amount"]))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, InvalidOperation):
        return None


def fetch_market_by_slug(slug: str) -> MarketInfo | None:
    """GET gamma-api's /events/slug/{slug} (same endpoint as
    PolymarketClient.get_event_by_slug) and pull the single market's
    condition_id + the token_id for each of Up/Down. Returns None on any
    failure - callers must treat that as "can't track this window"."""
    url = f"{GAMMA_API_BASE}/events/slug/{slug}"
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "atlantis-btc5m/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            event = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    markets = event.get("markets") or []
    if not markets:
        return None
    market = markets[0]
    try:
        tokens = json.loads(market.get("clobTokenIds") or "[]")
        outcomes = json.loads(market.get("outcomes") or "[]")
    except (json.JSONDecodeError, TypeError):
        return None
    if len(tokens) != 2 or len(outcomes) != 2:
        return None
    up_idx = 0 if str(outcomes[0]).lower().startswith("up") else 1
    down_idx = 1 - up_idx
    return MarketInfo(
        condition_id=str(market.get("conditionId", "")),
        slug=slug,
        up_token_id=str(tokens[up_idx]),
        down_token_id=str(tokens[down_idx]),
    )


def fetch_clob_quotes(condition_id: str, up_token_id: str, down_token_id: str) -> tuple[Decimal | None, Decimal | None]:
    """Same live-snapshot shape already used elsewhere in this repo
    (live_execution.py::get_market_resolution, generate_dashboard.py::
    fetch_live_price) - GET clob.polymarket.com/markets/{condition_id} ->
    {"tokens": [...]}."""
    url = f"{CLOB_API_BASE}/markets/{condition_id}"
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


def load_window_state() -> dict | None:
    if not WINDOW_STATE_PATH.exists():
        return None
    try:
        return json.loads(WINDOW_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def save_window_state(state: dict) -> None:
    WINDOW_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    WINDOW_STATE_PATH.write_text(json.dumps(state))


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


def collect_samples(market: MarketInfo, target_price: Decimal | None) -> list[Sample]:
    samples: list[Sample] = []
    window_start = current_window_start()
    close_at = window_start + timedelta(seconds=WINDOW_SECONDS)
    while True:
        seconds_left = (close_at - datetime.now(timezone.utc)).total_seconds()
        spot = fetch_spot_price()
        up_q, down_q = fetch_clob_quotes(market.condition_id, market.up_token_id, market.down_token_id)
        samples.append(
            Sample(
                seconds_to_close=seconds_left,
                spot_price=spot,
                target_price=target_price,
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
        print(f"btc5m: no priced samples with a known target for {market.slug}, skipping strategies A/C")
    now = _now()
    rows = []
    winner = None
    if priced:
        final = priced[-1]
        winner = "UP" if final.spot_price >= final.target_price else "DOWN"
    for name, fn in STRATEGIES.items():
        result = fn(samples)
        if result is None:
            continue
        direction, entry_price = result
        if winner is None:
            continue  # can't score a strategy without knowing who won
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
                "target_price": str(priced[-1].target_price) if priced else "",
                "spot_price_at_entry": str(samples[0].spot_price) if samples[0].spot_price else "",
                "spot_price_at_close": str(priced[-1].spot_price) if priced else "",
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
        print(f"btc5m: no strategy fired (or no target price) for {market.slug}")


def main() -> None:
    now = datetime.now(timezone.utc)
    window_start = current_window_start(now)
    slug = slug_for_window(window_start)
    seconds_since_start = (now - window_start).total_seconds()

    state = load_window_state()
    if state is None or state.get("slug") != slug:
        market = fetch_market_by_slug(slug)
        if market is None:
            print(f"btc5m: could not fetch market for {slug}, skipping")
            return
        # Only trust a just-detected window's opening price if we caught it
        # early - if this process was down and restarts mid-window, "now"
        # is not "the start of the window" and target_price would be
        # wrong, so strategies A/C are deliberately left without a target
        # for that one window rather than scored against a bad reference.
        target_price = fetch_spot_price() if seconds_since_start <= STALE_START_SECONDS else None
        state = {
            "slug": slug,
            "condition_id": market.condition_id,
            "up_token_id": market.up_token_id,
            "down_token_id": market.down_token_id,
            "target_price": str(target_price) if target_price is not None else "",
        }
        save_window_state(state)
        print(f"btc5m: tracking new window {slug}, opening spot price {target_price}")

    close_at = window_start + timedelta(seconds=WINDOW_SECONDS)
    seconds_left = (close_at - now).total_seconds()
    if seconds_left > LOOKAHEAD_SECONDS:
        return  # already did the cheap bookkeeping above - nothing else this tick

    target_price = Decimal(state["target_price"]) if state.get("target_price") else None
    market = MarketInfo(
        condition_id=state["condition_id"],
        slug=slug,
        up_token_id=state["up_token_id"],
        down_token_id=state["down_token_id"],
    )
    samples = collect_samples(market, target_price)
    evaluate_and_log(market, samples)


if __name__ == "__main__":
    main()
