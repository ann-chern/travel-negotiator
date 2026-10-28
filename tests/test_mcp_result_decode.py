from types import SimpleNamespace

import pytest

from app.mcp_catalog.result_decode import decode_tool_result


def item(*, structured=None, content=None, is_error=False):
    return SimpleNamespace(structured_content=structured, content=content or [], is_error=is_error)


def test_structured_content():
    assert decode_tool_result(item(structured={"status": "ok", "offers": []})) == {
        "status": "ok",
        "offers": [],
    }


def test_structured_wrapped_result():
    assert decode_tool_result(item(structured={"result": {"status": "ok"}})) == {"status": "ok"}


def test_text_content_fallback():
    blocks = [SimpleNamespace(type="text", text='{"status":"ok","offers":[{"offer_id":"1"}]}')]
    assert decode_tool_result(item(content=blocks))["offers"][0]["offer_id"] == "1"


def test_mcp_error_fails():
    with pytest.raises(RuntimeError, match="MCP tool returned an error"):
        decode_tool_result(item(is_error=True))


def test_missing_payload_fails_instead_of_printing_null():
    with pytest.raises(ValueError, match="no decodable JSON"):
        decode_tool_result(item())
