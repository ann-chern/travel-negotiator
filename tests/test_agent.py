from datetime import date
from app.agent.llm_provider import ParsedTurn
from app.agent.orchestrator import TravelAgent, Session


class FakeParser:
    def __init__(self, *turns):
        self.turns = iter(turns)
    def parse(self, message, current_state):
        return next(self.turns)


def test_request_is_accumulated_and_search_started():
    agent = TravelAgent(FakeParser(
        ParsedTurn(origin="Москва", adults=2),
        ParsedTurn(departure_date=date(2026, 11, 10), nights=5)))
    session = Session()
    reply = agent.handle(session, "Из Москвы на двоих")
    assert reply.status == "needs_input" and session.version == 1
    reply = agent.handle(session, "10 ноября на 5 ночей")
    assert reply.status == "ready" and reply.options and session.version == 2
    assert session.request.origin == "Москва"


def test_revision_preserves_other_fields():
    agent = TravelAgent(FakeParser(
        ParsedTurn(origin="Москва", adults=2, nights=5, departure_date=date(2026, 11, 10)),
        ParsedTurn(nights=6, intent="revise")))
    state = Session()
    agent.handle(state, "поездка")
    reply = agent.handle(state, "на 6 ночей")
    assert reply.status == "ready"
    assert state.request.origin == "Москва"
    assert state.request.nights == 6
    assert state.request.departure_date == date(2026, 11, 10)


def test_failure_is_transactional():
    agent = TravelAgent(FakeParser(ParsedTurn(origin="Москва", adults=2), ParsedTurn(nights=5, clear_fields=["nights"])))
    session = Session()
    agent.handle(session, "Москва")
    before = session.request.model_dump()
    reply = agent.handle(session, "ошибка")
    assert reply.status == "error"
    assert session.request.model_dump() == before and session.version == 1


def test_no_unapproved_budget_mutation():
    agent = TravelAgent(FakeParser(ParsedTurn(origin="Москва", adults=2, nights=10,
        departure_date=date(2026, 11, 10), budget_rub=48000, allow_budget_increase=True)))
    state = Session()
    reply = agent.handle(state, "до 48 тысяч, бюджет можно обсудить")
    assert state.request.budget.amount == 48000
    assert reply.status in ("no_match", "ready", "unsupported")


def test_clear_field_is_not_silently_restored():
    agent = TravelAgent(FakeParser(ParsedTurn(origin="Москва", adults=2), ParsedTurn(clear_fields=["origin"])))
    state = Session()
    agent.handle(state, "вылет Москва")
    reply = agent.handle(state, "город вылета пока не знаю")
    assert state.request.origin is None
    assert reply.status == "needs_input"


def test_yandex_parser_uses_structured_schema_without_network():
    from types import SimpleNamespace
    from app.agent.llm_provider import YandexTurnParser
    class Completion:
        def __init__(self): self.kwargs = None
        def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                content='{"origin":"Москва","nights":5}'))])
    calls = Completion()
    client = SimpleNamespace(chat=SimpleNamespace(completions=calls))
    parser = YandexTurnParser(client=client, folder_id="test-folder", model="aliceai-llm-flash")
    parsed = parser.parse("Из Москвы на пять ночей", {})
    assert parsed.origin == "Москва" and parsed.nights == 5
    assert calls.kwargs["model"] == "gpt://test-folder/aliceai-llm-flash"
    assert calls.kwargs["response_format"]["type"] == "json_schema"
