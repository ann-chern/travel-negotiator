from decimal import Decimal
from types import SimpleNamespace

from app.agent.graph_agent import GraphTravelAgent
from app.agent.skills import SkillRouter, load_skills
from app.agent.text_provider import YandexTextGenerator
from app.schemas.agent import SkillName, TravelGraphState
from app.schemas.request import Budget, TripRequest


class UnusedParser:
    def parse(self, message, current_state):
        raise AssertionError("parser is not used by these node tests")


class RecordingWriter:
    def __init__(self):
        self.calls = []

    def generate(self, *, task, payload):
        self.calls.append((task, payload))
        return "Проверенное объяснение."


def test_skill_files_load_with_expected_tools():
    skills = load_skills()

    assert set(skills) == {skill.value for skill in SkillName}
    assert skills[SkillName.DESTINATION_DISCOVERY].tools == (
        "search_offers",
        "search_date_window",
    )
    assert skills[SkillName.BUDGET_NEGOTIATION].tools == ("negotiate_offers",)


def test_router_selects_destination_discovery_only_for_open_destination():
    router = SkillRouter()
    options = [{"id": "offer-1", "destination": "sochi"}]

    selected = router.for_recommendation(TripRequest(), options)
    explicit = router.for_recommendation(TripRequest(destination="Сочи"), options)

    assert selected and selected.name == SkillName.DESTINATION_DISCOVERY
    assert explicit is None


def test_router_selects_budget_skill_only_for_verified_budget_gap():
    router = SkillRouter()
    request = TripRequest(
        budget=Budget(amount=Decimal("100000"), currency="RUB", scope="whole_trip")
    )

    selected = router.for_negotiation(
        request,
        ["Минимальная цена 120000 RUB превышает бюджет на 20000 RUB"],
        [],
    )
    unrelated = router.for_negotiation(request, ["Нет подходящих дат"], [])

    assert selected and selected.name == SkillName.BUDGET_NEGOTIATION
    assert unrelated is None


def test_recommendation_with_skill_preserves_result_and_adds_instructions():
    enabled_writer = RecordingWriter()
    disabled_writer = RecordingWriter()
    state = TravelGraphState(
        request=TripRequest(),
        stage="ready",
        options=[{"id": "offer-1", "destination": "sochi"}],
    )
    enabled_agent = GraphTravelAgent(UnusedParser(), enabled_writer)
    disabled_agent = GraphTravelAgent(
        UnusedParser(),
        disabled_writer,
        skill_router=SkillRouter(enabled=False),
    )

    enabled = enabled_agent._recommend(state)
    disabled = disabled_agent._recommend(state)

    assert enabled["response"] == disabled["response"]
    assert enabled["active_skill"] == SkillName.DESTINATION_DISCOVERY
    assert disabled["active_skill"] is None
    assert "валидированные предложения" in enabled_writer.calls[0][1]["skill_instructions"]
    assert disabled_writer.calls[0][1]["skill_instructions"] is None


def test_budget_skill_uses_verified_compromises_without_applying_them():
    writer = RecordingWriter()
    agent = GraphTravelAgent(UnusedParser(), writer)
    request = TripRequest(
        budget=Budget(amount=Decimal("100000"), currency="RUB", scope="whole_trip")
    )
    state = TravelGraphState(
        request=request,
        stage="negotiating",
        reasons=["Бюджет ниже минимальной найденной цены"],
        compromises=[
            {
                "kind": "budget",
                "description": "Предложить бюджет 110000 RUB",
                "requires_confirmation": True,
            }
        ],
    )

    update = agent._negotiate(state)

    assert update["active_skill"] == SkillName.BUDGET_NEGOTIATION
    assert state.request.budget.amount == Decimal("100000")
    assert state.compromises[0]["requires_confirmation"] is True
    assert "явное подтверждение" in writer.calls[0][1]["skill_instructions"]


def test_text_generator_places_skill_in_system_message():
    class Completions:
        def __init__(self):
            self.messages = None

        def create(self, **kwargs):
            self.messages = kwargs["messages"]
            choice = SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="Готово."),
            )
            return SimpleNamespace(choices=[choice], usage=None)

    completions = Completions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    generator = YandexTextGenerator(client=client, folder_id="folder")

    generator.generate(
        task="recommendation",
        payload={
            "options": [],
            "skill": "destination-discovery",
            "skill_instructions": "Сравни только подтверждённые варианты.",
        },
    )

    assert "Специализированная процедура" in completions.messages[0]["content"]
    assert "Сравни только подтверждённые варианты" in completions.messages[0]["content"]
