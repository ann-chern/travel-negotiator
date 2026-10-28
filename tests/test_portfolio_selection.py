from datetime import date
from decimal import Decimal

from app.mcp_catalog.service import CatalogService
from app.schemas.request import Budget, DateWindow, Travelers, TripRequest
from app.web.presentation import option_view


def request(*, destination=None, window=False, budget=None):
    fields = dict(origin='Москва', destination=destination, nights=7,
                  travelers=Travelers(adults=2))
    if window:
        fields['date_window'] = DateWindow(earliest_departure=date(2027, 9, 1),
                                           latest_departure=date(2027, 9, 30))
    else:
        fields['departure_date'] = date(2027, 9, 10)
    if budget:
        fields['budget'] = Budget(amount=Decimal(budget), currency='RUB', scope='whole_trip')
    return TripRequest(**fields)


def test_open_destination_returns_distinct_cities():
    found = CatalogService().search_date_window(request(window=True).model_dump(mode='json'))
    assert found.status == 'ok'
    assert len({o.destination_id for o in found.offers}) == 3
    assert len({o.offer_id for o in found.offers}) == 3
    assert all('Вариант другого направления' in o.match_reasons for o in found.offers)


def test_fixed_destination_offers_distinct_tradeoffs():
    found = CatalogService().search_offers(request(destination='Стамбул').model_dump(mode='json'))
    assert found.status == 'ok'
    assert all(o.destination_id == 'istanbul' for o in found.offers)
    assert len({(o.hotel_name, o.outbound_stops) for o in found.offers}) >= 2
    assert any(o.match_reasons[0] == 'Самый доступный по цене' for o in found.offers)
    assert [o.match_score for o in found.offers] == sorted((o.match_score for o in found.offers), reverse=True)
    assert any(o.match_reasons[0] == 'Повышенный комфорт' for o in found.offers)


def test_budget_never_relaxed_for_portfolio():
    found = CatalogService().search_date_window(request(window=True, budget='150000').model_dump(mode='json'))
    if found.status == 'ok':
        assert all(Decimal(o.price_rub) <= Decimal('150000') for o in found.offers)


def test_price_breakdown_equals_total_and_survives_ui():
    found = CatalogService().search_offers(request(destination='Стамбул').model_dump(mode='json'))
    offer = found.offers[0]
    assert len(offer.price_breakdown_rub) == 5
    assert sum(map(Decimal, offer.price_breakdown_rub.values())) == Decimal(offer.price_rub)
    card = option_view({'price_rub': offer.price_rub, 'price_breakdown_rub': offer.price_breakdown_rub})
    assert 'Отель:' in card['price_breakdown']
    assert 'Трансфер' in card['price_breakdown']


def test_recommendations_are_deterministic():
    r = request(window=True).model_dump(mode='json')
    service = CatalogService()
    first = service.search_date_window(r)
    second = service.search_date_window(r)
    assert [x.offer_id for x in first.offers] == [x.offer_id for x in second.offers]
