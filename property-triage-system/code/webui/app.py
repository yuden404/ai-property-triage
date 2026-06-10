"""Property Triage System — Web UI (Streamlit).

Three tabs:
  1. Assistant      — chat with a local Ollama model (real-estate assistant).
  2. Submit Listing — submit a listing to the n8n webhook (or a mock) and render the brief.
  3. Dashboard      — live processing stats + charts from a local event log.

Run:  .venv/bin/python -m streamlit run code/webui/app.py
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

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

AVATARS = {"user": "🧑", "assistant": "🏠"}
SUGGESTIONS = [
    "Which listings need renovation?",
    "What properties do you have in Ramat Gan?",
    "What should I check when viewing an apartment?",
    "Explain the steps of buying a property.",
]

st.set_page_config(page_title="Property Triage", page_icon="🏠", layout="wide")

# --------------------------------------------------------------------------- #
# Styling
# --------------------------------------------------------------------------- #
CSS = """
<style>
.block-container { padding-top: 1.4rem; padding-bottom: 2rem; max-width: 1200px; }

/* Header banner */
.app-header {
  background: linear-gradient(120deg, #14532d 0%, #2e7d32 55%, #4caf50 100%);
  color: #fff; border-radius: 16px; padding: 16px 24px; margin-bottom: 10px;
  box-shadow: 0 8px 22px rgba(46,125,50,.20);
}
.app-header .h-title { font-size: 1.65rem; font-weight: 800; line-height: 1.1; }
.app-header .h-sub { font-size: .92rem; opacity: .93; margin-top: 3px; }

/* Tabs */
.stTabs [data-baseweb="tab-list"] { gap: 4px; }
.stTabs [data-baseweb="tab"] { font-size: 1rem; padding: 6px 14px; }

/* Buttons → lively, rounded */
div.stButton > button {
  border-radius: 10px; border: 1px solid #cfe3cf; background: #f4f9f4;
  color: #1b5e20; font-weight: 500; transition: all .15s ease;
}
div.stButton > button:hover {
  border-color: #2e7d32; background: #e8f4e8; transform: translateY(-1px);
  box-shadow: 0 3px 10px rgba(46,125,50,.15);
}

/* Metric cards */
[data-testid="stMetric"] {
  background: #f7faf7; border: 1px solid #e3ece3; border-radius: 14px;
  padding: 12px 18px; box-shadow: 0 1px 3px rgba(0,0,0,.04);
}
[data-testid="stMetricValue"] { color: #2e7d32; font-weight: 700; }

/* Rounded bordered containers (chat box, brief card) */
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 14px; }

/* Sidebar tint */
[data-testid="stSidebar"] { background: #f4f7f4; }

/* Chat input — clean border (overrides the red outline) */
[data-testid="stChatInput"] {
  background: #fff !important; border: 1px solid #d0d7de !important;
  border-radius: 12px !important; box-shadow: none !important;
}
[data-testid="stChatInput"] > div { border: none !important; box-shadow: none !important; background: transparent !important; }
[data-testid="stChatInput"]:focus-within { border-color: #2e7d32 !important; box-shadow: 0 0 0 2px rgba(46,125,50,.12) !important; }
[data-testid="stChatInput"] textarea { background: transparent !important; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


def header() -> None:
    st.markdown(
        '<div class="app-header">'
        '<div class="h-title">🏠 Property Triage System</div>'
        '<div class="h-sub">AI-powered real-estate listing triage · assistant · submission · monitoring</div>'
        "</div>",
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# Backend helpers
# --------------------------------------------------------------------------- #
def stream_ollama(messages: list[dict]):
    """Yield assistant text chunks from Ollama's /api/chat streaming endpoint."""
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


def build_messages(history: list[dict]) -> list[dict]:
    """System prompt + a hard per-turn language directive (prepended AND appended).

    The model defaults to Hebrew — especially on refusals — so we detect the user's
    language in code and force the reply language at both ends of the system prompt.
    """
    last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
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


def submit_listing(payload: dict) -> tuple[dict, int]:
    """Send the listing to n8n, or return the mock brief. Returns (result, elapsed_ms)."""
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


def load_events() -> tuple[list[dict], bool]:
    """Return (events, is_sample): real events if present, else sample data."""
    def _read(path: Path) -> list[dict]:
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

    real = _read(EVENTS_LOG)
    return (real, False) if real else (_read(SAMPLE_EVENTS), True)


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


def load_listings() -> tuple[list[dict], bool]:
    """Listings entered via the Submit tab (real), else sample listings.

    Phase 2 seam: this is where the chat will instead query the Bedrock KB
    (RAG service /query) over the managed knowledge base.
    """
    real = _read_jsonl(LISTINGS_STORE)
    return (real, False) if real else (_read_jsonl(SAMPLE_LISTINGS), True)


def listings_context(limit: int = 15) -> str:
    """Format stored listings as grounding context for the assistant."""
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


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #
with st.sidebar:
    st.markdown("### 🏠 Property Triage")
    st.caption("AI-Powered Real Estate Listing Triage")
    st.divider()
    st.markdown("**Configuration**")
    st.write(f"Ollama model: `{OLLAMA_MODEL}`")
    if USE_MOCK:
        st.warning("Submit mode: **MOCK**", icon="🧪")
        st.caption("No n8n webhook set — Submit returns a sample brief.")
    else:
        st.success("Submit mode: **LIVE** → n8n", icon="🔗")
    st.divider()
    st.caption("Layer 1 of 4 · WebUI")


header()
tab_chat, tab_submit, tab_dash = st.tabs(["💬 Assistant", "📤 Submit Listing", "📊 Dashboard"])

# --------------------------------------------------------------------------- #
# Tab 1 — Assistant
# --------------------------------------------------------------------------- #
with tab_chat:
    st.caption(
        "Ask about the listings in the system, the buying/renting process, what to "
        "check in a property, and market terms. It won't invent prices, links, or legal advice."
    )

    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Scrollable chat area (its own scrollbar; shorter when empty)
    chat_box = st.container(height=360 if st.session_state.messages else 240, border=True)
    if st.session_state.messages:
        for m in st.session_state.messages:
            chat_box.chat_message(m["role"], avatar=AVATARS[m["role"]]).markdown(m["content"])
    else:
        chat_box.chat_message("assistant", avatar=AVATARS["assistant"]).markdown(
            "Hi! I'm your real-estate assistant. I can answer questions about the "
            "listings in the system, explain the buying/renting process, and help with "
            "what to check when viewing a property.\n\nPick a question below or type your own 👇"
        )

    # Suggested questions (only before the first user message)
    pending = None
    if not st.session_state.messages:
        cols = st.columns(2)
        for i, s in enumerate(SUGGESTIONS):
            if cols[i % 2].button(s, key=f"sug_{i}", width="stretch"):
                pending = s

    typed = st.chat_input("Ask about the property market…")
    prompt = typed or pending

    if st.session_state.messages and st.button("🗑 Clear chat"):
        st.session_state.messages = []
        st.rerun()

    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        chat_box.chat_message("user", avatar=AVATARS["user"]).markdown(prompt)
        with chat_box.chat_message("assistant", avatar=AVATARS["assistant"]):
            try:
                reply = st.write_stream(
                    stream_ollama(build_messages(st.session_state.messages))
                )
            except requests.exceptions.RequestException:
                reply = None
                st.error("Couldn't reach Ollama. Run `ollama serve` and `ollama pull llama3.1`.")
        if reply:
            st.session_state.messages.append({"role": "assistant", "content": reply})
            st.rerun()


# --------------------------------------------------------------------------- #
# Tab 2 — Submit Listing
# --------------------------------------------------------------------------- #
def render_result(result: dict) -> None:
    status = result.get("status", "ok")
    if status == "rejected":
        st.error(f"🚫 Rejected by guardrail: {result.get('reason', 'not a valid listing')}")
        return
    if status == "review":
        st.warning("⚠️ Output flagged for human review — not auto-published.")

    st.success(f"✅ Processed · routed to **{result.get('routing', '—')}** team")

    if brief := result.get("brief_markdown"):
        st.markdown("#### 📄 Listing brief")
        with st.container(border=True):
            st.markdown(brief)

    col_a, col_b = st.columns(2)
    if imgs := (result.get("images") or []):
        with col_a:
            st.markdown("#### 🖼 Image analysis")
            st.dataframe(imgs, width="stretch", hide_index=True)
    if sims := (result.get("similar_listings") or []):
        with col_b:
            st.markdown("#### 🔎 Similar listings")
            st.dataframe(sims, width="stretch", hide_index=True)

    guard = result.get("guardrail", {}) or {}
    st.caption(
        f"Guardrails — input: {guard.get('input_pass')} · output: {guard.get('output_pass')} "
        f"· exec: {result.get('exec_ms', '?')} ms"
    )


with tab_submit:
    st.caption("Per the project spec: description, property images (upload), and listing agent name.")
    if USE_MOCK:
        st.info("Running in **MOCK mode** — returns a sample brief. Set `N8N_WEBHOOK_URL` to go live.", icon="🧪")

    # File uploader = drag-&-drop zone + folder browse (outside the form so previews show live).
    images = st.file_uploader(
        "Property images",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        help="Drag & drop images here, or click to browse your folders.",
    )
    if images:
        st.caption(f"{len(images)} image(s) selected")
        cols = st.columns(4)
        for i, img in enumerate(images):
            cols[i % 4].image(img, caption=img.name, width=150)

    with st.form("listing_form"):
        description = st.text_area(
            "Property description *",
            height=120,
            placeholder="e.g. Bright 3-bedroom apartment in Ramat Gan, 95 sqm, balcony, parking, quiet street…",
        )
        agent_name = st.text_input("Listing agent name")
        submitted = st.form_submit_button("🚀 Submit listing", type="primary")

    if submitted:
        if not description.strip():
            st.error("Please enter a property description.")
        else:
            # NOTE: in LIVE mode (Phase 4) the uploaded files are pushed to S3 and the
            # resulting URLs are sent to n8n; for now we pass filenames (mock ignores them).
            payload = {
                "description": description.strip(),
                "images": [img.name for img in images] if images else [],
                "agent_name": agent_name.strip(),
            }
            try:
                with st.spinner("Processing listing…"):
                    result, elapsed_ms = submit_listing(payload)
                save_listing(agent_name, description.strip(), payload["images"], result)
                log_event(agent_name, result, elapsed_ms)
                render_result(result)
            except requests.exceptions.RequestException as exc:
                st.error(f"Couldn't reach the n8n webhook: {exc}")


# --------------------------------------------------------------------------- #
# Tab 3 — Dashboard
# --------------------------------------------------------------------------- #
with tab_dash:
    events, is_sample = load_events()

    if not events:
        st.info("No processing events yet. Submit a listing to populate the dashboard.")
    else:
        if is_sample:
            st.caption("Showing **sample data** (no real submissions yet).")
        df = pd.DataFrame(events)

        total = len(df)
        rejected = int(((df["status"] == "rejected") | (df["input_pass"] == False)).sum())  # noqa: E712
        conds = df["avg_condition"].dropna()
        execs = df["exec_ms"].dropna()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Listings processed", total)
        c2.metric("Guardrail rejection rate", f"{rejected / total * 100:.0f}%")
        c3.metric("Avg. condition score", f"{conds.mean():.1f}" if len(conds) else "—")
        c4.metric("Avg. exec time", f"{int(execs.mean())} ms" if len(execs) else "—")

        st.write("")
        col_l, col_r = st.columns(2)
        with col_l:
            st.markdown("##### 🏘 Listings by team")
            st.bar_chart(df["routing"].value_counts(), color="#2e7d32", height=230)
        with col_r:
            st.markdown("##### ⏱ Execution time per listing (ms)")
            st.line_chart(df.set_index("ts")["exec_ms"], color="#1565c0", height=230)

        col_l2, col_r2 = st.columns(2)
        with col_l2:
            st.markdown("##### ⭐ Avg. condition score per listing")
            cond_series = df.dropna(subset=["avg_condition"]).set_index("ts")["avg_condition"]
            st.bar_chart(cond_series, color="#f9a825", height=230)
        with col_r2:
            st.markdown("##### ✅ Outcomes")
            st.bar_chart(df["status"].value_counts(), color="#ef6c00", height=230)

        st.markdown("##### 🧾 Recent listings (last 10)")
        recent = df.iloc[::-1].head(10).copy()
        labels = {
            "ts": "Time", "agent": "Agent", "property_type": "Type", "location": "Location",
            "routing": "Team", "status": "Status", "avg_condition": "Avg cond.",
            "input_pass": "Input ✓", "output_pass": "Output ✓", "exec_ms": "Time (ms)",
        }
        cols = [c for c in labels if c in recent.columns]
        st.dataframe(recent[cols].rename(columns=labels), width="stretch", hide_index=True)
