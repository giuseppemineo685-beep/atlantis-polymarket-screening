from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    log_level: str
    polymarket_data_api_base: str
    polymarket_gamma_api_base: str
    request_sleep_seconds: float
    request_timeout_seconds: float


def load_settings() -> Settings:
    return Settings(
        database_url=os.getenv("ATLANTIS_DATABASE_URL", ""),
        log_level=os.getenv("ATLANTIS_LOG_LEVEL", "INFO"),
        polymarket_data_api_base=os.getenv(
            "POLYMARKET_DATA_API_BASE", "https://data-api.polymarket.com"
        ).rstrip("/"),
        polymarket_gamma_api_base=os.getenv(
            "POLYMARKET_GAMMA_API_BASE", "https://gamma-api.polymarket.com"
        ).rstrip("/"),
        request_sleep_seconds=float(os.getenv("POLYMARKET_REQUEST_SLEEP_SECONDS", "0.15")),
        request_timeout_seconds=float(os.getenv("POLYMARKET_REQUEST_TIMEOUT_SECONDS", "30")),
    )
