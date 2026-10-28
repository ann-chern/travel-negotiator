"""Property-oriented calendar regressions; no city-specific test fixtures."""
from datetime import date
import calendar
import pytest
from app.agent.date_intent import recognize_window

@pytest.mark.parametrize('month, phrase', [
    (1, 'в январе'), (2, 'в феврале'), (3, 'в марте'),
    (4, 'в апреле'), (5, 'в мае'), (6, 'в июне'),
    (7, 'в июле'), (8, 'в августе'), (9, 'в сентябре'),
    (10, 'в октябре'), (11, 'в ноябре'), (12, 'в декабре'),
])
def test_month_without_year_is_next_occurrence(month, phrase):
    today = date(2026, 10, 28)
    result = recognize_window(phrase, today=today)
    assert result is not None
    assert result.earliest_departure.year == (2027 if month < 10 else 2026)
    assert result.earliest_departure.month == month
    assert result.latest_departure.day == calendar.monthrange(result.latest_departure.year, month)[1]
    assert result.earliest_departure >= today


def test_explicit_year_is_not_silently_changed():
    result = recognize_window('с 1 по 30 сентября 2026', today=date(2026, 10, 28))
    assert result.earliest_departure == date(2026, 9, 1)


def test_flexible_range_not_forced_to_specific_day():
    result = recognize_window('с 1 по 30 декабря', today=date(2026, 10, 28))
    assert result.earliest_departure == date(2026, 12, 1)
    assert result.latest_departure == date(2026, 12, 30)


def test_current_month_only_future_dates():
    result = recognize_window('любой день в октябре', today=date(2026, 10, 28))
    assert result.earliest_departure == date(2026, 10, 28)
    assert result.latest_departure == date(2026, 10, 31)
