"""MCP-only catalog search adapter for the synchronous LangGraph node.

No direct CatalogService or catalog imports are allowed in this adapter.
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from pydantic import BaseModel, Field

from app.mcp_catalog.contracts import SearchOffersResponse, NegotiateOffersResponse
from app.mcp_catalog.result_decode import decode_tool_result
from app.schemas.request import TripRequest
from app.observability import event as audit_event


class McpSearchError(RuntimeError):
    """Remote catalog tool could not provide a trustworthy response."""


class McpSearchResult(BaseModel):
    status: str
    options: list[dict[str, Any]] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    compromises: list[dict[str, Any]] = Field(default_factory=list)


class McpCatalogSearch:
    """A new stdio MCP session per synchronous search; robust for the CLI MVP.

    Do not instantiate/run inside an already running asyncio event loop.
    A persistent async session can replace this transport without graph changes.
    """
    def __init__(self, *, command: str | None = None, module: str = "app.mcp_catalog.server", limit: int = 3):
        self.command, self.module, self.limit = command or sys.executable, module, limit

    async def _call(self, request_json: dict) -> dict:
        from mcp import Client, StdioServerParameters
        params = StdioServerParameters(
            command=self.command,
            args=["-m", self.module],
            env=dict(os.environ),
        )
        async with Client(params) as client:
            result = await client.call_tool("search_offers", {"request_json": request_json, "limit": self.limit})
            return decode_tool_result(result)

    async def _call_window(self, request_json: dict) -> dict:
        from mcp import Client, StdioServerParameters
        params = StdioServerParameters(
            command=self.command,
            args=["-m", self.module],
            env=dict(os.environ),
        )
        async with Client(params) as client:
            result = await client.call_tool("search_date_window", {"request_json": request_json, "limit": self.limit})
            return decode_tool_result(result)

    async def _call_negotiation(self, request_json: dict, flags: dict[str, Any]) -> dict:
        from mcp import Client, StdioServerParameters
        params = StdioServerParameters(
            command=self.command,
            args=["-m", self.module],
            env=dict(os.environ),
        )
        args = {"request_json": request_json, "limit": self.limit, **{
            key: bool(flags.get(key, False)) for key in
            ("allow_nights", "allow_date_shift", "allow_budget_increase")}}
        async with Client(params) as client:
            result = await client.call_tool("negotiate_offers", args)
            return decode_tool_result(result)

    def __call__(self, request: TripRequest, **kwargs: Any) -> McpSearchResult:
        request_json = request.model_dump(mode="json")
        tool = "search_date_window" if request.departure_date is None and request.date_window is not None else "search_offers"
        audit_event("catalog.search.started", boundary="mcp", tool=tool,
                    parameters={"request_json": request_json, "limit": self.limit},
                    negotiation_flags={key: bool(kwargs.get(key, False)) for key in
                        ("allow_nights", "allow_date_shift", "allow_budget_increase")})
        try:
            raw = asyncio.run(
                self._call_window(request_json)
                if request.departure_date is None and request.date_window is not None
                else self._call(request_json)
            )
            payload = SearchOffersResponse.model_validate(raw)
        except Exception as exc:
            audit_event("catalog.search.failed", error_type=type(exc).__name__)
            raise McpSearchError(f"MCP catalog search failed: {type(exc).__name__}: {exc}") from exc
        if payload.status == "no_match" and request.date_window is None and any(bool(kwargs.get(k)) for k in (
            "allow_nights", "allow_date_shift", "allow_budget_increase"
        )):
            try:
                negotiation_raw = asyncio.run(self._call_negotiation(
                    request.model_dump(mode="json"), kwargs))
                negotiated = NegotiateOffersResponse.model_validate(negotiation_raw)
            except Exception as exc:
                raise McpSearchError(f"MCP negotiation failed: {type(exc).__name__}") from exc
            proposals = [{"kind": x.kind, "description": x.description,
                          "price_rub": x.offer.price_rub,
                          "offer_id": x.offer.offer_id,
                          "proposed_request": x.proposed_request,
                          "penalty": x.penalty, "requires_confirmation": True,
                          "synthetic": True} for x in negotiated.proposals]
            audit_event("catalog.negotiation.completed", status=negotiated.status,
                        proposals=proposals, reasons=[negotiated.message])
            return McpSearchResult(status="negotiating" if proposals else "no_match",
                                   reasons=[negotiated.message], compromises=proposals)
        if payload.status == "ok":
            options = [{"id": o.offer_id, "destination": o.destination_id,
                        "hotel": o.hotel_name, "price_rub": o.price_rub,
                        "price_breakdown_rub": o.price_breakdown_rub,
                        "departure_date": o.departure_date, "nights": o.nights,
                        "score": o.match_score, "tradeoffs": o.match_caveats,
                        "match_reasons": o.match_reasons, "synthetic": o.provenance == "synthetic",
                        "availability": o.availability, "is_bookable": o.is_bookable,
                        "hotel_stars": o.hotel_stars, "meal_plan": o.meal_plan,
                        "hotel_pets_allowed": o.hotel_pets_allowed,
                        "outbound_stops": o.outbound_stops, "inbound_stops": o.inbound_stops,
                        "adults": o.adults, "children_ages": o.children_ages,
                        "pets_count": o.pets_count}
                       for o in payload.offers]
            audit_event("catalog.search.completed", status="ready" if options else "no_match",
                        ranking_rule="match_score desc, price asc, departure_date asc, offer_id asc",
                        ranked_offers=[{"rank": rank, "offer_id": option["id"],
                            "score": option["score"], "price_rub": option["price_rub"],
                            "match_reasons": option["match_reasons"],
                            "tradeoffs": option["tradeoffs"]}
                            for rank, option in enumerate(options, start=1)])
            return McpSearchResult(status="ready" if options else "no_match", options=options,
                                   reasons=[] if options else [payload.message or "Нет подходящих предложений"])
        reasons = [payload.message] if payload.message else []
        audit_event("catalog.search.completed", status=payload.status, reasons=reasons,
                    ranked_offers=[])
        return McpSearchResult(status=payload.status, reasons=reasons)
