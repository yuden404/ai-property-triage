# Project Plan & Progress — AI-Powered Real Estate Property Triage System

**Owner:** Yehuda Rokach · **Type:** Individual final project · **Due:** ~2.5 weeks from 2026-06-06
**Status:** WebUI being **rebuilt** (Streamlit → HTML/CSS/JS + Flask, instructor permits) · then Phase 2 · **Last updated:** 2026-06-09

This is the **living** project doc: it tracks decisions and progress as we build. The formal report lives in [`PROJECT_BOOK.pdf`](PROJECT_BOOK.pdf); the code lives in [`property-triage-system/`](property-triage-system/).

---

## Progress tracker
- [x] **Phase 0 — Scaffold**: folder structure ✓ · venv + deps ✓ · living docs ✓ (`PROJECT_PLAN.md`, `PROJECT_BOOK.md`+PDF, `prompt_log.md`) · Ollama ✓ (`llama3.1`)
- [x] **Phase 1 — Full UI layer** ✓ — 3-tab Streamlit, validated (AppTest, no exceptions): **Assistant** (Ollama `llama3.1`) answers about the entered listings + general help; **Submit** with drag-&-drop image upload (MOCK mode); **Dashboard** with charts + rich table. Prompt Surface #5 iterated to **V6** (10/10); themed UI, Deploy button hidden. → **now being rebuilt as HTML/CSS/JS + Flask** (instructor permits; same features, all Python logic reused).
- [ ] **Phase 2 — Bedrock setup + microservices** (RAG, Guardrails, LangGraph, Image stub)
- [ ] **Phase 3 — Image Analyser training** ⟨after the class lesson, ~2026-06-09⟩
- [ ] **Phase 4 — n8n flow** (8 nodes) + wire the WebUI to the real webhook
- [ ] **Phase 5 — Excellent polish** (prompt logs to V5, output-guardrail review path, docs)
- [ ] **Phase 6 — Demo video + ZIP package** + finalize `PROJECT_BOOK.pdf`

## Decisions log
| Date | Decision | Rationale |
|---|---|---|
| 2026-06-06 | **Gemini is the LLM everywhere** (n8n + all microservices), not Bedrock | Reuse the student's existing Gemini code; one model across the project |
| 2026-06-06 | **Bedrock = Knowledge Bases + Guardrails (exactly 2 services)** | Satisfies the instructor's "≥2 Bedrock services"; both are managed → low solo effort; reuse `rag_app_aws` |
| 2026-06-06 | **Gemini API key in AWS Secrets Manager** | No secret in repo/env; services pull it via the same AWS auth they already use |
| 2026-06-06 | **Image Analyser = PyTorch transfer learning** (EfficientNet-B0) | Real trained model for the 10% rubric; training deferred to a middle phase (not taught yet) |
| 2026-06-06 | **Build the UI layer first** | Student's request; gives a tangible, demoable artifact early |
| 2026-06-06 | **Ollama model = `llama3.1:latest`** (spec's suggested model) | Trialed `aya-expanse:8b` (better Hebrew) but it ignored language directives on refusals → reverted; reply language enforced in code (`build_messages`) |
| 2026-06-06 | **Extensions: managed vector store + multilingual guardrail + monitoring dashboard** | First two are near-free given our architecture; dashboard fits the UI work. Skipping the heavy feedback-loop. |
| 2026-06-06 | **Project book = English, PDF from day one** | Standalone formal report; `.md` source → `PROJECT_BOOK.pdf` re-exported each phase |
| 2026-06-09 | **Chat answers about entered listings** (per instructor) | Submitted listings persisted (`listings.jsonl`) + injected as context into the Ollama chat; becomes the Bedrock RAG/KB source in Phase 2 |
| 2026-06-09 | **WebUI = custom HTML/CSS/JS + Flask** (instructor permits; was Streamlit) | Full design control + real-product feel; the Flask backend reuses all the existing Python logic (Ollama streaming, listings store, mock, dashboard, prompt) |
| 2026-06-10 | **Git identity for this repo = `yehuda.rokach@gmail.com`** (local config) | Personal course project — do NOT commit with the work (Cloudinary) account; repo-local `git config`, global work config untouched |

## Rubric self-assessment (filled at the final code review)
| Criterion | Weight | Target | Actual (final) |
|---|---|---|---|
| n8n Flow | 20% | Excellent | — |
| EC2 Services | 25% | Excellent | — |
| Image Analyser | 10% | Excellent (>75%) | — |
| Guardrails | 10% | Excellent (<5% FP) | — |
| Prompt Engineering Log | 25% | Excellent (5×5, pass rates) | — |
| WebUI + Ollama | 10% | Excellent | — |

---

## Context
Individual final project for the AI Engineering course. The guideline (`AI_Property_Triage_Project_Guideline.docx`) describes a team build of a multi-modal real-estate listing pipeline; we adapt it for solo work under two instructor constraints: **(1)** integrate **≥2 AWS Bedrock services**, **(2)** the project is sized as a few hours of *assembly* — favor managed services + reuse, while covering the **full spec at the highest quality**.

We reuse two things the student already built: `python_course/rag_app_aws/` (Bedrock KB + S3 + EC2 RAG app) and `document_analyst/` (n8n + Gemini + Gemini Vision, with a 5-iteration `prompt_log.md`).

## AWS Bedrock services used (exactly 2)
1. **Amazon Bedrock Knowledge Bases** — RAG for Service 1 (`Retrieve` + `StartIngestionJob`; Titan V2 embeddings + OpenSearch Serverless, managed).
2. **Amazon Bedrock Guardrails** — safety for Service 3 (`CreateGuardrail` + `ApplyGuardrail`).

> LLM generation is **Gemini** (key in AWS Secrets Manager), not Bedrock. This keeps the Bedrock footprint to the two managed services the requirement asks for.

## Architecture (4 layers)
1. **WebUI (HTML/CSS/JS + Flask, local)** — Tab 1: Ollama (`llama3.1`) real-estate assistant that **also answers questions about the listings entered into the system** (per instructor) — relevant listings are retrieved (local store now → **RAG service / Bedrock KB** in Phase 2) and injected as grounding context; it cites the listing and never invents beyond the data. Tab 2: listing submission form → n8n webhook → renders the brief. Tab 3: monitoring dashboard (extension).
2. **n8n orchestration** — 8 nodes: webhook → guardrails-input → IF → Information Extractor (Gemini) → AI Agent (Gemini, calls the 3 service tools) → LLM Chain (brief) → guardrails-output → Router.
3. **4 FastAPI microservices** — RAG (8001), Image Analyser (8002), Guardrails (8003), LangGraph Agent (8000). Each its own dir + Dockerfile + requirements.txt.
4. **External LLM + managed services** — Gemini (all generation), Bedrock KB + Guardrails, Ollama `llama3.1` (local, chat only).

## Service contracts (frozen — do not change once wired)
- **RAG** `POST /query` `{description}` → `{similar_listings[], insight}`
- **Image** `POST /analyse` `{image_url}` → `{room_type, condition_score, confidence}`
- **Guardrails** `POST /check/input` & `POST /check/output` `{text, source?}` → `{pass, reason, safe_text}`
- **Agent** `POST /agent/run` `{query}` → `{answer, tools_used[], reasoning_steps[]}`

## Prompt Engineering Log surfaces (25% — captured as we build)
1. n8n Information Extractor systemPromptTemplate · 2. n8n AI Agent prompt + tool descriptions · 3. RAG insight/citation prompt (Gemini) · 4. Guardrails rail prompts (Gemini) · 5. Ollama real-estate system prompt. Each: ≥5 versions + ≥10 test cases + pass-rate. **Surface #5 done — V1→V6, 10/10**; the rest captured during Phases 2–4.

## Committed extensions (extra credit — after MVP)
1. **Managed vector store** — already satisfied by Bedrock KB; add a precision@3 benchmark + write-up.
2. **Multilingual guardrail** — reject non-Hebrew/English input with a localized message.
3. **Monitoring dashboard** — WebUI Tab 3, wired to the event log.
*(Not doing: feedback loop / active learning.)*

## Risks
1. Image accuracy — train in the middle phase; stub unblocks the pipeline; ResNet-50 fallback.
2. Bedrock output-grounding can no-op (boto3 #4292) — Gemini-vs-source is the primary output check.
3. n8n AI Agent tool descriptions drive routing — iterate with the 10 benchmark queries.
4. Docker on Apple Silicon — build `--platform linux/amd64`.
5. Prompt Log must be captured live, not fabricated at the end.
6. Secrets — Gemini key only in AWS Secrets Manager; rotate the leaked OpenAI key in `langraph_example.py`.

> Full design detail (per-component implementation notes, build sequence, verification) is in the approved plan at `~/.claude/plans/smooth-doodling-riddle.md` and is mirrored into `PROJECT_BOOK.md`.
