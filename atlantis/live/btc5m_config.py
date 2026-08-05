from __future__ import annotations

import os
from pathlib import Path

from atlantis.live.config import LiveSettings

# Deliberately does NOT reuse atlantis.live.config.load_live_settings() -
# that function reads shared, non-namespaced env vars (LIVE_STAKE_PER_SIGNAL_USD,
# LIVE_STATUS_PATH, etc.) that belong to the sports vertical. Reusing it
# directly would collide: this pilot needs its own stake amount, its own
# kill-switch state file, and its own trade log, while still trading
# through the SAME real Polymarket account (same credentials/funder).


def load_btc5m_live_settings() -> LiveSettings:
    return LiveSettings(
        clob_host=os.getenv("POLYMARKET_CLOB_HOST", "https://clob.polymarket.com"),
        chain_id=int(os.getenv("POLYMARKET_CHAIN_ID", "137")),
        private_key_path=Path(
            os.getenv("POLYMARKET_PRIVATE_KEY_PATH", "/root/.atlantis_secrets/polymarket_private_key")
        ),
        funder_address=os.getenv("POLYMARKET_FUNDER_ADDRESS", ""),
        signature_type=int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1")),
        # $1 hit a real exchange constraint (2026-08-04): our own size
        # math floors down to stay within budget, which for $1 at a price
        # near the 0.99 cap computed exactly $0.99 of notional - the
        # exchange rejected it outright ("invalid amount ... min size: 1",
        # a real $1.00 minimum notional per order that our own floor-
        # rounding can undercut by a cent). $2 clears that floor with a
        # cent of margin. (A later "5 shares minimum" theory that pushed
        # this to $6 was disproven 2026-08-05 - the owner placed a real $1
        # marketable buy on Polymarket's own UI with no complaint; every
        # order failure that looked like a size problem was actually the
        # batching-timing bug in run_btc5m_live_execution.py placing every
        # order at ~0-2s before close, not an exchange-side size floor -
        # see clob_client.py's _place_order for the removed check.)
        stake_per_signal_usd=float(os.getenv("BTC5M_STAKE_PER_SIGNAL_USD", "2")),
        # Pilot capital allocated to this vertical (2026-08-04, owner's
        # explicit instruction: "capital dispuesto 100usd, si se pierde
        # todo que se pause"). Not used by kill_switch.py (that module is
        # sports-only and has auto-trip disabled by a separate decision) -
        # this pilot has its own, independent auto-pause check in
        # run_btc5m_live_execution.py that reads this value directly.
        initial_bankroll_usd=float(os.getenv("BTC5M_PILOT_CAPITAL_USD", "100")),
        kill_switch_loss_pct=100.0,
        status_path=Path(os.getenv("BTC5M_LIVE_STATUS_PATH", "state/live_trading_status_btc5m.json")),
        live_trade_log_path=Path(
            os.getenv("BTC5M_LIVE_TRADE_LOG_PATH", "outputs/live_trade_log_btc5m.csv")
        ),
        dryrun_trade_log_path=Path(
            os.getenv("BTC5M_DRYRUN_TRADE_LOG_PATH", "outputs/live_trade_log_btc5m_dryrun.csv")
        ),
        # Unused - this vertical has no BUY/SELL intent queue (no early-exit
        # concept, these markets resolve on their own within 5 minutes).
        intents_queue_path=Path("state/live_intents_queue_btc5m_unused.jsonl"),
    )
