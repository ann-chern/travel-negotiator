import io
import json
import logging

from app.observability import event, turn_context


def capture_audit(monkeypatch):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler._tn_audit = True
    logger = logging.getLogger("travel_negotiator.audit")
    monkeypatch.setattr(logger, "handlers", [handler])
    return stream


def test_audit_event_has_turn_correlation_and_filters_secrets(monkeypatch):
    stream = capture_audit(monkeypatch)
    with turn_context("request-1", "session-1"):
        event("test.event", data={"api_key": "must-not-leak", "value": 7})
    payload = json.loads(stream.getvalue())
    assert payload["event"] == "test.event"
    assert payload["request_id"] == "request-1"
    assert payload["session_id"] == "session-1"
    assert payload["data"] == {"value": 7}


def test_user_text_can_be_redacted_inside_nested_payload(monkeypatch):
    stream = capture_audit(monkeypatch)
    monkeypatch.setenv("TN_LOG_USER_MESSAGES", "0")
    event("test.redaction", payload={"user_message": "private text"})
    payload = json.loads(stream.getvalue())
    assert payload["payload"]["user_message"] == "<redacted>"
