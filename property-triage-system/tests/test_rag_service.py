"""RAG service tests — Bedrock retrieve + Gemini insight mocked (no network)."""
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def rag(monkeypatch):
    """Import rag_service.main with a fake Bedrock client and a stubbed insight."""
    import shared.aws_utils as aws
    fake_runtime = MagicMock(name="bedrock-agent-runtime")
    monkeypatch.setattr(aws, "client", lambda service: fake_runtime)
    sys.modules.pop("rag_service.main", None)  # fresh import → module picks up the fake
    import rag_service.main as m
    monkeypatch.setattr(m, "generate", MagicMock(return_value="MOCK INSIGHT (per L001)."))
    return SimpleNamespace(client=TestClient(m.app), runtime=fake_runtime, module=m)


def _hit(text, score):
    return {"content": {"text": text}, "score": score}


def test_health(rag):
    r = rag.client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_query_happy_path(rag):
    rag.runtime.retrieve.return_value = {"retrievalResults": [
        _hit("Listing L014: Modern unit\nType: industrial", 0.78123),
        _hit("Listing L007: Warehouse\nType: warehouse", 0.69),
    ]}
    r = rag.client.post("/query", json={"description": "industrial unit"})
    assert r.status_code == 200
    d = r.json()
    assert [x["id"] for x in d["similar_listings"]] == ["L014", "L007"]
    assert d["similar_listings"][0]["score"] == 0.781  # rounded to 3dp
    assert d["insight"] == "MOCK INSIGHT (per L001)."
    assert rag.runtime.retrieve.call_args.kwargs["retrievalQuery"] == {"text": "industrial unit"}


def test_query_empty_description_400(rag):
    assert rag.client.post("/query", json={"description": "   "}).status_code == 400


def test_query_no_results_skips_llm(rag):
    rag.runtime.retrieve.return_value = {"retrievalResults": []}
    r = rag.client.post("/query", json={"description": "x"})
    assert r.status_code == 200
    d = r.json()
    assert d["similar_listings"] == []
    assert "No comparable" in d["insight"]
    rag.module.generate.assert_not_called()  # no LLM spend when nothing was retrieved


def test_query_retrieve_failure_502(rag):
    rag.runtime.retrieve.side_effect = RuntimeError("kb down")
    assert rag.client.post("/query", json={"description": "x"}).status_code == 502


def test_listing_id_extraction(rag):
    assert rag.module._listing_id("Listing L014: foo") == "L014"
    assert rag.module._listing_id("no id here") == "unknown"
