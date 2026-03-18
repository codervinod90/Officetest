#!/bin/bash
# Run both Worker and App in separate terminals
# Usage: ./run.sh

cd "$(dirname "$0")"
source .venv/bin/activate 2>/dev/null || true

echo "Start in this order:"
echo ""
echo "Terminal 1 - Worker:"
echo "  uvicorn worker.main:app --host 0.0.0.0 --port 8000"
echo ""
echo "Terminal 2 - App:"
echo "  streamlit run app.py"
echo ""
echo "Or run worker in background:"
echo "  uvicorn worker.main:app --host 0.0.0.0 --port 8000 &"
echo "  streamlit run app.py"
