"""Loads atlantis/btc5m_longshot/config.yaml into typed, immutable config
objects - mirrors atlantis/btc5m_momentum/config.py's own pattern."""

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
    entry_window_seconds: float
    min_underdog_price: Decimal
    max_underdog_price: Decimal


@dataclass(frozen=True)
class RiskConfig:
    bet_size_usd: Decimal
    max_session_loss_usd: Decimal


@dataclass(frozen=True)
class PaperConfig:
    decision_log_path: Path
    window_summary_log_path: Path


@dataclass(frozen=True)
class LongshotBotConfig:
    market: MarketConfig
    signal: SignalConfig
    risk: RiskConfig
    paper: PaperConfig


def _dec(value) -> Decimal:
    return Decimal(str(value))


def load_config(path: Path | None = None, repo_root: Path | None = None) -> LongshotBotConfig:
    path = path or DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(path.read_text())
    root = repo_root or Path(__file__).resolve().parent.parent.parent

    m = raw["market"]
    s = raw["signal"]
    r = raw["risk"]
    p = raw["paper"]

    return LongshotBotConfig(
        market=MarketConfig(
            window_seconds=int(m["window_seconds"]),
            poll_interval_seconds=float(m["poll_interval_seconds"]),
        ),
        signal=SignalConfig(
            entry_window_seconds=float(s["entry_window_seconds"]),
            min_underdog_price=_dec(s["min_underdog_price"]),
            max_underdog_price=_dec(s["max_underdog_price"]),
        ),
        risk=RiskConfig(
            bet_size_usd=_dec(r["bet_size_usd"]),
            max_session_loss_usd=_dec(r["max_session_loss_usd"]),
        ),
        paper=PaperConfig(
            decision_log_path=root / p["decision_log_path"],
            window_summary_log_path=root / p["window_summary_log_path"],
        ),
    )
