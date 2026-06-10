"""Shared pytest setup for the Property Triage test suite.

Makes the service packages importable and supplies harmless defaults for the
env vars the services fail-fast on. Every test mocks boto3 / google-genai /
Ollama, so nothing here touches the network or real AWS credentials.
"""
import os
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1] / "code"
# `code/` so `import rag_service.main`, `shared.aws_utils`, … resolve;
# `code/webui/` so the Flask app imports the same way it runs (`import server`).
for _p in (str(CODE), str(CODE / "webui")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# rag_service / guardrails_service raise at import if these are unset. The tests
# mock the clients, so any non-empty value works. setdefault lets a real .env
# (loaded by the service at import) win if present — the value is irrelevant.
os.environ.setdefault("KB_ID", "test-kb")
os.environ.setdefault("GUARDRAIL_ID", "test-guardrail")
os.environ.setdefault("AWS_REGION", "us-east-1")
