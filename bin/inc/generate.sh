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
typeset -A AVAILABLE_EXPERIMENT_ALGORITHMS=()
typeset -A SELECTED_ALGORITHM_SET=()
typeset -A SELECTED_EXPERIMENT_ALGORITHM_SET=()
typeset -a SELECTED_ALGORITHMS=()
typeset -a SELECTED_EXPERIMENT_SELECTIONS=()
typeset -a REGISTERED_META_FILES=()
SUBMIT_INSTALL=0
INSTALL_JOB_ID=""
PARSE_JOB_ID=""
SUBMIT_DIR="${0:A:h}"
FILTER_DIR=""
SELECTED_FILTER_FILE=""
SELECTION_INPUT_FILE=""
SUBMIT_LOCK_FILE=""
SUBMIT_LOCK_ACQUIRED=0
SUBMIT_LOCK_KEEP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install)
      SUBMIT_INSTALL=1
      shift
      ;;
    --selection-file)
      if [[ $# -lt 2 ]]; then
        echo "error: --selection-file requires a TSV file" >&2
        exit 1
      fi
      SELECTION_INPUT_FILE="$2"
      shift 2
      ;;
    --select)
      if [[ $# -lt 2 ]]; then
        echo "error: --select requires <experiment>:<algorithm>" >&2
        exit 1
      fi
      SELECTED_EXPERIMENT_SELECTIONS+=("$2")
      shift 2
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
SUBMIT_LOCK_FILE="$SUBMIT_DIR/.mkexp2/submit.lock"

clear_submit_lock() {
  if [[ -n "$SUBMIT_LOCK_FILE" ]]; then
    rm -f "$SUBMIT_LOCK_FILE"
  fi
}

append_submit_lock_line() {
  local key="$1"
  local value="$2"
  if (( SUBMIT_LOCK_ACQUIRED )) && [[ -n "$SUBMIT_LOCK_FILE" ]]; then
    printf '%s=%s\n' "$key" "$value" >> "$SUBMIT_LOCK_FILE"
  fi
}

record_submit_job() {
  local kind="$1"
  local job_id="$2"
  if [[ -n "$job_id" ]]; then
    append_submit_lock_line "slurm_job_id" "$job_id"
    append_submit_lock_line "slurm_job" "$kind:$job_id"
  fi
}

submit_lock_exit_cleanup() {
  if (( SUBMIT_LOCK_ACQUIRED && ! SUBMIT_LOCK_KEEP )); then
    clear_submit_lock
  fi
}

trap submit_lock_exit_cleanup EXIT

selection_active() {
  (( ${#SELECTED_ALGORITHMS[@]} > 0 || ${#SELECTED_EXPERIMENT_ALGORITHM_SET[@]} > 0 ))
}

add_selected_experiment_algorithm() {
  local experiment="$1"
  local algorithm="$2"
  local key=""

  if [[ -z "$experiment" || -z "$algorithm" ]]; then
    echo "error: invalid experiment algorithm selection: experiment='$experiment' algorithm='$algorithm'" >&2
    exit 1
  fi

  key="${experiment}:${algorithm}"
  SELECTED_EXPERIMENT_ALGORITHM_SET[$key]=1
}

add_selected_experiment_token() {
  local token="$1"
  local experiment=""
  local algorithm=""

  if [[ "$token" != *:* ]]; then
    echo "error: invalid --select token '$token' (expected <experiment>:<algorithm>)" >&2
    exit 1
  fi
  experiment="${token%%:*}"
  algorithm="${token#*:}"
  add_selected_experiment_algorithm "$experiment" "$algorithm"
}

load_selected_selection_file() {
  local line=""
  local experiment=""
  local algorithm=""

  for line in "${SELECTED_EXPERIMENT_SELECTIONS[@]}"; do
    add_selected_experiment_token "$line"
  done

  if [[ -z "$SELECTION_INPUT_FILE" ]]; then
    return 0
  fi
  if [[ ! -f "$SELECTION_INPUT_FILE" ]]; then
    echo "error: selection file not found: $SELECTION_INPUT_FILE" >&2
    exit 1
  fi

  while IFS=$'\t' read -r experiment algorithm _rest; do
    [[ -n "$experiment" ]] || continue
    if [[ -z "$algorithm" ]]; then
      SELECTED_ALGORITHMS+=("$experiment")
      SELECTED_ALGORITHM_SET[$experiment]=1
    else
      add_selected_experiment_algorithm "$experiment" "$algorithm"
    fi
  done < "$SELECTION_INPUT_FILE"
}

selected_summary() {
  local -a items=()
  local algorithm=""
  local key=""
  local experiment=""

  for algorithm in "${SELECTED_ALGORITHMS[@]}"; do
    items+=("$algorithm")
  done
  for key in ${(ko)SELECTED_EXPERIMENT_ALGORITHM_SET}; do
    local pair_algorithm=""
    experiment="${key%%:*}"
    pair_algorithm="${key#*:}"
    items+=("$experiment:$pair_algorithm")
  done

  if (( ${#items[@]} == 0 )); then
    echo "all"
  else
    echo "${(j:, :)items}"
  fi
}

acquire_submit_lock() {
  mkdir -p "${SUBMIT_LOCK_FILE:h}"
  if [[ -e "$SUBMIT_LOCK_FILE" ]]; then
    echo "error: submit lock exists: $SUBMIT_LOCK_FILE" >&2
    echo "another submission may still be running; remove the lock only if that run is gone" >&2
    exit 1
  fi
  if ! (
    set -o noclobber
    {
      printf 'started_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      printf 'pid=%s\n' "$$"
      printf 'cwd=%s\n' "$SUBMIT_DIR"
      if selection_active; then
        printf 'algorithms=%s\n' "$(selected_summary)"
      else
        printf 'algorithms=all\n'
      fi
    } > "$SUBMIT_LOCK_FILE"
  ) 2>/dev/null; then
    echo "error: failed to create submit lock: $SUBMIT_LOCK_FILE" >&2
    exit 1
  fi
  SUBMIT_LOCK_ACQUIRED=1
}

for algorithm in "${SELECTED_ALGORITHMS[@]}"; do
  SELECTED_ALGORITHM_SET[$algorithm]=1
done
load_selected_selection_file

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
  if ! selection_active; then
    return 0
  fi
  ensure_filter_dir
  if [[ -z "$SELECTED_FILTER_FILE" ]]; then
    SELECTED_FILTER_FILE="$FILTER_DIR/selection.tsv"
    : > "$SELECTED_FILTER_FILE"
    local algorithm=""
    local key=""
    for algorithm in "${SELECTED_ALGORITHMS[@]}"; do
      print -r -- "$algorithm" >> "$SELECTED_FILTER_FILE"
    done
    for key in ${(ko)SELECTED_EXPERIMENT_ALGORITHM_SET}; do
      local experiment="${key%%:*}"
      local pair_algorithm="${key#*:}"
      printf '%s\t%s\n' "$experiment" "$pair_algorithm" >> "$SELECTED_FILTER_FILE"
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
    AVAILABLE_ALGORITHMS[$algorithm]=1
    if [[ -n "$experiment" ]]; then
      local pair_key="${experiment}:${algorithm}"
      AVAILABLE_EXPERIMENT_ALGORITHMS[$pair_key]=1
    fi
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

format_available_experiment_algorithms() {
  local -a items=()
  local key=""
  local experiment=""
  local algorithm=""

  for key in ${(ko)AVAILABLE_EXPERIMENT_ALGORITHMS}; do
    experiment="${key%%:*}"
    algorithm="${key#*:}"
    items+=("$experiment:$algorithm")
  done

  if (( ${#items[@]} == 0 )); then
    echo "(none)"
  else
    echo "${(j:, :)items}"
  fi
}

validate_selected_algorithms() {
  if ! selection_active; then
    return 0
  fi

  local -a unknown=()
  local -a unknown_pairs=()
  local algorithm=""
  local key=""
  local experiment=""
  for algorithm in "${SELECTED_ALGORITHMS[@]}"; do
    if [[ -z "${AVAILABLE_ALGORITHMS[$algorithm]:-}" ]]; then
      unknown+=("$algorithm")
    fi
  done
  for key in ${(k)SELECTED_EXPERIMENT_ALGORITHM_SET}; do
    if [[ -z "${AVAILABLE_EXPERIMENT_ALGORITHMS[$key]:-}" ]]; then
      local pair_algorithm=""
      experiment="${key%%:*}"
      pair_algorithm="${key#*:}"
      unknown_pairs+=("$experiment:$pair_algorithm")
    fi
  done

  if (( ${#unknown[@]} > 0 || ${#unknown_pairs[@]} > 0 )); then
    if (( ${#unknown[@]} > 0 )); then
      echo "error: unknown algorithm(s): ${(j:, :)unknown}" >&2
    fi
    if (( ${#unknown_pairs[@]} > 0 )); then
      echo "error: unknown experiment/algorithm selection(s): ${(j:, :)unknown_pairs}" >&2
    fi
    echo "available algorithms: $(format_available_algorithms)" >&2
    echo "available experiment/algorithm selections: $(format_available_experiment_algorithms)" >&2
    exit 1
  fi
}

metadata_row_selected() {
  local experiment="$1"
  local algorithm="$2"
  local key="${experiment}:${algorithm}"

  if ! selection_active; then
    return 0
  fi
  if [[ -n "${SELECTED_ALGORITHM_SET[$algorithm]:-}" ]]; then
    return 0
  fi
  if [[ -n "${SELECTED_EXPERIMENT_ALGORITHM_SET[$key]:-}" ]]; then
    return 0
  fi
  return 1
}

metadata_has_selected() {
  local meta_file="$1"
  if ! selection_active; then
    return 0
  fi

  local index=""
  local algorithm=""
  local base=""
  local experiment=""
  local topology=""
  local log_file=""
  while IFS=$'\t' read -r index algorithm base experiment topology log_file; do
    if metadata_row_selected "$experiment" "$algorithm"; then
      return 0
    fi
  done < "$meta_file"
  return 1
}

make_filtered_cmd_file() {
  local cmd_file="$1"
  local meta_file="$2"
  local out=""

  if ! selection_active; then
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
    NR == FNR {
      if (NF >= 2 && $2 != "") {
        keep_pair[$1 SUBSEP $2] = 1
      } else if ($1 != "") {
        keep_algorithm[$1] = 1
      }
      next
    }
    FILENAME == ARGV[2] {
      if (($2 in keep_algorithm) || (($4 SUBSEP $2) in keep_pair)) {
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

  if ! selection_active; then
    echo ""
    return 0
  fi

  prepare_selected_filter_file
  indices=$(awk -F '\t' '
    NR == FNR {
      if (NF >= 2 && $2 != "") {
        keep_pair[$1 SUBSEP $2] = 1
      } else if ($1 != "") {
        keep_algorithm[$1] = 1
      }
      next
    }
    (($2 in keep_algorithm) || (($4 SUBSEP $2) in keep_pair)) {
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
  if [[ -n "$SELECTED_FILTER_FILE" && -z "$cmd_file" ]]; then
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
  record_submit_job "install" "$INSTALL_JOB_ID"
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

  if selection_active; then
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
  record_submit_job "$key" "$id"
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

  if selection_active; then
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
  PARSE_JOB_ID=$(echo "$out" | awk '{print $NF}')
  record_submit_job "parse" "$PARSE_JOB_ID"
}

submit_cleanup_slurm() {
  local -a dep_ids=()
  local id=""
  local dep_arg=""
  local cleanup_script=""
  local out=""
  local postprocess_command=""
  local postprocess_helper=""
  local postprocess_mkexp2=""

  if [[ -n "$PARSE_JOB_ID" ]]; then
    dep_ids+=("$PARSE_JOB_ID")
  else
    if [[ -n "$INSTALL_JOB_ID" ]]; then
      dep_ids+=("$INSTALL_JOB_ID")
    fi
    for id in "${(@v)JOB_IDS}"; do
      [[ -n "$id" ]] || continue
      dep_ids+=("$id")
    done
  fi

  if (( ${#dep_ids[@]} == 0 )); then
    clear_submit_lock
    return 0
  fi

  mkdir -p "$SUBMIT_DIR/.mkexp2" "$SUBMIT_DIR/slurm"
  cleanup_script="$SUBMIT_DIR/.mkexp2/submit-lock-cleanup-$(date +%Y%m%d-%H%M%S)-$$.sh"
  if (( ${MKEXP2_POSTPROCESS_AUTO_REQUIRED:-0} )); then
    local postprocess_log_dir="$SUBMIT_DIR/logs/postprocess"
    local postprocess_log_file="$postprocess_log_dir/${MKEXP2_RUN_ID}.log"
    postprocess_helper="$MKEXP2_HOME/bin/mkexp2_postprocess.py"
    postprocess_mkexp2="$MKEXP2_HOME/bin/mkexp2"
    postprocess_command=$(
      printf '%s\n' \
        "mkdir -p ${(qqq)postprocess_log_dir}" \
        "echo \"[mkexp2] postprocess log: ${(qqq)postprocess_log_file}\"" \
        "set +e" \
        "python3 ${(qqq)postprocess_helper} --mkexp2 ${(qqq)postprocess_mkexp2} --experiment-dir ${(qqq)SUBMIT_DIR} --run-id ${(qqq)MKEXP2_RUN_ID} > ${(qqq)postprocess_log_file} 2>&1" \
        "postprocess_exit_code=\$?" \
        "set -e" \
        "if (( postprocess_exit_code != 0 )); then" \
        "  echo \"[mkexp2] postprocess failed, log: ${(qqq)postprocess_log_file}\"" \
        "  tail -n 200 ${(qqq)postprocess_log_file}" \
        "fi"
    )
  fi
  cat > "$cleanup_script" <<CLEANUP
#!/usr/bin/env zsh
set -euo pipefail
$postprocess_command
rm -f ${(qqq)SUBMIT_LOCK_FILE}
CLEANUP
  chmod +x "$cleanup_script"

  dep_arg="--dependency=afterany:${(j/:/)dep_ids}"
  out=$(sbatch "$dep_arg" "$cleanup_script")
  echo "$out"
  record_submit_job "cleanup" "$(echo "$out" | awk '{print $NF}')"
  SUBMIT_LOCK_KEEP=1
}

run_install_local() {
  local mkexp2_bin="$1"
  shift

  echo "==> Installing dependencies"
  "$mkexp2_bin" install "$@"
}

run_postprocess_local() {
  local mkexp2_home="$1"
  local run_id="$2"
  local helper="$mkexp2_home/bin/mkexp2_postprocess.py"
  local log_dir="$SUBMIT_DIR/logs/postprocess"
  local log_file="$log_dir/${run_id}.log"

  if [[ ! -f "$helper" ]]; then
    echo "warning: postprocess helper not found: $helper" >&2
    return 0
  fi

  mkdir -p "$log_dir"
  echo "==> Postprocessing results"
  echo "[mkexp2] postprocess log: $log_file"
  set +e
  python3 "$helper" --mkexp2 "$mkexp2_home/bin/mkexp2" --experiment-dir "$SUBMIT_DIR" --run-id "$run_id" > "$log_file" 2>&1
  local postprocess_exit_code=$?
  set -e
  if (( postprocess_exit_code != 0 )); then
    echo "warning: postprocess failed, log: $log_file" >&2
    tail -n 80 "$log_file" >&2
  fi
  return 0
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
if [[ -z "\${MKEXP2_SLURM_INSTALL_LOGIN_ENV:-}" ]]; then
  export MKEXP2_SLURM_INSTALL_LOGIN_ENV=1
  exec zsh -lic "zsh ${(qqq)MKEXP2_SLURM_INSTALL_JOB_SCRIPT}"
fi

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
              local graph_name="${graph:t}"
              local instance_id="${graph_name}___k${k}_seed${seed}_eps${epsilon}_P${topology}"
              local log_file="$log_dir/${instance_id}.log"

              RUN_algorithm="$algorithm"
              RUN_base="${ctx_base["$algorithm"]}"
              RUN_binary_path="${ctx_binary_path["$algorithm"]}"
              RUN_graph="$graph"
              RUN_k="$k"
              RUN_epsilon="$epsilon"
              RUN_seed="$seed"
              RUN_topology="$topology"
              RUN_nodes="$nodes"
              RUN_mpis="$mpis"
              RUN_threads="$threads"
              RUN_instance_id="$instance_id"
              RUN_log_file="$log_file"
              ResolveRunArgPlaceholders "${ctx_args["$algorithm"]}"

              local raw_cmd=""
              local wrapped_cmd=""

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
  echo "acquire_submit_lock" >> "$PWD/submit.sh"
  printf 'MKEXP2_HOME=%q\n' "$MKEXP2_HOME" >> "$PWD/submit.sh"
  printf 'MKEXP2_RUN_ID=%q\n' "$MKEXP2_RUN_ID" >> "$PWD/submit.sh"
  printf 'MKEXP2_POSTPROCESS_AUTO_REQUIRED=%d\n' "$MKEXP2_POSTPROCESS_AUTO_REQUIRED" >> "$PWD/submit.sh"
  if (( MKEXP2_LOCAL_HAS_RUN_JOBS && MKEXP2_SLURM_HAS_RUN_JOBS )); then
    echo "append_submit_lock_line system mixed" >> "$PWD/submit.sh"
  elif (( MKEXP2_SLURM_HAS_RUN_JOBS )); then
    echo "append_submit_lock_line system slurm" >> "$PWD/submit.sh"
  elif (( MKEXP2_LOCAL_HAS_RUN_JOBS )); then
    echo "append_submit_lock_line system local" >> "$PWD/submit.sh"
  else
    echo "append_submit_lock_line system unknown" >> "$PWD/submit.sh"
  fi

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

  if (( MKEXP2_POSTPROCESS_AUTO_REQUIRED && MKEXP2_LOCAL_HAS_RUN_JOBS && ! MKEXP2_SLURM_HAS_RUN_JOBS )); then
    printf 'run_postprocess_local %q %q\n' "$MKEXP2_HOME" "$MKEXP2_RUN_ID" >> "$PWD/submit.sh"
    EchoStep "Enabled auto-postprocess in submit script"
  fi

  if (( MKEXP2_SLURM_HAS_RUN_JOBS )); then
    echo "submit_cleanup_slurm" >> "$PWD/submit.sh"
  fi

  EchoStep "Generated submit script: $PWD/submit.sh"
}
