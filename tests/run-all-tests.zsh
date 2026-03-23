#!/usr/bin/env zsh
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)

zsh "$ROOT/tests/run-probe-tests.zsh"
zsh "$ROOT/tests/run-e2e-tests.zsh"
