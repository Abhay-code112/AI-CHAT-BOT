#!/usr/bin/env python3
"""Black-box check of a running backend against the assignment contract (stdlib only).

Usage:
  python3 scripts/smoke_test.py --api https://your-backend --origin https://your-frontend
Needs ENABLE_FAILURE_SIMULATION=true on the target and >= 2 working keys (it makes ~8 real LLM calls).
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid

results = []


def call(method, url, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text else None), dict(r.headers)
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        try:
            return e.code, json.loads(text), dict(e.headers)
        except ValueError:
            return e.code, text, dict(e.headers)


def check(name, ok, detail=""):
    ok = bool(ok)
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not ok else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--origin", default="")
    a = ap.parse_args()
    api = a.api.rstrip("/")
    run = uuid.uuid4().hex[:8]

    # free hosts sleep when idle, so allow a cold start (up to ~90 s) before the first check
    import time
    st, body = 0, None
    for _ in range(30):
        try:
            st, body, _ = call("GET", f"{api}/health")
            if st == 200:
                break
        except Exception:
            pass
        time.sleep(3)
    check("GET /health -> 200 with status/keys_configured/cache_enabled",
          st == 200 and body.get("status") == "ok" and body.get("keys_configured", 0) >= 2 and body.get("cache_enabled") is True, body)

    msg = f"Reply with one short sentence about the number {run}."
    st, r1, _ = call("POST", f"{api}/chat", {"session_id": f"smoke-{run}-1", "message": msg})
    m1 = (r1 or {}).get("metadata", {}) if st == 200 else {}
    need = {"resolution_source", "model", "latency_ms", "input_tokens", "output_tokens", "key_id", "fallback_attempts"}
    check("POST /chat -> 200 with full metadata, LIVE_INFERENCE, key_1",
          st == 200 and need <= set(m1) and m1["resolution_source"] == "LIVE_INFERENCE" and m1["key_id"] == "key_1"
          and isinstance(m1["latency_ms"], int) and r1["reply"], r1)
    check("input/output tokens are positive integers", m1.get("input_tokens", 0) > 0 and m1.get("output_tokens", 0) > 0, m1)

    st, r2, _ = call("POST", f"{api}/chat", {"session_id": f"smoke-{run}-2", "message": msg})
    m2 = (r2 or {}).get("metadata", {}) if st == 200 else {}
    check("same first message, NEW session -> LLM_CACHE, identical reply, key_id null, fallback 0",
          st == 200 and m2.get("resolution_source") == "LLM_CACHE" and r2["reply"] == r1["reply"]
          and m2["key_id"] is None and m2["fallback_attempts"] == 0, r2)
    check("cache hit tokens match the stored usage", m2.get("input_tokens") == m1.get("input_tokens")
          and m2.get("output_tokens") == m1.get("output_tokens"), (m1, m2))
    check("cache hit faster than live call", m2.get("latency_ms", 10**9) < m1.get("latency_ms", 0),
          f"live={m1.get('latency_ms')}ms cache={m2.get('latency_ms')}ms")

    st, r3, _ = call("POST", f"{api}/chat", {"session_id": f"smoke-{run}-3", "message": f"Name one color. Seed {run}-b"})
    check("different message is NOT served from cache", st == 200 and r3["metadata"]["resolution_source"] == "LIVE_INFERENCE", r3)

    st, r4, _ = call("POST", f"{api}/chat", {"session_id": f"smoke-{run}-4", "message": f"Say hi. Seed {run}-c"},
                     {"X-Simulate-Failure": "key_1=429"})
    check("X-Simulate-Failure: key_1=429 -> 200 answered by key_2, fallback_attempts=1",
          st == 200 and r4["metadata"]["key_id"] == "key_2" and r4["metadata"]["fallback_attempts"] == 1, r4)
    st, r5, _ = call("POST", f"{api}/chat", {"session_id": f"smoke-{run}-5", "message": f"Say hi. Seed {run}-d"},
                     {"X-Simulate-Failure": "key_1=500"})
    check("X-Simulate-Failure: key_1=500 -> answered by key_2", st == 200 and r5["metadata"]["key_id"] == "key_2", r5)

    sid = f"smoke-{run}-6"
    st, r6, _ = call("POST", f"{api}/chat", {"session_id": sid, "message": "hello"}, {"X-Simulate-Failure": "*=429"})
    check("X-Simulate-Failure: *=429 -> 503 {'detail': 'all providers unavailable'}",
          st == 503 and r6 == {"detail": "all providers unavailable"}, (st, r6))
    st, r7, _ = call("GET", f"{api}/sessions/{sid}/messages")
    check("failed request not stored (session 404)", st == 404 and r7 == {"detail": "session not found"}, (st, r7))

    sid = f"smoke-{run}-7"
    call("POST", f"{api}/chat", {"session_id": sid, "message": f"My favourite number is {run}. Remember it."})
    st, t2, _ = call("POST", f"{api}/chat", {"session_id": sid, "message": "What is my favourite number?"})
    check("multi-turn: bot remembers earlier message (reply contains the number)", st == 200 and run in t2["reply"], t2)
    st, h, _ = call("GET", f"{api}/sessions/{sid}/messages")
    roles = [m["role"] for m in h["messages"]] if st == 200 else []
    check("GET /sessions/{id}/messages -> 4 messages oldest first, no system prompt",
          roles == ["user", "assistant", "user", "assistant"] and h["session_id"] == sid, h)
    check("assistant history items carry metadata (source + tokens)",
          st == 200 and all({"resolution_source", "input_tokens", "output_tokens"} <= set(m["metadata"]) for m in h["messages"] if m["role"] == "assistant"))

    bad = [{"session_id": "x", "message": ""}, {"session_id": "x", "message": "   "}, {"session_id": "x", "message": "a" * 4001},
           {"message": "hi"}, {"session_id": "x"}]
    codes = [call("POST", f"{api}/chat", b)[0] for b in bad]
    check("validation: empty/blank/too long/missing fields -> 422", codes == [422] * len(bad), codes)
    st, b, _ = call("POST", f"{api}/chat", raw=b"{not json")
    check("malformed JSON -> 422 JSON body", st == 422 and isinstance(b, dict) and "detail" in b, (st, b))

    if a.origin:
        req = urllib.request.Request(f"{api}/chat", method="OPTIONS", headers={
            "Origin": a.origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-simulate-failure"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                allow = r.headers.get("access-control-allow-origin")
        except urllib.error.HTTPError as e:
            allow = e.headers.get("access-control-allow-origin")
        check(f"CORS allows frontend origin {a.origin}", allow in (a.origin, "*"), allow)

    ok = sum(results)
    print(f"\n{ok}/{len(results)} checks passed")
    sys.exit(0 if ok == len(results) else 1)


if __name__ == "__main__":
    main()
