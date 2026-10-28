"""Run explicitly to refresh Open-Meteo data; no runtime API dependence."""
import argparse
from app.data.catalog import CITIES
from app.services.openmeteo import refresh_city, write_cache

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--start-year', type=int, default=2022)
    parser.add_argument('--end-year', type=int, default=2025)
    args = parser.parse_args()
    records = []
    for city in CITIES:
        try:
            records.append(refresh_city(city.id, city.lat, city.lon, start_year=args.start_year, end_year=args.end_year))
            print(city.id, 'downloaded')
        except Exception as exc:
            print(city.id, type(exc).__name__, 'not added')
    if not records:
        raise SystemExit('No climate data retrieved; keeping existing cache untouched')
    write_cache(records)
