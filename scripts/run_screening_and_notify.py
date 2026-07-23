from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from atlantis.config import load_settings  # noqa: E402
from atlantis.polymarket.client import build_client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "notified_signals.json"
EXIT_STATE_PATH = ROOT / "state" / "exit_alerts_sent.json"
PARTIAL_STATE_PATH = ROOT / "state" / "partial_alerts_sent.json"
TRADE_LOG_PATH = ROOT / "outputs" / "trade_log.csv"
TRADE_LOG_FIELDS = [
    "condition_id",
    "asset",
    "title",
    "outcome",
    "traders",
    "entry_supporting_traders",
    "supporting_traders",
    "date_first_seen",
    "entry_price",
    "current_price",
    "consensus_active",
    "status",
    "exit_price",
    "pct_return",
    "last_updated",
]

VERDICT_MAP = {
    "WATCHLIST_STRONG": "A",
    "WATCHLIST": "B",
    "PAPER_ONLY": "C",
    "REJECT": "REJECT",
}


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT)


def convert_to_portfolio_format() -> None:
    with open(ROOT / "outputs" / "watchlist_evaluation.csv") as f:
        rows = list(csv.DictReader(f))

    out_rows = []
    for r in rows:
        verdict = VERDICT_MAP.get(r["verdict"], "REJECT")
        sports_trades = int(r["sports_trades"] or 0)
        sports_volume = float(r["sports_volume"] or 0)
        recent_sports_14d = int(r["recent_sports_14d"] or 0)
        bot_score = float(r["bot_score"] or 0)

        confidence_score = min(100, sports_trades / 5 + recent_sports_14d)
        copy_score = min(100, (sports_volume / 20000) + confidence_score * 0.3)
        risk_score = bot_score

        out_rows.append(
            {
                "username": r["label"],
                "wallet_address": r["wallet"],
                "verdict": verdict,
                "copy_score": round(copy_score, 2),
                "confidence_score": round(confidence_score, 2),
                "risk_score": round(risk_score, 2),
            }
        )

    out_path = ROOT / "outputs" / "portfolio_traders.csv"
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["username", "wallet_address", "verdict", "copy_score", "confidence_score", "risk_score"]
        )
        writer.writeheader()
        writer.writerows(out_rows)


def load_state_path(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(json.loads(path.read_text()))


def save_state_path(path: Path, keys: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(keys), indent=2))


def load_state() -> set[str]:
    return load_state_path(STATE_PATH)


def save_state(keys: set[str]) -> None:
    save_state_path(STATE_PATH, keys)


def signal_key(row: dict) -> str:
    return f"{row['condition_id']}|{row['asset']}"


def load_trade_log() -> dict[str, dict]:
    if not TRADE_LOG_PATH.exists():
        return {}
    with TRADE_LOG_PATH.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return {f"{r['condition_id']}|{r['asset']}": r for r in rows}


def save_trade_log(log: dict[str, dict]) -> None:
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Sort: OPEN trades first (most actionable), then most recently updated first
    rows = sorted(
        log.values(),
        key=lambda r: (r["status"] != "OPEN", r.get("last_updated", "")),
        reverse=False,
    )
    with TRADE_LOG_PATH.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_LOG_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def get_market_price_info(condition_id: str, asset: str) -> tuple[Decimal | None, bool]:
    """Return (current_price, is_closed) for `asset` in this market.

    current_price reflects the live market price whether the market is open
    or closed (Polymarket keeps `outcomePrices` current either way) - this
    lets us keep a trade's displayed price fresh even after it drops out of
    consensus, instead of freezing on the last price we happened to see.
    """
    url = f"https://gamma-api.polymarket.com/markets?condition_ids={condition_id}"
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "atlantis-screening/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        print(f"  aviso: no se pudo consultar {condition_id}: {exc}", file=sys.stderr)
        return None, False

    if not data:
        return None, False
    market = data[0]
    closed = bool(market.get("closed"))

    try:
        clob_token_ids = json.loads(market.get("clobTokenIds", "[]"))
        outcome_prices = json.loads(market.get("outcomePrices", "[]"))
    except (json.JSONDecodeError, TypeError):
        return None, closed

    if asset not in clob_token_ids:
        return None, closed
    idx = clob_token_ids.index(asset)
    try:
        return Decimal(outcome_prices[idx]), closed
    except (IndexError, InvalidOperation):
        return None, closed


def load_label_to_wallet() -> dict[str, str]:
    with open(ROOT / "outputs" / "portfolio_traders.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    return {r["username"]: r["wallet_address"] for r in rows}


def count_traders_still_holding(condition_id: str, asset: str, trader_labels: list[str], label_to_wallet: dict[str, str], client) -> int:
    """How many of the originally-supporting traders still hold this exact
    position right now, checked directly against /positions - independent of
    whatever price-band filter active-portfolio applies. This is what tells
    a real exit (trader sold) apart from a signal merely aging out of our
    0.05-0.90 price window as a market nears resolution."""
    held = 0
    for label in trader_labels:
        wallet = label_to_wallet.get(label)
        if not wallet:
            continue
        try:
            positions = client.get_user_positions(wallet_address=wallet, limit=500)
        except Exception as exc:
            print(f"    aviso: no se pudo chequear posicion de {label}: {exc}", file=sys.stderr)
            continue
        match = next(
            (p for p in positions if p.get("conditionId") == condition_id and p.get("asset") == asset),
            None,
        )
        if match and float(match.get("size") or 0) > 0:
            held += 1
    return held


MIN_CONSENSUS = 2


def apply_trader_change(row: dict, live_supporting: int, live_price: Decimal | None, now: str) -> str | None:
    """Update `row` in place given the current live count of supporting
    traders and live price. Returns the alert tier to raise for this run:
    None, "partial", "exit", or "closed" (closed also finalizes the trade).

    Rules:
    - If a MAJORITY of the traders present when we first logged this trade
      have since exited (e.g. 1 of 2, or 2 of 3), and the position is
      currently in profit, we close it right there and lock in the gain -
      waiting for the market to fully resolve risks giving that gain back
      once the smart money has already left.
    - Same majority exit but NOT in profit: don't force a loss - flag it as
      a strong "consider exiting yourself" alert but leave it open.
    - Any exit below majority: a softer "conviction reduced" notice.
    """
    entry_supporting = int(row.get("entry_supporting_traders") or row.get("supporting_traders") or live_supporting)
    previous_supporting = int(row.get("supporting_traders") or entry_supporting)

    if live_price is not None:
        row["current_price"] = str(live_price)
    row["supporting_traders"] = live_supporting
    row["last_updated"] = now

    exited_from_entry = entry_supporting - live_supporting
    majority_needed = math.ceil(entry_supporting / 2)

    if exited_from_entry >= majority_needed and live_supporting < entry_supporting:
        entry_price = Decimal(row["entry_price"])
        in_profit = live_price is not None and live_price > entry_price
        if in_profit:
            pct_return = ((live_price / entry_price) - 1) * Decimal("100") if entry_price > 0 else Decimal("0")
            row["status"] = "CLOSED"
            row["exit_price"] = str(live_price)
            row["pct_return"] = f"{pct_return:.2f}"
            row["consensus_active"] = "no"
            return "closed"
        row["consensus_active"] = "no"
        return "exit"

    if live_supporting < previous_supporting:
        row["consensus_active"] = "yes" if live_supporting >= MIN_CONSENSUS else "no"
        return "partial"

    row["consensus_active"] = "yes" if live_supporting >= MIN_CONSENSUS else row.get("consensus_active", "yes")
    return None


def update_trade_log(
    log: dict[str, dict], copy_signals: list[dict], label_to_wallet: dict[str, str], client
) -> tuple[dict[str, dict], list[dict], list[dict], list[dict]]:
    """Update the persistent trade log. Returns (log, exit_alerts,
    partial_alerts, closed_alerts) - see apply_trader_change for the rules
    behind each tier."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    current_keys = set()
    exit_alerts = []
    partial_alerts = []
    closed_alerts = []

    for signal in copy_signals:
        key = signal_key(signal)
        current_keys.add(key)
        if key not in log:
            log[key] = {
                "condition_id": signal["condition_id"],
                "asset": signal["asset"],
                "title": signal["title"],
                "outcome": signal["outcome"],
                "traders": signal["traders"],
                "entry_supporting_traders": signal["supporting_traders"],
                "supporting_traders": signal["supporting_traders"],
                "date_first_seen": now,
                "entry_price": signal["avg_entry_price"],
                "current_price": signal["current_price"],
                "consensus_active": "yes",
                "status": "OPEN",
                "exit_price": "",
                "pct_return": "",
                "last_updated": now,
            }
            print(f"  + nuevo trade en log: {signal['title']} -> {signal['outcome']}")
        else:
            row = log[key]
            if row["status"] == "OPEN":
                previous_supporting = int(row.get("supporting_traders") or 0)
                tier = apply_trader_change(row, int(signal["supporting_traders"]), Decimal(signal["current_price"]), now)
                if tier == "closed":
                    closed_alerts.append(row)
                    print(f"  = cerrado en ganancia: {row['title']} ({row['pct_return']}%)")
                elif tier == "exit":
                    exit_alerts.append(row)
                    print(f"  ! mayoria salio, sin ganancia: {row['title']}")
                elif tier == "partial":
                    partial_alerts.append({**row, "previous_supporting": previous_supporting})
                    print(f"  ~ salida parcial: {row['title']} -> {previous_supporting} a {row['supporting_traders']} traders")

    # Anything OPEN but no longer backed by consensus: check if the market
    # actually resolved (finalize WIN/LOSS), or just refresh its live price
    # and flag that the traders are no longer aligned on it.
    for key, row in log.items():
        if row["status"] != "OPEN" or key in current_keys:
            continue

        price, closed = get_market_price_info(row["condition_id"], row["asset"])

        if closed and price is not None and price in (Decimal("0"), Decimal("1")):
            entry_price = Decimal(row["entry_price"])
            pct_return = ((price / entry_price) - 1) * Decimal("100") if entry_price > 0 else Decimal("0")
            row["status"] = "WIN" if price == Decimal("1") else "LOSS"
            row["exit_price"] = str(price)
            row["current_price"] = str(price)
            row["pct_return"] = f"{pct_return:.2f}"
            row["consensus_active"] = "no"
            row["last_updated"] = now
            print(f"  = resuelto: {row['title']} -> {row['status']} ({pct_return:.2f}%)")
            continue

        # Still unresolved. It dropped out of the active-portfolio COPY list,
        # but that also happens when a price walks outside our 0.05-0.90
        # filter band as a market nears resolution - that is NOT the same as
        # traders selling. Check their actual positions to tell those apart.
        trader_labels = [t.strip() for t in row["traders"].split(",") if t.strip()]
        previous_supporting = int(row.get("supporting_traders") or len(trader_labels))
        still_holding = count_traders_still_holding(row["condition_id"], row["asset"], trader_labels, label_to_wallet, client)

        tier = apply_trader_change(row, still_holding, price, now)
        if tier == "closed":
            closed_alerts.append(row)
            print(f"  = cerrado en ganancia: {row['title']} ({row['pct_return']}%)")
        elif tier == "exit":
            exit_alerts.append(row)
            print(
                f"  ! salida real: {row['title']} -> {row['outcome']} "
                f"(solo {still_holding} traders siguen, precio actual {row['current_price']})"
            )
        elif tier == "partial":
            partial_alerts.append({**row, "previous_supporting": previous_supporting})
            print(f"  ~ salida parcial: {row['title']} -> {previous_supporting} a {still_holding} traders")
        else:
            print(f"  ~ fuera del filtro de precio pero {still_holding}/{len(trader_labels)} siguen dentro: {row['title']}")

    return log, exit_alerts, partial_alerts, closed_alerts


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def format_closed_message(row: dict) -> str:
    return (
        f"✅ <b>CERRADO — ganancia asegurada</b>\n"
        f"{row['title']}\n"
        f"Resultado: <b>{row['outcome']}</b>\n"
        f"Entrada: {row['entry_price']}  →  Salida: {row['exit_price']}  ({row['pct_return']}%)\n"
        f"La mayoría de los traders que respaldaban esto ya salieron, así que cerramos con ganancia "
        f"en vez de esperar a que resuelva el mercado."
    )


def format_partial_exit_message(row: dict) -> str:
    prev = row.get("previous_supporting", "?")
    now_count = row.get("supporting_traders", "?")
    return (
        f"🔵 <b>UN TRADER SALIÓ — sigue habiendo consenso</b>\n"
        f"{row['title']}\n"
        f"Resultado: <b>{row['outcome']}</b>\n"
        f"Apoyo: bajó de {prev} a {now_count} traders  |  Precio actual: {row['current_price']}\n"
        f"Todavía hay consenso mínimo, pero la convicción bajó — no es una recomendación de salida, solo aviso."
    )


def format_exit_message(row: dict) -> str:
    entry = row.get("entry_price", "")
    current = row.get("current_price", "")
    try:
        pct = (float(current) / float(entry) - 1) * 100 if entry and float(entry) > 0 else 0.0
        pct_txt = f"{pct:+.1f}%"
    except (ValueError, ZeroDivisionError):
        pct_txt = "—"
    return (
        f"🟡 <b>CONSENSO ROTO — considerá salir</b>\n"
        f"{row['title']}\n"
        f"Resultado: <b>{row['outcome']}</b>\n"
        f"Entrada: {entry}  |  Precio actual: {current} ({pct_txt})\n"
        f"Los traders que respaldaban esta posición ya no coinciden. El mercado sigue abierto (no resolvió), "
        f"pero perdió la señal que la sostenía."
    )


def format_signal_message(row: dict) -> str:
    return (
        f"🟢 <b>NUEVA SEÑAL COPY</b>\n"
        f"{row['title']}\n"
        f"Resultado: <b>{row['outcome']}</b>\n"
        f"Precio: {row['current_price']}  |  Traders de acuerdo: {row['supporting_traders']}\n"
        f"Convicción: {row['conviction']}  |  Stake sugerido: ${row['stake']}\n"
        f"Traders: {row['traders']}"
    )


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID en el entorno", file=sys.stderr)
        return 1

    try:
        return run_pipeline(token, chat_id)
    except Exception as exc:
        print(f"Error fatal en el pipeline: {exc}", file=sys.stderr)
        try:
            send_telegram(
                token,
                chat_id,
                f"🔴 <b>El screening falló esta corrida</b>\n{type(exc).__name__}: {exc}\n"
                f"La próxima corrida programada lo va a reintentar solo.",
            )
        except Exception as notify_exc:
            print(f"Ademas fallo el aviso por Telegram: {notify_exc}", file=sys.stderr)
        return 1


def run_pipeline(token: str, chat_id: str) -> int:
    run(
        [
            sys.executable,
            "-B",
            "-m",
            "atlantis.cli",
            "evaluate-watchlist",
            "--wallets-csv",
            "inputs/approved_wallets.csv",
            "--statuses",
            "approved,paper_only",
            "--since-days",
            "30",
            "--csv",
            "outputs/watchlist_evaluation.csv",
        ]
    )

    convert_to_portfolio_format()

    run(
        [
            sys.executable,
            "-B",
            "-m",
            "atlantis.cli",
            "active-portfolio",
            "--traders-csv",
            "outputs/portfolio_traders.csv",
            "--min-verdict",
            "B",
            "--bankroll",
            "1000",
            "--csv",
            "outputs/active_portfolio_signals.csv",
        ]
    )

    with open(ROOT / "outputs" / "active_portfolio_signals.csv") as f:
        signals = list(csv.DictReader(f))

    copy_signals = [s for s in signals if s.get("action") == "COPY"]

    seen = load_state()
    new_signals = [s for s in copy_signals if signal_key(s) not in seen]

    print(f"Señales COPY totales: {len(copy_signals)}  |  Nuevas: {len(new_signals)}")

    for signal in new_signals:
        try:
            send_telegram(token, chat_id, format_signal_message(signal))
        except Exception as exc:
            print(f"Error enviando notificacion: {exc}", file=sys.stderr)

    all_current_keys = {signal_key(s) for s in copy_signals}
    save_state(seen | all_current_keys)

    settings = load_settings()
    client = build_client(settings)
    label_to_wallet = load_label_to_wallet()

    trade_log = load_trade_log()
    trade_log, exit_alerts, partial_alerts, closed_alerts = update_trade_log(trade_log, copy_signals, label_to_wallet, client)

    print(f"Cierres por mayoria en ganancia: {len(closed_alerts)}")
    for row in closed_alerts:
        try:
            send_telegram(token, chat_id, format_closed_message(row))
        except Exception as exc:
            print(f"Error enviando alerta de cierre: {exc}", file=sys.stderr)

    exit_seen = load_state_path(EXIT_STATE_PATH)
    new_exit_alerts = [row for row in exit_alerts if signal_key(row) not in exit_seen]
    print(f"Alertas de salida (consenso roto): {len(exit_alerts)}  |  Nuevas: {len(new_exit_alerts)}")
    for row in new_exit_alerts:
        try:
            send_telegram(token, chat_id, format_exit_message(row))
        except Exception as exc:
            print(f"Error enviando alerta de salida: {exc}", file=sys.stderr)
    save_state_path(EXIT_STATE_PATH, exit_seen | {signal_key(row) for row in exit_alerts})

    # Dedup partial alerts per (signal, new count) so a further step down
    # (e.g. 3 -> 2, later 2 -> 1) still notifies again at each level.
    partial_seen = load_state_path(PARTIAL_STATE_PATH)
    partial_keys = {f"{signal_key(row)}|{row['supporting_traders']}": row for row in partial_alerts}
    new_partial_alerts = [row for k, row in partial_keys.items() if k not in partial_seen]
    print(f"Alertas de salida parcial: {len(partial_alerts)}  |  Nuevas: {len(new_partial_alerts)}")
    for row in new_partial_alerts:
        try:
            send_telegram(token, chat_id, format_partial_exit_message(row))
        except Exception as exc:
            print(f"Error enviando alerta parcial: {exc}", file=sys.stderr)
    save_state_path(PARTIAL_STATE_PATH, partial_seen | set(partial_keys.keys()))

    save_trade_log(trade_log)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
