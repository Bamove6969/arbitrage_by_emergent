import asyncio
import json
import logging
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
    
    # Absolute Best 1000 as requested
    TOP_K = 1000
    top_k = sorted_results[:TOP_K]
    
    # Save full results to JSON for audit trail
    results_dir = "/mnt/shared/Download"
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
            # Colab results exist — refresh their prices with the new market data
            scan_state["progress"] = 48
            scan_state["phase"] = "Refreshing prices"
            scan_state["message"] = f"Updating prices on {len(_all_opportunities)} Colab results..."
            logger.info(f"Local scan refreshing prices on {len(_all_opportunities)} Colab results...")
            await refresh_top_leads(limit=len(_all_opportunities))
            scan_state["message"] = f"Prices refreshed. {len(_all_opportunities)} Colab opportunities ready."
        else:
            # Wait for IBKR to complete both scans (takes ~5 minutes)
            scan_state["message"] = "Waiting for IBKR scans to complete..."
            logger.info("IBKR runs 2 scans - waiting 6 minutes for completion...")
            scan_state["progress"] = 48
            scan_state["phase"] = "IBKR scanning"
            await asyncio.sleep(360)  # 6 minutes for IBKR to finish both scans
            
            # Upload to Colab and auto-execute via Colab API
            try:
                import os
                import requests
                notebook_path = '/app/Cloud_GPU_Matcher_v4_Stable.ipynb'
                
                if os.path.exists(notebook_path):
                    logger.info(f"Preparing Colab notebook: {notebook_path}")
                    
                    # Read notebook content
                    with open(notebook_path, 'r') as f:
                        notebook_content = f.read()
                    
                    # Get ngrok URL and update WebSocket URL
                    try:
                        import httpx
                        tunnels_resp = httpx.get("http://ngrok-tunnel:4040/api/tunnels", timeout=5.0)
                        tunnels = tunnels_resp.json().get("tunnels", [])
                        if tunnels:
                            ngrok_url = tunnels[0].get("public_url", "")
                            ws_url = ngrok_url.replace("https://", "wss://") + "/ws"
                            import re
                            pattern = r'WS_URL_PLACEHOLDER = \\"REPLACE_ME\\"'
                            notebook_content = re.sub(pattern, f'WS_URL = "{ws_url}"', notebook_content)
                            logger.info(f"Notebook WS_URL updated to: {ws_url}")
                    except Exception as e:
                        logger.warning(f"Could not update WS_URL: {e}")
                    
                    # Upload to GitHub Gist
                    github_token = os.environ.get('GITHUB_TOKEN')
                    if not github_token:
                        logger.error("No GitHub token - add GITHUB_TOKEN to .env")
                        scan_state["message"] = "GitHub auth missing"
                    else:
                        gist_data = {
                            'description': f'Arbitrage Scanner - {datetime.utcnow().isoformat()}',
                            'public': True,
                            'files': {
                                'Cloud_GPU_Matcher_v4_Stable.ipynb': {
                                    'content': notebook_content
                                }
                            }
                        }
                        
                        gist_resp = requests.post(
                            'https://api.github.com/gists',
                            json=gist_data,
                            headers={
                                'Authorization': f'token {github_token}',
                                'Accept': 'application/vnd.github.v3+json'
                            },
                            timeout=30
                        )
                        
                        if gist_resp.status_code == 201:
                            gist = gist_resp.json()
                            gist_id = gist['id']
                            logger.info(f"Gist created: {gist_id}")

                            # Trigger Oracle Cloud executor to auto-run the notebook
                            oracle_executor_url = os.environ.get('ORACLE_EXECUTOR_URL', 'http://localhost:5000')

                            try:
                                logger.info(f"Triggering Colab executor: {oracle_executor_url}")
                                exec_resp = requests.post(
                                    f'{oracle_executor_url}/execute',
                                    json={'gist_id': gist_id, 'owner': 'Bamove6969'},
                                    timeout=10
                                )
                                if exec_resp.status_code == 200:
                                    exec_data = exec_resp.json()
                                    logger.info(f"Executor queued: {exec_data}")
                                    scan_state["message"] = f"Colab auto-executing (queue: {exec_data.get('queue_position', '?')})"
                                    scan_state["gist_id"] = gist_id
                                    scan_state["executor_status"] = "queued"
                                else:
                                    logger.warning(f"Executor response: {exec_resp.status_code}")
                                    colab_url = f'https://colab.research.google.com/gist/Bamove6969/{gist_id}/Cloud_GPU_Matcher_v4_Stable.ipynb'
                                    scan_state["message"] = f"Executor failed - manual: {colab_url}"
                                    scan_state["colab_url"] = colab_url
                            except Exception as e:
                                logger.warning(f"Could not reach Oracle executor: {e}")
                                colab_url = f'https://colab.research.google.com/gist/Bamove6969/{gist_id}/Cloud_GPU_Matcher_v4_Stable.ipynb'
                                scan_state["message"] = f"Executor offline - manual: {colab_url}"
                                scan_state["colab_url"] = colab_url
                        else:
                            logger.error(f"GitHub API error: {gist_resp.status_code}")
                            scan_state["message"] = "GitHub upload failed"
                else:
                    logger.warning(f"Notebook not found: {notebook_path}")
            except Exception as e:
                logger.error(f"Colab automation error: {e}")

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
