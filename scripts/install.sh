#!/usr/bin/env bash
# install.sh — One-liner: git clone && install && config && start
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/NoelJudeNoel/open-free-router/main/scripts/install.sh | bash
# Or:
#   bash install.sh [--github-url https://github.com/NoelJudeNoel/open-free-router.git]

set -euo pipefail

REPO_URL="${1:-https://github.com/NoelJudeNoel/open-free-router.git}"
INSTALL_DIR="${OPEN_FREE_ROUTER_HOME:-$HOME/.local/open-free-router}"
CONFIG_DIR="${OPEN_FREE_ROUTER_CONFIG_HOME:-$HOME/.config/open-free-router}"
PYTHON="${OPEN_FREE_ROUTER_PYTHON:-python3}"

echo "🆓 open-free-router installer"
echo "   Install dir: $INSTALL_DIR"
echo "   Config dir:  $CONFIG_DIR"
echo ""

# 1. Clone or update
if [ -d "$INSTALL_DIR/.git" ]; then
  echo "⏳ Updating existing installation..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo "⏳ Cloning repository..."
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

# 2. Venv + install
echo "⏳ Setting up Python environment..."
cd "$INSTALL_DIR"
"$PYTHON" -m venv .venv
source .venv/bin/activate
pip install -q -U pip setuptools wheel
pip install -q -e .

# 3. Config dir
mkdir -p "$CONFIG_DIR"
REGISTRY="$CONFIG_DIR/registry.yaml"
CONFIG="$CONFIG_DIR/config.yaml"

# 4. Registry (seed if missing)
if [ ! -f "$REGISTRY" ]; then
  echo "⏳ Creating registry..."
  "$PYTHON" -m open_free_router.cli init --registry "$REGISTRY"
fi

# 5. Config (seed if missing)
if [ ! -f "$CONFIG" ]; then
  echo "⏳ Creating config..."
  cat > "$CONFIG" <<EOF
registry: $REGISTRY

proxy:
  host: 127.0.0.1
  openrouter_port: 8337
  zen_port: 8338

ui:
  host: 127.0.0.1
  port: 9527

agents:
  hermes: ~/.hermes/config.yaml
  pi: ~/.pi/agent/models.json
  omp: ~/.omp/agent/models.yml
  opencode: ~/.config/opencode/opencode.jsonc
EOF
fi

# 6. Add OpenRouter provider if no providers
if [ ! -s "$REGISTRY" ] || ! "$PYTHON" -c "import yaml; d=yaml.safe_load(open('$REGISTRY')); exit(0 if d and any(d.values()) else 1)" 2>/dev/null; then
  echo "⏳ Seeding OpenRouter provider..."
  echo "   (You'll need to add your API key in $CONFIG or run: open-free-router add ...)"
fi

# 7. Done
echo ""
echo "✔ Installation complete"
echo "  Install: $INSTALL_DIR"
echo "  Config:  $CONFIG"
echo "  Registry: $REGISTRY"
echo ""
echo "Next steps:"
echo "  1. Edit $CONFIG and add your API keys"
echo "  2. Run: source $INSTALL_DIR/.venv/bin/activate"
echo "  3. Start proxy: open-free-router proxy"
echo "  4. Start UI:    open-free-router ui"
echo "  5. Refresh:     open-free-router refresh"
echo ""
