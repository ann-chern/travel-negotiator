"""Run explicitly to refresh Open-Meteo data; no runtime API dependence."""

import argparse

from app.data.catalog import CITIES, EXTENDED_CITIES
from app.services.openmeteo import refresh_city, write_cache

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2022)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument(
        "--include-extended",
        action="store_true",
        help="Also download all 12 extended destinations",
    )
    parser.add_argument("--out", default=None, help="Optional cache output path")
    args = parser.parse_args()
    records = []
    for city in CITIES + (EXTENDED_CITIES if args.include_extended else ()):
        try:
            records.append(
                refresh_city(
                    city.id,
                    city.lat,
                    city.lon,
                    start_year=args.start_year,
                    end_year=args.end_year,
                )
            )
            print(city.id, "downloaded")
        except Exception as exc:
            print(city.id, type(exc).__name__, "not added")
    if not records:
        raise SystemExit("No climate data retrieved; keeping existing cache untouched")
    if args.out:
        from pathlib import Path

        write_cache(records, Path(args.out))
    else:
        write_cache(records)
    missing = [
        r["city_id"]
        for r in records
        if not any(isinstance(m.get("sea_c"), (int, float)) for m in r["months"].values())
    ]
    if missing:
        print("WARNING: No validated marine temperatures for:", ", ".join(missing))
    print(f"Climate records: {len(records)}; marine data requires verification before ranking.")
