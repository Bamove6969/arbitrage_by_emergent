#!/bin/bash
# Run GPU matching locally via papermill - no Colab auth needed

cd "$(dirname "$0")"

# Save current markets to JSON
curl -s http://localhost:8001/api/raw-markets > /tmp/markets.json
MARKET_COUNT=$(jq 'length' /tmp/markets.json)

echo "Found $MARKET_COUNT markets"
echo "Running GPU matching via Papermill..."

# Run the notebook (skip the WebSocket cell, use local markets)
papermill Cloud_GPU_Matcher_v3_Auto.ipynb /tmp/output.ipynb \
  -p ws_mode "local" \
  -p markets_file "/tmp/markets.json" \
  --timeout 3600 \
  --kernel-name python3

echo "Matching complete. Results in /tmp/output.ipynb"