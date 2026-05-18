#!/bin/bash
set -e

echo "🚀 Starting Arbitrage System with Automated Colab Pipeline"
echo "============================================================"

# Configuration
export NGROK_AUTH_TOKEN="${NGROK_AUTH_TOKEN:-your_ngrok_token_here}"
export REPORTS_DIR="/app/reports"

# Create reports directory
mkdir -p $REPORTS_DIR

# Step 1: Start Docker Compose
echo ""
echo "🐳 Starting Docker containers..."
docker-compose up -d

# Wait for backend to be ready
echo "⏳ Waiting for backend to initialize..."
sleep 10

# Step 2: Check backend health
echo "🏥 Checking backend health..."
for i in {1..30}; do
    if curl -s http://localhost:8001/api/health > /dev/null 2>&1; then
        echo "✅ Backend is ready!"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "❌ Backend failed to start. Check logs: docker logs arbitrage-backend"
        exit 1
    fi
    echo "   Waiting... ($i/30)"
    sleep 2
done

# Step 3: Check Ollama status
echo ""
echo "🦙 Checking Ollama status..."
for i in {1..60}; do
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo "✅ Ollama is ready!"
        
        # List models
        echo ""
        echo "📦 Installed models:"
        curl -s http://localhost:11434/api/tags | python3 -m json.tool 2>/dev/null || echo "   (no models yet - they will be pulled on first use)"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "⚠️  Ollama not responding yet - will continue in background"
        break
    fi
    echo "   Waiting... ($i/60)"
    sleep 2
done

# Step 4: Start ngrok tunnel (if not using docker ngrok service)
echo ""
echo "🌐 Starting ngrok tunnel..."
if command -v ngrok &> /dev/null; then
    pkill -f "ngrok" 2>/dev/null || true
    npx ngrok http 8001 --domain=copyrightable-pseudocartilaginous-sade.ngrok-free.dev > ngrok.log 2>&1 &
    sleep 5
    echo "✅ ngrok tunnel started"
    echo "   Public URL: https://copyrightable-pseudocartilaginous-sade.ngrok-free.dev"
else
    echo "ℹ️  ngrok not installed - using docker ngrok service"
    echo "   Check status: docker logs arbitrage-universal"
fi

# Step 5: Display status
echo ""
echo "============================================================"
echo "✅ System Started Successfully!"
echo "============================================================"
echo ""
echo "📊 Dashboard: http://localhost:8001"
echo "🔗 API:       http://localhost:8001/api"
echo "🦙 Ollama:    http://localhost:11434"
echo "🌐 ngrok:     https://copyrightable-pseudocartilaginous-sade.ngrok-free.dev"
echo ""
echo "📁 Reports:   $REPORTS_DIR"
echo ""
echo "📝 Useful Commands:"
echo "   - View logs:         docker-compose logs -f"
echo "   - Backend logs:      docker logs -f arbitrage-universal"
echo "   - Ollama logs:       docker exec arbitrage-universal tail -f /app/logs/ollama.log"
echo "   - ngrok logs:        docker exec arbitrage-universal tail -f /app/logs/ngrok.log"
echo "   - Stop all:          docker-compose down"
echo ""
echo "🚀 To trigger the automated pipeline:"
echo "   curl -X POST http://localhost:8001/api/run-pipeline"
echo ""
echo "📊 View latest report:"
echo "   ls -lt $REPORTS_DIR/*.html | head -1"
echo ""
echo "============================================================"

# Keep script running to show logs
echo "📺 Tailing logs (Ctrl+C to stop viewing, containers keep running)..."
docker-compose logs -f
