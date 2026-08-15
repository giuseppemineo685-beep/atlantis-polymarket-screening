"""Pure decision logic - no I/O, no state, fully unit-testable.

Real-data derivation (2026-08-15, 7 real days / 4,020 real (side, window)
observations on Polymarket's BTC 5-min Up/Down market): in the last
~150s of a window, whichever side is currently cheaper ("the underdog")
gets bucketed by price and checked against what actually won:

  price bucket      n     real win rate   EV per $1
  <$0.02 (50x+)     798   0.4%            -$0.51
  $0.02-0.03        99    1.0%            -$0.59
  $0.03-0.05        183   2.2%            -$0.43
  $0.05-0.10        281   11.0%           +$0.52
  $0.10-0.15        180   17.8%           +$0.46
  $0.15-0.25        281   34.5%           +$0.75

The obvious-looking trade (buy the most extreme longshot for a 1x50-90
payout) is a LOSING bet - the market prices those about right or
slightly against you. The real, sizeable, consistently positive edge
sits in the $0.05-$0.25 band - a moderate underdog (4x-20x payout), not
an extreme one. Confirmed on a full week after an initial 3-day pass
already agreed directionally; a bucket that looked positive on the
3-day sample ($0.02-0.03, n=36) flipped negative once the sample grew
to 99, which is exactly why the extreme-longshot buckets should not be
trusted even though they look tempting.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

UP = "Up"
DOWN = "Down"


@dataclass(frozen=True)
class Decision:
    should_bet: bool
    side: str | None
    reason: str


def decide(
    *,
    up_price: Decimal | None,
    down_price: Decimal | None,
    seconds_remaining: float,
    entry_window_seconds: float,
    min_underdog_price: Decimal,
    max_underdog_price: Decimal,
) -> Decision:
    """Only looks at the underdog (cheaper) side, and only within the
    last `entry_window_seconds` of the window - matches how the real
    data above was sampled (last ~150s), not the whole 5 minutes."""
    if seconds_remaining > entry_window_seconds:
        return Decision(False, None, f"todavia quedan {seconds_remaining:.0f}s - fuera de la ventana de entrada ({entry_window_seconds:.0f}s)")
    if up_price is None or down_price is None:
        return Decision(False, None, "sin precio visible de alguno de los dos lados")

    if up_price < down_price:
        side, price = UP, up_price
    else:
        side, price = DOWN, down_price

    if not (min_underdog_price <= price <= max_underdog_price):
        return Decision(
            False, None,
            f"underdog {side} @ ${price} fuera del rango objetivo (${min_underdog_price}-${max_underdog_price})",
        )
    return Decision(
        True, side,
        f"underdog {side} @ ${price} dentro del rango objetivo (${min_underdog_price}-${max_underdog_price}) - apostando a reversion",
    )
