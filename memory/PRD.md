# ARB TERMINAL — Prediction Market Arbitrage Scanner

## Original Problem Statement
User asked to clone https://github.com/Bamove6969/arbitrage-calculator-main, understand it, build/run it, modernize the UI, make it more user friendly, and report what it's lacking. Follow-up: IBKR support is mandatory (user normally runs the "universal docker container" which installs headless IB Gateway / IBGA and sends a 2FA push to their phone).

## Architecture (this environment)
- Frontend: React 19 + Vite + Tailwind v4 + shadcn/ui, served on port 3000 via `/app/frontend/package.json` shim → runs vite from /app root (config: /app/vite.config.ts, root=client/)
- Backend: FastAPI (`/app/backend/main.py`) exposed as `server:app` via `/app/backend/server.py` shim on port 8001; env from `/app/backend/.env` (loaded with python-dotenv in server.py)
- DB: SQLite via aiosqlite at /app/backend/arbitrage.db (repo's original storage; NOT MongoDB)
- Ingress: `/api/*` → 8001, everything else → 3000 (frontend uses relative /api fetches)

## Data Sources
- Polymarket: CLOB API (waves of 8×1000, ~21k active markets) — works
- PredictIt: public API (~677 contracts) — works
- IBKR ForecastEx: tiered fetcher `backend/fetchers/ibkr_public.py::fetch_ibkr_combined`
  - Tier 1: live TWS pricing via IB_GATEWAY_URL (user's gateway + 2FA flow) — auto-detected via TCP probe
  - Tier 2: public data fallback — REST contract discovery (936 events) + ForecastEx public S3 (intraday pairs CSV ~10min refresh + daily prices) → ~878 priced markets
  - env: IBKR_MODE=auto|tws|public|off, IB_GATEWAY_URL
- Kalshi: public elections API returns NULL prices unauthenticated (Kalshi now gates price data). Fetcher works but yields few/no priced markets (~55 from deep pages). NEEDS a Kalshi API key (RSA-signed requests) for full support.

## Key Changes Made (June 2026)
1. Environment wiring: server.py shim, frontend launcher shim, .env files, deps installed
2. Re-enabled LOCAL matching (repo had it disabled — scans hung forever waiting for Kaggle/Colab cloud GPU + Ollama verification that don't exist here). Keyword-index + Jaccard + SequenceMatcher matcher runs in ThreadPoolExecutor with live progress
3. Added Kalshi to scanner platform_map; added Kalshi fee model to matcher.estimate_fee; IBKR fee $0.01/contract
4. Built IBKR public-data fetcher + TWS auto-detection (fetch_ibkr_combined)
5. Persistence: matched_pairs saved to DB after scan, markets+opportunities restored on startup (load_cached_from_db) — survives restarts
6. Created missing backend/live_state.py (was untracked in repo, caused 500s on /api/arbitrage-opportunities via llm_verifier import)
7. Fixed whale tracker field mapping (userName/proxyWallet/vol → username/proxyAddress/volume)
8. UI modernization: dark-only "tactical terminal" theme (obsidian bg, cyan #00E5FF primary, neon green positive), Chivo + IBM Plex Mono fonts, sharp 2px radii, ARB TERMINAL branding, restructured Radar page (scanner + opportunities first, watchlist below), renamed Sentinel → Arbitrage Radar
9. market-stats includes kalshi count; scan status completes properly (no more waiting_for_cloud)

## Scan Results (verified)
- 4-platform scan: ~23k markets (21,478 Poly + 677 PI + 878 IBKR + 55 Kalshi), ~1,100+ matched pairs, ~900 positive-ROI, ~5 min runtime

## What's Lacking / Backlog
- P0: Kalshi API key auth (RSA request signing) for real Kalshi prices — need user's key
- P0: Live TWS pricing requires user's IB Gateway reachable from this pod (IB_GATEWAY_URL via tailscale/ngrok TCP), or run the universal docker container on user's own hardware — Docker is NOT available in this K8s pod
- P1: LLM match verification (repo used Kaggle/Ollama) — could re-add via Emergent LLM key to filter false-positive matches
- P1: Persist verified/LLM matches; alert push channels (Telegram/SMS)
- P2: ML Analysis page depends on feedback data; Weather Terminal edge model (weather_edge.py uses open-meteo)
- P2: matcher semantic mode (sentence-transformers) not installed — keyword mode only

## Test Reports
- /app/test_reports/iteration_1.json (testing agent)
