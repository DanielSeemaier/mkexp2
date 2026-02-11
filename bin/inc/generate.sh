#!/usr/bin/env zsh

PrepareGenerateOutputs() {
  mkdir -p "$PWD/jobs" "$PWD/logs" "$MKEXP2_WORK_DIR/bin" "$MKEXP2_WORK_DIR/src"
  GENERATED_JOB_META=()
  GENERATED_JOB_KEYS=()

  cat > "$PWD/submit.sh" <<'SCRIPT'
#!/usr/bin/env zsh
set -euo pipefail

typeset -A JOB_IDS=()
INSTALL_JOB_ID=""

submit_install_slurm() {
  local script="$1"
  local out=""

  out=$(sbatch "$script")
  echo "$out"
  INSTALL_JOB_ID=$(echo "$out" | awk '{print $NF}')
}

submit_slurm() {
  local key="$1"
  local dep_key="$2"
  local script="$3"
  local dep_arg=""
  local -a dep_ids=()

  if [[ -n "$dep_key" ]]; then
    local dep_id="${JOB_IDS["$dep_key"]:-}"
    if [[ -n "$dep_id" ]]; then
      dep_ids+=("$dep_id")
    else
      echo "warning: dependency key '$dep_key' not submitted yet, submitting without that dependency"
    fi
  fi

  if [[ -n "$INSTALL_JOB_ID" ]]; then
    dep_ids+=("$INSTALL_JOB_ID")
  fi

  if (( ${#dep_ids[@]} > 0 )); then
    dep_arg="--dependency=afterok:${(j/:/)dep_ids}"
  fi

  local out=""
  if [[ -n "$dep_arg" ]]; then
    out=$(sbatch "$dep_arg" "$script")
  else
    out=$(sbatch "$script")
  fi

  echo "$out"
  local id=""
  id=$(echo "$out" | awk '{print $NF}')
  JOB_IDS["$key"]="$id"
}

submit_local() {
  local script="$1"
  zsh "$script"
}
SCRIPT

  chmod +x "$PWD/submit.sh"
}

EnsureSlurmInstallJob() {
  if [[ -n "$MKEXP2_SLURM_INSTALL_JOB_SCRIPT" ]]; then
    return
  fi

  local partition=""
  local qos=""
  local account=""
  local constraint=""
  local timelimit=""
  local install_job_name=""
  local install_cmd=""

  partition=$(ResolveRunProperty "slurm.partition" "default")
  qos=$(ResolveRunProperty "slurm.qos" "")
  account=$(ResolveRunProperty "slurm.account" "")
  constraint=$(ResolveRunProperty "slurm.constraint" "")
  timelimit=$(ResolveRunProperty "slurm.install.timelimit" "02:00:00")

  install_job_name="mkexp2-install-$(SafeName "$(basename "$PWD")")"
  MKEXP2_SLURM_INSTALL_JOB_SCRIPT="$PWD/jobs/install__${MKEXP2_RUN_ID}.sh"
  install_cmd="$(ShellQuote "$MKEXP2_HOME/bin/mkexp2") install"
  if [[ -n "$MKEXP2_BUILD_MAX_CORES" ]]; then
    install_cmd+=" --build-max-cores $(ShellQuote "$MKEXP2_BUILD_MAX_CORES")"
  fi

  local install_log_dir="$PWD/logs/install/slurm/$MKEXP2_RUN_ID"
  local command_log_dir="$install_log_dir/commands"
  local run_log_file="$install_log_dir/install.log"

  cat > "$MKEXP2_SLURM_INSTALL_JOB_SCRIPT" <<SCRIPT
#!/usr/bin/env zsh
#SBATCH --job-name=${install_job_name}
#SBATCH --time=${timelimit}
#SBATCH --partition=${partition}
SCRIPT

  if [[ -n "$qos" ]]; then
    echo "#SBATCH --qos=$qos" >> "$MKEXP2_SLURM_INSTALL_JOB_SCRIPT"
  fi
  if [[ -n "$account" ]]; then
    echo "#SBATCH --account=$account" >> "$MKEXP2_SLURM_INSTALL_JOB_SCRIPT"
  fi
  if [[ -n "$constraint" ]]; then
    echo "#SBATCH --constraint=$constraint" >> "$MKEXP2_SLURM_INSTALL_JOB_SCRIPT"
  fi

  cat >> "$MKEXP2_SLURM_INSTALL_JOB_SCRIPT" <<SCRIPT
set -euo pipefail

cd "$PWD"
mkdir -p "$install_log_dir"
mkdir -p "$command_log_dir"

echo "[mkexp2] install job started"
echo "[mkexp2] install log: $run_log_file"

set +e
MKEXP2_INSTALL_LOG_DIR="$command_log_dir" MKEXP2_RUN_VERBOSE=1 $install_cmd > "$run_log_file" 2>&1
install_exit_code=\$?
set -e

if (( install_exit_code != 0 )); then
  echo "[mkexp2] install failed, log: $run_log_file"
  tail -n 200 "$run_log_file"
fi

exit \$install_exit_code
SCRIPT

  chmod +x "$MKEXP2_SLURM_INSTALL_JOB_SCRIPT"
}

ResolveDependencyKey() {
  local topology="$1"

  local dep=""
  dep=$(ResolveRunProperty "slurm.dependency" "")
  if [[ -z "$dep" ]]; then
    echo ""
    return
  fi

  if [[ "$dep" == afterok:* ]]; then
    local dep_name="${dep#afterok:}"
    echo "${dep_name}:${topology}"
  else
    echo "$dep"
  fi
}

BuildInstanceId() {
  local graph="$1"
  local k="$2"
  local seed="$3"
  local epsilon="$4"
  local topology="$5"

  local graph_name=""
  graph_name=$(basename "$graph")
  echo "${graph_name}__k${k}__s${seed}__e${epsilon}__${topology}"
}

GenerateCurrentExperiment() {
  local experiment_name="$1"
  local experiment_label=""
  experiment_label=$(SafeName "$experiment_name")

  if [[ "$_system" == "slurm" ]]; then
    local install_mode=""
    install_mode=$(ResolveRunProperty "slurm.install.mode" "local")
    if [[ "$install_mode" == "job" ]]; then
      MKEXP2_SLURM_INSTALL_JOB_REQUIRED=1
      EnsureSlurmInstallJob
    fi
  fi

  local topology=""
  for topology in "${_threads[@]}"; do
    local nodes mpis threads
    nodes=$(ParseNodes "$topology")
    mpis=$(ParseMpis "$topology")
    threads=$(ParseThreads "$topology")

    local job_name="${experiment_label}__${topology}"
    local job_key="${experiment_name}:${topology}"

    local cmd_file="$PWD/jobs/${job_name}.cmds"
    local job_script="$PWD/jobs/${job_name}.sh"
    : > "$cmd_file"

    local per_instance_limit=""
    per_instance_limit=$(ResolveRunProperty "timelimit.per_instance" "$_timelimit_per_instance")

    local algorithm=""
    for algorithm in "${_algorithms[@]}"; do
      PopulateBuildContext "$algorithm"
      LoadPartitionerPlugin "$CTX_base"

      local distributed="false"
      if (( nodes > 1 || mpis > 1 )); then
        distributed="true"
      fi
      if [[ "$distributed" == "true" && "$CTX_supports_distributed" != "true" ]]; then
        EchoFatal "$algorithm does not support distributed mode ($topology)"
        exit 1
      fi

      mkdir -p "$PWD/logs/$algorithm/$experiment_label"

      local seed=""
      for seed in "${_seeds[@]}"; do
        local epsilon=""
        for epsilon in "${_epsilons[@]}"; do
          local k=""
          for k in "${_ks[@]}"; do
            local graph=""
            for graph in "${_graphs[@]}"; do
              RUN_algorithm="$algorithm"
              RUN_base="$CTX_base"
              RUN_binary_path="$CTX_binary_path"
              RUN_args="$CTX_args"
              RUN_graph="$graph"
              RUN_k="$k"
              RUN_epsilon="$epsilon"
              RUN_seed="$seed"
              RUN_nodes="$nodes"
              RUN_mpis="$mpis"
              RUN_threads="$threads"

              local invoke_fn="PartitionerInvoke_${CTX_base}"
              if ! FunctionExists "$invoke_fn"; then
                EchoFatal "plugin ${CTX_base} is missing $invoke_fn"
                exit 1
              fi

              local raw_cmd=""
              raw_cmd=$("$invoke_fn")

              local wrap_fn="LauncherWrapCommand_${_system}"
              local wrapped_cmd=""
              wrapped_cmd=$("$wrap_fn" "$raw_cmd" "$nodes" "$mpis" "$threads" "$distributed" "$CTX_use_openmp_env")

              if [[ -n "$per_instance_limit" ]]; then
                local timeout_seconds=""
                timeout_seconds=$(ParseTimelimitToSeconds "$per_instance_limit")
                wrapped_cmd="timeout -v ${timeout_seconds}s $wrapped_cmd"
              fi

              local id=""
              id=$(BuildInstanceId "$graph" "$k" "$seed" "$epsilon" "$topology")
              local log_file="$PWD/logs/$algorithm/$experiment_label/${id}.log"
              printf '%s\n' "$wrapped_cmd >> \"$log_file\" 2>&1" >> "$cmd_file"
            done
          done
        done
      done
    done

    local cmd_count=""
    cmd_count=$(wc -l < "$cmd_file" | tr -d ' ')
    if [[ "$cmd_count" == "0" ]]; then
      rm -f "$cmd_file"
      continue
    fi

    local timelimit=""
    timelimit=$(ResolveRunProperty "timelimit" "$_timelimit")

    local write_fn="LauncherWriteJob_${_system}"
    "$write_fn" "$job_script" "$cmd_file" "$job_name" "$nodes" "$mpis" "$threads" "$timelimit" "$cmd_count"
    chmod +x "$job_script"

    local dependency_key=""
    dependency_key=$(ResolveDependencyKey "$topology")

    GENERATED_JOB_META["$job_key"]="${_system}|$job_script|$dependency_key"
    GENERATED_JOB_KEYS+=("$job_key")
  done
}

FinalizeGenerateOutputs() {
  if (( MKEXP2_SLURM_INSTALL_JOB_REQUIRED )) && [[ -n "$MKEXP2_SLURM_INSTALL_JOB_SCRIPT" ]]; then
    printf 'submit_install_slurm %q\n' "$MKEXP2_SLURM_INSTALL_JOB_SCRIPT" >> "$PWD/submit.sh"
    EchoStep "Generated Slurm install job: $MKEXP2_SLURM_INSTALL_JOB_SCRIPT"
  fi

  local key=""
  for key in "${GENERATED_JOB_KEYS[@]}"; do
    local entry="${GENERATED_JOB_META["$key"]:-}"
    if [[ -z "$entry" ]]; then
      continue
    fi

    local launcher=""
    local job_script=""
    local dep_key=""

    IFS='|' read -r launcher job_script dep_key <<< "$entry"
    if [[ "$launcher" == "slurm" ]]; then
      printf 'submit_slurm %q %q %q\n' "$key" "$dep_key" "$job_script" >> "$PWD/submit.sh"
    else
      printf 'submit_local %q\n' "$job_script" >> "$PWD/submit.sh"
    fi
  done

  EchoStep "Generated submit script: $PWD/submit.sh"
}
