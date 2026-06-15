from datetime import datetime

from app.dates import classify_due

# A fixed "now" so the tests are deterministic — no real clock.
NOW = datetime(2026, 6, 15, 12, 0, 0)


def test_due_in_the_past_is_past_due():
    assert classify_due(datetime(2026, 6, 14, 9, 0, 0), NOW) == "past_due"


def test_due_later_today_is_due_today():
    assert classify_due(datetime(2026, 6, 15, 18, 0, 0), NOW) == "due_today"


def test_due_on_a_future_day_is_upcoming():
    assert classify_due(datetime(2026, 6, 16, 9, 0, 0), NOW) == "upcoming"


# Boundaries.
def test_due_exactly_now_is_due_today():
    assert classify_due(NOW, NOW) == "due_today"


def test_due_at_the_last_second_of_today_is_due_today():
    assert classify_due(datetime(2026, 6, 15, 23, 59, 59), NOW) == "due_today"


def test_due_at_midnight_tomorrow_is_upcoming():
    assert classify_due(datetime(2026, 6, 16, 0, 0, 0), NOW) == "upcoming"
