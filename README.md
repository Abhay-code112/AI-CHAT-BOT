# LiteLLM Multi-Turn Chat

React + FastAPI + SQLite chat app. LiteLLM provides the key pool with automatic failover and the response cache.
Every assistant reply shows its own metadata: resolution source, model, latency, and input/output tokens.

## 1. Live URLs

| | URL |
|---|---|
| Frontend | `https://<your-frontend-url>` |
| Backend | `https://<your-backend-url>` (try `/health`) |
| GitHub | `https://github.com/<you>/<repo>` |
| Demo video | `<link>` |

> Fill these in after deploying. The backend must have `ENABLE_FAILURE_SIMULATION=true`, and `ALLOWED_ORIGINS` must contain the frontend URL.

## 2. Run locally: with Docker and without

### With Docker (needs Docker + make)
```bash
cp .env.example .env        # put at least 2 real keys in LLM_API_KEYS
make up                     # frontend http://localhost:3000, backend http://localhost:8000
make down                   # stop (the named volume `appdata` keeps SQLite + cache)
```

### Without Docker
```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export LLM_API_KEYS=key1,key2 DATABASE_URL=sqlite:///./app.db \
       ALLOWED_ORIGINS=http://localhost:3000 ENABLE_FAILURE_SIMULATION=true
uvicorn app.main:app --port 8000

# frontend (second terminal)
cd frontend
npm install
VITE_API_URL=http://localhost:8000 npm run dev      # http://localhost:3000
```

### Tests
```bash
cd backend && python -m pytest -q     # 24 tests, LiteLLM runs in mock mode (no network, no keys)
```

### Environment variables
See `.env.example`. Backend: `LLM_API_KEYS`, `DATABASE_URL`, `ALLOWED_ORIGINS`, `ENABLE_FAILURE_SIMULATION`, plus
`LLM_MODEL`, `CACHE_TYPE`, `CACHE_DIR`, `DEFAULT_SYSTEM_PROMPT`, `REQUEST_TIMEOUT_SECONDS`, `MAX_HISTORY_MESSAGES`.
Frontend: `API_URL` (runtime, Docker) or `VITE_API_URL` (build time, Vercel/Netlify).

### Deploying
Full click-by-click guide: see [`DEPLOY.md`](DEPLOY.md). `make smoke API=<backend> ORIGIN=<frontend>` verifies a live deployment against the contract.

- **Backend** (Docker): deploy `backend/` with its Dockerfile (it honours `PORT`). Set the four required env vars. SQLite needs a
  **persistent volume mounted at `/data`** (Railway volume, Fly.io volume, or a paid Render disk). Render's free web services have no persistent disk
  and Render says their filesystem resets on restart/redeploy, so history would not survive there. Without a volume the app still works, but history and the disk cache are ephemeral.
- **Frontend**: Vercel/Netlify/Cloudflare Pages with root `frontend/`, build command `npm run build`, output `dist`, env `VITE_API_URL=<backend url>`.

## 3. Design decisions: key fallback, caching, SQLite session storage

**Key fallback.** `LLM_API_KEYS` becomes the pool `key_1…key_N` (order preserved). For each request the gateway tries keys in order, passing the key
per call to `litellm.completion(num_retries=0)`. Any failure (429, quota, 5xx, timeout, auth error, empty reply) moves on to the next key and increments `fallback_attempts`.
If every key fails the API returns `503 {"detail":"all providers unavailable"}` and **nothing is written to history**. I loop over keys myself
(rather than using the Router) so that `key_id` and `fallback_attempts` are exact, and keys are never logged or returned.

**Caching.** LiteLLM's built-in cache (`CACHE_TYPE=disk` persists under `/data/cache`; `local` is in-memory) is enabled for `litellm.completion`.
Its key is built from the model and the full message list (system prompt + history + new message), and is independent of the API key, so the same first message
in a new session hits the cache, while any different message or history misses. A hit is detected via `response._hidden_params["cache_hit"]` and reported as
`LLM_CACHE` with the stored usage, `key_id=null`, `fallback_attempts=0`. Requests carrying `X-Simulate-Failure` skip the cache *read* so that failover can always be demonstrated.

**SQLite sessions.** `messages` follows the required schema (+ an index and a `sessions` table that stores the optional per-session system prompt).
The client sends only the new message; the server loads the last `MAX_HISTORY_MESSAGES` rows, calls the model, and writes the user + assistant rows in **one transaction**
after success. The schema is created on startup; the file lives at `DATABASE_URL` (a Docker volume at `/data`) so history survives restarts.
Latency is measured server-side from request start to the model response (DB write excluded), as an integer in ms. Tokens are per request, never cumulative.

**Errors.** Validation is 422 (FastAPI shape); unknown session is 404; all keys failing is 503; any unexpected error is a JSON 500 with no stack trace.

## 4. Verification: how to see failover, cache hits and per-message token usage

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
In the UI every assistant bubble shows `meta-source`, `meta-model`, `meta-latency`, `meta-input-tokens`, `meta-output-tokens` (plus key and fallback count).
Open **Settings** in the UI to set a system prompt or type `key_1=429` / `*=429` into the "Simulate failure" box to demo failover without curl.

## 5. Setup Used

- Editor: `<your editor, e.g. VS Code>`
- AI tools: `<e.g. Claude for scaffolding the backend, tests and Docker setup>`; all code was reviewed and run locally before deploying.
- Pinned stack: Python 3.12, FastAPI, LiteLLM 1.74.9, React 18 + Vite 5, nginx for static serving.

## 6. Bonus features (if any)

- Per-session **system prompt** field in the UI (`system-prompt-input`), stored per session on the backend.
- **Simulate failure** box in the UI so failover can be demoed on screen.
- **Disk-backed LiteLLM cache** so cache hits survive restarts.
- Runtime frontend config (`API_URL`) so one image works against any backend; non-root backend image with a healthcheck.
- "New chat" button, "not delivered" marker for failed sends, readable error bubbles for 422/503/network errors.
