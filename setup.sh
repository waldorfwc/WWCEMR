#!/bin/bash
# ============================================================
#  Waldorf Womens Care — ERA 835 System — First-Time Setup
# ============================================================
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
err()  { echo -e "${RED}✗ $1${NC}"; exit 1; }
step() { echo -e "\n${YELLOW}── $1${NC}"; }

echo ""
echo "============================================"
echo "  Waldorf Womens Care — ERA 835 Setup"
echo "============================================"
echo ""

# ── 1. Homebrew ─────────────────────────────────────────────
step "Checking Homebrew..."
if ! command -v brew &>/dev/null; then
  warn "Homebrew not found. Installing now (this takes 2-3 minutes)..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

  # Add brew to PATH for Apple Silicon / Intel
  if [ -f "/opt/homebrew/bin/brew" ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
    echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
  elif [ -f "/usr/local/bin/brew" ]; then
    eval "$(/usr/local/bin/brew shellenv)"
    echo 'eval "$(/usr/local/bin/brew shellenv)"' >> ~/.zprofile
  fi
  ok "Homebrew installed"
else
  ok "Homebrew already installed"
fi

# Ensure brew is in PATH
if [ -f "/opt/homebrew/bin/brew" ]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi

# ── 2. Python ───────────────────────────────────────────────
step "Checking Python..."
if ! command -v python3 &>/dev/null; then
  warn "Python not found. Installing..."
  brew install python
  ok "Python installed"
else
  ok "Python already installed: $(python3 --version)"
fi

# ── 3. Node.js ──────────────────────────────────────────────
step "Checking Node.js..."
if ! command -v node &>/dev/null; then
  warn "Node.js not found. Installing..."
  brew install node
  ok "Node.js installed"
else
  ok "Node.js already installed: $(node --version)"
fi

# ── 4. Backend virtualenv & packages ────────────────────────
step "Setting up Python backend..."
cd backend

if [ ! -d "venv" ]; then
  python3 -m venv venv
  ok "Virtual environment created"
fi

source venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
ok "Python packages installed"
cd ..

# ── 5. Frontend packages ─────────────────────────────────────
step "Setting up frontend..."
cd frontend
if [ ! -d "node_modules" ]; then
  npm install --silent
  ok "Frontend packages installed"
else
  ok "Frontend packages already installed"
fi
cd ..

# ── 6. Create upload/export folders ─────────────────────────
mkdir -p uploads exports backend/uploads backend/exports
ok "Upload/export directories ready"

echo ""
echo "============================================"
echo -e "${GREEN}  Setup complete!${NC}"
echo "============================================"
echo ""
echo "Now run:  ./start.sh"
echo ""
