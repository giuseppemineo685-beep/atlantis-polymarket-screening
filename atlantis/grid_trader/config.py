from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class GridTraderConfig:
    usd_per_level: Decimal
    num_levels: int
    take_profit_pct: Decimal
    stop_loss_pct: Decimal
    flat_pos_min: Decimal
    flat_pos_max: Decimal
    flat_max_corr: Decimal
    max_concurrent_positions: int
    scan_interval_seconds: int
    poll_interval_seconds: int
    fee_rate: Decimal
    screener_snapshot_path: Path
    positions_path: Path
    log_path: Path

    @property
    def take_profit_usd(self) -> Decimal:
        return self.usd_per_level * self.num_levels * self.take_profit_pct / 100

    @property
    def stop_loss_usd(self) -> Decimal:
        return self.usd_per_level * self.num_levels * self.stop_loss_pct / 100


def load_config(path: Path | None = None) -> GridTraderConfig:
    path = path or Path(__file__).resolve().parent / "config.yaml"
    raw = yaml.safe_load(path.read_text())
    return GridTraderConfig(
        usd_per_level=Decimal(str(raw["usd_per_level"])),
        num_levels=int(raw["num_levels"]),
        take_profit_pct=Decimal(str(raw["take_profit_pct"])),
        stop_loss_pct=Decimal(str(raw["stop_loss_pct"])),
        flat_pos_min=Decimal(str(raw["flat_pos_min"])),
        flat_pos_max=Decimal(str(raw["flat_pos_max"])),
        flat_max_corr=Decimal(str(raw["flat_max_corr"])),
        max_concurrent_positions=int(raw["max_concurrent_positions"]),
        scan_interval_seconds=int(raw["scan_interval_seconds"]),
        poll_interval_seconds=int(raw["poll_interval_seconds"]),
        fee_rate=Decimal(str(raw["fee_rate"])),
        screener_snapshot_path=ROOT / raw["screener_snapshot_path"],
        positions_path=ROOT / raw["positions_path"],
        log_path=ROOT / raw["log_path"],
    )
