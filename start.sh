#!/bin/bash
# ============================================================
#  Waldorf Womens Care — ERA 835 System — Start
# ============================================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Ensure Homebrew tools are in PATH (Apple Silicon + Intel)
if [ -f "/opt/homebrew/bin/brew" ]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [ -f "/usr/local/bin/brew" ]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi
# Also add common Node locations directly in case brew shellenv isn't enough
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# Check setup was done
if ! command -v python3 &>/dev/null || [ ! -d "backend/venv" ]; then
  echo -e "${YELLOW}First-time setup needed. Running setup.sh...${NC}"
  bash setup.sh
fi

echo ""
echo "============================================"
echo "  Waldorf Womens Care — ERA 835 System"
echo "============================================"
echo ""

# ── Backend ─────────────────────────────────────────────────
echo -e "${YELLOW}Starting backend API...${NC}"
cd backend
source venv/bin/activate
mkdir -p uploads exports
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload --log-level warning &
BACKEND_PID=$!
cd ..

# Wait for backend to be ready
echo "Waiting for backend..."
for i in {1..20}; do
  if curl -s http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# ── Frontend ─────────────────────────────────────────────────
echo -e "${YELLOW}Starting frontend...${NC}"
cd frontend
npm run dev -- --host 0.0.0.0 &
FRONTEND_PID=$!
cd ..

sleep 2

echo ""
echo "============================================"
echo -e "${GREEN}  System is running!${NC}"
echo "============================================"
echo ""
echo -e "  ${GREEN}App:${NC}      http://localhost:3000"
echo -e "  ${GREEN}API docs:${NC} http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Open browser automatically
sleep 1
open http://localhost:3000 2>/dev/null || true

# Cleanup on exit
trap "echo ''; echo 'Stopping...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
