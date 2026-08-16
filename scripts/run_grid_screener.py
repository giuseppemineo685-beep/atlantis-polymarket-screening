"""Automatic grid-bot screener - pulls the whole liquid Binance USDS-M
perpetual universe, scores every pair with the exact same rubric as the
manual tool (atlantis/grid_screener/scoring.py), and writes a fresh
ranked snapshot. Meant to run periodically via cron (see
run_grid_screener_supervisor's crontab entry), not continuously - this
is a decision-support snapshot, not a trading bot with open positions.
"""

from __future__ import annotations

import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlantis.grid_screener.binance_client import fetch_daily_klines, fetch_screenable_universe  # noqa: E402
from atlantis.grid_screener.metrics import (  # noqa: E402
    correlacion_bucket,
    daily_log_returns,
    daily_volatility_pct,
    efficiency_ratio,
    liquidez_bucket,
    net_move_pct,
    pearson,
    position_in_range_pct,
    regimen_from_er,
)
from atlantis.grid_screener.scoring import Candidate, evaluar  # noqa: E402

OUT_PATH = ROOT / "outputs" / "grid_screener_snapshot.csv"
FIELDS = [
    "symbol", "score", "verdict", "vol_pct", "regimen", "er", "net_move_pct", "pos_pct",
    "liquidez", "quote_volume_24h", "correlacion", "corr_value",
    "flags", "snapshot_at",
]

SLEEP_BETWEEN_CALLS = 0.15


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _log(msg: str) -> None:
    print(f"[{_now()}] {msg}")


def main() -> None:
    _log("grid-screener: arrancando")
    universe = fetch_screenable_universe()
    _log(f"grid-screener: {len(universe)} pares por encima del piso de liquidez")
    if not universe:
        _log("grid-screener: no se pudo obtener el universo, abortando esta corrida")
        return

    btc_klines = fetch_daily_klines("BTCUSDT")
    time.sleep(SLEEP_BETWEEN_CALLS)
    if not btc_klines:
        _log("grid-screener: no se pudo obtener BTCUSDT, abortando esta corrida")
        return
    btc_returns = daily_log_returns(btc_klines)

    rows = []
    for i, t in enumerate(universe):
        klines = fetch_daily_klines(t.symbol)
        time.sleep(SLEEP_BETWEEN_CALLS)
        if not klines or len(klines) < 15:
            continue

        vol_pct = daily_volatility_pct(klines)
        er = efficiency_ratio(klines)
        regimen = regimen_from_er(er)
        move_pct = net_move_pct(klines)
        pos_pct = position_in_range_pct(klines)
        liquidez = liquidez_bucket(t.quote_volume_24h)
        returns = daily_log_returns(klines)
        corr_value = pearson(returns, btc_returns) if t.symbol != "BTCUSDT" else 1.0
        correlacion = correlacion_bucket(corr_value)

        candidate = Candidate(
            symbol=t.symbol, vol_pct=vol_pct, regimen=regimen, pos_pct=pos_pct,
            liquidez=liquidez, correlacion=correlacion,
        )
        ev = evaluar(candidate)

        rows.append({
            "symbol": t.symbol, "score": ev.score, "verdict": ev.verdict,
            "vol_pct": f"{vol_pct:.3f}", "regimen": regimen, "er": f"{er:.4f}",
            "net_move_pct": f"{move_pct:.2f}", "pos_pct": f"{pos_pct:.1f}",
            "liquidez": liquidez, "quote_volume_24h": f"{t.quote_volume_24h:.0f}",
            "correlacion": correlacion, "corr_value": f"{corr_value:.3f}",
            "flags": "; ".join(f.text for f in ev.flags),
            "snapshot_at": _now(),
        })

        if i % 50 == 0:
            _log(f"grid-screener: procesados {i}/{len(universe)}")

    rows.sort(key=lambda r: r["score"], reverse=True)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    buenos = sum(1 for r in rows if r["verdict"] == "bueno")
    _log(f"grid-screener: listo - {len(rows)} pares evaluados, {buenos} 'bueno', escrito en {OUT_PATH}")


if __name__ == "__main__":
    main()
