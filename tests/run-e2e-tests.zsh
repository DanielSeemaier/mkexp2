#!/usr/bin/env zsh
set -euo pipefail

. "$(cd "$(dirname "$0")" && pwd)/lib/test_framework.zsh"
. "$ROOT/tests/e2e_init_test.zsh"
. "$ROOT/tests/e2e_pipeline_test.zsh"
. "$ROOT/tests/e2e_validation_test.zsh"
. "$ROOT/tests/parser_plugins_test.zsh"
. "$ROOT/tests/plot_compose_test.zsh"

test_e2e_init_and_discoverability
test_e2e_local_pipeline_and_parse
test_e2e_slurm_array_submit_filter
test_e2e_check_and_describe
test_parser_mt_kahypar_example
test_parser_kaminpar_example
test_plot_compose_uses_keyed_image_and_small_context
test_plot_threads_filter_builds_r_args
test_plot_catalog_json
test_managed_plot_args
test_plot_source_csv_staging
test_native_r_env_paths_are_passed
test_native_r_env_paths_allow_unset_user_lib
test_spack_plot_r_libs_are_cached
test_topology_validation_for_plot_threads

echo "1..$TEST_COUNT"
