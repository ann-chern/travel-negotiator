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
def search_date_window(request_json: dict, limit: int = 3) -> dict:
    """Search all days within a <=31-day departure window using synthetic catalog."""
    return _service.search_date_window(request_json, limit).model_dump(mode="json")


@server.tool()
def negotiate_offers(
    request_json: dict,
    allow_nights: bool = False,
    allow_date_shift: bool = False,
    allow_budget_increase: bool = False,
    limit: int = 3,
) -> dict:
    """Offer verified, unapproved changes to budget/nights/dates; never mutates request."""
    return _service.negotiate_offers(
        request_json,
        allow_nights=allow_nights,
        allow_date_shift=allow_date_shift,
        allow_budget_increase=allow_budget_increase,
        limit=limit,
    ).model_dump(mode="json")


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


@server.tool()
def search_historical_snapshots(destination_id: str | None = None, limit: int = 20) -> dict:
    """Read sourced historical price observations, NOT live inventory or bookable trips."""
    from app.data.snapshot_catalog import SnapshotCatalogProvider

    rows = SnapshotCatalogProvider().search(destination_id=destination_id)
    return {
        "mode": "historical_non_bookable",
        "total": len(rows),
        "offers": [r.model_dump(mode="json") for r in rows[: max(1, min(limit, 100))]],
    }
