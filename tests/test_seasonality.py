from datetime import date
from app.services.seasonality import SeasonalityProvider
from app.mcp_catalog.service import CatalogService
from app.schemas.request import TripRequest


def test_month_and_year_are_independent_for_climate_normals():
    p = SeasonalityProvider()
    assert p.assess_beach('antalya', date(2026, 12, 1)).status == 'unfavorable'
    assert p.assess_beach('antalya', date(2027, 9, 1)).status == 'favorable'
    assert p.assess_beach('antalya', date(2027, 12, 1)).status == 'unfavorable'


def test_missing_values_are_not_made_up():
    x = SeasonalityProvider().assess_beach('sochi', date(2027, 9, 1))
    assert x.status == 'unknown'
    assert x.sea_c is None and x.source_url is None


def test_cool_month_does_not_get_beach_tag_bonus():
    from app.mcp_catalog.service import _assess_offer
    from app.mcp_catalog.contracts import OfferSummary
    from app.schemas.request import Preference
    req = TripRequest.model_validate({'preferences':[{'field':'destination.tags','desired_value':'beach'}]})
    offer = OfferSummary(offer_id='1', destination_id='antalya', hotel_name='x',departure_date='2026-12-01',nights=7,price_rub='50000',availability='available', observed_at='2026-10-01T00:00:00Z')
    cold = _assess_offer(req, offer)
    warm = _assess_offer(req, offer.model_copy(update={'departure_date':'2027-09-01'}))
    assert cold.match_score < warm.match_score
    assert any('Климатическая норма' in x for x in cold.match_caveats)
