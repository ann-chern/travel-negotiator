"""Report availability of monthly air/SST evidence for all demo destinations."""

import json

from app.data.catalog import CITIES, EXTENDED_CITIES
from app.services.openmeteo import DEFAULT_CACHE

if __name__ == "__main__":
    if not DEFAULT_CACHE.exists():
        raise SystemExit(
            f"Climate cache missing: {DEFAULT_CACHE}\nRun scripts.refresh_climate first."
        )
    records = json.loads(DEFAULT_CACHE.read_text(encoding="utf-8")).get("cities", {})
    for city in CITIES + EXTENDED_CITIES:
        row = records.get(city.id, {})
        months = row.get("months", {})
        full = [
            m
            for m in range(1, 13)
            if isinstance(months.get(str(m), {}).get("air_c"), (int, float))
            and isinstance(months.get(str(m), {}).get("sea_c"), (int, float))
        ]
        print(f"{city.id:12} {len(full):2}/12 complete months: {full}")
