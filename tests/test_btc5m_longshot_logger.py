import csv
from decimal import Decimal
from pathlib import Path

from atlantis.btc5m_longshot.logger import WINDOW_SUMMARY_FIELDS, compute_session_realized


def _write_summary(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=WINDOW_SUMMARY_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in WINDOW_SUMMARY_FIELDS})


def test_compute_session_realized_missing_file_is_zero(tmp_path):
    assert compute_session_realized(tmp_path / "nope.csv") == Decimal(0)


def test_compute_session_realized_sums_only_resolved_rows(tmp_path):
    path = tmp_path / "summary.csv"
    _write_summary(path, [
        {"window_slug": "a", "side": "Up", "realized_profit": "8.33"},
        {"window_slug": "b", "side": "Down", "realized_profit": "-1"},
        {"window_slug": "c", "side": "Up", "realized_profit": ""},
        {"window_slug": "d", "side": "", "realized_profit": ""},
    ])
    assert compute_session_realized(path) == Decimal("8.33") - Decimal("1")
