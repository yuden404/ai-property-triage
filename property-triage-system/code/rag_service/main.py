"""Property Triage — RAG Service (Service 1).

POST /query  { "description": "<listing text>" }
          →  { "similar_listings": [ {id, text, score}, … ], "insight": "<text>" }

Retrieval: Amazon Bedrock Knowledge Base (S3 Vectors) — top-K semantic search.
Generation: Gemini (key from AWS Secrets Manager) — cited, no-fabrication insight.

Run locally (from the code/ directory):
    AWS_PROFILE=course ../.venv/bin/python -m uvicorn rag_service.main:app --port 8001
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from rag_service.prompts import INSIGHT_PROMPT
from shared.aws_utils import client
from shared.gemini_utils import generate

try:  # load this service's .env (no-op if python-dotenv or the file is absent)
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name(".env"))
except ImportError:
    pass

KB_ID = os.getenv("KB_ID")
if not KB_ID:
    raise RuntimeError("KB_ID is required — set it in code/rag_service/.env (see .env.example)")
TOP_K = int(os.getenv("TOP_K", "3"))

app = FastAPI(title="Property Triage — RAG Service")
_runtime = client("bedrock-agent-runtime")


class QueryRequest(BaseModel):
    description: str


def _listing_id(text: str) -> str:
    """Extract the listing id from the document text ('Listing L007: …')."""
    m = re.match(r"Listing\s+(L\d+)", text)
    return m.group(1) if m else "unknown"


@app.get("/health")
def health():
    return {"status": "ok", "kb_id": KB_ID}


@app.post("/query")
def query(req: QueryRequest):
    description = req.description.strip()
    if not description:
        raise HTTPException(status_code=400, detail="description is required")

    try:
        resp = _runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": description},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": TOP_K}},
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"knowledge base retrieval failed: {exc}")

    similar = [
        {
            "id": _listing_id(hit["content"]["text"]),
            "text": hit["content"]["text"].strip(),
            "score": round(hit["score"], 3),
        }
        for hit in resp.get("retrievalResults", [])
    ]
    if not similar:
        return {"similar_listings": [], "insight": "No comparable listings found in the knowledge base."}

    context = "\n\n".join(f"[{s['id']}] (similarity {s['score']})\n{s['text']}" for s in similar)
    try:
        insight = generate(
            INSIGHT_PROMPT.format(description=description, k=len(similar), context=context),
            temperature=0.3,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"insight generation failed: {exc}")

    return {"similar_listings": similar, "insight": insight}
