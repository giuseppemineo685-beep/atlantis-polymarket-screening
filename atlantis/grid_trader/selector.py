"""Picks ONE symbol for the flat-grid strategy and ONE for the
trend-grid strategy out of the existing grid_screener_snapshot.csv
(the same universe scan that already runs every 15 min on the VPS -
no separate fetch needed, just a different read of the same data).

Flat candidate: reuses the manual screener's own "bueno" scoring as-is
(atlantis/grid_screener/scoring.py) - that rubric already rewards
exactly what a flat grid wants (regimen=rango, mid-range position,
healthy volatility, liquidity). Highest score among regimen=="rango"
wins.

Trend candidate: the manual scoring can't be reused here - it actively
PENALIZES regimen=="fuerte" (that rubric is flat-grid-only). Trend
selection instead ranks by |efficiency_ratio| among regimen=="fuerte"
rows (straightest, most persistent recent move) with a liquidity floor,
and reads net_move_pct's sign to decide long-biased (uptrend) vs
short-biased (downtrend).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SelectedCandidate:
    symbol: str
    direction: str  # "long" | "short" ("long" always for flat)
    vol_pct: float
    pos_pct: float
    er: float
    net_move_pct: float
    liquidez: str
    score: int | None  # only meaningful for the flat candidate (manual score)


MIN_LIQUIDEZ = {"alto", "medio"}


def _read_snapshot(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def select_flat_candidate(snapshot_path: Path) -> SelectedCandidate | None:
    rows = _read_snapshot(snapshot_path)
    candidates = [
        r for r in rows
        if r.get("regimen") == "rango" and r.get("liquidez") in MIN_LIQUIDEZ
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda r: float(r.get("score") or 0), reverse=True)
    best = candidates[0]
    return SelectedCandidate(
        symbol=best["symbol"], direction="long",
        vol_pct=float(best.get("vol_pct") or 0), pos_pct=float(best.get("pos_pct") or 0),
        er=float(best.get("er") or 0), net_move_pct=float(best.get("net_move_pct") or 0),
        liquidez=best.get("liquidez", ""), score=int(float(best.get("score") or 0)),
    )


def select_trend_candidate(snapshot_path: Path) -> SelectedCandidate | None:
    rows = _read_snapshot(snapshot_path)
    candidates = [
        r for r in rows
        if r.get("regimen") == "fuerte" and r.get("liquidez") in MIN_LIQUIDEZ
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda r: float(r.get("er") or 0), reverse=True)
    best = candidates[0]
    net_move = float(best.get("net_move_pct") or 0)
    return SelectedCandidate(
        symbol=best["symbol"], direction="long" if net_move >= 0 else "short",
        vol_pct=float(best.get("vol_pct") or 0), pos_pct=float(best.get("pos_pct") or 0),
        er=float(best.get("er") or 0), net_move_pct=net_move,
        liquidez=best.get("liquidez", ""), score=None,
    )
