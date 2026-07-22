import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor

from backend.database import get_db
from backend.fetchers.polymarket import fetch_polymarket_markets
from backend.fetchers.predictit import fetch_predictit_markets

try:
    from backend.fetchers.ibkr_public import fetch_ibkr_combined
    HAS_IBKR = True
except Exception:
    HAS_IBKR = False

    async def fetch_ibkr_combined(*args, **kwargs):
        return []

from backend.matcher import find_arbitrage_pairs, compute_pair_arb

IBKR_MODE = os.environ.get("IBKR_MODE", "auto").lower()
POLY_LIMIT = int(os.environ.get("SCAN_POLYMARKET_LIMIT", "20000"))

logger = logging.getLogger(__name__)

# WebSocket broadcast helper - graceful fallback if main.py not available
_broadcast_enabled = False

def _broadcast_scan_update(event: str, data: dict):
    """Broadcast scan update to WebSocket clients if available"""
    global _broadcast_enabled
    if not _broadcast_enabled:
        return
    try:
        from backend.main import broadcast_scan_update
        asyncio.create_task(broadcast_scan_update(event, data))
    except Exception:
        pass

def _broadcast_state():
    """Broadcast current scan state to all WebSocket clients"""
    _broadcast_scan_update("scan_state", scan_state.copy())

# Handle on the in-flight run_scan() task. Without it a "reset" could only flip
# the state flags while the real coroutine kept running and stomped them back.
_active_scan_task: "Optional[asyncio.Task]" = None


def register_scan_task(task: "asyncio.Task") -> None:
    global _active_scan_task
    _active_scan_task = task


def clear_stale_results() -> None:
    """Drop any results carried over from a previous run.

    Without this a restart shows the last run's opportunities as if they were
    current, which for arbitrage means acting on edges that may have closed.
    """
    _all_markets.clear()
    _all_opportunities.clear()
    scan_state["total_markets"] = 0
    scan_state["total_opportunities"] = 0
    scan_state["pairs_found"] = 0
    scan_state["progress"] = 0
    scan_state["phase"] = "idle"
    scan_state["status"] = "idle"
    scan_state["is_scanning"] = False
    scan_state["ibkr_scan_rounds_done"] = 0
    scan_state["message"] = "No scan yet - results will appear when a scan completes"


async def abort_scan() -> dict:
    """Cancel any in-flight scan and return state to idle.

    Safe to call when nothing is running - it still clears stale state, which is
    the case that used to leave the dashboard stuck showing a finished scan.
    """
    global _active_scan_task
    cancelled = False
    task = _active_scan_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        cancelled = True
    _active_scan_task = None

    scan_state["is_scanning"] = False
    scan_state["status"] = "idle"
    scan_state["phase"] = "idle"
    scan_state["message"] = "Scan reset by user"
    scan_state["progress"] = 0
    scan_state["total_comparisons"] = 0
    scan_state["completed_comparisons"] = 0
    scan_state["platform_counts"] = {"polymarket": 0, "predictit": 0, "ibkr": 0}
    _fetch_status.clear()
    _broadcast_state()
    return {"cancelled_running_scan": cancelled}


scan_state = {
    "is_scanning": False,
    "progress": 0,
    "phase": "idle",
    "message": "",
    "status": "idle",
    "last_scan_time": None,
    "next_scan_time": None,
    "total_markets": 0,
    "total_opportunities": 0,
    "total_comparisons": 0,
    "completed_comparisons": 0,
    "pairs_found": 0,
    "auto_scan_enabled": False,
    "ibkr_scan_rounds_done": 0,  # tracks IBKR's 2-pass discovery (1=REST done, 2=TWS+REST round 2 done)
    # live per-platform "loaded" counts, filled as each fetcher finishes its pass
    # (so the UI can count up live instead of jumping at the end-of-scan merge)
    "platform_counts": {"polymarket": 0, "predictit": 0, "ibkr": 0},
}

_scan_signal = asyncio.Event()

# ── Kaggle GPU notebook live state ─────────────────────────────────────────
# The notebook self-reports per-cell progress via POST /api/kaggle-progress.
# Stage indices match the notebook's executable cells (cell 0 is markdown).
KAGGLE_STAGES = [
    "Install + GPU check",
    "Load models (Qwen3-Embed → bge-reranker → Qwen3-Reranker)",
    "Fetch markets (WebSocket) + noise pre-filter",
    "Compatibility filters (dates / numbers / proper nouns)",
    "GPU matching (embed → rerank → rerank)",
    "Send results back",
    "Preview top opportunities",
]


def _fresh_kaggle_stages() -> List[Dict[str, Any]]:
    return [
        {"index": i, "name": name, "status": "pending", "message": "",
         "started_at": None, "ended_at": None}
        for i, name in enumerate(KAGGLE_STAGES)
    ]


kaggle_state = {
    "running": False,          # True once the notebook starts beaconing
    "kernel": None,            # e.g. jessefleming/cloud-gpu-matcher-v4-stable
    "current_stage": None,     # index of the active stage
    "started_at": None,        # ISO ts of first beacon
    "updated_at": None,        # ISO ts of last beacon
    "stages": _fresh_kaggle_stages(),
}


def get_kaggle_state() -> dict:
    return {**kaggle_state, "stages": [dict(s) for s in kaggle_state["stages"]]}


def reset_kaggle_state(kernel: Optional[str] = None):
    """Clear per-stage state for a new Kaggle run (called when executor is queued)."""
    kaggle_state["running"] = False
    kaggle_state["kernel"] = kernel
    kaggle_state["current_stage"] = None
    kaggle_state["started_at"] = None
    kaggle_state["updated_at"] = None
    kaggle_state["stages"] = _fresh_kaggle_stages()


def update_kaggle_stage(index: int, status: str, message: str = "") -> dict:
    """Apply a beacon from the notebook. status: running | done | error."""
    now = datetime.utcnow().isoformat()
    if not kaggle_state["started_at"]:
        kaggle_state["started_at"] = now
    kaggle_state["running"] = status != "error"
    kaggle_state["updated_at"] = now
    if 0 <= index < len(kaggle_state["stages"]):
        st = kaggle_state["stages"][index]
        st["status"] = status
        if message:
            st["message"] = message
        if status == "running" and not st["started_at"]:
            st["started_at"] = now
            kaggle_state["current_stage"] = index
        if status in ("done", "error"):
            st["ended_at"] = now
        # mark the final stage completing as run finished
        if status == "done" and index == len(kaggle_state["stages"]) - 1:
            kaggle_state["running"] = False
    return get_kaggle_state()


_all_markets: List[Dict[str, Any]] = []
_all_opportunities: List[Dict[str, Any]] = []

SCAN_INTERVAL_SECONDS = 300

_matcher_pool = ThreadPoolExecutor(max_workers=1)
_refresh_lock = asyncio.Lock()


def get_scan_state() -> dict:
    return {**scan_state}


def get_cached_markets() -> List[Dict[str, Any]]:
    return _all_markets


def get_cached_opportunities() -> List[Dict[str, Any]]:
    return _all_opportunities


def set_cloud_results(results: List[Dict[str, Any]], clear: bool = True):
    global _all_opportunities, scan_state
    import json
    import os
    from datetime import datetime
    
    if clear:
        _all_opportunities.clear()
    
    # Prioritize results that have high matchScore (Cross-Encoder nuance) and then ROI
    # matchScore from Colab (ms-marco) is typically 0-10+ or normalized 0-100
    sorted_results = sorted(
        results, 
        key=lambda x: (x.get("isVerified", False), x.get("matchScore", 0), x.get("roi", 0)), 
        reverse=True
    )
    
    # Keep all 2000 the notebook sends — matches the notebook's top_k so no GPU work is discarded
    TOP_K = 2000
    top_k = sorted_results[:TOP_K]
    
    # Save full results to JSON for audit trail
    results_dir = "/app/data/results"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = f"{results_dir}/arb_results_{timestamp}.json"
    
    # Ensure directory exists
    os.makedirs(results_dir, exist_ok=True)
    
    # Create detailed output with full question text and URLs
    detailed_results = []
    for r in top_k:
        ma, mb = r.get("marketA", {}), r.get("marketB", {})
        end_a = ma.get("endDate") or "9999-12-31"
        end_b = mb.get("endDate") or "9999-12-31"
        earliest_end = min(end_a, end_b)
        
        detailed_results.append({
            "roi": r.get("roi"),
            "matchScore": r.get("matchScore"),
            "isVerified": r.get("isVerified"),
            "isExactMatch": r.get("is_exact_match", False),
            "llmConfidence": r.get("confidence", 0),
            "llmReasoning": r.get("reasoning", ""),
            "earliestEndDate": earliest_end,
            "marketA": {
                "id": ma.get("id"),
                "title": ma.get("title"),
                "question": ma.get("title"),
                "yesPrice": ma.get("yesPrice"),
                "noPrice": ma.get("noPrice"),
                "url": ma.get("marketUrl") or ma.get("url"),
                "platform": ma.get("platform"),
                "endDate": ma.get("endDate"),
            },
            "marketB": {
                "id": mb.get("id"),
                "title": mb.get("title"),
                "question": mb.get("title"),
                "yesPrice": mb.get("yesPrice"),
                "noPrice": mb.get("noPrice"),
                "url": mb.get("marketUrl") or mb.get("url"),
                "platform": mb.get("platform"),
                "endDate": mb.get("endDate"),
            }
        })
    
    # Sort by: earliest end date (soonest first) then by ROI (highest first)
    detailed_results.sort(key=lambda x: (x["earliestEndDate"], -x["roi"]))
    
    with open(results_file, 'w') as f:
        json.dump(detailed_results, f, indent=2)
    
    logger.info(f"Saved {len(top_k)} results to {results_file}")
    
    _all_opportunities.extend(top_k)

    scan_state["progress"] = 100
    scan_state["phase"] = "Cloud match complete"
    scan_state["status"] = "complete"
    scan_state["message"] = f"Cloud GPU found {len(top_k)} top-tier opportunities!"
    scan_state["pairs_found"] = len(top_k)

    scan_state["is_scanning"] = False
    _broadcast_state()  # Final broadcast

    logger.info(f"Cloud results synced! Top {TOP_K} prioritized. Total ops: {len(_all_opportunities)}")

    # Verification stage. Two modes:
    #   REMOTE_VERIFY on  -> park the 2000 for a second Kaggle GPU session
    #                        (Ollama qwen3.6:27b, 3 workers) to pull over the
    #                        tailnet, verify, and POST verdicts back.
    #   REMOTE_VERIFY off -> legacy path: local Ollama (-cloud) verifies inline.
    if REMOTE_VERIFY:
        set_verification_queue(top_k)
        scan_state["phase"] = "Awaiting remote verification"
        scan_state["message"] = (
            f"{len(top_k)} fuzzy matches parked for the Kaggle Ollama verifier "
            f"({REMOTE_VERIFY_MODEL}, {REMOTE_VERIFY_WORKERS} workers).")
        _broadcast_state()
        if REMOTE_VERIFY_AUTOLAUNCH:
            _launch_remote_verifier_bg()
        return

    # Kick off LLM verification + report generation in the background so the
    # caller (the /ws cloud_results handler) can ack Colab without blocking.
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_verify_and_report(top_k))
        else:
            logger.warning("No running event loop -- skipping LLM verification kickoff")
    except RuntimeError:
        logger.warning("No event loop in this thread -- skipping LLM verification kickoff")


async def _verify_and_report(matches: List[Dict[str, Any]]):
    """
    Post-Colab pipeline: 2 gemma workers verify the 2000 matches in parallel,
    then a comprehensive HTML report is written to /app/reports/.
    """
    global _llm_verified_matches, _latest_report_path
    try:
        from backend.llm_verifier import run_llm_verification
        from backend.html_report_generator import generate_html_report
        from datetime import datetime
        from pathlib import Path

        scan_state["phase"] = "LLM verification"
        scan_state["message"] = f"Ollama cloud (gpt-oss:120b + gemma4:31b, 2x2 workers) analysing {len(matches)} matches..."
        _broadcast_state()

        # run_llm_verification returns ONLY confirmed exact matches
        # (is_exact_match true and confidence >= threshold)
        confirmed = await run_llm_verification(matches)

        await _generate_and_store_report(confirmed)
    except Exception as e:
        logger.error(f"LLM verification / report generation failed: {e}", exc_info=True)
        scan_state["phase"] = "Report failed"
        scan_state["message"] = f"LLM pipeline error: {e}"
        _broadcast_state()


# Stateful holders for the verified matches and the most recent report path,
# exposed via /api/llm-matches and /api/report/latest in main.py.
_llm_verified_matches: List[Dict[str, Any]] = []
_latest_report_path: Optional[str] = None


def get_llm_verified_matches() -> List[Dict[str, Any]]:
    return _llm_verified_matches


def get_latest_report_path() -> Optional[str]:
    return _latest_report_path


# ── Remote verification (Session 2: Kaggle Ollama qwen3.6:27b, 3 workers) ─────
# When REMOTE_VERIFY is on, the 2000 fuzzy matches are parked here instead of
# being verified by local Ollama. A second Kaggle GPU session joins the tailnet,
# pulls /api/verification-queue, runs qwen3.6:27b sharded across both T4s, and
# POSTs verdicts to /api/verification-results, which calls apply_remote_verdicts.
REMOTE_VERIFY            = os.environ.get("REMOTE_VERIFY", "0").lower() in ("1", "true", "yes")
REMOTE_VERIFY_AUTOLAUNCH = os.environ.get("REMOTE_VERIFY_AUTOLAUNCH", "1").lower() in ("1", "true", "yes")
REMOTE_VERIFY_MODEL      = os.environ.get("REMOTE_VERIFY_MODEL", "qwen3.6:27b")
REMOTE_VERIFY_WORKERS    = int(os.environ.get("REMOTE_VERIFY_WORKERS", "3"))
REMOTE_VERIFY_MIN_CONF   = int(os.environ.get("LLM_VERIFY_MIN_CONF", "85"))

_verification_queue: List[Dict[str, Any]] = []
_verification_meta: Dict[str, Any] = {"received": 0, "total": 0, "confirmed": 0, "started_at": None}


def set_verification_queue(matches: List[Dict[str, Any]]):
    """Park the fuzzy matches for the remote Kaggle verifier to pull. Each pair
    gets a stable pairId so verdicts can be merged back by id."""
    global _verification_queue, _verification_meta
    for i, m in enumerate(matches):
        m["pairId"] = i
    _verification_queue = matches
    _verification_meta = {
        "received": 0, "total": len(matches), "confirmed": 0,
        "started_at": datetime.now().isoformat(),
    }
    logger.info(f"Parked {len(matches)} matches for remote verification.")


def get_verification_queue() -> List[Dict[str, Any]]:
    return _verification_queue


def get_verification_meta() -> Dict[str, Any]:
    return {**_verification_meta, "queued": len(_verification_queue)}


async def apply_remote_verdicts(verdicts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge verdicts POSTed back by the Kaggle Ollama verifier onto the parked
    matches, keep confirmed exact matches, and generate the HTML report.
    Each verdict: {pairId, is_exact_match, confidence, reasoning, verifyModel}."""
    global _verification_meta
    by_id = {v.get("pairId"): v for v in verdicts if v.get("pairId") is not None}
    if not by_id:
        logger.warning("apply_remote_verdicts called with no usable verdicts -- skipping report.")
        return {"merged": 0, "confirmed": 0}
    merged = []
    for m in _verification_queue:
        v = by_id.get(m.get("pairId"))
        if not v:
            continue
        merged.append({**m,
                       "is_exact_match": bool(v.get("is_exact_match")),
                       "confidence": int(v.get("confidence", 0)),
                       "reasoning": str(v.get("reasoning", ""))[:200],
                       "verifyModel": v.get("verifyModel", REMOTE_VERIFY_MODEL)})

    confirmed = [m for m in merged
                 if m.get("is_exact_match") and m.get("confidence", 0) >= REMOTE_VERIFY_MIN_CONF]
    for m in confirmed:
        end_a = m.get("marketA", {}).get("endDate") or "9999-12-31"
        end_b = m.get("marketB", {}).get("endDate") or "9999-12-31"
        m["earliestEndDate"] = min(end_a, end_b)
    confirmed.sort(key=lambda x: (x["earliestEndDate"], -x.get("roi", 0)))

    _verification_meta["received"] = len(by_id)
    _verification_meta["confirmed"] = len(confirmed)
    logger.info(f"Remote verdicts: {len(by_id)} received, {len(confirmed)} confirmed exact matches.")
    await _generate_and_store_report(confirmed)
    return {"merged": len(merged), "confirmed": len(confirmed)}


async def _generate_and_store_report(confirmed: List[Dict[str, Any]]):
    """Shared report tail used by both the local and remote verification paths."""
    global _llm_verified_matches, _latest_report_path
    from backend.html_report_generator import generate_html_report
    from pathlib import Path

    scan_state["phase"] = "Generating report"
    scan_state["message"] = f"Confirmed {len(confirmed)} exact matches -- writing report..."
    _broadcast_state()

    reports_dir = Path("/app/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = reports_dir / f"arbitrage_report_{ts}.html"
    generate_html_report(confirmed, str(out_path))

    _llm_verified_matches = confirmed
    _latest_report_path = str(out_path)

    scan_state["phase"] = "Report ready"
    scan_state["message"] = f"{len(confirmed)} confirmed matches. Report: {out_path.name}"
    scan_state["confirmed_matches"] = len(confirmed)
    scan_state["latest_report"] = out_path.name
    _broadcast_state()
    logger.info(f"Pipeline complete: {len(confirmed)} confirmed matches -> {out_path}")


def _launch_remote_verifier_bg():
    """Fire-and-forget launch of the Kaggle Ollama verifier kernel (Session 2)."""
    try:
        import threading
        from verifier_launcher import launch_verifier
        threading.Thread(target=launch_verifier, daemon=True).start()
        logger.info("Remote verifier launch dispatched (Session 2).")
    except Exception as e:
        logger.warning(f"Could not auto-launch remote verifier: {e} "
                       "(matches are still queryable at /api/verification-queue).")


async def refresh_top_leads(limit: int = 20):
    """
    Refreshes the prices for the top N opportunities in real-time.
    This ensures that 'leads' found by Colab stay fresh while the user watches.
    If a refresh is already in progress, skip this call to avoid pile-up.
    """
    global _all_opportunities, _all_markets
    if not _all_opportunities:
        return 0

    # Non-blocking lock check: if a refresh is already running, skip this one
    if _refresh_lock.locked():
        logger.debug("Refresh already in progress, skipping.")
        return 0

    async with _refresh_lock:
        # 1. Get unique markets from the top N opportunities
        top_opps = _all_opportunities[:limit]
        market_ids_to_refresh = set()
        for opp in top_opps:
            market_ids_to_refresh.add(opp["marketA"]["id"])
            market_ids_to_refresh.add(opp["marketB"]["id"])

        if not market_ids_to_refresh:
            return 0

        # 2. Re-fetch only the platforms present in the top leads to save time.
        logger.info(f"Refreshing prices for top {len(market_ids_to_refresh)} markets...")
        platforms_to_refresh = set(m_id.split('_')[0] for m_id in market_ids_to_refresh)

        results = {}
        tasks = []
        # Manifold removed - play-money only, not real funds
        if 'poly' in platforms_to_refresh:
            tasks.append(_fetch_with_progress("Polymarket", lambda on_progress=None: fetch_polymarket_markets(limit=2000, on_progress=on_progress), results))
        if 'pi' in platforms_to_refresh:
            tasks.append(_fetch_with_progress("PredictIt", fetch_predictit_markets, results))

        if tasks:
            await asyncio.gather(*tasks)

        # 3. Update the global market cache with new prices
        new_markets_list = []
        for platform_results in results.values():
            new_markets_list.extend(platform_results)

        new_price_map = {m["id"]: (m["yesPrice"], m["noPrice"]) for m in new_markets_list}

        updated_count: int = 0
        for m in _all_markets:
            if m["id"] in new_price_map:
                y, n = new_price_map[m["id"]]
                if m["yesPrice"] != y:
                    m["yesPrice"] = y
                    m["noPrice"] = n
                    updated_count += 1

        # 4. Recalculate ROI for the opportunities using the new prices
        for opp in _all_opportunities:
            ma_id = opp["marketA"]["id"]
            mb_id = opp["marketB"]["id"]

            if ma_id in new_price_map or mb_id in new_price_map:
                ma = next((m for m in _all_markets if m["id"] == ma_id), opp["marketA"])
                mb = next((m for m in _all_markets if m["id"] == mb_id), opp["marketB"])

                opp["marketA"]["yesPrice"] = ma["yesPrice"]
                opp["marketA"]["noPrice"] = ma["noPrice"]
                opp["marketB"]["yesPrice"] = mb["yesPrice"]
                opp["marketB"]["noPrice"] = mb["noPrice"]

                new_roi_data = compute_pair_arb(ma, mb)
                opp["roi"] = new_roi_data["roi"]
                opp["combinedYesCost"] = new_roi_data["grossCost"]
                opp["totalCost"] = new_roi_data["cost"]
                opp["scenario"] = new_roi_data["scenario"]

        # Re-sort opportunities by ROI
        _all_opportunities.sort(key=lambda x: x["roi"], reverse=True)

        return updated_count


def set_auto_scan(enabled: bool):
    scan_state["auto_scan_enabled"] = enabled
    if enabled:
        _scan_signal.set()
    logger.info(f"Auto-scan {'enabled' if enabled else 'disabled'}")


def get_auto_scan() -> bool:
    return scan_state.get("auto_scan_enabled", False)


_fetch_status: Dict[str, str] = {}


async def _fetch_with_progress(name, fetch_coro_func, results_dict):
    try:
        _fetch_status[name] = "fetching..."
        
        def on_fetch_progress(progress_val, count=None):
            if isinstance(progress_val, int):
                # Page number
                _fetch_status[name] = f"fetching (pg {progress_val}+)"
            else:
                # String status
                _fetch_status[name] = str(progress_val)
        
        # We pass the callback if the fetcher supports it
        import inspect
        sig = inspect.signature(fetch_coro_func)
        if "on_progress" in sig.parameters:
            result = await fetch_coro_func(on_progress=on_fetch_progress)
        else:
            result = await fetch_coro_func()
            
        results_dict[name] = result
        _fetch_status[name] = f"done ({len(result):,})"
        # live "loaded" count so the UI fills in as each platform finishes
        scan_state["platform_counts"][name.lower()] = len(result)
        logger.info(f"{name}: fetched {len(result)} markets")
    except Exception as e:
        logger.error(f"{name} fetch error: {e}")
        results_dict[name] = []
        _fetch_status[name] = f"error"


def _update_fetch_progress(total_expected: int):
    parts = []
    # Order them logically
    for name in ["Polymarket", "PredictIt", "IBKR"]:
        status = _fetch_status.get(name, "waiting")
        parts.append(f"{name}: {status}")
    
    done_count = sum(1 for s in _fetch_status.values() if s.startswith("done") or s == "error")
    
    # Base progress for starting: 3%
    # Fetching phase: up to 45%
    if total_expected > 0:
        # Each done platform adds (42 / count) to the progress
        # Plus a tiny bit for active "fetching" status
        fetching_bonus = sum(0.05 for s in _fetch_status.values() if s.startswith("fetching") or "discovering" in s)
        pct = 3 + int(((done_count + fetching_bonus) / total_expected) * 42)
    else:
        pct = 45
        
    scan_state["progress"] = min(pct, 45)
    scan_state["message"] = " | ".join(parts)


async def _fetch_progress_updater(results_dict, total_expected: int):
    while True:
        _update_fetch_progress(total_expected)
        if len(results_dict) >= total_expected:
            # Final update
            _update_fetch_progress(total_expected)
            break
        await asyncio.sleep(0.5) # Update more frequently


async def run_scan(platforms: Optional[List[str]] = None) -> Dict[str, Any]:
    global _all_markets, _all_opportunities, scan_state

    if scan_state["is_scanning"]:
        return {"status": "already_scanning", "message": "A scan is already in progress"}

    scan_state["is_scanning"] = True
    # Must be reset here: load_cached_from_db() marks state "complete" at boot, and
    # nothing else ever clears it, so without this every later scan still reads as
    # finished to the dashboard and the /live page's "scanning" branch never fires.
    scan_state["status"] = "scanning"
    scan_state["progress"] = 0
    scan_state["phase"] = "Fetching markets"
    scan_state["message"] = "Starting scan..."
    scan_state["ibkr_scan_rounds_done"] = 0
    scan_state["platform_counts"] = {"polymarket": 0, "predictit": 0, "ibkr": 0}
    _fetch_status.clear()
    _broadcast_state()  # Broadcast to WebSocket clients

    try:
        scan_state["progress"] = 3
        scan_state["phase"] = "Fetching all platforms"
        scan_state["message"] = "Fetching Polymarket & PredictIt in parallel..."

        results: Dict[str, List] = {}
        fetch_tasks = []

        # Store as lambda to defer execution (and check signature)
        # Note: Manifold removed - it's play-money only, not real funds
        platform_map = {
            "polymarket": ("Polymarket", lambda on_progress=None: fetch_polymarket_markets(limit=POLY_LIMIT, on_progress=on_progress)),
            "predictit": ("PredictIt", lambda: fetch_predictit_markets()), # PredictIt doesn't have progress yet
            "ibkr": ("IBKR", lambda on_progress=None: fetch_ibkr_combined(on_progress=on_progress)),
        }

        default_platforms = ["polymarket", "predictit"]
        if IBKR_MODE != "off" and HAS_IBKR:
            default_platforms.append("ibkr")
        active_platforms = platforms if platforms else default_platforms
        active_platforms = [p.lower() for p in active_platforms]
        
        for p_id, (name, coro_func) in platform_map.items():
            is_active = False
            if p_id in active_platforms:
                is_active = True
            else:
                # Check for "IBKR Forecast" match
                for ap in active_platforms:
                    if p_id in ap or ap in p_id:
                        is_active = True
                        break

            # IBKR: honour IBKR_MODE=off
            if p_id == "ibkr" and (IBKR_MODE == "off" or not HAS_IBKR):
                is_active = False

            if is_active:
                fetch_tasks.append(_fetch_with_progress(name, coro_func, results))
            else:
                _fetch_status[name] = "skipped"

        if not fetch_tasks:
            scan_state["status"] = "error"
            scan_state["message"] = "No matching platforms found to scan"
            scan_state["is_scanning"] = False
            return {"status": "error", "message": "No matching platforms found"}

        await asyncio.gather(
            *fetch_tasks,
            _fetch_progress_updater(results, len(fetch_tasks)),
        )

# Manifold removed - play-money only
        poly_markets = results.get("Polymarket", [])
        pi_markets = results.get("PredictIt", [])
        ibkr_markets = results.get("IBKR", [])

        # fetch_ibkr_combined runs REST discovery then TWS pricing internally, so a
        # non-empty result means both passes finished. The Kaggle notebook's primary
        # gate polls this counter (>= 2) before it will pull markets; it was specced
        # in scan_state but never written, leaving the notebook on its legacy
        # fallback path forever.
        if ibkr_markets:
            scan_state["ibkr_scan_rounds_done"] = 2

        fetch_warnings = []
        if not poly_markets:
            fetch_warnings.append("Polymarket returned 0 markets")
        if not pi_markets:
            fetch_warnings.append("PredictIt returned 0 markets")
        if fetch_warnings:
            logger.warning(f"Partial fetch: {'; '.join(fetch_warnings)}")

        scan_state["progress"] = 45
        scan_state["message"] = f"Got {len(poly_markets)} Polymarket + {len(pi_markets)} PredictIt. Saving to DB..."

        all_markets = poly_markets + pi_markets + ibkr_markets
        
        # Tag weather markets
        weather_keywords = [
            "temperature", "rain", "snow", "precipitation", "weather", 
            "climate", "storm", "hurricane", "degree", "high in", "low in",
            "forecast", "meteorology", "atmospheric"
        ]
        for m in all_markets:
            title_lower = m["title"].lower()
            cat_lower = m.get("category", "").lower()
            is_weather = any(kw in title_lower or kw in cat_lower for kw in weather_keywords)
            m["isWeather"] = 1 if is_weather else 0

        _all_markets = all_markets
        scan_state["total_markets"] = len(all_markets)

        db = await get_db()
        try:
            batch_size = 500
            for i in range(0, len(all_markets), batch_size):
                batch = all_markets[i:i+batch_size]
                for m in batch:
                    outcomes_json = json.dumps(m.get("outcomes")) if m.get("outcomes") else None
                    await db.execute(
                        """INSERT OR REPLACE INTO markets 
                           (id, platform, title, category, yes_price, no_price, volume, 
                            last_updated, end_date, market_url, is_binary, outcome_count,
                            contract_label, outcomes_json, is_weather, fetched_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            m["id"], m["platform"], m["title"], m.get("category", ""),
                            m["yesPrice"], m["noPrice"], m.get("volume", 0),
                            m.get("lastUpdated"), m.get("endDate"), m.get("marketUrl"),
                            1 if m.get("isBinary", True) else 0,
                            m.get("outcomeCount", 2), m.get("contractLabel", ""),
                            outcomes_json, m["isWeather"], datetime.utcnow().isoformat(),
                        ),
                    )
                await db.commit()
        finally:
            await db.close()

        # ── Local cross-platform matching (fee-aware ROI) ─────────────────
        scan_state["progress"] = 50
        scan_state["phase"] = "Matching markets"
        scan_state["message"] = f"Matching {len(all_markets):,} markets across platforms..."
        _broadcast_state()

        loop = asyncio.get_event_loop()

        def on_match_progress(completed, total, found):
            def _apply():
                scan_state["total_comparisons"] = total
                scan_state["completed_comparisons"] = completed
                scan_state["pairs_found"] = found
                pct = 50 + int((completed / total) * 48) if total else 98
                scan_state["progress"] = min(pct, 98)
                scan_state["message"] = f"Compared {completed:,} / {total:,} candidate pairs — {found} matches so far"
            loop.call_soon_threadsafe(_apply)

        # LOCAL_MATCH=skip drops the CPU pairwise grind (~an hour on this box for
        # 3M+ pairs) and leaves ranking entirely to the Kaggle GPU matcher, which
        # re-derives candidates from the full market dump anyway. The trade-off:
        # the dashboard shows no opportunities until the notebook reports back.
        if os.environ.get("LOCAL_MATCH", "full").lower() in ("0", "off", "skip"):
            logger.info("LOCAL_MATCH=skip - CPU matching skipped; GPU matcher ranks all pairs")
            scan_state["message"] = (
                f"CPU matching skipped - Kaggle GPU matcher will rank all {len(all_markets):,} markets"
            )
            pairs = []
        else:
            min_sim = float(os.environ.get("MATCH_MIN_SIMILARITY", "35"))
            pairs = await loop.run_in_executor(
                _matcher_pool,
                lambda: find_arbitrage_pairs(all_markets, min_similarity=min_sim, on_progress=on_match_progress),
            )
        pairs = pairs or []

        _all_opportunities.clear()
        _all_opportunities.extend(pairs)
        await save_opportunities_to_db(pairs)

        scan_state["progress"] = 100
        scan_state["phase"] = "complete"
        scan_state["status"] = "complete"
        scan_state["message"] = (
            f"Scan complete — {len(pairs):,} matched pairs across {len(all_markets):,} markets "
            f"({sum(1 for p in pairs if p.get('roi', 0) > 0):,} with positive ROI)"
        )
        scan_state["is_scanning"] = False
        scan_state["total_opportunities"] = len(_all_opportunities)
        scan_state["pairs_found"] = len(_all_opportunities)
        scan_state["last_scan_time"] = datetime.utcnow().isoformat()
        scan_state["next_scan_time"] = (datetime.utcnow() + timedelta(seconds=SCAN_INTERVAL_SECONDS)).isoformat()
        _broadcast_state()  # Broadcast scan complete

        # Auto-dispatch the GPU matcher notebook. Nothing else triggers the
        # executor: the orchestrator that once did is dead code no module
        # imports, so without this POST "auto upload to Kaggle" never happens
        # and the executor waits forever for a request that never comes.
        if os.environ.get("AUTO_DISPATCH_KAGGLE", "1") == "1":
            executor_url = os.environ.get("ORACLE_EXECUTOR_URL", "http://localhost:5000")
            try:
                import httpx
                async with httpx.AsyncClient(timeout=20) as client:
                    resp = await client.post(f"{executor_url}/execute", json={})
                logger.info(f"Kaggle matcher auto-dispatched: {resp.json()}")
            except Exception as e:
                logger.warning(
                    f"Kaggle auto-dispatch failed ({e}) - dispatch manually: "
                    f"curl -X POST {executor_url}/execute"
                )

        return {
            "status": "complete",
            "total_markets": len(all_markets),
            "total_opportunities": len(_all_opportunities),
            "markets_by_platform": {
                "Polymarket": len(poly_markets),
                "PredictIt": len(pi_markets),
                "IBKR": len(ibkr_markets),
            },
        }

    except asyncio.CancelledError:
        # CancelledError is a BaseException, so the handler below never sees it and
        # the finally clause (which only releases on "error") would leave the scan
        # lock stuck on - blocking every future scan.
        logger.info("Scan cancelled - releasing scan lock")
        scan_state["is_scanning"] = False
        scan_state["status"] = "idle"
        scan_state["phase"] = "idle"
        scan_state["progress"] = 0
        raise

    except Exception as e:
        logger.error(f"Scan error: {e}", exc_info=True)
        scan_state["status"] = "error"
        scan_state["message"] = f"Scan error: {str(e)}"
        scan_state["is_scanning"] = False
        return {"status": "error", "message": str(e)}

    finally:
        # We used to set scan_state["is_scanning"] = False here, but we shouldn't anymore!
        # The scan isn't technically "done" until Colab responds.
        # But we DO release the lock if there was an error.
        if scan_state["status"] == "error":
            scan_state["is_scanning"] = False


async def auto_scan_loop():
    logger.info("Auto-scan loop worker started")
    while True:
        try:
            # Wait for either the event (manual trigger/toggle) or a timeout (interval)
            if not scan_state.get("auto_scan_enabled", False):
                logger.info("Auto-scan is OFF, waiting for toggle...")
                await _scan_signal.wait()
            
            # Clear it so we don't loop infinitely without waiting
            _scan_signal.clear()
            
            if scan_state.get("auto_scan_enabled", False):
                logger.info("Triggering scan (Auto-scan is ON)")
                # Run as its own task and register it, so a user reset cancels the
                # scan without also cancelling (and permanently killing) this loop.
                task = asyncio.create_task(run_scan())
                register_scan_task(task)
                try:
                    result = await task
                except asyncio.CancelledError:
                    logger.info("Auto-scan cancelled by user reset")
                    result = {"status": "cancelled"}
                logger.info(f"Scan complete: {result.get('status')} — {result.get('total_markets', 0)} markets found")

        except Exception as e:
            logger.error(f"Auto-scan loop error: {e}", exc_info=True)
        
        # Wait for the interval OR a manual trigger via the signal
        try:
            # If auto-scan was just turned off, we'll hit the wait at the top of the loop next
            await asyncio.wait_for(_scan_signal.wait(), timeout=SCAN_INTERVAL_SECONDS)
            logger.info("Auto-scan loop awakened by signal")
        except asyncio.TimeoutError:
            # Timeout is fine, just means we hit the periodic mark
            pass


# ── Persistence: survive backend restarts ────────────────────────────────────

def _row_to_market(r) -> Dict[str, Any]:
    outcomes = None
    try:
        if r["outcomes_json"]:
            outcomes = json.loads(r["outcomes_json"])
    except Exception:
        pass
    return {
        "id": r["id"], "platform": r["platform"], "title": r["title"],
        "category": r["category"], "yesPrice": r["yes_price"], "noPrice": r["no_price"],
        "volume": r["volume"], "lastUpdated": r["last_updated"], "endDate": r["end_date"],
        "marketUrl": r["market_url"], "isBinary": bool(r["is_binary"]),
        "outcomeCount": r["outcome_count"], "contractLabel": r["contract_label"],
        "outcomes": outcomes, "isWeather": r["is_weather"],
    }


async def save_opportunities_to_db(pairs: List[Dict[str, Any]]):
    db = await get_db()
    try:
        # Prune old history
        await db.execute("DELETE FROM market_price_history WHERE timestamp < datetime('now', '-30 days')")
        
        await db.execute("DELETE FROM matched_pairs")
        for p in pairs:
            await db.execute(
                """INSERT INTO matched_pairs
                   (market_a_id, market_b_id, match_score, match_reason,
                    combined_yes_cost, potential_profit, roi, combo_type,
                    leg_count, legs_json, fees, earliest_resolution, scenario)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    p["marketA"]["id"], p["marketB"]["id"],
                    p.get("matchScore", 0), p.get("matchReason", ""),
                    p.get("combinedYesCost", 0), p.get("potentialProfit", 0),
                    p.get("roi", 0), p.get("comboType", "pair"),
                    p.get("legCount", 2), json.dumps(p.get("legs", [])),
                    p.get("fees", 0), p.get("earliestResolution"),
                    str(p.get("scenario", "")),
                ),
            )
            
            # Log historical price data
            m_a = p["marketA"]
            m_b = p["marketB"]
            match_hash = f"{m_a['id']}-{m_b['id']}"
            
            await db.execute(
                "INSERT INTO market_price_history (match_hash, platform, yes_price, volume) VALUES (?, ?, ?, ?)",
                (match_hash, m_a["platform"], m_a["yesPrice"], m_a.get("volume", 0))
            )
            await db.execute(
                "INSERT INTO market_price_history (match_hash, platform, yes_price, volume) VALUES (?, ?, ?, ?)",
                (match_hash, m_b["platform"], m_b["yesPrice"], m_b.get("volume", 0))
            )
            if p.get("marketC"):
                m_c = p["marketC"]
                await db.execute(
                    "INSERT INTO market_price_history (match_hash, platform, yes_price, volume) VALUES (?, ?, ?, ?)",
                    (match_hash, m_c["platform"], m_c["yesPrice"], m_c.get("volume", 0))
                )
                
        await db.commit()
        logger.info(f"Persisted {len(pairs)} matched pairs and logged historical prices to DB")
    except Exception as e:
        logger.warning(f"Could not persist matched pairs and history: {e}")
    finally:
        await db.close()


async def load_cached_from_db():
    """Restore markets + opportunities from SQLite after a backend restart."""
    global _all_markets, _all_opportunities
    markets, opps = [], []
    try:
        db = await get_db()
        try:
            cur = await db.execute("SELECT * FROM markets")
            rows = await cur.fetchall()
            markets = [_row_to_market(r) for r in rows]
            by_id = {m["id"]: m for m in markets}

            cur = await db.execute("SELECT * FROM matched_pairs ORDER BY roi DESC")
            prows = await cur.fetchall()
            for r in prows:
                ma, mb = by_id.get(r["market_a_id"]), by_id.get(r["market_b_id"])
                if not ma or not mb:
                    continue
                legs = []
                try:
                    legs = json.loads(r["legs_json"]) if r["legs_json"] else []
                except Exception:
                    pass
                opps.append({
                    "comboType": r["combo_type"] or "pair",
                    "legCount": r["leg_count"] or 2,
                    "legs": legs,
                    "marketA": ma, "marketB": mb,
                    "combinedYesCost": r["combined_yes_cost"],
                    "totalCost": r["combined_yes_cost"],
                    "fees": r["fees"] or 0,
                    "potentialProfit": r["potential_profit"],
                    "roi": r["roi"],
                    "matchScore": r["match_score"],
                    "matchReason": r["match_reason"],
                    "earliestResolution": r["earliest_resolution"],
                    "scenario": r["scenario"],
                })
        finally:
            await db.close()

        if markets:
            _all_markets.clear()
            _all_markets.extend(markets)
            scan_state["total_markets"] = len(markets)
        if opps:
            _all_opportunities.clear()
            _all_opportunities.extend(opps)
            scan_state["total_opportunities"] = len(opps)
            scan_state["pairs_found"] = len(opps)
            scan_state["status"] = "complete"
            scan_state["phase"] = "complete"
            scan_state["progress"] = 100
            scan_state["message"] = f"Restored {len(opps):,} opportunities and {len(markets):,} markets from last scan"
        logger.info(f"Restored {len(markets)} markets, {len(opps)} opportunities from DB")
    except Exception as e:
        logger.warning(f"DB cache restore failed: {e}")
