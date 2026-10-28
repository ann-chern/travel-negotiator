from datetime import date
from unittest.mock import Mock

from app.agent.clarification import clarification_plan, select_clarification
from app.agent.graph_agent import GraphTravelAgent, TravelGraphState
from app.schemas.request import TripRequest, Travelers, UnresolvedRequirement


def test_missing_fields_prioritized():
    req = TripRequest()
    plan = clarification_plan(req)
    assert [x.code for x in plan] == ['origin', 'departure_date', 'nights', 'adults']
    assert '?' in plan[0].question


def test_open_destination_does_not_require_question():
    req = TripRequest(origin='Москва', departure_date=date(2026,11,10), nights=5,
                      travelers=Travelers(adults=2))
    assert select_clarification(req) is None


def test_unknown_child_age_asks_question_without_guessing():
    req = TripRequest(origin='Москва', departure_date=date(2026,11,10), nights=5,
                      travelers=Travelers(adults=2, children_ages_unknown=1))
    assert select_clarification(req).code == 'children_ages'
    assert req.travelers.children_ages == []


def test_ambiguous_preference_requires_detail():
    req = TripRequest(origin='Москва', departure_date=date(2026,11,10), nights=5,
        travelers=Travelers(adults=2), unresolved=[UnresolvedRequirement(text='недолгий перелёт',reason='ambiguous')])
    assert select_clarification(req).code == 'unresolved_0'


def test_unsupported_not_silently_treated_as_clarification():
    req = TripRequest(origin='Москва', departure_date=date(2026,11,10), nights=5,
        travelers=Travelers(adults=2), unresolved=[UnresolvedRequirement(text='вертолёт',reason='unsupported_by_catalog')])
    assert select_clarification(req) is None


def test_route_and_question_focus():
    agent = GraphTravelAgent.__new__(GraphTravelAgent)
    writer = Mock()
    writer.generate.return_value = 'Из какого города вылетаете?'
    agent.writer = writer
    s = TravelGraphState(request=TripRequest(nights=5, travelers=Travelers(adults=2)))
    routed = agent._route(s)
    assert routed['stage'] == 'needs_input'
    s.missing = routed['missing']
    reply = agent._clarify(s)
    assert reply['stage'] == 'needs_input'
    assert writer.generate.call_args.kwargs['payload']['focus']['code'] == 'origin'


def test_bad_llm_question_uses_safe_fallback():
    agent = GraphTravelAgent.__new__(GraphTravelAgent)
    agent.writer = Mock()
    agent.writer.generate.return_value = 'Сейчас предложу путешествие.'
    s = TravelGraphState(request=TripRequest(nights=5, travelers=Travelers(adults=2)))
    assert agent._clarify(s)['response'] == 'Из какого города планируете выезжать?'


def test_clarify_no_state_mutation():
    agent = GraphTravelAgent.__new__(GraphTravelAgent)
    agent.writer = Mock()
    agent.writer.generate.side_effect = TimeoutError()
    req = TripRequest(travelers=Travelers(adults=2))
    s = TravelGraphState(request=req)
    before = s.request.model_dump(mode='json')
    assert agent._clarify(s)['stage'] == 'needs_input'
    assert s.request.model_dump(mode='json') == before


def test_window_and_return_date_still_need_catalog_specific_values():
    from app.schemas.request import DateWindow
    req = TripRequest(origin='Москва', date_window=DateWindow(
        earliest_departure=date(2026,11,10), latest_departure=date(2026,11,20)),
        return_date=date(2026,11,25), travelers=Travelers(adults=2))
    codes = [x.code for x in clarification_plan(req)]
    assert codes == ['nights']
