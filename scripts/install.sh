#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${1:-https://github.com/NoelJudeNoel/open-free-router.git}"
INSTALL_DIR="${OPEN_FREE_ROUTER_HOME:-$HOME/.local/open-free-router}"
CONFIG_DIR="${OPEN_FREE_ROUTER_CONFIG_HOME:-$HOME/.config/open-free-router}"
PYTHON="${OPEN_FREE_ROUTER_PYTHON:-python3}"

echo "🆓 open-free-router installer"
echo "   Install dir: $INSTALL_DIR"
echo "   Config dir:  $CONFIG_DIR"
echo ""

if [ -d "$INSTALL_DIR/.git" ]; then
  echo "⏳ Updating existing installation..."
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo "⏳ Cloning repository..."
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

echo "⏳ Setting up Python environment..."
cd "$INSTALL_DIR"
"$PYTHON" -m venv .venv
source .venv/bin/activate
pip install -q -U pip setuptools wheel
pip install -q -e .

# Symlink to system path if writable
if [ -w /usr/local/bin ]; then
  ln -sf "$INSTALL_DIR/.venv/bin/open-free-router" /usr/local/bin/open-free-router
fi

mkdir -p "$CONFIG_DIR"

if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
  echo "⏳ Creating default config..."
  cat > "$CONFIG_DIR/config.yaml" <<EOF
registry: $CONFIG_DIR/registry.yaml

proxy:
  host: 127.0.0.1
  port: 8337

ui:
  host: 127.0.0.1
  port: 9057
EOF
fi

echo ""
echo "✔ Installation complete"
echo "  Install: $INSTALL_DIR"
echo "  Config:  $CONFIG_DIR/config.yaml"
echo ""
echo "Next steps:"
echo "  1. Edit $CONFIG_DIR/registry.yaml and add your API keys"
echo "  2. Or run:  open-free-router setup"
echo "  3. Start:   open-free-router serve"
echo ""

# Optional: install systemd service for auto-start
if [ "${1:-}" = "--with-systemd" ] || [ "${OPEN_FREE_ROUTER_SYSTEMD:-}" = "1" ]; then
  if command -v systemctl >/dev/null 2>&1; then
    echo "⏳ Installing systemd service..."
    sed "s|/opt/open-free-router|$INSTALL_DIR|g" "$INSTALL_DIR/contrib/systemd/open-free-router.service" \
      > /etc/systemd/system/open-free-router.service
    systemctl daemon-reload
    systemctl enable open-free-router.service
    echo "✔ systemd service installed and enabled"
    echo "  Start:  systemctl start open-free-router"
    echo "  Status: systemctl status open-free-router"
  else
    echo "⚠ systemctl not found, skipping systemd setup"
  fi
fi