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
  timelimit=$(ResolveRunProperty "slurm.install.timelimit" "")

  install_job_name="mkexp2-install-$(SafeName "$(basename "$PWD")")"
  MKEXP2_SLURM_INSTALL_JOB_SCRIPT="$PWD/jobs/install__${MKEXP2_RUN_ID}.sh"
  install_cmd="$(ShellQuote "$MKEXP2_HOME/bin/mkexp2") install"
  if [[ -n "$MKEXP2_BUILD_MAX_CORES" ]]; then
    install_cmd+=" --build-max-cores $(ShellQuote "$MKEXP2_BUILD_MAX_CORES")"
  fi

  local install_log_dir="$PWD/logs/install"
  local run_log_file="$install_log_dir/${MKEXP2_RUN_ID}-install.log"

  cat > "$MKEXP2_SLURM_INSTALL_JOB_SCRIPT" <<SCRIPT
#!/usr/bin/env zsh
#SBATCH --job-name=${install_job_name}
#SBATCH --partition=${partition}
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
mkdir -p "$install_log_dir"

echo "[mkexp2] install job started"
echo "[mkexp2] install log: $run_log_file"

set +e
MKEXP2_INSTALL_LOG_DIR="$install_log_dir" MKEXP2_RUN_VERBOSE=1 $install_cmd > "$run_log_file" 2>&1
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
  timelimit=$(ResolveRunProperty "parse.slurm.timelimit" "")

  parse_job_name="mkexp2-parse-$(SafeName "$(basename "$PWD")")"
  MKEXP2_SLURM_PARSE_JOB_SCRIPT="$PWD/jobs/parse__${MKEXP2_RUN_ID}.sh"
  parse_cmd="$(ShellQuote "$MKEXP2_HOME/bin/mkexp2") parse"

  local parse_log_dir="$PWD/logs/parse/slurm/$MKEXP2_RUN_ID"
  local parse_log_file="$parse_log_dir/parse.log"

  cat > "$MKEXP2_SLURM_PARSE_JOB_SCRIPT" <<SCRIPT
#!/usr/bin/env zsh
#SBATCH --job-name=${parse_job_name}
#SBATCH --partition=${partition}
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
  ExpandCurrentExperiment "$experiment_name" "generate"

  if [[ "$_system" == "slurm" && "$EXPAND_SLURM_INSTALL_MODE" == "job" ]]; then
    MKEXP2_SLURM_INSTALL_JOB_REQUIRED=1
    EnsureSlurmInstallJob
  fi

  local job_key=""
  for job_key in "${EXPAND_JOB_KEYS[@]}"; do
    : > "${EXPAND_JOB["$job_key::cmd_file"]}"
  done

  local call_id=""
  for call_id in "${EXPAND_CALL_IDS[@]}"; do
    mkdir -p "$(dirname "${EXPAND_CALL["$call_id::log_file"]}")"
    job_key="${EXPAND_CALL["$call_id::job_key"]}"
    print -r -- "${EXPAND_CALL["$call_id::final_command"]} >> \"${EXPAND_CALL["$call_id::log_file"]}\" 2>&1" >> "${EXPAND_JOB["$job_key::cmd_file"]}"
  done

  local write_fn="LauncherWriteJob_${_system}"
  if ! FunctionExists "$write_fn"; then
    EchoFatal "launcher ${_system} is missing $write_fn"
    exit 1
  fi

  for job_key in "${EXPAND_JOB_KEYS[@]}"; do
    "$write_fn" \
      "${EXPAND_JOB["$job_key::job_script"]}" \
      "${EXPAND_JOB["$job_key::cmd_file"]}" \
      "${EXPAND_JOB["$job_key::launcher_job_name"]}" \
      "${EXPAND_JOB["$job_key::nodes"]}" \
      "${EXPAND_JOB["$job_key::mpis"]}" \
      "${EXPAND_JOB["$job_key::threads"]}" \
      "$EXPAND_TIMELIMIT" \
      "${EXPAND_JOB["$job_key::cmd_count"]}"
    chmod +x "${EXPAND_JOB["$job_key::job_script"]}"
    GENERATED_JOB_META["$job_key"]="${_system}|${EXPAND_JOB["$job_key::job_script"]}|${EXPAND_JOB["$job_key::dependency_key"]}"
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

  algorithms_summary=$(_GenerateFormatList 5 "${EXPAND_ALGORITHM_LABELS[@]}")
  partitioners_summary=$(_GenerateFormatList 6 "${EXPAND_PARTITIONERS[@]}")
  graphs_summary=$(_GenerateFormatList 8 "${EXPAND_GRAPH_NAMES[@]}")
  ks_summary=$(_GenerateFormatList 8 "${_ks[@]}")
  seeds_summary=$(_GenerateFormatList 8 "${_seeds[@]}")
  epsilons_summary=$(_GenerateFormatList 8 "${_epsilons[@]}")
  topologies_summary=$(_GenerateFormatList 8 "${EXPAND_GENERATED_TOPOLOGIES[@]}")

  local algorithm=""
  for algorithm in "${_algorithms[@]}"; do
    calls_per_algorithm_parts+=("$algorithm=${EXPAND_CALL_COUNT_BY_ALGORITHM["$algorithm"]:-0}")
  done
  local topology=""
  for topology in "${EXPAND_GENERATED_TOPOLOGIES[@]}"; do
    calls_per_topology_parts+=("$topology=${EXPAND_CALL_COUNT_BY_TOPOLOGY["$topology"]:-0}")
  done

  EchoStep "Generated experiment summary: $EXPAND_EXPERIMENT_DISPLAY"
  _GenerateSummaryDivider
  _GenerateInfoKV "launcher" "$_system"
  _GenerateInfoKV "calls" "$EXPAND_TOTAL_CALLS total (${#EXPAND_GENERATED_TOPOLOGIES[@]} job script(s))"
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
