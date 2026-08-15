"""Loads atlantis/btc5m_momentum/config.yaml into typed, immutable config
objects - mirrors atlantis/btc5m_hedge/config.py's own pattern."""

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
class SignalConfig:
    momentum_lookback_seconds: int
    momentum_min_pct_move: Decimal
    trend_lookback_seconds: int


@dataclass(frozen=True)
class RiskConfig:
    max_entry_price: Decimal
    bet_size_usd: Decimal
    max_session_loss_usd: Decimal


@dataclass(frozen=True)
class PaperConfig:
    decision_log_path: Path
    window_summary_log_path: Path


@dataclass(frozen=True)
class MomentumBotConfig:
    market: MarketConfig
    signal: SignalConfig
    risk: RiskConfig
    paper: PaperConfig


def _dec(value) -> Decimal:
    return Decimal(str(value))


def load_config(path: Path | None = None, repo_root: Path | None = None) -> MomentumBotConfig:
    path = path or DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(path.read_text())
    root = repo_root or Path(__file__).resolve().parent.parent.parent

    m = raw["market"]
    s = raw["signal"]
    r = raw["risk"]
    p = raw["paper"]

    return MomentumBotConfig(
        market=MarketConfig(
            window_seconds=int(m["window_seconds"]),
            poll_interval_seconds=float(m["poll_interval_seconds"]),
        ),
        signal=SignalConfig(
            momentum_lookback_seconds=int(s["momentum_lookback_seconds"]),
            momentum_min_pct_move=_dec(s["momentum_min_pct_move"]),
            trend_lookback_seconds=int(s["trend_lookback_seconds"]),
        ),
        risk=RiskConfig(
            max_entry_price=_dec(r["max_entry_price"]),
            bet_size_usd=_dec(r["bet_size_usd"]),
            max_session_loss_usd=_dec(r["max_session_loss_usd"]),
        ),
        paper=PaperConfig(
            decision_log_path=root / p["decision_log_path"],
            window_summary_log_path=root / p["window_summary_log_path"],
        ),
    )
