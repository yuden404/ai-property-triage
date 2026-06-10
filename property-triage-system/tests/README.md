# Tests

Offline, deterministic test suite for the Property Triage services. Every AWS
(boto3), Gemini (google-genai) and Ollama call is **mocked** — the suite needs
no credentials, no network, and no running services.

## Run

```bash
# from property-triage-system/
.venv/bin/python -m pip install -r tests/requirements-dev.txt   # once: installs pytest
.venv/bin/python -m pytest                                       # run everything
.venv/bin/python -m pytest tests/test_guardrails_service.py -v   # one file, verbose
```

## What's covered

| File | Scope |
|------|-------|
| `test_shared.py` | `aws_utils` bounded-timeout config, `client()` wiring, Secrets-Manager key fetch (raw + JSON + cache); `gemini_utils.generate()` arguments + text handling |
| `test_rag_service.py` | `/health`, `/query` (happy / empty input / no results / KB failure → 502), `_listing_id` parsing |
| `test_guardrails_service.py` | both `/check` endpoints, `_parse_json`, `_PII_HINT`, the PII-pass skip optimization (call counts), anonymize-vs-block parsing |
| `test_webui_server.py` | `/`, dashboard, submit (accepted saves / rejected does **not**), chat streaming + Ollama-down 502, `build_messages` language + missing-content guard, listings mtime cache |

How the mocking works: each service fixture patches `shared.aws_utils.client`
to a `MagicMock`, then imports the service fresh — so the module-level Bedrock
client is the fake. Gemini `generate` and the Ollama stream helpers are patched
per test. No real client is ever constructed.

## Live smoke test

`smoke_live.sh` runs the original `curl` checks against **running** services
(needs AWS creds + Ollama). It's not part of `pytest`:

```bash
bash tests/smoke_live.sh
```

## Not yet covered

`agent_service/` and `image_analyser/` are not built yet — tests land when those
services do.
