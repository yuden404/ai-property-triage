"""Property Triage — LangGraph Agent (Service 4).

POST /agent/run  { "query": "...", "image_urls": [ ... ]  (optional) }
              ->  { "answer": "...", "tools_used": [...], "reasoning_steps": [...] }

A stateful LangGraph agent: planner -> tool_executor -> synthesiser. The planner
(Gemini) picks tools from their descriptions; the executor calls the RAG service
and the Image Analyser over HTTP; the synthesiser (Gemini) writes the answer.
State accumulates `tools_used` / `reasoning_steps` (operator.add) so the response
schema fills itself as the graph runs.

Run locally (from the code/ directory):
    AWS_PROFILE=course ../.venv/bin/python -m uvicorn agent_service.main:app --port 8000
"""
from __future__ import annotations

import json
import operator
import os
import re
from pathlib import Path
from typing import Annotated, TypedDict

try:  # load this service's .env before anything reads env (tool URLs, Gemini model)
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass

import requests  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from langgraph.graph import END, START, StateGraph  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from agent_service.prompts import PLANNER_PROMPT, SYNTH_PROMPT, TOOL_DESCRIPTIONS  # noqa: E402
from shared.gemini_utils import generate  # noqa: E402

# --- Tools: HTTP clients to the sibling services (URLs from env) ----------- #
RAG_URL = os.getenv("RAG_URL", "http://127.0.0.1:8001").rstrip("/")
IMAGE_URL = os.getenv("IMAGE_URL", "http://127.0.0.1:8002").rstrip("/")
TOOL_TIMEOUT = int(os.getenv("TOOL_TIMEOUT", "60"))


def rag_query(description: str) -> dict:
    """RAG service → {similar_listings: [...], insight: "..."}."""
    resp = requests.post(f"{RAG_URL}/query", json={"description": description}, timeout=TOOL_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def analyse_image(image_url: str) -> dict:
    """Image Analyser → {room_type, condition_score, confidence}."""
    resp = requests.post(f"{IMAGE_URL}/analyse", json={"image_url": image_url}, timeout=TOOL_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# --- Graph: state + three nodes -------------------------------------------- #
class AgentState(TypedDict, total=False):
    query: str
    image_urls: list
    plan: dict
    findings: Annotated[list, operator.add]
    tools_used: Annotated[list, operator.add]
    reasoning_steps: Annotated[list, operator.add]
    answer: str


def _parse_json(raw: str) -> dict:
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.M).strip()
    return json.loads(raw)


def planner(state: AgentState) -> dict:
    """Decide which tools to call (Gemini), reading the tool descriptions."""
    has_images = bool(state.get("image_urls"))
    prompt = PLANNER_PROMPT.format(tools=TOOL_DESCRIPTIONS, has_images=has_images, query=state["query"])
    try:
        plan = _parse_json(generate(prompt, temperature=0.0))
    except Exception:  # noqa: BLE001 — fall back to a safe default plan
        plan = {"use_rag": True, "use_image": has_images, "rationale": "planner fallback"}
    if not has_images:
        plan["use_image"] = False  # never call the image tool without images
    return {"plan": plan, "reasoning_steps": [f"Planner: {plan.get('rationale', '').strip()}"]}


def tool_executor(state: AgentState) -> dict:
    """Invoke the chosen tools over HTTP; a tool failure degrades gracefully."""
    plan = state.get("plan", {})
    findings: list[str] = []
    used: list[str] = []
    steps: list[str] = []

    if plan.get("use_rag"):
        try:
            r = rag_query(state["query"])
            comps = "; ".join(f"{l.get('id')}: {l.get('text', '')}" for l in r.get("similar_listings", []))
            findings.append(f"[RAG] insight: {r.get('insight', '')}\ncomparables: {comps}")
            used.append("rag")
            steps.append("Called rag: retrieved comparable listings + market insight.")
        except Exception as exc:  # noqa: BLE001
            steps.append(f"rag unavailable: {exc}")

    if plan.get("use_image"):
        for url in state.get("image_urls", []):
            try:
                a = analyse_image(url)
                findings.append(
                    f"[IMAGE {url}] room={a.get('room_type')} "
                    f"condition={a.get('condition_score')}/5 confidence={a.get('confidence')}"
                )
                used.append("image")
                steps.append(f"Called image on {url}.")
            except Exception as exc:  # noqa: BLE001
                steps.append(f"image unavailable for {url}: {exc}")

    if not findings:
        findings.append("No tool results were available.")
    return {"findings": findings, "tools_used": used, "reasoning_steps": steps}


def synthesiser(state: AgentState) -> dict:
    """Combine tool outputs into the final grounded answer (Gemini)."""
    findings = "\n".join(state.get("findings", [])) or "none"
    try:
        answer = generate(SYNTH_PROMPT.format(query=state["query"], findings=findings), temperature=0.3)
    except Exception as exc:  # noqa: BLE001
        answer = f"Could not synthesise an answer: {exc}"
    return {"answer": answer, "reasoning_steps": ["Synthesiser: combined tool results into the final answer."]}


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("planner", planner)
    g.add_node("tool_executor", tool_executor)
    g.add_node("synthesiser", synthesiser)
    g.add_edge(START, "planner")
    g.add_edge("planner", "tool_executor")
    g.add_edge("tool_executor", "synthesiser")
    g.add_edge("synthesiser", END)
    return g.compile()


GRAPH = build_graph()


# --- API ------------------------------------------------------------------- #
app = FastAPI(title="Property Triage — LangGraph Agent")


class AgentRequest(BaseModel):
    query: str
    image_urls: list[str] = []


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/agent/run")
def agent_run(req: AgentRequest):
    final = GRAPH.invoke({"query": req.query.strip(), "image_urls": req.image_urls})
    return {
        "answer": final.get("answer", ""),
        "tools_used": final.get("tools_used", []),
        "reasoning_steps": final.get("reasoning_steps", []),
    }
