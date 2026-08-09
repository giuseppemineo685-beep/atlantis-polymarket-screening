"""BTC 'Up or Down 5m' cross-side arbitrage shadow detector - ZERO real
money risk, no credentials, no orders. Answers the question raised
2026-08-08 after reverse-engineering wallet 0x3048...e7537's real trades:
that wallet isn't betting direction, it's buying Up and Down within
seconds of each other whenever best_ask(Up) + best_ask(Down) < $1 (e.g.
0.64 + 0.34 = 0.98), which pays out exactly $1 regardless of which side
resolves - a near risk-free cross-side hedge, not a directional bet. That
explains the wallet's ~78% "always positive" real record and its 25-40+
trades per 5-minute window (it's scanning both sides continuously for
this transient mispricing, not reacting to a directional signal).

Before building a live executor that actually buys both legs (real
capital, real legging risk if one side fills and the other doesn't, real
minimum-size/slippage constraints), this script measures - on the REAL
public order book, not the laggy /markets "last price" snapshot
fetch_clob_quotes() uses elsewhere in this repo - how often the
opportunity appears, how deep it goes, how much size is available at
that price, and how long it survives. Same public, unauthenticated
clob.polymarket.com/book endpoint and shadow-only philosophy as
run_wallet_copy_shadow_analysis.py.

Reuses current_window_start/slug_for_window/fetch_market_by_slug/
MarketInfo/WINDOW_SECONDS from run_btc5m_paper_trading.py rather than
re-deriving window-tracking logic.

Deliberately does NOT need Finland (nothing here places an order) - runs
anywhere as a persistent process (systemd, Restart=always), same pattern
as run_wallet_copy_shadow_analysis.py.
"""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from run_btc5m_paper_trading import (  # noqa: E402
    WINDOW_SECONDS,
    MarketInfo,
    current_window_start,
    fetch_market_by_slug,
    slug_for_window,
)

BOOK_API_URL = "https://clob.polymarket.com/book?token_id={token_id}"
POLL_INTERVAL_SECONDS = 1.0

# Anything below 1.00 is technically an opportunity, but the real book has
# a natural bid/ask spread and our own two-leg execution would eat a
# little more - 0.97 leaves ~3c of margin (matches the threshold floated
# 2026-08-08) so what gets logged as "actionable" is closer to what a real
# two-leg buy could actually capture, not just book noise.
ARB_THRESHOLD = Decimal("0.97")
# Below this, the ask likely can't fill our real $1 minimum notional per
# leg without walking deeper into the book at a worse price than the
# top-of-book ask this script is measuring.
MIN_USABLE_SIZE = Decimal("1")

OPPORTUNITIES_CSV = ROOT / "outputs" / "btc5m_arb_shadow_opportunities.csv"
WINDOW_SUMMARY_CSV = ROOT / "outputs" / "btc5m_arb_shadow_window_summary.csv"

OPP_FIELDS = [
    "window_slug",
    "seconds_to_close",
    "up_ask",
    "up_ask_size",
    "down_ask",
    "down_ask_size",
    "sum_ask",
    "usable",
    "observed_at",
]
SUMMARY_FIELDS = [
    "window_slug",
    "total_polls",
    "polls_below_1",
    "polls_below_threshold_usable",
    "min_sum_seen",
    "seconds_below_threshold_usable_approx",
    "window_closed_at",
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _log(msg: str) -> None:
    print(f"[{_now()}] {msg}")


def append_row(path: Path, fields: list[str], row: dict) -> None:
    is_new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def fetch_best_ask(token_id: str) -> tuple[Decimal | None, Decimal | None]:
    """Public order book read, no auth - the real price a marketable BUY
    would have to pay right now (min ask), not the /markets endpoint's
    lagging last-trade-price snapshot."""
    req = urllib.request.Request(
        BOOK_API_URL.format(token_id=token_id),
        headers={"Accept": "application/json", "User-Agent": "atlantis-btc5m-arb-shadow/0.1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            book = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None, None
    asks = book.get("asks") or []
    best = None
    for level in asks:
        try:
            price = Decimal(str(level["price"]))
            size = Decimal(str(level["size"]))
        except (KeyError, InvalidOperation, TypeError):
            continue
        if best is None or price < best[0]:
            best = (price, size)
    return best if best else (None, None)


class WindowStats:
    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.total_polls = 0
        self.polls_below_1 = 0
        self.polls_below_threshold_usable = 0
        self.min_sum_seen: Decimal | None = None

    def record(self, sum_ask: Decimal | None, usable: bool) -> None:
        self.total_polls += 1
        if sum_ask is None:
            return
        if self.min_sum_seen is None or sum_ask < self.min_sum_seen:
            self.min_sum_seen = sum_ask
        if sum_ask < 1:
            self.polls_below_1 += 1
        if sum_ask < ARB_THRESHOLD and usable:
            self.polls_below_threshold_usable += 1

    def flush(self) -> None:
        append_row(
            WINDOW_SUMMARY_CSV,
            SUMMARY_FIELDS,
            {
                "window_slug": self.slug,
                "total_polls": self.total_polls,
                "polls_below_1": self.polls_below_1,
                "polls_below_threshold_usable": self.polls_below_threshold_usable,
                "min_sum_seen": str(self.min_sum_seen) if self.min_sum_seen is not None else "",
                "seconds_below_threshold_usable_approx": round(
                    self.polls_below_threshold_usable * POLL_INTERVAL_SECONDS, 1
                ),
                "window_closed_at": _now(),
            },
        )
        _log(
            f"btc5m-arb-shadow: {self.slug} cerrada - {self.polls_below_threshold_usable}/"
            f"{self.total_polls} polls con arb usable (min_sum={self.min_sum_seen})"
        )


def main() -> None:
    _log("btc5m-arb-shadow: proceso persistente arrancando (solo lectura, sin dinero real)")
    current_slug: str | None = None
    market: MarketInfo | None = None
    stats: WindowStats | None = None

    while True:
        now = datetime.now(timezone.utc)
        window_start = current_window_start(now)
        slug = slug_for_window(window_start)
        close_at = window_start + timedelta(seconds=WINDOW_SECONDS)
        seconds_to_close = (close_at - now).total_seconds()

        if slug != current_slug:
            if stats is not None:
                stats.flush()
            fetched = fetch_market_by_slug(slug)
            if fetched is None:
                _log(f"btc5m-arb-shadow: no se pudo obtener el mercado para {slug}, reintentando")
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            market = fetched
            current_slug = slug
            stats = WindowStats(slug)
            _log(f"btc5m-arb-shadow: siguiendo nueva ventana {slug}")

        up_ask, up_size = fetch_best_ask(market.up_token_id)
        down_ask, down_size = fetch_best_ask(market.down_token_id)

        sum_ask = None
        usable = False
        if up_ask is not None and down_ask is not None:
            sum_ask = up_ask + down_ask
            usable = (
                up_size is not None
                and down_size is not None
                and up_size >= MIN_USABLE_SIZE
                and down_size >= MIN_USABLE_SIZE
            )

        stats.record(sum_ask, usable)

        if sum_ask is not None and sum_ask < ARB_THRESHOLD:
            append_row(
                OPPORTUNITIES_CSV,
                OPP_FIELDS,
                {
                    "window_slug": slug,
                    "seconds_to_close": round(seconds_to_close, 1),
                    "up_ask": str(up_ask),
                    "up_ask_size": str(up_size) if up_size is not None else "",
                    "down_ask": str(down_ask),
                    "down_ask_size": str(down_size) if down_size is not None else "",
                    "sum_ask": str(sum_ask),
                    "usable": usable,
                    "observed_at": _now(),
                },
            )
            _log(
                f"btc5m-arb-shadow: OPORTUNIDAD {slug} sum={sum_ask} "
                f"(up={up_ask}x{up_size}, down={down_ask}x{down_size}) usable={usable}"
            )

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
