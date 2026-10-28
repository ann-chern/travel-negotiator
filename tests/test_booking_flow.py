from datetime import date
from types import SimpleNamespace

from app.agent.graph_agent import GraphTravelAgent, TravelGraphState
from app.agent.llm_provider import ParsedTurn
from app.schemas.request import TripRequest, Travelers

OFFER = 'trip-sochi-budget-connecting-2026-11-10-5-2-0'


class Parser:
    def parse(self, message, current_state):
        if 'шесть' in message:
            return ParsedTurn(nights=6, intent='revise')
        raise AssertionError(f'Parser should not process selection/confirmation: {message}')


class Booking:
    def __init__(self):
        self.calls = []
        self.price = '40200.00'
        self.fail = False

    def details(self, request, offer_id):
        self.calls.append('details')
        return SimpleNamespace(status='found', offer=SimpleNamespace(offer_id=offer_id, hotel_name='Сочи — Эконом'))

    def validate(self, request, offer_id):
        self.calls.append('validate')
        return SimpleNamespace(status='pass', offer_id=offer_id, total_rub=self.price)

    def simulate(self, request, offer_id):
        self.calls.append('simulate')
        return SimpleNamespace(status='simulated', real_booking_created=False,
                               payment_collected=False, simulation_reference='DEMO-TEST')


def agent_and_state():
    agent = object.__new__(GraphTravelAgent)
    agent.parser = Parser()
    agent.booking = Booking()
    req = TripRequest(origin='Москва', departure_date=date(2026,11,10), nights=5,
                      travelers=Travelers(adults=2))
    state = TravelGraphState(user_message='беру первый вариант', stage='ready',
                             request=req, version=3,
                             options=[{'id': OFFER, 'price_rub': '40200.00'}])
    return agent, state


def updated(state, update):
    return state.model_copy(update=update)


def test_selection_requires_separate_explicit_confirmation():
    agent, state = agent_and_state()
    state = updated(state, agent._understand(state))
    assert state.stage == 'cart_review'
    assert agent._next(state) == 'cart'
    assert 'simulate' not in agent.booking.calls
    state = updated(state, agent._cart_review(state))
    assert state.stage == 'awaiting_confirmation'
    assert 'simulate' not in agent.booking.calls
    state = updated(state, agent._understand(state.model_copy(update={
        'user_message': 'подтверждаю демо-бронирование'})))
    assert agent._next(state) == 'booking'
    state = updated(state, agent._booking_confirmation(state))
    assert state.stage == 'booked_demo'
    assert state.booking_reference == 'DEMO-TEST'
    assert agent.booking.calls == ['details', 'validate', 'validate', 'simulate']


def test_price_change_blocks_simulation():
    agent, state = agent_and_state()
    state = updated(state, agent._understand(state))
    state = updated(state, agent._cart_review(state))
    agent.booking.price = '50000.00'
    state = updated(state, agent._understand(state.model_copy(update={
        'user_message': 'подтверждаю демо-бронирование'})))
    state = updated(state, agent._booking_confirmation(state))
    assert state.stage == 'cart_rejected'
    assert 'simulate' not in agent.booking.calls


def test_cancel_never_simulates():
    agent, state = agent_and_state()
    state = updated(state, agent._understand(state))
    state = updated(state, agent._cart_review(state))
    state = updated(state, agent._understand(state.model_copy(update={'user_message': 'отмена'})))
    assert state.stage == 'cancelled' and state.selected_offer_id is None
    assert 'simulate' not in agent.booking.calls


def test_new_request_clears_selection():
    agent, state = agent_and_state()
    state = updated(state, agent._understand(state))
    state = updated(state, agent._cart_review(state))
    state = updated(state, agent._understand(state.model_copy(update={'user_message': 'давай шесть ночей'})))
    assert state.stage == 'parsed' and state.request.nights == 6
    assert state.selected_offer_id is None and state.version == 4


def test_unconfirmed_short_yes_not_authorized():
    agent, state = agent_and_state()
    assert agent._selected_option(state.options, 'да') is None
    state = updated(state, agent._understand(state.model_copy(update={
        'user_message': 'подтверждаю демо-бронирование'})))
    assert state.stage == 'error'
    assert agent.booking.calls == []


def test_not_found_or_changed_price_during_cart_review():
    agent, state = agent_and_state()
    state = updated(state, agent._understand(state))
    agent.booking.price = '50000.00'
    state = updated(state, agent._cart_review(state))
    assert state.stage == 'cart_rejected'
    assert state.selected_offer_id is None
    assert 'simulate' not in agent.booking.calls
