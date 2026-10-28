"""Requires `uv add langgraph`; validates LangGraph routing and thread persistence."""

import pytest

pytest.importorskip("langgraph")

from datetime import date

from app.agent.graph_agent import GraphTravelAgent
from app.agent.llm_provider import ParsedTurn


class StubParser:
    def parse(self, message, current_state):
        if message == "first":
            return ParsedTurn(origin="Москва", adults=2)
        if message == "dates":
            return ParsedTurn(nights=5, departure_date=date(2026, 11, 10), intent="revise")
        if message == "change":
            return ParsedTurn(nights=6, intent="revise")
        if message == "delete":
            return ParsedTurn(clear_fields=["origin"], intent="revise")
        if message == "bad":
            return ParsedTurn(nights=5, clear_fields=["nights"])
        return ParsedTurn(intent="other")


class StubWriter:
    def generate(self, *, task, payload):
        return {
            "clarification": "Уточните данные поездки.",
            "recommendation": "Проверенные демонстрационные предложения представлены ниже.",
            "negotiation": "Требуется согласовать изменение условий.",
        }[task]


def test_thread_state_and_replanning():
    agent = GraphTravelAgent(StubParser(), StubWriter())
    a = agent.handle("a", "first")
    assert a.status == "needs_input" and a.version == 1
    b = agent.handle("a", "dates")
    assert b.status == "ready" and b.version == 2
    assert b.request.origin == "Москва"
    c = agent.handle("a", "change")
    assert c.request.nights == 6 and c.request.origin == "Москва"
    assert c.version == 3
    separate = agent.handle("b", "dates")
    assert separate.status == "needs_input" and separate.request.origin is None


def test_deletion_and_failure_preserve_state():
    agent = GraphTravelAgent(StubParser(), StubWriter())
    agent.handle("x", "first")
    reply = agent.handle("x", "bad")
    assert reply.status == "error" and reply.version == 1
    reply = agent.handle("x", "delete")
    assert reply.request.origin is None and reply.version == 2
    assert reply.status == "needs_input"
