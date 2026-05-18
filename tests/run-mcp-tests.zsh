#!/usr/bin/env zsh
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
python3 "$ROOT/tests/mcp_bridge_test.py"
