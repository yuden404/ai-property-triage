"""Property Triage System — Web UI backend (Flask).

Serves the HTML/CSS/JS frontend and four JSON/stream endpoints. All the real
logic (Ollama, prompt building, listings store, mock/n8n, dashboard stats) is
plain Python — the same logic the Streamlit version used, now behind Flask.

Run:  .venv/bin/python code/webui/server.py   →  http://localhost:5050
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import markdown as md
import requests
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from system_prompts import REALESTATE_SYSTEM_PROMPT

# --------------------------------------------------------------------------- #
# Config (env with safe defaults)
# --------------------------------------------------------------------------- #
HERE = Path(__file__).parent
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:latest")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "").strip()
USE_MOCK = not N8N_WEBHOOK_URL

MOCK_BRIEF = HERE / "mock_brief.json"
EVENTS_LOG = HERE / "events.jsonl"
SAMPLE_EVENTS = HERE / "sample_events.jsonl"
LISTINGS_STORE = HERE / "listings.jsonl"
SAMPLE_LISTINGS = HERE / "sample_listings.jsonl"

app = Flask(__name__)


# --------------------------------------------------------------------------- #
# Storage helpers (files; Phase 2 swaps listings for the Bedrock KB)
# --------------------------------------------------------------------------- #
def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def load_events() -> tuple[list[dict], bool]:
    real = _read_jsonl(EVENTS_LOG)
    return (real, False) if real else (_read_jsonl(SAMPLE_EVENTS), True)


def load_listings() -> tuple[list[dict], bool]:
    real = _read_jsonl(LISTINGS_STORE)
    return (real, False) if real else (_read_jsonl(SAMPLE_LISTINGS), True)


def listings_context(limit: int = 15) -> str:
    items, _ = load_listings()
    if not items:
        return ""
    lines = [
        f"Listing {i} — {it.get('property_type', '?')} in {it.get('location', '?')} "
        f"(agent: {it.get('agent', '?')}): {it.get('description', '').strip()}"
        for i, it in enumerate(items[-limit:], 1)
    ]
    return (
        "PROPERTY LISTINGS CURRENTLY IN THE SYSTEM (you MAY answer questions about "
        "these using ONLY this data; cite the listing number; do not invent details "
        "beyond what is written here):\n" + "\n".join(lines)
    )


def save_listing(agent: str, description: str, images: list[str], result: dict) -> None:
    extracted = result.get("extracted", {}) or {}
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "agent": agent or "—",
        "property_type": extracted.get("property_type", "—"),
        "location": extracted.get("location", "—"),
        "description": description,
        "images": images,
    }
    with LISTINGS_STORE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def log_event(agent: str, result: dict, elapsed_ms: int) -> None:
    imgs = result.get("images", []) or []
    conds = [i.get("condition_score") for i in imgs if isinstance(i.get("condition_score"), (int, float))]
    guard = result.get("guardrail", {}) or {}
    extracted = result.get("extracted", {}) or {}
    event = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "agent": agent or "—",
        "property_type": extracted.get("property_type", "—"),
        "location": extracted.get("location", "—"),
        "status": result.get("status", "ok"),
        "routing": result.get("routing", "—"),
        "avg_condition": round(sum(conds) / len(conds), 2) if conds else None,
        "input_pass": guard.get("input_pass"),
        "output_pass": guard.get("output_pass"),
        "exec_ms": result.get("exec_ms", elapsed_ms),
    }
    with EVENTS_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


# --------------------------------------------------------------------------- #
# Ollama (Assistant)
# --------------------------------------------------------------------------- #
def build_messages(history: list[dict]) -> list[dict]:
    """System prompt + per-turn language directive + the listings context."""
    last_user = next((m["content"] for m in reversed(history) if m.get("role") == "user"), "")
    lang = "Hebrew" if re.search(r"[֐-׿]", last_user) else "English"
    directive = (
        f"CRITICAL: The user's message is in {lang}. Your ENTIRE reply — including "
        f"any refusal or apology — MUST be written ONLY in {lang}."
    )
    if lang == "English":
        directive += (
            ' If you must decline an off-topic request, decline IN ENGLISH, e.g.: '
            '"I can only help with real-estate questions — is there a property topic '
            'I can help you with?"'
        )
    ctx = listings_context()
    parts = [directive, REALESTATE_SYSTEM_PROMPT]
    if ctx:
        parts.append(ctx)
    parts.append(directive)
    return [{"role": "system", "content": "\n\n".join(parts)}] + history


def stream_ollama(messages: list[dict]):
    resp = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": OLLAMA_MODEL, "messages": messages, "stream": True},
        stream=True,
        timeout=120,
    )
    resp.raise_for_status()
    for line in resp.iter_lines():
        if not line:
            continue
        data = json.loads(line)
        chunk = data.get("message", {}).get("content", "")
        if chunk:
            yield chunk
        if data.get("done"):
            break


# --------------------------------------------------------------------------- #
# Listing submission
# --------------------------------------------------------------------------- #
def submit_listing(payload: dict) -> tuple[dict, int]:
    start = time.perf_counter()
    if USE_MOCK:
        result = json.loads(MOCK_BRIEF.read_text(encoding="utf-8"))
        if payload.get("description"):
            result.setdefault("extracted", {})["submitted_description"] = payload["description"]
        time.sleep(0.4)
    else:
        resp = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=120)
        resp.raise_for_status()
        result = resp.json()
    return result, int((time.perf_counter() - start) * 1000)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return render_template("index.html", mock=USE_MOCK, model=OLLAMA_MODEL)


@app.route("/api/chat", methods=["POST"])
def api_chat():
    history = (request.get_json(silent=True) or {}).get("history", [])
    messages = build_messages(history)

    @stream_with_context
    def generate():
        try:
            for chunk in stream_ollama(messages):
                yield chunk
        except requests.exceptions.RequestException:
            yield "\n\n⚠️ Couldn't reach Ollama. Run `ollama serve` and `ollama pull llama3.1`."

    return Response(generate(), mimetype="text/plain; charset=utf-8")


@app.route("/api/submit", methods=["POST"])
def api_submit():
    body = request.get_json(silent=True) or {}
    description = (body.get("description") or "").strip()
    if not description:
        return jsonify({"error": "Property description is required."}), 400
    agent_name = (body.get("agent_name") or "").strip()
    images = body.get("images") or []
    payload = {"description": description, "images": images, "agent_name": agent_name}
    try:
        result, elapsed_ms = submit_listing(payload)
    except requests.exceptions.RequestException as exc:
        return jsonify({"error": f"Couldn't reach the n8n webhook: {exc}"}), 502
    save_listing(agent_name, description, images, result)
    log_event(agent_name, result, elapsed_ms)
    if result.get("brief_markdown"):
        result["brief_html"] = md.markdown(result["brief_markdown"], extensions=["extra"])
    return jsonify(result)


@app.route("/api/dashboard")
def api_dashboard():
    events, is_sample = load_events()
    total = len(events)
    rejected = sum(1 for e in events if e.get("status") == "rejected" or e.get("input_pass") is False)
    conds = [e["avg_condition"] for e in events if isinstance(e.get("avg_condition"), (int, float))]
    execs = [e["exec_ms"] for e in events if isinstance(e.get("exec_ms"), (int, float))]
    routing = Counter(e.get("routing", "—") for e in events)
    outcomes = Counter(e.get("status", "ok") for e in events)
    return jsonify(
        is_sample=is_sample,
        metrics={
            "total": total,
            "rejection_rate": round(rejected / total * 100) if total else 0,
            "avg_condition": round(sum(conds) / len(conds), 1) if conds else None,
            "avg_exec_ms": round(sum(execs) / len(execs)) if execs else None,
        },
        routing=dict(routing),
        outcomes=dict(outcomes),
        exec_series=[{"ts": e.get("ts", ""), "exec_ms": e.get("exec_ms")} for e in events],
        cond_series=[
            {"ts": e.get("ts", ""), "avg_condition": e["avg_condition"]}
            for e in events
            if isinstance(e.get("avg_condition"), (int, float))
        ],
        recent=list(reversed(events))[:10],
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=True)
