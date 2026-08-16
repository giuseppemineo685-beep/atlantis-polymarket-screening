from datetime import datetime, timezone

from atlantis.forex.market_hours import market_is_open, ok_to_open_new_position


def _dt(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def test_open_on_a_normal_weekday():
    assert market_is_open(_dt(2026, 8, 18, 12, 0)) == (True, "mercado abierto")  # Tuesday
    assert ok_to_open_new_position(_dt(2026, 8, 18, 12, 0))[0] is True


def test_closed_all_saturday():
    ok, reason = market_is_open(_dt(2026, 8, 15, 12, 0))  # Saturday
    assert ok is False
    assert "sabado" in reason


def test_closed_friday_after_close():
    ok, _ = market_is_open(_dt(2026, 8, 14, 22, 0))  # Friday 22:00 UTC
    assert ok is False


def test_open_friday_before_close_but_blocked_for_new_entries_near_close():
    is_open, _ = market_is_open(_dt(2026, 8, 14, 20, 0))  # Friday 20:00, still open
    assert is_open is True
    ok, reason = ok_to_open_new_position(_dt(2026, 8, 14, 20, 0))
    assert ok is False
    assert "cierre semanal" in reason


def test_closed_sunday_before_open():
    ok, _ = market_is_open(_dt(2026, 8, 16, 20, 0))  # Sunday 20:00 UTC
    assert ok is False


def test_open_sunday_after_open_but_blocked_right_after_open():
    is_open, _ = market_is_open(_dt(2026, 8, 16, 22, 0))  # Sunday 22:00, open
    assert is_open is True
    ok, reason = ok_to_open_new_position(_dt(2026, 8, 16, 22, 0))
    assert ok is False
    assert "recien abrio" in reason


def test_open_sunday_well_after_open():
    ok, _ = ok_to_open_new_position(_dt(2026, 8, 16, 23, 30))  # Sunday 23:30, past the buffer
    assert ok is True
