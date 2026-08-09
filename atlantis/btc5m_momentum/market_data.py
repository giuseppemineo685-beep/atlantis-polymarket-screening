"""BTC spot price fetch for the momentum signal - separate from
atlantis/btc5m_hedge/market_data.py's fetch_spot_price (Coinbase) because
the 2026-08-09 signal derivation used Binance's own klines specifically
(the old run_btc5m_paper_trading.py's own comment notes a confirmed
sustained ~$31 divergence between Binance and Coinbase/Kraken at least
once - single-exchange spot is not a universally safe proxy, so this
bot deliberately keeps using the SAME exchange the signal was derived
against rather than assuming any of them are interchangeable).

Everything else this bot needs (which window is active, its token ids,
real order-book depth, resolved outcomes) is generic to the "Bitcoin Up
or Down 5m" market itself, not specific to hedging - reused directly
from atlantis.btc5m_hedge.market_data rather than duplicated.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT"
USER_AGENT = "atlantis-btc5m-momentum/0.1"


def fetch_binance_price() -> Decimal | None:
    req = urllib.request.Request(
        BINANCE_PRICE_URL, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=6.0) as resp:
            data = json.loads(resp.read())
        return Decimal(str(data["price"]))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, InvalidOperation):
        return None
