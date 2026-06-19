#!/usr/bin/env python3
"""
Colab Auto-Executor Service
Uses Playwright (bundled Chromium) to auto-execute Colab notebooks.
Requires a saved auth state file — run local_colab_auth.py once to set it up.
"""

import os
import sys
import json
import time
import asyncio
import logging
import threading
from pathlib import Path
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

AUTH_STATE_PATH = Path(os.environ.get("COLAB_AUTH_STATE", "/app/data/colab_state.json"))

CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-setuid-sandbox",
    "--disable-extensions",
    "--mute-audio",
    "--no-first-run",
]

execution_queue = []
current_runtime = None


async def _execute_notebook(gist_id: str, owner: str) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"status": "error", "error": "playwright not installed"}

    if not AUTH_STATE_PATH.exists():
        colab_url = f"https://colab.research.google.com/gist/{owner}/{gist_id}/Cloud_GPU_Matcher_v4_Stable.ipynb"
        logger.warning("No Colab auth state at %s — cannot auto-execute. Manual URL: %s", AUTH_STATE_PATH, colab_url)
        return {
            "status": "no_auth",
            "message": "Auth state missing. Run local_colab_auth.py on your device then POST to /colab-setup/upload-state",
            "colab_url": colab_url,
        }

    colab_url = f"https://colab.research.google.com/gist/{owner}/{gist_id}/Cloud_GPU_Matcher_v4_Stable.ipynb"
    logger.info("Opening Colab: %s", colab_url)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=CHROMIUM_ARGS)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            storage_state=str(AUTH_STATE_PATH),
        )
        page = await context.new_page()

        try:
            await page.goto(colab_url, wait_until="domcontentloaded", timeout=60_000)
            logger.info("Notebook page loaded")

            # Wait for runtime to connect
            logger.info("Waiting for runtime to connect (up to 2 min)...")
            await page.wait_for_selector("colab-connect-button", timeout=120_000)
            await asyncio.sleep(5)

            # Trigger Runtime → Run All via keyboard shortcut (Ctrl+F9)
            await page.keyboard.press("Control+F9")
            logger.info("Triggered Run All (Ctrl+F9)")
            await asyncio.sleep(3)

            # Also try the menu as a backup
            try:
                run_menu = page.locator("text=Runtime")
                await run_menu.click(timeout=5_000)
                run_all = page.locator("text=Run all")
                await run_all.click(timeout=5_000)
                logger.info("Triggered Run All via menu")
            except Exception:
                logger.info("Menu trigger skipped (keyboard shortcut should have worked)")

            # Monitor up to MONITOR_MAX seconds. The browser MUST stay connected
            # the whole time — it is the only frontend keeping the free Colab
            # runtime alive (idle disconnect otherwise). A full run = scan-wait +
            # fetch ~48k + single-T4 match + push, so give it plenty of room.
            monitor_max = int(os.environ.get("COLAB_MONITOR_MAX", "7200"))  # 2h
            deadline = time.time() + monitor_max
            loops = 0
            while time.time() < deadline:
                try:
                    content = await page.content()
                    # completion markers the notebook actually prints (cell 6)
                    if ("Pipeline complete" in content
                            or "results returned through the ngrok tunnel" in content
                            or "HTTP fallback OK" in content
                            or "Results sent" in content):
                        logger.info("Pipeline completed successfully!")
                        await browser.close()
                        return {"status": "success", "gist_id": gist_id}
                    if ("Session crashed" in content
                            or "Runtime disconnected" in content
                            or "aborting WITHOUT pulling" in content):
                        logger.warning("Run ended/aborted (see notebook output)")
                        break
                    # keep-alive heartbeat so Colab doesn't idle-disconnect
                    loops += 1
                    if loops % 4 == 0:
                        try:
                            await page.mouse.move(5 + (loops % 20), 5 + (loops % 20))
                        except Exception:
                            pass
                except Exception as e:
                    logger.debug("Monitor: %s", e)
                await asyncio.sleep(15)

            await browser.close()
            return {"status": "timeout", "gist_id": gist_id, "message": "Notebook still running (monitoring timed out)"}

        except Exception as e:
            logger.error("Notebook execution failed: %s", e, exc_info=True)
            await browser.close()
            return {"status": "error", "gist_id": gist_id, "error": str(e)}


def execute_colab_notebook(gist_id: str, owner: str = "Bamove6969") -> dict:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_execute_notebook(gist_id, owner))
    finally:
        loop.close()


_SETUP_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Colab Auth Setup</title>
<style>
body{background:#0f172a;color:#e2e8f0;font-family:system-ui,sans-serif;padding:20px;max-width:600px;margin:0 auto}
h2{color:#7dd3fc;margin-bottom:8px}p{color:#94a3b8;font-size:14px;margin-bottom:16px}
textarea{width:100%;height:200px;background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:8px;padding:12px;font-size:12px;font-family:monospace;resize:vertical}
button{background:#2563eb;color:#fff;border:none;padding:12px 24px;border-radius:8px;font-size:16px;cursor:pointer;width:100%;margin-top:12px}
button:hover{background:#1d4ed8}.status{margin-top:16px;padding:12px;border-radius:8px;font-size:14px}
.ok{background:#052e16;color:#4ade80;border:1px solid #166534}
.err{background:#450a0a;color:#f87171;border:1px solid #991b1b}
</style></head><body>
<h2>Colab Auth Setup</h2>
<p>In Firefox on your phone:<br>
1. Install <b>Cookie-Editor</b> extension<br>
2. Go to <b>colab.research.google.com</b> (make sure you're logged in)<br>
3. Open Cookie-Editor → <b>Export → Export as JSON</b><br>
4. Paste the JSON below and tap Save</p>
<form id="f">
<textarea id="j" placeholder='Paste Cookie-Editor JSON here...'></textarea>
<button type="submit">Save Auth State</button>
</form>
<div id="s"></div>
<script>
document.getElementById('f').onsubmit=async e=>{
  e.preventDefault();
  const s=document.getElementById('s');
  s.className='status';s.textContent='Saving...';
  try{
    const r=await fetch('/colab-setup/upload-state',{method:'POST',headers:{'Content-Type':'application/json'},body:document.getElementById('j').value});
    const d=await r.json();
    if(r.ok){s.className='status ok';s.textContent='✓ Saved '+d.cookies+' cookies. Executor ready!';}
    else{s.className='status err';s.textContent='Error: '+(d.error||JSON.stringify(d));}
  }catch(err){s.className='status err';s.textContent='Error: '+err;}
};
</script></body></html>"""

@app.route("/colab-setup")
def colab_setup_page():
    from flask import Response
    return Response(_SETUP_HTML, mimetype="text/html")


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "auth_state": str(AUTH_STATE_PATH),
        "auth_ready": AUTH_STATE_PATH.exists(),
        "queue_size": len(execution_queue),
    })


@app.route("/execute", methods=["POST"])
def queue_execution():
    data = request.json or {}
    gist_id = data.get("gist_id")
    owner = data.get("owner", "Bamove6969")
    if not gist_id:
        return jsonify({"error": "gist_id required"}), 400
    execution_queue.append({"gist_id": gist_id, "owner": owner})
    logger.info("Queued gist %s (queue: %d)", gist_id, len(execution_queue))
    return jsonify({"status": "queued", "gist_id": gist_id, "queue_position": len(execution_queue)})


@app.route("/colab-setup/upload-state", methods=["POST"])
def upload_auth_state():
    """
    Accepts either:
    - multipart file: -F "state=@colab_state.json"
    - raw JSON body:  -H "Content-Type: application/json" -d @cookies.json
    Cookie-Editor exports a plain array; converts it to Playwright storage_state format.
    """
    raw = None
    if "state" in request.files:
        raw = request.files["state"].read()
    elif request.data:
        raw = request.data
    elif request.json is not None:
        raw = json.dumps(request.json).encode()

    if not raw:
        return jsonify({"error": "Send file as multipart (-F state=@file) or raw JSON body"}), 400

    try:
        data = json.loads(raw)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    # Convert Cookie-Editor array format → Playwright storage_state format
    if isinstance(data, list):
        playwright_state = {"cookies": [], "origins": []}
        for c in data:
            playwright_state["cookies"].append({
                "name": c.get("name", ""),
                "value": c.get("value", ""),
                "domain": c.get("domain", ""),
                "path": c.get("path", "/"),
                "expires": c.get("expirationDate", -1),
                "httpOnly": c.get("httpOnly", False),
                "secure": c.get("secure", False),
                "sameSite": c.get("sameSite", "Lax"),
            })
        data = playwright_state
        logger.info("Converted Cookie-Editor array (%d cookies) to Playwright format", len(playwright_state["cookies"]))

    AUTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_STATE_PATH.write_text(json.dumps(data))
    n = len(data.get("cookies", []))
    size_kb = AUTH_STATE_PATH.stat().st_size // 1024
    logger.info("Saved Colab auth state (%d cookies, %d KB) to %s", n, size_kb, AUTH_STATE_PATH)
    return jsonify({"status": "saved", "cookies": n, "size_kb": size_kb})


@app.route("/status")
def status():
    return jsonify({
        "queue_size": len(execution_queue),
        "current_runtime": current_runtime,
        "auth_ready": AUTH_STATE_PATH.exists(),
        "service": "colab-executor",
        "version": "2.0-playwright",
    })


def process_queue():
    global current_runtime
    while True:
        if execution_queue:
            task = execution_queue.pop(0)
            current_runtime = task["gist_id"]
            logger.info("Executing gist: %s", task["gist_id"])
            result = execute_colab_notebook(task["gist_id"], task["owner"])
            logger.info("Execution result: %s", result)
            current_runtime = None
        else:
            time.sleep(5)


if __name__ == "__main__":
    worker_thread = threading.Thread(target=process_queue, daemon=True)
    worker_thread.start()
    logger.info("Colab Executor v2 (Playwright) starting on port 5000...")
    app.run(host="0.0.0.0", port=5000)
