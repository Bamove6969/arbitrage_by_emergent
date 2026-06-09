#!/usr/bin/env python3
"""
Kaggle Auto-Executor
Uploads Cloud_GPU_Matcher_v4_Stable.ipynb to Kaggle with the live ngrok
WS URL injected, then starts execution on dual T4 GPUs and monitors status.
Replaces the old colab_executor.py Playwright-based flow.
"""

import os, sys, json, time, logging, threading, tempfile, subprocess, stat
from pathlib import Path
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

NOTEBOOK_PATH   = Path(os.environ.get("NOTEBOOK_PATH", "/app/Cloud_GPU_Matcher_v4_Stable.ipynb"))
KAGGLE_USERNAME = os.environ.get("KAGGLE_USERNAME", "bamove6969")
KAGGLE_KEY      = os.environ.get("KAGGLE_API_TOKEN") or os.environ.get("KAGGLE_KEY", "")
KERNEL_SLUG     = os.environ.get("KAGGLE_KERNEL_SLUG", "cloud-gpu-matcher-v4")

_queue: list  = []
_current_status: str | None = None
_kaggle_env: dict = {}


def _ensure_kaggle_cfg():
    """Write ~/.kaggle/kaggle.json from env vars (idempotent)."""
    global _kaggle_env
    kdir = Path.home() / ".kaggle"
    kdir.mkdir(exist_ok=True)
    kcfg = kdir / "kaggle.json"
    creds = {"username": KAGGLE_USERNAME, "key": KAGGLE_KEY}
    kcfg.write_text(json.dumps(creds))
    kcfg.chmod(0o600)
    _kaggle_env = {**os.environ, "KAGGLE_USERNAME": KAGGLE_USERNAME, "KAGGLE_KEY": KAGGLE_KEY}
    logger.info(f"Kaggle creds written to {kcfg}")


def _get_ngrok_ws_url() -> str:
    """Ask the ngrok inspector API for the active tunnel, return wss:// URL."""
    try:
        import httpx
        r = httpx.get("http://localhost:4040/api/tunnels", timeout=5)
        tunnels = r.json().get("tunnels", [])
        if tunnels:
            pub = tunnels[0].get("public_url", "")
            return pub.replace("https://", "wss://") + "/ws"
    except Exception as e:
        logger.warning(f"ngrok inspector error: {e}")
    domain = os.environ.get("NGROK_DOMAIN", "")
    if domain:
        return f"wss://{domain}/ws"
    return ""


def _rewrite_and_push(ws_url: str) -> str:
    """Rewrite WS_URL in notebook, write kernel-metadata, push to Kaggle.
    Returns the kernel id (username/slug)."""
    with open(NOTEBOOK_PATH) as f:
        nb = json.load(f)

    replaced = 0
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        new_src = []
        for line in cell["source"]:
            if "WS_URL_PLACEHOLDER" in line and "REPLACE_ME" in line:
                new_src.append(f'WS_URL_PLACEHOLDER = "{ws_url}"\n')
                replaced += 1
            else:
                new_src.append(line)
        cell["source"] = new_src

    logger.info(f"WS_URL → {ws_url} ({replaced} replacement(s))")

    kernel_id = f"{KAGGLE_USERNAME}/{KERNEL_SLUG}"

    with tempfile.TemporaryDirectory() as tmpdir:
        nb_out = Path(tmpdir) / NOTEBOOK_PATH.name
        nb_out.write_text(json.dumps(nb, indent=1))

        meta = {
            "id":                  kernel_id,
            "title":               "Cloud GPU Matcher v4 Stable",
            "code_file":           NOTEBOOK_PATH.name,
            "language":            "python",
            "kernel_type":         "notebook",
            "is_private":          False,
            "enable_gpu":          True,
            "enable_internet":     True,
            "dataset_sources":     [],
            "competition_sources": [],
            "kernel_sources":      [],
            "model_sources":       [],
        }
        (Path(tmpdir) / "kernel-metadata.json").write_text(json.dumps(meta))

        result = subprocess.run(
            ["kaggle", "kernels", "push", "-p", tmpdir],
            capture_output=True, text=True, env=_kaggle_env, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"kaggle push failed: {result.stderr.strip()}")
        logger.info(f"Kaggle push OK: {result.stdout.strip()}")

    return kernel_id


def _monitor(kernel_id: str, max_hours: float = 3.0):
    """Poll kaggle kernels status every 2 min until complete/error."""
    global _current_status
    deadline = time.time() + max_hours * 3600
    poll = 0
    while time.time() < deadline:
        time.sleep(120)
        poll += 1
        try:
            r = subprocess.run(
                ["kaggle", "kernels", "status", kernel_id],
                capture_output=True, text=True, env=_kaggle_env, timeout=30,
            )
            out = r.stdout.strip()
            logger.info(f"[poll {poll}] {out}")
            _current_status = out
            lo = out.lower()
            if "complete" in lo:
                logger.info("Kaggle run COMPLETE")
                return True
            if "error" in lo or "failed" in lo or "cancel" in lo:
                logger.error(f"Kaggle run FAILED: {out}")
                return False
        except Exception as e:
            logger.warning(f"Status poll error: {e}")
    logger.warning("Kaggle monitor timed out")
    return False


def _run_job(task: dict):
    global _current_status
    try:
        ws_url = task.get("ws_url") or _get_ngrok_ws_url()
        if not ws_url:
            logger.error("No ngrok URL — cannot inject WS_URL into notebook")
            _current_status = "error: no ngrok URL"
            return

        _current_status = "pushing"
        kernel_id = _rewrite_and_push(ws_url)

        _current_status = f"running ({kernel_id})"
        logger.info(f"Notebook pushed; monitoring {kernel_id}...")
        _monitor(kernel_id)
    except Exception as e:
        logger.error(f"Job error: {e}", exc_info=True)
        _current_status = f"error: {e}"
    finally:
        _current_status = None


def _worker():
    while True:
        if _queue:
            _run_job(_queue.pop(0))
        else:
            time.sleep(5)


# ── Flask routes ───────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({
        "status":        "ok",
        "queue_size":    len(_queue),
        "current":       _current_status,
        "kaggle_user":   KAGGLE_USERNAME,
        "kernel_slug":   KERNEL_SLUG,
    })


@app.route("/execute", methods=["POST"])
def enqueue():
    data = request.json or {}
    ws_url = data.get("ws_url") or _get_ngrok_ws_url()
    task = {"ws_url": ws_url}
    _queue.append(task)
    logger.info(f"Queued Kaggle job (queue={len(_queue)}, ws_url={ws_url})")
    return jsonify({"status": "queued", "queue_position": len(_queue), "ws_url": ws_url})


@app.route("/status")
def status():
    return jsonify({
        "queue_size":    len(_queue),
        "current":       _current_status,
        "service":       "kaggle-executor",
    })


if __name__ == "__main__":
    _ensure_kaggle_cfg()
    threading.Thread(target=_worker, daemon=True).start()
    logger.info(f"Kaggle Executor v1.0 — port 5000 | kernel: {KAGGLE_USERNAME}/{KERNEL_SLUG}")
    app.run(host="0.0.0.0", port=5000)
