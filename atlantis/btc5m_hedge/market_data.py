"""Real market data for one BTC 'Up or Down 5m' window: which window is
active right now, its two token ids, and REAL order-book depth (not the
/markets endpoint's lagging last-trade-price snapshot that
run_btc5m_paper_trading.py's fetch_clob_quotes() uses - confirmed
2026-08-05 to sometimes disagree with the tradeable book) on both sides.

Deliberately reimplements the small window-slug helpers already present
in scripts/run_btc5m_paper_trading.py rather than importing them - this
is a package under atlantis/, and packages here don't reach into scripts/
(scripts/ imports FROM atlantis/, never the other way). Same public,
unauthenticated endpoints throughout - no credentials needed for this
whole first (paper) version.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from atlantis.btc5m_hedge.portfolio import OrderBookLevel

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_API_BASE = "https://clob.polymarket.com"
SPOT_PRICE_URL = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
USER_AGENT = "atlantis-btc5m-hedge/0.1"


@dataclass(frozen=True)
class MarketInfo:
    condition_id: str
    slug: str
    up_token_id: str
    down_token_id: str


def current_window_start(now: datetime | None = None, window_seconds: int = 300) -> datetime:
    now = now or datetime.now(timezone.utc)
    epoch = int(now.timestamp())
    floored = epoch - (epoch % window_seconds)
    return datetime.fromtimestamp(floored, tz=timezone.utc)


def slug_for_window(window_start: datetime) -> str:
    return f"btc-updown-5m-{int(window_start.timestamp())}"


def _get_json(url: str, timeout: float = 8.0) -> dict | list | None:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def fetch_market_by_slug(slug: str) -> MarketInfo | None:
    event = _get_json(f"{GAMMA_API_BASE}/events/slug/{slug}")
    if not isinstance(event, dict):
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


def fetch_order_book_asks(token_id: str) -> list[OrderBookLevel]:
    """Real public order book, sorted best (lowest ask) first. Empty list
    on any fetch/parse failure - callers must treat that as 'no liquidity
    visible right now', not raise."""
    book = _get_json(f"{CLOB_API_BASE}/book?token_id={token_id}")
    if not isinstance(book, dict):
        return []
    levels: list[OrderBookLevel] = []
    for raw in book.get("asks") or []:
        try:
            levels.append(OrderBookLevel(price=Decimal(str(raw["price"])), size=Decimal(str(raw["size"]))))
        except (KeyError, InvalidOperation, TypeError):
            continue
    levels.sort(key=lambda lvl: lvl.price)
    return levels


def fetch_resolved_outcome(slug: str) -> str | None:
    """Best-effort, non-blocking - a window that JUST closed almost never
    has a resolution yet (UMA/oracle resolution lags close by anywhere
    from under a minute to several minutes), so a single check at close
    time nearly always returns None. Returning None here just means "not
    resolved YET, not that it never will be" - callers that only check
    once at close time and never retry will end up with realized_outcome
    permanently blank even for windows that resolved fine a few minutes
    later (confirmed live 2026-08-08 - every window summary the paper bot
    logged this way showed realized_outcome as "?"). See
    logger.backfill_missing_outcomes for the retry that fixes this."""
    event = _get_json(f"{GAMMA_API_BASE}/events/slug/{slug}")
    if not isinstance(event, dict):
        return None
    markets = event.get("markets") or []
    if not markets or not markets[0].get("closed"):
        return None
    try:
        outcomes = json.loads(markets[0].get("outcomes") or "[]")
        prices = json.loads(markets[0].get("outcomePrices") or "[]")
    except (json.JSONDecodeError, TypeError):
        return None
    for outcome, price in zip(outcomes, prices):
        if str(price) == "1":
            return str(outcome)
    return None


def fetch_spot_price() -> Decimal | None:
    """Optional context column (per the backtest input spec's 'btc_price')
    - never used by the hedge strategy's own decisions, which only look
    at the two option books, not BTC's spot price."""
    data = _get_json(SPOT_PRICE_URL, timeout=6.0)
    if not isinstance(data, dict):
        return None
    try:
        return Decimal(str(data["data"]["amount"]))
    except (KeyError, InvalidOperation, TypeError):
        return None
