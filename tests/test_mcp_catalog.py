from datetime import date
from app.mcp_catalog.service import CatalogService
from app.schemas.request import TripRequest, Travelers


def request_json(**overrides):
    values = TripRequest(origin="Москва", departure_date=date(2026, 11, 10),
        nights=5, travelers=Travelers(adults=2)).model_dump(mode="json")
    values.update(overrides)
    return values


def test_missing_input():
    response = CatalogService().search_offers({})
    assert response.status == "needs_input"


def test_valid_catalog_and_provenance():
    found = CatalogService().search_offers(request_json(), limit=3)
    assert found.status == "ok" and len(found.offers) == 3
    assert all(x.provenance == "synthetic" and not x.is_bookable for x in found.offers)
    assert all(x.price_rub for x in found.offers)


def test_lookup_and_cart():
    svc = CatalogService()
    first = svc.search_offers(request_json(), limit=1).offers[0]
    details = svc.get_offer_details(request_json(), first.offer_id)
    assert details.status == "found"
    cart = svc.validate_cart(request_json(), first.offer_id)
    assert cart.status == "pass" and cart.total_rub == first.price_rub


def test_offer_from_another_request_rejected():
    svc = CatalogService()
    first = svc.search_offers(request_json(), limit=1).offers[0]
    changed = request_json(nights=6)
    assert svc.validate_cart(changed, first.offer_id).status == "not_found"


def test_booking_requires_confirmation():
    svc = CatalogService()
    first = svc.search_offers(request_json(), limit=1).offers[0]
    denied = svc.simulate_booking(request_json(), first.offer_id)
    assert denied.status == "confirmation_required" and denied.simulation_reference is None
    confirmed = svc.simulate_booking(request_json(), first.offer_id, user_confirmed=True)
    assert confirmed.status == "simulated" and confirmed.simulation_reference.startswith("DEMO-")
    assert not confirmed.real_booking_created and not confirmed.payment_collected


def test_fake_offer_cannot_be_booked():
    result = CatalogService().simulate_booking(request_json(), "not-an-offer", user_confirmed=True)
    assert result.status == "rejected"


def test_invalid_input_and_limits():
    svc = CatalogService()
    assert svc.search_offers(request_json(), limit=0).status == "unsupported"
    assert svc.search_offers({"garbage": 1}).status == "unsupported"


def test_hard_constraint_is_enforced_by_mcp():
    from app.schemas.request import Constraint
    req = request_json()
    req["constraints"] = [Constraint(field="transport.stops", operator="eq", value=0).model_dump(mode="json")]
    svc = CatalogService()
    offers = svc.search_offers(req, limit=50)
    assert offers.status == "ok" and offers.offers
    assert all("-direct-" in row.offer_id for row in offers.offers)
    # Connecting offer is in the underlying catalog but must NOT escape the tool.
    no_constraint = svc.search_offers(request_json(), limit=50)
    connecting = next(x for x in no_constraint.offers if "-connecting-" in x.offer_id)
    assert svc.validate_cart(req, connecting.offer_id).status == "not_found"
