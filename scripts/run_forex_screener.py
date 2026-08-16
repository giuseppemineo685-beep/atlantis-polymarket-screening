"""Forex regime screener - runs every 15 min via cron, feeds
scripts/run_forex_grid_trader_paper.py the same way
scripts/run_grid_screener.py feeds the crypto bot."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlantis.forex.config import load_config  # noqa: E402
from atlantis.forex.screener import run_screener  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def main() -> None:
    config = load_config()
    print(f"[{_now()}] forex-screener: arrancando")
    n = run_screener(config.screener_snapshot_path)
    print(f"[{_now()}] forex-screener: listo - {n} pares evaluados, escrito en {config.screener_snapshot_path}")


if __name__ == "__main__":
    main()
