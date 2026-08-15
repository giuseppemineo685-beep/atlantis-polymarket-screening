"""Pure momentum-signal logic - no I/O, no state, fully unit-testable.

Real-data derivation (2026-08-09, see the wallet-0x3048-real-strategy
memory / that day's conversation for the full trace): wallet
0x3048...e7537's big directional bets align with BTC's own prior-3-
minute price momentum 58.2% of the time, and win 71.2% of the time when
aligned vs 33.3% when against it. Because that first pass measured
momentum up to the moment of the bet (partway INTO the window being
predicted), it was re-checked using ONLY momentum from strictly BEFORE
the window opened (zero overlap with the outcome), against all 296
windows' actual resolution, independent of what the wallet did: 58.0%
overall accuracy, 65.3% in the strongest half by magnitude vs 50.7% in
the weakest half (i.e. no edge at all when the move is small). Crucially,
Polymarket's own price for the momentum-favored side barely moves off
~$0.51 regardless of how strong the momentum is (~$0.505 weak vs ~$0.519
strong) - the market is not pricing this signal in, which is the actual
tradeable edge, not the raw prediction accuracy by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

UP = "Up"
DOWN = "Down"


def compute_momentum_pct(price_now: Decimal, price_lookback: Decimal) -> Decimal:
    """Signed fractional move from price_lookback to price_now. Positive
    means BTC has moved up (favors UP); negative means down (favors
    DOWN). Returns 0 if price_lookback is 0 (degenerate/no data) rather
    than raising - callers treat a 0 momentum as "no signal" anyway."""
    if price_lookback == 0:
        return Decimal(0)
    return (price_now - price_lookback) / price_lookback


@dataclass(frozen=True)
class MomentumSignal:
    side: str  # UP or DOWN
    momentum_pct: Decimal


def evaluate_signal(
    momentum_pct: Decimal, *, min_pct_move: Decimal, long_momentum_pct: Decimal
) -> MomentumSignal | None:
    """None means "no trustworthy signal this poll" - either the 3-min
    momentum is too weak (per the real-data derivation above, below-
    median moves showed no measurable edge, 50.7% accuracy, indistin-
    guishable from a coin flip), OR it disagrees with the longer-term
    (20-min) trend.

    The trend-agreement check was added 2026-08-15 after a real bad day
    live (2026-08-12: 39% win rate, -$62.92, entirely on the DOWN side):
    BTC was grinding UP slowly all morning with lots of 3-minute dips
    along the way - each dip fired a DOWN signal that the 3-minute
    window alone couldn't tell apart from a real reversal, and lost
    against the underlying uptrend over and over. Re-checked against
    that exact week's real logged decisions (not a fresh backtest -
    the ACTUAL momentum_pct and outcome of all 859 real live bets):
    requiring the 20-min trend to agree with the 3-min signal would have
    turned that week from -$35.19 (48.0% win rate) into +$54.50 (51.2%),
    filtering out 294 bets that alone would have summed to -$89.68
    (41.8%). 15-min and 40-min lookbacks were also tested and both
    improved things too, just less than 20-min specifically."""
    if abs(momentum_pct) < min_pct_move:
        return None
    if (momentum_pct > 0) != (long_momentum_pct > 0):
        return None
    return MomentumSignal(side=UP if momentum_pct > 0 else DOWN, momentum_pct=momentum_pct)


@dataclass(frozen=True)
class Decision:
    should_bet: bool
    side: str | None
    reason: str


def decide(
    signal: MomentumSignal | None,
    favored_ask_price: Decimal | None,
    *,
    max_entry_price: Decimal,
) -> Decision:
    """The full entry gate for one window: a signal alone isn't enough -
    the favored side must still be cheap enough to preserve the edge (see
    max_entry_price's own config.yaml comment: real data shows this is
    normally not binding, since the market barely reprices on momentum,
    but it's the guard against the case where it does)."""
    if signal is None:
        return Decision(False, None, "momentum por debajo del umbral minimo - sin señal")
    if favored_ask_price is None:
        return Decision(False, None, f"sin liquidez visible en el lado favorecido ({signal.side})")
    if favored_ask_price > max_entry_price:
        return Decision(
            False, None,
            f"precio de entrada ${favored_ask_price} en {signal.side} excede el maximo aceptable (${max_entry_price})",
        )
    return Decision(
        True, signal.side,
        f"momentum {signal.momentum_pct:+.4%} favorece {signal.side} - entrando a ${favored_ask_price}",
    )
