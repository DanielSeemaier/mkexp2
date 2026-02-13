#!/usr/bin/env zsh

PrepareGenerateOutputs() {
  mkdir -p "$PWD/jobs" "$PWD/logs" "$MKEXP2_WORK_DIR/bin" "$MKEXP2_WORK_DIR/src"
  GENERATED_JOB_META=()
  GENERATED_JOB_KEYS=()
  MKEXP2_SLURM_HAS_RUN_JOBS=0
  MKEXP2_SLURM_PARSE_JOB_SCRIPT=""

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

submit_parse_slurm() {
  local script="$1"
  local dep_arg=""
  local out=""
  local -a dep_ids=()
  local id=""

  if [[ -n "$INSTALL_JOB_ID" ]]; then
    dep_ids+=("$INSTALL_JOB_ID")
  fi

  for id in "${(@v)JOB_IDS}"; do
    [[ -n "$id" ]] || continue
    dep_ids+=("$id")
  done

  if (( ${#dep_ids[@]} > 0 )); then
    dep_arg="--dependency=afterok:${(j/:/)dep_ids}"
  fi

  if [[ -n "$dep_arg" ]]; then
    out=$(sbatch "$dep_arg" "$script")
  else
    out=$(sbatch "$script")
  fi

  echo "$out"
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

EnsureSlurmParseJob() {
  if [[ -n "$MKEXP2_SLURM_PARSE_JOB_SCRIPT" ]]; then
    return
  fi

  local partition=""
  local qos=""
  local account=""
  local constraint=""
  local timelimit=""
  local parse_job_name=""
  local parse_cmd=""

  partition=$(ResolveRunProperty "slurm.partition" "default")
  qos=$(ResolveRunProperty "slurm.qos" "")
  account=$(ResolveRunProperty "slurm.account" "")
  constraint=$(ResolveRunProperty "slurm.constraint" "")
  timelimit=$(ResolveRunProperty "parse.slurm.timelimit" "00:30:00")

  parse_job_name="mkexp2-parse-$(SafeName "$(basename "$PWD")")"
  MKEXP2_SLURM_PARSE_JOB_SCRIPT="$PWD/jobs/parse__${MKEXP2_RUN_ID}.sh"
  parse_cmd="$(ShellQuote "$MKEXP2_HOME/bin/mkexp2") parse"

  local parse_log_dir="$PWD/logs/parse/slurm/$MKEXP2_RUN_ID"
  local parse_log_file="$parse_log_dir/parse.log"

  cat > "$MKEXP2_SLURM_PARSE_JOB_SCRIPT" <<SCRIPT
#!/usr/bin/env zsh
#SBATCH --job-name=${parse_job_name}
#SBATCH --time=${timelimit}
#SBATCH --partition=${partition}
SCRIPT

  if [[ -n "$qos" ]]; then
    echo "#SBATCH --qos=$qos" >> "$MKEXP2_SLURM_PARSE_JOB_SCRIPT"
  fi
  if [[ -n "$account" ]]; then
    echo "#SBATCH --account=$account" >> "$MKEXP2_SLURM_PARSE_JOB_SCRIPT"
  fi
  if [[ -n "$constraint" ]]; then
    echo "#SBATCH --constraint=$constraint" >> "$MKEXP2_SLURM_PARSE_JOB_SCRIPT"
  fi

  cat >> "$MKEXP2_SLURM_PARSE_JOB_SCRIPT" <<SCRIPT
set -euo pipefail

cd "$PWD"
mkdir -p "$parse_log_dir"

echo "[mkexp2] parse job started"
echo "[mkexp2] parse log: $parse_log_file"

set +e
$parse_cmd > "$parse_log_file" 2>&1
parse_exit_code=\$?
set -e

if (( parse_exit_code != 0 )); then
  echo "[mkexp2] parse failed, log: $parse_log_file"
  tail -n 200 "$parse_log_file"
fi

exit \$parse_exit_code
SCRIPT

  chmod +x "$MKEXP2_SLURM_PARSE_JOB_SCRIPT"
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

_GenerateFormatList() {
  local max_items="$1"
  shift

  local -a items=("$@")
  local count=${#items[@]}
  if (( count == 0 )); then
    echo "(none)"
    return
  fi

  if (( count <= max_items )); then
    echo "${(j:, :)items}"
    return
  fi

  local -a head=("${(@)items[1,$max_items]}")
  echo "${(j:, :)head} (+$((count - max_items)) more)"
}

_GenerateInfoKV() {
  local key="$1"
  local value="$2"
  _UiTag info
  printf "  %s %-14s %s\n" "$MKEXP2_UI_TAG" "${key}:" "$value"
}

_GenerateSummaryDivider() {
  InitUi
  printf "  %s%s%s\n" "$MKEXP2_UI_DIM" "------------------------------------------------------------" "$MKEXP2_UI_RESET"
}

GenerateCurrentExperiment() {
  local experiment_name="$1"
  local experiment_label=""
  experiment_label=$(SafeName "$experiment_name")
  local experiment_display=""
  experiment_display=$(DisplayExperimentName "$experiment_name")

  local total_generated_calls=0
  local -A generated_calls_per_algorithm=()
  local -A generated_calls_per_topology=()
  local -a generated_topologies=()
  local -A seen_partitioners=()
  local -a partitioners=()
  local -a algorithm_labels=()
  local -A seen_graph_names=()
  local -a graph_names=()

  local algorithm=""
  for algorithm in "${_algorithms[@]}"; do
    local base="${FLAT_ALGO_BASE["$algorithm"]:-}"
    if [[ -z "$base" ]]; then
      base=$(GetAlgorithmBase "$algorithm")
    fi

    if [[ "$algorithm" == "$base" ]]; then
      algorithm_labels+=("$algorithm")
    else
      algorithm_labels+=("${algorithm}[$base]")
    fi

    if [[ -z "${seen_partitioners["$base"]:-}" ]]; then
      seen_partitioners["$base"]=1
      partitioners+=("$base")
    fi
  done

  local graph=""
  for graph in "${_graphs[@]}"; do
    local graph_name="${graph:t}"
    if [[ -z "${seen_graph_names["$graph_name"]:-}" ]]; then
      seen_graph_names["$graph_name"]=1
      graph_names+=("$graph_name")
    fi
  done

  if [[ "$_system" == "slurm" ]]; then
    local install_mode=""
    install_mode=$(ResolveRunProperty "slurm.install.mode" "local")
    if [[ "$install_mode" == "job" ]]; then
      MKEXP2_SLURM_INSTALL_JOB_REQUIRED=1
      EnsureSlurmInstallJob
    fi
  fi

  local wrap_fn="LauncherWrapCommand_${_system}"
  if ! FunctionExists "$wrap_fn"; then
    EchoFatal "launcher ${_system} is missing $wrap_fn"
    exit 1
  fi

  local per_instance_limit=""
  per_instance_limit=$(ResolveRunProperty "timelimit.per_instance" "$_timelimit_per_instance")
  local timeout_prefix=""
  if [[ -n "$per_instance_limit" ]]; then
    local timeout_seconds=""
    timeout_seconds=$(ParseTimelimitToSeconds "$per_instance_limit")
    timeout_prefix="timeout -v ${timeout_seconds}s "
  fi

  local timelimit=""
  timelimit=$(ResolveRunProperty "timelimit" "$_timelimit")

  # Precompute algorithm runtime contexts once per experiment.
  local -A ctx_base=()
  local -A ctx_binary_path=()
  local -A ctx_args=()
  local -A ctx_supports_distributed=()
  local -A ctx_use_openmp_env=()
  local -A ctx_invoke_fn=()
  local -A ctx_log_dir=()

  for algorithm in "${_algorithms[@]}"; do
    PopulateBuildContext "$algorithm"
    LoadPartitionerPlugin "$CTX_base"

    local invoke_fn="PartitionerInvoke_${CTX_base}"
    if ! FunctionExists "$invoke_fn"; then
      EchoFatal "plugin ${CTX_base} is missing $invoke_fn"
      exit 1
    fi

    ctx_base["$algorithm"]="$CTX_base"
    ctx_binary_path["$algorithm"]="$CTX_binary_path"
    ctx_args["$algorithm"]="$CTX_args"
    ctx_supports_distributed["$algorithm"]="$CTX_supports_distributed"
    ctx_use_openmp_env["$algorithm"]="$CTX_use_openmp_env"
    ctx_invoke_fn["$algorithm"]="$invoke_fn"

    local log_dir="$PWD/logs/$algorithm/$experiment_label"
    mkdir -p "$log_dir"
    ctx_log_dir["$algorithm"]="$log_dir"
  done

  local topology=""
  for topology in "${_threads[@]}"; do
    local nodes="1"
    local mpis="1"
    local threads="$topology"
    if [[ "$topology" == *x*x* ]]; then
      nodes="${topology%%x*}"
      local without_threads="${topology%x*}"
      mpis="${without_threads#*x}"
      threads="${topology##*x}"
    fi
    local distributed="false"
    if (( nodes > 1 || mpis > 1 )); then
      distributed="true"
    fi

    local job_name="${experiment_label}__${topology}"
    local job_key="${experiment_name}:${topology}"

    local cmd_file="$PWD/jobs/${job_name}.cmds"
    local job_script="$PWD/jobs/${job_name}.sh"
    : > "$cmd_file"
    local cmd_count=0
    local cmd_fd=-1
    exec {cmd_fd}> "$cmd_file"

    for algorithm in "${_algorithms[@]}"; do
      if [[ "$distributed" == "true" && "${ctx_supports_distributed["$algorithm"]}" != "true" ]]; then
        EchoFatal "$algorithm does not support distributed mode ($topology)"
        exit 1
      fi

      local invoke_fn="${ctx_invoke_fn["$algorithm"]}"
      local use_openmp_env="${ctx_use_openmp_env["$algorithm"]}"
      local log_dir="${ctx_log_dir["$algorithm"]}"

      local seed=""
      for seed in "${_seeds[@]}"; do
        local epsilon=""
        for epsilon in "${_epsilons[@]}"; do
          local k=""
          for k in "${_ks[@]}"; do
            for graph in "${_graphs[@]}"; do
              RUN_algorithm="$algorithm"
              RUN_base="${ctx_base["$algorithm"]}"
              RUN_binary_path="${ctx_binary_path["$algorithm"]}"
              RUN_args="${ctx_args["$algorithm"]}"
              RUN_graph="$graph"
              RUN_k="$k"
              RUN_epsilon="$epsilon"
              RUN_seed="$seed"
              RUN_nodes="$nodes"
              RUN_mpis="$mpis"
              RUN_threads="$threads"

              local raw_cmd=""
              PARTITIONER_INVOKE_CMD=""
              "$invoke_fn" >/dev/null
              raw_cmd="$PARTITIONER_INVOKE_CMD"
              if [[ -z "$raw_cmd" ]]; then
                # Backward compatibility: older plugins may still print the command.
                raw_cmd=$("$invoke_fn")
              fi
              if [[ -z "$raw_cmd" ]]; then
                EchoFatal "plugin ${ctx_base["$algorithm"]} produced an empty invoke command"
                exit 1
              fi

              local wrapped_cmd=""
              LAUNCHER_WRAPPED_CMD=""
              "$wrap_fn" "$raw_cmd" "$nodes" "$mpis" "$threads" "$distributed" "$use_openmp_env" >/dev/null
              wrapped_cmd="$LAUNCHER_WRAPPED_CMD"
              if [[ -z "$wrapped_cmd" ]]; then
                # Backward compatibility: older launchers may still print wrapped commands.
                wrapped_cmd=$("$wrap_fn" "$raw_cmd" "$nodes" "$mpis" "$threads" "$distributed" "$use_openmp_env")
              fi
              if [[ -z "$wrapped_cmd" ]]; then
                EchoFatal "launcher ${_system} produced an empty wrapped command"
                exit 1
              fi

              if [[ -n "$timeout_prefix" ]]; then
                wrapped_cmd="${timeout_prefix}${wrapped_cmd}"
              fi

              local graph_name="${graph:t}"
              local id="${graph_name}__k${k}__s${seed}__e${epsilon}__${topology}"
              local log_file="$log_dir/${id}.log"
              print -r -- "$wrapped_cmd >> \"$log_file\" 2>&1" >&$cmd_fd

              total_generated_calls=$((total_generated_calls + 1))
              cmd_count=$((cmd_count + 1))
              generated_calls_per_algorithm["$algorithm"]=$(( ${generated_calls_per_algorithm["$algorithm"]:-0} + 1 ))
              generated_calls_per_topology["$topology"]=$(( ${generated_calls_per_topology["$topology"]:-0} + 1 ))
            done
          done
        done
      done
    done

    exec {cmd_fd}>&-

    if (( cmd_count == 0 )); then
      rm -f "$cmd_file"
      continue
    fi
    generated_topologies+=("$topology")

    local write_fn="LauncherWriteJob_${_system}"
    "$write_fn" "$job_script" "$cmd_file" "$job_name" "$nodes" "$mpis" "$threads" "$timelimit" "$cmd_count"
    chmod +x "$job_script"

    local dependency_key=""
    dependency_key=$(ResolveDependencyKey "$topology")

    GENERATED_JOB_META["$job_key"]="${_system}|$job_script|$dependency_key"
    GENERATED_JOB_KEYS+=("$job_key")
  done

  local algorithms_summary=""
  local partitioners_summary=""
  local graphs_summary=""
  local ks_summary=""
  local seeds_summary=""
  local epsilons_summary=""
  local topologies_summary=""
  local -a calls_per_algorithm_parts=()
  local -a calls_per_topology_parts=()

  algorithms_summary=$(_GenerateFormatList 5 "${algorithm_labels[@]}")
  partitioners_summary=$(_GenerateFormatList 6 "${partitioners[@]}")
  graphs_summary=$(_GenerateFormatList 8 "${graph_names[@]}")
  ks_summary=$(_GenerateFormatList 8 "${_ks[@]}")
  seeds_summary=$(_GenerateFormatList 8 "${_seeds[@]}")
  epsilons_summary=$(_GenerateFormatList 8 "${_epsilons[@]}")
  topologies_summary=$(_GenerateFormatList 8 "${generated_topologies[@]}")

  for algorithm in "${_algorithms[@]}"; do
    calls_per_algorithm_parts+=("$algorithm=${generated_calls_per_algorithm["$algorithm"]:-0}")
  done
  local topology=""
  for topology in "${generated_topologies[@]}"; do
    calls_per_topology_parts+=("$topology=${generated_calls_per_topology["$topology"]:-0}")
  done

  EchoStep "Generated experiment summary: $experiment_display"
  _GenerateSummaryDivider
  _GenerateInfoKV "launcher" "$_system"
  _GenerateInfoKV "calls" "$total_generated_calls total (${#generated_topologies[@]} job script(s))"
  _GenerateInfoKV "algorithms" "$algorithms_summary"
  _GenerateInfoKV "partitioners" "$partitioners_summary"
  _GenerateInfoKV "graphs" "$graphs_summary"
  _GenerateInfoKV "ks" "$ks_summary"
  _GenerateInfoKV "epsilons" "$epsilons_summary"
  _GenerateInfoKV "seeds" "$seeds_summary"
  _GenerateInfoKV "topologies" "$topologies_summary"
  _GenerateInfoKV "per algorithm" "$(_GenerateFormatList 8 "${calls_per_algorithm_parts[@]}")"
  _GenerateInfoKV "per topology" "$(_GenerateFormatList 8 "${calls_per_topology_parts[@]}")"
  _GenerateSummaryDivider
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
      MKEXP2_SLURM_HAS_RUN_JOBS=1
      printf 'submit_slurm %q %q %q\n' "$key" "$dep_key" "$job_script" >> "$PWD/submit.sh"
    else
      printf 'submit_local %q\n' "$job_script" >> "$PWD/submit.sh"
    fi
  done

  if (( MKEXP2_PARSE_AUTO_REQUIRED )); then
    if (( MKEXP2_SLURM_HAS_RUN_JOBS )); then
      EnsureSlurmParseJob
      printf 'submit_parse_slurm %q\n' "$MKEXP2_SLURM_PARSE_JOB_SCRIPT" >> "$PWD/submit.sh"
      EchoStep "Generated Slurm parse job: $MKEXP2_SLURM_PARSE_JOB_SCRIPT"
    else
      local parse_cmd=""
      parse_cmd="$(ShellQuote "$MKEXP2_HOME/bin/mkexp2") parse"
      {
        echo "echo \"==> Parsing logs into CSV\""
        echo "$parse_cmd"
      } >> "$PWD/submit.sh"
      EchoStep "Enabled auto-parse in submit script"
    fi
  fi

  EchoStep "Generated submit script: $PWD/submit.sh"
}
