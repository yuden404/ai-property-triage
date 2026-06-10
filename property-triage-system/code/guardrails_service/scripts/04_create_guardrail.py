"""Create the Bedrock Guardrail used by the Guardrails service (one-time, idempotent).

Policies:
  - Content filters: HATE / INSULTS / SEXUAL / VIOLENCE / MISCONDUCT / PROMPT_ATTACK
  - PII: email+phone → ANONYMIZE (masked, still passes), credit card → BLOCK
  - Profanity word list
  - Denied topics: crypto/get-rich schemes (spam in a listing pipeline)
  - Contextual grounding (secondary signal; the Gemini auditor is the primary gate)

Run:  AWS_PROFILE=course .venv/bin/python code/guardrails_service/scripts/04_create_guardrail.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[2]))

from shared.aws_utils import session  # noqa: E402

NAME = "property-triage-guardrail"

bedrock = session().client("bedrock")


def main() -> int:
    for g in bedrock.list_guardrails()["guardrails"]:
        if g["name"] == NAME:
            print(f"guardrail exists: GUARDRAIL_ID={g['id']} (version {g['version']})")
            return 0

    resp = bedrock.create_guardrail(
        name=NAME,
        description="Input/output safety for the Property Triage pipeline",
        contentPolicyConfig={
            "filtersConfig": [
                {"type": "HATE", "inputStrength": "HIGH", "outputStrength": "HIGH"},
                {"type": "INSULTS", "inputStrength": "HIGH", "outputStrength": "HIGH"},
                {"type": "SEXUAL", "inputStrength": "HIGH", "outputStrength": "HIGH"},
                {"type": "VIOLENCE", "inputStrength": "HIGH", "outputStrength": "HIGH"},
                {"type": "MISCONDUCT", "inputStrength": "MEDIUM", "outputStrength": "MEDIUM"},
                {"type": "PROMPT_ATTACK", "inputStrength": "HIGH", "outputStrength": "NONE"},
            ]
        },
        wordPolicyConfig={"managedWordListsConfig": [{"type": "PROFANITY"}]},
        sensitiveInformationPolicyConfig={
            "piiEntitiesConfig": [
                {"type": "EMAIL", "action": "ANONYMIZE"},
                {"type": "PHONE", "action": "ANONYMIZE"},
                {"type": "CREDIT_DEBIT_CARD_NUMBER", "action": "BLOCK"},
            ]
        },
        topicPolicyConfig={
            "topicsConfig": [
                {
                    "name": "crypto-investment-schemes",
                    "definition": "Cryptocurrency investments, tokens, get-rich-quick or "
                                  "guaranteed-return investment schemes unrelated to a property listing.",
                    "examples": ["Invest in my new coin for guaranteed 10x returns"],
                    "type": "DENY",
                },
            ]
        },
        contextualGroundingPolicyConfig={
            "filtersConfig": [
                {"type": "GROUNDING", "threshold": 0.5},
                {"type": "RELEVANCE", "threshold": 0.5},
            ]
        },
        blockedInputMessaging="This submission was rejected by the agency's safety policy.",
        blockedOutputsMessaging="This generated content was withheld for review by the agency's safety policy.",
    )
    print(f"guardrail created: GUARDRAIL_ID={resp['guardrailId']} (version {resp['version']})")
    print("→ set GUARDRAIL_ID in code/guardrails_service/.env.example")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
