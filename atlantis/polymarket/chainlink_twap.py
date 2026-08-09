"""Real-time BTC/USD Chainlink TWAP client via Polymarket's public RTDS
WebSocket (wss://ws-live-data.polymarket.com) - free, no credentials.

Confirmed 2026-08-08: this is the EXACT value Polymarket's crypto Up/Down
markets resolve against (Polymarket switched these markets to TWAP-based
resolution 2026-08-07 specifically to close a manipulation window that
existed under single-snapshot-price resolution - see the event
description on gamma-api, resolutionSource
data.chain.link/streams/btc-usd-twap-30s-streams). Direct API access to
that Chainlink stream costs $150/mo; Polymarket's own RTDS relays the
identical Chainlink-computed values for free via the
crypto_prices_twap_thirty / crypto_prices_twap_sixty topics.

Verified live by connecting directly and comparing against Coinbase/
Binance/Kraken spot at the same moments: Binance was sitting ~$30 away
from Coinbase/Kraken for 20+ seconds (not a one-tick blip); this feed
tracked Coinbase/Kraken closely, not Binance - confirming single-exchange
spot (whichever exchange) is not a safe stand-in for what actually
resolves these markets, and this feed is the real thing, not another
proxy.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import websockets

RTDS_WS_URL = "wss://ws-live-data.polymarket.com"
PING_INTERVAL_SECONDS = 5.0
MAX_RECONNECT_BACKOFF_SECONDS = 30.0


def _topic_for_window(window: str) -> str:
    if window not in ("thirty", "sixty"):
        raise ValueError(f"window must be 'thirty' or 'sixty', got {window!r}")
    return f"crypto_prices_twap_{window}"


@dataclass(frozen=True)
class TwapReading:
    value: Decimal
    chainlink_timestamp_ms: int | None
    received_at_monotonic: float


def parse_twap_message(raw: str, symbol: str) -> TwapReading | None:
    """Pure parsing, no I/O - kept separate from the socket loop so it's
    unit-testable without a live connection. Returns None for anything
    that isn't a matching-symbol update (wrong symbol, malformed JSON,
    non-numeric value)."""
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    payload = msg.get("payload") if isinstance(msg, dict) else None
    if not isinstance(payload, dict) or payload.get("symbol") != symbol:
        return None
    try:
        value = Decimal(str(payload["value"]))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None
    return TwapReading(
        value=value,
        chainlink_timestamp_ms=payload.get("timestamp"),
        received_at_monotonic=time.monotonic(),
    )


class TwapStream:
    """Persistent background connection keeping the latest TWAP reading
    for one symbol/window fresh in memory. Reconnects with exponential
    backoff on any failure - a dropped connection must never silently
    freeze `latest()` on a stale value without `latest()` itself noticing
    via the max_age_seconds check.

    Use as a context manager:
        with TwapStream("btc/usd") as stream:
            price = stream.latest()
    or call start()/stop() directly for a longer-lived process.
    """

    def __init__(self, symbol: str = "btc/usd", window: str = "thirty") -> None:
        self.symbol = symbol
        self.topic = _topic_for_window(window)
        self._reading: TwapReading | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._connected_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, connect_timeout: float = 10.0) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(target=lambda: asyncio.run(self._run_async()), daemon=True)
        self._thread.start()
        if not self._connected_event.wait(timeout=connect_timeout):
            raise TimeoutError(f"TwapStream no conecto en {connect_timeout}s")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> TwapStream:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def latest(self, max_age_seconds: float = 5.0) -> Decimal | None:
        with self._lock:
            reading = self._reading
        if reading is None:
            return None
        if time.monotonic() - reading.received_at_monotonic > max_age_seconds:
            return None
        return reading.value

    async def _run_async(self) -> None:
        backoff = 1.0
        # separators=(",", ":") is load-bearing, not cosmetic - the docs
        # require the inner filter as compact JSON with no spaces
        # ('{"symbol":"btc/usd"}'). json.dumps' default separators add a
        # space after ':', which the server silently fails to match on -
        # confirmed live 2026-08-08: with the space, the socket connects
        # fine but no update ever arrives (one empty ack frame, then
        # nothing), no error of any kind to indicate why.
        subscribe_msg = json.dumps(
            {
                "action": "subscribe",
                "subscriptions": [
                    {"topic": self.topic, "type": "update", "filters": json.dumps({"symbol": self.symbol}, separators=(",", ":"))}
                ],
            }
        )
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(RTDS_WS_URL, ping_interval=None, open_timeout=8) as ws:
                    await ws.send(subscribe_msg)
                    self._connected_event.set()
                    backoff = 1.0
                    last_ping = time.monotonic()
                    while not self._stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=PING_INTERVAL_SECONDS)
                            reading = parse_twap_message(raw, self.symbol)
                            if reading is not None:
                                with self._lock:
                                    self._reading = reading
                        except asyncio.TimeoutError:
                            pass
                        if time.monotonic() - last_ping >= PING_INTERVAL_SECONDS:
                            await ws.send("PING")
                            last_ping = time.monotonic()
            except Exception:
                self._connected_event.clear()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_RECONNECT_BACKOFF_SECONDS)


def fetch_twap_once(symbol: str = "btc/usd", window: str = "thirty", timeout: float = 10.0) -> Decimal | None:
    """One-shot connect/subscribe/read-one-value/disconnect - for short-
    lived cron-style scripts that just want a single fresh reading
    without holding a persistent connection open. Prefer TwapStream for
    anything that already runs a multi-second polling loop (e.g. the
    final ~150s before a window closes) - one connection reused across
    that whole loop is far cheaper than reconnecting every poll."""

    async def _fetch() -> Decimal | None:
        topic = _topic_for_window(window)
        subscribe_msg = json.dumps(
            {"action": "subscribe", "subscriptions": [{"topic": topic, "type": "update", "filters": json.dumps({"symbol": symbol}, separators=(",", ":"))}]}
        )
        deadline = time.monotonic() + timeout
        try:
            async with websockets.connect(RTDS_WS_URL, ping_interval=None, open_timeout=8) as ws:
                await ws.send(subscribe_msg)
                while time.monotonic() < deadline:
                    remaining = max(deadline - time.monotonic(), 0.1)
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    reading = parse_twap_message(raw, symbol)
                    if reading is not None:
                        return reading.value
        except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException):
            return None
        return None

    return asyncio.run(_fetch())
