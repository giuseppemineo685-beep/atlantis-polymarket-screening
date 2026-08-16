"""Classifies every OANDA currency pair's CURRENT regime - the forex
analog of atlantis/grid_screener/run_grid_screener.py, but simpler on
purpose: forex v1 has NOT validated a BTC-correlation-style predictive
filter yet (that finding was crypto-specific, discovered by correlating
each score component against real 3-month outcomes - see
docs/GRID_TRADER_STRATEGIES.md). Until forex has its own real
walk-forward data to check what predicts, this only classifies regime
(rango/leve/fuerte) + position in range - both scale-invariant
measures that should transfer across asset classes, unlike the
crypto-calibrated volatility/liquidity thresholds which are deliberately
NOT reused here.
"""

from __future__ import annotations

import csv
import time
from datetime import datetime, timezone
from pathlib import Path

from atlantis.forex.oanda_client import candles_to_klines, fetch_candles, fetch_currency_instruments
from atlantis.grid_screener.metrics import (
    daily_volatility_pct,
    efficiency_ratio,
    net_move_pct,
    position_in_range_pct,
    regimen_from_er,
)

FIELDS = ["instrument", "regimen", "er", "vol_pct", "pos_pct", "net_move_pct", "snapshot_at"]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def run_screener(out_path: Path, sleep_between_calls: float = 0.15) -> int:
    instruments = fetch_currency_instruments()
    rows = []
    for inst in instruments:
        candles = fetch_candles(inst.name, granularity="D", count=35)
        time.sleep(sleep_between_calls)
        if not candles:
            continue
        klines = candles_to_klines(candles)
        if len(klines) < 15:
            continue

        er = efficiency_ratio(klines)
        rows.append({
            "instrument": inst.name, "regimen": regimen_from_er(er), "er": f"{er:.4f}",
            "vol_pct": f"{daily_volatility_pct(klines):.4f}", "pos_pct": f"{position_in_range_pct(klines):.1f}",
            "net_move_pct": f"{net_move_pct(klines):.2f}", "snapshot_at": _now(),
        })

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)
