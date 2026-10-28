from uuid import uuid4
from types import SimpleNamespace
from fastapi.testclient import TestClient
from app.web.api import app, get_agent

class FakeAgent:
    def __init__(self): self.calls=[]
    def handle(self, thread_id, message):
        self.calls.append((thread_id, message))
        return SimpleNamespace(status="ready", message="Демо варианты", version=1,
            options=({"hotel":"Сочи", "destination":"sochi", "price_rub":"40200", "departure_date":"2026-11-16",
                      "nights":5,"hotel_stars":3,"meal_plan":"none","outbound_stops":1,"inbound_stops":1},), compromises=())

def test_health_and_html():
    client=TestClient(app)
    assert client.get("/api/health").json()["mode"]=="synthetic-demo"
    assert "Travel Negotiator" in client.get("/").text
    assert client.get("/static/app.js").status_code==200

def test_chat_and_session_isolation():
    fake=FakeAgent();app.dependency_overrides.clear() if hasattr(app,'dependency_overrides') else None
    # lru_cache replacement via monkeypatch is independent from production API credentials.
    import app.web.api as api
    from unittest.mock import patch
    a,b=str(uuid4()),str(uuid4())
    with patch.object(api,'get_agent',return_value=fake):
        client=TestClient(app)
        first=client.post('/api/chat',json={'session_id':a,'message':'Привет'})
        second=client.post('/api/chat',json={'session_id':b,'message':'Здравствуйте'})
    assert first.status_code==second.status_code==200
    assert fake.calls==[(a,'Привет'),(b,'Здравствуйте')]
    assert first.json()['options'][0]['price']=='40\u00a0200 ₽'
    assert first.json()['commands'][0]['message']=='Беру первый вариант'

def test_reject_invalid_input():
    client=TestClient(app)
    assert client.post('/api/chat',json={'session_id':'broken','message':'hello'}).status_code==422
    assert client.post('/api/chat',json={'session_id':str(uuid4()),'message':'   '}).status_code==422
