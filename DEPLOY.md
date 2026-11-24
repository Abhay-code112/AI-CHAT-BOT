# From zip to submission: step by step

## 0. What you need
- 2 Gemini API keys from **different Google accounts/projects** (https://aistudio.google.com/app/apikey). Two keys from one project share one quota, so failover would not help in real life.
- A GitHub account, Docker Desktop, `make`, Node 20+ (Node only if you run without Docker).
- Accounts for hosting: Railway (backend, needs a volume) and Vercel or Netlify (frontend).

## 1. Check it works locally (10 min)
```bash
cp .env.example .env            # edit: LLM_API_KEYS=key_a,key_b
make up                         # first build takes a few minutes
```
Open http://localhost:3000, send a message, send the same first message in "New chat" (expect LLM_CACHE).
Then: `make smoke` (runs 17 contract checks against http://localhost:8000).
**Pick and verify the model before anything else** (Google retired `gemini-2.0-flash` on June 1, 2026, so old examples fail):
```bash
make check-keys        # lists the Gemini models each key can use and test-calls LLM_MODEL on every key
```
Pick a flash model that works on ALL keys, set `LLM_MODEL=gemini/<name>` in `.env`, run `make check-keys` again until both keys say WORKS, then `make up`.
Use the exact same `LLM_MODEL` value in Railway later.

## 2. Push to GitHub (public)
```bash
git init && git add . && git commit -m "LiteLLM multi-turn chat"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```
Check on GitHub that `.env` is NOT in the repo (it is gitignored) and `.env.example` is.

## 3. Deploy the backend (Railway)
1. railway.com -> New Project -> Deploy from GitHub repo -> pick the repo.
2. Service -> Settings -> **Root Directory = `backend`** (Railway then builds `backend/Dockerfile`).
3. Service -> Variables:
   - `LLM_API_KEYS` = `key_a,key_b`
   - `ENABLE_FAILURE_SIMULATION` = `true`
   - `ALLOWED_ORIGINS` = `*` for now (you tighten it in step 5)
   - `DATABASE_URL` = `sqlite:////data/app.db`   (four slashes)
4. Service -> **Volumes -> add a volume, mount path `/data`**. This is what makes history survive restarts.
5. Settings -> Networking -> **Generate Domain**.
6. Open `https://<backend-domain>/health` -> `{"status":"ok","keys_configured":2,"cache_enabled":true}`.

Free-tier reality (checked Nov 2026, prices change, confirm on the provider's page): Fly.io gives new accounts only a short trial and then needs a card; Render's free web services have **no persistent disk** and spin down when idle. Railway has a trial credit and then a paid Hobby plan. If you cannot attach a volume at all, deploy on Render free instead (same settings, Docker, root dir `backend`), and write in the README that history is lost on restart/spin-down. That loses some "SQLite survives restarts" credit but everything else still works.

## 4. Deploy the frontend (Vercel)
1. vercel.com -> Add New Project -> import the repo.
2. **Root Directory = `frontend`**; framework preset Vite (build `npm run build`, output `dist`).
3. Environment variable: `VITE_API_URL` = `https://<backend-domain>` (no trailing slash).
4. Deploy. Netlify/Cloudflare Pages work the same way.

## 5. Connect them (CORS)
In Railway set `ALLOWED_ORIGINS` = `https://<your-app>.vercel.app` (exact origin, no trailing slash, no path) and let the service redeploy.

## 6. Verify the live deployment
```bash
make smoke API=https://<backend-domain> ORIGIN=https://<frontend-domain>
```
All 17 checks should pass (needs 2 working keys; if the 'remember' check fails with a real model, just re-run once, it is an LLM answer).
Then open the frontend in an **incognito window** (no login anywhere) and chat. Also check that the page loads on your phone.
If you used a volume: in Railway press Restart on the backend, then reload `/sessions/<id>/messages` for an old session, it should still return the history.

## 7. Fill in the README placeholders
Live URLs, GitHub link, demo video link, your editor and AI tools (section 5). Commit and push.

## 8. Record the demo (2-3 min, voice on, deployed app)
1. (0:00) Show frontend URL + backend `/health` in the browser. Say what you built.
2. (0:20) Chat: "What is the capital of France?" then a follow-up that needs memory ("And what is its population?"). Point at source/model/latency/tokens on each reply.
3. (1:00) Click New chat, send the exact first message again -> **LLM_CACHE**, same text, lower latency.
4. (1:25) Settings -> Simulate failure `key_1=429` -> send a new message -> still answers; show it in the terminal too:
   `curl -s $API/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: key_1=429' -d '{"session_id":"demo","message":"hi"}'` -> `key_id: key_2`, `fallback_attempts: 1`.
5. (1:55) `*=429` -> readable error in the UI, `503 all providers unavailable` in curl.
6. (2:20) Open `$API/sessions/<id>/messages` to show stored history; mention SQLite on a volume and the repo.
Keep it under 3:00.

## 9. Submit
Public frontend URL, public backend URL, public GitHub repo, demo video link (make it viewable without login), then the submission form. Do it with margin before the 24 h limit.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Browser console: CORS error | `ALLOWED_ORIGINS` must equal the frontend origin exactly; restart backend |
| Every chat is 503 | Keys wrong/quota, or the model name is retired. Check Railway logs, change `LLM_MODEL` |
| `/health` fails, logs show "unable to open database" | Volume not mounted at `/data`, or `DATABASE_URL` wrong |
| Frontend calls localhost:8000 in production | `VITE_API_URL` was not set at build time; set it and redeploy |
| First request after idle takes ~1 min | Free host cold start; open `/health` before recording |
