"""MCP-only synchronous cart and demo booking adapter."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from pydantic import BaseModel

from app.mcp_catalog.contracts import (
    BookingResponse,
    CartValidationResponse,
    OfferDetailsResponse,
)
from app.mcp_catalog.result_decode import decode_tool_result
from app.schemas.request import TripRequest


class CatalogToolError(RuntimeError):
    pass


class McpCatalogBooking:
    def __init__(self, *, command: str | None = None, module: str = "app.mcp_catalog.server"):
        self.command = command or sys.executable
        self.module = module

    async def _invoke(self, tool_name: str, args: dict[str, Any]) -> dict:
        from mcp import Client, StdioServerParameters

        params = StdioServerParameters(
            command=self.command,
            args=["-m", self.module],
            env=dict(os.environ),
        )
        async with Client(params) as client:
            result = await client.call_tool(tool_name, args)
            return decode_tool_result(result)

    def _call(
        self,
        tool_name: str,
        model: type[BaseModel],
        request: TripRequest,
        offer_id: str,
        **kwargs: Any,
    ):
        try:
            payload = asyncio.run(
                self._invoke(
                    tool_name,
                    {
                        "request_json": request.model_dump(mode="json"),
                        "offer_id": offer_id,
                        **kwargs,
                    },
                )
            )
            return model.model_validate(payload)
        except Exception as exc:
            raise CatalogToolError(f"{tool_name} failed: {type(exc).__name__}: {exc}") from exc

    def details(self, request: TripRequest, offer_id: str) -> OfferDetailsResponse:
        return self._call("get_offer_details", OfferDetailsResponse, request, offer_id)

    def validate(self, request: TripRequest, offer_id: str) -> CartValidationResponse:
        return self._call("validate_cart", CartValidationResponse, request, offer_id)

    def simulate(self, request: TripRequest, offer_id: str) -> BookingResponse:
        return self._call(
            "simulate_booking", BookingResponse, request, offer_id, user_confirmed=True
        )
