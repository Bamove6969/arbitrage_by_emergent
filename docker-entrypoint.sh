#!/bin/bash
# Universal entrypoint: IB Gateway + Ollama + ngrok + Colab executor + backend
# All in one container, ordered so the 2FA gate at IBKR doesn't block the rest of
# the stack from being ready by the time the user approves the IBKey push.

set -e

LOG_DIR=/app/logs
mkdir -p "$LOG_DIR"

echo "=== Universal Arbitrage Container ==="
echo "=== $(date) ==="

# ---------------------------------------------------------------------------
# Patch ibga so the IB Gateway manager doesn't start VNC / noVNC. We run fully
# headless; keeping VNC would just consume ports and CPU.
# ---------------------------------------------------------------------------
if [ -f /opt/ibga/_run_xv.sh ] && grep -q "x11vnc" /opt/ibga/_run_xv.sh 2>/dev/null; then
    awk '
        /^function _run_vnc \{/   { found=1; print; print "  : # VNC disabled"; next }
        /^function _run_novnc \{/ { found=1; print; print "  : # noVNC disabled"; next }
        found && /^\}$/           { found=0; print; next }
        found                     { next }
                                  { print }
    ' /opt/ibga/_run_xv.sh > /tmp/_run_xv_patched.sh \
        && cp /tmp/_run_xv_patched.sh /opt/ibga/_run_xv.sh
    echo "    VNC / noVNC stubbed out"
fi

# ---------------------------------------------------------------------------
# 1. IB Gateway (ibga). It will show a 2FA push on the user's phone after the
#    Java client comes up. The API port (4001) opens once 2FA is approved.
# ---------------------------------------------------------------------------
echo "[1/5] Starting IB Gateway (ibga manager)..."
/opt/ibga/manager.sh > "$LOG_DIR/ibga.log" 2>&1 &
IBGA_PID=$!
sleep 15
echo "    ibga manager booted (PID $IBGA_PID, log: $LOG_DIR/ibga.log)"

# ---------------------------------------------------------------------------
# 2. Ollama
# ---------------------------------------------------------------------------
echo "[2/5] Starting Ollama (OLLAMA_HOST=${OLLAMA_HOST:-0.0.0.0:11434})..."
ollama serve > "$LOG_DIR/ollama.log" 2>&1 &
OLLAMA_PID=$!
for i in $(seq 1 30); do
    if curl -sf "http://localhost:11434/api/tags" >/dev/null 2>&1; then
        echo "    Ollama ready"; break
    fi
    sleep 1
done

if [ -n "${OLLAMA_MODELS}" ]; then
    IFS=',' read -ra MODELS <<< "${OLLAMA_MODELS}"
    for m in "${MODELS[@]}"; do
        m_trimmed="$(echo "$m" | xargs)"
        [ -z "$m_trimmed" ] && continue
        echo "    Pulling Ollama model: $m_trimmed"
        ollama pull "$m_trimmed" >> "$LOG_DIR/ollama.log" 2>&1 || \
            echo "    WARN: failed to pull $m_trimmed (continuing)"
    done
fi

# ---------------------------------------------------------------------------
# 3. ngrok tunnel (2-way; Colab pulls markets, pushes results back)
# ---------------------------------------------------------------------------
echo "[3/5] Starting ngrok tunnel..."
if [ -n "${NGROK_AUTHTOKEN:-$NGROK_AUTH_TOKEN}" ]; then
    mkdir -p /root/.config/ngrok
    ngrok config add-authtoken "${NGROK_AUTHTOKEN:-$NGROK_AUTH_TOKEN}" \
        --config /root/.config/ngrok/ngrok.yml >/dev/null 2>&1 || true
    NG_ARGS=(http 8000 --config /root/.config/ngrok/ngrok.yml --log=stdout --log-level=info)
    if [ -n "${NGROK_DOMAIN}" ]; then
        NG_ARGS+=("--url=${NGROK_DOMAIN}")
    fi
    ngrok "${NG_ARGS[@]}" > "$LOG_DIR/ngrok.log" 2>&1 &
    NGROK_PID=$!
    sleep 4
    echo "    ngrok up (inspector on :4040)"
else
    echo "    WARN: NGROK_AUTHTOKEN not set, skipping ngrok"
fi

# ---------------------------------------------------------------------------
# 4. Colab executor (Playwright). Uses a saved storage_state for Colab auth.
#    If state is missing, visit http://<your-host>:5000/colab-setup from the
#    Pixel to paste cookies once.
# ---------------------------------------------------------------------------
echo "[4/5] Starting Colab executor (Playwright)..."
python3 /app/colab_executor.py > "$LOG_DIR/colab_executor.log" 2>&1 &
EXECUTOR_PID=$!
sleep 3

# ---------------------------------------------------------------------------
# 5. Optional barrier: wait for ibga's 4001 to open (2FA-gated). Backend can
#    boot before this, but the scanner will block on TWS connect until 4001
#    is up, so giving the loop here makes the log easier to read.
# ---------------------------------------------------------------------------
IBGA_WAIT_TIMEOUT="${IBGA_WAIT_TIMEOUT:-600}"
echo "[5/5] Waiting up to ${IBGA_WAIT_TIMEOUT}s for IBKR API at localhost:4001 (approve 2FA on your phone)..."
deadline=$(( $(date +%s) + IBGA_WAIT_TIMEOUT ))
while ! nc -z localhost 4001 2>/dev/null; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "    WARN: IBKR API not up after ${IBGA_WAIT_TIMEOUT}s; backend will retry on each scan"
        break
    fi
    sleep 3
done
if nc -z localhost 4001 2>/dev/null; then
    echo "    IBKR API armed"
fi

# Cleanup on exit
trap 'kill $IBGA_PID $OLLAMA_PID ${NGROK_PID:-} $EXECUTOR_PID 2>/dev/null || true' EXIT

echo "=== Starting backend (Poly + PredictIt + IBKR x2 fan out in parallel) ==="
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
