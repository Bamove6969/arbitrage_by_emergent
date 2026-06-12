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
from backend.fetchers.ibkr import fetch_ibkr_markets
from backend.matcher import find_arbitrage_pairs, compute_pair_arb

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
}

_scan_signal = asyncio.Event()

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
    scan_state["progress"] = 0
    scan_state["phase"] = "Fetching markets"
    scan_state["message"] = "Starting scan..."
    scan_state["status"] = "scanning"
    scan_state["total_comparisons"] = 0
    scan_state["completed_comparisons"] = 0
    scan_state["pairs_found"] = 0
    _fetch_status.clear()
    _broadcast_state()  # Broadcast to WebSocket clients

    try:
        scan_state["progress"] = 3
        scan_state["phase"] = "Fetching all platforms"
        scan_state["message"] = "Fetching Polymarket, PredictIt & IBKR in parallel..."

        results: Dict[str, List] = {}
        fetch_tasks = []
        
        # Store as lambda to defer execution (and check signature)
        # Note: Manifold removed - it's play-money only, not real funds
        platform_map = {
            "polymarket": ("Polymarket", lambda on_progress=None: fetch_polymarket_markets(limit=50000, on_progress=on_progress)),
            "predictit": ("PredictIt", lambda: fetch_predictit_markets()), # PredictIt doesn't have progress yet
            "ibkr": ("IBKR", lambda on_progress=None: fetch_ibkr_markets(on_progress=on_progress)),
        }

        active_platforms = platforms if platforms else ["polymarket", "predictit", "ibkr"]
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

        # If TWS connection failed, fall back to cached IBKR markets from DB
        if not ibkr_markets:
            logger.warning("IBKR live fetch returned 0 — loading cached markets from DB")
            scan_state["message"] = "IBKR TWS unavailable — loading cached IBKR markets from DB..."
            try:
                import aiosqlite
                async with aiosqlite.connect("/app/backend/arbitrage.db") as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute("SELECT * FROM markets WHERE platform='IBKR' LIMIT 5000") as cur:
                        rows = await cur.fetchall()
                        ibkr_markets = [{
                            "id": r["id"],
                            "platform": r["platform"],
                            "title": r["title"],
                            "category": r["category"],
                            "yesPrice": r["yes_price"],
                            "noPrice": r["no_price"],
                            "volume": r["volume"],
                            "lastUpdated": r["last_updated"],
                            "endDate": r["end_date"],
                            "marketUrl": r["market_url"],
                            "isBinary": bool(r["is_binary"]),
                            "outcomeCount": r["outcome_count"],
                            "contractLabel": r["contract_label"],
                            "outcomes": None,
                        } for r in rows]
                if ibkr_markets:
                    logger.info(f"Loaded {len(ibkr_markets)} cached IBKR markets from DB")
                else:
                    logger.warning("No cached IBKR markets in DB either")
            except Exception as e:
                logger.error(f"DB fallback for IBKR failed: {e}")

        fetch_warnings = []
        if not poly_markets:
            fetch_warnings.append("Polymarket returned 0 markets")
        if not pi_markets:
            fetch_warnings.append("PredictIt returned 0 markets")
        if not ibkr_markets:
            fetch_warnings.append("IBKR returned 0 (no cache)")
        if fetch_warnings:
            logger.warning(f"Partial fetch: {'; '.join(fetch_warnings)}")

        scan_state["progress"] = 45
        scan_state["message"] = f"Got {len(poly_markets)} Polymarket + {len(pi_markets)} PredictIt + {len(ibkr_markets)} IBKR. Saving to DB..."

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

        # -------------------------------------------------------
        # LOCAL MATCHING IS DISABLED — Colab handles all matching.
        # The local scan's job is only to:
        #   1. Keep _all_markets fresh (so /api/raw-markets gives Colab current data)
        #   2. Refresh prices on existing Colab results if any are present
        # -------------------------------------------------------

        if _all_opportunities:
            # Kaggle results already present — refresh prices with fresh market data
            scan_state["progress"] = 48
            scan_state["phase"] = "Refreshing prices"
            scan_state["message"] = f"Updating prices on {len(_all_opportunities)} Kaggle results..."
            logger.info(f"Local scan refreshing prices on {len(_all_opportunities)} Kaggle results...")
            await refresh_top_leads(limit=len(_all_opportunities))
            scan_state["message"] = f"Prices refreshed. {len(_all_opportunities)} opportunities ready."
        else:
            # ── IBKR round 1 already ran in the gather above ──────────────────
            # Round 1 typically returns ~1 000 markets (REST category tree pass).
            # Round 2 (another full fetch a minute later) returns ~120 000 markets
            # because TWS subscriptions have had time to propagate bid/ask data.
            ibkr_r1 = results.get("IBKR", [])
            scan_state["ibkr_scan_rounds_done"] = 1
            logger.info(f"IBKR round 1 complete: {len(ibkr_r1)} markets")

            scan_state["phase"] = "IBKR round 2"
            scan_state["message"] = f"IBKR round 1 done ({len(ibkr_r1):,} markets). Starting round 2 (full TWS depth)..."
            scan_state["progress"] = 50
            _broadcast_state()

            # Brief pause so TWS can propagate more bid/ask ticks before round 2
            await asyncio.sleep(90)

            results_r2: dict = {}
            ibkr_func_r2 = platform_map["ibkr"][1]
            await _fetch_with_progress("IBKR_R2", ibkr_func_r2, results_r2)
            ibkr_r2 = results_r2.get("IBKR_R2", [])

            # Merge: round-2 is authoritative; add any extras from round-1
            ibkr_r2_ids = {m["id"] for m in ibkr_r2}
            ibkr_extra  = [m for m in ibkr_r1 if m["id"] not in ibkr_r2_ids]
            results["IBKR"] = ibkr_r2 + ibkr_extra

            scan_state["ibkr_scan_rounds_done"] = 2
            logger.info(f"IBKR round 2 complete: {len(ibkr_r2)} markets (merged total: {len(results['IBKR'])})")

            # Rebuild the combined market list with fresh IBKR data
            poly_markets  = results.get("Polymarket", [])
            pi_markets    = results.get("PredictIt",  [])
            ibkr_markets  = results["IBKR"]
            all_markets_merged = poly_markets + pi_markets + ibkr_markets

            # Tag weather
            for m in all_markets_merged:
                title_lower = m.get("title", "").lower()
                m["isWeather"] = 1 if any(kw in title_lower for kw in [
                    "temperature","rain","snow","precipitation","weather","climate",
                    "storm","hurricane","degree","high in","low in","forecast",
                ]) else 0

            _all_markets.clear()
            _all_markets.extend(all_markets_merged)
            scan_state["total_markets"] = len(_all_markets)

            scan_state["phase"] = "Triggering Kaggle"
            scan_state["message"] = f"Both IBKR scans done. Pushing notebook to Kaggle ({len(_all_markets):,} markets)..."
            scan_state["progress"] = 55
            _broadcast_state()

            # ── Trigger Kaggle executor ────────────────────────────────────────
            try:
                import requests as _req
                kaggle_executor_url = os.environ.get("ORACLE_EXECUTOR_URL", "http://localhost:5000")
                logger.info(f"Triggering Kaggle executor at {kaggle_executor_url}")
                exec_resp = _req.post(
                    f"{kaggle_executor_url}/execute",
                    json={},   # executor fetches ngrok URL itself
                    timeout=15,
                )
                if exec_resp.status_code == 200:
                    exec_data = exec_resp.json()
                    logger.info(f"Kaggle executor queued: {exec_data}")
                    scan_state["message"] = (
                        f"Kaggle notebook queued (pos {exec_data.get('queue_position','?')}). "
                        f"Notebook polling for scan completion before fetching markets."
                    )
                    scan_state["kaggle_kernel"] = f"bamove6969/cloud-gpu-matcher-v4"
                    scan_state["executor_status"] = "queued"
                else:
                    logger.warning(f"Kaggle executor HTTP {exec_resp.status_code}: {exec_resp.text[:200]}")
                    scan_state["message"] = "Kaggle executor unreachable — trigger manually at kaggle.com"
            except Exception as e:
                logger.error(f"Kaggle executor trigger error: {e}")

        scan_state["progress"] = 50
        scan_state["phase"] = "Waiting for Cloud GPU"
        # We explicitly DO NOT set "complete". The UI will hang the progress bar at 50%.
        # The /api/cloud-results endpoint will set it to 100% and "complete" later.
        scan_state["status"] = "waiting_for_cloud" 
        scan_state["total_opportunities"] = len(_all_opportunities)
        scan_state["pairs_found"] = len(_all_opportunities)
        scan_state["last_scan_time"] = datetime.utcnow().isoformat()
        scan_state["next_scan_time"] = (datetime.utcnow() + timedelta(seconds=SCAN_INTERVAL_SECONDS)).isoformat()
        _broadcast_state()  # Broadcast scan complete

        return {
            "status": "waiting_for_cloud",
            "total_markets": len(all_markets),
            "total_opportunities": len(_all_opportunities),
            "markets_by_platform": {
                # Manifold removed - play-money only
                "Polymarket": len(poly_markets),
                "PredictIt": len(pi_markets),
                "IBKR": len(ibkr_markets),
            },
        }

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
                result = await run_scan()
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
