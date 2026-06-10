"""Guardrails service tests — Bedrock ApplyGuardrail + Gemini rails mocked."""
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def guard(monkeypatch):
    import shared.aws_utils as aws
    fake_runtime = MagicMock(name="bedrock-runtime")
    monkeypatch.setattr(aws, "client", lambda service: fake_runtime)
    sys.modules.pop("guardrails_service.main", None)
    import guardrails_service.main as m
    return SimpleNamespace(client=TestClient(m.app), runtime=fake_runtime, module=m)


def _ok_apply(*a, **k):
    return {"blocked": False, "reasons": [], "masked_text": ""}


def _classifier(verdict):
    return MagicMock(return_value=json.dumps(verdict))


# --- pure helpers --------------------------------------------------------- #
def test_parse_json_strips_fences(guard):
    assert guard.module._parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert guard.module._parse_json('{"b": 2}') == {"b": 2}


def test_pii_hint_matches_contacts_not_prices(guard):
    H = guard.module._PII_HINT
    assert H.search("call 054-1234567")                # phone
    assert H.search("write to a.b@example.com")        # email
    assert not H.search("asking 2.1M NIS")             # price — too short
    assert not H.search("4 rooms, 95 sqm, 3rd floor")  # no contact info


# --- /check/input --------------------------------------------------------- #
def test_input_valid_listing_passes_and_skips_pii_pass(guard, monkeypatch):
    apply_mock = MagicMock(side_effect=_ok_apply)
    monkeypatch.setattr(guard.module, "_apply_guardrail", apply_mock)
    monkeypatch.setattr(guard.module, "generate",
                        _classifier({"language": "en", "is_property_listing": True}))
    r = guard.client.post("/check/input", json={"text": "4-room apartment in Haifa, 95 sqm, balcony"})
    assert r.status_code == 200 and r.json()["pass"] is True
    assert apply_mock.call_count == 1  # M5: no PII pattern → second Bedrock pass skipped


def test_input_with_phone_runs_second_pii_pass(guard, monkeypatch):
    apply_mock = MagicMock(side_effect=_ok_apply)
    monkeypatch.setattr(guard.module, "_apply_guardrail", apply_mock)
    monkeypatch.setattr(guard.module, "generate",
                        _classifier({"language": "en", "is_property_listing": True}))
    r = guard.client.post("/check/input", json={"text": "Apartment in Haifa. Call 054-1234567"})
    assert r.status_code == 200
    assert apply_mock.call_count == 2  # PII pattern → INPUT + OUTPUT(mask) passes


def test_input_blocked_by_safety(guard, monkeypatch):
    monkeypatch.setattr(guard.module, "_apply_guardrail",
                        MagicMock(return_value={"blocked": True, "reasons": ["topic:crypto"], "masked_text": ""}))
    r = guard.client.post("/check/input", json={"text": "buy crypto now"})
    assert r.json()["pass"] is False
    assert "crypto" in r.json()["reason"]


def test_input_wrong_language_rejected(guard, monkeypatch):
    monkeypatch.setattr(guard.module, "_apply_guardrail", MagicMock(side_effect=_ok_apply))
    monkeypatch.setattr(guard.module, "generate",
                        _classifier({"language": "fr", "is_property_listing": True}))
    r = guard.client.post("/check/input", json={"text": "appartement a Paris"})
    assert r.json()["pass"] is False
    assert r.json()["reason"] == guard.module.REJECT_MESSAGES["other"]


def test_input_not_a_listing_rejected(guard, monkeypatch):
    monkeypatch.setattr(guard.module, "_apply_guardrail", MagicMock(side_effect=_ok_apply))
    monkeypatch.setattr(guard.module, "generate",
                        _classifier({"language": "en", "is_property_listing": False, "reason": "recipe"}))
    assert guard.client.post("/check/input", json={"text": "how to bake bread"}).json()["pass"] is False


def test_input_empty_rejected(guard):
    assert guard.client.post("/check/input", json={"text": "   "}).json()["pass"] is False


# --- /check/output -------------------------------------------------------- #
def test_output_clean_passes(guard, monkeypatch):
    monkeypatch.setattr(guard.module, "_apply_guardrail",
                        MagicMock(return_value={"blocked": False, "reasons": [], "masked_text": ""}))
    r = guard.client.post("/check/output", json={"text": "A bright 3-room flat."})
    assert r.json()["pass"] is True
    assert r.json()["safe_text"] == "A bright 3-room flat."


def test_output_returns_masked_text(guard, monkeypatch):
    monkeypatch.setattr(guard.module, "_apply_guardrail",
                        MagicMock(return_value={"blocked": False, "reasons": ["pii:email:anonymized"],
                                                "masked_text": "mail {EMAIL}"}))
    assert guard.client.post("/check/output", json={"text": "mail a@b.com"}).json()["safe_text"] == "mail {EMAIL}"


def test_output_factuality_failure(guard, monkeypatch):
    monkeypatch.setattr(guard.module, "_apply_guardrail",
                        MagicMock(return_value={"blocked": False, "reasons": [], "masked_text": ""}))
    monkeypatch.setattr(guard.module, "generate", MagicMock(return_value=json.dumps(
        {"pass": False, "violations": [{"type": "invented_price", "quote": "5M NIS"}]})))
    r = guard.client.post("/check/output", json={"text": "Priced at 5M NIS", "source": "no price given"})
    assert r.json()["pass"] is False
    assert "invented_price" in r.json()["reason"]


# --- _apply_guardrail parsing (drive the raw boto response) --------------- #
def test_apply_guardrail_parses_topic_block(guard):
    guard.runtime.apply_guardrail.return_value = {
        "action": "GUARDRAIL_INTERVENED",
        "assessments": [{"topicPolicy": {"topics": [{"name": "crypto"}]}}],
        "outputs": [],
    }
    out = guard.module._apply_guardrail("buy crypto", "INPUT")
    assert out["blocked"] is True
    assert "topic:crypto" in out["reasons"]


def test_apply_guardrail_anonymize_is_not_a_block(guard):
    guard.runtime.apply_guardrail.return_value = {
        "action": "GUARDRAIL_INTERVENED",
        "assessments": [{"sensitiveInformationPolicy": {"piiEntities": [
            {"type": "EMAIL", "action": "ANONYMIZED"}]}}],
        "outputs": [{"text": "mail {EMAIL}"}],
    }
    out = guard.module._apply_guardrail("mail a@b.com", "OUTPUT")
    assert out["blocked"] is False  # anonymize-only ⇒ pass + masked text, not a block
    assert out["masked_text"] == "mail {EMAIL}"
