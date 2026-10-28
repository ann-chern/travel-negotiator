from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app.agent.graph_agent import GraphTravelAgent, TravelGraphState
from app.agent.mcp_search import McpCatalogSearch, McpSearchError
from app.mcp_catalog.service import CatalogService
from app.schemas.request import (
    Budget,
    Constraint,
    Operator,
    Strength,
    Travelers,
    TripRequest,
)


def request():
    return TripRequest(
        origin="Москва",
        departure_date=date(2026, 11, 10),
        nights=5,
        travelers=Travelers(adults=2),
    )


def test_mcp_adapter_reads_public_contract_without_calling_local_catalog():
    payload = CatalogService().search_offers(request().model_dump(mode="json"), limit=2)
    adapter = McpCatalogSearch()
    with patch.object(adapter, "_call", return_value=payload.model_dump(mode="json")) as mock:
        result = adapter(request())
    assert mock.called and result.status == "ready" and len(result.options) == 2
    assert all(x["synthetic"] and not x["is_bookable"] for x in result.options)


def test_adapter_rejects_malformed_response():
    adapter = McpCatalogSearch()
    with patch.object(
        adapter, "_call", return_value={"status": "ok", "offers": [{"broken": True}]}
    ):
        try:
            adapter(request())
        except McpSearchError:
            pass
        else:
            assert False


def test_mcp_service_respects_hard_budget():
    req = request().model_copy(
        update={"budget": Budget(amount=Decimal("100"), currency="RUB", scope="whole_trip")}
    )
    result = CatalogService().search_offers(req.model_dump(mode="json"))
    assert result.status == "no_match" and not result.offers


def test_mcp_service_respects_hard_constraints():
    req = request().model_copy(
        update={
            "constraints": [
                Constraint(
                    field="hotel.stars",
                    operator=Operator.GTE,
                    value=5,
                    strength=Strength.HARD,
                )
            ]
        }
    )
    result = CatalogService().search_offers(req.model_dump(mode="json"))
    assert result.status in ("ok", "no_match")
    if result.status == "ok":
        details = [
            CatalogService().get_offer_details(req.model_dump(mode="json"), x.offer_id)
            for x in result.offers
        ]
        assert all(d.hotel["stars"] == 5 for d in details)


def test_graph_search_fail_closed_when_mcp_unavailable():
    agent = object.__new__(GraphTravelAgent)

    def fail(*args, **kwargs):
        raise McpSearchError("offline")

    agent.search = fail
    state = TravelGraphState(request=request(), stage="searching")
    result = agent._search(state)
    assert result["stage"] == "catalog_error" and not result["options"]
    state = state.model_copy(update=result)
    assert "недоступен" in agent._negotiate(state)["response"]


def test_graph_converts_mcp_offers_into_options():
    agent = object.__new__(GraphTravelAgent)
    adapter = McpCatalogSearch()
    payload = CatalogService().search_offers(request().model_dump(mode="json"), limit=2)
    with patch.object(adapter, "_call", return_value=payload.model_dump(mode="json")):
        agent.search = adapter
        result = agent._search(TravelGraphState(request=request(), stage="searching"))
    assert result["stage"] == "ready" and len(result["options"]) == 2
