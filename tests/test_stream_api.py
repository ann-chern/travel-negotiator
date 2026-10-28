import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from fastapi.testclient import TestClient
from app.web.api import app


class FakeAgent:
    def stream_updates(self, thread_id, message):
        yield ("search", "Проверяю предложения…")
        yield ("done", SimpleNamespace(status="ready", message="Готово", version=1,
            options=(), compromises=()))


def test_stream_progress_and_result():
    with patch("app.web.api.get_agent", return_value=FakeAgent()):
        result=TestClient(app).post("/api/chat/stream", json={"session_id":str(uuid4()),"message":"Сочи"})
    assert result.status_code == 200
    assert result.headers["content-type"].startswith("text/event-stream")
    assert "event: progress" in result.text
    assert "event: result" in result.text
    assert '"status": "ready"' in result.text


def test_stream_rejects_blank():
    response=TestClient(app).post("/api/chat/stream",json={"session_id":str(uuid4()),"message":"  "})
    assert response.status_code==422


def test_root_health():
    assert TestClient(app).get("/health").json()["status"]=="ok"
