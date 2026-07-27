from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
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
    through this. Deliberately the ONLY module that imports py_clob_client -
    that import boundary is what keeps a bug here from ever being reachable
    from the free paper-screening pipeline.
    """

    def __init__(self, settings: LiveSettings) -> None:
        # Deferred import: this class must be importable (e.g. for isinstance
        # checks or type hints elsewhere) even in environments where
        # py-clob-client isn't installed - only constructing one requires it.
        from py_clob_client.client import ClobClient

        self.settings = settings
        private_key = read_private_key(settings)
        self._client = ClobClient(
            settings.clob_host,
            chain_id=settings.chain_id,
            key=private_key,
            signature_type=settings.signature_type,
            funder=settings.funder_address or None,
        )
        creds = self._client.create_or_derive_api_creds()
        self._client.set_api_creds(creds)

    def get_address(self) -> str:
        return self._client.get_address()

    def get_balance(self) -> dict[str, Any]:
        from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

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

    def get_orders(self, **params: Any) -> list[dict[str, Any]]:
        try:
            return self._client.get_orders(**params) or []
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
        """BUY side: amount is USDC notional to spend (per py-clob-client
        docs) - VERIFY against the real fill on the Phase 2 test order before
        trusting this for the full $20 sizing."""
        from py_clob_client.clob_types import MarketOrderArgs
        from py_clob_client.order_builder.constants import BUY

        return self._place_market_order(token_id, usdc_amount, BUY, max_slippage_pct, MarketOrderArgs)

    def place_market_sell(
        self, token_id: str, shares_amount: Decimal, max_slippage_pct: Decimal = Decimal("3")
    ) -> OrderResult:
        """SELL side: amount is shares held (per py-clob-client docs) -
        VERIFY against the real fill on the Phase 2 test order."""
        from py_clob_client.clob_types import MarketOrderArgs
        from py_clob_client.order_builder.constants import SELL

        return self._place_market_order(token_id, shares_amount, SELL, max_slippage_pct, MarketOrderArgs)

    def _place_market_order(
        self,
        token_id: str,
        amount: Decimal,
        side: str,
        max_slippage_pct: Decimal,
        market_order_args_cls: Any,
    ) -> OrderResult:
        from urllib.error import HTTPError, URLError

        try:
            current_price = self.get_price(token_id, side)
            protective_price = None
            if current_price is not None:
                if side == "BUY":
                    protective_price = current_price * (1 + max_slippage_pct / 100)
                else:
                    protective_price = current_price * (1 - max_slippage_pct / 100)

            order_args = market_order_args_cls(
                token_id=token_id,
                amount=float(amount),
                side=side,
                price=float(protective_price) if protective_price is not None else None,
            )
            signed_order = self._client.create_market_order(order_args)
            response = self._client.post_order(signed_order)
            return _parse_order_response(response)
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


def _parse_order_response(response: Any) -> OrderResult:
    # NOTE: written before ever seeing a real response payload. Phase 2's
    # test order must capture the raw dict and this function must be
    # revisited/corrected against that real shape before Phase 4 - treat
    # this as a best-effort placeholder, not a verified parser.
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
    success = bool(response.get("success", response.get("orderID") or response.get("orderId")))
    order_id = response.get("orderID") or response.get("orderId") or response.get("id")
    status = str(response.get("status", "UNKNOWN"))
    filled = response.get("size_matched") or response.get("filledSize")
    price = response.get("price") or response.get("avgPrice")
    return OrderResult(
        success=success,
        order_id=str(order_id) if order_id else None,
        status=status,
        filled_size=Decimal(str(filled)) if filled is not None else None,
        avg_fill_price=Decimal(str(price)) if price is not None else None,
        raw_response=response,
        error=None if success else str(response.get("errorMsg") or response.get("error") or "orden no confirmada"),
    )


def build_live_client(settings: LiveSettings) -> LiveClobClient:
    return LiveClobClient(settings)
