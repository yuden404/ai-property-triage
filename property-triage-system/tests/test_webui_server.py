"""WebUI (Flask) tests — Ollama, n8n and storage mocked; the mock-brief path is offline."""
import sys
from types import SimpleNamespace

import pytest
import requests


@pytest.fixture
def web(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)  # the submit mock sleeps 0.4s — skip it
    sys.modules.pop("server", None)
    import server as s
    s.app.config.update(TESTING=True)
    return SimpleNamespace(client=s.app.test_client(), module=s)


def test_index_ok(web):
    assert web.client.get("/").status_code == 200


def test_dashboard_shape(web):
    d = web.client.get("/api/dashboard").get_json()
    assert {"metrics", "recent", "routing"} <= d.keys()
    assert "total" in d["metrics"]


def test_submit_requires_description(web):
    assert web.client.post("/api/submit", json={"description": "  "}).status_code == 400


def test_submit_accepted_saves_and_has_real_exec_ms(web, monkeypatch):
    saved, logged = [], []
    monkeypatch.setattr(web.module, "save_listing", lambda *a, **k: saved.append(a))
    monkeypatch.setattr(web.module, "log_event", lambda *a, **k: logged.append(a))
    r = web.client.post("/api/submit",
                        json={"description": "3-room flat in Givatayim", "agent_name": "Dana", "images": []})
    assert r.status_code == 200
    d = r.get_json()
    assert d["status"] == "ok"
    assert d.get("brief_markdown")
    assert "brief_html" not in d            # severe #1: no server-rendered HTML
    assert isinstance(d["exec_ms"], int) and d["exec_ms"] != 4200  # M2: measured, not the canned value
    assert len(saved) == 1 and len(logged) == 1


def test_submit_rejected_is_logged_but_not_saved(web, monkeypatch):
    saved, logged = [], []
    monkeypatch.setattr(web.module, "save_listing", lambda *a, **k: saved.append(a))
    monkeypatch.setattr(web.module, "log_event", lambda *a, **k: logged.append(a))
    monkeypatch.setattr(web.module, "submit_listing", lambda payload: ({"status": "rejected", "reason": "spam"}, 12))
    r = web.client.post("/api/submit", json={"description": "buy crypto now"})
    assert r.get_json()["status"] == "rejected"
    assert len(logged) == 1   # dashboard still records the rejection
    assert saved == []        # severe #3: rejected input never enters the grounding store


def test_chat_streams_ollama(web, monkeypatch):
    # Mock only the HTTP boundary so the real open→iter path runs (covers the
    # connect-before-stream ordering rather than stubbing it away).
    class FakeResp:
        def raise_for_status(self): pass
        def iter_lines(self):
            yield b'{"message":{"content":"Hello"}}'
            yield b'{"message":{"content":" world"},"done":true}'
    monkeypatch.setattr(web.module.requests, "post", lambda *a, **k: FakeResp())
    monkeypatch.setattr(web.module, "listings_context", lambda *a, **k: "")
    r = web.client.post("/api/chat", json={"history": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert r.data == b"Hello world"


def test_chat_midstream_failure_is_graceful(web, monkeypatch):
    # Ollama drops AFTER connect succeeds — the stream must end with a notice,
    # not abort unhandled (review #2).
    class FakeResp:
        def raise_for_status(self): pass
        def iter_lines(self):
            yield b'{"message":{"content":"partial"}}'
            raise requests.exceptions.ChunkedEncodingError("dropped")
    monkeypatch.setattr(web.module.requests, "post", lambda *a, **k: FakeResp())
    monkeypatch.setattr(web.module, "listings_context", lambda *a, **k: "")
    r = web.client.post("/api/chat", json={"history": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert b"partial" in r.data and b"interrupted" in r.data


def test_chat_ollama_down_returns_502(web, monkeypatch):
    def boom(messages):
        raise requests.exceptions.ConnectionError("no ollama")
    monkeypatch.setattr(web.module, "open_ollama_stream", boom)
    monkeypatch.setattr(web.module, "listings_context", lambda *a, **k: "")
    r = web.client.post("/api/chat", json={"history": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 502          # severe #7: clean error, client won't store a fake turn
    assert "error" in r.get_json()


def test_build_messages_language_and_keyguard(web, monkeypatch):
    monkeypatch.setattr(web.module, "listings_context", lambda *a, **k: "")
    assert "English" in web.module.build_messages([{"role": "user", "content": "hello"}])[0]["content"]
    assert "Hebrew" in web.module.build_messages([{"role": "user", "content": "שלום, יש דירות?"}])[0]["content"]
    # M1: a user turn with no 'content' key must not raise KeyError
    assert isinstance(web.module.build_messages([{"role": "user"}]), list)


def test_listings_context_formatting(web, monkeypatch):
    monkeypatch.setattr(web.module, "load_listings", lambda: (
        [{"property_type": "apartment", "location": "Haifa", "agent": "Dana", "description": "bright flat"}], False))
    ctx = web.module.listings_context()
    assert "Listing 1" in ctx and "bright flat" in ctx


def test_read_jsonl_cached_copies_and_invalidates(web, tmp_path):
    s = web.module
    p = tmp_path / "x.jsonl"
    p.write_text('{"a": 1}\n', encoding="utf-8")
    cache = {"sig": None, "data": []}
    assert s._read_jsonl_cached(p, cache) == [{"a": 1}]
    s._read_jsonl_cached(p, cache).append({"x": 9})          # mutate the result…
    assert s._read_jsonl_cached(p, cache) == [{"a": 1}]       # …cache stays intact (copy returned)
    p.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")
    assert s._read_jsonl_cached(p, cache) == [{"a": 1}, {"b": 2}]  # changed → re-read
