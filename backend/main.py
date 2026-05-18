import asyncio
import json
import uuid
import logging
import collections
from datetime import datetime
from typing import Optional, List, Dict, Any

# Robust log broadcasting system
class LogBroadcaster:
    def __init__(self):
        self.listeners = set() # Set of (queue, loop)

    def subscribe(self):
        loop = asyncio.get_event_loop()
        q = asyncio.Queue()
        self.listeners.add((q, loop))
        return q

    def unsubscribe(self, q):
        # Find and remove the (q, loop) tuple
        self.listeners = {item for item in self.listeners if item[0] is not q}

    def broadcast(self, message):
        """Thread-safe broadcast to all listeners."""
        for q, loop in list(self.listeners):
            try:
                if loop.is_running():
                    loop.call_soon_threadsafe(q.put_nowait, message)
            except Exception:
                pass

log_broadcaster = LogBroadcaster()

# A simple ring buffer to keep the last N log messages
class LogRingBuffer(logging.Handler):
    def __init__(self, maxlen=200):
        super().__init__()
        self.log_buffer = collections.deque(maxlen=maxlen)
        self.count = 0
        self.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s', '%H:%M:%S'))

    def emit(self, record):
        try:
            msg = self.format(record)
            self.log_buffer.append(msg)
            self.count += 1
            # Push to all active SSE listeners
            log_broadcaster.broadcast(msg)
        except Exception:
            self.handleError(record)

# Attach the ring buffer to the root logger so it catches all logs
memory_handler = LogRingBuffer(maxlen=200)

from fastapi import FastAPI, Query, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel

from backend.database import init_db, get_db
from backend.scanner import (
    run_scan, get_scan_state, get_cached_markets, 
    get_cached_opportunities, auto_scan_loop,
    set_auto_scan, get_auto_scan
)
from backend import whale_tracker, weather_edge

logger = logging.getLogger(__name__)

app = FastAPI(title="Arbitrage Scanner API")

# ===========================================
# WebSocket Connection Manager
# ===========================================
class WSConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Active: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        for conn in self.active_connections:
            try:
                await conn.send_json(message)
            except Exception as e:
                logger.warning(f"WS broadcast failed: {e}")

ws_manager = WSConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            msg_type = msg.get("type")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong", "time": datetime.now().isoformat()})

            elif msg_type == "subscribe" and msg.get("channel") == "markets":
                # Colab requests market data — send all current markets
                from backend.scanner import get_cached_markets
                markets = get_cached_markets()
                await websocket.send_json({"type": "markets_data", "markets": markets, "count": len(markets)})
                logger.info(f"Sent {len(markets)} markets to Colab via WebSocket")

            elif msg_type == "subscribe":
                await websocket.send_json({"type": "subscribed", "channels": ["scan", "matches"]})

            elif msg_type == "cloud_results":
                # Colab sends back matched pairs
                from backend.scanner import set_cloud_results
                pairs = msg.get("data", [])
                logger.info(f"Received {len(pairs)} matched pairs from Colab via WebSocket")
                set_cloud_results(pairs)
                await websocket.send_json({
                    "type": "results_received",
                    "message": f"Received {len(pairs)} pairs, processing complete",
                    "count": len(pairs)
                })

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)

# Helper to broadcast from anywhere
async def broadcast_scan_update(event: str, data: dict):
    await ws_manager.broadcast({"event": event, "data": data, "time": datetime.now().isoformat()})

# Enable scanner broadcasts
@app.on_event("startup")
async def setup_broadcasts():
    try:
        from backend import scanner
        scanner._broadcast_enabled = True
        logger.info("WebSocket broadcast enabled for scanner")
    except Exception as e:
        logger.warning(f"Could not enable scanner broadcasts: {e}")

@app.on_event("startup")
async def startup():
    # Configure logging safely during startup
    logging.basicConfig(level=logging.INFO)
    root_logger = logging.getLogger()
    root_logger.addHandler(memory_handler)

    # Specifically also catch uvicorn logs
    for name in ["uvicorn", "uvicorn.error", "uvicorn.access"]:
        l = logging.getLogger(name)
        l.addHandler(memory_handler)
        l.propagate = True

    # Redirect print statements to the log buffer
    import sys
    class StreamToLogger:
        def __init__(self, logger, log_level):
            self.logger = logger
            self.log_level = log_level

        def write(self, buf):
            for line in buf.rstrip().splitlines():
                self.logger.log(self.log_level, line.rstrip())

        def flush(self):
            pass

        def isatty(self):
            return False

    sys.stdout = StreamToLogger(root_logger, logging.INFO)
    sys.stderr = StreamToLogger(root_logger, logging.ERROR)

    logger.info("--- ARBITRAGE BACKEND INITIALIZED ---")
    logger.info("Log buffer initialized with stdout/stderr redirection")

    await init_db()
    asyncio.create_task(auto_scan_loop())
    logger.info("Backend started, auto-scan loop initiated")


@app.post("/api/reset-scan")
async def reset_scan():
    """Force-reset stuck scan state so a new scan can be triggered."""
    from backend.scanner import scan_state
    scan_state["is_scanning"] = False
    scan_state["status"] = "idle"
    scan_state["phase"] = "idle"
    scan_state["message"] = "Scan reset by user"
    scan_state["progress"] = 0
    return {"status": "reset", "message": "Scan state reset"}


@app.get("/api/health")
async def health():
    """Container healthcheck endpoint."""
    return {"status": "ok", "service": "arbitrage-backend"}


@app.post("/api/scan")
async def trigger_scan(request: Request = None):
    try:
        body = await request.json() if request else {}
    except Exception:
        body = {}
    platforms = body.get("platforms", None)
    state = get_scan_state()
    if state["is_scanning"]:
        return {"status": "already_scanning", "message": "A scan is already in progress"}
    asyncio.create_task(run_scan(platforms))
    return {"status": "started", "message": "Scan started in background"}


@app.get("/api/scan-status")
async def scan_status():
    state = get_scan_state()
    return state


@app.get("/api/scan-progress")
async def scan_progress():
    async def event_stream():
        last_state = None
        idle_count = 0
        while True:
            state = get_scan_state()
            state_str = json.dumps({
                "percent": state["progress"],
                "phase": state["phase"],
                "message": state["message"],
                "status": state["status"],
                "totalComparisons": state.get("total_comparisons", 0),
                "completedComparisons": state.get("completed_comparisons", 0),
                "pairsFound": state.get("pairs_found", 0),
                "totalMarkets": state.get("total_markets", 0),
            })
            if state_str != last_state:
                yield f"data: {state_str}\n\n"
                last_state = state_str
                idle_count = 0
            else:
                idle_count += 1

            if state["status"] in ("complete", "error"):
                yield f"data: {state_str}\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/raw-markets")
async def serve_raw_markets():
    """Serves all discovered markets as a direct JSON download for Cloud GPU processing."""
    markets = get_cached_markets()
    if not markets:
        return []  # Return empty list so Colab handles it gracefully (not a dict)
    return markets

@app.post("/api/cloud-results")
async def receive_cloud_results(results: List[Dict[str, Any]], clear: bool = Query(True)):
    """Receives parsed and matched arbitrage pairs from the Cloud GPU notebook.
    Runs LLM verification to filter to only exact matches.
    
    Args:
        results: The matched pairs to import.
        clear: If True (default), wipes any existing cloud results before importing.
               Set to False when sending multiple additive batches.
    """
    from backend.scanner import set_cloud_results
    from backend.llm_openrouter import verify_matches_openrouter
    
    # Store the fuzzy matches
    set_cloud_results(results, clear=clear)
    
    # Run LLM verification in background (OpenRouter - 2 models x 2 workers = 4 parallel)
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.create_task(verify_matches_openrouter(results[:2000], num_workers=2))
    
    return {"status": "success", "imported": len(results), "llm_verifying": True}


@app.get("/api/scanner-config")
async def get_scanner_config():
    return {"autoScan": get_auto_scan()}


@app.post("/api/scanner-config")
async def update_scanner_config(request: Request):
    body = await request.json()
    enabled = body.get("enabled", False)
    set_auto_scan(enabled)
    return {"status": "ok", "autoScan": enabled}


@app.get("/api/logs")
async def stream_logs():
    """Streams the backend python logs to the frontend in real-time."""
    async def log_stream():
        # 1. Initial burst of history
        history = list(memory_handler.log_buffer)
        if history:
            yield f"data: {json.dumps({'logs': history})}\n\n"
        else:
            yield f"data: {json.dumps({'logs': ['--- Log stream connected. Waiting for activity... ---']})}\n\n"
        
        # 2. Subscribe to live updates
        q = log_broadcaster.subscribe()
        try:
            logger.info("New SSE client connected to log stream")
            while True:
                msg = await q.get()
                # The client expects {logs: [string, string]}
                # We send one line at a time but wrapped as history for simplicity 
                # or we can send the full updated buffer. 
                # Let's send the full buffer to keep it in sync with the current client logic.
                full_history = list(memory_handler.log_buffer)
                yield f"data: {json.dumps({'logs': full_history})}\n\n"
        finally:
            log_broadcaster.unsubscribe(q)

    return StreamingResponse(log_stream(), media_type="text/event-stream")


@app.get("/api/markets")
async def get_markets(q: Optional[str] = None):
    markets = get_cached_markets()
    if q:
        q_lower = q.lower()
        markets = [m for m in markets if q_lower in m["title"].lower()]
    return markets


@app.get("/api/market-stats")
async def market_stats():
    markets = get_cached_markets()
    manifold_count = sum(1 for m in markets if m["platform"] == "Manifold")
    poly_count = sum(1 for m in markets if m["platform"] == "Polymarket")
    pi_count = sum(1 for m in markets if m["platform"] == "PredictIt")
    ibkr_count = sum(1 for m in markets if m["platform"].lower() == "ibkr")
    state = get_scan_state()
    return {
        "manifold": manifold_count,
        "polymarket": poly_count,
        "predictit": pi_count,
        "ibkr": ibkr_count,
        "total": len(markets),
        "lastUpdated": state.get("last_scan_time", datetime.utcnow().isoformat()),
    }


@app.get("/api/arbitrage-opportunities")
async def get_opportunities(
    q: Optional[str] = None,
    minRoi: float = 0,
    platforms: Optional[str] = None,
    page: int = Query(1, ge=1, le=10),
    limit: int = Query(300, ge=1, le=1000),
    llm_verified: bool = False,
):
    from backend.llm_verifier import get_llm_verified_matches
    
    if llm_verified:
        opps = get_llm_verified_matches()
    else:
        opps = get_cached_opportunities()

    if q:
        q_lower = q.lower()
        opps = [o for o in opps if
                q_lower in o["marketA"]["title"].lower() or
                q_lower in o["marketB"]["title"].lower()]

    if minRoi > 0:
        opps = [o for o in opps if o["roi"] >= minRoi]

    if platforms:
        platform_set = set(p.strip().lower() for p in platforms.split(","))
        def matches_platform(o):
            platforms_in_opp = {o["marketA"]["platform"].lower(), o["marketB"]["platform"].lower()}
            return bool(platforms_in_opp & platform_set)
        opps = [o for o in opps if matches_platform(o)]

    start = (page - 1) * limit
    end = start + limit
    paginated = opps[start:end]

    return paginated


@app.post("/api/refresh-leads")
async def refresh_leads(limit: int = Query(20, ge=1, le=100)):
    """Triggers a targeted price refresh for the top N opportunities."""
    from backend.scanner import refresh_top_leads
    count = await refresh_top_leads(limit=limit)
    return {"status": "success", "updated_markets": count}


class WatchlistCreate(BaseModel):
    marketName: str
    siteAName: str
    siteBName: str
    siteAYesPrice: float
    siteBYesPrice: float
    investment: float = 500
    alertThreshold: float = 3.0
    isActive: bool = True

class WatchlistUpdate(BaseModel):
    isActive: Optional[bool] = None
    siteAYesPrice: Optional[float] = None
    siteBYesPrice: Optional[float] = None
    investment: Optional[float] = None
    alertThreshold: Optional[float] = None
    lastChecked: Optional[str] = None
    lastMakerRoi: Optional[float] = None
    lastTakerRoi: Optional[float] = None


@app.get("/api/watchlist")
async def get_watchlist():
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM watchlist ORDER BY created_at DESC")
        rows = await cursor.fetchall()
        return [
            {
                "id": r["id"],
                "marketName": r["market_name"],
                "siteAName": r["site_a_name"],
                "siteBName": r["site_b_name"],
                "siteAYesPrice": r["site_a_yes_price"],
                "siteBYesPrice": r["site_b_yes_price"],
                "investment": r["investment"],
                "alertThreshold": r["alert_threshold"],
                "isActive": bool(r["is_active"]),
                "lastChecked": r["last_checked"],
                "lastMakerRoi": r["last_maker_roi"],
                "lastTakerRoi": r["last_taker_roi"],
                "createdAt": r["created_at"],
            }
            for r in rows
        ]
    finally:
        await db.close()


@app.post("/api/watchlist")
async def create_watchlist(item: WatchlistCreate):
    item_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO watchlist (id, market_name, site_a_name, site_b_name,
               site_a_yes_price, site_b_yes_price, investment, alert_threshold, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item_id, item.marketName, item.siteAName, item.siteBName,
             item.siteAYesPrice, item.siteBYesPrice, item.investment,
             item.alertThreshold, 1 if item.isActive else 0),
        )
        await db.commit()
    finally:
        await db.close()
    return {"id": item_id, **item.model_dump()}


@app.patch("/api/watchlist/{item_id}")
async def update_watchlist(item_id: str, updates: WatchlistUpdate):
    db = await get_db()
    try:
        fields = []
        values: List[Any] = []
        if updates.isActive is not None:
            fields.append("is_active = ?")
            values.append(1 if updates.isActive else 0)
        if updates.siteAYesPrice is not None:
            fields.append("site_a_yes_price = ?")
            values.append(updates.siteAYesPrice)
        if updates.siteBYesPrice is not None:
            fields.append("site_b_yes_price = ?")
            values.append(updates.siteBYesPrice)
        if updates.investment is not None:
            fields.append("investment = ?")
            values.append(updates.investment)
        if updates.alertThreshold is not None:
            fields.append("alert_threshold = ?")
            values.append(updates.alertThreshold)
        if updates.lastChecked is not None:
            fields.append("last_checked = ?")
            values.append(updates.lastChecked)
        if updates.lastMakerRoi is not None:
            fields.append("last_maker_roi = ?")
            values.append(updates.lastMakerRoi)
        if updates.lastTakerRoi is not None:
            fields.append("last_taker_roi = ?")
            values.append(updates.lastTakerRoi)

        if fields:
            values.append(item_id)
            await db.execute(f"UPDATE watchlist SET {', '.join(fields)} WHERE id = ?", values)
            await db.commit()
    finally:
        await db.close()
    return {"id": item_id, "updated": True}


@app.delete("/api/watchlist/{item_id}")
async def delete_watchlist(item_id: str):
    db = await get_db()
    try:
        await db.execute("DELETE FROM watchlist WHERE id = ?", (item_id,))
        await db.commit()
    finally:
        await db.close()
    return {"deleted": True}


    isRead: bool = False

class AlertCreate(BaseModel):
    watchlistId: Optional[str] = None
    marketName: str
    makerRoi: float = 0
    takerRoi: float = 0
    siteAYesPrice: Optional[float] = None
    siteBYesPrice: Optional[float] = None
    isRead: bool = False

class FeedbackCreate(BaseModel):
    marketAId: str
    marketATitle: Optional[str] = None
    marketAPlatform: Optional[str] = None
    marketBId: str
    marketBTitle: Optional[str] = None
    marketBPlatform: Optional[str] = None
    matchScore: Optional[float] = 0
    matchReason: Optional[str] = None
    verdict: str # 'correct' or 'incorrect'


@app.get("/api/whales/leaderboard")
async def get_whale_leaderboard():
    return whale_tracker.fetch_leaderboard()

@app.get("/api/whales/activity")
async def get_whale_activity(address: str):
    return whale_tracker.fetch_whale_activity(address)

@app.get("/api/weather/edges")
async def get_weather_edges():
    markets = get_cached_markets()
    # Filter for weather markets
    weather_markets = [m for m in markets if m.get("isWeather") == 1]
    
    results = []
    for m in weather_markets:
        analysis = weather_edge.analyze_weather_market(m)
        if analysis.get("edge", 0) != 0:
            results.append({
                "market": m,
                "analysis": analysis
            })
            
    # Sort by absolute edge
    results.sort(key=lambda x: abs(x["analysis"]["edge"]), reverse=True)
    return results

@app.get("/api/match-feedback")
async def get_match_feedback():
    db = await get_db()
    try:
        cursor = await db.execute("SELECT market_a_id, market_b_id, verdict FROM feedback")
        rows = await cursor.fetchall()
        return [
            {
                "marketAId": r["market_a_id"],
                "marketBId": r["market_b_id"],
                "verdict": r["verdict"]
            }
            for r in rows
        ]
    finally:
        await db.close()

@app.post("/api/match-feedback")
async def create_feedback(item: FeedbackCreate):
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO feedback (
                market_a_id, market_a_title, market_a_platform,
                market_b_id, market_b_title, market_b_platform,
                match_score, match_reason, verdict
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.marketAId, item.marketATitle, item.marketAPlatform,
                item.marketBId, item.marketBTitle, item.marketBPlatform,
                item.matchScore, item.matchReason, item.verdict
            )
        )
        await db.commit()
    finally:
        await db.close()
    return {"status": "success"}

@app.get("/api/ml/stats")
async def get_ml_stats():
    db = await get_db()
    try:
        cursor = await db.execute("SELECT verdict, COUNT(*) as count FROM feedback GROUP BY verdict")
        rows = await cursor.fetchall()
        stats = {r["verdict"]: r["count"] for r in rows}
        
        cursor = await db.execute("SELECT COUNT(*) FROM matched_pairs")
        total_matches = (await cursor.fetchone())[0]
        
        return {
            "feedback": stats,
            "totalMatches": total_matches,
            "trainingSamples": sum(stats.values())
        }
    finally:
        await db.close()

@app.post("/api/ml/export")
async def export_ml_data():
    from backend.data_collector import export_feedback_to_csv
    filepath = await export_feedback_to_csv()
    if filepath:
        return {"status": "success", "file": filepath}
    return {"status": "error", "message": "Export failed"}


@app.post("/api/alerts")
async def create_alert(alert: AlertCreate):
    alert_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO alert_history (id, watchlist_id, market_name, maker_roi, taker_roi,
               site_a_yes_price, site_b_yes_price, is_read)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (alert_id, alert.watchlistId, alert.marketName, alert.makerRoi,
             alert.takerRoi, alert.siteAYesPrice, alert.siteBYesPrice,
             0 if not alert.isRead else 1),
        )
        await db.commit()
    finally:
        await db.close()
    return {"id": alert_id, **alert.model_dump()}


@app.patch("/api/alerts/{alert_id}/read")
async def mark_alert_read(alert_id: str):
    db = await get_db()
    try:
        await db.execute("UPDATE alert_history SET is_read = 1 WHERE id = ?", (alert_id,))
        await db.commit()
    finally:
        await db.close()
    return {"id": alert_id, "isRead": True}


@app.delete("/api/alerts")
async def clear_alerts():
    db = await get_db()
    try:
        await db.execute("DELETE FROM alert_history")
        await db.commit()
    finally:
        await db.close()
    return {"cleared": True}


class FeedbackCreate(BaseModel):
    marketAId: str
    marketATitle: Optional[str] = None
    marketAPlatform: Optional[str] = None
    marketBId: str
    marketBTitle: Optional[str] = None
    marketBPlatform: Optional[str] = None
    matchScore: Optional[float] = None
    matchReason: Optional[str] = None
    verdict: str


@app.post("/api/match-feedback")
async def submit_feedback(fb: FeedbackCreate):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id FROM feedback WHERE market_a_id = ? AND market_b_id = ?",
            (fb.marketAId, fb.marketBId),
        )
        existing = await cursor.fetchone()
        if existing:
            await db.execute(
                "UPDATE feedback SET verdict = ?, match_score = ?, match_reason = ? WHERE market_a_id = ? AND market_b_id = ?",
                (fb.verdict, fb.matchScore, fb.matchReason, fb.marketAId, fb.marketBId),
            )
            await db.commit()
        else:
            await db.execute(
                """INSERT INTO feedback (market_a_id, market_a_title, market_a_platform,
                   market_b_id, market_b_title, market_b_platform,
                   match_score, match_reason, verdict)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (fb.marketAId, fb.marketATitle, fb.marketAPlatform,
                 fb.marketBId, fb.marketBTitle, fb.marketBPlatform,
                 fb.matchScore, fb.matchReason, fb.verdict),
            )
            await db.commit()
    finally:
        await db.close()
    return {"success": True}


@app.get("/api/match-feedback")
async def get_feedback():
    db = await get_db()
    try:
        cursor = await db.execute("SELECT market_a_id, market_b_id, verdict FROM feedback")
        rows = await cursor.fetchall()
        return [{"marketAId": r[0], "marketBId": r[1], "verdict": r[2]} for r in rows]
    finally:
        await db.close()


class ArbitrageHistoryCreate(BaseModel):
    marketName: str
    siteAName: str
    siteBName: str
    siteAYesPrice: float
    siteBYesPrice: float
    investment: float
    orderMode: str = "Maker"
    grossRoi: Optional[float] = None
    netRoi: Optional[float] = None
    netProfit: Optional[float] = None
    shares: Optional[int] = None
    isProfitable: Optional[bool] = None
    scenario: Optional[str] = None
    legCount: Optional[int] = 2
    legsJson: Optional[str] = None


@app.get("/api/arbitrage-history")
async def get_history():
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM arbitrage_history ORDER BY created_at DESC LIMIT 100")
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            entry = {
                "id": r["id"],
                "marketName": r["market_name"],
                "siteAName": r["site_a_name"],
                "siteBName": r["site_b_name"],
                "siteAYesPrice": r["site_a_yes_price"],
                "siteBYesPrice": r["site_b_yes_price"],
                "investment": r["investment"],
                "orderMode": r["order_mode"],
                "grossRoi": r["gross_roi"],
                "netRoi": r["net_roi"],
                "netProfit": r["net_profit"],
                "shares": r["shares"],
                "createdAt": r["created_at"],
            }
            try:
                entry["isProfitable"] = bool(r["is_profitable"])
            except (IndexError, KeyError):
                entry["isProfitable"] = (r["net_profit"] or 0) > 0
            try:
                entry["scenario"] = r["scenario"]
            except (IndexError, KeyError):
                entry["scenario"] = None
            try:
                entry["legCount"] = r["leg_count"] or 2
            except (IndexError, KeyError):
                entry["legCount"] = 2
            try:
                entry["legsJson"] = r["legs_json"]
            except (IndexError, KeyError):
                entry["legsJson"] = None
            result.append(entry)
        return result
    finally:
        await db.close()


@app.post("/api/arbitrage-history")
async def save_history(entry: ArbitrageHistoryCreate):
    entry_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO arbitrage_history (id, market_name, site_a_name, site_b_name,
               site_a_yes_price, site_b_yes_price, investment, order_mode,
               gross_roi, net_roi, net_profit, shares, is_profitable, scenario,
               leg_count, legs_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entry_id, entry.marketName, entry.siteAName, entry.siteBName,
             entry.siteAYesPrice, entry.siteBYesPrice, entry.investment,
             entry.orderMode, entry.grossRoi, entry.netRoi, entry.netProfit,
             entry.shares, 1 if entry.isProfitable else 0,
             entry.scenario, entry.legCount or 2, entry.legsJson),
        )
        await db.commit()
    finally:
        await db.close()
    return {"id": entry_id, **entry.model_dump()}


@app.delete("/api/arbitrage-history")
async def clear_history():
    db = await get_db()
    try:
        await db.execute("DELETE FROM arbitrage_history")
        await db.commit()
    finally:
        await db.close()
    return {"cleared": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
