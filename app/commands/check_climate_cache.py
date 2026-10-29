"""Проверка полноты локального климатического кэша."""

import json

from app.data.catalog import CITIES, EXTENDED_CITIES
from app.services.openmeteo import DEFAULT_CACHE


def main() -> None:
    if not DEFAULT_CACHE.exists():
        raise SystemExit(
            f"Climate cache missing: {DEFAULT_CACHE}\nRun app.commands.refresh_climate_cache first."
        )
    records = json.loads(DEFAULT_CACHE.read_text(encoding="utf-8")).get("cities", {})
    for city in CITIES + EXTENDED_CITIES:
        row = records.get(city.id, {})
        months = row.get("months", {})
        complete_months = [
            month
            for month in range(1, 13)
            if isinstance(months.get(str(month), {}).get("air_c"), (int, float))
            and isinstance(months.get(str(month), {}).get("sea_c"), (int, float))
        ]
        print(f"{city.id:12} {len(complete_months):2}/12 complete months: {complete_months}")


if __name__ == "__main__":
    main()
