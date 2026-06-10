# WebUI — Flask + HTML/CSS/JS

The user-facing layer of the Property Triage System. A small **Flask** backend (`server.py`) serves a hand-written **HTML/CSS/JS** frontend and proxies the logic. Three tabs:

1. **Assistant** — chat with a local **Ollama** model (`llama3.1`), grounded as a real-estate assistant that also **answers questions about the listings entered into the system** (retrieved + injected as context; Phase 2 → Bedrock KB).
2. **Submit Listing** — description + drag-&-drop image upload + agent name → posts to the **n8n** webhook, or returns a sample brief in **MOCK mode**.
3. **Dashboard** — live processing stats + **Chart.js** charts from a local event log.

## Run

```bash
# from the repo root (property-triage-system/)
.venv/bin/python code/webui/server.py
```

Opens at **http://localhost:5050**.

Prerequisites:
- **Ollama** running locally with a model pulled: `ollama pull llama3.1`.
- Dependencies: `pip install -r code/webui/requirements.txt` (flask, requests, markdown — already in the project `.venv`).

## Backend endpoints (`server.py`)
- `GET /` — the page.
- `POST /api/chat` — `{history}` → streams the Ollama reply (system prompt + language directive + listings context).
- `POST /api/submit` — `{description, images, agent_name}` → mock/n8n brief; saves the listing; logs an event.
- `GET /api/dashboard` — metrics + chart series + recent events.

## Config (env, optional)
- `OLLAMA_URL` (default `http://localhost:11434`), `OLLAMA_MODEL` (default `llama3.1:latest`).
- `N8N_WEBHOOK_URL` — leave empty for **MOCK mode**; set to go live (Phase 4).

## Notes
- The Ollama system prompt is **Prompt Surface #5** (`system_prompts.py` → `docs/prompt_log.md`).
- `mock_brief.json` = sample brief (MOCK mode); `sample_events.jsonl` / `sample_listings.jsonl` seed the dashboard + chat before real data exists.
- Real submissions append to `events.jsonl` + `listings.jsonl` (git-ignored).
