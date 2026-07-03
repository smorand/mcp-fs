#!/usr/bin/env bash
# Full local stack launcher (macOS / Linux, kitty): moto, mcp-fs, config-a2a, web-a2a.
# Ports are sequential to avoid collisions: moto S3 5001, mcp-fs 5002,
# config-a2a 5003, web-a2a 5004 (5000 is left to macOS AirPlay).
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
# --copy-env carries this shell's full environment (PATH with uv, plus the LLM
# env vars referenced by the run scripts) into each tab.
ktab() { kitty @ launch --type=tab --tab-title="$1" --copy-env --hold bash "$FS_DIR/state/$2" >/dev/null; }

if [ "${START_MOTO:-0}" = "1" ]; then
  echo "[2/6] Starting moto server..."
  # moto is in-memory; reset mcp-fs's SQLite metadata and ACL so they stay
  # consistent with a freshly emptied blob store (no stale nodes, no
  # ERR_PROJECT_EXISTS on re-run).
  rm -rf state/volumes state/admin.db state/admin.db-wal state/admin.db-shm 2>/dev/null || true
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
echo "When web-a2a is up, open http://localhost:5004"
echo "  login as ${ADMIN_EMAIL}"
echo "  add a remote agent by URL: http://127.0.0.1:5003/agents/files  (auth: none)"
echo "  then chat, for example: list my files"
echo
echo "Close the tabs to stop the services."
