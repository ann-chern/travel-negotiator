import json
from datetime import date
from pathlib import Path
import pytest

from app.services.openmeteo import refresh_city, write_cache
from app.services.seasonality import OpenMeteoSeasonalityProvider
from app.data.snapshot_catalog import SnapshotCatalogProvider
from app.data.catalog import active_cities


def test_openmeteo_data_ingestion_and_unknown_sea(tmp_path):
    class Response:
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def opener(url, timeout):
        if 'daily=temperature_2m_max' in url:
            return ResponseFile({'daily': {'time': [f'2025-09-{n:02d}' for n in range(1, 11)], 'temperature_2m_max': [28]*10}})
        return ResponseFile({'hourly': {'time': [f'2025-09-01T{n:02d}:00' for n in range(24)], 'sea_surface_temperature': [25]*24}})
    class ResponseFile(Response):
        def read(self, *_): return json.dumps(self.payload).encode()
    record = refresh_city('testsea', 12, 14, start_year=2025, end_year=2025, opener=opener)
    write_cache([record], tmp_path/'normals.json')
    assess = OpenMeteoSeasonalityProvider(tmp_path/'normals.json').assess_beach('testsea', date(2027,9,2))
    assert assess.status == 'favorable'
    assert assess.confidence == 'openmeteo_historical'
    assert OpenMeteoSeasonalityProvider(tmp_path/'missing.json').assess_beach('testsea', date(2027,9,2)).status == 'unknown'


def test_snapshots_require_source_and_are_not_bookable(tmp_path):
    provider = SnapshotCatalogProvider(tmp_path/'historical.json')
    assert provider.load() == []
    example = {'source_url':'https://example.com/booking/123','observed_at':'2026-09-01T08:00:00Z',
               'destination_id':'antalya','hotel_name':'Example Hotel','check_in':'2026-09-10',
               'check_out':'2026-09-17','price_rub':'45000','adults':2,'source_offer_id':'offer-1'}
    path = tmp_path/'in.json'; path.write_text(json.dumps([example]), encoding='utf-8')
    assert provider.import_file(path) == 1
    assert provider.import_file(path) == 1
    assert len(provider.load()) == 1
    assert not provider.load()[0].includes_transport
    assert len(provider.search(destination_id='antalya')) == 1
    assert provider.search(destination_id='istanbul') == []
    example.pop('source_url')
    path.write_text(json.dumps([example]))
    with pytest.raises(ValueError): provider.import_file(path)


def test_extended_demo_destinations_are_explicit_opt_in(monkeypatch):
    monkeypatch.delenv('TN_EXTENDED_DEMO', raising=False)
    assert len(active_cities()) == 3
    monkeypatch.setenv('TN_EXTENDED_DEMO', '1')
    assert len(active_cities()) >= 12
