from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app.agent.graph_agent import GraphTravelAgent, TravelGraphState
from app.agent.mcp_search import McpCatalogSearch, McpSearchError
from app.mcp_catalog.contracts import NegotiateOffersResponse
from app.mcp_catalog.service import CatalogService
from app.schemas.request import Budget, TripRequest, Travelers


def req():
    return TripRequest(origin="Москва", departure_date=date(2026, 11, 10), nights=10,
        travelers=Travelers(adults=2), budget=Budget(amount=Decimal("70000"), currency="RUB", scope="whole_trip"))


def test_proposals_are_verified_and_do_not_mutate_user_request():
    initial = req()
    snapshot = initial.model_dump(mode="json")
    result = CatalogService().negotiate_offers(snapshot, allow_nights=True, limit=3)
    assert result.status == "ok" and result.proposals
    assert all(p.requires_confirmation and p.offer.provenance == "synthetic" for p in result.proposals)
    assert snapshot == initial.model_dump(mode="json")
    for proposal in result.proposals:
        offered = CatalogService().search_offers(proposal.proposed_request, limit=100)
        assert offered.status == "ok"
        assert proposal.offer.offer_id in {x.offer_id for x in offered.offers}
        assert Decimal(proposal.offer.price_rub) <= Decimal(proposal.proposed_request["budget"]["amount"])


def test_no_consent_means_no_budget_or_duration_proposals():
    result = CatalogService().negotiate_offers(req().model_dump(mode="json"))
    assert result.status == "no_match" and not result.proposals


def test_invalid_request_returns_safe_contract():
    result = CatalogService().negotiate_offers({"nights": -1}, allow_nights=True)
    assert result.status == "unsupported" and not result.proposals


def test_mcp_adapter_calls_negotiation_only_when_permitted():
    adapter = McpCatalogSearch()
    service = CatalogService()
    current = req().model_dump(mode="json")
    offers = service.search_offers(current).model_dump(mode="json")
    proposals = service.negotiate_offers(current, allow_nights=True).model_dump(mode="json")
    async def fake_search(_): return offers
    async def fake_negotiation(_, flags):
        assert flags["allow_nights"] is True
        return proposals
    with patch.object(adapter, "_call", side_effect=fake_search), patch.object(adapter, "_call_negotiation", side_effect=fake_negotiation) as mock:
        result = adapter(req(), allow_nights=True)
        assert mock.call_count == 1
    assert result.status == "negotiating" and result.compromises
    assert all(p["requires_confirmation"] for p in result.compromises)


def test_graph_passes_negotiated_mcp_proposals_to_response_node():
    adapter = McpCatalogSearch()
    service = CatalogService()
    current = req().model_dump(mode="json")
    async def fake_search(_): return service.search_offers(current).model_dump(mode="json")
    async def fake_negotiation(_, flags):
        return service.negotiate_offers(current, allow_nights=True).model_dump(mode="json")
    agent = object.__new__(GraphTravelAgent)
    agent.search = adapter
    with patch.object(adapter, "_call", side_effect=fake_search), patch.object(adapter, "_call_negotiation", side_effect=fake_negotiation):
        update = agent._search(TravelGraphState(request=req(), flags={"allow_nights": True}, stage="searching"))
    assert update["stage"] == "negotiating" and update["compromises"]
    assert all(x["requires_confirmation"] for x in update["compromises"])


def test_invalid_mcp_negotiation_contract_fails_closed():
    adapter = McpCatalogSearch()
    service = CatalogService()
    async def fake_search(_): return service.search_offers(req().model_dump(mode="json")).model_dump(mode="json")
    async def fake_negotiation(_, flags): return {"status": "ok", "proposals": [{"invalid": 1}]}
    with patch.object(adapter, "_call", side_effect=fake_search), patch.object(adapter, "_call_negotiation", side_effect=fake_negotiation):
        try:
            adapter(req(), allow_nights=True)
        except McpSearchError:
            pass
        else:
            raise AssertionError("Invalid proposal must fail closed")
