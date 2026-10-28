import json
from datetime import date
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from app.agent.compact_parser import CompactTurn, CompactYandexTurnParser
from app.agent.orchestrator import _apply_turn
from app.schemas.request import TripRequest, Travelers


class DummyCompletions:
    def __init__(self, outcomes): self.outcomes = iter(outcomes); self.calls = 0
    def create(self, **kwargs):
        self.calls += 1
        item = next(self.outcomes)
        if isinstance(item, Exception): raise item
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=item), finish_reason='stop')],
                               usage=SimpleNamespace(prompt_tokens=100, completion_tokens=55))


def parser(*outputs, retry_invalid=True):
    api = DummyCompletions(outputs)
    obj = CompactYandexTurnParser(folder_id='test-folder', client=SimpleNamespace(chat=SimpleNamespace(completions=api)),
                                  retry_invalid=retry_invalid)
    return obj, api


def test_compact_new_request_and_constraints():
    output = json.dumps({'intent':'plan','operations':[
        {'op':'set','field':'origin','value':'Москва'},
        {'op':'set','field':'departure_date','value':'2026-11-10'},
        {'op':'set','field':'nights','value':5},
        {'op':'set','field':'adults','value':2},
        {'op':'set','field':'children_ages','value':[4]},
        {'op':'set','field':'pets','value':'dog'},
        {'op':'set','field':'transport.stops','value':0},
        {'op':'set','field':'hotel.meal_plan','value':'breakfast'}]})
    p,_ = parser(output)
    req = _apply_turn(TripRequest(), p.parse('request', {}))
    assert req.origin == 'Москва'
    assert req.departure_date == date(2026,11,10)
    assert req.travelers.children_ages == [4]
    assert req.travelers.pets[0].kind == 'dog'
    assert {x.field for x in req.constraints} == {'transport.stops','hotel.meal_plan'}


def test_removals_preserve_other_fields():
    initial = TripRequest(origin='Москва', nights=5, travelers=Travelers(adults=2, children_ages=[4]))
    out = '{"intent":"revise","operations":[{"op":"set","field":"nights","value":6}]}'
    p,_ = parser(out)
    updated = _apply_turn(initial, p.parse('change', initial.model_dump(mode='json')))
    assert updated.nights == 6 and updated.origin == 'Москва' and updated.travelers.children_ages == [4]
    assert initial.nights == 5


def test_duplicate_edits_rejected_before_state_change():
    with pytest.raises(ValidationError):
        CompactTurn.model_validate({'operations':[{'op':'set','field':'nights','value':5},
                                                  {'op':'remove','field':'nights','value':None}]})


def test_bad_edit_is_atomic():
    previous = TripRequest(nights=5)
    output = '{"intent":"revise","operations":[{"op":"set","field":"nights","value":6},{"op":"set","field":"children_ages","value":[19]}]}'
    p,_ = parser(output)
    with pytest.raises(ValidationError):
        _apply_turn(previous, p.parse('change', {}))
    assert previous.nights == 5


def test_api_timeout_does_not_retry():
    class APITimeoutError(Exception): pass
    p, api = parser(APITimeoutError())
    with pytest.raises(APITimeoutError): p.parse('msg', {})
    assert api.calls == 1 and p.last_diagnostic['error_type'] == 'APITimeoutError'


def test_invalid_json_single_corrective_retry():
    p, api = parser('garbage', '{"intent":"revise","operations":[]}')
    assert p.parse('msg', {}).intent == 'revise'
    assert api.calls == 2


def test_invalid_json_twice_fails_closed():
    p, api = parser('garbage', 'still garbage')
    with pytest.raises(ValueError): p.parse('msg', {})
    assert api.calls == 2
