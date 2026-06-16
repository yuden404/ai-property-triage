# AI-Powered Real Estate Property Triage System

A multi-modal AI pipeline that automates intake and triage of real-estate listings.
A listing agent submits a free-text description plus photos; the system validates it,
extracts structured fields, classifies each photo (room type + condition), retrieves
comparable listings, writes a publishable brief, re-checks it, and routes it to the
right team. There is also a conversational assistant grounded on the listing corpus.

**Author:** Yehuda Rokach · **Type:** Individual final project
**Live demo (when the instance is running):** `http://34.232.184.39:5050`
**Formal report:** [`PROJECT_BOOK.pdf`](PROJECT_BOOK.pdf) · **Demo video:** `demo/` (5–8 min walkthrough)

---

## Architecture (4 layers)

1. **Web UI** — HTML/CSS/JS + Flask, 3 tabs: Assistant (Ollama chat, KB-grounded),
   Submit Listing (upload → S3 + Image Analyser → n8n → brief), Dashboard (Chart.js).
2. **n8n orchestration** — 8-node flow: webhook → guardrails-in → IF → Information
   Extractor → AI Agent → LLM Chain (brief) → guardrails-out → router.
3. **4 FastAPI microservices** — RAG (8001), Image Analyser (8002), Guardrails (8003),
   LangGraph Agent (8000). Each has its own Dockerfile + requirements.
4. **Managed services & LLM** — Amazon **Bedrock Knowledge Bases** (RAG) + **Bedrock
   Guardrails** (the two required Bedrock services); **DynamoDB** (durable record store
   for listings + events); **S3** (photos); **Google Gemini** (all text generation +
   the served image condition score); **Ollama** Llama 3.1 (local chat).

Full diagram + intentional deviations: [`docs/architecture.md`](docs/architecture.md).

## Repository layout

```
README.md                  this file
docker-compose.yml         the whole stack on one host (services + n8n + Ollama + WebUI)
PROJECT_BOOK.pdf           formal project report
code/
  shared/                  Gemini client + AWS helpers (Secrets Manager, S3, DynamoDB)
  webui/                   Flask app (server.py, static/, templates/)
  rag_service/             Service 1 — Bedrock KB + Gemini  (+ scripts/ to build the KB)
  image_analyser/          Service 2 — PyTorch EfficientNet-B0 (train.py, label_condition.py)
  guardrails_service/      Service 3 — Bedrock Guardrails + Gemini
  agent_service/           Service 4 — LangGraph + Gemini
  n8n/n8n_flow.json        importable n8n workflow
docs/                      prompt_log.md (25%), architecture.md, model_card.md
demo/                      recorded demo video (5–8 min walkthrough)
deploy/                    ec2-userdata.sh (bootstrap)
tests/                     43-test offline pytest suite (AWS/Gemini/Ollama mocked)
```

## Prerequisites

- **Docker** + Docker Compose.
- **AWS account** with: a Bedrock **Knowledge Base** (S3 Vectors) seeded with the listings,
  a Bedrock **Guardrail**, two **DynamoDB** tables (`pt_listings`, `pt_events`), an **S3**
  bucket for photos, and the **Gemini API key** stored in **Secrets Manager**
  (`property-triage/gemini-api-key`). IDs are passed via env in `docker-compose.yml`.
- Credentials: locally via an AWS profile (e.g. `course`); on EC2 via the instance **IAM role**
  (no keys in the repo).
- Python 3.12 + the per-service `requirements.txt` (a shared `.venv` is used for dev/tests).

## Run the whole stack (local)

```bash
# from this directory; AWS creds available (e.g. export AWS_PROFILE=course)
docker compose up --build -d
# WebUI    → http://localhost:5050
# n8n      → http://localhost:5678   (import code/n8n/n8n_flow.json, attach Gemini cred)
# services → :8001 RAG · :8002 Image · :8003 Guardrails · :8000 Agent
```

On Apple Silicon, images build for `linux/amd64` to match the EC2 host.

## Service endpoints (curl)

```bash
curl -s localhost:8002/health
curl -s -X POST localhost:8001/query    -H 'Content-Type: application/json' -d '{"description":"3-room flat in Givatayim"}'
curl -s -X POST localhost:8002/analyse  -H 'Content-Type: application/json' -d '{"image_url":"https://.../kitchen.jpg"}'
curl -s -X POST localhost:8003/check/input -H 'Content-Type: application/json' -d '{"text":"buy crypto now"}'
curl -s -X POST localhost:8000/agent/run   -H 'Content-Type: application/json' -d '{"query":"what would bring this property to condition 5?"}'
```

## Train the Image Analyser (optional — `model.pth` is included)

```bash
cd code/image_analyser
../../.venv/bin/python prepare_data.py --per-class 500      # rooms (needs ~/.kaggle/kaggle.json)
AWS_PROFILE=course ../../.venv/bin/python label_condition.py --per-class 70   # condition labels via Gemini Vision
../../.venv/bin/python train.py --epochs 14                 # writes model.pth + classes.json
../../.venv/bin/python eval.py                             # reproduces 84.4% val → eval_metrics.json
```

Room-type head reaches **84.4%** val accuracy (>75%). The condition head (1–5) is trained
on Gemini-bootstrapped labels; at serving the condition score is **served by Gemini Vision**
(reliable across condition types) with the trained head as the spec deliverable + fallback.
See [`docs/model_card.md`](docs/model_card.md).

## Tests

```bash
.venv/bin/python -m pytest tests/ -q     # 43 tests, fully mocked — no creds/network
```

## Deploy to EC2

One GPU instance (`g4dn.xlarge` / Tesla T4) runs the full stack via Docker Compose.
`deploy/ec2-userdata.sh` installs Docker, clones the repo, pulls `model.pth` from S3,
and runs `docker compose up --build -d`. Management is via AWS Systems Manager (no SSH);
credentials come from the instance IAM role. Details: [`PROJECT_BOOK.pdf`](PROJECT_BOOK.pdf) §8.

## Rubric coverage

| Criterion | Where |
|---|---|
| n8n Flow (20%) | `code/n8n/n8n_flow.json` · 8 nodes, both guardrails, routing |
| EC2 Services (25%) | `code/{rag,image_analyser,guardrails,agent}_service/` · deployed |
| Image Analyser (10%) | `code/image_analyser/` · 84.4% room acc · [`model_card.md`](docs/model_card.md) |
| Guardrails (10%) | `code/guardrails_service/` · Bedrock + Gemini, fail-closed |
| Prompt Engineering Log (25%) | [`docs/prompt_log.md`](docs/prompt_log.md) · 6 surfaces, pass rates |
| WebUI + Ollama (10%) | `code/webui/` · 3 tabs, KB-grounded chat |
