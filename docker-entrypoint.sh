#!/bin/bash
# Universal entrypoint: Ollama + ngrok + Colab executor + backend
# Chained so backend doesn't start fetching until IB Gateway has cleared 2FA.
set -e

LOG_DIR=/app/logs
mkdir -p "$LOG_DIR"

echo "=== Universal Arbitrage Container ==="

# Virtual display (Chromium / Selenium for Colab automation)
Xvfb :99 -screen 0 1920x1080x24 >/dev/null 2>&1 &
export DISPLAY=:99

# 1. Ollama (local LLM)
echo "[1/4] Starting Ollama (OLLAMA_HOST=${OLLAMA_HOST})..."
ollama serve > "$LOG_DIR/ollama.log" 2>&1 &
OLLAMA_PID=$!

# Wait for Ollama to answer
for i in $(seq 1 30); do
    if curl -sf "http://localhost:11434/api/tags" >/dev/null 2>&1; then
        echo "      Ollama ready."
        break
    fi
    sleep 1
done

# Pre-pull requested models (comma-separated in OLLAMA_MODELS)
if [ -n "${OLLAMA_MODELS}" ]; then
    IFS=',' read -ra MODELS <<< "${OLLAMA_MODELS}"
    for m in "${MODELS[@]}"; do
        m_trimmed="$(echo "$m" | xargs)"
        [ -z "$m_trimmed" ] && continue
        echo "      Pulling Ollama model: $m_trimmed"
        ollama pull "$m_trimmed" >> "$LOG_DIR/ollama.log" 2>&1 || \
            echo "      WARN: failed to pull $m_trimmed (continuing)"
    done
fi

# 2. ngrok (2-way tunnel for Colab <-> phone)
echo "[2/4] Starting ngrok tunnel..."
if [ -n "${NGROK_AUTH_TOKEN}" ]; then
    ngrok config add-authtoken "$NGROK_AUTH_TOKEN" >/dev/null 2>&1 || true
    NGROK_ARGS=(http 8000 --log=stdout --log-level=info)
    if [ -n "${NGROK_DOMAIN}" ]; then
        NGROK_ARGS+=(--domain="${NGROK_DOMAIN}")
    fi
    ngrok "${NGROK_ARGS[@]}" > "$LOG_DIR/ngrok.log" 2>&1 &
    NGROK_PID=$!
    sleep 4
    echo "      ngrok up (inspector on :4040)"
else
    echo "      WARN: NGROK_AUTH_TOKEN not set, skipping ngrok"
fi

# 3. Wait for IB Gateway — this is the 2FA gate.
#    User approves the IBKey push on their phone; the API port opens after.
IBGA_HOST="${IBGA_HOST:-ibga}"
IBGA_PORT="${IBGA_PORT:-4001}"
IBGA_WAIT_TIMEOUT="${IBGA_WAIT_TIMEOUT:-600}"
echo "[3/4] Waiting for IB Gateway at ${IBGA_HOST}:${IBGA_PORT} (approve 2FA on your phone)..."
deadline=$(( $(date +%s) + IBGA_WAIT_TIMEOUT ))
while ! nc -z "$IBGA_HOST" "$IBGA_PORT" 2>/dev/null; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "      WARN: IB Gateway not reachable after ${IBGA_WAIT_TIMEOUT}s — continuing without it."
        break
    fi
    sleep 3
done
if nc -z "$IBGA_HOST" "$IBGA_PORT" 2>/dev/null; then
    echo "      IB Gateway reachable — IBKR API armed."
fi

# 4. Colab executor (handles notebook upload + WebSocket bridge)
echo "[4/4] Starting Colab executor..."
python3 /app/colab_executor.py > "$LOG_DIR/colab_executor.log" 2>&1 &
EXECUTOR_PID=$!
sleep 2

# Cleanup background procs on exit
trap 'kill $OLLAMA_PID ${NGROK_PID:-} $EXECUTOR_PID 2>/dev/null || true' EXIT

# Foreground: FastAPI backend. Scanner / orchestrator inside the app will
# fan out Polymarket + PredictIt + IBKR (x2) in parallel once it boots.
echo "=== Starting backend (Poly + PredictIt + IBKRx2 will run in parallel) ==="
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
