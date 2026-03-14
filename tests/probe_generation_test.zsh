#!/usr/bin/env zsh

test_probe_local_generation_parity() {
  local tmp=""
  tmp=$(mktemp -d)
  mkdir -p "$tmp/graphs"
  : > "$tmp/graphs/demo.metis"
  cat > "$tmp/Experiment" <<'EOF'
System local
Property local.call_wrapper none
DefineAlgorithm MockArg Mock --alpha 1

ExperimentLocalParity() {
  Algorithms MockArg
  Graph graphs/demo
  Ks 2 4
  Seeds 3
  Epsilons 0.03 0.1
  Threads 1x1x2
  Property timelimit.per_instance 00:00:07
}
EOF

  (
    cd "$tmp"
    "$MKEXP2" probe LocalParity --calls > probe-calls.json
    jq -r '.calls[] | "\(.final_command) >> \"\(.log_file)\" 2>&1"' probe-calls.json > expected.cmds

    "$MKEXP2" generate >/dev/null
    assert_file_eq jobs/ExperimentLocalParity__1x1x2.cmds expected.cmds "probe call expansion matches generated local command file"
    assert_eq "$(json_value probe-calls.json '.calls | length')" "4" "probe reports all local calls"
    assert_eq "$(json_value probe-calls.json '.calls[0].final_command | startswith("timeout -v 7s ")')" "true" "per-instance timeout is reflected in probe output"
  )

  pass "local generation parity"
}

test_probe_slurm_generation_parity() {
  local tmp=""
  tmp=$(mktemp -d)
  mkdir -p "$tmp/graphs"
  : > "$tmp/graphs/demo.metis"
  cat > "$tmp/Experiment" <<'EOF'
System slurm
Property slurm.partition cpu
Property slurm.use_array true
Property slurm.array.max_parallel 5
Property slurm.install.mode job
Property slurm.install.timelimit 02:00:00
Property parse.auto true
Property parse.slurm.timelimit 00:30:00
Property timelimit 01:02:03

ExperimentAlpha() {
  Algorithms Mock
  Graph graphs/demo
  Ks 2
  Seeds 1
  Epsilons 0.03
  Threads 2x1x3
}

ExperimentBeta() {
  Algorithms Mock
  Graph graphs/demo
  Ks 2 4
  Seeds 1
  Epsilons 0.03
  Threads 2x1x3
  Property slurm.dependency afterok:ExperimentAlpha
}
EOF

  (
    cd "$tmp"
    "$MKEXP2" probe Beta --jobs > probe-jobs.json
    "$MKEXP2" probe Beta --calls > probe-calls.json
    jq -r '.calls[] | "\(.final_command) >> \"\(.log_file)\" 2>&1"' probe-calls.json > expected.cmds

    "$MKEXP2" generate >/dev/null

    assert_eq "$(json_value probe-jobs.json '.jobs.summary.count')" "1" "probe reports slurm job count"
    assert_eq "$(json_value probe-jobs.json '.jobs.run_jobs[0].dependency_key')" 'ExperimentAlpha:2x1x3' "probe reports slurm dependency key"
    assert_eq "$(json_value probe-jobs.json '.jobs.run_jobs[0].array_enabled')" "true" "probe reports array usage"
    assert_eq "$(json_value probe-jobs.json '.jobs.run_jobs[0].array_max_parallel')" "5" "probe reports array max parallel"
    assert_eq "$(json_value probe-jobs.json '.jobs.install_job.mode')" 'job' "probe reports install job summary"
    assert_eq "$(json_value probe-jobs.json '.jobs.parse_job.launcher')" 'slurm' "probe reports slurm parse job summary"

    assert_file_eq jobs/ExperimentBeta__2x1x3.cmds expected.cmds "probe call expansion matches generated slurm command file"
    if ! grep -q '^#SBATCH --array=0-1%5$' jobs/ExperimentBeta__2x1x3.sh; then
      fail "generated slurm job script contains expected array setting"
    fi
    if ! grep -q '^submit_install_slurm ' submit.sh; then
      fail "submit script contains install job submission"
    fi
    if ! grep -q '^submit_parse_slurm ' submit.sh; then
      fail "submit script contains parse job submission"
    fi
  )

  pass "slurm generation parity"
}
