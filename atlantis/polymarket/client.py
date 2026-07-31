from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from atlantis.config import Settings


class PolymarketClient:
    def __init__(
        self,
        data_api_base: str,
        gamma_api_base: str,
        request_sleep_seconds: float = 0.15,
        timeout_seconds: float = 30,
    ) -> None:
        self.data_api_base = data_api_base.rstrip("/")
        self.gamma_api_base = gamma_api_base.rstrip("/")
        self.request_sleep_seconds = request_sleep_seconds
        self.timeout_seconds = timeout_seconds

    def get_leaderboard(
        self,
        *,
        category: str = "OVERALL",
        time_period: str = "DAY",
        order_by: str = "PNL",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self._get_data(
            "/v1/leaderboard",
            {
                "category": category,
                "timePeriod": time_period,
                "orderBy": order_by,
                "limit": limit,
                "offset": offset,
            },
        )

    def iter_leaderboard(
        self,
        *,
        category: str = "OVERALL",
        time_period: str = "DAY",
        order_by: str = "PNL",
        max_rows: int = 250,
    ):
        offset = 0
        while offset <= 1000 and offset < max_rows:
            limit = min(50, max_rows - offset)
            batch = self.get_leaderboard(
                category=category,
                time_period=time_period,
                order_by=order_by,
                limit=limit,
                offset=offset,
            )
            if not batch:
                break
            for row in batch:
                yield row
            if len(batch) < limit:
                break
            offset += len(batch)

    def get_user_trades(
        self,
        *,
        wallet_address: str,
        limit: int = 500,
        offset: int = 0,
        start: int | None = None,
        end: int | None = None,
    ) -> list[dict[str, Any]]:
        return self._get_data(
            "/trades",
            {
                "user": wallet_address,
                "limit": limit,
                "offset": offset,
                "start": start,
                "end": end,
            },
        )

    def iter_user_trades(
        self,
        *,
        wallet_address: str,
        max_rows: int = 5000,
        start: int | None = None,
        end: int | None = None,
    ):
        offset = 0
        while offset < max_rows:
            limit = min(500, max_rows - offset)
            batch = self.get_user_trades(
                wallet_address=wallet_address,
                limit=limit,
                offset=offset,
                start=start,
                end=end,
            )
            if not batch:
                break
            for row in batch:
                yield row
            if len(batch) < limit:
                break
            offset += len(batch)

    def get_market_trades(
        self,
        *,
        condition_id: str,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """All trades for a market (any wallet) - the same /trades endpoint
        as get_user_trades, but filtered by market instead of by user. Used
        to find who's actually trading a given market, for verticals where
        the interesting wallets aren't necessarily top-N on the general
        leaderboard (see esports_traders.py::discover_esports_traders)."""
        return self._get_data(
            "/trades",
            {
                "market": condition_id,
                "limit": limit,
                "offset": offset,
            },
        )

    def iter_market_trades(self, *, condition_id: str, max_rows: int = 2000):
        offset = 0
        while offset < max_rows:
            limit = min(500, max_rows - offset)
            batch = self.get_market_trades(condition_id=condition_id, limit=limit, offset=offset)
            if not batch:
                break
            for row in batch:
                yield row
            if len(batch) < limit:
                break
            offset += len(batch)

    def get_events(
        self,
        *,
        tag_slug: str,
        closed: bool = False,
        order: str = "volume24hr",
        ascending: bool = False,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return self._get_gamma(
            "/events",
            {
                "tag_slug": tag_slug,
                "closed": str(closed).lower(),
                "order": order,
                "ascending": str(ascending).lower(),
                "limit": limit,
            },
        )

    def get_user_positions(
        self,
        *,
        wallet_address: str,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self._get_data(
            "/positions",
            {
                "user": wallet_address,
                "limit": limit,
                "offset": offset,
                "sizeThreshold": 0,
                "sortBy": "CURRENT",
                "sortDirection": "DESC",
            },
        )

    # The API used to silently cap this at 50 regardless of the requested
    # limit; it now hard-rejects any limit above 50 with a 400 error, so the
    # default has to match the real ceiling instead of just the historical
    # observed page size.
    def get_closed_positions(
        self,
        *,
        wallet_address: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        return self._get_data(
            "/closed-positions",
            {
                "user": wallet_address,
                "limit": limit,
                "offset": offset,
            },
        )

    def iter_closed_positions(
        self,
        *,
        wallet_address: str,
        max_rows: int = 5000,
    ):
        # The API rejects any limit above 50 (400 Bad Request) and used to
        # silently cap it at 50 too, so we must advance the offset by what
        # was actually returned, not by the requested limit - advancing by
        # a larger requested limit would skip rows and undercount.
        offset = 0
        rows_yielded = 0
        while rows_yielded < max_rows:
            batch = self.get_closed_positions(wallet_address=wallet_address, limit=50, offset=offset)
            if not batch:
                break
            for row in batch:
                yield row
                rows_yielded += 1
                if rows_yielded >= max_rows:
                    return
            offset += len(batch)

    def get_event_by_slug(self, slug: str) -> dict[str, Any]:
        return self._get_gamma(f"/events/slug/{slug}", {})

    def _get_data(self, path: str, params: dict[str, Any]) -> Any:
        return self._get(f"{self.data_api_base}{path}", params)

    def _get_gamma(self, path: str, params: dict[str, Any]) -> Any:
        return self._get(f"{self.gamma_api_base}{path}", params)

    RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
    MAX_RETRIES = 4

    def _get(self, url: str, params: dict[str, Any]) -> Any:
        query = urllib.parse.urlencode(
            {key: value for key, value in params.items() if value is not None},
            doseq=True,
        )
        full_url = f"{url}?{query}" if query else url
        request = urllib.request.Request(
            full_url,
            headers={
                "Accept": "application/json",
                "User-Agent": "atlantis-polymarket-collector/0.1",
            },
        )

        last_error: Exception | None = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    body = response.read().decode("utf-8")
                return json.loads(body)
            except urllib.error.HTTPError as exc:
                details = exc.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(f"Polymarket API error {exc.code} for {full_url}: {details}")
                if exc.code not in self.RETRYABLE_STATUS_CODES or attempt == self.MAX_RETRIES:
                    raise last_error from exc
            except urllib.error.URLError as exc:
                last_error = RuntimeError(f"Network error for {full_url}: {exc}")
                if attempt == self.MAX_RETRIES:
                    raise last_error from exc
            except (TimeoutError, ConnectionError, OSError) as exc:
                # A read timeout mid-response (e.g. the socket stalls after the
                # connection is already open) raises a raw TimeoutError/OSError,
                # not urllib.error.URLError - without this branch it escaped
                # retry entirely and crashed the whole pipeline.
                last_error = RuntimeError(f"Network error for {full_url}: {exc}")
                if attempt == self.MAX_RETRIES:
                    raise last_error from exc
            finally:
                if self.request_sleep_seconds > 0:
                    time.sleep(self.request_sleep_seconds)

            backoff = (2**attempt) * 1.0
            time.sleep(backoff)

        raise last_error  # unreachable, keeps type checkers happy


def build_client(settings: Settings) -> PolymarketClient:
    return PolymarketClient(
        data_api_base=settings.polymarket_data_api_base,
        gamma_api_base=settings.polymarket_gamma_api_base,
        request_sleep_seconds=settings.request_sleep_seconds,
        timeout_seconds=settings.request_timeout_seconds,
    )
