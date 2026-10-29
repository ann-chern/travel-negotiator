"""Open-Meteo historical climate ingestion. Not a long-range forecast.

Refresh is explicit, never executed during a user request. Data is persisted so
Docker works offline; incomplete/failed responses are not silently imputed.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from urllib.parse import urlencode
from urllib.request import urlopen

AIR_ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
# Historical SST is retrieved from the reanalysis archive, not the marine forecast endpoint.
MARINE_ENDPOINT = AIR_ENDPOINT
DEFAULT_CACHE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "climate_normals.json"


def _get_json(endpoint: str, params: dict, *, opener=urlopen) -> dict:
    url = endpoint + "?" + urlencode(params)
    with opener(url, timeout=20) as response:
        data = json.load(response)
    if not isinstance(data, dict) or data.get("error"):
        raise ValueError(f"Invalid Open-Meteo response: {data!r}")
    return data


def _monthly(dates: list[str], values: list, *, hourly: bool = False) -> dict[int, float]:
    grouped: dict[int, list[float]] = defaultdict(list)
    for day, value in zip(dates, values):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            grouped[int(day[5:7])].append(float(value))
    return {
        month: round(mean(items), 1)
        for month, items in grouped.items()
        if len(items) >= (24 if hourly else 2)
    }


def refresh_city(
    city_id: str,
    lat: float,
    lon: float,
    *,
    start_year: int = 2022,
    end_year: int = 2025,
    opener=urlopen,
    sea_coords: tuple[float, float] | None = None,
) -> dict:
    """Download paired air and marine history; sea is optional but never invented.

    Marine coordinates are near the city, not necessarily over sea. Missing sea
    grid-cell data is recorded as unavailable. User should supply a coastal sea
    point if accurate local water data is needed.
    """
    base = dict(
        latitude=lat,
        longitude=lon,
        start_date=f"{start_year}-01-01",
        end_date=f"{end_year}-12-31",
        timezone="auto",
    )
    air = _get_json(
        AIR_ENDPOINT,
        {**base, "daily": "temperature_2m_max", "models": "era5"},
        opener=opener,
    )["daily"]
    highs = _monthly(air["time"], air["temperature_2m_max"])
    seas: dict[int, float] = {}
    marine_error = None
    try:
        marine_base = {
            **base,
            "hourly": "sea_surface_temperature",
            "models": "era5",
            "cell_selection": "sea",
        }
        if sea_coords is not None:
            marine_base.update(latitude=sea_coords[0], longitude=sea_coords[1])
        sea = _get_json(MARINE_ENDPOINT, marine_base, opener=opener)["hourly"]
        seas = _monthly(sea["time"], sea["sea_surface_temperature"], hourly=True)
    except (OSError, ValueError, KeyError, TimeoutError) as exc:
        marine_error = type(exc).__name__
    return dict(
        city_id=city_id,
        latitude=lat,
        longitude=lon,
        years=[start_year, end_year],
        source="Open-Meteo / ERA5 + marine",
        air_url=AIR_ENDPOINT,
        sea_url=MARINE_ENDPOINT,
        sea_data_issue=marine_error,
        sea_coordinates=list(sea_coords) if sea_coords is not None else None,
        months={
            str(month): {"air_c": highs.get(month), "sea_c": seas.get(month)}
            for month in range(1, 13)
        },
    )


def write_cache(records: list[dict], path: Path = DEFAULT_CACHE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "dataset": "historical monthly averages, not forecast",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "cities": {r["city_id"]: r for r in records},
    }
    target = path.with_suffix(".tmp")
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    target.replace(path)
