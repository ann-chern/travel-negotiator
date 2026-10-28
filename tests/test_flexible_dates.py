from datetime import date
from app.agent.clarification import clarification_plan
from app.mcp_catalog.service import CatalogService
from app.schemas.request import TripRequest, DateWindow, Travelers


def req(first=16,last=30):
    return TripRequest(origin="Москва", nights=5, travelers=Travelers(adults=2),
        date_window=DateWindow(earliest_departure=date(2026,11,first), latest_departure=date(2026,11,last)))


def test_window_does_not_require_exact_day():
    assert not clarification_plan(req())


def test_search_window_picks_only_days_in_range():
    result=CatalogService().search_date_window(req().model_dump(mode="json"),limit=3)
    assert result.status in ("ok", "no_match", "unsupported")
    if result.status=="ok":
        assert len(result.offers)<=3
        assert all("2026-11-16"<=o.departure_date<="2026-11-30" for o in result.offers)
        assert all(not o.is_bookable for o in result.offers)
        assert [float(o.price_rub) for o in result.offers]==sorted(float(o.price_rub) for o in result.offers)


def test_window_is_bounded_no_silent_sampling():
    request=req().model_copy(update={"date_window": DateWindow(earliest_departure=date(2026,11,1),latest_departure=date(2026,12,10))})
    assert CatalogService().search_date_window(request.model_dump(mode="json")).status=="unsupported"


def test_exact_search_unchanged():
    r=req().model_copy(update={"date_window": None,"departure_date": date(2026,11,10)})
    assert CatalogService().search_offers(r.model_dump(mode="json"),limit=3).status=="ok"


def test_selected_offer_pins_date_before_cart():
    from app.agent.graph_agent import GraphTravelAgent, TravelGraphState
    agent = object.__new__(GraphTravelAgent)
    state = TravelGraphState(user_message="беру первый вариант", request=req(), version=4,
        stage="ready", options=[{"id": "trip-sochi-budget-connecting-2026-11-20-5-2-0",
                                   "departure_date": "2026-11-20", "price_rub": "40200.00"}])
    result = agent._understand(state)
    assert result['stage'] == 'cart_review'
    assert result['version'] == 5 and result['selected_request_version'] == 5
    assert TripRequest.model_validate(result['request']).departure_date == date(2026,11,20)
    assert TripRequest.model_validate(result['request']).date_window is None


def test_fabricated_date_outside_window_cannot_be_selected():
    from app.agent.graph_agent import GraphTravelAgent, TravelGraphState
    agent = object.__new__(GraphTravelAgent)
    state = TravelGraphState(user_message="беру первый вариант", request=req(), version=4,
        stage="ready", options=[{"id": "fake", "departure_date": "2026-12-05", "price_rub": "1.00"}])
    result = agent._understand(state)
    assert result['stage'] == 'error'
    assert 'request' not in result


def test_date_variants_explicitly_listed_to_user():
    from app.agent.graph_agent import GraphTravelAgent, TravelGraphState
    class Writer:
        def generate(self, **kwargs):
            return 'Подходят варианты для двоих.'
    agent = object.__new__(GraphTravelAgent)
    agent.writer = Writer()
    state = TravelGraphState(request=req(), stage='ready', options=[
        {'id': 'one', 'departure_date': '2026-11-16'},
        {'id': 'two', 'departure_date': '2026-11-19'}])
    text = agent._recommend(state)['response']
    assert '2026-11-16' in text and '2026-11-19' in text
