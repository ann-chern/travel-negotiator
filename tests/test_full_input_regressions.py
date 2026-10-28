from datetime import date
from app.agent.graph_agent import GraphTravelAgent, TravelGraphState
from app.agent.intent_rules import reinforce_explicit_facts, destination_matches
from app.agent.llm_provider import ParsedTurn
from app.schemas.request import TripRequest
from app.agent.mcp_search import McpSearchResult

class ForgetfulParser:
    def parse(self, message, current_state):
        return ParsedTurn(intent='plan')

def test_full_message_without_any_unnecessary_clarifications():
    agent = object.__new__(GraphTravelAgent)
    agent.parser = ForgetfulParser()
    message = 'Хочу в Стамбул с 10 по 15 ноября, нас двое. Бюджет 100 тысяч. вылет из москвы'
    update = agent._understand(TravelGraphState(user_message=message))
    req = TripRequest.model_validate(update['request'])
    assert req.destination == 'Стамбул'
    assert req.origin == 'Москва'
    assert req.nights == 5
    assert req.departure_date == date(2026, 11, 10)
    assert req.return_date == date(2026, 11, 15)
    assert req.travelers.adults == 2
    assert req.budget.amount == 100000
    assert agent._route(TravelGraphState(request=req))['stage'] == 'searching'

def test_explicit_destination_is_a_hard_search_filter():
    agent = object.__new__(GraphTravelAgent)
    agent.search = lambda request, **kwargs: McpSearchResult(status='ready', options=[
        {'id':'sochi','destination':'sochi'}, {'id':'istanbul','destination':'istanbul'}])
    req = TripRequest(destination='Стамбул')
    output = agent._search(TravelGraphState(request=req))
    assert [option['id'] for option in output['options']] == ['istanbul']

def test_no_cross_destination_fallback():
    agent = object.__new__(GraphTravelAgent)
    agent.search = lambda request, **kwargs: McpSearchResult(status='ready',options=[{'id':'sochi','destination':'sochi'}])
    output = agent._search(TravelGraphState(request=TripRequest(destination='Стамбул')))
    assert output['stage'] == 'no_match'
    assert not output['options']
