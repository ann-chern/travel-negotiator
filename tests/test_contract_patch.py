from datetime import date
import pytest
from pydantic import ValidationError

from app.agent.llm_provider import ParsedTurn, TurnOperation
from app.agent.orchestrator import _apply_turn
from app.mcp_catalog.service import CatalogService
from app.mcp_catalog.contracts import SearchOffersResponse
from app.schemas.request import TripRequest, Travelers


def _base():
    return _apply_turn(
        TripRequest(origin='Москва', departure_date=date(2026, 11, 10), nights=5,
                    travelers=Travelers(adults=2)),
        ParsedTurn(children_ages=[4], pet_kind='dog', direct_flights=True, meal_plan='breakfast')
    )


def test_mcp_search_exposes_evidenced_amenities():
    payload = CatalogService().search_offers(_base().model_dump(mode='json'), limit=3)
    assert payload.status == 'ok'
    assert payload.offers
    for offer in payload.offers:
        assert offer.meal_plan == 'breakfast'
        assert offer.outbound_stops == 0
        assert offer.inbound_stops == 0
        assert offer.hotel_pets_allowed is True
        assert offer.adults == 2
        assert offer.children_ages == [4]
        assert offer.pets_count == 1
        assert offer.provenance == 'synthetic'
    assert SearchOffersResponse.model_validate(payload.model_dump()).offers[0].meal_plan == 'breakfast'


def test_multiple_removals_are_atomic_and_preserve_travelers():
    old = _base()
    turn = ParsedTurn(operations=[
        TurnOperation(op='remove', field='transport.stops'),
        TurnOperation(op='remove', field='hotel.meal_plan')])
    new = _apply_turn(old, turn)
    assert not new.constraints
    assert new.travelers == old.travelers
    assert len(old.constraints) == 2


def test_set_remove_and_validate():
    old = _base()
    new = _apply_turn(old, ParsedTurn(operations=[
        TurnOperation(op='set', field='nights', value=6),
        TurnOperation(op='set', field='hotel.meal_plan', value='all_inclusive')]))
    assert new.nights == 6
    assert len(new.constraints) == 2
    assert next(c.value for c in new.constraints if c.field == 'hotel.meal_plan') == 'all_inclusive'
    assert old.nights == 5


def test_invalid_second_operation_does_not_mutate_original():
    old = _base()
    turn = ParsedTurn(operations=[TurnOperation(op='set', field='nights', value=6),
                                  TurnOperation(op='set', field='transport.stops', value=2)])
    with pytest.raises(ValueError):
        _apply_turn(old, turn)
    assert old.nights == 5
    assert next(c.value for c in old.constraints if c.field == 'transport.stops') == 0


def test_duplicate_operations_rejected():
    old = _base()
    turn = ParsedTurn(operations=[TurnOperation(op='set', field='nights', value=6),
                                  TurnOperation(op='remove', field='nights')])
    with pytest.raises(ValueError, match='Duplicate'):
        _apply_turn(old, turn)


def test_new_and_legacy_formats_cannot_conflict():
    with pytest.raises(ValueError, match='legacy'):
        _apply_turn(_base(), ParsedTurn(nights=6, operations=[TurnOperation(op='remove', field='hotel.meal_plan')]))


def test_bad_remove_value_and_field_rejected():
    with pytest.raises(ValidationError):
        TurnOperation(op='remove', field='nights', value=4)
    with pytest.raises(ValidationError):
        TurnOperation(op='set', field='something.malicious', value='yes')
