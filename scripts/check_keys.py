#!/usr/bin/env python3
"""Check your Gemini keys and pick a model that works on ALL of them (stdlib only).

Usage:  python3 scripts/check_keys.py                 # reads LLM_API_KEYS / LLM_MODEL from ./.env
        python3 scripts/check_keys.py --model gemini-3.5-flash-lite
Keys are sent only to Google, in a header, and are never printed.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "https://generativelanguage.googleapis.com/v1beta"


def load_env(path):
    vals = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip("\"'")
    return vals


def api(method, path, key, body=None):
    req = urllib.request.Request(
        BASE + path, method=method, data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except ValueError:
            return e.code, {}
    except Exception as e:  # network problem
        return 0, {"error": {"message": type(e).__name__}}


def list_models(key):
    names, token = [], ""
    while True:
        st, data = api("GET", f"/models?pageSize=200{('&pageToken=' + token) if token else ''}", key)
        if st != 200:
            return st, data, []
        for m in data.get("models", []):
            if "generateContent" in m.get("supportedGenerationMethods", []):
                names.append(m["name"].split("/", 1)[1])
        token = data.get("nextPageToken", "")
        if not token:
            return 200, data, names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=".env")
    ap.add_argument("--model", default="")
    a = ap.parse_args()
    env = load_env(a.env)
    keys = [k.strip() for k in (os.environ.get("LLM_API_KEYS") or env.get("LLM_API_KEYS", "")).split(",") if k.strip()]
    model = (a.model or os.environ.get("LLM_MODEL") or env.get("LLM_MODEL", "")).replace("gemini/", "", 1)
    if len(keys) < 2 or any("your-" in k for k in keys):
        sys.exit("Put at least 2 real keys in LLM_API_KEYS in .env first (comma-separated).")
    print(f"{len(keys)} keys found. Model under test: {model or '(none set)'}\n")

    usable, ok_all = [], True
    for i, key in enumerate(keys, start=1):
        st, data, names = list_models(key)
        if st != 200:
            msg = data.get("error", {}).get("message", "")[:140]
            print(f"key_{i}: FAIL listing models (HTTP {st}) {msg}")
            ok_all = False
            continue
        flash = sorted(n for n in names if n.startswith("gemini") and "flash" in n and "image" not in n
                       and "tts" not in n and "live" not in n and "audio" not in n)
        usable.append(set(flash))
        print(f"key_{i}: key accepted, {len(flash)} text 'flash' models visible")
        if model:
            st, out = api("POST", f"/models/{model}:generateContent", key,
                          {"contents": [{"parts": [{"text": "Reply with the single word: ok"}]}]})
            if st == 200:
                print(f"        {model}: WORKS")
            else:
                err = out.get("error", {})
                print(f"        {model}: FAIL HTTP {st} {err.get('status', '')} {err.get('message', '')[:140]}")
                ok_all = False
    if usable:
        common = sorted(set.intersection(*usable))
        print("\nFlash models visible to ALL keys (newest names usually sort last):")
        for n in common:
            print("   gemini/" + n)
        print("\nSet LLM_MODEL=gemini/<name> in .env, then re-run this script with that model to confirm.")
    sys.exit(0 if ok_all and model else 1)


if __name__ == "__main__":
    main()
