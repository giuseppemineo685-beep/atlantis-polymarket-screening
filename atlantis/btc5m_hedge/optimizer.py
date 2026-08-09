"""Decides what (if anything) to buy this poll. Two candidate sources,
tried together every cycle:

1. Fixed order-book-relative sizes (config.optimizer.candidate_order_sizes_usd)
   converted to a share quantity via the side's current best ask - covers
   "there's no position yet, start one" (the closed-form solver below is
   undefined/degenerate at 0/0) and "keep probing in normal increments".

2. The closed-form deficit-buy-range solver (hedge_buy_range) plus its
   derived equalize-quantity - covers "top up the currently-cheaper side
   by exactly enough to lock in (or maximize) guaranteed_profit" without
   guessing sizes.

Every candidate is simulated against REAL order-book depth
(Portfolio.simulate_orderbook_buy), never a flat best-ask price, then
risk-filtered (risk.check_candidate), then scored. The best surviving
candidate is returned; None means WAIT.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from atlantis.btc5m_hedge.config import HedgeTimingConfig, OptimizerConfig, RiskConfig
from atlantis.btc5m_hedge.portfolio import DOWN, UP, FillResult, OrderBookLevel, Portfolio
from atlantis.btc5m_hedge.risk import check_candidate


@dataclass(frozen=True)
class DeficitRange:
    x_min_for_target_positive: Decimal
    x_max_for_other_positive: Decimal | None
    feasible: bool


def hedge_buy_range(buying_side_shares: Decimal, other_side_shares: Decimal, total_cost: Decimal, price: Decimal) -> DeficitRange:
    """Solve for x (more shares of the side currently at `price`) such
    that buying_side_shares + x > total_cost + price*x (this side becomes
    profitable) and, where possible, other_side_shares > total_cost +
    price*x too (the side we already hold stays profitable). Symmetric:
    call with (down_shares, up_shares, total_cost, down_price) to solve
    for DOWN, or (up_shares, down_shares, total_cost, up_price) for UP."""
    x_min = max((total_cost - buying_side_shares) / (Decimal(1) - price), Decimal(0)) if price < 1 else None
    x_max = (other_side_shares - total_cost) / price if price > 0 else None
    feasible = x_min is not None and x_max is not None and x_max > x_min
    return DeficitRange(
        x_min_for_target_positive=x_min if x_min is not None else Decimal(0),
        x_max_for_other_positive=x_max,
        feasible=feasible,
    )


def equalizing_quantity(buying_side_shares: Decimal, other_side_shares: Decimal) -> Decimal:
    """The single-order size that maximizes guaranteed_profit for a lump-
    sum buy of one side: profit_if_up(x) and profit_if_down(x) are linear
    in x with opposite slopes, so min(...) is maximized exactly where
    they cross - which algebraically happens at the quantity that makes
    up_shares == down_shares (buying_side_shares + x == other_side_shares).
    Negative results (already ahead) clip to 0 - nothing to buy."""
    return max(other_side_shares - buying_side_shares, Decimal(0))


@dataclass(frozen=True)
class Candidate:
    side: str
    requested_quantity: Decimal
    fill: FillResult
    new_portfolio: Portfolio
    delta_guaranteed_profit: Decimal
    projected_guaranteed_profit: Decimal
    score: Decimal
    source: str


def _score(new_portfolio: Portfolio, optimizer_cfg: OptimizerConfig) -> Decimal:
    guaranteed = new_portfolio.get_guaranteed_profit()
    if optimizer_cfg.objective == "score":
        return guaranteed - optimizer_cfg.imbalance_penalty_lambda * new_portfolio.directional_imbalance
    return guaranteed


def _project_completed_hedge(portfolio: Portfolio, other_side: str, other_levels: list[OrderBookLevel]) -> Decimal:
    """What guaranteed_profit WOULD be if the currently-deficient side
    were topped up right now, at the OTHER side's current best-ask depth,
    to equalize shares. This is a lookahead, not an order - it never
    executes anything, it only answers "does completing this hedge at
    today's prices look achievable", which is the spec's third
    acceptance criterion: a candidate is worth taking if it "permite
    alcanzar posteriormente una cartera completamente cubierta", not only
    if it improves guaranteed_profit by itself. Without this, opening the
    FIRST leg of any position always looks like a pure regression
    (guaranteed_profit goes from exactly 0 - nothing at risk - to
    negative - one side now costs money with nothing hedging it yet),
    so delta_guaranteed_profit alone can never justify ever starting a
    position, no matter how cheap. Confirmed live 2026-08-08: 64/64 WAIT
    across several windows that the arb-shadow tool independently showed
    real sub-$1 combined pricing in."""
    buying_side_shares = portfolio.down_shares if other_side == DOWN else portfolio.up_shares
    already_held_shares = portfolio.up_shares if other_side == DOWN else portfolio.down_shares
    x = equalizing_quantity(buying_side_shares, already_held_shares)
    projected_portfolio, _fill = portfolio.simulate_orderbook_buy(other_side, x, other_levels)
    return projected_portfolio.get_guaranteed_profit()


def _build_candidate(
    *,
    side: str,
    requested_quantity: Decimal,
    levels: list[OrderBookLevel],
    other_levels: list[OrderBookLevel],
    portfolio: Portfolio,
    source: str,
    optimizer_cfg: OptimizerConfig,
) -> Candidate | None:
    if requested_quantity <= 0:
        return None
    new_portfolio, fill = portfolio.simulate_orderbook_buy(side, requested_quantity, levels)
    if fill.filled_quantity <= 0:
        return None
    delta = new_portfolio.get_guaranteed_profit() - portfolio.get_guaranteed_profit()
    other_side = DOWN if side == UP else UP
    projected = _project_completed_hedge(new_portfolio, other_side, other_levels)
    return Candidate(
        side=side,
        requested_quantity=requested_quantity,
        fill=fill,
        new_portfolio=new_portfolio,
        delta_guaranteed_profit=delta,
        projected_guaranteed_profit=projected,
        score=_score(new_portfolio, optimizer_cfg),
        source=source,
    )


def generate_candidates(
    *,
    portfolio: Portfolio,
    up_levels: list[OrderBookLevel],
    down_levels: list[OrderBookLevel],
    optimizer_cfg: OptimizerConfig,
) -> list[Candidate]:
    candidates: list[Candidate] = []

    sides = ((UP, up_levels, down_levels), (DOWN, down_levels, up_levels))

    for side, levels, other_levels in sides:
        if not levels:
            continue
        best_price = levels[0].price
        for size_usd in optimizer_cfg.candidate_order_sizes_usd:
            if best_price <= 0:
                continue
            quantity = size_usd / best_price
            cand = _build_candidate(
                side=side, requested_quantity=quantity, levels=levels, other_levels=other_levels,
                portfolio=portfolio, source="fixed_size", optimizer_cfg=optimizer_cfg,
            )
            if cand is not None:
                candidates.append(cand)

    for side, levels, other_levels in sides:
        if not levels:
            continue
        same_side_shares = portfolio.up_shares if side == UP else portfolio.down_shares
        other_side_shares = portfolio.down_shares if side == UP else portfolio.up_shares
        x = equalizing_quantity(same_side_shares, other_side_shares)
        cand = _build_candidate(
            side=side, requested_quantity=x, levels=levels, other_levels=other_levels,
            portfolio=portfolio, source="deficit_range", optimizer_cfg=optimizer_cfg,
        )
        if cand is not None:
            candidates.append(cand)

    return candidates


@dataclass(frozen=True)
class Decision:
    candidate: Candidate | None
    reason: str


def choose_action(
    *,
    portfolio: Portfolio,
    up_levels: list[OrderBookLevel],
    down_levels: list[OrderBookLevel],
    seconds_remaining: float,
    optimizer_cfg: OptimizerConfig,
    risk_cfg: RiskConfig,
) -> Decision:
    """Returns the single best risk-approved candidate to execute this
    poll, or a Decision with candidate=None (WAIT) and a human-readable
    reason - either nothing improved guaranteed_profit, or everything
    that would have was rejected by risk.py."""
    candidates = generate_candidates(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels, optimizer_cfg=optimizer_cfg
    )
    if not candidates:
        return Decision(None, "sin liquidez visible en ninguno de los dos lados")

    approved: list[Candidate] = []
    rejection_reasons: list[str] = []
    for cand in candidates:
        # Two independent ways a candidate earns consideration: it
        # improves the CURRENT guaranteed_profit outright (closing/
        # topping up an existing hedge), or - even if it doesn't, because
        # the other side has no shares yet and any first leg alone always
        # makes guaranteed_profit go from 0 to negative - completing the
        # hedge at TODAY's price on the other side is projected to clear
        # the safety margin. Rejecting every candidate that fails both
        # would make the bot permanently unable to open a position.
        improves_now = cand.delta_guaranteed_profit > 0
        # The projection path is only valid for the side that is currently
        # BEHIND (or tied) - buying more of the side that's already ahead
        # can never raise guaranteed_profit today (the trailing side's
        # profit is the binding min, and it only gets worse as cost rises
        # with shares unchanged), so its "good" projection is an illusion:
        # it assumes a same-priced top-up of the trailing side will still
        # happen later, but that top-up gets harder (or hits the exposure
        # cap) the further ahead this side is allowed to run. Confirmed
        # live 2026-08-08: a position at guaranteed=-$0.14 (one DOWN order
        # away from locked) instead bought MORE of the already-larger UP
        # side at a good price, deepening the imbalance and dragging
        # guaranteed_profit down to -$3.14 by window close.
        side_shares = portfolio.up_shares if cand.side == UP else portfolio.down_shares
        other_shares = portfolio.down_shares if cand.side == UP else portfolio.up_shares
        # STRICT lagging (or a truly empty portfolio) - NOT <=. A tie is
        # already a hedged position; topping up the tie-break side (UP,
        # arbitrarily) is a fresh directional bet, not completing
        # anything, no matter which opening path claims to justify it.
        # Confirmed live 2026-08-08: with the <= version, EVERY one of 5
        # UP buys in one window fired while already tied to DOWN - each
        # round did complete at a small profit, but DOWN kept getting
        # more expensive each time (BTC trending hard one way), inflating
        # cost from $0.50 to $26.88 chasing a moving target, and the
        # final round never fully re-tied (29.76 UP vs 26.37 DOWN) before
        # the window closed - Down (the lagging, smaller side) won,
        # -$0.50. Applies to BOTH opening paths below, not just the
        # cheap-price one - the same "keeps re-opening the same side
        # every time it ties back up" failure mode was possible via the
        # projected-profit path too, and did happen.
        is_strictly_lagging_or_empty = side_shares < other_shares or portfolio.total_cost == 0
        # Capped separately (max_opening_order_usd, normally much smaller
        # than max_order_size_usd) - an opening trade's justification is a
        # PROJECTION at today's other-side price, which a few seconds of
        # real BTC movement can invalidate before the completing order
        # even fires (confirmed live 2026-08-08). Keeping opening size
        # small limits how much rides on that projection actually holding.
        within_opening_size_cap = cand.fill.total_cost <= optimizer_cfg.max_opening_order_usd
        opens_toward_locked_profit = (
            is_strictly_lagging_or_empty
            and within_opening_size_cap
            and cand.projected_guaranteed_profit >= risk_cfg.minimum_guaranteed_profit_usd
        )
        # Second, independent opening path: buy a side that's simply
        # cheap right now (<= cheap_open_threshold), without requiring
        # the OTHER side to already be cheap enough for an instantly-
        # profitable pair. Mirrors how the real wallet 0x3048...e7537
        # actually trades - buy whichever side looks cheap, trust BTC's
        # own oscillation during the 5 minutes to cheapen the other side
        # later, and lean on MODE B/C to complete or damage-control if it
        # doesn't. Added 2026-08-08 after opens_toward_locked_profit's
        # ~25%-combined-edge bar (given max_opening_order_usd) left the
        # bot unable to open anything for 3.5+ hours while Up traded at
        # 37-42c with nothing happening. This does reintroduce real
        # legging risk on purpose - see config.yaml's own comment on why
        # that's considered acceptable (MODE B/C bound it). Uses the same
        # strict-lagging-or-empty guard as opens_toward_locked_profit
        # above, for the same reason.
        opens_on_cheap_price = (
            is_strictly_lagging_or_empty
            and within_opening_size_cap
            and cand.fill.vwap_price is not None
            and cand.fill.vwap_price <= optimizer_cfg.cheap_open_threshold
        )
        if not (improves_now or opens_toward_locked_profit or opens_on_cheap_price):
            continue
        verdict = check_candidate(
            side=cand.side,
            requested_quantity=cand.requested_quantity,
            fill=cand.fill,
            portfolio_before=portfolio,
            portfolio_after=cand.new_portfolio,
            seconds_remaining=seconds_remaining,
            risk=risk_cfg,
        )
        if verdict.allowed:
            approved.append(cand)
        else:
            rejection_reasons.append(f"{cand.side} x{cand.requested_quantity:.2f} ({cand.source}): {verdict.reason}")

    if not approved:
        if rejection_reasons:
            return Decision(None, "candidatos rechazados por riesgo: " + "; ".join(rejection_reasons[:3]))
        return Decision(None, "ningun candidato mejora guaranteed_profit - WAIT")

    # Rank by whichever of the two acceptance signals is stronger - a
    # closing candidate ranks on how much it improves guaranteed_profit
    # right now, an opening candidate ranks on how good the completed
    # hedge looks projected at today's prices. Using score (raw
    # guaranteed_profit of new_portfolio) alone would unfairly penalize
    # every opening candidate, since new_portfolio is deliberately still
    # one-sided at this point.
    best = max(approved, key=lambda c: max(c.delta_guaranteed_profit, c.projected_guaranteed_profit))
    if best.delta_guaranteed_profit > 0:
        reason = f"{best.side} x{best.fill.filled_quantity:.2f} ({best.source}) mejora guaranteed_profit en ${best.delta_guaranteed_profit:.4f}"
    else:
        reason = (
            f"{best.side} x{best.fill.filled_quantity:.2f} ({best.source}) abre posicion - hedge completo "
            f"proyectado a precios actuales: ${best.projected_guaranteed_profit:.4f}"
        )
    return Decision(best, reason)


# --- Three-mode hedge state machine (2026-08-08) ---------------------
# MODE A (PROFIT_HEDGE) is exactly choose_action() above, unchanged, and
# always tried first regardless of time remaining - "este modo siempre
# tiene prioridad". Everything below only runs when MODE A finds nothing:
# MODE B (DEFENSIVE_HEDGE) tries to shrink the worst-case loss without
# necessarily locking a profit; MODE C (EMERGENCY_HEDGE) drops the
# "worth it" bar entirely and just maximizes worst_case_profit with
# whatever's left. Both B and C only ever buy the LAGGING side (fewer
# shares) - buying the leading side can only make the worst case worse,
# same reasoning as choose_action's is_lagging_or_tied guard.

PROFIT_HEDGE = "PROFIT_HEDGE"
DEFENSIVE_HEDGE = "DEFENSIVE_HEDGE"
EMERGENCY_HEDGE = "EMERGENCY_HEDGE"
WAIT = "WAIT"


def generate_lagging_side_candidates(
    *,
    portfolio: Portfolio,
    up_levels: list[OrderBookLevel],
    down_levels: list[OrderBookLevel],
    hedge_timing_cfg: HedgeTimingConfig,
    optimizer_cfg: OptimizerConfig,
) -> list[Candidate]:
    """Candidates for MODE B/C: only ever top up the side with FEWER
    shares (ties go to UP arbitrarily - buying either would equally do
    nothing since the imbalance is already 0). Tries the configured
    absolute share counts, the configured fractions of the current
    imbalance, and the mathematically optimal full-close quantity
    (equalizing_quantity) - the spec's "no usar porcentajes fijos
    solamente"."""
    if portfolio.up_shares <= portfolio.down_shares:
        side, levels, other_levels = UP, up_levels, down_levels
    else:
        side, levels, other_levels = DOWN, down_levels, up_levels
    if not levels:
        return []

    lagging_shares = portfolio.up_shares if side == UP else portfolio.down_shares
    leading_shares = portfolio.down_shares if side == UP else portfolio.up_shares
    imbalance = leading_shares - lagging_shares

    quantities = {q for q in hedge_timing_cfg.defensive_share_quantities}
    for frac in hedge_timing_cfg.defensive_imbalance_fractions:
        quantities.add(frac * imbalance)
    quantities.add(equalizing_quantity(lagging_shares, leading_shares))
    quantities.discard(Decimal(0))

    candidates = []
    for qty in quantities:
        cand = _build_candidate(
            side=side, requested_quantity=qty, levels=levels, other_levels=other_levels,
            portfolio=portfolio, source="defensive_scan", optimizer_cfg=optimizer_cfg,
        )
        if cand is not None:
            candidates.append(cand)
    return candidates


@dataclass(frozen=True)
class HedgeDecision:
    candidate: Candidate | None
    hedge_mode: str
    reason: str
    seconds_remaining: float
    current_worst_case_profit: Decimal
    new_worst_case_profit: Decimal
    current_max_loss: Decimal
    new_max_loss: Decimal
    loss_reduction: Decimal
    loss_reduction_pct: Decimal | None


def _no_op_decision(portfolio: Portfolio, seconds_remaining: float, reason: str) -> HedgeDecision:
    wcp = portfolio.get_worst_case_profit()
    loss = portfolio.get_max_loss()
    return HedgeDecision(
        candidate=None, hedge_mode=WAIT, reason=reason, seconds_remaining=seconds_remaining,
        current_worst_case_profit=wcp, new_worst_case_profit=wcp,
        current_max_loss=loss, new_max_loss=loss, loss_reduction=Decimal(0), loss_reduction_pct=None,
    )


def _loss_reduction_pct(current_loss: Decimal, new_loss: Decimal) -> Decimal | None:
    if current_loss <= 0:
        return None  # nothing to reduce - not the same as "0% improvement"
    return (current_loss - new_loss) / current_loss


def _evaluate_defensive_hedge(
    *,
    portfolio: Portfolio,
    up_levels: list[OrderBookLevel],
    down_levels: list[OrderBookLevel],
    seconds_remaining: float,
    optimizer_cfg: OptimizerConfig,
    risk_cfg: RiskConfig,
    hedge_timing_cfg: HedgeTimingConfig,
) -> HedgeDecision:
    current_wcp = portfolio.get_worst_case_profit()
    current_loss = portfolio.get_max_loss()

    # The 90-120s sub-band demands double the normal improvement bar -
    # "seguir priorizando hedge rentable, permitir hedge parcial
    # solamente si mejora mucho worst_case" (this repo's own
    # interpretation of "mejora mucho", see config.yaml's comment).
    required_pct = hedge_timing_cfg.min_worst_case_improvement_pct
    if seconds_remaining >= hedge_timing_cfg.defensive_hedge_start_seconds:
        required_pct *= 2

    candidates = generate_lagging_side_candidates(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels,
        hedge_timing_cfg=hedge_timing_cfg, optimizer_cfg=optimizer_cfg,
    )
    if not candidates:
        return _no_op_decision(portfolio, seconds_remaining, "defensive hedge: sin liquidez visible en el lado rezagado")

    best = None
    best_improvement = Decimal(0)
    rejection_reasons: list[str] = []
    for cand in candidates:
        verdict = check_candidate(
            side=cand.side, requested_quantity=cand.requested_quantity, fill=cand.fill,
            portfolio_before=portfolio, portfolio_after=cand.new_portfolio,
            seconds_remaining=seconds_remaining, risk=risk_cfg,
        )
        if not verdict.allowed:
            rejection_reasons.append(f"{cand.side} x{cand.requested_quantity:.2f}: {verdict.reason}")
            continue
        new_loss = cand.new_portfolio.get_max_loss()
        pct = _loss_reduction_pct(current_loss, new_loss)
        if pct is None or pct < required_pct:
            continue
        # Absolute floor, independent of the relative pct check above: a
        # candidate can satisfy "cuts max_loss by 20%+" while still locking
        # in an ugly guaranteed_profit (e.g. -$0.39 on a $0.50 opening leg,
        # confirmed live 2026-08-08) - reject it and let the window ride
        # unhedged rather than pay for expensive "insurance". See
        # max_forced_hedge_loss_usd's comment in config.yaml for the
        # real-data justification.
        if cand.new_portfolio.get_guaranteed_profit() < -risk_cfg.max_forced_hedge_loss_usd:
            continue
        improvement = current_loss - new_loss
        if best is None or improvement > best_improvement:
            best, best_improvement = cand, improvement

    if best is None:
        reason = (
            "candidatos de hedge defensivo rechazados por riesgo: " + "; ".join(rejection_reasons[:3])
            if rejection_reasons
            else (
                f"ningun hedge defensivo mejora el worst-case al menos {required_pct:.0%} "
                f"sin superar la perdida maxima aceptable (${risk_cfg.max_forced_hedge_loss_usd:.2f})"
            )
        )
        return _no_op_decision(portfolio, seconds_remaining, reason)

    new_wcp = best.new_portfolio.get_worst_case_profit()
    new_loss = best.new_portfolio.get_max_loss()
    pct = _loss_reduction_pct(current_loss, new_loss)
    reason = f"{best.side} x{best.fill.filled_quantity:.2f} reduce max loss de ${current_loss:.2f} a ${new_loss:.2f} ({pct:.0%})"
    return HedgeDecision(
        candidate=best, hedge_mode=DEFENSIVE_HEDGE, reason=reason, seconds_remaining=seconds_remaining,
        current_worst_case_profit=current_wcp, new_worst_case_profit=new_wcp,
        current_max_loss=current_loss, new_max_loss=new_loss,
        loss_reduction=current_loss - new_loss, loss_reduction_pct=pct,
    )


def _evaluate_emergency_hedge(
    *,
    portfolio: Portfolio,
    up_levels: list[OrderBookLevel],
    down_levels: list[OrderBookLevel],
    seconds_remaining: float,
    optimizer_cfg: OptimizerConfig,
    risk_cfg: RiskConfig,
    hedge_timing_cfg: HedgeTimingConfig,
) -> HedgeDecision:
    current_wcp = portfolio.get_worst_case_profit()
    current_loss = portfolio.get_max_loss()

    # MODE C exists to defend an EXISTING exposure as the clock runs out -
    # with nothing bought yet, current_worst_case_profit is exactly 0
    # (nothing at risk), so there is nothing to "minimize" and no reason
    # to start chasing a hedge this late against a market that may be
    # moving fast. Confirmed live 2026-08-08: a position opened from
    # scratch with ~28s left, into a market moving hard in one direction,
    # needed 12 alternating 1-share buys just to keep shares tied, paying
    # worse prices each round (one matched pair cost 0.13+0.95=$1.08) and
    # closed guaranteed-negative (-$0.35) even though every individual
    # buy technically "improved" the worst case versus the position that
    # opening itself had just created.
    if portfolio.total_cost == 0:
        return _no_op_decision(
            portfolio, seconds_remaining, "emergency hedge: sin posicion existente que defender, no abrir nueva tan cerca del cierre"
        )

    candidates = generate_lagging_side_candidates(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels,
        hedge_timing_cfg=hedge_timing_cfg, optimizer_cfg=optimizer_cfg,
    )
    approved: list[Candidate] = []
    rejection_reasons: list[str] = []
    for cand in candidates:
        verdict = check_candidate(
            side=cand.side, requested_quantity=cand.requested_quantity, fill=cand.fill,
            portfolio_before=portfolio, portfolio_after=cand.new_portfolio,
            seconds_remaining=seconds_remaining, risk=risk_cfg,
        )
        if verdict.allowed:
            approved.append(cand)
        else:
            rejection_reasons.append(f"{cand.side} x{cand.requested_quantity:.2f}: {verdict.reason}")

    if not approved:
        reason = (
            "candidatos de emergency hedge rechazados por riesgo: " + "; ".join(rejection_reasons[:3])
            if rejection_reasons
            else "emergency hedge: sin liquidez visible en el lado rezagado"
        )
        return _no_op_decision(portfolio, seconds_remaining, reason)

    # No RELATIVE profit floor - MODE C doesn't require the result to be
    # positive, unlike MODE A/B - but it still must never make the worst
    # case WORSE than just leaving the position alone (see the
    # buy-more-of-a-tied-side proof below), AND it must never pay more
    # than max_forced_hedge_loss_usd for that "improvement". Confirmed
    # live 2026-08-08: with 29s left and DOWN lagging, the only liquidity
    # left was at $0.99 - buying it nudged guaranteed_profit from -$0.4987
    # to -$0.4857 (technically better than not hedging, per the proof
    # below) but locked in a near-total loss on a position whose still-
    # open single leg (DOWN @ $0.39) had an expected value of roughly
    # +$0.14 if just left alone for the coin flip. An absolute floor here
    # means MODE C sometimes does nothing and eats full variance on the
    # open leg - that is intentional, not a bug: forced "insurance" this
    # expensive has worse EV than no insurance at all.
    affordable = [
        c for c in approved if c.new_portfolio.get_guaranteed_profit() >= -risk_cfg.max_forced_hedge_loss_usd
    ]
    if not affordable:
        return _no_op_decision(
            portfolio, seconds_remaining,
            f"emergency hedge: todos los candidatos superan la perdida maxima aceptable "
            f"(${risk_cfg.max_forced_hedge_loss_usd:.2f}) - se deja la posicion sin cubrir",
        )

    # Buying more of a tied position's tie-break side (the only kind of
    # candidate generate_lagging_side_candidates produces once tied)
    # provably lowers worst_case_profit for ANY price > 0: with
    # up_shares == down_shares == S and guaranteed_profit G, buying x more
    # of one side at price p makes profit_if_up/profit_if_down =
    # G + x(1-p) and G - p*x respectively - the second is always the
    # smaller one for x, p > 0, and it strictly DECREASES as x grows.
    # Confirmed live 2026-08-08: a tied position already sitting at +$0.10
    # guaranteed (an 8%+ edge) kept getting bought into by MODE C anyway,
    # ending at -$1.48 twelve orders later - "no profit floor" was being
    # read as "no floor at all", including no floor against actively
    # destroying an already-locked gain.
    best = max(affordable, key=lambda c: c.new_portfolio.get_worst_case_profit())
    new_wcp = best.new_portfolio.get_worst_case_profit()
    if new_wcp <= current_wcp:
        return _no_op_decision(
            portfolio, seconds_remaining,
            f"emergency hedge: ningun candidato mejora el worst-case actual (${current_wcp:.2f}) - se mantiene la posicion",
        )
    new_loss = best.new_portfolio.get_max_loss()
    reason = (
        f"Emergency hedge seleccionado porque quedan {seconds_remaining:.0f}s - "
        f"{best.side} x{best.fill.filled_quantity:.2f} lleva worst-case de ${current_wcp:.2f} a ${new_wcp:.2f}"
    )
    return HedgeDecision(
        candidate=best, hedge_mode=EMERGENCY_HEDGE, reason=reason, seconds_remaining=seconds_remaining,
        current_worst_case_profit=current_wcp, new_worst_case_profit=new_wcp,
        current_max_loss=current_loss, new_max_loss=new_loss,
        loss_reduction=current_loss - new_loss, loss_reduction_pct=_loss_reduction_pct(current_loss, new_loss),
    )


def evaluate_hedge(
    *,
    portfolio: Portfolio,
    up_levels: list[OrderBookLevel],
    down_levels: list[OrderBookLevel],
    seconds_remaining: float,
    optimizer_cfg: OptimizerConfig,
    risk_cfg: RiskConfig,
    hedge_timing_cfg: HedgeTimingConfig,
) -> HedgeDecision:
    """Top-level entry point implementing the full state machine:
    MODE A (always tried first) -> MODE B (defensive, 30s-120s band if A
    found nothing) -> MODE C (emergency, <30s if A found nothing). Times
    are configurable via hedge_timing_cfg; see config.yaml for the exact
    band boundaries and rationale."""
    profit_decision = choose_action(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels,
        seconds_remaining=seconds_remaining, optimizer_cfg=optimizer_cfg, risk_cfg=risk_cfg,
    )
    # choose_action's own bar is just "improves guaranteed_profit over
    # RIGHT NOW" (delta > 0) or "projects toward a future lock" - neither
    # requires the RESULT to actually clear real profitability. That was
    # fine back when choose_action was the only mode (any improvement was
    # worth taking), but now that MODE B exists specifically for
    # "improves things but isn't a real profit hedge yet", a candidate
    # that only nudges guaranteed_profit from deeply negative to less-
    # deeply negative must NOT be counted as MODE A - confirmed live
    # 2026-08-08: without this check, a tiny $10 top-up that left
    # guaranteed_profit at -$37.50 was being reported as "PROFIT_HEDGE",
    # starving MODE B/C of a candidate that badly needed the "reduce the
    # worst case" framing instead of the "lock in a profit" one.
    is_genuine_lock = (
        profit_decision.candidate is not None
        and profit_decision.candidate.new_portfolio.get_guaranteed_profit() >= risk_cfg.minimum_guaranteed_profit_usd
    )
    # A candidate justified purely by cheap_open_threshold (see
    # choose_action's opens_on_cheap_price) is NOT a genuine lock by
    # design - it's a deliberate speculative open, on the premise that
    # BTC's own oscillation cheapens the other side later and MODE B/C
    # bound the downside if it doesn't. It must still execute here
    # (that's the whole point of adding it 2026-08-08 after the bot went
    # 3.5+ hours never opening anything), just without being held to the
    # "did this already lock a profit" bar the tiny-marginal-improvement
    # case above needed to be excluded by.
    cand = profit_decision.candidate
    is_cheap_open = False
    if cand is not None:
        side_shares = portfolio.up_shares if cand.side == UP else portfolio.down_shares
        other_shares = portfolio.down_shares if cand.side == UP else portfolio.up_shares
        is_cheap_open = (
            side_shares <= other_shares
            and cand.fill.total_cost <= optimizer_cfg.max_opening_order_usd
            and cand.fill.vwap_price is not None
            and cand.fill.vwap_price <= optimizer_cfg.cheap_open_threshold
        )
    if is_genuine_lock or is_cheap_open:
        current_wcp = portfolio.get_worst_case_profit()
        current_loss = portfolio.get_max_loss()
        new_wcp = profit_decision.candidate.new_portfolio.get_worst_case_profit()
        new_loss = profit_decision.candidate.new_portfolio.get_max_loss()
        return HedgeDecision(
            candidate=profit_decision.candidate, hedge_mode=PROFIT_HEDGE, reason=profit_decision.reason,
            seconds_remaining=seconds_remaining,
            current_worst_case_profit=current_wcp, new_worst_case_profit=new_wcp,
            current_max_loss=current_loss, new_max_loss=new_loss,
            loss_reduction=current_loss - new_loss, loss_reduction_pct=_loss_reduction_pct(current_loss, new_loss),
        )

    if seconds_remaining >= hedge_timing_cfg.profit_hedge_only_seconds:
        return _no_op_decision(portfolio, seconds_remaining, profit_decision.reason)

    if seconds_remaining < hedge_timing_cfg.emergency_hedge_start_seconds:
        return _evaluate_emergency_hedge(
            portfolio=portfolio, up_levels=up_levels, down_levels=down_levels,
            seconds_remaining=seconds_remaining, optimizer_cfg=optimizer_cfg, risk_cfg=risk_cfg,
            hedge_timing_cfg=hedge_timing_cfg,
        )

    return _evaluate_defensive_hedge(
        portfolio=portfolio, up_levels=up_levels, down_levels=down_levels,
        seconds_remaining=seconds_remaining, optimizer_cfg=optimizer_cfg, risk_cfg=risk_cfg,
        hedge_timing_cfg=hedge_timing_cfg,
    )
