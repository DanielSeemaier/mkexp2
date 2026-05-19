#!/usr/bin/env zsh

. "$ROOT/bin/inc/util.sh"
. "$ROOT/bin/inc/plot.sh"

test_plot_compose_uses_keyed_image_and_small_context() {
  local tmp=""
  tmp=$(mktemp -d)

  local plots_dir="$tmp/plots"
  local results_dir="$tmp/results"
  local build_context="$tmp/.mkexp2/plots-image"
  local compose_file="$tmp/plots-compose.yml"
  local image=""

  mkdir -p "$plots_dir" "$results_dir"
  cat > "$plots_dir/Dockerfile" <<'EOF'
FROM scratch
WORKDIR /work
EOF

  image=$(_PlotsDockerImage "$plots_dir")
  _PreparePlotsBuildContext "$plots_dir" "$build_context"
  _WritePlotsComposeFile "$plots_dir" "$results_dir" "$tmp" "$image" "$build_context" "$compose_file"

  assert_file_contains "$compose_file" "image: $image" "plot compose pins a Dockerfile-keyed image tag"
  assert_file_contains "$compose_file" "context: $build_context" "plot compose uses generated build context"
  assert_file_not_contains "$compose_file" "context: $plots_dir" "plot compose does not use plots directory as build context"
  assert_file_contains "$compose_file" "$plots_dir:/work:ro" "plot compose still mounts plot scripts at runtime"
  assert_file_eq "$build_context/Dockerfile" "$plots_dir/Dockerfile" "plot build context contains Dockerfile"
  assert_file_contains "$build_context/.dockerignore" "*" "plot build context ignores all non-Dockerfile files"
  assert_file_contains "$build_context/.dockerignore" "!Dockerfile" "plot build context keeps Dockerfile"

  pass "plot compose image cache and build context"
}

test_plot_threads_filter_builds_r_args() {
  MKEXP2_PLOT_THREADS="$(NormalizeTopology 4)"

  _BuildPlotRArgs 0 0 1 /tmp/plots.pdf KaMinPar-FM KaMinPar-LP

  assert_eq "${(j: :)PLOT_R_ARGS}" "--running-time --threads 1x1x4 --output /tmp/plots.pdf KaMinPar-FM KaMinPar-LP" "plot passes normalized thread filter to R"

  MKEXP2_PLOT_THREADS=""
  pass "plot threads filter R arguments"
}

test_native_r_env_paths_are_passed() {
  local tmp=""
  tmp=$(mktemp -d)
  local plots_dir="$tmp/plots"
  local results_dir="$tmp/results"
  local script="$tmp/check-env.R"

  mkdir -p "$plots_dir" "$results_dir"
  cat > "$script" <<'EOF'
stopifnot(Sys.getenv("MKEXP2_PLOTS_DATA_DIR") == Sys.getenv("EXPECTED_RESULTS"))
stopifnot(Sys.getenv("MKEXP2_PLOTS_DATA_OUTPUT_DIR") == Sys.getenv("EXPECTED_RESULTS"))
stopifnot(nzchar(Sys.getenv("MKEXP2_PLOTS_CACHE_DIR")))
stopifnot(grepl(".r-libs-native", Sys.getenv("R_LIBS_USER"), fixed = TRUE))
stopifnot(grepl(Sys.getenv("EXISTING_R_LIB"), Sys.getenv("R_LIBS_USER"), fixed = TRUE))
EOF

  export EXPECTED_RESULTS="$results_dir"
  export EXISTING_R_LIB="$tmp/existing-r-lib"
  export R_LIBS_USER="$EXISTING_R_LIB"
  _RunNativeRscript "$plots_dir" "$results_dir" "$script"
  unset EXPECTED_RESULTS EXISTING_R_LIB R_LIBS_USER

  pass "native R receives mkexp2 plot paths and preserves existing R library paths"
}

test_topology_validation_for_plot_threads() {
  assert_eq "$(NormalizeTopology 4)" "1x1x4" "bare thread count normalizes to local topology"
  assert_eq "$(NormalizeTopology 2x3x4)" "2x3x4" "full topology is preserved"

  IsValidTopology 1x1x4 || fail "valid topology should pass"
  if IsValidTopology 1x0x4; then
    fail "zero topology should fail"
  fi
  if IsValidTopology 1x4; then
    fail "partial topology should fail"
  fi

  pass "plot threads topology validation"
}
