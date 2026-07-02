#!/usr/bin/env bash
# Full local stack launcher (macOS / Linux, kitty): moto, mcp-fs, config-a2a, web-a2a.
# Same as test-local.bat, except each service opens in its own named kitty tab.
# Usage:  ./test-local.sh [test_config.yaml]   (run from the mcp-fs repo root, inside kitty)
set -euo pipefail

CONFIG="${1:-test_config.yaml}"
if [ ! -f "$CONFIG" ]; then
  echo "Test config not found: $CONFIG"
  echo "Copy test_config.example.yaml to test_config.yaml and fill it in."
  exit 1
fi
if ! kitty @ ls >/dev/null 2>&1; then
  echo "kitty remote control is not available."
  echo "Run this from a kitty terminal with 'allow_remote_control yes' in kitty.conf."
  exit 1
fi

echo "[1/6] Preparing keys, config, and launch scripts..."
uv run python scripts/test_local_prepare.py "$CONFIG"
# shellcheck source=/dev/null
source state/test-local.vars.sh

FS_DIR="$(pwd)"
ktab() { kitty @ launch --type=tab --tab-title="$1" --hold bash "$FS_DIR/state/$2" >/dev/null; }

if [ "${START_MOTO:-0}" = "1" ]; then
  echo "[2/6] Starting moto server..."
  ktab moto run-moto.sh
  sleep 4
else
  echo "[2/6] Using external S3 endpoint; not starting moto."
fi

echo "[3/6] Starting mcp-fs..."
ktab mcp-fs run-mcp-fs.sh
sleep 10

echo "[4/6] Provisioning project ${MCP_FS_MOUNT} (owner ${ADMIN_EMAIL})..."
MCP_FS_ADMIN="${ADMIN_EMAIL}" uv run python scripts/provision.py "${MCP_FS_MOUNT}" "${ADMIN_EMAIL}"

echo "[5/6] Starting config-a2a agent..."
ktab config-a2a run-config-a2a.sh
sleep 6

echo "[6/6] Starting web-a2a UI..."
ktab web-a2a run-web-a2a.sh

echo
echo "Services launching in named kitty tabs."
echo "When web-a2a is up, open http://localhost:8000"
echo "  login as ${ADMIN_EMAIL}"
echo "  add a remote agent by URL: http://127.0.0.1:9100/agents/files  (auth: none)"
echo "  then chat, for example: list my files"
echo
echo "Close the tabs to stop the services."
