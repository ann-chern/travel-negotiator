from types import SimpleNamespace as N
import pytest
from app.agent.llm_provider import YandexTurnParser
from app.agent.orchestrator import _apply_turn
from app.schemas.request import TripRequest


def _parser(items, monkeypatch):
    monkeypatch.setenv("YANDEX_PARSER_FORMAT", "json_object")
    class Fake:
        def __init__(self): self.items = iter(items); self.chat = N(completions=N(create=self.create)); self.calls=[]
        def create(self, **kw):
            self.calls.append(kw)
            nxt=next(self.items)
            if isinstance(nxt, Exception): raise nxt
            return N(choices=[N(message=N(content=nxt[0]), finish_reason=nxt[1])])
    client = Fake()
    return YandexTurnParser(folder_id="test-folder", client=client), client


def test_normal_legacy_json(monkeypatch):
    parser, client = _parser([('{"origin":"Москва","nights":5,"adults":2}', 'stop')], monkeypatch)
    turn=parser.parse("поездка", {})
    assert turn.origin == "Москва" and turn.nights == 5
    assert client.calls[0]["response_format"] == {"type":"json_object"}


def test_invalid_schema_then_valid(monkeypatch):
    parser, client = _parser([('{"nights":"пять"}', 'stop'), ('{"nights":5}', 'stop')], monkeypatch)
    assert parser.parse("5 ночей", {}).nights == 5
    assert len(client.calls)==2


def test_truncated_then_valid(monkeypatch):
    parser, client = _parser([('{"nights":5}', 'length'), ('{"nights":5}', 'stop')], monkeypatch)
    assert parser.parse("5 ночей", {}).nights == 5


def test_unsupported_mixed_format_recovered(monkeypatch):
    parser, _ = _parser([('{"operations":[{"op":"set","field":"nights","value":5}],"nights":7}', 'stop'), ('{"nights":5}', 'stop')], monkeypatch)
    assert parser.parse("5 ночей", {}).nights == 5


def test_no_response_fails_and_original_request_unchanged(monkeypatch):
    parser, _ = _parser([('', 'length'), ('', 'length')], monkeypatch)
    original=TripRequest(origin="Москва")
    before=original.model_dump(mode="json")
    with pytest.raises(ValueError): parser.parse("blah", before)
    assert original.model_dump(mode="json") == before


def test_valid_operations_apply_atomically(monkeypatch):
    parser, _ = _parser([('{"operations":[{"op":"set","field":"nights","value":6}]}', 'stop')], monkeypatch)
    original=TripRequest(origin="Москва", nights=5)
    updated=_apply_turn(original, parser.parse("6 ночей", original.model_dump(mode="json")))
    assert updated.nights==6 and original.nights==5
