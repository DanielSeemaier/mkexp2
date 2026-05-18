#!/usr/bin/env zsh

PrepareGenerateOutputs() {
  mkdir -p "$PWD/jobs" "$PWD/logs" "$MKEXP2_WORK_DIR/bin" "$MKEXP2_WORK_DIR/src"
  GENERATED_JOB_META=()
  GENERATED_JOB_KEYS=()
  MKEXP2_LOCAL_HAS_RUN_JOBS=0
  MKEXP2_SLURM_HAS_RUN_JOBS=0
  MKEXP2_SLURM_PARSE_JOB_SCRIPT=""

  cat > "$PWD/submit.sh" <<'SCRIPT'
#!/usr/bin/env zsh
set -euo pipefail

typeset -A JOB_IDS=()
typeset -A AVAILABLE_ALGORITHMS=()
typeset -A SELECTED_ALGORITHM_SET=()
typeset -a SELECTED_ALGORITHMS=()
typeset -a REGISTERED_META_FILES=()
SUBMIT_INSTALL=0
INSTALL_JOB_ID=""
SUBMIT_DIR="${0:A:h}"
FILTER_DIR=""
SELECTED_FILTER_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install)
      SUBMIT_INSTALL=1
      shift
      ;;
    --)
      shift
      SELECTED_ALGORITHMS+=("$@")
      break
      ;;
    --*)
      echo "error: unknown submit option: $1" >&2
      exit 1
      ;;
    *)
      SELECTED_ALGORITHMS+=("$1")
      shift
      ;;
  esac
done

cd "$SUBMIT_DIR"

for algorithm in "${SELECTED_ALGORITHMS[@]}"; do
  SELECTED_ALGORITHM_SET["$algorithm"]=1
done

ensure_slurm_dir() {
  mkdir -p "$SUBMIT_DIR/slurm"
}

ensure_filter_dir() {
  if [[ -z "$FILTER_DIR" ]]; then
    FILTER_DIR="$SUBMIT_DIR/.mkexp2/submit-filter-$(date +%Y%m%d-%H%M%S)-$$"
    mkdir -p "$FILTER_DIR"
  fi
}

prepare_selected_filter_file() {
  if (( ${#SELECTED_ALGORITHMS[@]} == 0 )); then
    return 0
  fi
  ensure_filter_dir
  if [[ -z "$SELECTED_FILTER_FILE" ]]; then
    SELECTED_FILTER_FILE="$FILTER_DIR/algorithms.txt"
    : > "$SELECTED_FILTER_FILE"
    local algorithm=""
    for algorithm in "${SELECTED_ALGORITHMS[@]}"; do
      print -r -- "$algorithm" >> "$SELECTED_FILTER_FILE"
    done
  fi
}

register_meta() {
  local meta_file="$1"
  local index=""
  local algorithm=""
  local base=""
  local experiment=""
  local topology=""
  local log_file=""

  REGISTERED_META_FILES+=("$meta_file")
  if [[ ! -f "$meta_file" ]]; then
    echo "warning: metadata file not found: $meta_file" >&2
    return
  fi

  while IFS=$'\t' read -r index algorithm base experiment topology log_file; do
    [[ -n "$algorithm" ]] || continue
    AVAILABLE_ALGORITHMS["$algorithm"]=1
  done < "$meta_file"
}

format_available_algorithms() {
  local -a names=("${(@ko)AVAILABLE_ALGORITHMS}")
  if (( ${#names[@]} == 0 )); then
    echo "(none)"
  else
    echo "${(j:, :)names}"
  fi
}

validate_selected_algorithms() {
  if (( ${#SELECTED_ALGORITHMS[@]} == 0 )); then
    return 0
  fi

  local -a unknown=()
  local algorithm=""
  for algorithm in "${SELECTED_ALGORITHMS[@]}"; do
    if [[ -z "${AVAILABLE_ALGORITHMS["$algorithm"]:-}" ]]; then
      unknown+=("$algorithm")
    fi
  done

  if (( ${#unknown[@]} > 0 )); then
    echo "error: unknown algorithm(s): ${(j:, :)unknown}" >&2
    echo "available algorithms: $(format_available_algorithms)" >&2
    exit 1
  fi
}

metadata_has_selected() {
  local meta_file="$1"
  if (( ${#SELECTED_ALGORITHMS[@]} == 0 )); then
    return 0
  fi

  local index=""
  local algorithm=""
  local base=""
  local experiment=""
  local topology=""
  local log_file=""
  while IFS=$'\t' read -r index algorithm base experiment topology log_file; do
    if [[ -n "${SELECTED_ALGORITHM_SET["$algorithm"]:-}" ]]; then
      return 0
    fi
  done < "$meta_file"
  return 1
}

make_filtered_cmd_file() {
  local cmd_file="$1"
  local meta_file="$2"
  local out=""

  if (( ${#SELECTED_ALGORITHMS[@]} == 0 )); then
    echo "$cmd_file"
    return 0
  fi

  if ! metadata_has_selected "$meta_file"; then
    return 1
  fi

  prepare_selected_filter_file
  ensure_filter_dir
  out="$FILTER_DIR/${cmd_file:t}"
  awk -F '\t' '
    NR == FNR { keep[$0] = 1; next }
    FILENAME == ARGV[2] {
      if ($2 in keep) {
        run[$1 + 1] = 1
      }
      next
    }
    FNR in run { print }
  ' "$SELECTED_FILTER_FILE" "$meta_file" "$cmd_file" > "$out"

  if [[ ! -s "$out" ]]; then
    rm -f "$out"
    return 1
  fi

  echo "$out"
}

is_slurm_array_script() {
  local script="$1"
  grep -q '^#SBATCH --array=' "$script"
}

selected_array_indices() {
  local meta_file="$1"
  local indices=""

  if (( ${#SELECTED_ALGORITHMS[@]} == 0 )); then
    echo ""
    return 0
  fi

  prepare_selected_filter_file
  indices=$(awk -F '\t' '
    NR == FNR { keep[$0] = 1; next }
    ($2 in keep) {
      if (indices != "") {
        indices = indices ","
      }
      indices = indices $1
    }
    END { print indices }
  ' "$SELECTED_FILTER_FILE" "$meta_file")
  echo "$indices"
}

slurm_array_limit() {
  local script="$1"
  local line=""
  local value=""
  line=$(grep -m1 '^#SBATCH --array=' "$script" || true)
  value="${line#*--array=}"
  if [[ "$value" == *%* ]]; then
    echo "${value##*%}"
  else
    echo ""
  fi
}

slurm_export_arg() {
  local cmd_file="${1:-}"
  local meta_file="${2:-}"
  local -a exports=("ALL")

  if [[ -n "$cmd_file" ]]; then
    exports+=("MKEXP2_CMD_FILE=$cmd_file")
  fi
  if [[ -n "$meta_file" ]]; then
    exports+=("MKEXP2_META_FILE=$meta_file")
  fi
  if [[ -n "$SELECTED_FILTER_FILE" ]]; then
    exports+=("MKEXP2_SUBMIT_ALGORITHMS_FILE=$SELECTED_FILTER_FILE")
  fi

  echo "--export=${(j:,:)exports}"
}

submit_install_slurm() {
  local script="$1"
  local out=""

  ensure_slurm_dir
  out=$(sbatch "$script")
  echo "$out"
  INSTALL_JOB_ID=$(echo "$out" | awk '{print $NF}')
}

submit_slurm() {
  local key="$1"
  local dep_key="$2"
  local script="$3"
  local cmd_file="$4"
  local meta_file="$5"
  local dep_arg=""
  local array_arg=""
  local export_arg=""
  local -a dep_ids=()

  if ! metadata_has_selected "$meta_file"; then
    echo "skipping $key (no selected algorithms)"
    return 0
  fi

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

  ensure_slurm_dir
  local out=""
  local -a sbatch_args=()
  if [[ -n "$dep_arg" ]]; then
    sbatch_args+=("$dep_arg")
  fi

  if (( ${#SELECTED_ALGORITHMS[@]} > 0 )); then
    prepare_selected_filter_file
    if is_slurm_array_script "$script"; then
      local indices=""
      local limit=""
      indices=$(selected_array_indices "$meta_file")
      if [[ -z "$indices" ]]; then
        echo "skipping $key (no selected array tasks)"
        return 0
      fi
      array_arg="--array=$indices"
      limit=$(slurm_array_limit "$script")
      if [[ -n "$limit" ]]; then
        array_arg+="%$limit"
      fi
      sbatch_args+=("$array_arg")
      export_arg=$(slurm_export_arg "" "$meta_file")
    else
      local filtered_cmd_file=""
      filtered_cmd_file=$(make_filtered_cmd_file "$cmd_file" "$meta_file") || {
        echo "skipping $key (no selected commands)"
        return 0
      }
      export_arg=$(slurm_export_arg "$filtered_cmd_file" "$meta_file")
    fi
    sbatch_args+=("$export_arg")
  fi

  if (( ${#sbatch_args[@]} > 0 )); then
    out=$(sbatch "${sbatch_args[@]}" "$script")
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
  local cmd_file="$2"
  local meta_file="$3"
  local filtered_cmd_file=""

  if ! metadata_has_selected "$meta_file"; then
    echo "skipping $script (no selected algorithms)"
    return 0
  fi

  filtered_cmd_file=$(make_filtered_cmd_file "$cmd_file" "$meta_file") || {
    echo "skipping $script (no selected commands)"
    return 0
  }

  if (( ${#SELECTED_ALGORITHMS[@]} > 0 )); then
    prepare_selected_filter_file
    MKEXP2_CMD_FILE="$filtered_cmd_file" \
      MKEXP2_META_FILE="$meta_file" \
      MKEXP2_SUBMIT_ALGORITHMS_FILE="$SELECTED_FILTER_FILE" \
      zsh "$script"
  else
    zsh "$script"
  fi
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

  ensure_slurm_dir
  if [[ -n "$dep_arg" ]]; then
    out=$(sbatch "$dep_arg" "$script")
  else
    out=$(sbatch "$script")
  fi

  echo "$out"
}

run_install_local() {
  local mkexp2_bin="$1"
  shift

  echo "==> Installing dependencies"
  "$mkexp2_bin" install "$@"
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
  timelimit=$(ResolveRunProperty "slurm.install.timelimit" "")

  install_job_name="mkexp2-install-$(SafeName "$(basename "$PWD")")"
  mkdir -p "$PWD/jobs" "$PWD/slurm"
  MKEXP2_SLURM_INSTALL_JOB_SCRIPT="$PWD/jobs/install__${MKEXP2_RUN_ID}.sh"
  install_cmd="$(ShellQuote "$MKEXP2_HOME/bin/mkexp2") install"
  if [[ -n "$MKEXP2_BUILD_MAX_CORES" ]]; then
    install_cmd+=" --build-max-cores $(ShellQuote "$MKEXP2_BUILD_MAX_CORES")"
  fi

  local install_log_file="$PWD/logs/install.md"

  cat > "$MKEXP2_SLURM_INSTALL_JOB_SCRIPT" <<SCRIPT
#!/usr/bin/env zsh
#SBATCH --job-name=${install_job_name}
#SBATCH --partition=${partition}
#SBATCH --output=slurm/slurm-%j.out
#SBATCH --error=slurm/slurm-%j.out
SCRIPT

  if [[ -n "$timelimit" ]]; then
    echo "#SBATCH --time=$timelimit" >> "$MKEXP2_SLURM_INSTALL_JOB_SCRIPT"
  fi

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
mkdir -p "$PWD/logs"

echo "[mkexp2] install job started"
echo "[mkexp2] install log: $install_log_file"

set +e
MKEXP2_INSTALL_LOG_FILE="$install_log_file" MKEXP2_RUN_VERBOSE=1 $install_cmd
install_exit_code=\$?
set -e

if (( install_exit_code != 0 )); then
  echo "[mkexp2] install failed, log: $install_log_file"
  tail -n 200 "$install_log_file"
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
  timelimit=$(ResolveRunProperty "parse.slurm.timelimit" "")

  parse_job_name="mkexp2-parse-$(SafeName "$(basename "$PWD")")"
  mkdir -p "$PWD/jobs" "$PWD/slurm"
  MKEXP2_SLURM_PARSE_JOB_SCRIPT="$PWD/jobs/parse__${MKEXP2_RUN_ID}.sh"
  parse_cmd="$(ShellQuote "$MKEXP2_HOME/bin/mkexp2") parse"

  local parse_log_dir="$PWD/logs/parse/slurm/$MKEXP2_RUN_ID"
  local parse_log_file="$parse_log_dir/parse.log"

  cat > "$MKEXP2_SLURM_PARSE_JOB_SCRIPT" <<SCRIPT
#!/usr/bin/env zsh
#SBATCH --job-name=${parse_job_name}
#SBATCH --partition=${partition}
#SBATCH --output=slurm/slurm-%j.out
#SBATCH --error=slurm/slurm-%j.out
SCRIPT

  if [[ -n "$timelimit" ]]; then
    echo "#SBATCH --time=$timelimit" >> "$MKEXP2_SLURM_PARSE_JOB_SCRIPT"
  fi

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
    EnsureSlurmInstallJob
    if [[ "$install_mode" == "job" ]]; then
      MKEXP2_SLURM_INSTALL_JOB_REQUIRED=1
    fi
  fi

  local wrap_fn="LauncherWrapCommand_${_system}"
  local write_fn="LauncherWriteJob_${_system}"
  if ! FunctionExists "$wrap_fn"; then
    EchoFatal "launcher ${_system} is missing $wrap_fn"
    exit 1
  fi
  if ! FunctionExists "$write_fn"; then
    EchoFatal "launcher ${_system} is missing $write_fn"
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

    local job_name="${experiment_label}__${topology}"
    local launcher_job_name="$job_name"
    if [[ "$_system" == "slurm" ]]; then
      local slurm_experiment_name=""
      slurm_experiment_name=$(SafeName "$experiment_display")
      launcher_job_name="${slurm_experiment_name}/${threads}"
    fi
    local job_key="${experiment_name}:${topology}"
    local cmd_file="$PWD/jobs/${job_name}.cmds"
    local meta_file="${cmd_file}.meta.tsv"
    local job_script="$PWD/jobs/${job_name}.sh"
    if [[ "$_system" == "slurm" ]]; then
      mkdir -p "$PWD/slurm"
    fi
    local dependency_key=""
    dependency_key=$(ResolveDependencyKey "$topology")
    local cmd_count=0
    local cmd_fd=-1
    local meta_fd=-1
    : > "$cmd_file"
    : > "$meta_file"
    exec {cmd_fd}> "$cmd_file"
    exec {meta_fd}> "$meta_file"

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
              local graph_name="${graph:t}"
              local instance_id="${graph_name}___k${k}_seed${seed}_eps${epsilon}_P${topology}"
              local log_file="$log_dir/${instance_id}.log"

              PARTITIONER_INVOKE_CMD=""
              MKEXP2_ACTIVE_ALGORITHM="$RUN_algorithm"
              "$invoke_fn" >/dev/null
              raw_cmd="$PARTITIONER_INVOKE_CMD"
              if [[ -z "$raw_cmd" ]]; then
                raw_cmd=$("$invoke_fn")
              fi
              MKEXP2_ACTIVE_ALGORITHM=""
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

              if [[ -n "$timeout_prefix" ]]; then
                wrapped_cmd="${timeout_prefix}${wrapped_cmd}"
              fi

              local cmd_index="$cmd_count"
              print -r -- "$wrapped_cmd >> \"$log_file\" 2>&1" >&$cmd_fd
              printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
                "$cmd_index" "$algorithm" "${ctx_base["$algorithm"]}" "$experiment_name" "$topology" "$log_file" >&$meta_fd
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
    exec {meta_fd}>&-

    if (( cmd_count == 0 )); then
      rm -f "$cmd_file" "$meta_file"
      continue
    fi
    generated_topologies+=("$topology")

    "$write_fn" "$job_script" "$cmd_file" "$launcher_job_name" "$nodes" "$mpis" "$threads" "$timelimit" "$cmd_count" "$meta_file"
    chmod +x "$job_script"
    GENERATED_JOB_META["$job_key"]="${_system}|$job_script|$dependency_key|$cmd_file|$meta_file"
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
  local key=""
  for key in "${GENERATED_JOB_KEYS[@]}"; do
    local entry="${GENERATED_JOB_META["$key"]:-}"
    if [[ -z "$entry" ]]; then
      continue
    fi

    local launcher=""
    local job_script=""
    local dep_key=""
    local cmd_file=""
    local meta_file=""

    IFS='|' read -r launcher job_script dep_key cmd_file meta_file <<< "$entry"
    if [[ "$launcher" == "local" ]]; then
      MKEXP2_LOCAL_HAS_RUN_JOBS=1
    elif [[ "$launcher" == "slurm" ]]; then
      MKEXP2_SLURM_HAS_RUN_JOBS=1
    fi
    printf 'register_meta %q\n' "$meta_file" >> "$PWD/submit.sh"
  done

  echo "validate_selected_algorithms" >> "$PWD/submit.sh"

  if (( MKEXP2_LOCAL_HAS_RUN_JOBS )); then
    {
      echo "if (( SUBMIT_INSTALL )); then"
      printf '  run_install_local %q' "$MKEXP2_HOME/bin/mkexp2"
      if [[ -n "$MKEXP2_BUILD_MAX_CORES" ]]; then
        printf ' --build-max-cores %q' "$MKEXP2_BUILD_MAX_CORES"
      fi
      printf '\n'
      echo "fi"
    } >> "$PWD/submit.sh"
  fi

  if [[ -n "$MKEXP2_SLURM_INSTALL_JOB_SCRIPT" ]]; then
    if (( MKEXP2_SLURM_INSTALL_JOB_REQUIRED )); then
      printf 'submit_install_slurm %q\n' "$MKEXP2_SLURM_INSTALL_JOB_SCRIPT" >> "$PWD/submit.sh"
    else
      {
        echo "if (( SUBMIT_INSTALL )); then"
        printf '  submit_install_slurm %q\n' "$MKEXP2_SLURM_INSTALL_JOB_SCRIPT"
        echo "fi"
      } >> "$PWD/submit.sh"
    fi
    EchoStep "Generated Slurm install job: $MKEXP2_SLURM_INSTALL_JOB_SCRIPT"
  fi

  for key in "${GENERATED_JOB_KEYS[@]}"; do
    local entry="${GENERATED_JOB_META["$key"]:-}"
    if [[ -z "$entry" ]]; then
      continue
    fi

    local launcher=""
    local job_script=""
    local dep_key=""
    local cmd_file=""
    local meta_file=""

    IFS='|' read -r launcher job_script dep_key cmd_file meta_file <<< "$entry"
    if [[ "$launcher" == "slurm" ]]; then
      printf 'submit_slurm %q %q %q %q %q\n' "$key" "$dep_key" "$job_script" "$cmd_file" "$meta_file" >> "$PWD/submit.sh"
    else
      printf 'submit_local %q %q %q\n' "$job_script" "$cmd_file" "$meta_file" >> "$PWD/submit.sh"
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
