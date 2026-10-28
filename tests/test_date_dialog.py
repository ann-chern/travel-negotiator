from datetime import date

from app.agent.clarification import select_clarification
from app.agent.date_intent import recognize_window
from app.agent.graph_agent import GraphTravelAgent, TravelGraphState
from app.agent.llm_provider import ParsedTurn, TurnOperation
from app.schemas.request import Travelers, TripRequest


def test_range_never_becomes_exact_departure():
    w = recognize_window("с 14 по 30 ноября", today=date(2026, 10, 27))
    assert w.earliest_departure == date(2026, 11, 14)
    assert w.latest_departure == date(2026, 11, 30)
    assert recognize_window(
        "во второй половине ноября", date(2026, 10, 27)
    ).earliest_departure == date(2026, 11, 16)


def test_range_is_sufficient_for_search_without_destination():
    w = recognize_window("во второй половине ноября", date(2026, 10, 27))
    r = TripRequest(origin="Москва", nights=5, travelers=Travelers(adults=2), date_window=w)
    q = select_clarification(r)
    assert q is None


def test_exact_date_supersedes_range():
    class Parser:
        def parse(self, message, current_state):
            return ParsedTurn(
                intent="revise",
                operations=[TurnOperation(op="set", field="departure_date", value="2026-06-14")],
            )

    agent = object.__new__(GraphTravelAgent)
    agent.parser = Parser()
    base = TripRequest(
        origin="Москва",
        nights=5,
        travelers=Travelers(adults=2),
        date_window=recognize_window("с 14 по 30 ноября", date(2026, 10, 27)),
    )
    data = agent._understand(TravelGraphState(user_message="14 июня 2026 года", request=base))
    updated = TripRequest.model_validate(data["request"])
    assert updated.departure_date == date(2026, 6, 14) and updated.date_window is None


def test_unsupported_explanation_no_fabricated_required_fields():
    class Writer:
        def generate(self, **kwargs):
            raise AssertionError("Should not call LLM to invent catalog limitations")

    agent = object.__new__(GraphTravelAgent)
    agent.writer = Writer()
    result = agent._negotiate(
        TravelGraphState(stage="unsupported", reasons=["Нет покрытия каталога"])
    )
    assert "Направление выбирать не обязательно" in result["response"]
