#!/usr/bin/env zsh

test_probe_local_generation_parity() {
  local tmp=""
  tmp=$(mktemp -d)
  mkdir -p "$tmp/graphs"
  : > "$tmp/graphs/demo.metis"
  cat > "$tmp/Experiment" <<'EOF'
System local
Property local.call_wrapper none
Property spack.environment x86
DefineAlgorithm HarnessArg TestHarness --alpha 1

ExperimentLocalParity() {
  Algorithms HarnessArg
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
    jq -r '.calls | to_entries[] | "\(.key)\t\(.value.algorithm)\t\(.value.base)\tExperimentLocalParity\t\(.value.topology)\t\(.value.log_file)"' probe-calls.json > expected.cmds.meta.tsv

    "$MKEXP2" generate >/dev/null
    assert_file_eq jobs/ExperimentLocalParity__1x1x2.cmds expected.cmds "probe call expansion matches generated local command file"
    assert_file_eq jobs/ExperimentLocalParity__1x1x2.cmds.meta.tsv expected.cmds.meta.tsv "generated local command metadata matches probe calls"
    assert_line_count jobs/ExperimentLocalParity__1x1x2.cmds.meta.tsv "4" "local metadata has one row per command"
    assert_eq "$(json_value probe-calls.json '.calls | length')" "4" "probe reports all local calls"
    assert_eq "$(json_value probe-calls.json '.calls[0].final_command | startswith("timeout -v 7s ")')" "true" "per-instance timeout is reflected in probe output"
    assert_file_contains jobs/ExperimentLocalParity__1x1x2.sh "spack env activate --sh x86" "local job activates configured Spack environment"
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
Property spack.environment x86
Property slurm.use_array true
Property slurm.array.max_parallel 5
Property slurm.install.mode job
Property slurm.install.timelimit 02:00:00
Property parse.auto true
Property parse.slurm.timelimit 00:30:00
Property postprocess.auto true
Property postprocess.email.to results@example.org
Property postprocess.plots running-time-box
Property timelimit 01:02:03

ExperimentAlpha() {
  Algorithms TestHarness
  Graph graphs/demo
  Ks 2
  Seeds 1
  Epsilons 0.03
  Threads 2x1x3
}

ExperimentBeta() {
  Algorithms TestHarness
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
    jq -r '.calls | to_entries[] | "\(.key)\t\(.value.algorithm)\t\(.value.base)\tExperimentBeta\t\(.value.topology)\t\(.value.log_file)"' probe-calls.json > expected.cmds.meta.tsv

    "$MKEXP2" generate >/dev/null

    assert_eq "$(json_value probe-jobs.json '.jobs.summary.count')" "1" "probe reports slurm job count"
    assert_eq "$(json_value probe-jobs.json '.jobs.run_jobs[0].dependency_key')" 'ExperimentAlpha:2x1x3' "probe reports slurm dependency key"
    assert_eq "$(json_value probe-jobs.json '.jobs.run_jobs[0].array_enabled')" "true" "probe reports array usage"
    assert_eq "$(json_value probe-jobs.json '.jobs.run_jobs[0].array_max_parallel')" "5" "probe reports array max parallel"
    assert_eq "$(json_value probe-jobs.json '.jobs.install_job.mode')" 'job' "probe reports install job summary"
    assert_eq "$(json_value probe-jobs.json '.jobs.parse_job.launcher')" 'slurm' "probe reports slurm parse job summary"
    assert_eq "$(json_value probe-jobs.json '.jobs.run_jobs[0].job_script | endswith("/jobs/ExperimentBeta__2x1x3.sh")')" "true" "probe reports slurm job script directory"

    assert_file_eq jobs/ExperimentBeta__2x1x3.cmds expected.cmds "probe call expansion matches generated slurm command file"
    assert_file_eq jobs/ExperimentBeta__2x1x3.cmds.meta.tsv expected.cmds.meta.tsv "generated slurm command metadata matches probe calls"
    assert_line_count jobs/ExperimentBeta__2x1x3.cmds.meta.tsv "2" "slurm metadata has one row per command"
    if ! grep -q '^#SBATCH --array=0-1%5$' jobs/ExperimentBeta__2x1x3.sh; then
      fail "generated slurm job script contains expected array setting"
    fi
    if ! grep -q '^#SBATCH --output=slurm/slurm-%A_%a.out$' jobs/ExperimentBeta__2x1x3.sh; then
      fail "generated slurm job script writes array output under slurm directory"
    fi
    if ! grep -q '^#SBATCH --error=slurm/slurm-%A_%a.out$' jobs/ExperimentBeta__2x1x3.sh; then
      fail "generated slurm job script writes array errors under slurm directory"
    fi
    if ! grep -q '^#SBATCH --output=slurm/slurm-%j.out$' jobs/ExperimentAlpha__2x1x3.sh; then
      fail "generated slurm run job writes non-array output under slurm directory"
    fi
    if ! grep -q '^#SBATCH --error=slurm/slurm-%j.out$' jobs/ExperimentAlpha__2x1x3.sh; then
      fail "generated slurm run job writes non-array errors under slurm directory"
    fi
    if ! grep -q '^#SBATCH --output=slurm/slurm-%j.out$' jobs/install__*.sh; then
      fail "generated slurm install job writes output under slurm directory"
    fi
    if ! grep -q '^#SBATCH --error=slurm/slurm-%j.out$' jobs/install__*.sh; then
      fail "generated slurm install job writes errors under slurm directory"
    fi
    if ! grep -q 'MKEXP2_SLURM_INSTALL_LOGIN_ENV=1' jobs/install__*.sh; then
      fail "generated slurm install job reloads login shell environment"
    fi
    assert_file_contains jobs/ExperimentAlpha__2x1x3.sh "spack env activate --sh x86" "non-array Slurm job activates configured Spack environment"
    assert_file_contains jobs/ExperimentBeta__2x1x3.sh "spack env activate --sh x86" "array Slurm job activates configured Spack environment"
    assert_file_contains jobs/install__*.sh "spack env activate --sh x86" "Slurm install job activates configured Spack environment"
    assert_file_contains jobs/parse__*.sh "spack env activate --sh x86" "Slurm parse job activates configured Spack environment"
    if ! grep -q '^#SBATCH --output=slurm/slurm-%j.out$' jobs/parse__*.sh; then
      fail "generated slurm parse job writes output under slurm directory"
    fi
    if ! grep -q '^#SBATCH --error=slurm/slurm-%j.out$' jobs/parse__*.sh; then
      fail "generated slurm parse job writes errors under slurm directory"
    fi
    local -a slurm_scripts=(slurm/*.sh(N))
    if (( ${#slurm_scripts[@]} > 0 )); then
      fail "slurm directory does not contain generated shell scripts"
    fi
    if ! grep -q '^submit_install_slurm ' submit.sh; then
      fail "submit script contains install job submission"
    fi
    if ! grep -q '^submit_parse_slurm ' submit.sh; then
      fail "submit script contains parse job submission"
    fi
    if ! grep -q '^MKEXP2_POSTPROCESS_AUTO_REQUIRED=1$' submit.sh; then
      fail "submit script records postprocess requirement"
    fi
    if ! grep -q 'mkexp2_postprocess.py' submit.sh; then
      fail "submit script cleanup path can run postprocess helper"
    fi
  )

  pass "slurm generation parity"
}

test_probe_slurm_auto_packs_whole_node_partition() {
  local tmp=""
  tmp=$(mktemp -d)
  mkdir -p "$tmp/graphs" "$tmp/fakebin"
  : > "$tmp/graphs/demo.metis"
  cat > "$tmp/fakebin/scontrol" <<'EOF'
#!/usr/bin/env zsh
cat <<'PARTITION'
PartitionName=liskov
   Nodes=liskov OverSubscribe=NO
   State=UP TotalCPUs=256 TotalNodes=1 SelectTypeParameters=NONE
PARTITION
EOF
  chmod +x "$tmp/fakebin/scontrol"

  cat > "$tmp/Experiment" <<'EOF'
System slurm
Property slurm.partition liskov
Property slurm.use_array true
Property slurm.array.max_parallel 4

ExperimentPackedArray() {
  Algorithms TestHarness
  Graph graphs/demo
  Ks 2 4 8
  Seeds 1
  Epsilons 0.03
  Threads 1x1x1
}
EOF

  (
    cd "$tmp"
    PATH="$PWD/fakebin:$PATH" "$MKEXP2" probe PackedArray --jobs > probe-jobs.json
    PATH="$PWD/fakebin:$PATH" "$MKEXP2" generate >/dev/null

    assert_eq "$(json_value probe-jobs.json '.jobs.run_jobs[0].array_enabled')" "true" "probe reports packed array usage"
    assert_eq "$(json_value probe-jobs.json '.jobs.run_jobs[0].array_mode')" "packed" "probe reports packed array mode"
    assert_eq "$(json_value probe-jobs.json '.jobs.run_jobs[0].array_effective_parallel')" "3" "probe reports packed array fanout"
    assert_file_not_contains jobs/ExperimentPackedArray__1x1x1.sh "#SBATCH --array=" "packed array mode does not emit scheduler array"
    assert_file_contains jobs/ExperimentPackedArray__1x1x1.sh "# mkexp2 array mode: packed (3 concurrent command(s) in one Slurm allocation)" "packed array script documents local fanout"
    assert_file_contains jobs/ExperimentPackedArray__1x1x1.sh "#SBATCH --nodes=1" "packed array requests one liskov node"
    assert_file_contains jobs/ExperimentPackedArray__1x1x1.sh "#SBATCH --ntasks=3" "packed array scales task count to command fanout"
    assert_file_contains jobs/ExperimentPackedArray__1x1x1.sh "#SBATCH --ntasks-per-node=3" "packed array places fanout tasks on the node"
    assert_file_contains jobs/ExperimentPackedArray__1x1x1.sh "mkexp2_init_semaphore 3" "packed array limits concurrent commands"
  )

  pass "slurm auto packed array on whole-node partition"
}

test_probe_slurm_auto_uses_scheduler_array_when_packing_cannot_share_node() {
  local tmp=""
  tmp=$(mktemp -d)
  mkdir -p "$tmp/graphs" "$tmp/fakebin"
  : > "$tmp/graphs/demo.metis"
  cat > "$tmp/fakebin/scontrol" <<'EOF'
#!/usr/bin/env zsh
cat <<'PARTITION'
PartitionName=diffie
   Nodes=diffie OverSubscribe=NO
   State=UP TotalCPUs=96 TotalNodes=1 SelectTypeParameters=NONE
PARTITION
EOF
  chmod +x "$tmp/fakebin/scontrol"

  cat > "$tmp/Experiment" <<'EOF'
System slurm
Property slurm.partition diffie
Property slurm.use_array true

ExperimentFullNodeArray() {
  Algorithms TestHarness
  Graph graphs/demo
  Ks 2 4
  Seeds 1
  Epsilons 0.03
  Threads 1x1x96
}
EOF

  (
    cd "$tmp"
    PATH="$PWD/fakebin:$PATH" "$MKEXP2" probe FullNodeArray --jobs > probe-jobs.json
    PATH="$PWD/fakebin:$PATH" "$MKEXP2" generate >/dev/null

    assert_eq "$(json_value probe-jobs.json '.jobs.run_jobs[0].array_enabled')" "true" "probe reports array usage"
    assert_eq "$(json_value probe-jobs.json '.jobs.run_jobs[0].array_mode')" "scheduler" "probe falls back to scheduler arrays when one command fills a node"
    assert_eq "$(json_value probe-jobs.json '.jobs.run_jobs[0].array_max_parallel')" "1" "probe reports default array max parallel"
    assert_file_contains jobs/ExperimentFullNodeArray__1x1x96.sh "#SBATCH --array=0-1%1" "full-node commands use scheduler array fanout"
    assert_file_contains jobs/ExperimentFullNodeArray__1x1x96.sh "#SBATCH --output=slurm/slurm-%A_%a.out" "scheduler fallback uses array output files"
    assert_file_contains jobs/ExperimentFullNodeArray__1x1x96.sh "#SBATCH --nodes=1" "scheduler fallback requests one node"
    assert_file_contains jobs/ExperimentFullNodeArray__1x1x96.sh "#SBATCH --ntasks=1" "scheduler fallback keeps one task per array element"
    assert_file_contains jobs/ExperimentFullNodeArray__1x1x96.sh "#SBATCH --ntasks-per-node=1" "scheduler fallback keeps one task per node"
    assert_file_contains jobs/ExperimentFullNodeArray__1x1x96.sh "#SBATCH --cpus-per-task=96" "scheduler fallback preserves requested thread count"
    assert_file_not_contains jobs/ExperimentFullNodeArray__1x1x96.sh "# mkexp2 array mode: packed" "scheduler fallback does not emit packed launcher"
  )

  pass "slurm auto scheduler fallback for full-node array commands"
}

test_probe_kahip_parhip_alias_generation() {
  local tmp=""
  tmp=$(mktemp -d)
  mkdir -p "$tmp/graphs"
  : > "$tmp/graphs/demo.graph"

  cat > "$tmp/Experiment" <<'EOF'
System local
Property local.call_wrapper none

ExperimentKaHIPAliases() {
  Algorithms KaHIP-Fast KaHIP-Eco KaHIP-Strong KaHIP-SocialFast KaHIP-SocialEco KaHIP-SocialStrong
  Graph graphs/demo
  Ks 4
  Seeds 7
  Epsilons 0.03
  Threads 1x1x2
}

ExperimentParHIPAliases() {
  Algorithms ParHIP-Fast ParHIP-Eco ParHIP-SocialFast ParHIP-SocialEco
  Graph graphs/demo
  Ks 4
  Seeds 7
  Epsilons 0.03
  Threads 1x2x1
}
EOF

  (
    cd "$tmp"
    "$MKEXP2" probe KaHIPAliases --calls > kahip-calls.json
    assert_eq "$(jq -r '.calls | length' kahip-calls.json)" "6" "KaHIP aliases expand to six calls"
    assert_eq "$(jq -r '[.calls[].raw_command | capture("--preconfiguration=(?<p>[^ ]+)").p] | sort | join(",")' kahip-calls.json)" "eco,esocial,fast,fsocial,ssocial,strong" "KaHIP aliases use upstream preconfigurations"
    assert_eq "$(jq -r '[.calls[].raw_command | contains("--imbalance=3")] | all' kahip-calls.json)" "true" "KaHIP generation converts fractional epsilon to percent"
    assert_eq "$(jq -r '[.calls[].raw_command | contains("--num_threads")] | any' kahip-calls.json)" "false" "KaHIP generation does not pass unsupported thread count flag"

    "$MKEXP2" probe ParHIPAliases --calls > parhip-calls.json
    assert_eq "$(jq -r '.calls | length' parhip-calls.json)" "4" "ParHIP aliases expand to four calls"
    assert_eq "$(jq -r '[.calls[].raw_command | capture("--preconfiguration=(?<p>[^ ]+)").p] | sort | join(",")' parhip-calls.json)" "ecomesh,ecosocial,fastmesh,fastsocial" "ParHIP aliases use mesh and social preconfigurations"
    assert_eq "$(jq -r '[.calls[].raw_command | contains("--imbalance=3")] | all' parhip-calls.json)" "true" "ParHIP generation converts fractional epsilon to percent"
    assert_eq "$(jq -r '[.calls[].final_command | startswith("mpirun -n 2 ")] | all' parhip-calls.json)" "true" "ParHIP distributed local topology is wrapped with mpirun"
  )

  pass "KaHIP and ParHIP alias generation"
}
