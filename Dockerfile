# Universal Arbitrage Container
# Backend + Ollama + Ngrok + Colab Executor + Chromium, all-in-one
# Pairs with the `ibga` service defined in docker-compose.yml
# ===========================================

FROM python:3.11-slim

WORKDIR /app

# System dependencies (one layer)
RUN apt-get update && apt-get install -y \
        curl \
        git \
        wget \
        ca-certificates \
        gnupg \
        unzip \
        xvfb \
        netcat-openbsd \
        procps \
        chromium \
        chromium-driver \
    && wget -q https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-amd64.tgz \
    && tar -xzf ngrok-v3-stable-linux-amd64.tgz -C /usr/local/bin \
    && rm ngrok-v3-stable-linux-amd64.tgz \
    && curl -fsSL https://ollama.com/install.sh | sh \
    && rm -rf /var/lib/apt/lists/*

# uv for fast Python installs
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Python deps
COPY requirements.txt .
RUN uv pip install --system -r requirements.txt \
    && pip install --no-cache-dir \
        google-api-python-client \
        google-auth-httplib2 \
        google-auth-oauthlib \
        websockets \
        flask \
        selenium \
        webdriver-manager \
        nest_asyncio

# Application
COPY backend/ backend/
COPY main.py .
COPY colab_executor.py .
COPY Cloud_GPU_Matcher_v3_Auto.ipynb .
COPY AGENTS.md .
COPY start.sh .

RUN mkdir -p /app/reports /app/data /app/logs /root/.ollama

# Ports: backend, ngrok dashboard, dashboard, ollama
EXPOSE 8000 4040 5000 11434

ENV PYTHONPATH=/app \
    LLM_PROVIDER=openrouter \
    IB_GATEWAY_URL=http://ibga:4001 \
    OLLAMA_HOST=0.0.0.0:11434 \
    DISPLAY=:99

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

COPY docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
