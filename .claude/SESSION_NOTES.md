# Session notes — universal-docker-setup-8euO6

Working branch: `claude/universal-docker-setup-8euO6`
Target host: **Pixel 10 Pro (aarch64)** — every component must be arm64-compatible.

## End-to-end pipeline (one `docker compose up`)

1. **ibga manager** starts. IBKR fires an IBKey 2FA push to the phone.
   - Watchdog in `docker-entrypoint.sh` re-sends the push every `TWO_FA_WINDOW` seconds (default 120) up to `MAX_2FA_RETRIES` times (default 5) by killing + restarting `/opt/ibga/manager.sh`.
   - VNC / noVNC are patched out of `/opt/ibga/_run_xv.sh` (fully headless).
2. **Ollama** + **ngrok** + **Colab executor** start while the user is approving 2FA.
   - Ollama listens on `0.0.0.0:11434`, pre-pulls models named in `$OLLAMA_MODELS`.
   - ngrok takes auth from `$NGROK_AUTHTOKEN`, optional `$NGROK_DOMAIN`, exposes `:4040` inspector.
   - Colab executor (`/app/colab_executor.py`) runs Flask on `:5000`; exposes `/colab-setup` HTML for one-time Colab cookie paste from the phone, persists `colab_state.json` for headless Playwright.
3. Entrypoint waits on `localhost:4001` (IBKR API opens once 2FA approved), then `exec`s uvicorn.
4. **Backend** (FastAPI on `:8000`). `POST /api/scan` fans out Polymarket + PredictIt + IBKR in parallel (`asyncio.gather`). IBKR fetcher already does 2 connect attempts → matches the "2 searches" behavior.
5. After fetches + 6 min IBKR settle window, `scanner.py` uploads `Cloud_GPU_Matcher_v4_Stable.ipynb` to a GitHub Gist with the ngrok wss:// URL **JSON-safely rewritten** into the `WS_URL_PLACEHOLDER` line. Posts gist ID to the Colab executor.
6. Colab executor opens the gist URL in headless Chromium (Playwright + persisted auth), hits Ctrl+F9 (Run All) and a backup menu click. Notebook also has an auto-execute trigger cell.
7. Notebook on T4 connects to `wss://<ngrok>/ws`, sends `subscribe markets`, receives full market dump, runs **bge-m3** bi-encoder + **bge-reranker-v2-m3** with pure torch `util.cos_sim` (no faiss-gpu, no spacy — these were intentionally removed for T4 stability). Sends top 2000 back as `cloud_results`.
8. Backend's `/ws` handler calls `set_cloud_results` → `_all_opportunities` is populated.

## v4-stable vs PMAS v4 — keep local

- LOCAL: pure torch + regex filters + WebSocket transport + auto-execute trigger.
- PMAS: faiss-gpu + spacy + HTTP polling to a hardcoded Tailscale host.
- LOCAL is strictly more stable on Colab T4. Don't port PMAS's notebook back.

## Bug fixes already landed

- `main.py`: `get_all_markets` → `get_cached_markets` (function name didn't exist).
- `websocket_colab.receive_results`: was double-receiving from socket → deadlock; now takes pre-dispatched data.
- `scanner.py`: `WS_URL` injection was raw text replace → produced invalid JSON on Gist; now parses the .ipynb as JSON and rewrites the placeholder line.
- Polymarket `PAGE_SIZE` 500 → 100 (Gamma API silently truncates pages > 100).
- Added `/api/health` for the Docker HEALTHCHECK.
- Added `python-multipart` for FastAPI multipart endpoints (used by `/colab-setup/upload-state`).

## Audit of `Bamove6969/Prediction_Market_Arbitrage_System` (PMAS)

Two full-pass audits done. 17 backend/engine/root Python files are byte-identical between the two repos. Already ported every meaningful win. Notebook stays local.

## What this session is implementing right now

- 2× parallel Ollama workers (`gemma4:31b-cloud`), each takes 1000 of the 2000 Colab matches.
- Strict binary-only filter (≤ 2 outcomes; reject anything else — already mostly in `_verify_binary_pair`).
- Auto-fire LLM verification when `set_cloud_results` lands.
- Comprehensive HTML report: full question text from both platforms, both answers with %, clickable market URLs. Served from a FastAPI endpoint so it's reachable through the ngrok tunnel.

## Open / deferred

- Pixel-side local Ollama hand-off (the *phone's* Ollama processing the report). Explicitly deferred by user — wire later once data arrives.
- `backend/orchestrator.py` has a stale hardcoded `/home/droid/projects/...` path. Only invoked as a standalone script; not in the `docker compose up` path. Safe to ignore until needed.
- `gemini_agent.py` is a 0-byte stub in both repos. Dead code.

## Env vars the user must set in `.env` for `docker compose up`

Required: `IB_PASSWORD`, `NGROK_AUTHTOKEN`, `OPENROUTER_API_KEY`, `GITHUB_TOKEN`.
Optional: `NGROK_DOMAIN`, `OLLAMA_MODELS` (comma-separated, defaults `llama3.1:8b`), `IB_USERNAME`, `TWO_FA_WINDOW`, `MAX_2FA_RETRIES`.

First-run-only manual step: open `http://<host>:5000/colab-setup` from the Pixel, paste a Cookie-Editor JSON export of `colab.research.google.com` cookies, tap Save. Persists in `./data/colab_state.json`.
