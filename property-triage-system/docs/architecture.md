# Architecture

## Overview
A multi-modal real-estate listing-triage pipeline. A listing (text + images) is
validated, enriched by an agent that calls specialised services, turned into a
publishable brief, re-checked, and routed. Plus a conversational assistant over
the listings in the system.

```
                         ┌──────────────────────────────────────────────┐
   Browser  ──────────▶  │  WebUI  (Flask + HTML/CSS/JS, :5050)           │
                         │  Assistant tab · Submit tab · Dashboard tab    │
                         └───────┬───────────────────────┬────────────────┘
              chat (stream)      │                        │  POST listing
                                 ▼                        ▼
                    ┌────────────────────┐     ┌────────────────────────────┐
                    │ Ollama llama3.1     │     │  n8n orchestration (Phase 4)│
                    │ (local, chat only)  │     │  webhook → guardrails-in →  │
                    └────────────────────┘     │  extract → AI Agent → brief │
                                                │  → guardrails-out → router  │
                                                └───────┬─────────────────────┘
                                                        │ HTTP tool calls
        ┌───────────────────────────────────────────────┼───────────────────────────┐
        ▼                         ▼                       ▼                            ▼
 ┌──────────────┐        ┌──────────────┐        ┌──────────────┐           ┌──────────────────┐
 │ RAG  :8001   │        │ Guardrails   │        │ Image        │           │ LangGraph Agent  │
 │ Bedrock KB + │        │ :8003        │        │ Analyser     │           │ :8000            │
 │ Gemini       │        │ Bedrock GR + │        │ :8002        │           │ planner→tools→   │
 │ insight      │        │ Gemini rails │        │ EfficientNet │           │ synthesiser      │
 └──────┬───────┘        └──────┬───────┘        └──────────────┘           └────────┬─────────┘
        │                       │                                                     │ calls RAG + Image
        ▼                       ▼                                                     ▼
  Bedrock KB (S3 Vectors)   Bedrock Guardrail        Gemini key ← AWS Secrets Manager (all services)
```

## Services (each: FastAPI + Dockerfile + requirements.txt)

| Service | Port | Endpoint | Stack |
|---------|------|----------|-------|
| RAG | 8001 | `POST /query` → `{similar_listings, insight}` | Bedrock KB (S3 Vectors) retrieve + Gemini cited insight |
| Image Analyser | 8002 | `POST /analyse` → `{room_type, condition_score, confidence}` | EfficientNet-B0 (7-class) |
| Guardrails | 8003 | `POST /check/input` `/check/output` → `{pass, reason, safe_text}` | Bedrock ApplyGuardrail + Gemini classifier/auditor |
| LangGraph Agent | 8000 | `POST /agent/run` → `{answer, tools_used, reasoning_steps}` | LangGraph (planner→tool_executor→synthesiser), Gemini, calls RAG + Image |

## Two AWS Bedrock services (the ≥2 requirement)
1. **Bedrock Knowledge Bases** — managed RAG retrieval (Titan v2 embeddings on **S3 Vectors**).
2. **Bedrock Guardrails** — managed safety (content filters, denied topics, profanity), `ApplyGuardrail`.

All LLM *generation* is **Gemini** (`gemini-2.5-flash`); its key lives in **AWS Secrets
Manager**, fetched by each service via its AWS credentials. Keeps the Bedrock
footprint to exactly the two managed services.

## Intentional deviations from the guideline (and why)
| Guideline says | We did | Why |
|---|---|---|
| WebUI in Gradio/Streamlit | **HTML/CSS/JS + Flask** | Instructor permits; full design control, real-product feel |
| RAG uses Llama.cpp; agent LLM = GPT-4o/Gemini | **Gemini everywhere** (key in Secrets Manager) | One model, reuse existing code, no secret in repo |
| KB on OpenSearch Serverless | **S3 Vectors** | OpenSearch idles ~$5–6/day on a personal account; S3 Vectors costs cents |
| Image: 6 room classes | **7 classes (+ `not_a_room`)** | Explicit reject of non-property photos; fixes CNN overconfidence on OOD |
| Guardrails PII masking | **Removed** | No email/phone fields exist in the system → scope creep |
| Image condition score (1–5) | **Placeholder** | No condition ground truth in room datasets; second head is future work (Gemini-Vision-bootstrapped) |

## Testing
Offline `pytest` suite (43 tests) covers shared helpers, all four services, and the
WebUI with AWS/Gemini/Ollama mocked — no credentials or network. See `tests/`.
