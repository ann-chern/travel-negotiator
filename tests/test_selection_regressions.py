from pathlib import Path
from datetime import date

from app.agent.graph_agent import GraphTravelAgent, TravelGraphState
from app.schemas.request import TripRequest


def test_selection_phrases():
    options = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    for message in ("выбрать вариант 2", "Выбираю вариант 2", "беру второй вариант", "Беру второй вариант"):
        assert GraphTravelAgent._selected_option(options, message) == options[1]
    assert GraphTravelAgent._selected_option(options, "да") is None


def test_number_answers_adult_clarification_without_parser():
    agent = object.__new__(GraphTravelAgent)
    class Parser:
        def parse(self, *args):
            raise AssertionError("A numeric answer must not reach LLM parser")
    agent.parser = Parser()
    state = TravelGraphState(
        user_message="1", stage="needs_input", pending_question="Сколько взрослых поедет?",
        request=TripRequest(origin="Москва", destination="Япония", departure_date=date(2026, 11, 10), nights=14),
    )
    update = agent._understand(state)
    assert update["request"]["travelers"]["adults"] == 1
    assert update["stage"] == "parsed"


def test_browser_actions_not_permanently_disabled():
    script = (Path(__file__).parents[1] / "app/web/static/app.js").read_text(encoding="utf-8")
    assert "button.disabled=busy" not in script
