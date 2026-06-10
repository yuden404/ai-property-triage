"""LangGraph agent tests — RAG/Image HTTP + Gemini fully mocked (no network)."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


def _fake_generate(prompt, **kw):
    # The planner asks for JSON; the synthesiser asks for prose.
    if "Return ONLY JSON" in prompt:
        use_image = "Images provided with this request: True" in prompt
        return json.dumps({"use_rag": True, "use_image": use_image, "rationale": "test plan"})
    return "Synthesised answer."


@pytest.fixture
def agent(monkeypatch):
    import agent_service.main as m
    monkeypatch.setattr(m, "generate", _fake_generate)
    return SimpleNamespace(client=TestClient(m.app), m=m)


def test_health(agent):
    assert agent.client.get("/health").json()["status"] == "ok"


def test_run_uses_rag_tool(agent, monkeypatch):
    monkeypatch.setattr(agent.m, "rag_query",
                        MagicMock(return_value={"similar_listings": [{"id": "L1", "text": "3BR Haifa"}],
                                                "insight": "comparable found"}))
    d = agent.client.post("/agent/run", json={"query": "What are similar homes worth?"}).json()
    assert d["answer"] == "Synthesised answer."
    assert "rag" in d["tools_used"]
    assert d["reasoning_steps"]  # planner + executor + synthesiser steps recorded


def test_run_with_images_calls_image_tool(agent, monkeypatch):
    monkeypatch.setattr(agent.m, "rag_query",
                        MagicMock(return_value={"similar_listings": [], "insight": ""}))
    monkeypatch.setattr(agent.m, "analyse_image",
                        MagicMock(return_value={"room_type": "kitchen", "condition_score": 4, "confidence": 0.9}))
    d = agent.client.post("/agent/run",
                          json={"query": "Which rooms need attention?", "image_urls": ["http://x/k.jpg"]}).json()
    assert "image" in d["tools_used"]


def test_image_failure_is_graceful(agent, monkeypatch):
    monkeypatch.setattr(agent.m, "rag_query",
                        MagicMock(return_value={"similar_listings": [], "insight": ""}))
    monkeypatch.setattr(agent.m, "analyse_image",
                        MagicMock(side_effect=ConnectionError("image service down")))
    d = agent.client.post("/agent/run",
                          json={"query": "Condition of the kitchen?", "image_urls": ["http://x/k.jpg"]}).json()
    assert "image" not in d["tools_used"]                       # a failed tool is not marked used
    assert any("image unavailable" in s for s in d["reasoning_steps"])
    assert d["answer"] == "Synthesised answer."                 # agent still answers


def test_no_images_skips_image_tool(agent, monkeypatch):
    # Even if the planner says use_image, no image_urls ⇒ the image tool never runs.
    monkeypatch.setattr(agent.m, "rag_query",
                        MagicMock(return_value={"similar_listings": [], "insight": ""}))
    called = MagicMock()
    monkeypatch.setattr(agent.m, "analyse_image", called)
    agent.client.post("/agent/run", json={"query": "Which rooms need work?"})
    called.assert_not_called()
