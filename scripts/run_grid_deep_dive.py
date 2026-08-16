"""'Screen 2' CLI - run this by hand with the 10-15 symbols already
shortlisted from the Binance copy-trade marketplace (Screen 1). Prints
a Market Quality / Futures Risk report per symbol and writes a CSV.

Usage: python3 scripts/run_grid_deep_dive.py SOLUSDT SNDKUSDT MUUSDT ...
"""

from __future__ import annotations

import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atlantis.grid_screener.binance_client import fetch_daily_klines  # noqa: E402
from atlantis.grid_screener.deep_dive import evaluar_profundo  # noqa: E402
from atlantis.grid_screener.metrics import CORR_WINDOW_DAYS, daily_log_returns  # noqa: E402

OUT_PATH = ROOT / "outputs" / "grid_deep_dive_snapshot.csv"
FIELDS = [
    "symbol", "market_quality", "futures_risk", "entry_timing", "regimen", "vol_pct",
    "pos_pct", "bb_width_pct", "volume_change_pct", "funding_rate_pct", "oi_change_pct",
    "long_short_ratio", "spread_pct", "btc_corr", "notes", "snapshot_at",
]


def main() -> None:
    symbols = [s.strip().upper() for s in sys.argv[1:] if s.strip()]
    if not symbols:
        print("uso: python3 scripts/run_grid_deep_dive.py SIMBOLO1 SIMBOLO2 ...")
        return

    btc_klines = fetch_daily_klines("BTCUSDT")
    if not btc_klines:
        print("no se pudo obtener BTCUSDT, abortando")
        return
    btc_returns = daily_log_returns(btc_klines, window_days=CORR_WINDOW_DAYS)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    rows = []
    for symbol in symbols:
        result = evaluar_profundo(symbol, btc_returns)
        time.sleep(0.2)
        if result is None:
            print(f"{symbol}: no se pudo evaluar (sin klines suficientes)")
            continue

        print(f"\n{result.symbol}")
        print(f"  MARKET QUALITY   {result.market_quality}/100")
        print(f"  FUTURES RISK     {result.futures_risk}/100")
        print(f"  ENTRY TIMING     {result.entry_timing.upper()}")
        print(f"  regimen={result.regimen}  vol={result.vol_pct:.2f}%  pos_en_rango={result.pos_pct:.0f}%  "
              f"BB_width={result.bb_width_pct:.2f}%  cambio_volumen={result.volume_change_pct:+.1f}%")
        funding = f"{result.funding_rate_pct:+.4f}%" if result.funding_rate_pct is not None else "?"
        oi = f"{result.oi_change_pct:+.1f}%" if result.oi_change_pct is not None else "?"
        ls = f"{result.long_short_ratio:.2f}" if result.long_short_ratio is not None else "?"
        spread = f"{result.spread_pct:.3f}%" if result.spread_pct is not None else "?"
        print(f"  funding={funding}  OI_24h={oi}  long/short={ls}  spread={spread}  corr_BTC={result.btc_corr:+.2f}")
        if result.notes:
            for note in result.notes:
                print(f"  - {note}")

        rows.append({
            "symbol": result.symbol, "market_quality": result.market_quality,
            "futures_risk": result.futures_risk, "entry_timing": result.entry_timing,
            "regimen": result.regimen, "vol_pct": f"{result.vol_pct:.3f}",
            "pos_pct": f"{result.pos_pct:.1f}", "bb_width_pct": f"{result.bb_width_pct:.3f}",
            "volume_change_pct": f"{result.volume_change_pct:.1f}",
            "funding_rate_pct": str(result.funding_rate_pct) if result.funding_rate_pct is not None else "",
            "oi_change_pct": str(result.oi_change_pct) if result.oi_change_pct is not None else "",
            "long_short_ratio": str(result.long_short_ratio) if result.long_short_ratio is not None else "",
            "spread_pct": str(result.spread_pct) if result.spread_pct is not None else "",
            "btc_corr": f"{result.btc_corr:.3f}", "notes": "; ".join(result.notes), "snapshot_at": now,
        })

    if rows:
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with OUT_PATH.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nescrito en {OUT_PATH}")


if __name__ == "__main__":
    main()
