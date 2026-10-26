from datetime import date
from decimal import Decimal
import pytest
from pydantic import ValidationError
from app.schemas.request import (
    Budget, Constraint, DateWindow, Pet, Preference, RequestPatch, Travelers,
    TripRequest, apply_request_patch,
)


def test_initial_request_defaults_are_not_shared():
    first, second = TripRequest(), TripRequest()
    first.constraints.append(Constraint(field="transport.stops", operator="eq", value=0))
    assert second.constraints == []


def test_budget_normalized_and_hard_tolerance_rejected():
    assert Budget(amount=Decimal("90000"), currency="rub").currency == "RUB"
    with pytest.raises(ValidationError):
        Budget(amount=90000, currency="RUB", tolerance_fraction=0.1)


def test_incomplete_family_request_is_representable():
    request = TripRequest(travelers=Travelers(adults=2, children_ages_unknown=1, pets=[Pet(kind="dog")]))
    assert request.travelers.children_ages_unknown == 1


def test_invalid_dates_and_ages_rejected():
    with pytest.raises(ValidationError):
        TripRequest(departure_date=date(2026, 11, 10), return_date=date(2026, 11, 9))
    with pytest.raises(ValidationError):
        Travelers(children_ages=[18])
    with pytest.raises(ValidationError):
        DateWindow(earliest_departure=date(2026, 12, 2), latest_departure=date(2026, 11, 1))


def test_constraint_operator_validation():
    with pytest.raises(ValidationError):
        Constraint(field="hotel.stars", operator="between", value=[3])
    with pytest.raises(ValidationError):
        Constraint(field="hotel.stars", operator="gte", value=[3, 4])
    with pytest.raises(ValidationError):
        Constraint(field="dangerous arbitrary path", operator="eq", value=True)


def test_patch_keeps_unmentioned_fields():
    original = TripRequest(origin="Москва", destination="Анталья", nights=7,
                           budget=Budget(amount=90000, currency="RUB"))
    patch = RequestPatch.model_validate({"operations": [{"op": "set", "field": "nights", "value": 5}]})
    updated = apply_request_patch(original, patch)
    assert updated.nights == 5 and updated.origin == "Москва"
    assert updated.budget == original.budget
    assert original.nights == 7


def test_patch_can_clear_value():
    original = TripRequest(destination="Анталья")
    patch = RequestPatch.model_validate({"operations": [{"op": "remove", "field": "destination"}]})
    assert apply_request_patch(original, patch).destination is None


def test_patch_rejects_invalid_field_and_duplicate_edits():
    with pytest.raises(ValidationError):
        RequestPatch.model_validate({"operations": [{"op": "set", "field": "__class__", "value": "a"}]})
    with pytest.raises(ValidationError):
        RequestPatch.model_validate({"operations": [
            {"op": "set", "field": "nights", "value": 3},
            {"op": "remove", "field": "nights"}
        ]})


def test_invalid_patch_is_atomic():
    original = TripRequest(nights=7, origin="Москва")
    patch = RequestPatch.model_validate({"operations": [
        {"op": "set", "field": "origin", "value": "Казань"},
        {"op": "set", "field": "nights", "value": -10}
    ]})
    with pytest.raises(ValidationError):
        apply_request_patch(original, patch)
    assert original.origin == "Москва" and original.nights == 7


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        TripRequest.model_validate({"origin": "Москва", "invented": 42})
