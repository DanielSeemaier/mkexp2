#!/usr/bin/env zsh

write_test_harness_pipeline_experiment() {
  local dir="$1"

  mkdir -p "$dir/graphs"
  : > "$dir/graphs/alpha.metis"
  : > "$dir/graphs/beta.metis"

  cat > "$dir/Experiment" <<'EOF'
System local
Property local.call_wrapper none

DefineAlgorithm TestHarness-Custom TestHarness --custom-flag
AlgorithmProperty TestHarness-Custom mode custom
AlgorithmProperty TestHarness-Custom extra user
AlgorithmProperty TestHarness-Custom use_openmp_env true

ExperimentPipeline() {
  Algorithms TestHarness TestHarness-Dbg TestHarness-Custom
  Graphs graphs metis
  Ks 2
  Seeds 1
  Epsilons 0.03
  Threads 1x1x2
}
EOF
}

test_e2e_local_pipeline_and_parse() {
  local tmp=""
  tmp=$(mktemp -d)
  write_test_harness_pipeline_experiment "$tmp"

  (
    cd "$tmp"
    "$MKEXP2" all -j 1 > all.out
    assert_file_contains all.out "build cores: 1" "CLI build flag is reflected during install"
    assert_file_contains all.out "TestHarness-Dbg (already built in this run)" "derived alias reuses existing build"
    assert_file_contains all.out "TestHarness-Custom (already built in this run)" "user-defined alias reuses existing build"

    zsh ./submit.sh > submit.out

    assert_file_contains jobs/ExperimentPipeline__1x1x2.cmds "--mode baseline" "base algorithm command is generated"
    assert_file_contains jobs/ExperimentPipeline__1x1x2.cmds "--mode debug --dbg" "plugin-derived algorithm command is generated"
    assert_file_contains jobs/ExperimentPipeline__1x1x2.cmds "OMP_NUM_THREADS=2 OMP_PROC_BIND=spread OMP_PLACES=threads" "use_openmp_env affects generated command"
    assert_file_contains jobs/ExperimentPipeline__1x1x2.cmds "--mode custom --extra user --custom-flag" "user-defined alias properties affect generated command"

    assert_eq "$(find logs -name '*.log' | wc -l | tr -d ' ')" "6" "submit executes every algorithm/graph combination"
    assert_file_contains logs/TestHarness-Dbg/ExperimentPipeline/alpha___k2_seed1_eps0.03_P1x1x2.log "test-harness: --graph graphs/alpha" "plugin-derived algorithm log is produced"
    assert_file_contains logs/TestHarness-Dbg/ExperimentPipeline/alpha___k2_seed1_eps0.03_P1x1x2.log "--mode debug --dbg" "plugin-derived algorithm log reflects alias args"
    assert_file_contains logs/TestHarness-Custom/ExperimentPipeline/beta___k2_seed1_eps0.03_P1x1x2.log "--mode custom --extra user --custom-flag" "user-defined alias log reflects overridden properties"

    "$MKEXP2" parse > parse.out
    assert_path_exists results/TestHarness.csv "parse writes CSV for base algorithm"
    assert_path_exists results/TestHarness-Dbg.csv "parse writes CSV for plugin-derived alias"
    assert_path_exists results/TestHarness-Custom.csv "parse writes CSV for user-defined alias"
    assert_line_count results/TestHarness.csv "3" "base algorithm CSV has header plus one row per graph"
    assert_line_count results/TestHarness-Dbg.csv "3" "plugin-derived alias CSV has header plus one row per graph"
    assert_line_count results/TestHarness-Custom.csv "3" "user-defined alias CSV has header plus one row per graph"
  )

  pass "local all + submit + parse pipeline"
}
