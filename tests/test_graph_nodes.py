"""Runs without LangGraph installed: business-node contract tests.
The actual graph routing/compilation requires langgraph and is tested on developer machine.
"""

from datetime import date

from app.agent.graph_agent import GraphTravelAgent, TravelGraphState
from app.agent.llm_provider import ParsedTurn


class FakeParser:
    def __init__(self, *turns):
        self.turns = iter(turns)

    def parse(self, message, state):
        return next(self.turns)


class FakeWriter:
    def __init__(self):
        self.tasks = []

    def generate(self, *, task, payload):
        self.tasks.append(task)
        return {
            "clarification": "Из какого города вы хотите вылететь?",
            "recommendation": "Вот предложения из демонстрационного каталога.",
            "negotiation": "Нужна уступка, но сначала ваше согласие.",
        }[task]


def make_agent(parser):
    agent = object.__new__(GraphTravelAgent)
    agent.parser = parser
    agent.writer = FakeWriter()
    from app.services.negotiation import search_with_plan_changes

    agent.search = search_with_plan_changes
    return agent


def test_state_is_pydantic_and_has_explicit_defaults():
    state = TravelGraphState()
    assert state.version == 0 and state.request.origin is None
    assert state.options == []


def test_parse_and_clarify_are_independent_nodes():
    agent = make_agent(FakeParser(ParsedTurn(nights=5, adults=2)))
    state = TravelGraphState(user_message="Нас двое, на пять ночей")
    state = state.model_copy(update=agent._understand(state))
    state = state.model_copy(update=agent._route(state))
    assert agent._next(state) == "clarify"
    assert "город отправления" in state.missing
    state = state.model_copy(update=agent._clarify(state))
    assert state.pending_question and agent.writer.tasks == ["clarification"]


def test_search_recommendation_uses_validated_catalog():
    agent = make_agent(
        FakeParser(
            ParsedTurn(origin="Москва", adults=2, nights=5, departure_date=date(2026, 11, 10))
        )
    )
    state = TravelGraphState(user_message="Москва, двое, 10 ноября на 5 ночей")
    state = state.model_copy(update=agent._understand(state))
    state = state.model_copy(update=agent._route(state))
    assert agent._next(state) == "search"
    state = state.model_copy(update=agent._search(state))
    assert state.stage == "ready" and state.options
    assert all(item["synthetic"] for item in state.options)
    state = state.model_copy(update=agent._recommend(state))
    assert agent.writer.tasks == ["recommendation"]
    assert state.response


def test_patch_preserves_previous_values():
    agent = make_agent(FakeParser(ParsedTurn(nights=3, intent="revise")))
    from app.schemas.request import Travelers, TripRequest

    state = TravelGraphState(
        user_message="лучше 3 ночи",
        request=TripRequest(
            origin="Москва",
            departure_date=date(2026, 11, 10),
            nights=5,
            travelers=Travelers(adults=2),
        ),
        version=1,
    )
    state = state.model_copy(update=agent._understand(state))
    assert state.request.origin == "Москва" and state.request.nights == 3
    assert state.version == 2


def test_invalid_patch_does_not_mutate_state():
    agent = make_agent(FakeParser(ParsedTurn(nights=5, clear_fields=["nights"])))
    state = TravelGraphState(user_message="неоднозначно")
    try:
        agent._understand(state)
    except ValueError:
        pass
    else:
        assert False, "Should reject contradictory updates"
    assert state.version == 0 and state.request.nights is None


def test_other_intent_does_not_change_request():
    agent = make_agent(FakeParser(ParsedTurn(intent="other")))
    state = TravelGraphState(user_message="Что такое градиентный бустинг?")
    update = agent._understand(state)
    assert update["stage"] == "other"
    assert "request" not in update


def test_writer_failure_falls_back_without_fabricating_data():
    agent = make_agent(FakeParser())

    class BrokenWriter:
        def generate(self, **kwargs):
            raise RuntimeError("model failure")

    agent.writer = BrokenWriter()
    state = TravelGraphState(missing=["город отправления"], stage="needs_input")
    update = agent._clarify(state)
    assert update["stage"] == "needs_input"
    assert "город" in update["response"].lower()
    assert update["response"].strip().endswith("?")


def test_negotiation_does_not_apply_proposals():
    agent = make_agent(FakeParser())
    state = TravelGraphState(
        stage="negotiating",
        compromises=[
            {
                "kind": "budget",
                "description": "повысить бюджет",
                "requires_confirmation": True,
            }
        ],
    )
    update = agent._negotiate(state)
    assert update["response"]
    assert state.request.budget is None
