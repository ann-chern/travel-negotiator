"""Deterministic, plausibility-bounded DEMO tariffs, not live/historical quotes.

Reference bands are editorial calibration assumptions in RUB (2026-style price
levels), NOT scraped prices. Keep provenance synthetic in every downstream offer.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib

@dataclass(frozen=True)
class DestinationPriceBand:
    hotel_nightly_3star: int  # two people, base room
    flight_oneway_per_person: int
    transfer_oneway_group: int
    # Multipliers for each calendar month, Jan-Dec; broad demonstration seasonality.
    monthly_factors: tuple[float, ...]

_DEFAULT_MONTHS = (1.0, .98, 1.05, 1.12, 1.20, 1.26, 1.35, 1.32, 1.15, 1.05, 1.0, 1.23)
_TROPICAL_MONTHS = (1.27, 1.20, 1.14, 1.04, .94, .86, .84, .88, .94, 1.06, 1.20, 1.32)
_MEDITERRANEAN_MONTHS = (.78, .76, .87, 1.03, 1.19, 1.35, 1.55, 1.52, 1.28, 1.04, .82, .88)

# Explicit per-destination floor-like reference values, not fabricated scraped snapshots.
BANDS: dict[str, DestinationPriceBand] = {
    'antalya': DestinationPriceBand(5200, 19000, 2500, _MEDITERRANEAN_MONTHS),
    'istanbul': DestinationPriceBand(5600, 17000, 2900, _DEFAULT_MONTHS),
    'sochi': DestinationPriceBand(4500, 11500, 1900, _MEDITERRANEAN_MONTHS),
    'dubai': DestinationPriceBand(10500, 29000, 5000, _TROPICAL_MONTHS),
    'phuket': DestinationPriceBand(6000, 52000, 5300, _TROPICAL_MONTHS),
    'bali': DestinationPriceBand(6500, 63000, 5400, _TROPICAL_MONTHS),
    'maldives': DestinationPriceBand(18000, 62000, 11000, _TROPICAL_MONTHS),
    'sharm': DestinationPriceBand(5400, 26000, 3800, _TROPICAL_MONTHS),
    'hurghada': DestinationPriceBand(5200, 24000, 3500, _TROPICAL_MONTHS),
    'tbilisi': DestinationPriceBand(4900, 17000, 2600, _DEFAULT_MONTHS),
    'yerevan': DestinationPriceBand(4500, 16000, 2100, _DEFAULT_MONTHS),
    'bangkok': DestinationPriceBand(5100, 47000, 4400, _TROPICAL_MONTHS),
    'goa': DestinationPriceBand(4700, 41000, 3300, _TROPICAL_MONTHS),
    'colombo': DestinationPriceBand(5000, 42000, 3600, _TROPICAL_MONTHS),
    'kazan': DestinationPriceBand(4100, 9500, 1900, _DEFAULT_MONTHS),
}

HOTEL_LEVEL = {'budget': Decimal('1'), 'family': Decimal('1.65'), 'premium': Decimal('3.3')}

def _jitter(identifier: str, day: date, *, variation: int = 9) -> Decimal:
    """Stable +/-variation% across date/offer, without changing during reruns."""
    key = f'{identifier}:{day.isoformat()}'.encode('utf-8')
    offset = int.from_bytes(hashlib.sha256(key).digest()[:4], 'big') % (2 * variation + 1) - variation
    return Decimal(100 + offset) / 100

def _rounded(value: Decimal) -> int:
    return max(1, int(value.quantize(Decimal('1'), rounding=ROUND_HALF_UP)))

def hotel_nightly(city: str, tier: str, day: date) -> int:
    band = BANDS[city]
    season = Decimal(str(band.monthly_factors[day.month - 1]))
    return max(2200, _rounded(Decimal(band.hotel_nightly_3star) * HOTEL_LEVEL[tier] * season * _jitter(f'{city}:{tier}:hotel', day)))

def flight_oneway(city: str, fare: str, day: date) -> int:
    band = BANDS[city]
    season = Decimal(str(band.monthly_factors[day.month - 1]))
    factor = Decimal('1.28') if fare == 'direct' else Decimal('0.94')
    return max(4500, _rounded(Decimal(band.flight_oneway_per_person) * factor * season * _jitter(f'{city}:{fare}:air', day)))

def transfer_oneway(city: str, day: date) -> int:
    band = BANDS[city]
    return max(1000, _rounded(Decimal(band.transfer_oneway_group) * _jitter(f'{city}:transfer', day, variation=5)))
