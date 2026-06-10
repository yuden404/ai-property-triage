#!/usr/bin/env bash
# Live smoke tests — hit the REAL running services (needs AWS creds + Ollama).
# These are the manual curl checks from development, captured so they're repeatable.
# For the offline/mocked suite run:  .venv/bin/python -m pytest   (see README.md)
#
# Start the services first (from code/, their .env is auto-loaded):
#   ../.venv/bin/python -m uvicorn rag_service.main:app        --port 8001
#   ../.venv/bin/python -m uvicorn guardrails_service.main:app --port 8003
#   FLASK_DEBUG=1 ../.venv/bin/python webui/server.py
set -u
RAG=${RAG_URL:-http://127.0.0.1:8001}
GUARD=${GUARD_URL:-http://127.0.0.1:8003}
WEB=${WEB_URL:-http://127.0.0.1:5050}
pass=0; fail=0

check() {  # usage: <name> <expected-substring> ; body on stdin
  local name="$1" exp="$2" body; body=$(cat)
  if echo "$body" | grep -Eq "$exp"; then
    echo "  ✓ $name"; pass=$((pass + 1))
  else
    echo "  ✗ $name (missing: $exp)"; echo "    got: $(echo "$body" | head -c 200)"; fail=$((fail + 1))
  fi
}

echo "RAG ($RAG)"
curl -s -m 10 "$RAG/health" | check "health" '"status"'
curl -s -m 40 -X POST "$RAG/query" -H 'Content-Type: application/json' \
  -d '{"description":"industrial unit with office in Rishon"}' | check "query returns listing ids" '"id"'

echo "Guardrails ($GUARD)"
curl -s -m 10 "$GUARD/health" | check "health" '"status"'
curl -s -m 40 -X POST "$GUARD/check/input" -H 'Content-Type: application/json' \
  -d '{"text":"4-room apartment in Haifa, renovated kitchen, asking 2.1M NIS"}' | check "valid listing passes" '"pass": *true'
curl -s -m 40 -X POST "$GUARD/check/input" -H 'Content-Type: application/json' \
  -d '{"text":"Buy cheap crypto now, double your money fast"}' | check "spam rejected" '"pass": *false'

echo "WebUI ($WEB)"
curl -s -m 10 "$WEB/api/dashboard" | check "dashboard metrics" '"metrics"'
# NOTE: a successful submit appends to listings.jsonl / events.jsonl (real local data).
curl -s -m 30 -X POST "$WEB/api/submit" -H 'Content-Type: application/json' \
  -d '{"description":"Bright 3-room apartment in Ramat Gan, 95sqm","agent_name":"Smoke","images":[]}' \
  | check "submit returns a brief" '"brief_markdown"'

echo
echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ]
