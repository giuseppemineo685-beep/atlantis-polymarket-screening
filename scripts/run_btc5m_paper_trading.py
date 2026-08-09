"""BTC 'Up or Down 5m' paper-trading experiment - 5 independent entry
strategies (A/B/C/D/E) tested against the same price samples. Paper only,
deliberately standalone: no import of live_intents or anything from the
live-execution path - this vertical has no wallets to vet and nothing
here should ever be able to touch the real-money order queue (same
isolation rule scripts/run_screening_and_notify_esports.py uses).

Strategy E (added 2026-08-04) is inspired by a real high-volume wallet
(0x3048d65321be3497164cdfc2996f94f98a2e7537) found to be trading these
exact markets - not a byte-for-byte reproduction of its actual algorithm
(that isn't observable, only its resulting trades are), but a faithful
approximation of the confirmed pattern: within one 5-minute window it
placed ~6 small buys over ~57 seconds, mostly scaling into whichever side
spot favored, with brief cheap probes of the other side, able to flip
sides entirely if price reversed mid-window. Investigated live via
data-api.polymarket.com/trades + /positions (~78% real win rate, small
losses/large wins - the "ghost loss" gotcha already known in this repo
meant /closed-positions alone looked like ~100% win rate before cross-
checking against /positions for the never-redeemed losing side).

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
STAKE_USD_SCALED_ENTRY = Decimal("0.20")  # Strategy E's per-increment stake (several fire per window)
WINDOW_SECONDS = 5 * 60
# Strategy E needs samples from the back half of the window (its earliest
# confirmed real-world entry was ~200s before close) - the other 4 only
# ever look at the closing ~40s, but sampling from 150s doesn't cost them
# anything, they just ignore the earlier samples.
LOOKAHEAD_SECONDS = 150
STALE_START_SECONDS = 90  # if we first see a window this late into it, its target price is unusable
SAMPLE_INTERVAL_SECONDS = 4
SPOT_PRICE_URL = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
KRAKEN_PRICE_URL = "https://api.kraken.com/0/public/Ticker?pair=XBTUSD"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
# How far apart two exchanges' spot prices were confirmed to drift
# live 2026-08-08 (Binance vs Coinbase/Kraken, sustained ~$31 for at
# least 20+ seconds, not a one-tick blip) - single-exchange spot is not
# always a safe proxy for anything, let alone a 30s TWAP.
TWAP_WINDOW_SECONDS = 30


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


def fetch_binance_price() -> Decimal | None:
    req = urllib.request.Request(
        BINANCE_PRICE_URL, headers={"Accept": "application/json", "User-Agent": "atlantis-btc5m/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
        return Decimal(str(data["price"]))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, InvalidOperation):
        return None


def fetch_kraken_price() -> Decimal | None:
    req = urllib.request.Request(
        KRAKEN_PRICE_URL, headers={"Accept": "application/json", "User-Agent": "atlantis-btc5m/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
        if data.get("error"):
            return None
        pair_data = next(iter(data["result"].values()))
        return Decimal(str(pair_data["c"][0]))  # "c" = last trade [price, lot volume]
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, StopIteration, InvalidOperation):
        return None


def fetch_multi_exchange_price() -> Decimal | None:
    """Simple mean of whichever of Coinbase/Binance/Kraken respond right
    now - NOT a TWAP by itself (that needs samples over time, see
    RollingTwap below), just a single instant's cross-exchange average.
    Confirmed live 2026-08-08: Binance can sit ~$30 away from Coinbase/
    Kraken for 20+ seconds at a time, so averaging 3 sources is already
    more representative of "the real price" than trusting any single
    exchange, even before adding the time dimension."""
    prices = [p for p in (fetch_spot_price(), fetch_binance_price(), fetch_kraken_price()) if p is not None]
    if not prices:
        return None
    return sum(prices) / len(prices)


@dataclass
class RollingTwap:
    """Homegrown approximation of Chainlink's paid 30s BTC/USD TWAP
    stream (data.chain.link/streams/btc-usd-twap-30s-streams, $150/mo to
    access directly) - Polymarket's OWN resolution source for these
    markets (confirmed via the event description 2026-08-08), not
    instantaneous spot from any single exchange. Since Chainlink's own
    feed is itself an aggregate across multiple venues, averaging our own
    multi-exchange samples over a rolling time window should track it far
    more closely than a single Coinbase spot tick ever could - not an
    exact match (different exchange set, different weighting, no
    guarantee of the same 30s alignment), but a meaningfully better proxy
    for what actually decides these markets.

    Time-weighted, not just an arithmetic mean of samples: each sample's
    price is assumed to hold from when it was taken until the NEXT
    sample (or until `now`, for the most recent one), so uneven polling
    intervals (a slow network response, a GC pause) don't silently over-
    or under-weight a price that happened to get sampled more or less
    often.
    """

    window_seconds: float = TWAP_WINDOW_SECONDS
    _samples: list[tuple[float, Decimal]] = field(default_factory=list)

    def record(self, timestamp: float, price: Decimal) -> None:
        self._samples.append((timestamp, price))
        cutoff = timestamp - self.window_seconds
        self._samples = [(t, p) for t, p in self._samples if t >= cutoff]

    def value(self, now: float | None = None) -> Decimal | None:
        if not self._samples:
            return None
        now = now if now is not None else self._samples[-1][0]
        cutoff = now - self.window_seconds
        relevant = [(t, p) for t, p in self._samples if t >= cutoff]
        if not relevant:
            return None
        if len(relevant) == 1:
            return relevant[0][1]

        weighted_sum = Decimal(0)
        total_duration = Decimal(0)
        for i, (t, p) in enumerate(relevant):
            next_t = relevant[i + 1][0] if i + 1 < len(relevant) else now
            duration = Decimal(str(max(next_t - t, 0)))
            weighted_sum += p * duration
            total_duration += duration
        if total_duration <= 0:
            return relevant[-1][1]
        return weighted_sum / total_duration


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
# window and returns a list of (direction, entry_price, stake_usd)
# entries it would have made - empty if its condition never fired. Pure
# functions, no shared state - all 5 run independently over the same
# sample list every window. A-D only ever make at most one entry each;
# E (see module docstring) can make several.

def strategy_a_lag_arbitrage(samples: list[Sample]) -> list[tuple[str, Decimal, Decimal]]:
    candidates = [s for s in samples if 30 <= s.seconds_to_close <= 40]
    if not candidates:
        return []
    s = candidates[-1]
    if s.spot_price is None or s.target_price is None:
        return []
    if s.spot_price > s.target_price and s.up_quote is not None and s.up_quote < Decimal("0.20"):
        return [("UP", s.up_quote, STAKE_USD)]
    if s.spot_price < s.target_price and s.down_quote is not None and s.down_quote < Decimal("0.20"):
        return [("DOWN", s.down_quote, STAKE_USD)]
    return []


def strategy_b_momentum(samples: list[Sample]) -> list[tuple[str, Decimal, Decimal]]:
    candidates = [s for s in samples if 30 <= s.seconds_to_close <= 40]
    priced = [s for s in samples if s.spot_price is not None]
    if not candidates or len(priced) < 2:
        return []
    s = candidates[-1]
    if s.spot_price is None:
        return []
    earliest, latest = priced[0], priced[-1]
    if latest.spot_price > earliest.spot_price and s.up_quote is not None:
        return [("UP", s.up_quote, STAKE_USD)]
    if latest.spot_price < earliest.spot_price and s.down_quote is not None:
        return [("DOWN", s.down_quote, STAKE_USD)]
    return []


def strategy_d_cheap_blind(samples: list[Sample]) -> list[tuple[str, Decimal, Decimal]]:
    candidates = [s for s in samples if 20 <= s.seconds_to_close <= 40]
    if not candidates:
        return []
    s = candidates[-1]
    threshold = Decimal("0.15")
    if s.up_quote is not None and s.up_quote < threshold:
        return [("UP", s.up_quote, STAKE_USD)]
    if s.down_quote is not None and s.down_quote < threshold:
        return [("DOWN", s.down_quote, STAKE_USD)]
    return []


def strategy_e_scaling_replicator(samples: list[Sample]) -> list[tuple[str, Decimal, Decimal]]:
    """Scales into whichever side spot currently favors, roughly every
    25s through the back half of the window (seconds_to_close <= 150) -
    can make several small entries, and can flip sides entirely if spot
    crosses back over target mid-window, same as the real bot's observed
    behavior. See module docstring for the confirmed trade sequence this
    is modeled on."""
    entries: list[tuple[str, Decimal, Decimal]] = []
    last_entry_at: float | None = None
    for s in samples:
        if s.seconds_to_close > 150:
            continue
        if s.spot_price is None or s.target_price is None:
            continue
        if last_entry_at is not None and (last_entry_at - s.seconds_to_close) < 25:
            continue
        if s.spot_price > s.target_price:
            direction, quote = "UP", s.up_quote
        elif s.spot_price < s.target_price:
            direction, quote = "DOWN", s.down_quote
        else:
            continue
        if quote is None or quote <= 0 or quote >= 1:
            continue
        entries.append((direction, quote, STAKE_USD_SCALED_ENTRY))
        last_entry_at = s.seconds_to_close
    return entries


STRATEGIES = {
    "A_lag_arbitrage": strategy_a_lag_arbitrage,
    "B_momentum": strategy_b_momentum,
    "D_cheap_blind": strategy_d_cheap_blind,
    "E_scaling_replicator": strategy_e_scaling_replicator,
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
    if winner is not None:
        for name, fn in STRATEGIES.items():
            for direction, entry_price, stake in fn(samples):
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
                        "stake_usd": str(stake),
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
