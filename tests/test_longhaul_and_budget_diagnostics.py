"""Regression tests for long-haul search and explainable budget failures."""
from datetime import date
from decimal import Decimal

from app.data.catalog import generate_candidates
from app.mcp_catalog.service import CatalogService
from app.schemas.request import Budget, DateWindow, Preference, Travelers, TripRequest
from app.services.validator import validate_trip


def test_phuket_finds_valid_overnight_arrival(monkeypatch):
    monkeypatch.setenv('TN_EXTENDED_DEMO', '1')
    request = TripRequest(origin='Москва', destination='Пхукет', departure_date=date(2026, 12, 5),
                          nights=7, travelers=Travelers(adults=3))
    response = CatalogService().search_offers(request.model_dump(mode='json'), limit=3)
    assert response.status == 'ok'
    assert response.offers and all(o.destination_id == 'phuket' for o in response.offers)
    assert all(o.departure_date == '2026-12-05' for o in response.offers)
    assert any(validate_trip(t, request).status == 'pass' for t in generate_candidates(request))
    assert all(t.arrival_transfer.arrival_at.date() == t.hotel_offer.check_in for t in generate_candidates(request))


def test_budget_gap_is_computed_without_mutating_budget(monkeypatch):
    monkeypatch.setenv('TN_EXTENDED_DEMO', '1')
    request = TripRequest(origin='Москва', nights=7, travelers=Travelers(adults=2),
                          date_window=DateWindow(earliest_departure=date(2027, 9, 1),
                                                 latest_departure=date(2027, 9, 30)),
                          budget=Budget(amount=Decimal('30000'), currency='RUB', scope='whole_trip'),
                          preferences=[Preference(field='destination.tags', desired_value='beach')])
    before = request.model_dump(mode='json')
    response = CatalogService().search_date_window(before)
    assert response.status == 'no_match'
    assert 'Минимальная цена' in response.message
    assert 'не хватает' in response.message
    assert request.model_dump(mode='json') == before


def test_open_beach_request_does_not_rank_non_coastal_city_first(monkeypatch):
    monkeypatch.setenv('TN_EXTENDED_DEMO', '1')
    request = TripRequest(origin='Москва', nights=7, travelers=Travelers(adults=3),
                          date_window=DateWindow(earliest_departure=date(2026, 12, 1),
                                                 latest_departure=date(2026, 12, 31)),
                          preferences=[Preference(field='destination.tags', desired_value='beach')])
    response = CatalogService().search_date_window(request.model_dump(mode='json'))
    assert response.status == 'ok'
    assert response.offers[0].destination_id not in {'kazan', 'yerevan', 'tbilisi', 'istanbul'}
