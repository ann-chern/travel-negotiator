"""Validated, externally sourced *historical* offer snapshots.

An empty catalog is intentional: no observed prices are invented. Import verified
rows using the CLI. Historical prices do not imply present or future inventory.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

DEFAULT_PATH = Path(__file__).with_name('offer_snapshots.json')


class HistoricalSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_url: HttpUrl
    observed_at: datetime
    destination_id: str = Field(min_length=2)
    hotel_name: str = Field(min_length=2)
    check_in: date
    check_out: date
    price_rub: Decimal = Field(gt=0)
    adults: int = Field(ge=1)
    source_offer_id: str = Field(min_length=1)
    evidence_file: str | None = None
    includes_transport: bool = False
    includes_transfer: bool = False
    notes: str = ''

    @model_validator(mode='after')
    def valid_dates(self):
        if self.check_out <= self.check_in:
            raise ValueError('check_out must be later than check_in')
        if self.observed_at.tzinfo is None:
            raise ValueError('observed_at must have a timezone')
        if self.observed_at > datetime.now(timezone.utc):
            raise ValueError('observation cannot be in the future')
        return self


class SnapshotCatalogProvider:
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)

    def load(self) -> list[HistoricalSnapshot]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data, list):
            raise ValueError('Snapshot catalog must be a JSON array')
        return [HistoricalSnapshot.model_validate(item) for item in data]

    def search(self, *, destination_id: str | None = None,
               departure_from: date | None = None, departure_to: date | None = None,
               adults: int | None = None) -> list[HistoricalSnapshot]:
        return [x for x in self.load()
                if (destination_id is None or x.destination_id == destination_id)
                and (departure_from is None or x.check_in >= departure_from)
                and (departure_to is None or x.check_in <= departure_to)
                and (adults is None or x.adults == adults)]

    def import_file(self, input_path: Path) -> int:
        raw = json.loads(Path(input_path).read_text(encoding='utf-8'))
        if not isinstance(raw, list):
            raise ValueError('Import expects a JSON array')
        incoming = [HistoricalSnapshot.model_validate(row) for row in raw]
        merged = {(str(x.source_url), x.source_offer_id, x.observed_at.isoformat()): x for x in self.load()}
        for row in incoming:
            merged[(str(row.source_url), row.source_offer_id, row.observed_at.isoformat())] = row
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([r.model_dump(mode='json') for r in merged.values()], ensure_ascii=False, indent=2), encoding='utf-8')
        return len(incoming)
