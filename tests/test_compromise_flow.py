from datetime import date
from decimal import Decimal
from unittest.mock import Mock

from app.agent.graph_agent import GraphTravelAgent, TravelGraphState
from app.agent.mcp_search import McpSearchResult
from app.schemas.request import TripRequest, Travelers, Budget


def original():
    return TripRequest(origin='Москва', departure_date=date(2026, 11, 10), nights=10,
                       travelers=Travelers(adults=2), budget=Budget(amount=Decimal('48000'),
                       currency='RUB', scope='whole_trip'))


def prepared():
    r = original()
    changed = r.model_copy(update={'nights': 7})
    proposal = {'offer_id': 'valid-7-nights', 'price_rub': '47000.00',
                'proposed_request': changed.model_dump(mode='json'),
                'requires_confirmation': True, 'kind': 'nights'}
    s = TravelGraphState(request=r, stage='negotiating', version=4,
                         compromise_request_version=4, compromises=[proposal],
                         flags={'allow_nights': True, 'allow_date_shift': False, 'allow_budget_increase': False})
    agent = object.__new__(GraphTravelAgent)
    agent.search = Mock(return_value=McpSearchResult(status='ready', options=[{'id':'valid-7-nights',
                                    'price_rub':'47000.00'}]))
    agent.parser = Mock()
    return agent, s


def test_accepted_proposal_is_rechecked_and_replaces_request():
    agent, s = prepared()
    s.user_message = 'принимаю компромисс 1'
    update = agent._understand_checkpoint_safe(s)
    assert update['request']['nights'] == 7
    assert update['version'] == 5
    assert update['compromises'] == []
    assert update['selected_offer_id'] is None
    agent.search.assert_called_once()
    agent.parser.parse.assert_not_called()


def test_changed_price_blocks_acceptance():
    agent, s = prepared()
    agent.search.return_value = McpSearchResult(status='ready', options=[{'id':'valid-7-nights',
                                                                       'price_rub':'48000.00'}])
    s.user_message = 'принимаю компромисс 1'
    update = agent._understand(s)
    assert update['stage'] == 'compromise_stale'
    assert 'request' not in update


def test_stale_version_cannot_be_accepted():
    agent, s = prepared()
    s.compromise_request_version = 3
    s.user_message = 'принимаю компромисс 1'
    update = agent._understand(s)
    assert update['stage'] == 'error'
    assert 'request' not in update
    agent.search.assert_not_called()


def test_decline_preserves_original_request():
    agent, s = prepared()
    s.user_message = 'отклоняю компромиссы'
    update = agent._understand(s)
    assert update['stage'] == 'compromise_declined'
    assert update['compromises'] == []
    assert 'request' not in update
    agent.search.assert_not_called()


def test_simple_yes_is_not_consent():
    agent, s = prepared()
    agent.parser.parse.return_value = __import__('app.agent.llm_provider', fromlist=['ParsedTurn']).ParsedTurn(intent='revise')
    s.user_message = 'да'
    update = agent._understand(s)
    assert update['request'].nights == 10
    assert update['compromises'] == []
    agent.search.assert_not_called()


def test_missing_proposal_number_fails_closed():
    agent, s = prepared()
    s.user_message = 'принимаю компромисс 3'
    update = agent._understand(s)
    assert update['stage'] == 'error'
    assert 'request' not in update


def test_negotiation_response_has_explicit_choice_instructions():
    agent, s = prepared()
    agent.writer = Mock()
    agent.writer.generate.return_value = 'Можно сократить поездку.'
    update = agent._negotiate(s)
    assert 'принимаю компромисс 1' in update['response']
    assert 'отклоняю компромиссы' in update['response']


def test_proposal_cannot_change_destination_as_hidden_side_effect():
    agent, s = prepared()
    proposal = s.compromises[0]
    proposal['proposed_request']['destination'] = 'istanbul'
    s.user_message = 'принимаю компромисс 1'
    update = agent._understand(s)
    assert update['stage'] == 'error'
    agent.search.assert_not_called()


def test_consent_flag_required_even_with_proposal():
    agent, s = prepared()
    s.flags['allow_nights'] = False
    s.user_message = 'принимаю компромисс 1'
    update = agent._understand(s)
    assert update['stage'] == 'error'
    agent.search.assert_not_called()


def test_clarification_detects_missing_origin_without_inventing_it():
    agent, s = prepared()
    s.request = TripRequest(travelers=Travelers(adults=2), nights=5)
    s.stage = 'parsed'
    update = agent._route(s)
    assert update['stage'] == 'needs_input'
    assert 'город отправления' in update['missing']
