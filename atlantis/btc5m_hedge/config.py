"""Loads atlantis/btc5m_hedge/config.yaml into typed, immutable config
objects - every tunable the rest of this package uses lives in that file,
nothing is hardcoded in portfolio.py/optimizer.py/risk.py."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"


@dataclass(frozen=True)
class MarketConfig:
    window_seconds: int
    poll_interval_seconds: float


@dataclass(frozen=True)
class RiskConfig:
    max_total_exposure_usd: Decimal
    max_exposure_per_market_usd: Decimal
    max_directional_imbalance_shares: Decimal
    minimum_guaranteed_profit_usd: Decimal
    minimum_guaranteed_roi_pct: Decimal
    max_order_size_usd: Decimal
    minimum_time_remaining_seconds: float
    stop_new_entries_seconds_before_resolution: float
    min_fill_ratio: Decimal
    max_forced_hedge_loss_usd: Decimal

    @property
    def entry_cutoff_seconds(self) -> float:
        """The effective "stop trying new entries" threshold - whichever
        of the two configured guards is more conservative (larger)."""
        return max(self.minimum_time_remaining_seconds, self.stop_new_entries_seconds_before_resolution)


@dataclass(frozen=True)
class OptimizerConfig:
    candidate_order_sizes_usd: tuple[Decimal, ...]
    max_opening_order_usd: Decimal
    cheap_open_threshold: Decimal
    imbalance_penalty_lambda: Decimal
    objective: str  # "guaranteed_profit" | "score"


@dataclass(frozen=True)
class HedgeTimingConfig:
    profit_hedge_only_seconds: float
    defensive_hedge_start_seconds: float
    emergency_hedge_start_seconds: float
    min_worst_case_improvement_pct: Decimal
    defensive_share_quantities: tuple[Decimal, ...]
    defensive_imbalance_fractions: tuple[Decimal, ...]
    price_runaway_window_samples: int
    price_runaway_min_move: Decimal
    price_runaway_max_pullback: Decimal


@dataclass(frozen=True)
class FeesConfig:
    taker_fee_pct: Decimal


@dataclass(frozen=True)
class PaperConfig:
    decision_log_path: Path
    window_summary_log_path: Path


@dataclass(frozen=True)
class HedgeBotConfig:
    market: MarketConfig
    risk: RiskConfig
    optimizer: OptimizerConfig
    hedge_timing: HedgeTimingConfig
    fees: FeesConfig
    paper: PaperConfig


def _dec(value) -> Decimal:
    return Decimal(str(value))


def load_config(path: Path | None = None, repo_root: Path | None = None) -> HedgeBotConfig:
    path = path or DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(path.read_text())
    root = repo_root or Path(__file__).resolve().parent.parent.parent

    m = raw["market"]
    r = raw["risk"]
    o = raw["optimizer"]
    h = raw["hedge_timing"]
    f = raw["fees"]
    p = raw["paper"]

    return HedgeBotConfig(
        market=MarketConfig(
            window_seconds=int(m["window_seconds"]),
            poll_interval_seconds=float(m["poll_interval_seconds"]),
        ),
        risk=RiskConfig(
            max_total_exposure_usd=_dec(r["max_total_exposure_usd"]),
            max_exposure_per_market_usd=_dec(r["max_exposure_per_market_usd"]),
            max_directional_imbalance_shares=_dec(r["max_directional_imbalance_shares"]),
            minimum_guaranteed_profit_usd=_dec(r["minimum_guaranteed_profit_usd"]),
            minimum_guaranteed_roi_pct=_dec(r["minimum_guaranteed_roi_pct"]),
            max_order_size_usd=_dec(r["max_order_size_usd"]),
            minimum_time_remaining_seconds=float(r["minimum_time_remaining_seconds"]),
            stop_new_entries_seconds_before_resolution=float(r["stop_new_entries_seconds_before_resolution"]),
            min_fill_ratio=_dec(r["min_fill_ratio"]),
            max_forced_hedge_loss_usd=_dec(r["max_forced_hedge_loss_usd"]),
        ),
        optimizer=OptimizerConfig(
            candidate_order_sizes_usd=tuple(_dec(x) for x in o["candidate_order_sizes_usd"]),
            max_opening_order_usd=_dec(o["max_opening_order_usd"]),
            cheap_open_threshold=_dec(o["cheap_open_threshold"]),
            imbalance_penalty_lambda=_dec(o["imbalance_penalty_lambda"]),
            objective=str(o["objective"]),
        ),
        hedge_timing=HedgeTimingConfig(
            profit_hedge_only_seconds=float(h["profit_hedge_only_seconds"]),
            defensive_hedge_start_seconds=float(h["defensive_hedge_start_seconds"]),
            emergency_hedge_start_seconds=float(h["emergency_hedge_start_seconds"]),
            min_worst_case_improvement_pct=_dec(h["min_worst_case_improvement_pct"]),
            defensive_share_quantities=tuple(_dec(x) for x in h["defensive_share_quantities"]),
            defensive_imbalance_fractions=tuple(_dec(x) for x in h["defensive_imbalance_fractions"]),
            price_runaway_window_samples=int(h["price_runaway_window_samples"]),
            price_runaway_min_move=_dec(h["price_runaway_min_move"]),
            price_runaway_max_pullback=_dec(h["price_runaway_max_pullback"]),
        ),
        fees=FeesConfig(taker_fee_pct=_dec(f["taker_fee_pct"])),
        paper=PaperConfig(
            decision_log_path=root / p["decision_log_path"],
            window_summary_log_path=root / p["window_summary_log_path"],
        ),
    )
