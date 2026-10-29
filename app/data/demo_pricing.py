"""Deterministic, plausibility-bounded DEMO tariffs, not live/historical quotes.

Reference bands are editorial calibration assumptions in RUB (2026-style price
levels), NOT scraped prices. Keep provenance synthetic in every downstream offer.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.data.loader import DestinationPriceBand, load_catalog_fixtures

_FIXTURES = load_catalog_fixtures()
BANDS: dict[str, DestinationPriceBand] = _FIXTURES.price_bands
HOTEL_LEVEL: dict[str, Decimal] = _FIXTURES.hotel_levels


def _jitter(identifier: str, day: date, *, variation: int = 9) -> Decimal:
    """Stable +/-variation% across date/offer, without changing during reruns."""
    key = f"{identifier}:{day.isoformat()}".encode("utf-8")
    offset = (
        int.from_bytes(hashlib.sha256(key).digest()[:4], "big") % (2 * variation + 1) - variation
    )
    return Decimal(100 + offset) / 100


def _rounded(value: Decimal) -> int:
    return max(1, int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


def hotel_nightly(city: str, tier: str, day: date) -> int:
    band = BANDS[city]
    season = Decimal(str(band.monthly_factors[day.month - 1]))
    return max(
        2200,
        _rounded(
            Decimal(band.hotel_nightly_3star)
            * HOTEL_LEVEL[tier]
            * season
            * _jitter(f"{city}:{tier}:hotel", day)
        ),
    )


def flight_oneway(city: str, fare: str, day: date) -> int:
    band = BANDS[city]
    season = Decimal(str(band.monthly_factors[day.month - 1]))
    factor = Decimal("1.28") if fare == "direct" else Decimal("0.94")
    return max(
        4500,
        _rounded(
            Decimal(band.flight_oneway_per_person)
            * factor
            * season
            * _jitter(f"{city}:{fare}:air", day)
        ),
    )


def transfer_oneway(city: str, day: date) -> int:
    band = BANDS[city]
    return max(
        1000,
        _rounded(
            Decimal(band.transfer_oneway_group) * _jitter(f"{city}:transfer", day, variation=5)
        ),
    )


def pet_flight_surcharge(city: str) -> int:
    return BANDS[city].pet_flight_surcharge
