from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import gcd
from typing import Any

from atlantis.live.config import LiveSettings, read_private_key


@dataclass(frozen=True)
class OrderResult:
    success: bool
    order_id: str | None
    status: str
    filled_size: Decimal | None
    avg_fill_price: Decimal | None
    raw_response: Any
    error: str | None


class LiveClobClient:
    """Thin wrapper around py-clob-client, mirroring how
    atlantis.polymarket.client.PolymarketClient wraps urllib: one place that
    owns the network/auth details, everything else in the codebase goes
    through this. Deliberately the ONLY module that imports py_clob_client_v2 -
    that import boundary is what keeps a bug here from ever being reachable
    from the free paper-screening pipeline.
    """

    def __init__(self, settings: LiveSettings) -> None:
        # Deferred import: this class must be importable (e.g. for isinstance
        # checks or type hints elsewhere) even in environments where
        # py-clob-client isn't installed - only constructing one requires it.
        from py_clob_client_v2.client import ClobClient

        self.settings = settings
        private_key = read_private_key(settings)
        self._client = ClobClient(
            settings.clob_host,
            chain_id=settings.chain_id,
            key=private_key,
            signature_type=settings.signature_type,
            funder=settings.funder_address or None,
        )
        creds = self._client.create_or_derive_api_key()
        self._client.set_api_creds(creds)

    def get_address(self) -> str:
        return self._client.get_address()

    def get_balance(self) -> dict[str, Any]:
        from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        return self._client.get_balance_allowance(params)

    def get_price(self, token_id: str, side: str) -> Decimal | None:
        try:
            result = self._client.get_price(token_id, side)
        except Exception:
            return None
        price = result.get("price") if isinstance(result, dict) else result
        return Decimal(str(price)) if price is not None else None

    def get_tick_size(self, token_id: str) -> Decimal | None:
        try:
            return Decimal(str(self._client.get_tick_size(token_id)))
        except Exception:
            return None

    def get_market(self, condition_id: str) -> dict[str, Any] | None:
        try:
            return self._client.get_market(condition_id)
        except Exception:
            return None

    def get_open_orders(self, **params: Any) -> list[dict[str, Any]]:
        try:
            return self._client.get_open_orders(**params) or []
        except Exception:
            return []

    def get_trades(self, **params: Any) -> list[dict[str, Any]]:
        try:
            return self._client.get_trades(**params) or []
        except Exception:
            return []

    def place_market_buy(
        self, token_id: str, usdc_amount: Decimal, max_slippage_pct: Decimal = Decimal("3")
    ) -> OrderResult:
        """Spend up to usdc_amount USDC buying token_id, executed as an
        aggressive FOK limit order (fills immediately at the protective price
        or better, or cancels entirely - functionally a market order, but see
        the note on _place_fok_order for why it isn't a literal MarketOrderArgs)."""
        return self._place_fok_order(token_id, "BUY", usdc_amount, max_slippage_pct)

    def place_market_sell(
        self, token_id: str, shares_amount: Decimal, max_slippage_pct: Decimal = Decimal("3")
    ) -> OrderResult:
        """Sell shares_amount shares of token_id, same FOK-immediate-or-cancel
        semantics as place_market_buy."""
        return self._place_fok_order(token_id, "SELL", shares_amount, max_slippage_pct)

    def _place_fok_order(
        self, token_id: str, side: str, amount: Decimal, max_slippage_pct: Decimal
    ) -> OrderResult:
        # A true MarketOrderArgs order lets the exchange derive maker/taker
        # amounts from (dollar amount / price), and that division routinely
        # produces an effective price with more decimal precision than the
        # market's tick size allows - confirmed against a real order that was
        # rejected with "price 0.5300259712725924 breaks minimum tick size
        # rule 0.01" even though our quoted price (0.53) was itself clean.
        # A limit order sidesteps this: WE round both price (to the tick)
        # and size before asking the library to compute maker_amount =
        # size * price, which stays clean because both inputs already are.
        # OrderType.FOK gives the same "fill now or don't fill at all"
        # behavior as a market order, so nothing about the trading semantics
        # changes - only which library code path builds the order.
        from urllib.error import HTTPError, URLError

        from py_clob_client_v2.clob_types import OrderArgs, OrderType
        from py_clob_client_v2.order_builder.constants import BUY, SELL

        clob_side = BUY if side == "BUY" else SELL

        try:
            current_price = self.get_price(token_id, side)
            if current_price is None or current_price <= 0:
                return OrderResult(
                    success=False,
                    order_id=None,
                    status="ERROR",
                    filled_size=None,
                    avg_fill_price=None,
                    raw_response=None,
                    error=f"No se pudo obtener precio de mercado para {token_id}",
                )

            tick_size = self.get_tick_size(token_id) or Decimal("0.01")
            if side == "BUY":
                raw_price = current_price * (1 + max_slippage_pct / 100)
                # Round UP - a BUY protective price must never round down
                # below what we actually intend to tolerate.
                limit_price = (raw_price / tick_size).to_integral_value(rounding="ROUND_CEILING") * tick_size
                # The exchange rounds our submitted size to 2 decimals
                # itself, then computes maker_amount (USDC spent) = size *
                # price - and rejects a marketable BUY if that product has
                # more than 2 decimal places (confirmed against a real
                # rejection: "maker amount supports a max accuracy of 2
                # decimals"). Two 2dp numbers only multiply to a 2dp-clean
                # result when size is a multiple of 1/gcd(price_cents, 100)
                # dollars-worth of shares - e.g. at price 0.54 (gcd=2), size
                # must land on a multiple of 0.50, not just any cent. Solve
                # for that step size directly instead of guessing.
                price_cents = int((limit_price * 100).to_integral_value(rounding="ROUND_HALF_UP"))
                step_cents = 100 // gcd(price_cents, 100)
                raw_size_cents = int((amount / limit_price * 100).to_integral_value(rounding="ROUND_FLOOR"))
                valid_size_cents = (raw_size_cents // step_cents) * step_cents
                if valid_size_cents <= 0:
                    return OrderResult(
                        success=False,
                        order_id=None,
                        status="ERROR",
                        filled_size=None,
                        avg_fill_price=None,
                        raw_response=None,
                        error=(
                            f"amount demasiado chico para este precio ({limit_price}): "
                            f"el tamano minimo valido es {Decimal(step_cents) / 100} shares"
                        ),
                    )
                size = Decimal(valid_size_cents) / 100
            else:
                raw_price = current_price * (1 - max_slippage_pct / 100)
                # Round DOWN - never round up above the floor we intend to accept.
                limit_price = (raw_price / tick_size).to_integral_value(rounding="ROUND_FLOOR") * tick_size
                size = amount.quantize(Decimal("0.01"), rounding="ROUND_DOWN")

            order_args = OrderArgs(
                token_id=token_id,
                price=float(limit_price),
                size=float(size),
                side=clob_side,
            )
            signed_order = self._client.create_order(order_args)
            response = self._client.post_order(signed_order, order_type=OrderType.FOK)
            return _parse_order_response(response, side)
        except (HTTPError, URLError, TimeoutError, ConnectionError, OSError) as exc:
            # Same broad exception discipline as PolymarketClient._get - a
            # raw TimeoutError escaping a narrower catch already crashed the
            # paper pipeline once this session. Here the stakes of an
            # uncaught exception mid-order are worse: it can leave an order
            # in an unknown state instead of just a missed screening cycle.
            return OrderResult(
                success=False,
                order_id=None,
                status="ERROR",
                filled_size=None,
                avg_fill_price=None,
                raw_response=None,
                error=f"{type(exc).__name__}: {exc}",
            )
        except Exception as exc:
            return OrderResult(
                success=False,
                order_id=None,
                status="ERROR",
                filled_size=None,
                avg_fill_price=None,
                raw_response=None,
                error=f"{type(exc).__name__}: {exc}",
            )


def _parse_order_response(response: Any, side: str) -> OrderResult:
    # Verified against a real filled order (Fase 2 test, $2 BUY):
    # {'errorMsg': '', 'orderID': '0x...', 'takingAmount': '3.566036',
    #  'makingAmount': '1.889999', 'status': 'matched',
    #  'transactionsHashes': ['0x...'], 'success': True}
    # For a BUY: makingAmount = USDC paid, takingAmount = shares received.
    # For a SELL: makingAmount = shares given up, takingAmount = USDC received.
    if not isinstance(response, dict):
        return OrderResult(
            success=False,
            order_id=None,
            status="UNKNOWN",
            filled_size=None,
            avg_fill_price=None,
            raw_response=response,
            error="Respuesta inesperada (no es un dict) - revisar manualmente",
        )
    success = bool(response.get("success"))
    order_id = response.get("orderID") or response.get("orderId") or response.get("id")
    status = str(response.get("status", "UNKNOWN"))

    # response.get("success") is the only signal that matters for whether
    # real money moved - a real incident (2026-08-01) had the exchange
    # confirm success=True on a fill while makingAmount/takingAmount came
    # back empty/unparseable, and Decimal(str(...)) raised ConversionSyntax
    # from inside this function. That exception was caught by the outer
    # try/except in _place_fok_order and reported as a plain failed order,
    # which the retry logic (correctly, for an order that never went
    # through) resubmitted every cron cycle - each resubmission ALSO filled
    # for real, since the order kept actually succeeding. One $25 signal
    # turned into a $124 position before anyone noticed. A parse failure on
    # the fill amounts must never be able to flip success=True into
    # success=False.
    def _to_decimal(value: Any) -> Decimal | None:
        if value in (None, ""):
            return None
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None

    making = response.get("makingAmount")
    taking = response.get("takingAmount")
    if side == "BUY":
        usdc_amount = _to_decimal(making)
        shares_amount = _to_decimal(taking)
    else:
        shares_amount = _to_decimal(making)
        usdc_amount = _to_decimal(taking)

    avg_price = (usdc_amount / shares_amount) if (usdc_amount and shares_amount) else None

    error = None
    if not success:
        error = str(response.get("errorMsg") or response.get("error") or "orden no confirmada")
    elif usdc_amount is None or shares_amount is None:
        error = (
            "orden confirmada por el exchange (success=True) pero no se pudo leer "
            "makingAmount/takingAmount de la respuesta - revisar raw_response manualmente"
        )

    return OrderResult(
        success=success,
        order_id=str(order_id) if order_id else None,
        status=status,
        filled_size=shares_amount,
        avg_fill_price=avg_price,
        raw_response=response,
        error=error,
    )


def build_live_client(settings: LiveSettings) -> LiveClobClient:
    return LiveClobClient(settings)
