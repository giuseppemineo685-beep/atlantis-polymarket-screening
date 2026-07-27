from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LiveSettings:
    clob_host: str
    chain_id: int
    private_key_path: Path
    funder_address: str
    signature_type: int
    stake_per_signal_usd: float
    initial_bankroll_usd: float
    kill_switch_loss_pct: float
    status_path: Path
    live_trade_log_path: Path
    dryrun_trade_log_path: Path
    intents_queue_path: Path


def load_live_settings() -> LiveSettings:
    return LiveSettings(
        clob_host=os.getenv("POLYMARKET_CLOB_HOST", "https://clob.polymarket.com"),
        chain_id=int(os.getenv("POLYMARKET_CHAIN_ID", "137")),
        # Path to a file containing just the private key - never the raw key
        # in an env var, so it can't leak via `crontab -l` or `ps auxe`.
        private_key_path=Path(
            os.getenv("POLYMARKET_PRIVATE_KEY_PATH", "/root/.atlantis_secrets/polymarket_private_key")
        ),
        funder_address=os.getenv("POLYMARKET_FUNDER_ADDRESS", ""),
        # 1 = email/Google "Magic" proxy-wallet accounts (this user's case).
        # 0 = plain EOA/MetaMask. 2 = Gnosis-Safe browser-wallet proxy.
        signature_type=int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1")),
        stake_per_signal_usd=float(os.getenv("LIVE_STAKE_PER_SIGNAL_USD", "20")),
        initial_bankroll_usd=float(os.getenv("LIVE_INITIAL_BANKROLL_USD", "500")),
        kill_switch_loss_pct=float(os.getenv("LIVE_KILL_SWITCH_LOSS_PCT", "20")),
        status_path=Path(os.getenv("LIVE_STATUS_PATH", "state/live_trading_status.json")),
        live_trade_log_path=Path(os.getenv("LIVE_TRADE_LOG_PATH", "outputs/live_trade_log.csv")),
        dryrun_trade_log_path=Path(
            os.getenv("LIVE_DRYRUN_TRADE_LOG_PATH", "outputs/live_trade_log_dryrun.csv")
        ),
        intents_queue_path=Path(os.getenv("LIVE_INTENTS_QUEUE_PATH", "state/live_intents_queue.jsonl")),
    )


def read_private_key(settings: LiveSettings) -> str:
    if not settings.private_key_path.exists():
        raise FileNotFoundError(
            f"No existe el archivo de clave privada en {settings.private_key_path}. "
            "Cargala vos mismo por SSH directo en el VPS - nunca por chat."
        )
    key = settings.private_key_path.read_text().strip()
    if not key:
        raise ValueError(f"El archivo {settings.private_key_path} esta vacio.")
    return key
