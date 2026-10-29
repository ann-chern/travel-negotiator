import json

import pytest

from app.data.loader import FIXTURES_DIR, load_catalog_fixtures


def test_catalog_fixtures_are_complete_and_cross_referenced():
    fixtures = load_catalog_fixtures()
    destination_ids = {destination.id for destination in fixtures.destinations}

    assert len(destination_ids) == 15
    assert set(fixtures.routes) == destination_ids
    assert set(fixtures.price_bands) == destination_ids
    assert set(fixtures.hotel_levels) == {hotel.slug for hotel in fixtures.hotels}


def test_loader_rejects_missing_route(tmp_path):
    for name in ("destinations.json", "hotels.json", "routes.json", "pricing.json"):
        payload = json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))
        if name == "routes.json":
            payload["routes"].pop()
        (tmp_path / name).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    with pytest.raises(ValueError, match="exactly one route"):
        load_catalog_fixtures(tmp_path)
