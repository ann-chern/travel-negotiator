"""Offline climate-normal evidence for *beach swimming* preferences.

These are long-period monthly averages, not weather forecasts or actual conditions.
Missing monthly values remain unknown; never extrapolate across cities/months.
The warm-water thresholds are a transparent product heuristic, not a medical claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class MonthlyClimate:
    sea_c: float
    daytime_high_c: float
    source_url: str
    source_note: str


@dataclass(frozen=True)
class BeachAssessment:
    status: str  # favorable | marginal | unfavorable | unknown
    confidence: str
    summary: str
    source_url: str | None
    sea_c: float | None
    air_c: float | None


# Antalya sea is a Mediterranean *coastal regional mean*, NOT a hotel beach reading.
_ANTALYA_HIGH = (15.0, 15.9, 18.4, 21.8, 26.4, 31.6, 34.9, 34.9, 31.7, 27.3, 21.6, 16.7)
_ANTALYA_SEA = (16.7, 16.0, 16.6, 18.1, 20.8, 23.9, 26.7, 28.1, 27.0, 24.6, 21.4, 18.5)
_ISTANBUL_HIGH = (9.6, 10.2, 12.4, 17.4, 22.3, 27.1, 29.7, 29.7, 25.9, 20.6, 16.1, 11.8)
_ISTANBUL_SEA = (9.3, 8.2, 8.7, 11.3, 15.4, 20.2, 23.4, 24.2, 22.3, 18.8, 15.2, 12.0)
_ANTALYA_SOURCE = 'https://nighthopper.de/en/reiseblog/antalya-tuerkische-riviera'
_ISTANBUL_SOURCE = 'https://sarebanhotel.com/en/travel-tips/istanbul-weather-by-month/'
_SOCHI_SOURCE = 'https://www.whereandwhen.net/when/europe/russia/sochi/month/december/'

_NORMALS: dict[tuple[str, int], MonthlyClimate] = {}
for month in range(1, 13):
    _NORMALS['antalya', month] = MonthlyClimate(_ANTALYA_SEA[month-1], _ANTALYA_HIGH[month-1],
        _ANTALYA_SOURCE, 'Turkish Mediterranean regional sea-water mean; long-period climate normals')
    _NORMALS['istanbul', month] = MonthlyClimate(_ISTANBUL_SEA[month-1], _ISTANBUL_HIGH[month-1],
        _ISTANBUL_SOURCE, 'Marmara Sea and Istanbul historical monthly averages')
_NORMALS['sochi', 12] = MonthlyClimate(13.3, 11.0, _SOCHI_SOURCE,
    'Historical climate summary; 2009–2025 reference period')


class SeasonalityProvider:
    """Deterministic read-only climate-normal lookup. No network at request time."""

    def assess_beach(self, destination_id: str, when: date) -> BeachAssessment:
        climate = _NORMALS.get((destination_id, when.month))
        if climate is None:
            return BeachAssessment('unknown', 'none',
                'Нет проверенных климатических норм для этого направления и месяца; купание не оценено.',
                None, None, None)
        # Both water and air matter; tag 'beach' cannot override cold weather.
        if climate.sea_c >= 23 and climate.daytime_high_c >= 24:
            status, phrase = 'favorable', 'Климатические средние обычно благоприятны для пляжного отдыха'
        elif climate.sea_c >= 20 and climate.daytime_high_c >= 21:
            status, phrase = 'marginal', 'Купание возможно не для всех; сезон пограничный'
        else:
            status, phrase = 'unfavorable', 'Для купания обычно слишком прохладно'
        summary = (f'{phrase}: средняя вода ≈{climate.sea_c:g} °C, '
                   f'средний дневной максимум ≈{climate.daytime_high_c:g} °C. '
                   'Климатическая норма, не прогноз.')
        return BeachAssessment(status, 'monthly_climate', summary,
                               climate.source_url, climate.sea_c, climate.daytime_high_c)


class OpenMeteoSeasonalityProvider(SeasonalityProvider):
    """Use verified pre-fetched Open-Meteo normals, then report unknown if unavailable.

    Set allow_legacy_fallback=True to retain prior hand-curated three-city demo
    normals while preparing a refreshed cache. Default refuses mixed-source data.
    """
    def __init__(self, cache_path=None, *, allow_legacy_fallback: bool = False):
        import json
        from app.services.openmeteo import DEFAULT_CACHE
        self.cache_path = cache_path or DEFAULT_CACHE
        self.allow_legacy_fallback = allow_legacy_fallback
        try:
            self.records = json.loads(self.cache_path.read_text(encoding='utf-8')).get('cities', {})
        except (FileNotFoundError, ValueError, OSError):
            self.records = {}

    def assess_beach(self, destination_id: str, when: date) -> BeachAssessment:
        row = self.records.get(destination_id, {}).get('months', {}).get(str(when.month), {})
        sea, air = row.get('sea_c'), row.get('air_c')
        if isinstance(sea, (int, float)) and isinstance(air, (int, float)):
            if sea >= 23 and air >= 24:
                status, phrase = 'favorable', 'Обычно благоприятно для купания'
            elif sea >= 20 and air >= 21:
                status, phrase = 'marginal', 'Пограничные условия для купания'
            else:
                status, phrase = 'unfavorable', 'Обычно прохладно для купания'
            src = self.records[destination_id]
            return BeachAssessment(status, 'openmeteo_historical',
                f'{phrase}; средние за {src.get("years")}: вода ≈{sea:g} °C, '
                f'дневная температура ≈{air:g} °C. Это история, не прогноз.',
                src.get('air_url'), float(sea), float(air))
        if self.allow_legacy_fallback:
            return super().assess_beach(destination_id, when)
        return BeachAssessment('unknown', 'none',
            'Нет проверенных исторических средних температур воздуха и воды Open-Meteo; сезонность не оценена.',
            None, None, None)
