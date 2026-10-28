"""Decode MCP call_tool results while respecting both structured and text payloads."""

from __future__ import annotations

import json
from typing import Any


def decode_tool_result(result: Any) -> dict:
    """Return tool JSON payload, fail clearly for errors/empty/unexpected replies."""
    if getattr(result, "is_error", False):
        raise RuntimeError(f"MCP tool returned an error: {getattr(result, 'content', None)!r}")

    structured = getattr(result, "structured_content", None)
    if structured is not None:
        if not isinstance(structured, dict):
            raise ValueError(f"Unexpected structured MCP payload: {type(structured).__name__}")
        # Some transports wrap the tool's result under 'result'.
        if "status" in structured:
            return structured
        if isinstance(structured.get("result"), dict):
            return structured["result"]
        raise ValueError(f"MCP structured payload has no status: {structured!r}")

    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            if "status" in obj:
                return obj
            if isinstance(obj.get("result"), dict) and "status" in obj["result"]:
                return obj["result"]
    raise ValueError(
        f"MCP call had no decodable JSON result; content={getattr(result, 'content', None)!r}"
    )
