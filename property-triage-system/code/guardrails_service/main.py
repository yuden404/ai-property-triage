"""Property Triage — Guardrails Service (Service 3).

POST /check/input   { "text": "<submission>" }
POST /check/output  { "text": "<generated report>", "source": "<facts (optional)>" }
                 →  { "pass": bool, "reason": "<if fail>", "safe_text": "<masked text>" }

Two layers per check:
  1. Amazon Bedrock Guardrails (ApplyGuardrail) — managed safety: hate/violence/
     sexual/insults, prompt attacks, profanity, denied topics, PII masking.
  2. A Gemini rail — the parts a denylist cannot express: input allow-list
     ("genuine property listing in he/en") and output factuality-vs-source.

Run locally (from the code/ directory):
    AWS_PROFILE=course ../.venv/bin/python -m uvicorn guardrails_service.main:app --port 8003
"""
from __future__ import annotations

import json
import os
import re

from fastapi import FastAPI
from pydantic import BaseModel

from guardrails_service.prompts import INPUT_CLASSIFIER_PROMPT, OUTPUT_AUDITOR_PROMPT
from shared.aws_utils import session
from shared.gemini_utils import generate

GUARDRAIL_ID = os.getenv("GUARDRAIL_ID", "huksxm9z68f6")
GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "DRAFT")

REJECT_MESSAGES = {  # multilingual extension: polite, localized rejection
    "other": "We're sorry — submissions are accepted in Hebrew or English only. / "
             "מצטערים — ניתן להגיש מודעות בעברית או באנגלית בלבד.",
    "not_listing": "This doesn't appear to be a property listing. Please describe the "
                   "property: type, location, size, rooms, price and key features.",
}

app = FastAPI(title="Property Triage — Guardrails Service")
_runtime = session().client("bedrock-runtime")


class CheckRequest(BaseModel):
    text: str
    source: str | None = None


def _parse_json(raw: str) -> dict:
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.M).strip()
    return json.loads(raw)


def _apply_guardrail(text: str, source: str) -> dict:
    """Run Bedrock ApplyGuardrail. Returns {blocked, reasons, masked_text}."""
    resp = _runtime.apply_guardrail(
        guardrailIdentifier=GUARDRAIL_ID,
        guardrailVersion=GUARDRAIL_VERSION,
        source=source,
        content=[{"text": {"text": text}}],
    )
    reasons, anonymized_only = [], True
    for a in resp.get("assessments", []):
        for f in a.get("contentPolicy", {}).get("filters", []):
            reasons.append(f"content:{f['type'].lower()}")
            anonymized_only = False
        for t in a.get("topicPolicy", {}).get("topics", []):
            reasons.append(f"topic:{t['name']}")
            anonymized_only = False
        for w in a.get("wordPolicy", {}).get("managedWordLists", []):
            reasons.append(f"word:{w['type'].lower()}")
            anonymized_only = False
        for p in a.get("sensitiveInformationPolicy", {}).get("piiEntities", []):
            reasons.append(f"pii:{p['type'].lower()}:{p['action'].lower()}")
            if p["action"] != "ANONYMIZED":
                anonymized_only = False
    intervened = resp.get("action") == "GUARDRAIL_INTERVENED"
    masked = ""
    if intervened and resp.get("outputs"):
        masked = resp["outputs"][0].get("text", "")
    return {
        "blocked": intervened and not anonymized_only,
        "reasons": reasons,
        "masked_text": masked if (intervened and anonymized_only) else "",
    }


@app.get("/health")
def health():
    return {"status": "ok", "guardrail_id": GUARDRAIL_ID}


@app.post("/check/input")
def check_input(req: CheckRequest):
    text = req.text.strip()
    if not text:
        return {"pass": False, "reason": "empty submission", "safe_text": ""}

    # Layer 1 — managed safety (Bedrock Guardrails)
    gr = _apply_guardrail(text, "INPUT")
    if gr["blocked"]:
        return {"pass": False, "reason": "; ".join(gr["reasons"]) or "safety policy", "safe_text": ""}

    # PII masking quirk: Bedrock only ANONYMIZEs with source=OUTPUT, so run a
    # second pass purely to harvest the masked text (and catch BLOCK-level PII).
    mask = _apply_guardrail(text, "OUTPUT")
    if mask["blocked"]:
        return {"pass": False, "reason": "; ".join(mask["reasons"]) or "safety policy", "safe_text": ""}
    gr["masked_text"] = mask["masked_text"] or gr["masked_text"]

    # Layer 2 — allow-list classifier (Gemini): genuine listing in he/en
    try:
        verdict = _parse_json(generate(INPUT_CLASSIFIER_PROMPT.format(text=text), temperature=0.0))
    except Exception as exc:  # noqa: BLE001
        return {"pass": False, "reason": f"classifier unavailable: {exc}", "safe_text": ""}

    if verdict.get("language") not in ("he", "en"):
        return {"pass": False, "reason": REJECT_MESSAGES["other"], "safe_text": ""}
    if not verdict.get("is_property_listing"):
        return {"pass": False,
                "reason": f"{REJECT_MESSAGES['not_listing']} ({verdict.get('reason', '')})",
                "safe_text": ""}

    return {"pass": True, "reason": "", "safe_text": gr["masked_text"] or text}


@app.post("/check/output")
def check_output(req: CheckRequest):
    text = req.text.strip()
    if not text:
        return {"pass": False, "reason": "empty report", "safe_text": ""}

    # Layer 1 — managed safety + PII masking on the generated report
    gr = _apply_guardrail(text, "OUTPUT")
    if gr["blocked"]:
        return {"pass": False, "reason": "; ".join(gr["reasons"]) or "safety policy", "safe_text": ""}
    safe_text = gr["masked_text"] or text

    # Layer 2 — factuality vs source (primary gate; Bedrock grounding is secondary)
    source = (req.source or "").strip()
    if source:
        try:
            verdict = _parse_json(
                generate(OUTPUT_AUDITOR_PROMPT.format(text=text, source=source), temperature=0.0)
            )
        except Exception as exc:  # noqa: BLE001
            return {"pass": False, "reason": f"auditor unavailable: {exc}", "safe_text": safe_text}
        if not verdict.get("pass", False):
            why = "; ".join(
                f"{v.get('type')}: \"{v.get('quote', '')[:80]}\"" for v in verdict.get("violations", [])
            )
            return {"pass": False, "reason": why or "factuality audit failed", "safe_text": safe_text}

    return {"pass": True, "reason": "", "safe_text": safe_text}
