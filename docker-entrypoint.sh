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
#    The API port opens once the push is approved. IBKR only honours
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

echo "[1/4] Starting IB Gateway (ibga manager)..."
echo "      2FA push will go to your phone; you have ${TWO_FA_WINDOW}s per attempt."
start_ibga
echo "    ibga manager booted (log: $LOG_DIR/ibga.log)"

# ---------------------------------------------------------------------------
# Supervision: set-and-forget means nothing stays dead. Each service runs in
# a restart loop; if it exits (crash, remote stop, OOM) it comes back in 5s.
# ---------------------------------------------------------------------------
supervise() {
    local name="$1"; shift
    while true; do
        "$@"
        echo "$(date) [supervisor] $name exited (code $?), restarting in 5s" >> "$LOG_DIR/supervisor.log"
        sleep 5
    done
}

# ---------------------------------------------------------------------------
# 2. ngrok tunnel (2-way; Colab pulls markets, pushes results back)
#    (Ollama runs remotely on Kaggle now -- nothing to start locally.)
# ---------------------------------------------------------------------------
echo "[2/4] Starting ngrok tunnel..."
if [ -n "${NGROK_AUTHTOKEN:-$NGROK_AUTH_TOKEN}" ]; then
    mkdir -p /root/.config/ngrok
    ngrok config add-authtoken "${NGROK_AUTHTOKEN:-$NGROK_AUTH_TOKEN}" \
        --config /root/.config/ngrok/ngrok.yml >/dev/null 2>&1 || true
    NG_ARGS=(http 8000 --config /root/.config/ngrok/ngrok.yml --log=stdout --log-level=info)
    if [ -n "${NGROK_DOMAIN}" ]; then
        NG_ARGS+=("--url=${NGROK_DOMAIN}")
    fi
    supervise ngrok ngrok "${NG_ARGS[@]}" >> "$LOG_DIR/ngrok.log" 2>&1 &
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
echo "[3/4] Starting Kaggle executor..."
mkdir -p /root/.kaggle
printf '{"username":"%s","key":"%s"}\n' \
    "${KAGGLE_USERNAME:-jessefleming}" \
    "${KAGGLE_API_TOKEN:-${KAGGLE_KEY:-}}" \
    > /root/.kaggle/kaggle.json
chmod 600 /root/.kaggle/kaggle.json

python3 /app/kaggle_executor.py > "$LOG_DIR/kaggle_executor.log" 2>&1 &
EXECUTOR_PID=$!
sleep 3

# ---------------------------------------------------------------------------
# 5. Wait for the 2FA gate at IBG_PORT_INTERNAL (the gateway's real API bind;
#    NEVER check 4000 -- socat holds that open from second one, which is how
#    the old gate false-passed). Two phases per attempt:
#      A. warmup: wait for ibga to actually raise the 2FA challenge (first
#         boot can spend minutes installing the gateway before a login form
#         even exists -- the push window must not count against that).
#      B. approval: once the challenge is real, give the user TWO_FA_WINDOW
#         seconds; on expiry restart ibga to send a fresh push.
# ---------------------------------------------------------------------------
IBG_PORT="${IBG_PORT_INTERNAL:-4001}"
IBGA_WAIT_TIMEOUT="${IBGA_WAIT_TIMEOUT:-600}"
echo "[4/4] Waiting on IBKR login + 2FA approval (localhost:${IBG_PORT})..."

challenge_count() {
    awk '/waiting for two-factor authentication/{n++} END{print n+0}' \
        "$LOG_DIR/ibga.log" 2>/dev/null || echo 0
}

ibga_up=0
for attempt in $(seq 1 "$MAX_2FA_RETRIES"); do
    chal_base=$(challenge_count)
    challenge_seen=0
    warmup_deadline=$(( $(date +%s) + IBGA_WAIT_TIMEOUT ))
    echo "    [attempt ${attempt}/${MAX_2FA_RETRIES}] waiting for gateway login to reach the 2FA challenge (up to ${IBGA_WAIT_TIMEOUT}s)..."
    while [ "$(date +%s)" -lt "$warmup_deadline" ]; do
        if nc -z localhost "${IBG_PORT}" 2>/dev/null; then
            ibga_up=1   # session resumed / TOTP auto-answered: no push needed
            break
        fi
        if [ "$(challenge_count)" -gt "$chal_base" ]; then
            challenge_seen=1
            break
        fi
        sleep 2
    done

    if [ "$challenge_seen" -eq 1 ]; then
        echo "    [push ${attempt}/${MAX_2FA_RETRIES}] 2FA challenge is LIVE -- approve the IBKey notification within ${TWO_FA_WINDOW}s"
        deadline=$(( $(date +%s) + TWO_FA_WINDOW ))
        while [ "$(date +%s)" -lt "$deadline" ]; do
            if nc -z localhost "${IBG_PORT}" 2>/dev/null; then
                ibga_up=1
                break
            fi
            sleep 2
        done
    fi
    if [ "$ibga_up" -eq 1 ]; then
        echo "    IBKR API armed: gateway is listening on :${IBG_PORT}"
        break
    fi

    if [ "$attempt" -lt "$MAX_2FA_RETRIES" ]; then
        echo "    no approval (challenge_seen=${challenge_seen}); restarting ibga to send a fresh push..."
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
# Kill supervisor loops AND their supervised children
trap 'kill $IBGA_PID ${NGROK_PID:-} $EXECUTOR_PID 2>/dev/null; pkill -x ngrok 2>/dev/null || true' EXIT

echo "=== Starting backend (Poly + PredictIt + IBKR x2 fan out in parallel) ==="
exec python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
