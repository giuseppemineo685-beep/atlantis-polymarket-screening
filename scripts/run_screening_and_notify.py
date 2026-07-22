from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "notified_signals.json"
TRADE_LOG_PATH = ROOT / "outputs" / "trade_log.csv"
TRADE_LOG_FIELDS = [
    "condition_id",
    "asset",
    "title",
    "outcome",
    "traders",
    "supporting_traders",
    "date_first_seen",
    "entry_price",
    "current_price",
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


def load_state() -> set[str]:
    if not STATE_PATH.exists():
        return set()
    return set(json.loads(STATE_PATH.read_text()))


def save_state(keys: set[str]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(sorted(keys), indent=2))


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


def get_market_resolution(condition_id: str, asset: str) -> Decimal | None:
    """Return the final settlement price (0 or 1) for `asset` if its market
    has closed, else None if still open/unresolved."""
    url = f"https://gamma-api.polymarket.com/markets?condition_ids={condition_id}"
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "atlantis-screening/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        print(f"  aviso: no se pudo consultar resolucion de {condition_id}: {exc}", file=sys.stderr)
        return None

    if not data:
        return None
    market = data[0]
    if not market.get("closed"):
        return None

    try:
        clob_token_ids = json.loads(market.get("clobTokenIds", "[]"))
        outcome_prices = json.loads(market.get("outcomePrices", "[]"))
    except (json.JSONDecodeError, TypeError):
        return None

    if asset not in clob_token_ids:
        return None
    idx = clob_token_ids.index(asset)
    try:
        return Decimal(outcome_prices[idx])
    except (IndexError, InvalidOperation):
        return None


def update_trade_log(log: dict[str, dict], copy_signals: list[dict]) -> dict[str, dict]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    current_keys = set()

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
                "supporting_traders": signal["supporting_traders"],
                "date_first_seen": now,
                "entry_price": signal["avg_entry_price"],
                "current_price": signal["current_price"],
                "status": "OPEN",
                "exit_price": "",
                "pct_return": "",
                "last_updated": now,
            }
            print(f"  + nuevo trade en log: {signal['title']} -> {signal['outcome']}")
        else:
            row = log[key]
            if row["status"] == "OPEN":
                row["current_price"] = signal["current_price"]
                row["last_updated"] = now

    # Anything OPEN but no longer in the active COPY list may have resolved.
    for key, row in log.items():
        if row["status"] != "OPEN" or key in current_keys:
            continue
        final_price = get_market_resolution(row["condition_id"], row["asset"])
        if final_price is None:
            continue  # still unresolved (or just fell out of consensus); leave OPEN
        entry_price = Decimal(row["entry_price"])
        pct_return = ((final_price / entry_price) - 1) * Decimal("100") if entry_price > 0 else Decimal("0")
        row["status"] = "WIN" if final_price == Decimal("1") else "LOSS"
        row["exit_price"] = str(final_price)
        row["pct_return"] = f"{pct_return:.2f}"
        row["last_updated"] = now
        print(f"  = resuelto: {row['title']} -> {row['status']} ({pct_return:.2f}%)")

    return log


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


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

    trade_log = load_trade_log()
    trade_log = update_trade_log(trade_log, copy_signals)
    save_trade_log(trade_log)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
