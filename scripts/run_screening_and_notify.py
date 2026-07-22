from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state" / "notified_signals.json"

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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
