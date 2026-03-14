#!/usr/bin/env zsh
set -euo pipefail

. "$(cd "$(dirname "$0")" && pwd)/lib/test_framework.zsh"
. "$ROOT/tests/probe_listing_test.zsh"
. "$ROOT/tests/probe_resolution_test.zsh"
. "$ROOT/tests/probe_generation_test.zsh"

test_probe_listing_and_selectors
test_probe_resolution_and_flags
test_probe_local_generation_parity
test_probe_slurm_generation_parity

echo "1..$TEST_COUNT"
