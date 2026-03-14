#!/usr/bin/env zsh
set -euo pipefail

. "$(cd "$(dirname "$0")" && pwd)/lib/test_framework.zsh"
. "$ROOT/tests/e2e_init_test.zsh"
. "$ROOT/tests/e2e_pipeline_test.zsh"
. "$ROOT/tests/e2e_validation_test.zsh"

test_e2e_init_and_discoverability
test_e2e_local_pipeline_and_parse
test_e2e_check_and_describe

echo "1..$TEST_COUNT"
