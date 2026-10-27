"""MCP v2 stdio server. stdout is protocol-only; log to stderr."""
from mcp.server.mcpserver import MCPServer
from app.mcp_catalog.service import CatalogService

server = MCPServer("Travel Negotiator Demo Catalog")
_service = CatalogService()


@server.tool()
def search_offers(request_json: dict, limit: int = 10) -> dict:
    """Search fictional travel offers; never claim live price or availability."""
    return _service.search_offers(request_json, limit).model_dump(mode="json")


@server.tool()
def get_offer_details(request_json: dict, offer_id: str) -> dict:
    """Read synthetic hotel, transport and transfer details for an offer ID."""
    return _service.get_offer_details(request_json, offer_id).model_dump(mode="json")


@server.tool()
def validate_cart(request_json: dict, offer_id: str) -> dict:
    """Revalidate a demo trip candidate against a complete request."""
    return _service.validate_cart(request_json, offer_id).model_dump(mode="json")


@server.tool()
def simulate_booking(request_json: dict, offer_id: str, user_confirmed: bool = False) -> dict:
    """Never books or charges; requires explicit confirmation for a demo receipt."""
    return _service.simulate_booking(request_json, offer_id, user_confirmed).model_dump(mode="json")


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
