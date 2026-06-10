#!/usr/bin/env zsh
set -euo pipefail

. "$(cd "$(dirname "$0")" && pwd)/lib/test_framework.zsh"
. "$ROOT/tests/probe_listing_test.zsh"
. "$ROOT/tests/probe_resolution_test.zsh"
. "$ROOT/tests/probe_generation_test.zsh"

test_probe_listing_and_selectors
test_probe_resolution_and_flags
test_probe_algorithm_property_inheritance_chain
test_probe_declared_algorithm_definitions_mark_builtin_aliases
test_probe_local_generation_parity
test_define_algorithm_cli_arg_placeholders
test_probe_slurm_generation_parity
test_probe_slurm_auto_packs_whole_node_partition
test_probe_slurm_auto_uses_scheduler_array_when_packing_cannot_share_node
test_probe_kahip_parhip_alias_generation

echo "1..$TEST_COUNT"
