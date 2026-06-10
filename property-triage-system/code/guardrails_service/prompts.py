"""Guardrails rail prompts — **Prompt Surface #4** in docs/prompt_log.md.

Two Gemini rails complement the managed Bedrock Guardrail:
  - INPUT_CLASSIFIER_PROMPT: allow-list check ("is this a genuine property
    listing in a supported language?") — something denied-topics cannot express.
  - OUTPUT_AUDITOR_PROMPT: factuality-vs-source check on the generated report
    (invented prices, fabricated certifications, false legal claims).
"""

# Surface #4 (input rail) — V1 baseline (2026-06-10)
INPUT_CLASSIFIER_PROMPT = """\
You are the intake filter of a real-estate agency's listing system.

SUBMISSION:
---
{text}
---

Decide:
1. is_property_listing — true only if this is a genuine attempt to describe a
   real-estate property for sale or rent (apartment, house, villa, office,
   retail, industrial, land). Spam, ads for other products, questions, jokes,
   code, or off-topic text are false. Short but genuine descriptions are true.
2. language — the submission's main language: "he", "en", or "other".

Return ONLY this JSON (no markdown, no commentary):
{{"is_property_listing": true/false, "language": "he"/"en"/"other", "reason": "<one short sentence>"}}
"""

# Surface #4 (output rail) — V1 baseline (2026-06-10)
OUTPUT_AUDITOR_PROMPT = """\
You audit AI-generated real-estate reports before publication.

GENERATED REPORT:
---
{text}
---

SOURCE FACTS the report must be based on (the submission + extracted data):
---
{source}
---

Flag a violation ONLY for:
1. INVENTED_FACT — a concrete claim (price, size, rooms, location, feature,
   certification) in the report that contradicts or does not appear in the
   source facts.
2. GUARANTEE — promises of future value, returns, or "guaranteed" outcomes.
3. LEGAL_CLAIM — legal or tax assertions stated as fact (e.g. "fully permitted",
   "no liens", "tax exempt") that the source facts do not support.

Reasonable marketing phrasing, summaries and soft language are NOT violations.

Return ONLY this JSON (no markdown):
{{"pass": true/false, "violations": [{{"type": "<INVENTED_FACT|GUARANTEE|LEGAL_CLAIM>", "quote": "<offending text>", "why": "<short>"}}]}}
"""
