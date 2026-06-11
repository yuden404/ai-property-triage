"""Image Analyser tests — the confidence-threshold logic + the stub contract.
Real EfficientNet inference is covered by the live smoke test with model.pth.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def img():
    import image_analyser.main as m
    return SimpleNamespace(client=TestClient(m.app), m=m)


def test_pick_label_confident(img):
    label, conf = img.m.pick_label([0.1, 0.85, 0.05], ["a", "b", "c"], 0.55)
    assert label == "b" and conf == 0.85


def test_pick_label_uncertain_below_threshold(img):
    label, conf = img.m.pick_label([0.4, 0.35, 0.25], ["a", "b", "c"], 0.55)
    assert label == "uncertain" and conf == 0.4


def test_health_reports_model_flag(img):
    d = img.client.get("/health").json()
    assert d["status"] == "ok"
    assert isinstance(d["model_loaded"], bool)


def test_analyse_stub_returns_schema(img, monkeypatch):
    # Force stub mode (no model) and assert the response still matches the schema.
    monkeypatch.setattr(img.m, "_load_model", lambda: (None, []))
    d = img.client.post("/analyse", json={"image_url": "http://x/y.jpg"}).json()
    assert {"room_type", "condition_score", "confidence"} <= d.keys()
    assert d["room_type"] == "uncertain"
