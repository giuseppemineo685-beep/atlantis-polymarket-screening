#!/usr/bin/env python3
"""
Monitor de la wallet 0xeebde7a0e019a63e6b476eb425505b7b3e6eba30 ("Bonereaper").

Corre indefinidamente y registra, para cada compra nueva que hace la wallet:
- mercado (moneda, ventana 5-min, slug)
- lado comprado (Up/Down), precio pagado, tamano
- tiempo transcurrido dentro de la ventana de 5 min
- precio real de Chainlink (feed exacto que usa Polymarket para resolver) en el
  momento de la compra
- TWAP corriente (apertura -> ahora) segun ese mismo feed
- una vez resuelto el mercado, el ganador real y si la compra fue correcta

Fuentes:
- data-api.polymarket.com/trades  (polling, deteccion de trades nuevos)
- wss://ws-live-data.polymarket.com, topic crypto_prices_chainlink (precio real)
- clob.polymarket.com/markets/{conditionId}  (resultado real de cada mercado)

Salida: append-only JSONL en OUT_PATH. Pensado para correr 24h+ sin intervencion.
"""
import asyncio, json, re, time, urllib.request, threading, collections, os, sys

WALLET = "0xeebde7a0e019a63e6b476eb425505b7b3e6eba30"
COINS = {"Bitcoin": "btc/usd", "Solana": "sol/usd", "XRP": "xrp/usd", "Ethereum": "eth/usd"}
OUT_PATH = os.environ.get("MONITOR_OUT", "bonereaper_monitor.jsonl")
HEADERS = {"User-Agent": "Mozilla/5.0"}

# rolling price history per symbol: deque of (unix_ts, price)
price_history = {sym: collections.deque(maxlen=20000) for sym in COINS.values()}
price_lock = threading.Lock()

seen_trade_keys = set()
resolution_cache = {}  # conditionId -> winner outcome string


def load_seen_keys_from_disk():
    """Seed seen_trade_keys from whatever is already logged, so a restart
    (crash, VPS reboot, manual bounce) doesn't re-poll the last ~100 trades
    and log them again as 'new' before enough time has passed for them to
    have actually scrolled out of the API's window."""
    if not os.path.exists(OUT_PATH):
        return
    n = 0
    with open(OUT_PATH) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            key = (
                rec.get("transactionHash", "")
                + str(rec.get("trade_timestamp"))
                + str(rec.get("size"))
            )
            seen_trade_keys.add(key)
            n += 1
    log(f"seeded {n} already-logged trade keys from {OUT_PATH}")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def http_get_json(url, timeout=10):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def get_resolution(condition_id):
    if condition_id in resolution_cache:
        return resolution_cache[condition_id]
    try:
        d = http_get_json(f"https://clob.polymarket.com/markets/{condition_id}", timeout=8)
        tokens = d.get("tokens", [])
        winner = next((t["outcome"] for t in tokens if t.get("winner")), None)
        if winner:
            resolution_cache[condition_id] = winner
        return winner
    except Exception:
        return None


def price_at_or_before(symbol, ts):
    with price_lock:
        hist = price_history[symbol]
        # hist is append-ordered by arrival, roughly by time; scan from the end
        best = None
        for t, p in reversed(hist):
            if t <= ts:
                best = (t, p)
                break
        return best


def twap_since(symbol, start_ts, end_ts):
    with price_lock:
        vals = [p for t, p in price_history[symbol] if start_ts <= t <= end_ts]
    return sum(vals) / len(vals) if vals else None


def append_record(rec):
    with open(OUT_PATH, "a") as f:
        f.write(json.dumps(rec) + "\n")


def poll_trades_loop(poll_interval=8):
    log("trade poller: starting")
    while True:
        try:
            trades = http_get_json(
                f"https://data-api.polymarket.com/trades?user={WALLET}&limit=100", timeout=10
            )
        except Exception as e:
            log(f"trade poll error: {e}")
            time.sleep(poll_interval)
            continue

        for t in trades:
            key = t["transactionHash"] + str(t.get("timestamp")) + str(t.get("size"))
            if key in seen_trade_keys:
                continue
            seen_trade_keys.add(key)

            coin = next((c for c in COINS if c in t.get("title", "")), None)
            if coin is None:
                continue
            symbol = COINS[coin]

            m = re.search(r"-(\d{10})$", t.get("eventSlug", ""))
            window_start = int(m.group(1)) if m else None
            trade_ts = t["timestamp"]

            open_px = price_at_or_before(symbol, window_start) if window_start else None
            now_px = price_at_or_before(symbol, trade_ts)
            running_twap = (
                twap_since(symbol, window_start, trade_ts) if window_start else None
            )

            rec = {
                "logged_at": time.time(),
                "coin": coin,
                "symbol": symbol,
                "conditionId": t["conditionId"],
                "eventSlug": t.get("eventSlug"),
                "window_start": window_start,
                "elapsed_in_window_s": (trade_ts - window_start) if window_start else None,
                "trade_timestamp": trade_ts,
                "side_bought": t["outcome"],
                "price_paid": t["price"],
                "size": t["size"],
                "chainlink_open_price": open_px[1] if open_px else None,
                "chainlink_open_price_ts": open_px[0] if open_px else None,
                "chainlink_price_at_trade": now_px[1] if now_px else None,
                "chainlink_price_at_trade_ts": now_px[0] if now_px else None,
                "chainlink_running_twap": running_twap,
                "transactionHash": t["transactionHash"],
                # filled in later once the market resolves:
                "winner": None,
                "correct": None,
            }
            append_record(rec)
            log(
                f"NEW TRADE  {coin:10s} {t['outcome']:5s} paid={t['price']:.3f} "
                f"size={t['size']:.1f}  elapsed={rec['elapsed_in_window_s']}s "
                f"chainlink_now={rec['chainlink_price_at_trade']}"
            )
        time.sleep(poll_interval)


def resolve_backfill_loop(interval=60):
    """Periodically re-scan the jsonl for records missing a resolution and fill them in."""
    log("resolution backfill: starting")
    while True:
        time.sleep(interval)
        try:
            if not os.path.exists(OUT_PATH):
                continue
            lines = open(OUT_PATH).readlines()
            changed = False
            out = []
            for line in lines:
                rec = json.loads(line)
                if rec.get("winner") is None:
                    winner = get_resolution(rec["conditionId"])
                    if winner:
                        rec["winner"] = winner
                        rec["correct"] = rec["side_bought"] == winner
                        changed = True
                out.append(rec)
            if changed:
                with open(OUT_PATH, "w") as f:
                    for rec in out:
                        f.write(json.dumps(rec) + "\n")
                n_resolved = sum(1 for r in out if r.get("winner"))
                log(f"resolution backfill: {n_resolved}/{len(out)} records resolved")
        except Exception as e:
            log(f"backfill error: {e}")


async def price_ws_loop():
    import websockets

    url = "wss://ws-live-data.polymarket.com"
    while True:
        try:
            async with websockets.connect(url, additional_headers=HEADERS) as ws:
                subs = [
                    {"topic": "crypto_prices_chainlink", "type": "*", "filters": f'{{"symbol":"{sym}"}}'}
                    for sym in COINS.values()
                ]
                await ws.send(json.dumps({"action": "subscribe", "subscriptions": subs}))
                log(f"price ws: connected & subscribed to {list(COINS.values())}")

                async def pinger():
                    while True:
                        await asyncio.sleep(5)
                        await ws.send("PING")

                ping_task = asyncio.create_task(pinger())
                try:
                    async for msg in ws:
                        if not msg:
                            continue
                        try:
                            d = json.loads(msg)
                        except Exception:
                            continue
                        payload = d.get("payload")
                        if not isinstance(payload, dict):
                            continue
                        sym = payload.get("symbol")
                        ts_raw = payload.get("timestamp")
                        val = payload.get("value")
                        if sym is None or ts_raw is None or val is None:
                            continue
                        ts = ts_raw // 1000
                        with price_lock:
                            price_history[sym].append((ts, val))
                finally:
                    ping_task.cancel()
        except Exception as e:
            log(f"price ws error, reconnecting in 5s: {e}")
            await asyncio.sleep(5)


def main():
    load_seen_keys_from_disk()
    t1 = threading.Thread(target=poll_trades_loop, daemon=True)
    t2 = threading.Thread(target=resolve_backfill_loop, daemon=True)
    t1.start()
    t2.start()
    asyncio.run(price_ws_loop())


if __name__ == "__main__":
    main()
