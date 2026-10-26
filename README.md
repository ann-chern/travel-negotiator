# Travel Negotiator — Step 1: user intent schemas

This is the first development increment, **not yet a runnable hackathon app**.

## Requirements

- Python 3.11+
- `pydantic==2.13.5`
- `pytest==9.0.2` (testing only)

## Run tests

From the `travel-negotiator` directory:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

## Design decisions

- `TripRequest` stores the user's *current* intent, not catalog offers.
- `Constraint` is limited to an allowlist of fields/operators. Unsupported requests remain in `unresolved`.
- `RequestPatch` has explicit `set`/`remove` operations. Omitted fields are kept. `set` may contain `null` only where the request schema permits it.
- Patch validation is atomic; an invalid change never mutates the original request.
- This MVP patch replaces whole nested objects or lists. Element-level constraint edits will be designed when building the replanner.
- `Budget.scope=None`, `Travelers.adults=None` explicitly mean unknown.
- These schemas do not yet normalize relative dates, infer traveler intent, or check travel inventory. Those belong in subsequent layers.

## Next increments

Catalog models and seeded offers; constraint evaluation and candidate generation; agent integration; replanning/cart; UI and Docker Compose packaging.
