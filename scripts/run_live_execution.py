"""Entry point for the Finland execution cron. Reads the intents queue
(written by the German screening cron via git sync), processes it in
dry-run or live mode depending on state/live_trading_status.json, and
pushes the results back. Deliberately separate from
scripts/run_screening_and_notify.py - see atlantis/services/live_intents.py
for why the two must never share a process.
"""

from __future__ import annotations

import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlantis.live.config import load_live_settings  # noqa: E402
from atlantis.live.kill_switch import evaluate_and_maybe_trip  # noqa: E402
from atlantis.services.live_execution import format_execution_summary, process_pending_intents  # noqa: E402


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def main() -> int:
    settings = load_live_settings()
    tripped = evaluate_and_maybe_trip(settings)
    if tripped:
        print("AVISO: kill switch activo - no se procesan compras nuevas.", file=sys.stderr)

    summary = process_pending_intents(settings=settings)
    print(format_execution_summary(summary))

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat_id:
        for notification in summary.notifications:
            try:
                send_telegram(token, chat_id, notification)
            except Exception as exc:
                print(f"Error enviando notificacion de ejecucion real: {exc}", file=sys.stderr)
    elif summary.notifications:
        print(
            "AVISO: hay notificaciones de ejecucion real pero faltan "
            "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID en el entorno.",
            file=sys.stderr,
        )

    return 1 if summary.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
