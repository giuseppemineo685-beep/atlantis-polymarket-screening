"""Replays historical order-book snapshots through the EXACT same
portfolio/optimizer/risk pipeline the live paper bot uses - this is the
"does this behavior actually produce edge after fees/spread/slippage/
partial fills" measurement tool the spec asks for, not a separate
strategy implementation.

Input CSV columns (one row per observed snapshot):
    timestamp        - unix epoch seconds (int) or ISO8601
    window_slug       - which 5-minute market this row belongs to (rows
                        are grouped and replayed independently per slug)
    up_ask, down_ask  - best ask price on each side at this instant
    up_ask_depth, down_ask_depth - size available at that best-ask level
                        (single-level VWAP - if you have full L2 depth,
                        feed it through market_data.OrderBookLevel lists
                        directly via run_backtest_rows() instead of CSV)
    resolved_outcome  - optional, "Up"/"Down"; only needs to be present
                        on (at least) the LAST row of each window - if
                        absent throughout, that window's summary reports
                        realized_outcome/realized_profit as blank rather
                        than guessing.
    btc_price         - optional, carried through for context only, the
                        strategy itself never reads it.
    seconds_remaining - optional; if absent, computed from timestamp
                        assuming standard 5-minute-UTC-aligned windows
                        (atlantis.btc5m_hedge.market_data.current_window_start).

STRATEGY A/B/C comparison (2026-08-08): run_backtest_comparison() replays
the SAME snapshots three times with different hedge_timing_cfg overrides
- STRATEGY A never falls through to defensive/emergency at all, STRATEGY
B adds defensive but not emergency, STRATEGY C runs the full three-mode
machine as configured. This measures whether defensive/emergency hedging
actually improves net_pnl/max_drawdown - never assumed, per the spec's
own instruction not to assume partial hedging helps.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from atlantis.btc5m_hedge.config import HedgeBotConfig, HedgeTimingConfig
from atlantis.btc5m_hedge.logger import WindowStats, log_decision, log_window_summary
from atlantis.btc5m_hedge.market_data import current_window_start
from atlantis.btc5m_hedge.optimizer import evaluate_hedge
from atlantis.btc5m_hedge.portfolio import OrderBookLevel, Portfolio


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


@dataclass(frozen=True)
class Snapshot:
    timestamp: str
    up_levels: list[OrderBookLevel]
    down_levels: list[OrderBookLevel]
    seconds_remaining: float
    resolved_outcome: str | None


def _parse_timestamp(raw: str) -> datetime:
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except ValueError:
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)


def load_snapshots_from_csv(path: Path, window_seconds: int) -> dict[str, list[Snapshot]]:
    windows: dict[str, list[Snapshot]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            slug = row["window_slug"]
            dt = _parse_timestamp(row["timestamp"])

            if row.get("seconds_remaining"):
                seconds_remaining = float(row["seconds_remaining"])
            else:
                close_at = current_window_start(dt, window_seconds).timestamp() + window_seconds
                seconds_remaining = close_at - dt.timestamp()

            snapshot = Snapshot(
                timestamp=row["timestamp"],
                up_levels=[OrderBookLevel(Decimal(row["up_ask"]), Decimal(row["up_ask_depth"]))],
                down_levels=[OrderBookLevel(Decimal(row["down_ask"]), Decimal(row["down_ask_depth"]))],
                seconds_remaining=seconds_remaining,
                resolved_outcome=row.get("resolved_outcome") or None,
            )
            windows.setdefault(slug, []).append(snapshot)
    return windows


@dataclass(frozen=True)
class WindowResult:
    slug: str
    portfolio: Portfolio
    stats: WindowStats
    realized_outcome: str | None
    realized_profit: Decimal | None
    unhedged_seconds: float


def run_backtest_window(
    slug: str,
    snapshots: list[Snapshot],
    config: HedgeBotConfig,
    decision_log_path: Path,
    window_summary_path: Path,
    *,
    hedge_timing_cfg: HedgeTimingConfig | None = None,
) -> WindowResult:
    hedge_timing_cfg = hedge_timing_cfg or config.hedge_timing
    portfolio = Portfolio()
    stats = WindowStats()
    resolved_outcome: str | None = None
    unhedged_seconds = 0.0
    prev_epoch: float | None = None

    for snap in snapshots:
        if snap.resolved_outcome:
            resolved_outcome = snap.resolved_outcome

        epoch = _parse_timestamp(snap.timestamp).timestamp()
        if prev_epoch is not None and portfolio.total_cost > 0 and portfolio.get_guaranteed_profit() < 0:
            unhedged_seconds += max(epoch - prev_epoch, 0.0)
        prev_epoch = epoch

        hedge_decision = evaluate_hedge(
            portfolio=portfolio,
            up_levels=snap.up_levels,
            down_levels=snap.down_levels,
            seconds_remaining=snap.seconds_remaining,
            optimizer_cfg=config.optimizer,
            risk_cfg=config.risk,
            hedge_timing_cfg=hedge_timing_cfg,
        )

        before = portfolio
        if hedge_decision.candidate is not None:
            portfolio = hedge_decision.candidate.new_portfolio
            stats.record(portfolio, hedge_mode=hedge_decision.hedge_mode, loss_reduction=hedge_decision.loss_reduction)
            action = "BUY"
        elif before.is_locked_profit():
            action = "LOCKED_PROFIT"
        else:
            action = "WAIT"

        log_decision(
            decision_log_path,
            timestamp=snap.timestamp,
            window_slug=slug,
            action=action,
            before=before,
            after=portfolio,
            hedge_decision=hedge_decision,
        )

    if resolved_outcome == "Up":
        realized_profit: Decimal | None = portfolio.up_shares - portfolio.total_cost
    elif resolved_outcome == "Down":
        realized_profit = portfolio.down_shares - portfolio.total_cost
    else:
        realized_profit = None

    log_window_summary(
        window_summary_path,
        window_slug=slug,
        portfolio=portfolio,
        stats=stats,
        realized_outcome=resolved_outcome or "",
        window_closed_at=_now(),
    )
    return WindowResult(
        slug=slug,
        portfolio=portfolio,
        stats=stats,
        realized_outcome=resolved_outcome,
        realized_profit=realized_profit,
        unhedged_seconds=unhedged_seconds,
    )


def run_backtest(
    csv_path: Path,
    config: HedgeBotConfig,
    decision_log_path: Path,
    window_summary_path: Path,
    *,
    hedge_timing_cfg: HedgeTimingConfig | None = None,
) -> dict[str, WindowResult]:
    windows = load_snapshots_from_csv(csv_path, config.market.window_seconds)
    results: dict[str, WindowResult] = {}
    for slug, snapshots in windows.items():
        snapshots.sort(key=lambda s: s.timestamp)
        results[slug] = run_backtest_window(
            slug, snapshots, config, decision_log_path, window_summary_path, hedge_timing_cfg=hedge_timing_cfg
        )
    return results


def _window_epoch(slug: str) -> int:
    try:
        return int(slug.rsplit("-", 1)[-1])
    except ValueError:
        return 0


def compute_max_drawdown(chronological_profits: list[Decimal]) -> Decimal:
    """Classic peak-to-trough drawdown over a cumulative PnL series -
    chronological_profits are PER-WINDOW realized profits, already sorted
    oldest-first; this cumulates them itself."""
    peak = Decimal(0)
    cumulative = Decimal(0)
    max_dd = Decimal(0)
    for profit in chronological_profits:
        cumulative += profit
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return max_dd


@dataclass(frozen=True)
class BacktestReport:
    number_of_windows: int
    number_of_profit_hedges: int
    number_of_defensive_hedges: int
    number_of_emergency_hedges: int
    profit_from_locked_positions: Decimal
    losses_from_failed_hedges: Decimal
    average_loss_reduction_from_defensive_hedges: Decimal | None
    average_unhedged_time: float
    markets_resolved_fully_unhedged: int
    net_pnl: Decimal
    max_drawdown: Decimal


def summarize_backtest_report(results: dict[str, WindowResult]) -> BacktestReport:
    ordered = sorted(results.values(), key=lambda r: _window_epoch(r.slug))

    number_of_profit_hedges = sum(r.stats.number_of_profit_hedges for r in ordered)
    number_of_defensive_hedges = sum(r.stats.number_of_defensive_hedges for r in ordered)
    number_of_emergency_hedges = sum(r.stats.number_of_emergency_hedges for r in ordered)

    # "Locked" = worst-case was already >= 0 at close (MODE A's own bar,
    # BOTH outcomes non-negative) - profit from those windows is close to
    # risk-free by construction, tracked separately from windows that
    # never got there.
    profit_from_locked_positions = sum(
        (r.realized_profit for r in ordered if r.realized_profit is not None and r.portfolio.get_guaranteed_profit() >= 0),
        Decimal(0),
    )
    losses_from_failed_hedges = sum(
        (
            r.realized_profit
            for r in ordered
            if r.realized_profit is not None and r.portfolio.get_guaranteed_profit() < 0 and r.realized_profit < 0
        ),
        Decimal(0),
    )

    defensive_reduction_total = sum((r.stats.defensive_loss_reduction_total for r in ordered), Decimal(0))
    average_loss_reduction_from_defensive_hedges = (
        defensive_reduction_total / number_of_defensive_hedges if number_of_defensive_hedges > 0 else None
    )

    average_unhedged_time = (sum(r.unhedged_seconds for r in ordered) / len(ordered)) if ordered else 0.0

    markets_resolved_fully_unhedged = sum(
        1 for r in ordered if r.stats.number_of_orders > 0 and r.portfolio.get_guaranteed_profit() < 0
    )

    net_pnl = sum((r.realized_profit for r in ordered if r.realized_profit is not None), Decimal(0))
    max_drawdown = compute_max_drawdown([r.realized_profit for r in ordered if r.realized_profit is not None])

    return BacktestReport(
        number_of_windows=len(ordered),
        number_of_profit_hedges=number_of_profit_hedges,
        number_of_defensive_hedges=number_of_defensive_hedges,
        number_of_emergency_hedges=number_of_emergency_hedges,
        profit_from_locked_positions=profit_from_locked_positions,
        losses_from_failed_hedges=losses_from_failed_hedges,
        average_loss_reduction_from_defensive_hedges=average_loss_reduction_from_defensive_hedges,
        average_unhedged_time=average_unhedged_time,
        markets_resolved_fully_unhedged=markets_resolved_fully_unhedged,
        net_pnl=net_pnl,
        max_drawdown=max_drawdown,
    )


# Each override reuses evaluate_hedge's OWN band logic to switch modes
# off, rather than a separate code path per strategy:
# - STRATEGY A: profit_hedge_only_seconds=0 makes "seconds_remaining >=
#   profit_hedge_only_seconds" true for the entire window (seconds_remaining
#   is never negative), so once MODE A itself finds nothing, evaluate_hedge
#   always takes its "too early for anything else" WAIT branch - B/C never run.
# - STRATEGY B: emergency_hedge_start_seconds=-1 makes "seconds_remaining <
#   emergency_hedge_start_seconds" never true, so MODE C never triggers -
#   MODE B (defensive) covers the whole post-MODE-A range down to close.
# - STRATEGY C: config as-is, all three modes active.
STRATEGY_CONFIGS = {
    "STRATEGY_A_profit_only": lambda base: replace(base, profit_hedge_only_seconds=0.0),
    "STRATEGY_B_plus_defensive": lambda base: replace(base, emergency_hedge_start_seconds=-1.0),
    "STRATEGY_C_plus_emergency": lambda base: base,
}


def run_backtest_comparison(csv_path: Path, config: HedgeBotConfig, output_dir: Path) -> dict[str, BacktestReport]:
    """Replays the SAME historical snapshots three times under different
    hedge_timing_cfg overrides - never assumes defensive/emergency
    hedging helps, measures it."""
    reports: dict[str, BacktestReport] = {}
    for label, override_fn in STRATEGY_CONFIGS.items():
        hedge_timing_cfg = override_fn(config.hedge_timing)
        decision_log = output_dir / f"{label}_decisions.csv"
        window_summary_log = output_dir / f"{label}_window_summary.csv"
        for path in (decision_log, window_summary_log):
            if path.exists():
                path.unlink()
        results = run_backtest(csv_path, config, decision_log, window_summary_log, hedge_timing_cfg=hedge_timing_cfg)
        reports[label] = summarize_backtest_report(results)
    return reports


if __name__ == "__main__":
    import argparse

    from atlantis.btc5m_hedge.config import load_config

    parser = argparse.ArgumentParser(description="Backtest the BTC5m cross-side hedge strategy against historical snapshots")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--decision-log", type=Path, default=None)
    parser.add_argument("--window-summary-log", type=Path, default=None)
    parser.add_argument(
        "--compare", action="store_true", help="Run the STRATEGY A/B/C comparison instead of a single full-featured run"
    )
    parser.add_argument("--output-dir", type=Path, default=None, help="Only used with --compare")
    args = parser.parse_args()

    cfg = load_config(args.config)

    if args.compare:
        output_dir = args.output_dir or cfg.paper.decision_log_path.parent
        comparison = run_backtest_comparison(args.csv_path, cfg, output_dir)
        for label, report in comparison.items():
            print(f"\n=== {label} ===")
            print(f"  ventanas: {report.number_of_windows}")
            print(
                f"  hedges: profit={report.number_of_profit_hedges} defensive={report.number_of_defensive_hedges} "
                f"emergency={report.number_of_emergency_hedges}"
            )
            print(f"  profit_from_locked_positions: ${report.profit_from_locked_positions:.2f}")
            print(f"  losses_from_failed_hedges: ${report.losses_from_failed_hedges:.2f}")
            avg_reduction = report.average_loss_reduction_from_defensive_hedges
            print(f"  average_loss_reduction_from_defensive_hedges: {'n/a' if avg_reduction is None else f'${avg_reduction:.2f}'}")
            print(f"  average_unhedged_time: {report.average_unhedged_time:.1f}s")
            print(f"  markets_resolved_fully_unhedged: {report.markets_resolved_fully_unhedged}")
            print(f"  net_pnl: ${report.net_pnl:.2f}")
            print(f"  max_drawdown: ${report.max_drawdown:.2f}")
    else:
        decision_log = args.decision_log or cfg.paper.decision_log_path.with_name("btc5m_hedge_backtest_decisions.csv")
        window_summary_log = args.window_summary_log or cfg.paper.window_summary_log_path.with_name(
            "btc5m_hedge_backtest_window_summary.csv"
        )
        outcome = run_backtest(args.csv_path, cfg, decision_log, window_summary_log)
        for slug, result in outcome.items():
            print(f"{slug}: guaranteed_profit={result.portfolio.get_guaranteed_profit():.4f} total_cost={result.portfolio.total_cost:.2f}")
