#!/usr/bin/env bash
# new-adapter.sh — Create a new agent adapter template
# Usage: bash scripts/new-adapter.sh <agent-name>
# Example: bash scripts/new-adapter.sh myagent

set -euo pipefail

AGENT_NAME="${1:-}"
if [ -z "$AGENT_NAME" ]; then
  echo "Usage: $0 <agent-name>"
  echo "Example: $0 myagent"
  exit 1
fi

ADAPTER_DIR="src/open_free_router/adapters/${AGENT_NAME}"
mkdir -p "$ADAPTER_DIR"

cat > "${ADAPTER_DIR}/__init__.py" <<EOF
from open_free_router.adapters.${AGENT_NAME}.apply import apply
EOF

cat > "${ADAPTER_DIR}/apply.py" <<'EOF'
#!/usr/bin/env python3
"""AGENT_NAME adapter: sync registry to AGENT config."""
from __future__ import annotations

from pathlib import Path

from open_free_router.registry import Registry


def apply(reg: Registry, path: Path):
    """Merge registry providers into AGENT_NAME config."""
    # TODO: Implement AGENT_NAME config format
    # Read path, merge providers from reg, write back
    raise NotImplementedError(f"{AGENT_NAME} adapter not implemented")
EOF

# Register in sync.py adapter map
SYNC_FILE="src/open_free_router/sync.py"
if ! grep -q "\"${AGENT_NAME}\"" "$SYNC_FILE"; then
  sed -i "s/BUILTIN_ADAPTERS = {/BUILTIN_ADAPTERS = {\n    \"${AGENT_NAME}\": \"open_free_router.adapters.${AGENT_NAME}\",/" "$SYNC_FILE"
fi

echo "✔ Adapter created: ${ADAPTER_DIR}/"
echo "  - __init__.py"
echo "  - apply.py  (edit this)"
echo ""
echo "Next:"
echo "  1. Edit ${ADAPTER_DIR}/apply.py"
echo "  2. Add ${AGENT_NAME} path to config.yaml agents:"
echo "     agents:"
echo "       ${AGENT_NAME}: /path/to/agent/config"
