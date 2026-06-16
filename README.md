# AI-Powered Real Estate Property Triage System

Final project for the AI Engineering course — a multi-modal pipeline that validates,
enriches, briefs and routes real-estate listings. **Built and deployed end-to-end on
AWS** (n8n + four FastAPI microservices + two Amazon Bedrock services + Gemini + a
PyTorch image model + a Flask/Ollama WebUI).

- **Submission root & full docs:** [`property-triage-system/`](property-triage-system/) — see its [`README.md`](property-triage-system/README.md) for setup, run and screenshots.
- **Formal report:** [`PROJECT_BOOK.pdf`](PROJECT_BOOK.pdf) (built from `PROJECT_BOOK.md`).
- **Living plan & decisions:** [`PROJECT_PLAN.md`](PROJECT_PLAN.md).
- **Original brief:** `AI_Property_Triage_Project_Guideline.docx`.

## Layout
```
property-triage-system/   the system (code, docs, tests, docker-compose) — submission root
PROJECT_BOOK.md / .pdf     formal project report (PDF has embedded figures)
PROJECT_PLAN.md            decisions log + per-phase progress + rubric self-assessment
book_figures/              screenshots embedded in the PDF
build_book.py              PROJECT_BOOK.md → PROJECT_BOOK.pdf
```

## Scripts
**Build the report** — `build_book.py`: renders `PROJECT_BOOK.md` (Markdown) to
`PROJECT_BOOK.pdf` with the embedded figures.

**Image Analyser** (`property-triage-system/code/image_analyser/`):
- `prepare_data.py` — download/balance the room datasets via `kagglehub` (7 classes).
- `label_condition.py` — bootstrap 1–5 condition labels with Gemini Vision.
- `train.py` — fine-tune EfficientNet-B0 (frozen backbone, two heads); writes `model.pth`.
- `eval.py` — reproduce the held-out metrics → `eval_metrics.json`.

**Knowledge Base** (`property-triage-system/code/rag_service/scripts/`):
- `01_generate_listings.py` → `02_upload_listings.py` → `03_create_kb.py` — generate ≥20
  synthetic listings, upload to S3, and create/ingest the Bedrock Knowledge Base.

**Guardrails** (`property-triage-system/code/guardrails_service/scripts/`):
- `04_create_guardrail.py` — one-time creation of the Amazon Bedrock Guardrail.

**Deploy** (`property-triage-system/deploy/`):
- `ec2-userdata.sh` — EC2 bootstrap: install Docker, clone, pull `model.pth` from S3,
  `docker compose up`.

All AWS access uses an instance IAM role / local profile; the Gemini key lives in AWS
Secrets Manager — no keys in the repo.
