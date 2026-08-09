import json
import time
from decimal import Decimal

from atlantis.polymarket.chainlink_twap import TwapReading, TwapStream, parse_twap_message


def test_parse_twap_message_extracts_value():
    raw = json.dumps(
        {
            "topic": "crypto_prices_twap_thirty",
            "type": "update",
            "timestamp": 1786188035731,
            "payload": {"symbol": "btc/usd", "value": 64950.99528242389, "timestamp": 1786188035000, "window_s": 30},
        }
    )
    reading = parse_twap_message(raw, "btc/usd")
    assert reading is not None
    assert reading.value == Decimal("64950.99528242389")
    assert reading.chainlink_timestamp_ms == 1786188035000


def test_parse_twap_message_ignores_wrong_symbol():
    raw = json.dumps({"payload": {"symbol": "eth/usd", "value": 3000.0}})
    assert parse_twap_message(raw, "btc/usd") is None


def test_parse_twap_message_ignores_malformed_json():
    assert parse_twap_message("not json", "btc/usd") is None


def test_parse_twap_message_ignores_missing_payload():
    assert parse_twap_message(json.dumps({"type": "update"}), "btc/usd") is None


def test_twap_stream_latest_returns_none_before_any_reading():
    stream = TwapStream("btc/usd")
    assert stream.latest() is None


def test_twap_stream_latest_returns_fresh_value():
    stream = TwapStream("btc/usd")
    stream._reading = TwapReading(value=Decimal("64950.5"), chainlink_timestamp_ms=123, received_at_monotonic=time.monotonic())
    assert stream.latest(max_age_seconds=5.0) == Decimal("64950.5")


def test_twap_stream_latest_returns_none_when_stale():
    stream = TwapStream("btc/usd")
    stream._reading = TwapReading(
        value=Decimal("64950.5"), chainlink_timestamp_ms=123, received_at_monotonic=time.monotonic() - 10.0
    )
    assert stream.latest(max_age_seconds=5.0) is None
