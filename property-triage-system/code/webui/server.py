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
import threading
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, make_response, render_template, request, stream_with_context

from system_prompts import REALESTATE_SYSTEM_PROMPT

try:  # load webui/.env so N8N_WEBHOOK_URL etc. actually take effect (no-op if absent)
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass

# --------------------------------------------------------------------------- #
# Config (env with safe defaults)
# --------------------------------------------------------------------------- #
HERE = Path(__file__).parent
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:latest")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "").strip()
USE_MOCK = not N8N_WEBHOOK_URL

MOCK_BRIEF = HERE / "mock_brief.json"
# Real submissions live in a mounted volume (DATA_DIR) so they survive container
# rebuilds; the sample/demo files stay baked into the image (read-only).
DATA_DIR = Path(os.getenv("DATA_DIR", str(HERE)))
DATA_DIR.mkdir(parents=True, exist_ok=True)
EVENTS_LOG = DATA_DIR / "events.jsonl"
SAMPLE_EVENTS = HERE / "sample_events.jsonl"
LISTINGS_STORE = DATA_DIR / "listings.jsonl"
SAMPLE_LISTINGS = HERE / "sample_listings.jsonl"

# Image upload + analysis (Service 2). The WebUI uploads photos to S3 and calls
# the Image Analyser directly, so every uploaded photo is classified and shown.
IMAGE_URL = os.getenv("IMAGE_URL", "http://localhost:8002").rstrip("/")
RAG_URL = os.getenv("RAG_URL", "http://localhost:8001").rstrip("/")
UPLOAD_BUCKET = os.getenv("UPLOAD_BUCKET", "").strip()
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Bedrock Knowledge Base — accepted listings are ingested so they become
# permanent RAG comparables (the KB itself lives in S3 + OpenSearch).
KB_ID = os.getenv("KB_ID", "").strip()
KB_DATA_SOURCE_ID = os.getenv("KB_DATA_SOURCE_ID", "").strip()

# DynamoDB — durable system-of-record for submitted listings + dashboard events.
# Survives container rebuilds (the volume did not). The KB stays the search index;
# DynamoDB holds the full records incl. photo S3 keys. Unset → fall back to local
# JSONL (so local dev / the offline test suite need no AWS).
DDB_LISTINGS_TABLE = os.getenv("DDB_LISTINGS_TABLE", "").strip()
DDB_EVENTS_TABLE = os.getenv("DDB_EVENTS_TABLE", "").strip()

app = Flask(__name__)

_s3_client = None


def s3():
    """Lazy S3 client — credentials come from the instance role on EC2 (boto3's
    default chain), so there are no keys in code or env."""
    global _s3_client
    if _s3_client is None:
        import boto3

        _s3_client = boto3.client("s3", region_name=AWS_REGION)
    return _s3_client


def public_url(key: str) -> str:
    """Permanent public URL for an uploaded photo. The `uploads/` prefix is granted
    public read by the bucket policy, so these links never expire — no presigning,
    nothing to refresh. (Only `uploads/` is public; the KB text under `listings/`
    stays private.)"""
    from urllib.parse import quote

    return f"https://{UPLOAD_BUCKET}.s3.{AWS_REGION}.amazonaws.com/{quote(key, safe='/')}"


_bedrock_agent_client = None


def bedrock_agent():
    """Lazy bedrock-agent client (KB ingestion). Instance role provides creds."""
    global _bedrock_agent_client
    if _bedrock_agent_client is None:
        import boto3

        _bedrock_agent_client = boto3.client("bedrock-agent", region_name=AWS_REGION)
    return _bedrock_agent_client


_ddb_client = None


def ddb():
    """Lazy DynamoDB client (durable record store). Instance role provides creds."""
    global _ddb_client
    if _ddb_client is None:
        import boto3

        _ddb_client = boto3.client("dynamodb", region_name=AWS_REGION)
    return _ddb_client


def _ddb_put_doc(table: str, doc: dict) -> None:
    """Store a record as a JSON document under its `id`, with a top-level `ts` for
    chronological ordering. Storing the whole record as a JSON string sidesteps
    DynamoDB's float/Decimal marshalling (condition scores, exec_ms) entirely."""
    key = str(doc.get("id") or uuid.uuid4().hex)
    ddb().put_item(TableName=table, Item={
        "id": {"S": key},
        "ts": {"S": str(doc.get("ts") or "")},
        "doc": {"S": json.dumps(doc, ensure_ascii=False)},
    })


def _ddb_all_docs(table: str) -> list[dict]:
    """Scan every record (low volume) and return them sorted by `ts` ascending —
    the same append order the JSONL store used to give."""
    docs = []
    for page in ddb().get_paginator("scan").paginate(TableName=table):
        for it in page.get("Items", []):
            raw = it.get("doc", {}).get("S")
            if not raw:
                continue
            try:
                docs.append(json.loads(raw))
            except json.JSONDecodeError:
                pass
    docs.sort(key=lambda d: d.get("ts") or "")
    return docs


def _ddb_get_doc(table: str, key: str) -> dict | None:
    raw = (ddb().get_item(TableName=table, Key={"id": {"S": key}}).get("Item") or {}).get("doc", {}).get("S")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


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


# The listings/events files are read on hot paths (every chat turn / dashboard
# poll). Cache them and re-read only when the file changes (mtime or size) — a
# new submission appends → the cache refreshes itself. Guarded by a lock because
# Werkzeug serves requests on multiple threads.
_cache_lock = threading.Lock()
_listings_cache: dict = {"sig": None, "data": []}
_events_cache: dict = {"sig": None, "data": []}


def _read_jsonl_cached(path: Path, cache: dict) -> list[dict]:
    try:
        st = path.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:  # missing / unreadable → treat as empty, don't 500 the caller
        sig = None
    with _cache_lock:
        if cache["sig"] != sig:
            cache["sig"] = sig
            cache["data"] = _read_jsonl(path)
        return list(cache["data"])  # copy: a caller can't mutate the shared cache


def load_events() -> tuple[list[dict], bool]:
    # DynamoDB is the durable source of truth; the seeded demo events stay baked
    # into the image and are shown first. is_sample is True only while there are
    # no real submissions yet.
    real = _ddb_all_docs(DDB_EVENTS_TABLE) if DDB_EVENTS_TABLE else _read_jsonl_cached(EVENTS_LOG, _events_cache)
    return _read_jsonl(SAMPLE_EVENTS) + real, not real


def load_listings() -> tuple[list[dict], bool]:
    real = _ddb_all_docs(DDB_LISTINGS_TABLE) if DDB_LISTINGS_TABLE else _read_jsonl_cached(LISTINGS_STORE, _listings_cache)
    return _read_jsonl(SAMPLE_LISTINGS) + real, not real


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


def _extracted_field(extracted: dict, key: str) -> str:
    """Pull a field from the Information Extractor output. n8n's langchain
    Information Extractor nests its attributes under 'output' (same shape the
    Router reads as output.routing), so check the top level and 'output'."""
    if not isinstance(extracted, dict):
        return "—"
    nested = extracted.get("output") if isinstance(extracted.get("output"), dict) else {}
    return extracted.get(key) or nested.get(key) or "—"


def save_listing(agent: str, description: str, images: list, result: dict, listing_id: str = "") -> None:
    extracted = result.get("extracted", {}) or {}
    rec = {
        "id": listing_id,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "agent": agent or "—",
        "property_type": _extracted_field(extracted, "property_type"),
        "location": _extracted_field(extracted, "location"),
        "routing": result.get("routing", "—"),
        "status": result.get("status", "ok"),
        "description": description,
        "brief_markdown": result.get("brief_markdown", ""),
        "images": images,
    }
    if DDB_LISTINGS_TABLE:
        _ddb_put_doc(DDB_LISTINGS_TABLE, rec)
    else:
        with LISTINGS_STORE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")


def log_event(agent: str, result: dict, listing_id: str = "") -> None:
    imgs = result.get("images", []) or []
    conds = [i.get("condition_score") for i in imgs if isinstance(i.get("condition_score"), (int, float))]
    guard = result.get("guardrail", {}) or {}
    extracted = result.get("extracted", {}) or {}
    event = {
        "id": listing_id,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "agent": agent or "—",
        "property_type": _extracted_field(extracted, "property_type"),
        "location": _extracted_field(extracted, "location"),
        "status": result.get("status", "ok"),
        "routing": result.get("routing", "—"),
        "avg_condition": round(sum(conds) / len(conds), 2) if conds else None,
        "input_pass": guard.get("input_pass"),
        "output_pass": guard.get("output_pass"),
        "exec_ms": result.get("exec_ms"),
    }
    if DDB_EVENTS_TABLE:
        _ddb_put_doc(DDB_EVENTS_TABLE, event)
    else:
        with EVENTS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")


# --------------------------------------------------------------------------- #
# Ollama (Assistant)
# --------------------------------------------------------------------------- #
# Stable listing IDs as they appear in chat text: seed ids (L015) and submissions.
LISTING_ID_RE = re.compile(r"SUBMITTED-\d{14}-[0-9a-f]{6}|\bL\d{2,}\b")


def _retrieval_query(history: list[dict]) -> str:
    """Build the retrieval query from the last few USER turns, not just the latest —
    so a vague follow-up ("tell me about it") still carries the topic of the
    conversation and retrieves the same listings instead of a random new set."""
    users = [m.get("content", "") for m in history if m.get("role") == "user"]
    return "  ".join(users[-3:]).strip()


def _listing_block_from_record(rec: dict) -> str:
    """Format a stored listing record as a grounding block, same shape as the KB
    text (starts 'Listing <ID>: …') so pinned and retrieved blocks read alike."""
    parts = [f"Listing {rec.get('id', '?')}: {rec.get('property_type', 'property')} "
             f"in {rec.get('location', '?')}"]
    if rec.get("description"):
        parts.append(rec["description"].strip())
    if rec.get("agent") and rec["agent"] != "—":
        parts.append(f"Listing agent: {rec['agent']}")
    return "\n".join(parts)


def _pinned_listing_blocks(history: list[dict]) -> list[tuple[str, str]]:
    """Listings already named anywhere in the conversation, fetched by id from the
    record store. Pinning them means a listing once discussed never 'disappears'
    on a later turn (retrieval for a vague follow-up used to drop it, which made
    the assistant contradict itself). Submitted listings live in DynamoDB; seed
    ids aren't there and are left to normal retrieval."""
    out, seen = [], set()
    if not DDB_LISTINGS_TABLE:
        return out
    for m in history:
        for lid in LISTING_ID_RE.findall(m.get("content", "") or ""):
            if lid in seen:
                continue
            seen.add(lid)
            try:
                rec = _ddb_get_doc(DDB_LISTINGS_TABLE, lid)
            except Exception:
                rec = None
            if rec:
                out.append((lid, _listing_block_from_record(rec)))
    return out


def rag_listings_context(history: list[dict]) -> str:
    """Ground the chat on the RAG/KB. Two things keep follow-up turns coherent:
    (1) the retrieval query is built from the last few user turns, so a vague
        "tell me about it" still carries the topic and retrieves the same listings;
    (2) any listing already named earlier in the conversation is pinned — fetched
        by id and always included — so a listing once discussed never vanishes on a
        later turn (the cause of the self-contradiction).
    Falls back to the local store only if the RAG service is unreachable AND
    nothing was pinned."""
    blocks: dict[str, str] = {}  # id -> text; dict keeps insertion order + dedups

    for lid, text in _pinned_listing_blocks(history):  # (2) pinned, listed first
        blocks.setdefault(lid, text)

    query = _retrieval_query(history)  # (1) retrieved, relevant to the topic
    if query:
        try:
            r = requests.post(f"{RAG_URL}/query",
                              json={"description": query, "with_insight": False}, timeout=20)
            r.raise_for_status()
            for h in (r.json().get("similar_listings", []) or []):
                text = (h.get("text") or "").strip()
                if not text:
                    continue
                m = LISTING_ID_RE.search(text)
                blocks.setdefault(m.group(0) if m else text[:40], text)
        except requests.exceptions.RequestException:
            if not blocks:
                return listings_context()  # RAG down and nothing pinned → local store

    if not blocks:
        return ""
    # Refer to listings by stable ID — NEVER a position number (the set changes
    # between turns, so positions are unstable and caused self-contradiction).
    return ("PROPERTY LISTINGS relevant to this conversation. Refer to each property "
            "by its ID (e.g. L015) or title, NEVER by a position number. Discuss ONLY "
            "the properties below; if asked about one not listed here, say you don't "
            "have it — and do NOT contradict a listing you described earlier in this "
            "chat:\n\n" + "\n\n----\n\n".join(blocks.values()))


def build_messages(history: list[dict]) -> list[dict]:
    """System prompt + per-turn language directive + the listings context."""
    last_user = next((m.get("content", "") for m in reversed(history) if m.get("role") == "user"), "")
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
    ctx = rag_listings_context(history)
    parts = [directive, REALESTATE_SYSTEM_PROMPT]
    if ctx:
        parts.append(ctx)
    parts.append(directive)
    return [{"role": "system", "content": "\n\n".join(parts)}] + history


def open_ollama_stream(messages: list[dict]):
    """Open the Ollama chat stream. Raises RequestException if it can't connect —
    callers turn that into a clean HTTP error BEFORE any streaming begins."""
    resp = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": OLLAMA_MODEL, "messages": messages, "stream": True},
        stream=True,
        timeout=120,
    )
    resp.raise_for_status()
    return resp


def iter_ollama_chunks(resp):
    try:
        for line in resp.iter_lines():
            if not line:
                continue
            data = json.loads(line)
            chunk = data.get("message", {}).get("content", "")
            if chunk:
                yield chunk
            if data.get("done"):
                break
    except (requests.exceptions.RequestException, json.JSONDecodeError):
        # Connection dropped or a malformed line mid-reply — end gracefully with a
        # short notice instead of aborting the chunked response unhandled.
        yield "\n\n⚠️ The reply was interrupted — please try again."


# --------------------------------------------------------------------------- #
# Listing submission
# --------------------------------------------------------------------------- #
def submit_listing(payload: dict) -> tuple[dict, int]:
    start = time.perf_counter()
    if USE_MOCK:
        result = json.loads(MOCK_BRIEF.read_text(encoding="utf-8"))
        if payload.get("description"):
            result.setdefault("extracted", {})["submitted_description"] = payload["description"]
    else:
        resp = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=120)
        resp.raise_for_status()
        result = resp.json()
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    # Report the REAL round-trip we just measured. Keep a backend-provided value
    # only if it sent one (n8n might); the mock no longer carries a canned
    # exec_ms, so the dashboard reflects actual latency.
    result.setdefault("exec_ms", elapsed_ms)
    return result, elapsed_ms


# --------------------------------------------------------------------------- #
# Bedrock KB ingestion — accepted listings become permanent RAG comparables
# --------------------------------------------------------------------------- #
def _kb_text(listing_id: str, description: str, result: dict, agent: str = "") -> str:
    """Format an accepted listing to match the seeded corpus (Type/Location/…)."""
    ex = result.get("extracted", {}) or {}
    ptype = _extracted_field(ex, "property_type")
    loc = _extracted_field(ex, "location")
    feats = _extracted_field(ex, "key_features")
    if isinstance(feats, list):
        feats = ", ".join(str(x) for x in feats)
    heading = re.search(r"^#+\s*(.+)$", result.get("brief_markdown", "") or "", re.M)
    if heading:
        title = heading.group(1).strip()
    elif ptype != "—":
        title = f"{ptype} in {loc}"
    else:
        title = "Submitted listing"
    return "\n".join([
        f"Listing {listing_id}: {title}",
        f"Listing agent: {agent or 'N/A'}",
        f"Type: {ptype}",
        f"Location: {loc}",
        f"Price: {_extracted_field(ex, 'price')}",
        f"Rooms: {_extracted_field(ex, 'num_rooms')}",
        f"Features: {feats}",
        f"Description: {description}",
    ])


def ingest_listing_to_kb(listing_id: str, description: str, result: dict, agent: str = "") -> None:
    """Upload the listing text to the KB's S3 data source and start ingestion,
    so it becomes a retrievable comparable. Best-effort: a ConflictException
    (a sync already running) is fine — the file is in S3 for the next sync."""
    if not (UPLOAD_BUCKET and KB_ID and KB_DATA_SOURCE_ID):
        return
    try:
        s3().put_object(
            Bucket=UPLOAD_BUCKET, Key=f"listings/{listing_id}.txt",
            Body=_kb_text(listing_id, description, result, agent).encode("utf-8"),
            ContentType="text/plain",
        )
        bedrock_agent().start_ingestion_job(knowledgeBaseId=KB_ID, dataSourceId=KB_DATA_SOURCE_ID)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    resp = make_response(render_template("index.html", mock=USE_MOCK, model=OLLAMA_MODEL))
    resp.headers["Cache-Control"] = "no-store"  # always serve fresh HTML (no stale UI after deploys)
    return resp


@app.route("/api/chat", methods=["POST"])
def api_chat():
    history = (request.get_json(silent=True) or {}).get("history", [])
    messages = build_messages(history)

    # Connect to Ollama BEFORE returning a streaming Response — a connection
    # failure becomes a clean 502 (the client shows an error and does NOT store
    # a fake assistant turn), instead of streaming error text as a reply.
    try:
        resp = open_ollama_stream(messages)
    except requests.exceptions.RequestException:
        return jsonify({"error": "Couldn't reach Ollama. Run `ollama serve` and `ollama pull llama3.1`."}), 502

    return Response(stream_with_context(iter_ollama_chunks(resp)), mimetype="text/plain; charset=utf-8")


@app.route("/api/analyse-images", methods=["POST"])
def api_analyse_images():
    """Upload each photo to S3 and run the Image Analyser on it. Returns one
    object per image with the S3 location + room type / condition / confidence.
    Photos are optional, so a per-image failure is reported, never fatal."""
    files = request.files.getlist("images")
    if not files:
        return jsonify({"images": []})
    if not UPLOAD_BUCKET:
        return jsonify({"error": "Image uploads are not configured."}), 503
    client = s3()
    out = []
    for f in files:
        if (f.mimetype or "") not in ALLOWED_IMAGE_TYPES:
            continue
        data = f.read()
        if len(data) > MAX_IMAGE_BYTES:
            out.append({"name": f.filename, "room_type": "error", "note": "file too large (>8MB)"})
            continue
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", f.filename or "image")
        key = f"uploads/{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}-{safe}"
        try:
            client.put_object(Bucket=UPLOAD_BUCKET, Key=key, Body=data, ContentType=f.mimetype)
            url = public_url(key)  # permanent (uploads/ is public) — never expires
        except Exception as exc:  # S3 misconfig / perms — report, keep going
            out.append({"name": f.filename, "room_type": "error", "note": f"upload failed: {exc}"})
            continue
        analysis = {"room_type": None, "condition_score": None, "confidence": None}
        try:
            r = requests.post(f"{IMAGE_URL}/analyse", json={"image_url": url}, timeout=40)
            r.raise_for_status()
            analysis = r.json()
        except requests.exceptions.RequestException as exc:
            analysis = {"room_type": "error", "condition_score": None,
                        "confidence": None, "note": str(exc)[:160]}
        out.append({
            "name": f.filename,
            "s3_key": key,
            "url": url,
            "room_type": analysis.get("room_type"),
            "condition_score": analysis.get("condition_score"),
            "confidence": analysis.get("confidence"),
        })
    return jsonify({"images": out})


@app.route("/api/submit", methods=["POST"])
def api_submit():
    body = request.get_json(silent=True) or {}
    description = (body.get("description") or "").strip()
    if not description:
        return jsonify({"error": "Property description is required."}), 400
    agent_name = (body.get("agent_name") or "").strip()
    if not agent_name:
        return jsonify({"error": "Listing agent name is required."}), 400
    # images = the analysis objects from /api/analyse-images (or bare filenames).
    images = body.get("images") or []
    image_names = [(im.get("name") if isinstance(im, dict) else im) for im in images]
    payload = {"description": description, "images": image_names, "agent_name": agent_name}
    try:
        result, _ = submit_listing(payload)
    except requests.exceptions.RequestException as exc:
        return jsonify({"error": f"Couldn't reach the n8n webhook: {exc}"}), 502
    # The photo analyses were computed here (S3 + Image Analyser), not by n8n —
    # attach them so the result grid and the dashboard condition stats see them.
    if images and isinstance(images[0], dict):
        result["images"] = images
    listing_id = f"SUBMITTED-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    result["id"] = listing_id
    # Log every event (incl. rejected) for the dashboard, but only feed ACCEPTED
    # listings into the chat store + the Knowledge Base — never persist rejected input.
    log_event(agent_name, result, listing_id)
    if result.get("status") != "rejected":
        save_listing(agent_name, description, images, result, listing_id)
        ingest_listing_to_kb(listing_id, description, result, agent_name)  # → permanent RAG comparable
    # brief_markdown is rendered safely client-side (mdLite escapes HTML) — no raw
    # HTML is generated server-side, closing the stored-XSS vector.
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


@app.route("/api/listing/<lid>")
def api_listing(lid):
    """Full detail for one listing (dashboard click-through): description, brief,
    and photos as permanent public URLs (the uploads/ prefix is public-read)."""
    if DDB_LISTINGS_TABLE:
        rec = _ddb_get_doc(DDB_LISTINGS_TABLE, lid)
    else:
        items, _ = load_listings()
        rec = next((x for x in reversed(items) if x.get("id") == lid), None)
    if not rec:
        return jsonify({"found": False}), 404
    images = []
    for im in (rec.get("images") or []):
        if not isinstance(im, dict):
            continue
        url, key = im.get("url"), im.get("s3_key")
        if key and UPLOAD_BUCKET:
            url = public_url(key)  # permanent public URL (uploads/ is public)
        images.append({"name": im.get("name"), "url": url, "room_type": im.get("room_type"),
                       "condition_score": im.get("condition_score"), "confidence": im.get("confidence")})
    return jsonify({
        "found": True, "id": lid, "ts": rec.get("ts"), "agent": rec.get("agent"),
        "property_type": rec.get("property_type"), "location": rec.get("location"),
        "routing": rec.get("routing"), "description": rec.get("description"),
        "brief_markdown": rec.get("brief_markdown", ""), "images": images,
    })


if __name__ == "__main__":
    # debug is OFF by default; enable only locally via FLASK_DEBUG=1.
    # Never run the Werkzeug debugger on a public bind (RCE risk).
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    host = os.getenv("HOST", "127.0.0.1")
    app.run(host=host, port=int(os.getenv("PORT", "5050")), debug=debug)
