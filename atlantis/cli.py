from __future__ import annotations

import argparse
import logging
import sys
from decimal import Decimal
from pathlib import Path

from atlantis.config import load_settings
from atlantis.services.bot_detection import detect_bot_wallet, format_bot_detection
from atlantis.services.active_portfolio import (
    build_active_portfolio,
    format_active_portfolio,
    write_active_portfolio_csv,
)
from atlantis.services.evaluate_wallet import evaluate_wallet, format_wallet_evaluation
from atlantis.services.evaluate_watchlist import (
    evaluate_watchlist,
    format_watchlist_evaluation,
    write_watchlist_evaluation_csv,
)
from atlantis.services.sports_traders import (
    discover_sports_traders,
    format_sports_traders,
    write_sports_traders_csv,
)
from atlantis.services.top_traders import format_top_traders, get_top_traders


def main(argv: list[str] | None = None) -> int:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(prog="atlantis")
    subparsers = parser.add_subparsers(dest="command", required=True)

    top = subparsers.add_parser("top-traders", help="Show Polymarket top traders.")
    top.add_argument("--category", default="OVERALL")
    top.add_argument("--time-period", default="DAY", choices=["DAY", "WEEK", "MONTH", "ALL"])
    top.add_argument("--order-by", default="PNL", choices=["PNL", "VOL"])
    top.add_argument("--limit", type=int, default=25)

    sports = subparsers.add_parser(
        "discover-sports-traders",
        help="Rank potentially copyable Polymarket sports traders.",
    )
    sports.add_argument("--leaderboard-limit", type=int, default=250)
    sports.add_argument("--max-traders", type=int, default=50)
    sports.add_argument("--max-trades-per-wallet", type=int, default=1000)
    sports.add_argument("--min-sports-trades", type=int, default=25)
    sports.add_argument("--min-sports-volume", type=Decimal, default=Decimal("1000"))
    sports.add_argument("--csv", default="")

    wallet = subparsers.add_parser("evaluate-wallet", help="Evaluate one wallet for copyability.")
    wallet.add_argument("wallet")
    wallet.add_argument("--max-trades", type=int, default=3000)
    wallet.add_argument("--max-positions", type=int, default=500)
    wallet.add_argument("--active-limit", type=int, default=20)

    bot = subparsers.add_parser("detect-bot-wallet", help="Detect bot-like wallet behavior.")
    bot.add_argument("wallet")
    bot.add_argument("--max-trades", type=int, default=3000)

    watchlist = subparsers.add_parser(
        "evaluate-watchlist",
        help="Evaluate approved wallets from a CSV watchlist.",
    )
    watchlist.add_argument("--wallets-csv", default="inputs/approved_wallets.csv")
    watchlist.add_argument("--statuses", default="approved,paper_only")
    watchlist.add_argument("--max-trades", type=int, default=3000)
    watchlist.add_argument("--max-positions", type=int, default=500)
    watchlist.add_argument("--since-days", type=int, default=None)
    watchlist.add_argument("--csv", default="")

    portfolio = subparsers.add_parser(
        "active-portfolio",
        help="Build active sports portfolio signals from ranked traders.",
    )
    portfolio.add_argument("--traders-csv", default="outputs/sports_traders.csv")
    portfolio.add_argument("--min-verdict", default="B", choices=["A", "B", "C", "REJECT"])
    portfolio.add_argument("--max-traders", type=int, default=25)
    portfolio.add_argument("--bankroll", type=Decimal, default=Decimal("0"))
    portfolio.add_argument("--max-stake-pct", type=Decimal, default=Decimal("0.005"))
    portfolio.add_argument("--min-position-value", type=Decimal, default=Decimal("25"))
    portfolio.add_argument("--min-price", type=Decimal, default=Decimal("0.05"))
    portfolio.add_argument("--max-price", type=Decimal, default=Decimal("0.90"))
    portfolio.add_argument("--limit", type=int, default=30)
    portfolio.add_argument("--csv", default="")

    args = parser.parse_args(argv)

    if args.command == "top-traders":
        traders = get_top_traders(
            settings=settings,
            category=args.category,
            time_period=args.time_period,
            order_by=args.order_by,
            limit=args.limit,
        )
        print(format_top_traders(traders))
        return 0

    if args.command == "discover-sports-traders":
        scores = discover_sports_traders(
            settings=settings,
            leaderboard_limit=args.leaderboard_limit,
            max_traders=args.max_traders,
            max_trades_per_wallet=args.max_trades_per_wallet,
            min_sports_trades=args.min_sports_trades,
            min_sports_volume=args.min_sports_volume,
        )
        print(format_sports_traders(scores))
        if args.csv:
            write_sports_traders_csv(Path(args.csv), scores)
            print(f"\nCSV written: {args.csv}")
        return 0

    if args.command == "evaluate-wallet":
        evaluation = evaluate_wallet(
            settings=settings,
            wallet_address=args.wallet,
            max_trades=args.max_trades,
            max_positions=args.max_positions,
        )
        print(format_wallet_evaluation(evaluation, active_limit=args.active_limit))
        return 0

    if args.command == "detect-bot-wallet":
        result = detect_bot_wallet(
            settings=settings,
            wallet_address=args.wallet,
            max_trades=args.max_trades,
        )
        print(format_bot_detection(result))
        return 0

    if args.command == "evaluate-watchlist":
        evaluations = evaluate_watchlist(
            settings=settings,
            wallets_csv=Path(args.wallets_csv),
            statuses={status.strip() for status in args.statuses.split(",") if status.strip()},
            max_trades=args.max_trades,
            max_positions=args.max_positions,
        )
        print(format_watchlist_evaluation(evaluations))
        if args.csv:
            write_watchlist_evaluation_csv(Path(args.csv), evaluations)
            print(f"\nCSV written: {args.csv}")
        return 0

    if args.command == "active-portfolio":
        signals = build_active_portfolio(
            settings=settings,
            traders_csv=Path(args.traders_csv),
            min_verdict=args.min_verdict,
            max_traders=args.max_traders,
            bankroll=args.bankroll,
            max_stake_pct=args.max_stake_pct,
            min_position_value=args.min_position_value,
            min_price=args.min_price,
            max_price=args.max_price,
        )
        print(format_active_portfolio(signals, limit=args.limit))
        if args.csv:
            write_active_portfolio_csv(Path(args.csv), signals)
            print(f"\nCSV written: {args.csv}")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
