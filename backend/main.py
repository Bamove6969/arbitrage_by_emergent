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
    from backend.scanner import load_cached_from_db
    await load_cached_from_db()
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


@app.get("/live", response_class=None)
async def live_monitor():
    """Real-time scan monitor. Accessible via the ngrok public URL."""
    from fastapi.responses import HTMLResponse
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Arbitrage Scanner — Live</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0e1a;color:#e2e8f0;font-family:'Courier New',monospace;padding:12px;font-size:13px}
h1{color:#7dd3fc;font-size:18px;margin-bottom:12px;letter-spacing:1px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px}
.card{background:#111827;border:1px solid #1e3a5f;border-radius:8px;padding:12px}
.label{color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px}
.value{color:#f1f5f9;font-size:20px;font-weight:bold}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:bold;margin-top:4px}
.scanning{background:#1e3a5f;color:#7dd3fc}
.complete{background:#052e16;color:#4ade80}
.idle{background:#1c1917;color:#78716c}
.error{background:#450a0a;color:#f87171}
.waiting{background:#2d1b4e;color:#a78bfa}
#progress-bar-wrap{background:#1e293b;border-radius:4px;height:8px;margin:8px 0}
#progress-bar{background:#3b82f6;height:8px;border-radius:4px;transition:width .3s}
#phase{color:#7dd3fc;font-size:12px;margin-bottom:4px}
#message{color:#94a3b8;font-size:12px;margin-bottom:8px;min-height:16px}
#ibkr-rounds{color:#f59e0b;font-size:13px;margin-bottom:8px}
#log-box{background:#0d1117;border:1px solid #1e3a5f;border-radius:6px;padding:10px;height:340px;overflow-y:auto;font-size:11px;line-height:1.5}
.log-line{color:#94a3b8}
.log-line.INFO{color:#93c5fd}
.log-line.WARN{color:#fbbf24}
.log-line.ERROR{color:#f87171}
#platform-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:8px}
.plat{background:#0f172a;border:1px solid #1e3a5f;border-radius:6px;padding:6px;text-align:center}
.plat-name{font-size:10px;color:#64748b}
.plat-count{font-size:16px;color:#7dd3fc;font-weight:bold}
.tabs{display:flex;gap:6px;margin-bottom:14px;border-bottom:1px solid #1e3a5f}
.tab{background:none;border:none;color:#64748b;font-family:inherit;font-size:13px;font-weight:bold;
     padding:8px 16px;cursor:pointer;border-bottom:2px solid transparent;letter-spacing:.5px}
.tab:hover{color:#94a3b8}
.tab.active{color:#7dd3fc;border-bottom-color:#3b82f6}
.tabpane{display:none}
.tabpane.active{display:block}
/* Kaggle stepper */
#kaggle-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
#kaggle-kernel{color:#94a3b8;font-size:12px}
#kaggle-kernel a{color:#7dd3fc;text-decoration:none}
.steps{display:flex;flex-direction:column;gap:8px}
.step{display:flex;align-items:flex-start;gap:10px;background:#111827;border:1px solid #1e3a5f;
      border-radius:8px;padding:10px 12px;transition:border-color .3s}
.step.running{border-color:#3b82f6;box-shadow:0 0 0 1px #3b82f6 inset}
.step.done{border-color:#166534}
.step.error{border-color:#991b1b}
.step-icon{font-size:15px;width:20px;text-align:center;flex:none;margin-top:1px}
.step.pending .step-icon{color:#475569}
.step.running .step-icon{color:#7dd3fc}
.step.done .step-icon{color:#4ade80}
.step.error .step-icon{color:#f87171}
.step-body{flex:1;min-width:0}
.step-name{font-size:13px;color:#e2e8f0}
.step.pending .step-name{color:#64748b}
.step-msg{font-size:11px;color:#94a3b8;margin-top:2px;word-break:break-word}
.step-time{font-size:10px;color:#475569;flex:none;margin-top:2px}
.spin{display:inline-block;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
/* Kaggle tab: two-session layout (everything runs in Kaggle, no local Ollama) */
.kaggle-intro{color:#94a3b8;font-size:12px;margin-bottom:12px;line-height:1.6}
.kaggle-intro b{color:#7dd3fc}
.t4-badge{display:inline-block;background:#0f172a;border:1px solid #1e3a5f;border-radius:4px;
          padding:1px 7px;color:#a78bfa;font-size:10px;font-weight:bold;margin-left:6px;letter-spacing:.3px}
#verifier-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
#verifier-kernel{color:#94a3b8;font-size:12px}
#verifier-kernel a{color:#7dd3fc;text-decoration:none}
.verifier-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:6px}
.vs-cell{background:#0f172a;border:1px solid #1e3a5f;border-radius:6px;padding:8px;text-align:center}
.vs-cell .n{font-size:20px;font-weight:bold;color:#7dd3fc;font-variant-numeric:tabular-nums}
.vs-cell.green .n{color:#4ade80}
.vs-cell .l{font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.4px;margin-top:2px}
.verifier-hint{font-size:11px;color:#475569;margin-top:6px}
/* Ollama verification */
#ollama-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
#ollama-flow{color:#94a3b8;font-size:12px;margin-bottom:14px;line-height:1.6}
#ollama-flow b{color:#7dd3fc}
.split-pill{display:inline-block;background:#0f172a;border:1px solid #1e3a5f;border-radius:12px;
            padding:1px 10px;color:#a78bfa;font-weight:bold}
.lanes{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:760px){.lanes{grid-template-columns:1fr}}
.lane{background:#111827;border:1px solid #1e3a5f;border-radius:10px;padding:14px}
.lane.active{border-color:#3b82f6}
.lane-top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px}
.lane-model{color:#7dd3fc;font-size:13px;font-weight:bold;word-break:break-all}
.lane-inst{color:#475569;font-size:10px;text-transform:uppercase;letter-spacing:.5px}
.countdown{font-size:34px;font-weight:bold;color:#f1f5f9;line-height:1.1}
.countdown small{font-size:13px;color:#64748b;font-weight:normal}
.lane-bar{background:#1e293b;border-radius:4px;height:7px;margin:8px 0}
.lane-bar > div{background:linear-gradient(90deg,#3b82f6,#7dd3fc);height:7px;border-radius:4px;transition:width .4s}
.workers{display:flex;gap:6px;margin:8px 0 4px}
.wkr{flex:1;height:22px;border-radius:5px;background:#0f172a;border:1px solid #1e3a5f;
     display:flex;align-items:center;justify-content:center;font-size:10px;color:#475569;transition:all .3s}
.wkr.busy{background:#1e3a5f;border-color:#3b82f6;color:#7dd3fc;animation:pulse 1.1s ease-in-out infinite}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(59,130,246,.5)}50%{box-shadow:0 0 0 4px rgba(59,130,246,0)}}
.exact{margin-top:8px;font-size:12px;color:#64748b}
.exact b{color:#4ade80;font-size:20px;font-variant-numeric:tabular-nums}
#ollama-total{margin-top:14px;text-align:center;font-size:13px;color:#94a3b8}
#ollama-total b{color:#4ade80;font-size:22px}
</style>
</head>
<body>
<h1>⚡ ARBITRAGE SCANNER — LIVE</h1>

<div class="tabs">
  <button class="tab active" data-pane="pane-scan">📡 SCAN</button>
  <button class="tab" data-pane="pane-kaggle">🖥️ KAGGLE GPU</button>
  <button class="tab" data-pane="pane-ollama">🧠 OLLAMA VERIFY</button>
</div>

<div id="pane-scan" class="tabpane active">
<div class="grid">
<div class="card">
  <div class="label">Status</div>
  <div id="status-badge" class="badge idle">idle</div>
  <div id="ibkr-rounds">IBKR rounds: 0 / 2</div>
  <div id="phase">—</div>
  <div id="message"></div>
  <div id="progress-bar-wrap"><div id="progress-bar" style="width:0%"></div></div>
</div>
<div class="card">
  <div class="label">Markets Loaded</div>
  <div id="total-markets" class="value">0</div>
  <div id="platform-stats">
    <div class="plat"><div class="plat-name">Polymarket</div><div id="c-poly" class="plat-count">0</div></div>
    <div class="plat"><div class="plat-name">PredictIt</div><div id="c-pi" class="plat-count">0</div></div>
    <div class="plat"><div class="plat-name">IBKR</div><div id="c-ibkr" class="plat-count">0</div></div>
  </div>
</div>
</div>

<div class="card" style="margin-bottom:10px">
  <div class="label">Log Stream</div>
  <div id="log-box"></div>
</div>
</div><!-- /pane-scan -->

<div id="pane-kaggle" class="tabpane">
  <div class="kaggle-intro">
    Both stages run <b>inside Kaggle</b> on dual&nbsp;T4 GPUs — nothing is sent to a local Ollama.
    <b>Session&nbsp;1</b> embeds &amp; matches every market; <b>Session&nbsp;2</b> verifies the matches
    with qwen3.6:27b over the tailnet.
  </div>

  <!-- Session 1 · Matcher (cloud-gpu-matcher-v4-stable) -->
  <div class="card" style="margin-bottom:10px">
    <div id="kaggle-head">
      <div>
        <div class="label">Session 1 · Matcher <span class="t4-badge">Kaggle GPU T4×2</span></div>
        <div id="kaggle-state-badge" class="badge idle">idle</div>
      </div>
      <div id="kaggle-kernel">—</div>
    </div>
    <div id="steps" class="steps"></div>
  </div>

  <!-- Session 2 · Verifier (ollama-verifier-v1) -->
  <div class="card">
    <div id="verifier-head">
      <div>
        <div class="label">Session 2 · Verifier <span class="t4-badge">Kaggle GPU T4×2</span></div>
        <div id="verifier-state-badge" class="badge idle">idle</div>
      </div>
      <div id="verifier-kernel">—</div>
    </div>
    <div id="verifier-summary"><div style="color:#475569;font-size:12px">Waiting for Session 1 to park matches…</div></div>
    <div class="verifier-hint">Live per-worker detail in the 🧠 OLLAMA VERIFY tab.</div>
  </div>
</div><!-- /pane-kaggle -->

<div id="pane-ollama" class="tabpane">
  <div class="card">
    <div id="ollama-head">
      <div>
        <div class="label">LLM Verification · qwen3.6:27b on Kaggle dual-T4</div>
        <div id="ollama-badge" class="badge idle">idle</div>
      </div>
    </div>
    <div id="ollama-flow">
      Pulled <b id="ol-received">0</b> matches from Session 1 →
      verified <b id="ol-done">0</b> one-by-one on <b>qwen3.6:27b</b>
      (dual-T4 sharded, <b id="ol-workers">3</b> workers) → strict 100%-meaning check.
    </div>
    <div class="lanes" id="ol-lanes"></div>
    <div id="ollama-total">Exact meaning-matches confirmed: <b id="ol-total">0</b></div>
  </div>
</div><!-- /pane-ollama -->

<script>
const badgeClass = {scanning:'scanning',complete:'complete',idle:'idle',error:'error',
                    waiting_for_cloud:'waiting',fetching:'scanning'};

async function pollStatus() {
  try {
    const r = await fetch('/api/scan-status');
    const s = await r.json();
    const sb = document.getElementById('status-badge');
    sb.textContent = s.status || 'idle';
    sb.className = 'badge ' + (badgeClass[s.status] || 'idle');
    document.getElementById('phase').textContent = s.phase || '—';
    document.getElementById('message').textContent = s.message || '';
    document.getElementById('progress-bar').style.width = (s.progress||0)+'%';
    // IBKR shown as two human-friendly rounds: Round 1 = REST discovery,
    // Round 2 = TWS pricing. Keyed off the fetcher's progress message
    // ("discovering X/Y" = REST, "... (TWS)" = TWS pricing).
    // rd: 0 = round 1 running, 1 = round 2 running, 2 = both done.
    // Each round runs two sub-steps: REST discovery then TWS pricing.
    const rd = s.ibkr_scan_rounds_done || 0;
    const msg = s.message || '';
    const dm = msg.match(/discovering\\s+([\\d,]+)\\s*\\/\\s*([\\d,]+)\\s*(\\(TWS\\))?/i);
    let rtxt;
    // Number reflects the sub-step being performed: REST discovery = 1, TWS pricing = 2.
    const isTws = dm ? !!dm[3] : null;
    if (rd >= 2) {
      rtxt = 'IBKR: REST + TWS complete ✓';
    } else if (dm) {
      const sub = isTws ? 'TWS pricing' : 'REST discovery';
      rtxt = 'IBKR Round ' + (isTws ? 2 : 1) + ' / 2 · ' + sub + ' — ' + dm[1] + ' / ' + dm[2];
    } else if (s.status === 'scanning') {
      rtxt = rd === 1 ? 'IBKR Round 2 / 2 · TWS warming up…' : 'IBKR Round 1 / 2 · REST starting…';
    } else {
      rtxt = 'IBKR: idle';
    }
    document.getElementById('ibkr-rounds').textContent = rtxt;
    // Markets Loaded: count up live per-platform during a scan (from
    // platform_counts), fall back to committed market-stats when idle.
    const pc = s.platform_counts || {};
    if (s.status === 'scanning') {
      window.__scanning = true;
      const poly = pc.polymarket||0, pi = pc.predictit||0, ib = pc.ibkr||0;
      document.getElementById('c-poly').textContent = poly.toLocaleString();
      document.getElementById('c-pi').textContent   = pi.toLocaleString();
      document.getElementById('c-ibkr').textContent = ib.toLocaleString();
      document.getElementById('total-markets').textContent =
        (s.total_markets || (poly+pi+ib)).toLocaleString();
    } else {
      window.__scanning = false;
      document.getElementById('total-markets').textContent =
        (s.total_markets||0).toLocaleString();
    }
  } catch(e) {}
}

async function pollStats() {
  if (window.__scanning) return;  // live counts handled by pollStatus during a scan
  try {
    const r = await fetch('/api/market-stats');
    const s = await r.json();
    document.getElementById('c-poly').textContent  = (s.polymarket||0).toLocaleString();
    document.getElementById('c-pi').textContent    = (s.predictit||0).toLocaleString();
    document.getElementById('c-ibkr').textContent  = (s.ibkr||0).toLocaleString();
  } catch(e) {}
}

const logBox = document.getElementById('log-box');
const LEVELS = ['ERROR','WARN','INFO'];
function appendLog(line) {
  const d = document.createElement('div');
  d.className = 'log-line ' + (LEVELS.find(l => line.includes('['+l+']')) || '');
  d.textContent = line;
  logBox.appendChild(d);
  if (logBox.children.length > 300) logBox.removeChild(logBox.firstChild);
  logBox.scrollTop = logBox.scrollHeight;
}

const es = new EventSource('/api/logs');
es.onmessage = e => {
  try {
    const d = JSON.parse(e.data);
    (d.logs||[]).forEach(l => appendLog(l));
  } catch(_) {}
};

// ── Tabs ──────────────────────────────────────────────
document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.tabpane').forEach(x => x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById(t.dataset.pane).classList.add('active');
  });
});

// ── Kaggle GPU tab ────────────────────────────────────
const STEP_ICON = {pending:'○', running:'⟳', done:'✓', error:'✗'};
function fmtElapsed(a, b) {
  if (!a) return '';
  const end = b ? new Date(b) : new Date();
  const s = Math.max(0, Math.round((end - new Date(a)) / 1000));
  return s < 60 ? s + 's' : Math.floor(s/60) + 'm ' + (s%60) + 's';
}
async function pollKaggle() {
  try {
    const r = await fetch('/api/kaggle-status');
    const k = await r.json();
    const badge = document.getElementById('kaggle-state-badge');
    const anyErr = (k.stages||[]).some(s => s.status === 'error');
    const allDone = (k.stages||[]).length && k.stages.every(s => s.status === 'done');
    if (anyErr)      { badge.textContent = 'error';    badge.className = 'badge error'; }
    else if (k.running) { badge.textContent = 'running'; badge.className = 'badge scanning'; }
    else if (allDone){ badge.textContent = 'complete'; badge.className = 'badge complete'; }
    else             { badge.textContent = 'waiting';  badge.className = 'badge waiting'; }

    const kd = document.getElementById('kaggle-kernel');
    if (k.kernel) {
      kd.innerHTML = 'kernel: <a href="https://www.kaggle.com/code/' + k.kernel +
                     '" target="_blank">' + k.kernel + '</a>';
    } else { kd.textContent = 'no kernel queued yet'; }

    const box = document.getElementById('steps');
    box.innerHTML = '';
    (k.stages||[]).forEach(s => {
      const div = document.createElement('div');
      div.className = 'step ' + s.status;
      const icon = STEP_ICON[s.status] || '○';
      const spin = s.status === 'running' ? ' spin' : '';
      div.innerHTML =
        '<div class="step-icon"><span class="' + spin.trim() + '">' + icon + '</span></div>' +
        '<div class="step-body"><div class="step-name">' + (s.index+1) + '. ' +
          s.name + '</div>' +
          (s.message ? '<div class="step-msg">' + s.message + '</div>' : '') +
        '</div>' +
        '<div class="step-time">' + fmtElapsed(s.started_at, s.ended_at) + '</div>';
      box.appendChild(div);
    });
  } catch(e) {}
}

// ── Ollama verification tab ───────────────────────────
async function pollOllama() {
  try {
    const r = await fetch('/api/llm-status');
    const k = await r.json();
    const badge = document.getElementById('ollama-badge');
    if (k.running)          { badge.textContent = 'verifying'; badge.className = 'badge scanning'; }
    else if (k.finished_at) { badge.textContent = 'complete';  badge.className = 'badge complete'; }
    else                    { badge.textContent = 'waiting';   badge.className = 'badge waiting'; }

    const insts = k.instances || [];
    const olDone = insts.reduce((a, i) => a + (i.done||0), 0);
    document.getElementById('ol-received').textContent = (k.received||0).toLocaleString();
    document.getElementById('ol-done').textContent = olDone.toLocaleString();
    document.getElementById('ol-workers').textContent =
      insts.length ? (insts[0].workers||3) : 3;
    document.getElementById('ol-total').textContent = (k.total_exact||0).toLocaleString();

    const lanes = document.getElementById('ol-lanes');
    if (!insts.length) {
      lanes.innerHTML = '<div style="color:#475569;font-size:12px;grid-column:1/-1">' +
        'Waiting for Kaggle to return fuzzy matches…</div>';
      return;
    }
    lanes.innerHTML = '';
    insts.forEach(inst => {
      const pct = inst.total ? Math.round(100 * inst.done / inst.total) : 0;
      let wk = '';
      for (let i = 0; i < inst.workers; i++) {
        const busy = i < inst.active;
        wk += '<div class="wkr' + (busy ? ' busy' : '') + '">' +
              (busy ? '⟳' : '·') + '</div>';
      }
      const div = document.createElement('div');
      div.className = 'lane' + (inst.active > 0 ? ' active' : '');
      div.innerHTML =
        '<div class="lane-top"><span class="lane-model">' + inst.model + '</span>' +
          '<span class="lane-inst">dual-T4 sharded</span></div>' +
        '<div class="countdown">' + (inst.remaining||0).toLocaleString() +
          ' <small>/ ' + (inst.total||0).toLocaleString() + ' left</small></div>' +
        '<div class="lane-bar"><div style="width:' + pct + '%"></div></div>' +
        '<div class="workers">' + wk + '</div>' +
        '<div class="exact">exact meaning-matches: <b>' + (inst.exact||0).toLocaleString() + '</b></div>';
      lanes.appendChild(div);
    });
  } catch(e) {}
}

// ── Kaggle tab · Session 2 (verifier) rollup ──────────
// Mirrors /api/llm-status into the Kaggle tab so the second Kaggle notebook
// (qwen3.6:27b on dual T4) shows alongside the matcher. Full per-worker lanes
// remain in the OLLAMA VERIFY tab.
async function pollVerifier() {
  try {
    const r = await fetch('/api/llm-status');
    const k = await r.json();
    const badge = document.getElementById('verifier-state-badge');
    if (k.running)          { badge.textContent = 'verifying'; badge.className = 'badge scanning'; }
    else if (k.finished_at) { badge.textContent = 'complete';  badge.className = 'badge complete'; }
    else                    { badge.textContent = 'waiting';   badge.className = 'badge waiting'; }

    document.getElementById('verifier-kernel').innerHTML =
      'kernel: <a href="https://www.kaggle.com/code/jessefleming/ollama-verifier-v1" target="_blank">jessefleming/ollama-verifier-v1</a>';

    const insts = k.instances || [];
    const received = k.received || 0;
    const done = insts.reduce((a, i) => a + (i.done || 0), 0);
    const exact = k.total_exact || 0;
    const sum = document.getElementById('verifier-summary');
    if (!received) {
      sum.className = '';
      sum.innerHTML = '<div style="color:#475569;font-size:12px">Waiting for Session 1 to park matches…</div>';
    } else {
      sum.className = 'verifier-summary';
      sum.innerHTML =
        '<div class="vs-cell"><div class="n">' + received.toLocaleString() + '</div><div class="l">Received</div></div>' +
        '<div class="vs-cell"><div class="n">' + done.toLocaleString() + '</div><div class="l">Verified</div></div>' +
        '<div class="vs-cell green"><div class="n">' + exact.toLocaleString() + '</div><div class="l">Exact ✓</div></div>';
    }
  } catch(e) {}
}

setInterval(pollStatus, 2000);
setInterval(pollStats,  5000);
setInterval(pollKaggle, 2000);
setInterval(pollOllama, 1500);
setInterval(pollVerifier, 1500);
pollStatus(); pollStats(); pollKaggle(); pollOllama(); pollVerifier();
</script>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/api/llm-matches")
async def llm_matches():
    """Confirmed binary exact-matches after the two-gemma verification pass."""
    from backend.scanner import get_llm_verified_matches
    return {"matches": get_llm_verified_matches()}


@app.get("/api/report/latest")
async def latest_report():
    """Serves the most recent arbitrage HTML report. Reachable through the ngrok tunnel."""
    from fastapi.responses import HTMLResponse, JSONResponse
    from backend.scanner import get_latest_report_path
    from backend.html_report_generator import get_latest_report_path as fs_latest
    path = get_latest_report_path() or fs_latest("/app/reports")
    if not path:
        return JSONResponse(
            {"status": "no_report", "message": "No report generated yet. Trigger a scan first."},
            status_code=404,
        )
    try:
        with open(path, "r") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return JSONResponse({"status": "missing", "path": path}, status_code=404)


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

    # Store the fuzzy matches. set_cloud_results kicks off the single
    # verification + report pipeline (_verify_and_report): local Ollama ->
    # gemma4:31b-cloud, 2 instances x 2 workers, 2000 split into 2x1000,
    # then the HTML report is written to /app/reports/.
    set_cloud_results(results, clear=clear)

    return {"status": "success", "imported": len(results), "llm_verifying": True}


@app.get("/api/verification-queue")
async def verification_queue():
    """Pulled by the Kaggle Ollama verifier (Session 2) over the tailnet.
    Returns the parked fuzzy matches plus the model/worker config to use."""
    from backend.scanner import (
        get_verification_queue, get_verification_meta,
        REMOTE_VERIFY_MODEL, REMOTE_VERIFY_WORKERS, REMOTE_VERIFY_MIN_CONF,
    )
    q = get_verification_queue()
    pairs = [{
        "pairId": m.get("pairId"),
        "a": (m.get("marketA", {}) or {}).get("title", ""),
        "b": (m.get("marketB", {}) or {}).get("title", ""),
        "inverted": bool(m.get("inverted", False)),
    } for m in q]
    return {
        "count": len(pairs),
        "model": REMOTE_VERIFY_MODEL,
        "workers": REMOTE_VERIFY_WORKERS,
        "min_confidence": REMOTE_VERIFY_MIN_CONF,
        "meta": get_verification_meta(),
        "pairs": pairs,
    }


@app.post("/api/verification-results")
async def verification_results(request: Request):
    """Verdicts POSTed back by the Kaggle Ollama verifier (Session 2).
    Body: {"verdicts": [{pairId, is_exact_match, confidence, reasoning, verifyModel}]}.
    Merges them onto the parked matches, keeps confirmed exact matches, and
    generates the HTML report."""
    from backend.scanner import apply_remote_verdicts
    try:
        body = await request.json()
    except Exception:
        body = {}
    verdicts = body.get("verdicts", []) if isinstance(body, dict) else (body or [])
    summary = await apply_remote_verdicts(verdicts)
    return {"status": "success", **summary}


@app.post("/api/verification-progress")
async def verification_progress(request: Request):
    """Live heartbeat from the Kaggle verifier (Session 2) so the OLLAMA VERIFY tab
    shows the one-by-one progress as it happens (verdicts themselves only arrive in
    one batch at the end, via /api/verification-results). Best-effort — the notebook
    wraps this in try/except so it can never break the GPU run.
    Body: {received, done, exact, model, workers}."""
    from backend.live_state import set_verification_progress
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        set_verification_progress(
            received=int(body.get("received", 0)),
            done=int(body.get("done", 0)),
            exact=int(body.get("exact", 0)),
            model=str(body.get("model", "qwen3.6:27b")),
            workers=int(body.get("workers", 3)),
        )
    except Exception as e:
        logger.warning(f"verification-progress update failed: {e}")
    return {"status": "ok"}


@app.post("/api/kaggle-progress")
async def kaggle_progress(request: Request):
    """Beacon endpoint hit by the Cloud GPU notebook once per cell/stage.

    Body: {"stage": int, "status": "running|done|error", "message": str}
    Best-effort — the notebook wraps these calls in try/except so a failure
    here can never break a GPU run.
    """
    from backend.scanner import update_kaggle_stage
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        stage = int(body.get("stage", -1))
    except (TypeError, ValueError):
        stage = -1
    status = str(body.get("status", "running"))
    message = str(body.get("message", ""))
    state = update_kaggle_stage(stage, status, message)
    return {"status": "ok", "current_stage": state.get("current_stage")}


@app.get("/api/kaggle-status")
async def kaggle_status():
    """Live per-cell state of the Cloud GPU notebook for the /live Kaggle tab."""
    from backend.scanner import get_kaggle_state
    return get_kaggle_state()


@app.get("/api/llm-status")
async def llm_status():
    """Live state of the Ollama LLM verification stage for the /live OLLAMA tab.
    Shows the 2000 fuzzy matches split across instances, each model's countdown,
    active workers, and the running exact-match tally."""
    from backend.live_state import get_llm_state
    return get_llm_state()


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
    kalshi_count = sum(1 for m in markets if m["platform"] == "Kalshi")
    manifold_count = sum(1 for m in markets if m["platform"] == "Manifold")
    poly_count = sum(1 for m in markets if m["platform"] == "Polymarket")
    pi_count = sum(1 for m in markets if m["platform"] == "PredictIt")
    ibkr_count = sum(1 for m in markets if m["platform"].lower() == "ibkr")
    state = get_scan_state()
    return {
        "kalshi": kalshi_count,
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
