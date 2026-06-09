#!/bin/bash
# Universal entrypoint: IB Gateway + Ollama + ngrok + Kaggle executor + backend
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
# 1. IB Gateway (ibga). Triggers the IBKey 2FA push to the user's phone.
#    The API port (4001) opens once the push is approved. IBKR only honours
#    one push at a time -- if the user misses it (~2 min window), the
#    login attempt expires and a fresh push has to be sent. We do that
#    here by restarting the ibga manager.
# ---------------------------------------------------------------------------
TWO_FA_WINDOW="${TWO_FA_WINDOW:-120}"      # seconds before we re-send the push
MAX_2FA_RETRIES="${MAX_2FA_RETRIES:-5}"    # how many pushes total before giving up

start_ibga() {
    /opt/ibga/manager.sh >> "$LOG_DIR/ibga.log" 2>&1 &
    IBGA_PID=$!
    echo "    ibga manager up (PID $IBGA_PID)"
    sleep 15  # let Xvfb + Java boot before we start watching the port
}

echo "[1/5] Starting IB Gateway (ibga manager)..."
echo "      2FA push will go to your phone; you have ${TWO_FA_WINDOW}s per attempt."
start_ibga
echo "    ibga manager booted (log: $LOG_DIR/ibga.log)"

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
# 4. Kaggle executor — pushes the v4 notebook to Kaggle and monitors it.
#    Writes ~/.kaggle/kaggle.json from env vars so the CLI works without a
#    pre-baked credential file inside the image.
# ---------------------------------------------------------------------------
echo "[4/5] Starting Kaggle executor..."
mkdir -p /root/.kaggle
printf '{"username":"%s","key":"%s"}\n' \
    "${KAGGLE_USERNAME:-bamove6969}" \
    "${KAGGLE_API_TOKEN:-${KAGGLE_KEY:-}}" \
    > /root/.kaggle/kaggle.json
chmod 600 /root/.kaggle/kaggle.json

python3 /app/kaggle_executor.py > "$LOG_DIR/kaggle_executor.log" 2>&1 &
EXECUTOR_PID=$!
sleep 3

# ---------------------------------------------------------------------------
# 5. Wait for the 2FA gate at localhost:4001. Re-send the push every
#    TWO_FA_WINDOW seconds (default 120s) up to MAX_2FA_RETRIES attempts.
# ---------------------------------------------------------------------------
echo "[5/5] Waiting on IBKR 2FA approval (localhost:4001)..."

ibga_up=0
for attempt in $(seq 1 "$MAX_2FA_RETRIES"); do
    echo "    [push ${attempt}/${MAX_2FA_RETRIES}] approve the IBKey notification on your phone within ${TWO_FA_WINDOW}s"
    deadline=$(( $(date +%s) + TWO_FA_WINDOW ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if nc -z localhost 4001 2>/dev/null; then
            ibga_up=1
            echo "    IBKR API armed (push #${attempt} approved)"
            break
        fi
        sleep 2
    done
    [ "$ibga_up" -eq 1 ] && break

    if [ "$attempt" -lt "$MAX_2FA_RETRIES" ]; then
        echo "    no approval in ${TWO_FA_WINDOW}s; restarting ibga to send a fresh push..."
        kill "$IBGA_PID" 2>/dev/null || true
        wait "$IBGA_PID" 2>/dev/null || true
        sleep 3
        start_ibga
    fi
done

if [ "$ibga_up" -ne 1 ]; then
    echo "    WARN: IBKR API still not up after ${MAX_2FA_RETRIES} pushes; backend will retry on each scan"
fi

# Cleanup on exit
trap 'kill $IBGA_PID $OLLAMA_PID ${NGROK_PID:-} $EXECUTOR_PID 2>/dev/null || true' EXIT

echo "=== Starting backend (Poly + PredictIt + IBKR x2 fan out in parallel) ==="
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
