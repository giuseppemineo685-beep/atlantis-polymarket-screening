from __future__ import annotations

import csv
from pathlib import Path

LOG_FIELDS = ["timestamp", "symbol", "strategy", "action", "reason", "price", "realized_profit", "trades"]


def log_event(path: Path, *, timestamp: str, symbol: str, strategy: str, action: str,
              reason: str, price: str, realized_profit: str = "", trades: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({
            "timestamp": timestamp, "symbol": symbol, "strategy": strategy, "action": action,
            "reason": reason, "price": price, "realized_profit": realized_profit, "trades": trades,
        })
