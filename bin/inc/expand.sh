#!/usr/bin/env zsh

LoadExperimentFunctionState() {
  local experiment_file="$1"
  local local_fn="$2"

  ResetExperiment
  LoadPartitionerAliasHooks
  # Re-source for each experiment function to restore global declarations.
  . "$experiment_file"
  "$local_fn"

  EnsureExperimentDefaults
  FlattenAlgorithmHierarchy
  RegisterCurrentExperimentParsers
  LoadLauncherPlugin "$_system"
}

ResetExpandedModel() {
  EXPAND_CALL_IDS=()
  EXPAND_CALL=()
  EXPAND_JOB_KEYS=()
  EXPAND_JOB=()
  EXPAND_CALL_COUNT_BY_ALGORITHM=()
  EXPAND_CALL_COUNT_BY_TOPOLOGY=()
  EXPAND_GENERATED_TOPOLOGIES=()
  EXPAND_ALGORITHM_LABELS=()
  EXPAND_PARTITIONERS=()
  EXPAND_GRAPH_NAMES=()
  EXPAND_EXPERIMENT_NAME=""
  EXPAND_EXPERIMENT_LABEL=""
  EXPAND_EXPERIMENT_DISPLAY=""
  EXPAND_TIMEOUT_PREFIX=""
  EXPAND_TIMELIMIT=""
  EXPAND_WRAP_FN=""
  EXPAND_TOTAL_CALLS=0
  EXPAND_SLURM_INSTALL_MODE=""
  EXPAND_PARSE_AUTO=""
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

  local graph_name="${graph:t}"
  echo "${graph_name}___k${k}_seed${seed}_eps${epsilon}_P${topology}"
}

ExpandCurrentExperiment() {
  local experiment_name="$1"
  local expand_mode="${2:-generate}"

  ResetExpandedModel

  EXPAND_EXPERIMENT_NAME="$experiment_name"
  EXPAND_EXPERIMENT_LABEL="$(SafeName "$experiment_name")"
  EXPAND_EXPERIMENT_DISPLAY="$(DisplayExperimentName "$experiment_name")"

  local -A seen_partitioners=()
  local -A seen_graph_names=()
  local -A ctx_base=()
  local -A ctx_binary_path=()
  local -A ctx_args=()
  local -A ctx_supports_distributed=()
  local -A ctx_use_openmp_env=()
  local -A ctx_invoke_fn=()
  local -A ctx_log_dir=()
  local -A generated_topology_seen=()

  local algorithm=""
  for algorithm in "${_algorithms[@]}"; do
    local base="${FLAT_ALGO_BASE["$algorithm"]:-}"
    if [[ -z "$base" ]]; then
      base=$(GetAlgorithmBase "$algorithm")
    fi

    if [[ "$algorithm" == "$base" ]]; then
      EXPAND_ALGORITHM_LABELS+=("$algorithm")
    else
      EXPAND_ALGORITHM_LABELS+=("${algorithm}[$base]")
    fi

    if [[ -z "${seen_partitioners["$base"]:-}" ]]; then
      seen_partitioners["$base"]=1
      EXPAND_PARTITIONERS+=("$base")
    fi

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
    ctx_log_dir["$algorithm"]="$PWD/logs/$algorithm/$EXPAND_EXPERIMENT_LABEL"
  done

  local graph=""
  for graph in "${_graphs[@]}"; do
    local graph_name="${graph:t}"
    if [[ -z "${seen_graph_names["$graph_name"]:-}" ]]; then
      seen_graph_names["$graph_name"]=1
      EXPAND_GRAPH_NAMES+=("$graph_name")
    fi
  done

  local wrap_fn="LauncherWrapCommand_${_system}"
  if ! FunctionExists "$wrap_fn"; then
    EchoFatal "launcher ${_system} is missing $wrap_fn"
    exit 1
  fi
  EXPAND_WRAP_FN="$wrap_fn"

  local per_instance_limit=""
  per_instance_limit=$(ResolveRunProperty "timelimit.per_instance" "$_timelimit_per_instance")
  if [[ -n "$per_instance_limit" ]]; then
    local timeout_seconds=""
    timeout_seconds=$(ParseTimelimitToSeconds "$per_instance_limit")
    EXPAND_TIMEOUT_PREFIX="timeout -v ${timeout_seconds}s "
  fi

  EXPAND_TIMELIMIT="$(ResolveRunProperty "timelimit" "$_timelimit")"
  EXPAND_SLURM_INSTALL_MODE="$(ResolveRunProperty "slurm.install.mode" "local")"
  EXPAND_PARSE_AUTO="$(ResolveRunProperty "parse.auto" "false")"

  local topology=""
  for topology in "${_threads[@]}"; do
    local nodes=""
    local mpis=""
    local threads=""
    local distributed="false"

    nodes=$(ParseNodes "$topology")
    mpis=$(ParseMpis "$topology")
    threads=$(ParseThreads "$topology")
    if (( nodes > 1 || mpis > 1 )); then
      distributed="true"
    fi

    local job_name="${EXPAND_EXPERIMENT_LABEL}__${topology}"
    local launcher_job_name="$job_name"
    if [[ "$_system" == "slurm" ]]; then
      local slurm_experiment_name=""
      slurm_experiment_name=$(SafeName "$EXPAND_EXPERIMENT_DISPLAY")
      launcher_job_name="${slurm_experiment_name}/${threads}"
    fi
    local job_key="${experiment_name}:${topology}"
    local cmd_file="$PWD/jobs/${job_name}.cmds"
    local job_script="$PWD/jobs/${job_name}.sh"
    local dependency_key=""
    dependency_key=$(ResolveDependencyKey "$topology")
    local cmd_count=0

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
              local wrapped_cmd=""
              local final_cmd=""
              local call_id="$(( ${#EXPAND_CALL_IDS[@]} + 1 ))"
              local instance_id=""
              local log_file=""
              local previous_probe_mode="$MKEXP2_PROBE_MODE"

              PARTITIONER_INVOKE_CMD=""
              MKEXP2_ACTIVE_ALGORITHM="$RUN_algorithm"
              if [[ "$expand_mode" == "probe" ]]; then
                MKEXP2_PROBE_MODE=1
              else
                MKEXP2_PROBE_MODE=0
              fi
              "$invoke_fn" >/dev/null
              raw_cmd="$PARTITIONER_INVOKE_CMD"
              if [[ -z "$raw_cmd" ]]; then
                raw_cmd=$("$invoke_fn")
              fi
              MKEXP2_ACTIVE_ALGORITHM=""
              MKEXP2_PROBE_MODE="$previous_probe_mode"
              if [[ -z "$raw_cmd" ]]; then
                EchoFatal "plugin ${ctx_base["$algorithm"]} produced an empty invoke command"
                exit 1
              fi

              LAUNCHER_WRAPPED_CMD=""
              "$wrap_fn" "$raw_cmd" "$nodes" "$mpis" "$threads" "$distributed" "$use_openmp_env" >/dev/null
              wrapped_cmd="$LAUNCHER_WRAPPED_CMD"
              if [[ -z "$wrapped_cmd" ]]; then
                wrapped_cmd=$("$wrap_fn" "$raw_cmd" "$nodes" "$mpis" "$threads" "$distributed" "$use_openmp_env")
              fi
              if [[ -z "$wrapped_cmd" ]]; then
                EchoFatal "launcher ${_system} produced an empty wrapped command"
                exit 1
              fi

              final_cmd="$wrapped_cmd"
              if [[ -n "$EXPAND_TIMEOUT_PREFIX" ]]; then
                final_cmd="${EXPAND_TIMEOUT_PREFIX}${final_cmd}"
              fi

              instance_id=$(BuildInstanceId "$graph" "$k" "$seed" "$epsilon" "$topology")
              log_file="$log_dir/${instance_id}.log"

              EXPAND_CALL_IDS+=("$call_id")
              EXPAND_CALL["$call_id::job_key"]="$job_key"
              EXPAND_CALL["$call_id::experiment_name"]="$experiment_name"
              EXPAND_CALL["$call_id::experiment_display"]="$EXPAND_EXPERIMENT_DISPLAY"
              EXPAND_CALL["$call_id::experiment_label"]="$EXPAND_EXPERIMENT_LABEL"
              EXPAND_CALL["$call_id::algorithm"]="$algorithm"
              EXPAND_CALL["$call_id::base"]="${ctx_base["$algorithm"]}"
              EXPAND_CALL["$call_id::binary_path"]="${ctx_binary_path["$algorithm"]}"
              EXPAND_CALL["$call_id::args"]="${ctx_args["$algorithm"]}"
              EXPAND_CALL["$call_id::graph"]="$graph"
              EXPAND_CALL["$call_id::graph_name"]="${graph:t}"
              EXPAND_CALL["$call_id::k"]="$k"
              EXPAND_CALL["$call_id::epsilon"]="$epsilon"
              EXPAND_CALL["$call_id::seed"]="$seed"
              EXPAND_CALL["$call_id::topology"]="$topology"
              EXPAND_CALL["$call_id::nodes"]="$nodes"
              EXPAND_CALL["$call_id::mpis"]="$mpis"
              EXPAND_CALL["$call_id::threads"]="$threads"
              EXPAND_CALL["$call_id::distributed"]="$distributed"
              EXPAND_CALL["$call_id::raw_command"]="$raw_cmd"
              EXPAND_CALL["$call_id::wrapped_command"]="$wrapped_cmd"
              EXPAND_CALL["$call_id::final_command"]="$final_cmd"
              EXPAND_CALL["$call_id::log_file"]="$log_file"
              EXPAND_CALL["$call_id::instance_id"]="$instance_id"

              EXPAND_TOTAL_CALLS=$((EXPAND_TOTAL_CALLS + 1))
              cmd_count=$((cmd_count + 1))
              EXPAND_CALL_COUNT_BY_ALGORITHM["$algorithm"]=$(( ${EXPAND_CALL_COUNT_BY_ALGORITHM["$algorithm"]:-0} + 1 ))
              EXPAND_CALL_COUNT_BY_TOPOLOGY["$topology"]=$(( ${EXPAND_CALL_COUNT_BY_TOPOLOGY["$topology"]:-0} + 1 ))
            done
          done
        done
      done
    done

    if (( cmd_count == 0 )); then
      continue
    fi

    if [[ -z "${generated_topology_seen["$topology"]:-}" ]]; then
      generated_topology_seen["$topology"]=1
      EXPAND_GENERATED_TOPOLOGIES+=("$topology")
    fi

    EXPAND_JOB_KEYS+=("$job_key")
    EXPAND_JOB["$job_key::system"]="${_system}"
    EXPAND_JOB["$job_key::experiment_name"]="$experiment_name"
    EXPAND_JOB["$job_key::experiment_display"]="$EXPAND_EXPERIMENT_DISPLAY"
    EXPAND_JOB["$job_key::experiment_label"]="$EXPAND_EXPERIMENT_LABEL"
    EXPAND_JOB["$job_key::job_name"]="$job_name"
    EXPAND_JOB["$job_key::launcher_job_name"]="$launcher_job_name"
    EXPAND_JOB["$job_key::cmd_file"]="$cmd_file"
    EXPAND_JOB["$job_key::job_script"]="$job_script"
    EXPAND_JOB["$job_key::dependency_key"]="$dependency_key"
    EXPAND_JOB["$job_key::topology"]="$topology"
    EXPAND_JOB["$job_key::nodes"]="$nodes"
    EXPAND_JOB["$job_key::mpis"]="$mpis"
    EXPAND_JOB["$job_key::threads"]="$threads"
    EXPAND_JOB["$job_key::distributed"]="$distributed"
    EXPAND_JOB["$job_key::cmd_count"]="$cmd_count"
  done
}
