"""Behavioral checks for calibrated mock pricing (not market price assertions)."""
from datetime import date
from decimal import Decimal
import pytest
from app.data.demo_pricing import BANDS, hotel_nightly, flight_oneway, transfer_oneway
from app.data.catalog import active_cities, generate_candidates
from app.schemas.request import TripRequest, Travelers
from app.services.validator import validate_trip

@pytest.mark.parametrize('city', sorted(BANDS))
def test_calibrated_tariffs_positive_and_tiered(city):
    day = date(2027, 9, 12)
    rates = [hotel_nightly(city, tier, day) for tier in ('budget', 'family', 'premium')]
    assert rates[0] >= 2200 and rates[0] < rates[1] < rates[2]
    assert flight_oneway(city, 'direct', day) > flight_oneway(city, 'connecting', day) >= 4500
    assert transfer_oneway(city, day) >= 1000

@pytest.mark.parametrize('city', ['antalya', 'maldives', 'dubai', 'sochi'])
def test_not_absurd_5_star_price(city):
    assert hotel_nightly(city, 'premium', date(2027, 8, 15)) >= 9000

def test_prices_deterministic_but_vary_by_date():
    a = hotel_nightly('antalya', 'premium', date(2027, 9, 1))
    assert a == hotel_nightly('antalya', 'premium', date(2027, 9, 1))
    assert len({hotel_nightly('antalya', 'premium', date(2027, 9, x)) for x in range(1, 8)}) > 1

def test_end_to_end_catalog_keeps_synthetic_provenance(monkeypatch):
    monkeypatch.setenv('TN_EXTENDED_DEMO', '1')
    r = TripRequest(origin='Москва', departure_date=date(2027, 9, 12), nights=7, travelers=Travelers(adults=2))
    trips = generate_candidates(r, max_results=100)
    assert {t.destination_id for t in trips} == {city.id for city in active_cities()}
    assert all(t.hotel_offer.provenance.source_type == 'synthetic' for t in trips)
    assert any(validate_trip(t, r).status == 'pass' for t in trips)
    assert all(validate_trip(t, r).status in {'pass', 'fail', 'unknown'} for t in trips)
    totals = [validate_trip(t, r).total_price.amount for t in trips]
    assert min(totals) > Decimal('25000')
