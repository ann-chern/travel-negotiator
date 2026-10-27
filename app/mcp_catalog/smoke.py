"""Actual MCP transport smoke test over stdio."""
import asyncio
import json
import sys
from datetime import date

from mcp import Client, StdioServerParameters

from app.mcp_catalog.contracts import SearchOffersResponse
from app.mcp_catalog.result_decode import decode_tool_result
from app.schemas.request import TripRequest, Travelers


async def smoke() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_catalog.server"],
    )
    async with Client(params) as client:
        tools = await client.list_tools()
        names = [tool.name for tool in tools.tools]
        print("MCP tools:", names)
        required = {"search_offers", "get_offer_details", "validate_cart", "simulate_booking"}
        if not required.issubset(names):
            raise RuntimeError(f"Missing MCP tools: {sorted(required - set(names))}")
        request = TripRequest(
            origin="Москва",
            departure_date=date(2026, 11, 10),
            nights=5,
            travelers=Travelers(adults=2),
        ).model_dump(mode="json")
        result = await client.call_tool("search_offers", {"request_json": request, "limit": 2})
        payload = decode_tool_result(result)
        parsed = SearchOffersResponse.model_validate(payload)
        if parsed.status != "ok":
            raise RuntimeError(f"search_offers returned {parsed.status}: {parsed.message}")
        if not parsed.offers:
            raise RuntimeError("search_offers returned zero offers for a known supported request")
        print("search_offers:", json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False, indent=2))
        print(f"MCP smoke passed: {len(parsed.offers)} demo offers")


if __name__ == "__main__":
    asyncio.run(smoke())
