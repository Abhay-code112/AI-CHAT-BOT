# LiteLLM Multi-Turn Chat

React + FastAPI + SQLite chat app. LiteLLM provides the key pool with automatic failover and the response cache.
Every assistant reply shows its own metadata: resolution source, model, latency, and input/output tokens.

## 1. Live URLs

| | URL |
|---|---|
| Frontend (Vercel) | https://aichatbot-two-beta.vercel.app |
| Backend (Render) | https://ai-chat-bot-9pnb.onrender.com (try [`/health`](https://ai-chat-bot-9pnb.onrender.com/health)) |
| GitHub | https://github.com/Abhay-code112/AI-CHAT-BOT |
| Demo video | https://drive.google.com/drive/folders/1c6bEeWrzyogqso_8PbioZSBJfJHCHnKW?usp=sharing |

> **Cold start:** the backend runs on Render's free tier, which sleeps after about 15 minutes without traffic. The first request after a sleep can take 50 seconds or more. Open `/health` once and wait for `{"status":"ok", ...}` before testing.
>
> The deployed backend has `ENABLE_FAILURE_SIMULATION=true`, and `ALLOWED_ORIGINS` contains the frontend URL.

## 2. Run locally: with Docker and without

### With Docker (needs Docker + make)
```bash
cp .env.example .env        # put at least 2 real keys in LLM_API_KEYS
make up                     # frontend http://localhost:3000, backend http://localhost:8000
make down                   # stop (the named volume `appdata` keeps SQLite + cache)
```
Without `make` (for example on Windows PowerShell): `docker compose up --build -d` and `docker compose down`.
Never run `docker compose down -v` unless you want to delete the stored history, because `-v` removes the volume.

The SQLite file lives inside the Docker volume at `/data/app.db`, not in the project folder. To inspect it:
```bash
docker compose exec backend ls -la /data
docker compose exec backend python -c "import sqlite3; c=sqlite3.connect('/data/app.db'); [print(r) for r in c.execute('select id, session_id, role, substr(content,1,40), created_at from messages order by id desc limit 10')]"
```

### Without Docker
```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
export LLM_API_KEYS=key1,key2 DATABASE_URL=sqlite:///./app.db \
       ALLOWED_ORIGINS=http://localhost:3000 ENABLE_FAILURE_SIMULATION=true
uvicorn app.main:app --port 8000

# frontend (second terminal)
cd frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev          # http://localhost:3000
```
On Windows PowerShell, set variables with `$env:LLM_API_KEYS="key1,key2"` instead of `export`.

### Tests
```bash
cd backend && python -m pytest -q     # 24 tests, LiteLLM runs in mock mode (no network, no keys)
```

### Contract checks
```bash
python scripts/check_keys.py                                   # verifies every key works with LLM_MODEL
python scripts/smoke_test.py --api http://localhost:8000 --origin http://localhost:3000   # 17 contract checks
```
The same smoke test runs against the live deployment:
`python scripts/smoke_test.py --api https://ai-chat-bot-9pnb.onrender.com --origin https://aichatbot-two-beta.vercel.app`

### Environment variables
See `.env.example` (placeholder values only, real keys never go in the repo).

| Variable | Meaning | Default |
|---|---|---|
| `LLM_API_KEYS` | Comma-separated key pool, at least 2. Ids are `key_1`, `key_2`, ... in this order | required |
| `DATABASE_URL` | SQLite location, e.g. `sqlite:////data/app.db` | `sqlite:///./app.db` |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins | `http://localhost:3000` |
| `ENABLE_FAILURE_SIMULATION` | `true` enables the `X-Simulate-Failure` hook | `false` |
| `LLM_MODEL` | LiteLLM model name used for every key | `gemini/gemini-3.5-flash-lite` |
| `CACHE_TYPE` | `disk` (persists under `/data/cache`) or `local` (in-memory) | `disk` |
| `CACHE_DIR` | Cache folder, defaults to a `cache` folder next to the SQLite file | empty |
| `DEFAULT_SYSTEM_PROMPT` | System prompt when the client sends none | concise assistant |
| `REQUEST_TIMEOUT_SECONDS` | Per-key timeout before failing over to the next key | `20` |
| `MAX_TOKENS` | Cap on reply length (thinking tokens also count on Gemini) | `1024` |
| `REASONING_EFFORT` | Optional thinking level (`minimal`, `low`, ...). Empty means the model default | empty |
| `MAX_HISTORY_MESSAGES` | How many stored messages are sent to the model | `50` |

Frontend: `API_URL` (runtime, Docker) or `VITE_API_URL` (build time, Vercel).

### Deploying
Full click-by-click guide: see [`DEPLOY.md`](DEPLOY.md).

- **Backend** (Docker): deployed on Render as a Docker web service with root directory `backend/` (the image honours `PORT`).
  Set `LLM_API_KEYS`, `DATABASE_URL`, `ALLOWED_ORIGINS`, `ENABLE_FAILURE_SIMULATION`, plus `LLM_MODEL`, `REQUEST_TIMEOUT_SECONDS`, `MAX_TOKENS`, `CACHE_TYPE`.
  SQLite needs a **persistent volume mounted at `/data`** to survive restarts (Railway or Fly.io volume, or a paid Render disk).
  The free Render tier has no persistent disk (see the trade-off in section 3).
- **Frontend**: deployed on Vercel with root directory `frontend/`, framework Vite, build command `npm run build`, output `dist`, env `VITE_API_URL=<backend url>`.

## 3. Design decisions: key fallback, caching, SQLite session storage

**Key fallback.** `LLM_API_KEYS` becomes the pool `key_1…key_N` (order preserved). For each request the gateway tries keys in order, passing the key
per call to `litellm.completion(num_retries=0)`. Any failure (429, quota, 5xx, timeout, auth error, empty reply) moves on to the next key and increments `fallback_attempts`.
Each call has a timeout (`REQUEST_TIMEOUT_SECONDS`, default 20 s), so a hanging key fails over quickly instead of freezing the request.
If every key fails the API returns `503 {"detail":"all providers unavailable"}` and **nothing is written to history**. I loop over keys myself
(rather than using the Router) so that `key_id` and `fallback_attempts` are exact, and keys are never logged or returned.

**Caching.** LiteLLM's built-in cache (`CACHE_TYPE=disk` persists under `/data/cache`; `local` is in-memory) is enabled for `litellm.completion`.
Its key is built from the model and the full message list (system prompt + history + new message), and is independent of the API key, so the same first message
in a new session hits the cache, while any different message or history misses. A hit is detected via `response._hidden_params["cache_hit"]` and reported as
`LLM_CACHE` with the stored usage, `key_id=null`, `fallback_attempts=0`. Requests carrying `X-Simulate-Failure` skip the cache *read* so that failover can always be demonstrated.

**Latency and cost controls.** Replies are capped with `MAX_TOKENS`, the model is a fast "flash-lite" model, and an optional `REASONING_EFFORT` setting limits the model's thinking
time. A thinking model was taking 11 to 48 seconds for simple prompts and timing out; switching to `gemini-3.5-flash-lite` brought live calls to about 3 seconds.

**SQLite sessions.** `messages` follows the required schema (+ an index and a `sessions` table that stores the optional per-session system prompt).
The client sends only the new message; the server loads the last `MAX_HISTORY_MESSAGES` rows, calls the model, and writes the user + assistant rows in **one transaction**
after success. The schema is created on startup; the file lives at `DATABASE_URL` (a Docker volume at `/data`) so history survives restarts.
Latency is measured server-side from request start to the model response (DB write excluded), as an integer in ms. Tokens are per request, never cumulative.

**Free-tier trade-off.** Locally (`make up`) the SQLite file and the cache sit on a named Docker volume, so history survives `docker compose down` and `up`.
The deployed backend runs on Render's free tier, which has no persistent disk: the filesystem (history and cache) resets when the instance sleeps, restarts or redeploys.
The same image keeps history unchanged on any host that mounts a volume at `/data`, with no code changes.

**Errors.** Validation is 422 (FastAPI shape); unknown session is 404; all keys failing is 503; any unexpected error is a JSON 500 with no stack trace.

## 4. Verification: how to see failover, cache hits and per-message token usage

### In the UI
Every assistant bubble shows `meta-source`, `meta-model`, `meta-latency`, `meta-input-tokens`, `meta-output-tokens` (plus key and fallback count).
- **Cache hit:** send a message, click **New chat**, send the exact same first message. The second reply shows `LLM_CACHE`, the same text, `key —` and a much lower latency.
- **Failover:** open **Settings**, type `key_1=429` into "Simulate failure", and send a new message. The reply still arrives, answered by `key_2` with `fallbacks 1`.
- **All keys fail:** type `*=429` and send. The chat shows a readable error, and the failed message is not added to history.

### With curl (macOS / Linux / Git Bash)
Replace `$API` with the backend URL.
```bash
curl $API/health

# live inference, then the same first message in a NEW session -> LLM_CACHE (same reply, key_id null)
curl -s $API/chat -H 'Content-Type: application/json' -d '{"session_id":"s-1","message":"What is the capital of France?"}'
curl -s $API/chat -H 'Content-Type: application/json' -d '{"session_id":"s-2","message":"What is the capital of France?"}'

# failover: key_1 fails with 429 -> answered by key_2, fallback_attempts=1
curl -s $API/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: key_1=429' \
     -d '{"session_id":"s-3","message":"Tell me a fun fact"}'

# every key fails -> 503 {"detail":"all providers unavailable"}, nothing stored
curl -si $API/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: *=429' \
     -d '{"session_id":"s-4","message":"hello"}'
curl -s $API/sessions/s-4/messages        # 404 session not found

# multi-turn memory + stored history (with per-message tokens)
curl -s $API/chat -H 'Content-Type: application/json' -d '{"session_id":"s-5","message":"My name is Abhay"}'
curl -s $API/chat -H 'Content-Type: application/json' -d '{"session_id":"s-5","message":"What is my name?"}'
curl -s $API/sessions/s-5/messages
```

### With PowerShell (Windows)
PowerShell mangles quotes inside inline JSON, so put the request body in a file and send it with `curl.exe`:
```powershell
$API = "https://ai-chat-bot-9pnb.onrender.com"
Set-Content -Encoding ascii a1.json '{"session_id":"a1","message":"What is the capital of France?"}'
Set-Content -Encoding ascii a2.json '{"session_id":"a2","message":"What is the capital of France?"}'
Set-Content -Encoding ascii a3.json '{"session_id":"a3","message":"Tell me a fun fact"}'
Set-Content -Encoding ascii a4.json '{"session_id":"a4","message":"hello"}'

curl.exe $API/health

# live inference, then the same message in a NEW session -> LLM_CACHE
curl.exe -X POST $API/chat -H "Content-Type: application/json" --data-binary "@a1.json" -w "\n"
curl.exe -X POST $API/chat -H "Content-Type: application/json" --data-binary "@a2.json" -w "\n"

# failover -> 200, key_id = key_2, fallback_attempts = 1
curl.exe -X POST $API/chat -H "Content-Type: application/json" -H "X-Simulate-Failure: key_1=429" --data-binary "@a3.json" -w "\n"

# every key fails -> 503 {"detail":"all providers unavailable"}, nothing stored
curl.exe -i -X POST $API/chat -H "Content-Type: application/json" -H "X-Simulate-Failure: *=429" --data-binary "@a4.json"
curl.exe -i $API/sessions/a4/messages      # 404 session not found

# stored history with per-message tokens
curl.exe $API/sessions/a1/messages
```

### Automated check
`python scripts/smoke_test.py --api <backend> --origin <frontend>` runs 17 contract checks: health, cache hit and speed, per-request tokens, all `X-Simulate-Failure` cases,
multi-turn memory, history endpoint, 422 validation, malformed JSON, and CORS.

## 5. Setup Used

- Editor: VS Code
- AI tools: Claude (Anthropic) for scaffolding the backend, tests and Docker setup; all code was reviewed and run locally before deploying.
- Pinned stack: Python 3.12, FastAPI, LiteLLM 1.74.9, React 18 + Vite 5, react-markdown 9.0.1, nginx for static serving.
- Model: `gemini/gemini-3.5-flash-lite` through a pool of two Gemini API keys.
- Hosting: Render (backend, Docker) and Vercel (frontend).

## 6. Bonus features (if any)

- Per-session **system prompt** field in the UI (`system-prompt-input`), stored per session on the backend.
- **Simulate failure** box in the UI so failover can be demoed on screen without curl.
- **Markdown rendering** for assistant replies (bold, lists, code blocks).
- **Disk-backed LiteLLM cache** so cache hits survive restarts on hosts with a volume.
- **Latency controls**: per-key timeout, `MAX_TOKENS` cap and an optional `REASONING_EFFORT` setting.
- Runtime frontend config (`API_URL`) so one image works against any backend; non-root backend image with a healthcheck.
- "New chat" button, "not delivered" marker for failed sends, readable error bubbles for 422/503/network errors.
- `scripts/check_keys.py` and `scripts/smoke_test.py` to verify the keys and the full API contract locally or against a live URL.