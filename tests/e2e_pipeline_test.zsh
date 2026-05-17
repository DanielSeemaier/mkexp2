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
    assert_path_exists logs/install.md "install writes a single markdown log"
    assert_file_contains logs/install.md "# mkexp2 install log" "install markdown log has a title"
    assert_file_contains logs/install.md "## Machine info" "install markdown log records machine info"
    assert_file_contains logs/install.md "### \`uname -a\`" "install markdown log captures uname output"
    assert_file_contains logs/install.md "## \`zsh\`" "install markdown log headings use command names"
    assert_file_contains logs/install.md "$ zsh -c" "install markdown log records the full command in a code block"
    assert_file_not_contains logs/install.md "## 0001." "install markdown log omits numbered command prefixes"
    assert_file_not_contains logs/install.md $'\033[' "install markdown log strips ANSI color escapes"
    assert_file_contains logs/install.md "test-harness build: TestHarness" "install markdown log captures command output"

    assert_file_contains jobs/ExperimentPipeline__1x1x2.cmds "--mode baseline" "base algorithm command is generated"
    assert_file_contains jobs/ExperimentPipeline__1x1x2.cmds "--mode debug --dbg" "plugin-derived algorithm command is generated"
    assert_file_contains jobs/ExperimentPipeline__1x1x2.cmds "OMP_NUM_THREADS=2 OMP_PROC_BIND=spread OMP_PLACES=threads" "use_openmp_env affects generated command"
    assert_file_contains jobs/ExperimentPipeline__1x1x2.cmds "--mode custom --extra user --custom-flag" "user-defined alias properties affect generated command"
    assert_line_count jobs/ExperimentPipeline__1x1x2.cmds.meta.tsv "6" "metadata has one row per generated command"
    assert_file_contains jobs/ExperimentPipeline__1x1x2.cmds.meta.tsv $'0\tTestHarness\tTestHarness\tExperimentPipeline\t1x1x2' "metadata records base algorithm"
    assert_file_contains jobs/ExperimentPipeline__1x1x2.cmds.meta.tsv $'\tTestHarness-Custom\tTestHarness\tExperimentPipeline\t1x1x2' "metadata records custom algorithm"

    assert_cmd_fails "submit rejects unknown algorithm filters" zsh ./submit.sh MissingAlgorithm

    zsh ./submit.sh TestHarness-Dbg > submit-debug.out
    assert_eq "$(find logs -name '*.log' | wc -l | tr -d ' ')" "2" "filtered submit runs one algorithm"
    assert_path_exists logs/TestHarness-Dbg/ExperimentPipeline/alpha___k2_seed1_eps0.03_P1x1x2.log "filtered submit writes selected algorithm log"
    if [[ -n "$(find logs/TestHarness-Custom -name '*.log' -print 2>/dev/null)" ]]; then
      fail "filtered submit does not run unselected custom algorithm"
    fi

    find logs -name '*.log' -delete
    zsh ./submit.sh TestHarness TestHarness-Custom > submit-two.out
    assert_eq "$(find logs -name '*.log' | wc -l | tr -d ' ')" "4" "filtered submit runs multiple algorithms"
    assert_path_exists logs/TestHarness/ExperimentPipeline/alpha___k2_seed1_eps0.03_P1x1x2.log "multi-filter submit writes base algorithm log"
    assert_path_exists logs/TestHarness-Custom/ExperimentPipeline/beta___k2_seed1_eps0.03_P1x1x2.log "multi-filter submit writes custom algorithm log"
    if [[ -n "$(find logs/TestHarness-Dbg -name '*.log' -print 2>/dev/null)" ]]; then
      fail "multi-filter submit does not run unselected debug algorithm"
    fi

    find logs -name '*.log' -delete
    zsh ./submit.sh > submit.out

    assert_eq "$(find logs -name '*.log' | wc -l | tr -d ' ')" "6" "submit without filters executes every algorithm/graph combination"
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

test_e2e_slurm_array_submit_filter() {
  local tmp=""
  tmp=$(mktemp -d)
  mkdir -p "$tmp/graphs"
  : > "$tmp/graphs/demo.metis"
  cat > "$tmp/Experiment" <<'EOF'
System slurm
Property slurm.partition cpu
Property slurm.use_array true
Property slurm.array.max_parallel 7
DefineAlgorithm MockA Mock --a
DefineAlgorithm MockB Mock --b

ExperimentArrayFilter() {
  Algorithms MockA MockB
  Graph graphs/demo
  Ks 2 4
  Seeds 1
  Epsilons 0.03
  Threads 1x1x2
}
EOF

  (
    cd "$tmp"
    "$MKEXP2" generate >/dev/null
    mkdir -p fakebin
    cat > fakebin/sbatch <<'EOF'
#!/usr/bin/env zsh
print -r -- "$@" >> "$SBATCH_ARGS_FILE"
echo "Submitted batch job 123"
EOF
    chmod +x fakebin/sbatch
    PATH="$PWD/fakebin:$PATH" SBATCH_ARGS_FILE="$PWD/sbatch.args" zsh ./submit.sh MockB > submit.out
    assert_file_contains sbatch.args "--array=2,3%7" "slurm filtered submit overrides array indices"
    assert_file_contains sbatch.args "MKEXP2_META_FILE=$PWD/jobs/ExperimentArrayFilter__1x1x2.cmds.meta.tsv" "slurm filtered submit exports metadata path"
    assert_file_contains submit.out "Submitted batch job 123" "slurm filtered submit invokes sbatch"
  )

  pass "slurm array submit filter"
}
