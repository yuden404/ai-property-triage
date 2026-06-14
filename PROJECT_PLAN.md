# Project Plan & Progress — AI-Powered Real Estate Property Triage System

**Owner:** Yehuda Rokach · **Type:** Individual final project · **Due:** ~2.5 weeks from 2026-06-06
**Status:** Phases 0–4 done — WebUI (Flask) + 4 services (43 pytest) + **n8n 8-node flow validated locally** (both guardrails pass, routing verified). **Next:** wire WebUI → webhook + EC2 deploy. **Last updated:** 2026-06-14

This is the **living** project doc: it tracks decisions and progress as we build. The formal report lives in [`PROJECT_BOOK.pdf`](PROJECT_BOOK.pdf); the code lives in [`property-triage-system/`](property-triage-system/).

---

## Progress tracker
- [x] **Phase 0 — Scaffold** ✓ — folders · venv + deps · living docs · Ollama (`llama3.1`)
- [x] **Phase 1 — WebUI** ✓ — rebuilt as **HTML/CSS/JS + Flask** (3 tabs: Assistant / Submit / Dashboard); streaming Ollama chat (listings-aware), drag-&-drop upload, Chart.js dashboard. Prompt Surface #5 V1→V6 (10/10).
- [x] **Phase 2 — Bedrock + microservices** ✓ — AWS profile `course` · Gemini key in Secrets Manager · **Bedrock KB** on S3 Vectors (`KB_ID=3KTFERDLUV`, 24/24) · **RAG** (:8001, Surface #3 10/10) · **Guardrails** (Bedrock Guardrail `huksxm9z68f6` + Gemini rails, :8003, Surface #4; **PII masking removed** — no email/phone fields) · **LangGraph Agent** (:8000, planner→tool_executor→synthesiser, Gemini, Surface #2 tool descriptions). All 4 services have Dockerfiles. **pytest suite: 43 tests, mocked/offline.** **2 code-review rounds applied** (XSS, error handling, fail-closed guardrail, cache safety, …).
- [x] **Phase 3 — Image Analyser** ✓ — EfficientNet-B0 fine-tune (`train.py`); data via `prepare_data.py` (kagglehub, 500/class, 7 classes). **Rooms-only argmax 84.6% (>75% ✓)**; 7-class val 84.4%. Added a **`not_a_room` reject class** for OOD robustness. Service :8002 loads `model.pth`. See [`docs/model_card.md`](property-triage-system/docs/model_card.md). ⬜ condition-score head (placeholder for now).
- [x] **Phase 4 — n8n flow** ✓ — 8-node flow built & validated locally (Webhook → Guardrails-In → IF → Information Extractor → AI Agent + 3 tools → LLM Chain → Guardrails-Out → Router → Respond). **Both guardrails pass; routing residential/commercial verified; spam rejected.** Captures Surfaces #1 (Extractor) + #2 (Agent + brief V1→V4). Exported to `code/n8n/n8n_flow.json`. **WebUI wired to the webhook — verified end-to-end** (browser → WebUI → n8n → 4 services → brief, both guardrails pass). ⬜ EC2 deploy
- [~] **Phase 5 — polish** — output-guardrail **human-review branch ✓** (output fail → held, not published); `architecture.md` ✓; remaining: full prompt-log 10-case runs
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
| 2026-06-10 | **Pushed to GitHub: `yuden404/ai-property-triage`** (public, personal account) | Remote URL pins the `yuden404` user; work gh account stays the active one |
| 2026-06-10 | **AWS = Yehuda's personal account `928974129332`**, IAM user `course-user`, profile `course`, `us-east-1` | There is no separate course account; costs are on Yehuda → keep them near zero |
| 2026-06-10 | **KB vector store = S3 Vectors** (not OpenSearch Serverless) | OpenSearch idles at ~$5–6/day on a personal account; S3 Vectors costs cents and is available in the account (verified via boto3) |
| 2026-06-11 | **Removed PII masking from Guardrails** | No email/phone fields exist in the system → scope creep; also let `_apply_guardrail` fail **closed** on any intervention |
| 2026-06-11 | **Image: added a `not_a_room` reject class** (7th, via `prasunroy/natural-images`) | Forces an explicit "not a property photo" output instead of overconfident room guesses; fixed OOD (dog/cat/doc → `not_a_room`) |
| 2026-06-11 | **Offline pytest suite (43 tests, all mocked)** | Re-runnable verification of every service + WebUI; needs no creds/network |
| 2026-06-11 | **Image data via `kagglehub` + `prepare_data.py`** (500/class) | One-time local download; combines robinreni (rooms) + mikhailma street_data (exterior) + natural-images (not_a_room); Kaggle key stays local (not in AWS — nothing on EC2 reads it) |

## Rubric self-assessment (filled at the final code review)
| Criterion | Weight | Target | Actual (in progress) |
|---|---|---|---|
| n8n Flow | 20% | Excellent | 8-node flow built + validated locally (both guardrails, routing, spam) ✓ |
| EC2 Services | 25% | Excellent | 3/4 built + tested (RAG, Guardrails, Agent); Image trained · **EC2 deploy pending** |
| Image Analyser | 10% | Excellent (>75%) | **84.6%** rooms-only on fresh images ✓ |
| Guardrails | 10% | Excellent (<5% FP) | Built (Surface #4 was 11/11); fail-closed |
| Prompt Engineering Log | 25% | Excellent (5×5, pass rates) | Surfaces #3/#4/#5 done; #1/#2 in Phase 4 |
| WebUI + Ollama | 10% | Excellent | Built (Flask, 3 tabs) ✓ |

---

## Context
Individual final project for the AI Engineering course. The guideline (`AI_Property_Triage_Project_Guideline.docx`) describes a team build of a multi-modal real-estate listing pipeline; we adapt it for solo work under two instructor constraints: **(1)** integrate **≥2 AWS Bedrock services**, **(2)** the project is sized as a few hours of *assembly* — favor managed services + reuse, while covering the **full spec at the highest quality**.

We reuse two things the student already built: `python_course/rag_app_aws/` (Bedrock KB + S3 + EC2 RAG app) and `document_analyst/` (n8n + Gemini + Gemini Vision, with a 5-iteration `prompt_log.md`).

## AWS Bedrock services used (exactly 2)
1. **Amazon Bedrock Knowledge Bases** — RAG for Service 1 (`Retrieve` + `StartIngestionJob`; Titan V2 embeddings on **S3 Vectors**, managed).
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
1. n8n Information Extractor systemPromptTemplate · 2. n8n AI Agent prompt + tool descriptions · 3. RAG insight/citation prompt (Gemini) · 4. Guardrails rail prompts (Gemini) · 5. Ollama real-estate system prompt. Each: ≥5 versions + ≥10 test cases + pass-rate. **Status:** #3 RAG 10/10 · #4 Guardrails 11/11 · #5 Ollama V1→V6 10/10 · #1 Extractor + #2 Agent/brief V1→V4 captured live (full 10-case runs being rounded out). (#6 LangGraph tool descriptions also logged.)

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
