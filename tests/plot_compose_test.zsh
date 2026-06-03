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

test_plot_catalog_json() {
  local tmp=""
  tmp=$(mktemp)

  R_HOME=/mkexp2/test/invalid-r-home "$MKEXP2" plot --list --json > "$tmp"

  assert_eq "$(json_value "$tmp" '.plots | length')" "7" "plot catalog lists managed plot types"
  assert_eq "$(json_value "$tmp" '.plots[] | select(.id == "speedup") | .min_sources')" "1" "speedup plot has minimum source count"
  assert_eq "$(json_value "$tmp" '.plots[] | select(.id == "speedup") | .max_sources')" "1" "speedup plot has maximum source count"
  assert_eq "$(json_value "$tmp" '.plots[] | select(.id == "imbalance") | .min_sources')" "1" "imbalance plot has minimum source count"
  assert_eq "$(json_value "$tmp" '.plots[] | select(.id == "imbalance") | .default_selected')" "false" "imbalance plot is opt-in by default"
  assert_eq "$(json_value "$tmp" '.plots[] | select(.id == "relative-cut-graph-grid") | .expensive')" "true" "graph-grid plot is marked expensive"

  pass "plot catalog JSON"
}

test_managed_plot_args() {
  MKEXP2_PLOT_THREADS="$(NormalizeTopology 2)"
  MKEXP2_PLOT_TYPES=(performance-profile running-time-box)

  _BuildManagedPlotRArgs /tmp/managed.pdf KaMinPar-FM KaMinPar-LP

  assert_eq "${(j: :)PLOT_R_ARGS}" "--plot performance-profile --plot running-time-box --threads 1x1x2 --output /tmp/managed.pdf KaMinPar-FM KaMinPar-LP" "managed plot passes plot ids and output to R"

  MKEXP2_PLOT_THREADS=""
  MKEXP2_PLOT_TYPES=()
  pass "managed plot R arguments"
}

test_plot_source_csv_staging() {
  local tmp=""
  tmp=$(mktemp -d)
  local csv="$tmp/source.csv"

  mkdir -p "$tmp/exp"
  print "Algorithm,Graph,K,Epsilon,Cores,Time,Cut,Imbalance,Failed,Timeout" > "$csv"
  print "A,g,2,0.03,1,1,10,0,0,0" >> "$csv"

  (
    cd "$tmp/exp" || exit 1
    MKEXP2_RUN_ID="test-run"
    _PreparePlotSources "$PWD" "Alias=$csv" "Algo"
    assert_path_exists "$PWD/.mkexp2/plot-inputs/test-run/alias-"*".csv" "external CSV is staged"
    assert_contains "${PLOT_SOURCE_ARGS_NATIVE[1]}" "Alias=$PWD/.mkexp2/plot-inputs/test-run/alias-" "native source uses staged CSV"
    assert_contains "${PLOT_SOURCE_ARGS_DOCKER[1]}" "Alias=/output/.mkexp2/plot-inputs/test-run/alias-" "docker source uses mounted staged CSV"
    assert_eq "${PLOT_SOURCE_ARGS_NATIVE[2]}" "Algo" "algorithm source remains unchanged"
  )

  pass "plot CSV source staging"
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
stopifnot(grepl(".r-libs-native", Sys.getenv("R_LIBS"), fixed = TRUE))
stopifnot(grepl(Sys.getenv("EXISTING_R_LIBS"), Sys.getenv("R_LIBS"), fixed = TRUE))
EOF

  export EXPECTED_RESULTS="$results_dir"
  export EXISTING_R_LIB="$tmp/existing-r-lib"
  export EXISTING_R_LIBS="$tmp/existing-r-libs"
  export R_LIBS_USER="$EXISTING_R_LIB"
  export R_LIBS="$EXISTING_R_LIBS"
  _RunNativeRscript "$plots_dir" "$results_dir" "$script"
  unset EXPECTED_RESULTS EXISTING_R_LIB EXISTING_R_LIBS R_LIBS_USER R_LIBS

  pass "native R receives mkexp2 plot paths and preserves existing R library paths"
}

test_native_r_env_paths_allow_unset_user_lib() {
  local tmp=""
  tmp=$(mktemp -d)
  local plots_dir="$tmp/plots"
  local results_dir="$tmp/results"
  local script="$tmp/check-env.R"

  mkdir -p "$plots_dir" "$results_dir"
  cat > "$script" <<'EOF'
stopifnot(grepl(".r-libs-native", Sys.getenv("R_LIBS_USER"), fixed = TRUE))
stopifnot(grepl(".r-libs-native", Sys.getenv("R_LIBS"), fixed = TRUE))
EOF

  unset R_LIBS_USER R_LIBS
  _RunNativeRscript "$plots_dir" "$results_dir" "$script"

  pass "native R accepts an unset R_LIBS_USER"
}

test_spack_plot_r_libs_are_cached() {
  local tmp=""
  tmp=$(mktemp -d)
  local bin_dir="$tmp/bin"
  local cache_dir="$tmp/cache"
  local count_file="$tmp/spack-count"
  local old_path="$PATH"

  mkdir -p "$bin_dir" "$cache_dir"
  cat > "$bin_dir/spack" <<'EOF'
#!/usr/bin/env zsh
print 1 >> "$SPACK_COUNT_FILE"
if [[ "$1" == "load" && "$2" == "--sh" ]]; then
  print "export R_LIBS='/spack/plot-r-libs'"
  exit 0
fi
exit 1
EOF
  chmod +x "$bin_dir/spack"

  export SPACK_COUNT_FILE="$count_file"
  export PATH="$bin_dir:$PATH"
  unset R_LIBS
  _PLOT_SPACK_PACKAGES_LOADED=0
  _MaybeLoadSpackPlotPackages "$cache_dir"
  assert_eq "$R_LIBS" "/spack/plot-r-libs" "spack plot libs are loaded"

  unset R_LIBS
  _PLOT_SPACK_PACKAGES_LOADED=0
  _MaybeLoadSpackPlotPackages "$cache_dir"
  assert_eq "$R_LIBS" "/spack/plot-r-libs" "cached spack plot libs are reused"
  assert_eq "$(wc -l < "$count_file" | tr -d ' ')" "1" "spack is called only once when cache is present"

  PATH="$old_path"
  unset SPACK_COUNT_FILE R_LIBS
  _PLOT_SPACK_PACKAGES_LOADED=0

  pass "spack plot R library paths are cached"
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
