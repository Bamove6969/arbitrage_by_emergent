# Universal Arbitrage Container
# Single image: IBKR Gateway + FastAPI backend + Ollama + ngrok + Playwright Colab executor.
# Builds on heshiming/ibga so IB Gateway (Java + IBC + Xvfb) is already installed.
# ===========================================

FROM heshiming/ibga

USER root
WORKDIR /app

# Extra system tools (most of what we need is already in the base image)
RUN apt-get update && apt-get install -y \
        python3-pip \
        curl \
        wget \
        git \
        ca-certificates \
        netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# uv for fast Python installs
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Python deps
COPY requirements.txt .
RUN uv pip install --system --break-system-packages -r requirements.txt \
    && pip install --no-cache-dir --break-system-packages \
        google-api-python-client \
        google-auth-httplib2 \
        google-auth-oauthlib \
        websockets \
        flask \
        playwright \
        nest_asyncio \
    && playwright install --with-deps chromium

# ngrok (auto-detect architecture)
RUN ARCH="$(dpkg --print-architecture)"; \
    case "$ARCH" in \
        amd64)  NG="ngrok-v3-stable-linux-amd64.tgz" ;; \
        arm64)  NG="ngrok-v3-stable-linux-arm64.tgz" ;; \
        armhf)  NG="ngrok-v3-stable-linux-arm.tgz"   ;; \
        *) echo "Unsupported arch: $ARCH" && exit 1 ;; \
    esac \
    && wget -q "https://bin.equinox.io/c/bNyj1mQVY4c/${NG}" \
    && tar -xzf "${NG}" -C /usr/local/bin \
    && rm "${NG}"

# Ollama (installer auto-detects arch)
RUN curl -fsSL https://ollama.com/install.sh | sh

# Application
COPY backend/ backend/
COPY main.py .
COPY colab_executor.py .
# v4-stable is the primary notebook (bge-m3 + reranker, pure torch on T4).
# v3 stays in the image as a fallback.
COPY Cloud_GPU_Matcher_v4_Stable.ipynb .
COPY Cloud_GPU_Matcher_v3_Auto.ipynb .
COPY AGENTS.md .
COPY start.sh .

RUN mkdir -p /app/reports /app/data /app/logs /root/.ollama

# Ports: backend, ngrok inspector, colab executor / setup page, Ollama
EXPOSE 8000 4040 5000 11434

ENV PYTHONPATH=/app \
    LLM_PROVIDER=openrouter \
    IB_GATEWAY_URL=http://localhost:4001 \
    OLLAMA_URL=http://localhost:11434 \
    OLLAMA_HOST=0.0.0.0:11434 \
    OLLAMA_NUM_PARALLEL=2 \
    OLLAMA_MAX_LOADED_MODELS=2

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=5 \
    CMD curl -f http://localhost:8000/api/health || exit 1

COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
