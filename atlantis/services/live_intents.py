from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_QUEUE_PATH = Path("state/live_intents_queue.jsonl")


def enqueue_intent(intent_type: str, row: dict, *, queue_path: Path = DEFAULT_QUEUE_PATH) -> None:
    """Append-only local write. Deliberately imports nothing from
    atlantis.polymarket.clob_client / py_clob_client_v2 - this is the
    isolation boundary between paper screening (which calls this) and real
    execution (which only reads the file this writes)."""
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "intent_type": intent_type,
        "condition_id": row.get("condition_id", ""),
        "asset": row.get("asset", ""),
        "title": row.get("title", ""),
        "slug": row.get("slug", ""),
        "outcome": row.get("outcome", ""),
        "traders": row.get("traders", ""),
        "signal_price": row.get("current_price", row.get("entry_price", "")),
    }
    with queue_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_intents(queue_path: Path = DEFAULT_QUEUE_PATH) -> list[dict]:
    if not queue_path.exists():
        return []
    intents = []
    with queue_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                intents.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return intents
