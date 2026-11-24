import json

import litellm
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import chat, make_settings


# ---------- /health ----------
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


def test_health_503_without_keys(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, api_keys=[]))) as c:
        r = c.get("/health")
        assert r.status_code == 503 and "detail" in r.json()


# ---------- /chat contract ----------
def test_chat_contract_fields(client):
    r = chat(client, "s-1", "What is the capital of France?")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == "s-1" and body["reply"]
    m = body["metadata"]
    assert m["resolution_source"] == "LIVE_INFERENCE"
    assert isinstance(m["model"], str) and m["model"]
    assert isinstance(m["latency_ms"], int) and m["latency_ms"] >= 0
    assert isinstance(m["input_tokens"], int) and isinstance(m["output_tokens"], int)
    assert m["key_id"] == "key_1"
    assert m["fallback_attempts"] == 0


def test_message_is_trimmed(client):
    r = chat(client, "s-trim", "   hello   ")
    assert r.status_code == 200
    msgs = client.get("/sessions/s-trim/messages").json()["messages"]
    assert msgs[0]["content"] == "hello"


# ---------- caching ----------
def test_cache_hit_in_new_session_and_different_message_misses(client):
    first = chat(client, "s-a", "What is the capital of France?").json()
    second = chat(client, "s-b", "What is the capital of France?").json()
    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    m = second["metadata"]
    assert m["resolution_source"] == "LLM_CACHE"
    assert second["reply"] == first["reply"]
    assert m["key_id"] is None and m["fallback_attempts"] == 0
    assert m["input_tokens"] == first["metadata"]["input_tokens"]
    assert m["output_tokens"] == first["metadata"]["output_tokens"]
    other = chat(client, "s-c", "What is the capital of Spain?").json()
    assert other["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert other["reply"] != first["reply"]


def test_cache_key_includes_history_and_system_prompt(client):
    chat(client, "s-h1", "hi")
    again = chat(client, "s-h1", "hi").json()  # same text, but history differs -> not cached
    assert again["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    p1 = chat(client, "s-p1", "hello", system_prompt="Be a pirate").json()
    p2 = chat(client, "s-p2", "hello", system_prompt="Be a poet").json()
    assert p2["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert p1["metadata"]["resolution_source"] == "LIVE_INFERENCE"


# ---------- multi-turn + history endpoint ----------
def test_multi_turn_history_and_isolation(client):
    r1 = chat(client, "s-m", "first").json()
    r2 = chat(client, "s-m", "second").json()
    assert "context=2 messages" in r1["reply"]  # system + user
    assert "context=4 messages" in r2["reply"]  # system + user + assistant + user
    other = chat(client, "s-other", "unrelated").json()
    assert "context=2 messages" in other["reply"]

    hist = client.get("/sessions/s-m/messages").json()
    assert hist["session_id"] == "s-m"
    assert [m["role"] for m in hist["messages"]] == ["user", "assistant", "user", "assistant"]
    assert hist["messages"][0]["content"] == "first"
    assert all(m["role"] != "system" for m in hist["messages"])
    meta = hist["messages"][1]["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE" and "input_tokens" in meta and "output_tokens" in meta
    assert "metadata" not in hist["messages"][0]


def test_unknown_session_404(client):
    r = client.get("/sessions/nope/messages")
    assert r.status_code == 404 and r.json() == {"detail": "session not found"}


def test_history_survives_restart(settings):
    with TestClient(create_app(settings)) as c1:
        chat(c1, "s-persist", "remember me")
    with TestClient(create_app(settings)) as c2:
        msgs = c2.get("/sessions/s-persist/messages").json()["messages"]
        assert msgs[0]["content"] == "remember me"


def test_system_prompt_override_is_sent_and_sticky(client, monkeypatch):
    seen = []
    from app import llm

    original = llm.LLMGateway._complete

    def spy(self, api_key, messages, bypass_cache):
        seen.append(messages)
        return original(self, api_key, messages, bypass_cache)

    monkeypatch.setattr(llm.LLMGateway, "_complete", spy)
    chat(client, "s-sys", "one", system_prompt="You are a pirate")
    chat(client, "s-sys", "two")  # no prompt -> session keeps the override
    assert seen[0][0] == {"role": "system", "content": "You are a pirate"}
    assert seen[1][0] == {"role": "system", "content": "You are a pirate"}
    chat(client, "s-default", "hi")
    assert seen[2][0]["content"] != "You are a pirate"


# ---------- validation (422) ----------
def test_validation_errors_are_422_and_not_stored(client):
    bad = [
        {"session_id": "s-v", "message": ""},
        {"session_id": "s-v", "message": "    "},
        {"session_id": "s-v", "message": "x" * 4001},
        {"session_id": "s-v"},
        {"message": "hi"},
        {"session_id": "", "message": "hi"},
        {"session_id": "s" * 129, "message": "hi"},
    ]
    for payload in bad:
        r = client.post("/chat", json=payload)
        assert r.status_code == 422, payload
        assert "detail" in r.json()
    assert client.get("/sessions/s-v/messages").status_code == 404


def test_max_length_message_ok_and_malformed_json(client):
    assert chat(client, "s-long", "x" * 4000).status_code == 200
    r = client.post("/chat", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and "detail" in r.json()


# ---------- failover ----------
def test_simulated_failover(client):
    r = chat(client, "s-f1", "failover please", headers={"X-Simulate-Failure": "key_1=429"})
    assert r.status_code == 200
    m = r.json()["metadata"]
    assert m["key_id"] == "key_2" and m["fallback_attempts"] == 1
    assert m["resolution_source"] == "LIVE_INFERENCE"
    r = chat(client, "s-f2", "failover 500", headers={"X-Simulate-Failure": "key_1=500"})
    assert r.status_code == 200 and r.json()["metadata"]["key_id"] == "key_2"


def test_all_keys_fail_returns_503_and_nothing_stored(client):
    for header in ("*=429", "key_1=429,key_2=500", "*=503"):
        r = chat(client, "s-503", "will fail", headers={"X-Simulate-Failure": header})
        assert r.status_code == 503
        assert r.json() == {"detail": "all providers unavailable"}
    assert client.get("/sessions/s-503/messages").status_code == 404


def test_simulation_bypasses_cache(client):
    chat(client, "s-x", "cached text")  # warms the cache
    r = chat(client, "s-y", "cached text", headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 503  # providers are really exercised, not answered from cache


def test_simulation_header_ignored_when_disabled(tmp_path):
    s = make_settings(tmp_path, enable_failure_simulation=False)
    with TestClient(create_app(s)) as c:
        r = chat(c, "s-off", "hi", headers={"X-Simulate-Failure": "*=429"})
        assert r.status_code == 200 and r.json()["metadata"]["key_id"] == "key_1"


def test_real_provider_errors_fail_over(client, monkeypatch):
    from app import llm

    original = llm.LLMGateway._complete

    def flaky(self, api_key, messages, bypass_cache):
        if api_key == "sk-test-1":
            raise litellm.RateLimitError("quota", "gemini", "m")
        return original(self, api_key, messages, bypass_cache)

    monkeypatch.setattr(llm.LLMGateway, "_complete", flaky)
    r = chat(client, "s-real", "hello")
    assert r.status_code == 200
    assert r.json()["metadata"]["key_id"] == "key_2"
    assert r.json()["metadata"]["fallback_attempts"] == 1

    def dead(self, api_key, messages, bypass_cache):
        raise litellm.InternalServerError("boom", "gemini", "m")

    monkeypatch.setattr(llm.LLMGateway, "_complete", dead)
    r = chat(client, "s-real2", "hello again")
    assert r.status_code == 503 and r.json() == {"detail": "all providers unavailable"}


def test_unexpected_error_is_json_not_traceback(settings, monkeypatch):
    from app import db as dbmod

    monkeypatch.setattr(dbmod.Database, "save_exchange", lambda *a, **k: 1 / 0)
    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        r = chat(c, "s-boom", "hi")
        assert r.status_code == 500 and r.json() == {"detail": "internal server error"}
        assert "Traceback" not in r.text


# ---------- misc ----------
def test_cors_preflight_allows_custom_header(client):
    r = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-simulate-failure",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_api_key_never_in_responses(client):
    r = chat(client, "s-k", "hi")
    assert "sk-test" not in r.text
    assert "sk-test" not in json.dumps(client.get("/sessions/s-k/messages").json())
