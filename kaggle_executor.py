#!/usr/bin/env python3
"""
Universal GPU-Notebook Auto-Executor (Kaggle + Colab)

ONE source-of-truth notebook (Cloud_GPU_Matcher_v4_Stable.ipynb, the v5 stack).
The executor injects the live ngrok WS URL and dispatches it to whichever GPU
provider is selected:

  EXECUTOR_PROVIDER = kaggle  (default) — push to Kaggle dual-T4, monitor
                    = colab           — derive a Colab single-T4 variant from the
                                        SAME notebook, upload to a GitHub gist,
                                        drive it headless via Playwright
                    = auto            — try Kaggle; on quota/error fall back to Colab

Because the Colab notebook is GENERATED from the Kaggle notebook every run, the
two can never drift — Colab always has the identical models/plugins/fixes.
"""

import os, sys, json, time, logging, threading, tempfile, subprocess, stat, copy
from pathlib import Path
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)

NOTEBOOK_PATH   = Path(os.environ.get("NOTEBOOK_PATH", "/app/Cloud_GPU_Matcher_v4_Stable.ipynb"))
PROVIDER        = os.environ.get("EXECUTOR_PROVIDER", "kaggle").lower()  # kaggle | colab | auto
KAGGLE_USERNAME = os.environ.get("KAGGLE_USERNAME", "jessefleming")
KAGGLE_KEY      = os.environ.get("KAGGLE_API_TOKEN") or os.environ.get("KAGGLE_KEY", "")
KERNEL_SLUG     = os.environ.get("KAGGLE_KERNEL_SLUG", "cloud-gpu-matcher-v4-stable")
HF_TOKEN        = os.environ.get("HF_TOKEN", "")
GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
COLAB_OWNER     = os.environ.get("COLAB_GIST_OWNER", "")  # filled from gist response if blank

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
    # The CLI prefers env vars over kaggle.json, and a KAGGLE_* var it considers
    # malformed poisons auth for every call (401 on kernels the account owns).
    # kaggle.json alone is verified to authenticate, so strip the env-var path.
    _kaggle_env = {
        k: v for k, v in os.environ.items()
        if k not in ("KAGGLE_USERNAME", "KAGGLE_KEY", "KAGGLE_API_TOKEN")
    }
    _kaggle_env["HOME"] = str(Path.home())
    logger.info(f"Kaggle creds written to {kcfg}; CLI auth via kaggle.json only")


def _get_ngrok_ws_url() -> str:
    """Ask the ngrok inspector API for the backend tunnel, return wss:// URL.

    Only trusts the live inspector — no static-domain fallback. The reserved
    domain may be claimed by another service (e.g. the LLM server notebook),
    and pushing a notebook pointed at the wrong service burns a GPU session.
    """
    try:
        import httpx
        r = httpx.get("http://localhost:4040/api/tunnels", timeout=5)
        tunnels = r.json().get("tunnels", [])
        # pick the tunnel that forwards to the backend on :8000, not just [0]
        for t in tunnels:
            addr = t.get("config", {}).get("addr", "")
            if addr.endswith(":8000"):
                pub = t.get("public_url", "")
                if pub.startswith("https://"):
                    return pub.replace("https://", "wss://") + "/ws"
        if tunnels:
            logger.warning(f"No tunnel forwarding to :8000 among {len(tunnels)} tunnels")
    except Exception as e:
        logger.warning(f"ngrok inspector error: {e}")
    return ""


def _load_notebook() -> dict:
    with open(NOTEBOOK_PATH) as f:
        return json.load(f)


def _inject(nb: dict, ws_url: str) -> dict:
    """Inject the live WS URL (and HF token, on Kaggle) into a copy of the
    notebook. Shared by the Kaggle and Colab paths so both stay identical."""
    nb = copy.deepcopy(nb)
    ws_replaced = 0
    # The live-progress beacon posts to the same backend over HTTPS, derived
    # from the ws URL: wss://<ngrok>/ws -> https://<ngrok>/api/kaggle-progress
    beacon_url = (ws_url.replace("wss://", "https://").replace("ws://", "http://")
                  .rsplit("/ws", 1)[0] + "/api/kaggle-progress") if ws_url else ""
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        raw = cell["source"]
        lines = raw.splitlines(keepends=True) if isinstance(raw, str) else list(raw)
        new_src = []
        for line in lines:
            if "WS_URL_PLACEHOLDER" in line and "REPLACE_ME" in line:
                new_src.append(f'WS_URL_PLACEHOLDER = "{ws_url}"\n')
                ws_replaced += 1
            elif "BEACON_URL_PLACEHOLDER" in line and beacon_url:
                new_src.append(f'_BEACON_URL = "{beacon_url}"\n')
            elif "HF_TOKEN_PLACEHOLDER" in line and HF_TOKEN:
                new_src.append(line.replace("HF_TOKEN_PLACEHOLDER", HF_TOKEN))
            else:
                new_src.append(line)
        cell["source"] = new_src
    if ws_replaced == 0:
        raise RuntimeError("WS_URL_PLACEHOLDER line not found — refusing to push a notebook that can't reach the backend")
    return nb


def _adapt_for_colab(nb: dict) -> dict:
    """Derive the Colab single-T4 variant from the (already WS-injected) Kaggle
    notebook. Same models/plugins/fixes — only the host-specific glue differs:
      - Kaggle Secrets API  -> google.colab.userdata
      - drop the Kaggle 'Batch' guard (Colab has no batch runs)
      - /kaggle/working/    -> /content/
    The Colab notebook reads HF_TOKEN from Colab Secrets, so we also strip any
    injected HF token to avoid leaking it into a gist."""
    nb = copy.deepcopy(nb)
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        src = "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]
        src = src.replace(
            "from kaggle_secrets import UserSecretsClient\n    _HF_TOKEN = UserSecretsClient().get_secret('HF_TOKEN')\n    print('HF token from Kaggle Secrets')",
            "from google.colab import userdata\n    _HF_TOKEN = userdata.get('HF_TOKEN')\n    print('HF token from Colab Secrets')")
        # remove Kaggle batch guard block if present
        if 'KAGGLE_KERNEL_RUN_TYPE' in src:
            lines = src.split("\n")
            kept, skip = [], 0
            for ln in lines:
                if 'KAGGLE_KERNEL_RUN_TYPE' in ln or 'Batch (save-version)' in ln:
                    skip = 1
                    continue
                if skip and (ln.strip() == "" or ln.startswith("#")):
                    continue
                skip = 0
                kept.append(ln)
            src = "\n".join(kept)
        src = src.replace("/kaggle/working/", "/content/")
        # never ship a real HF token inside a gist — Colab Secrets supplies it
        if HF_TOKEN:
            src = src.replace(HF_TOKEN, "HF_TOKEN_PLACEHOLDER")
        cell["source"] = src
    # tag the title cell
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "markdown":
            s = "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]
            if "Cloud GPU ML Matcher" in s:
                cell["source"] = s.replace("Matcher (v5)", "Matcher (v5 — Colab single-T4)")
            break
    return nb


def _gist_upload(nb: dict, filename: str) -> tuple:
    """Create/update a secret GitHub gist with the notebook. Returns (gist_id, owner)."""
    import httpx
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN not set — required to host the Colab notebook as a gist")
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}",
               "Accept": "application/vnd.github+json"}
    payload = {"description": "Cloud GPU Matcher (Colab) — auto-generated",
               "public": False,
               "files": {filename: {"content": json.dumps(nb, indent=1)}}}
    with httpx.Client(timeout=30) as c:
        r = c.post("https://api.github.com/gists", headers=headers, json=payload)
        r.raise_for_status()
        d = r.json()
    return d["id"], d["owner"]["login"]


def _push_colab(ws_url: str) -> str:
    """Adapt the notebook for Colab, host it on a gist, drive it headless."""
    nb = _adapt_for_colab(_inject(_load_notebook(), ws_url))
    gist_id, owner = _gist_upload(nb, NOTEBOOK_PATH.name)
    logger.info(f"Colab notebook → gist {owner}/{gist_id} (ws={ws_url})")
    from colab_executor import execute_colab_notebook
    result = execute_colab_notebook(gist_id, owner=COLAB_OWNER or owner)
    logger.info(f"Colab run result: {result}")
    if result.get("status") in ("error", "no_auth"):
        raise RuntimeError(f"Colab execution failed: {result}")
    return f"colab:{owner}/{gist_id}"


def _rewrite_and_push(ws_url: str) -> str:
    """Inject WS_URL, write kernel-metadata, push to Kaggle. Returns kernel id."""
    nb = _inject(_load_notebook(), ws_url)
    logger.info(f"WS_URL → {ws_url}; HF_TOKEN injected: {bool(HF_TOKEN)}")

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
            "is_private":          True,
            "enable_gpu":          True,
            # Kaggle's only T4 shape IS the dual-T4 pool (no single-T4 option
            # exists). The old "accelerator": "GPU_T4_X2" key was silently
            # ignored by every CLI version and the server defaulted to ONE GPU,
            # tripping the notebook's dual-GPU gate. Requires kaggle CLI >= 2.x.
            "machine_shape":       "NvidiaTeslaT4",
            "enable_internet":     True,
            "dataset_sources":     [],
            "competition_sources": [],
            "kernel_sources":      [],
            "model_sources":       [],
        }
        (Path(tmpdir) / "kernel-metadata.json").write_text(json.dumps(meta))

        # --accelerator is documented to override the enable_gpu boolean, which
        # otherwise wins server-side and allocates a legacy single GPU even when
        # machine_shape is present in the metadata file.
        result = subprocess.run(
            ["kaggle", "kernels", "push", "-p", tmpdir, "--accelerator", "NvidiaTeslaT4"],
            capture_output=True, text=True, env=_kaggle_env, timeout=120,
        )
        out = (result.stdout or "") + (result.stderr or "")
        # kaggle CLI exits 0 even on quota/permission errors — check the text
        if result.returncode != 0 or "error" in out.lower():
            raise RuntimeError(f"kaggle push failed: {out.strip()[:300]}")
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


def _dispatch_kaggle(ws_url: str):
    _current_status = "pushing (kaggle)"
    kernel_id = _rewrite_and_push(ws_url)
    logger.info(f"Notebook pushed to Kaggle; monitoring {kernel_id}...")
    _monitor(kernel_id)


def _run_job(task: dict):
    global _current_status
    try:
        ws_url = task.get("ws_url") or _get_ngrok_ws_url()
        if not ws_url:
            logger.error("No ngrok URL — cannot inject WS_URL into notebook")
            _current_status = "error: no ngrok URL"
            return

        provider = (task.get("provider") or PROVIDER).lower()
        logger.info(f"Dispatching to provider: {provider}")

        if provider == "colab":
            _current_status = "pushing (colab)"
            _push_colab(ws_url)
        elif provider == "auto":
            # Try Kaggle first; fall back to Colab on quota/permission/errors.
            try:
                _dispatch_kaggle(ws_url)
            except Exception as e:
                logger.warning(f"Kaggle path failed ({e}); falling back to Colab")
                _current_status = "pushing (colab fallback)"
                _push_colab(ws_url)
        else:  # kaggle
            _dispatch_kaggle(ws_url)
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
    provider = (data.get("provider") or PROVIDER).lower()  # per-request override
    task = {"ws_url": ws_url, "provider": provider}
    _queue.append(task)
    logger.info(f"Queued {provider} job (queue={len(_queue)}, ws_url={ws_url})")
    return jsonify({"status": "queued", "queue_position": len(_queue),
                    "ws_url": ws_url, "provider": provider})


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
    logger.info(f"Universal Executor v2.0 — port 5000 | provider={PROVIDER} | "
                f"kaggle={KAGGLE_USERNAME}/{KERNEL_SLUG}")
    app.run(host="0.0.0.0", port=5000)
