"""Явное обновление климатического кэша Open-Meteo."""

import argparse
from pathlib import Path

from app.data.catalog import active_cities
from app.services.openmeteo import refresh_city, write_cache


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2022)
    parser.add_argument("--end-year", type=int, default=2025)
    parser.add_argument("--out", default=None, help="Optional cache output path")
    args = parser.parse_args()

    records = []
    for city in active_cities():
        try:
            records.append(
                refresh_city(
                    city.id,
                    city.latitude,
                    city.longitude,
                    start_year=args.start_year,
                    end_year=args.end_year,
                )
            )
            print(city.id, "downloaded")
        except Exception as exc:
            print(city.id, type(exc).__name__, "not added")
    if not records:
        raise SystemExit("No climate data retrieved; keeping existing cache untouched")

    write_cache(records, Path(args.out)) if args.out else write_cache(records)
    missing = [
        record["city_id"]
        for record in records
        if not any(
            isinstance(month.get("sea_c"), (int, float)) for month in record["months"].values()
        )
    ]
    if missing:
        print("WARNING: No validated marine temperatures for:", ", ".join(missing))
    print(f"Climate records: {len(records)}; marine data requires verification before ranking.")


if __name__ == "__main__":
    main()
