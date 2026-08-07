"""Shadow analysis for the BTC5m wallet-copy mechanism - answers, at
ZERO real-money risk, the open question from 2026-08-07: is the copy
mechanism's poor real win rate (49.2% resolved, vs the wallet's own
~78% historical win rate) caused by execution-price decay from our
detection/processing delay, or mostly sample bias from only capturing a
fraction (~36%) of the wallet's real trades?

Watches wallet 0x3048...e7537's real trades (same public,
unauthenticated data-api.polymarket.com/trades endpoint the real copy
script uses) and, for each NEW trade detected, records the REAL public
order book's best price (clob.polymarket.com/book - also public, no
auth) at several delays after detection: ~0s, ~1s, ~2s, ~3s, ~5s. This
builds an empirical "cost of delay" curve without ever placing a real
order.

Deliberately does NOT need Finland or any account credentials - both
endpoints used here are public reads, so this runs on Germany (or
anywhere) as a plain persistent process, same pattern as the real copy
script (systemd, Restart=always) but with none of that script's
real-money safety machinery (no kill switch, no client, no stake) since
there is nothing here that can lose money.
"""

from __future__ import annotations

import csv
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TARGET_WALLET = "0x3048d65321be3497164cdfc2996f94f98a2e7537"
TRADES_API_URL = f"https://data-api.polymarket.com/trades?user={TARGET_WALLET}&limit=100"
BOOK_API_URL = "https://clob.polymarket.com/book?token_id={token_id}"

POLL_INTERVAL_SECONDS = 0.5
# Delay checkpoints (seconds after the wallet's trade) to sample the
# real order book at - chosen to bracket the range of delays actually
# observed in the real copy mechanism's logs (typically 1-10s, with a
# long tail out to minutes under load).
DELAY_CHECKPOINTS = [0, 1, 2, 3, 5, 8]

SEEN_TRADES_PATH = ROOT / "state" / "btc5m_shadow_seen_trades.json"
MAX_SEEN_TRADES = 500
OUTPUT_PATH = ROOT / "outputs" / "btc5m_copy_shadow_analysis.csv"

FIELDS = [
    "trade_tx_hash",
    "window_slug",
    "outcome",
    "wallet_price",
    "wallet_size",
    "wallet_timestamp",
    "delay_seconds_target",
    "delay_seconds_actual",
    "best_ask",
    "best_ask_size",
    "observed_at",
]

output_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _log(msg: str) -> None:
    print(f"[{_now()}] {msg}")


def append_rows(rows: list[dict]) -> None:
    with output_lock:
        is_new = not OUTPUT_PATH.exists()
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with OUTPUT_PATH.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            if is_new:
                writer.writeheader()
            for row in rows:
                writer.writerow(row)


def load_seen_trades() -> set[str]:
    if not SEEN_TRADES_PATH.exists():
        return set()
    try:
        return set(json.loads(SEEN_TRADES_PATH.read_text()))
    except (ValueError, OSError):
        return set()


def save_seen_trades(seen: set[str]) -> None:
    SEEN_TRADES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_TRADES_PATH.write_text(json.dumps(list(seen)[-MAX_SEEN_TRADES:]))


def fetch_wallet_trades() -> list[dict]:
    req = urllib.request.Request(
        TRADES_API_URL, headers={"Accept": "application/json", "User-Agent": "atlantis-btc5m-shadow/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def fetch_best_ask(token_id: str) -> tuple[Decimal | None, Decimal | None]:
    """Public order book read - no auth needed. Returns (best_ask_price,
    best_ask_size), the real price a marketable BUY would have to pay
    right now, same semantics as clob_client.py's get_best_price (BUY
    side) but via the public REST endpoint instead of the authenticated
    py_clob_client_v2 SDK."""
    req = urllib.request.Request(
        BOOK_API_URL.format(token_id=token_id),
        headers={"Accept": "application/json", "User-Agent": "atlantis-btc5m-shadow/0.1"},
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


def shadow_trade(trade: dict) -> None:
    tx_hash = str(trade.get("transactionHash", ""))
    token_id = str(trade.get("asset", ""))
    slug = str(trade.get("slug", ""))
    outcome = str(trade.get("outcome", ""))
    wallet_price = trade.get("price", "")
    wallet_size = trade.get("size", "")
    wallet_ts = int(trade.get("timestamp", 0))

    rows: list[dict] = []
    start = time.time()
    for target_delay in DELAY_CHECKPOINTS:
        wait = (wallet_ts + target_delay) - time.time()
        if wait > 0:
            time.sleep(wait)
        actual_delay = time.time() - wallet_ts
        best_ask, best_ask_size = fetch_best_ask(token_id)
        rows.append(
            {
                "trade_tx_hash": tx_hash,
                "window_slug": slug,
                "outcome": outcome,
                "wallet_price": str(wallet_price),
                "wallet_size": str(wallet_size),
                "wallet_timestamp": wallet_ts,
                "delay_seconds_target": target_delay,
                "delay_seconds_actual": round(actual_delay, 2),
                "best_ask": str(best_ask) if best_ask is not None else "",
                "best_ask_size": str(best_ask_size) if best_ask_size is not None else "",
                "observed_at": _now(),
            }
        )
    append_rows(rows)
    _log(f"btc5m-shadow: {len(rows)} observaciones para {tx_hash[:10]}... (tomo {time.time()-start:.1f}s)")


def main() -> None:
    established = SEEN_TRADES_PATH.exists()
    seen = load_seen_trades()
    _log("btc5m-shadow: proceso persistente arrancando (solo lectura, sin dinero real)")

    while True:
        trades = fetch_wallet_trades()
        candidates = [
            t
            for t in trades
            if t.get("transactionHash")
            and str(t["transactionHash"]) not in seen
            and str(t.get("side", "")).upper() == "BUY"
            and str(t.get("slug", "")).startswith("btc-updown-5m-")
        ]
        candidates.sort(key=lambda t: t.get("timestamp", 0))
        for t in candidates:
            seen.add(str(t["transactionHash"]))
            if established:
                threading.Thread(target=shadow_trade, args=(t,), daemon=True).start()
        if candidates:
            save_seen_trades(seen)
        established = True
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
