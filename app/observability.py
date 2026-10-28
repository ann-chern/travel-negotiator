"""Small structured audit log for one agent turn.

Events are JSON so ``docker compose logs -f app`` can be filtered by event,
request_id or session_id.  We log validated inputs/outputs and decisions, never
credentials or hidden model reasoning.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import os
import sys
from typing import Any, Iterator


_request_id: ContextVar[str] = ContextVar("tn_request_id", default="-")
_session_id: ContextVar[str] = ContextVar("tn_session_id", default="-")
_AUDIT_LOGGER = "travel_negotiator.audit"


def _logger() -> logging.Logger:
    """Use a dedicated stdout handler; Uvicorn does not configure root INFO logs."""
    logger = logging.getLogger(_AUDIT_LOGGER)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(getattr(handler, "_tn_audit", False) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler._tn_audit = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    return logger


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            name = str(key)
            if name.lower() in {"api_key", "authorization", "token", "secret"}:
                continue
            result[name] = (user_message_value(str(item)) if name == "user_message"
                            else _json_value(item))
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def user_message_value(message: str) -> str:
    """Allow production deployments to keep the flow while hiding user text."""
    enabled = os.getenv("TN_LOG_USER_MESSAGES", "1").strip().lower() in {"1", "true", "yes", "on"}
    return message if enabled else "<redacted>"


def event(name: str, *, level: int = logging.INFO, **fields: Any) -> None:
    payload = {
        "event": name,
        "request_id": _request_id.get(),
        "session_id": _session_id.get(),
        **{key: _json_value(value) for key, value in fields.items()},
    }
    _logger().log(level, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


@contextmanager
def turn_context(request_id: str, session_id: str) -> Iterator[None]:
    request_token = _request_id.set(request_id)
    session_token = _session_id.set(session_id)
    try:
        yield
    finally:
        # StreamingResponse may resume a sync generator in a copied worker
        # context. Tokens cannot be reset from a different Context; that copied
        # context is discarded by Starlette after iteration anyway.
        try:
            _session_id.reset(session_token)
            _request_id.reset(request_token)
        except ValueError:
            pass
