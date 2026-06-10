#!/usr/bin/env zsh

MKEXP2_UI_READY=0
MKEXP2_UI_RESET=""
MKEXP2_UI_BOLD=""
MKEXP2_UI_DIM=""
MKEXP2_UI_BLUE=""
MKEXP2_UI_GREEN=""
MKEXP2_UI_YELLOW=""
MKEXP2_UI_RED=""
MKEXP2_UI_CYAN=""
MKEXP2_UI_TAG=""

InitUi() {
  if (( MKEXP2_UI_READY )); then
    return
  fi

  if [[ -t 1 && "${TERM:-}" != "dumb" && -z "${NO_COLOR:-}" ]]; then
    MKEXP2_UI_RESET=$'\033[0m'
    MKEXP2_UI_BOLD=$'\033[1m'
    MKEXP2_UI_DIM=$'\033[2m'
    MKEXP2_UI_BLUE=$'\033[34m'
    MKEXP2_UI_GREEN=$'\033[32m'
    MKEXP2_UI_YELLOW=$'\033[33m'
    MKEXP2_UI_RED=$'\033[31m'
    MKEXP2_UI_CYAN=$'\033[36m'
  fi

  MKEXP2_UI_READY=1
}

_UiTag() {
  local kind="$1"
  InitUi
  case "$kind" in
    step) MKEXP2_UI_TAG="${MKEXP2_UI_CYAN}==>${MKEXP2_UI_RESET}" ;;
    exp) MKEXP2_UI_TAG="${MKEXP2_UI_CYAN}==>${MKEXP2_UI_RESET}" ;;
    info) MKEXP2_UI_TAG="[info]" ;;
    ok) MKEXP2_UI_TAG="${MKEXP2_UI_GREEN}[ok]${MKEXP2_UI_RESET}" ;;
    warn) MKEXP2_UI_TAG="${MKEXP2_UI_YELLOW}${MKEXP2_UI_BOLD}[warn]${MKEXP2_UI_RESET}" ;;
    fail) MKEXP2_UI_TAG="${MKEXP2_UI_RED}${MKEXP2_UI_BOLD}[fail]${MKEXP2_UI_RESET}" ;;
    fatal) MKEXP2_UI_TAG="${MKEXP2_UI_RED}${MKEXP2_UI_BOLD}[fatal]${MKEXP2_UI_RESET}" ;;
    build) MKEXP2_UI_TAG="${MKEXP2_UI_BLUE}${MKEXP2_UI_BOLD}[build]${MKEXP2_UI_RESET}" ;;
    run) MKEXP2_UI_TAG="${MKEXP2_UI_CYAN}[run]${MKEXP2_UI_RESET}" ;;
    skip) MKEXP2_UI_TAG="${MKEXP2_UI_DIM}${MKEXP2_UI_BOLD}[skip]${MKEXP2_UI_RESET}" ;;
    *) MKEXP2_UI_TAG="[${kind}]" ;;
  esac
}

EchoInfo() {
  _UiTag info
  echo "  $MKEXP2_UI_TAG $*"
}

EchoStep() {
  _UiTag step
  echo "$MKEXP2_UI_TAG $*"
}

EchoWarn() {
  _UiTag warn
  echo "$MKEXP2_UI_TAG $*" >&2
}

EchoFatal() {
  _UiTag fatal
  echo "$MKEXP2_UI_TAG $*" >&2
}

EchoExperiment() {
  local name="$1"
  InitUi
  _UiTag exp
  echo "$MKEXP2_UI_TAG ${MKEXP2_UI_BOLD}${name}${MKEXP2_UI_RESET}"
}

DisplayExperimentName() {
  local fn_name="$1"
  local display_name="${fn_name#Experiment}"
  if [[ -z "$display_name" || "$display_name" == "$fn_name" ]]; then
    display_name="$fn_name"
  fi
  display_name="${display_name//_/ }"
  echo "$display_name"
}

PrepareInstallLogFile() {
  if [[ -z "$MKEXP2_INSTALL_LOG_FILE" ]]; then
    if [[ -n "$MKEXP2_INSTALL_LOG_DIR" ]]; then
      MKEXP2_INSTALL_LOG_FILE="$MKEXP2_INSTALL_LOG_DIR/install.md"
    else
      MKEXP2_INSTALL_LOG_FILE="$PWD/logs/install.md"
    fi
  fi

  mkdir -p "$(dirname "$MKEXP2_INSTALL_LOG_FILE")"

  if [[ "$MKEXP2_INSTALL_LOG_INITIALIZED" == "$MKEXP2_INSTALL_LOG_FILE" ]]; then
    return
  fi

  if [[ -f "$MKEXP2_INSTALL_LOG_FILE" ]]; then
    _RepairInstallLogFence "$MKEXP2_INSTALL_LOG_FILE"
    {
      echo
      echo "---"
      echo
      echo "# mkexp2 install log"
      echo
      echo "- Resumed: $(date '+%Y-%m-%d %H:%M:%S %Z')"
      echo "- Directory: \`$PWD\`"
      echo
    } >> "$MKEXP2_INSTALL_LOG_FILE"
  else
    {
      echo "# mkexp2 install log"
      echo
      echo "- Started: $(date '+%Y-%m-%d %H:%M:%S %Z')"
      echo "- Directory: \`$PWD\`"
      echo
    } > "$MKEXP2_INSTALL_LOG_FILE"
  fi
  _AppendInstallMachineInfo

  MKEXP2_INSTALL_LOG_INITIALIZED="$MKEXP2_INSTALL_LOG_FILE"
}

_InstallLogFenceOpen() {
  local log_file="$1"
  awk '
    /^```/ { open = !open }
    END { exit(open ? 0 : 1) }
  ' "$log_file"
}

_RepairInstallLogFence() {
  local log_file="$1"
  [[ -f "$log_file" ]] || return 0
  if ! _InstallLogFenceOpen "$log_file"; then
    return 0
  fi

  {
    echo
    echo '```'
    echo
    echo "> Previous install log entry was interrupted before mkexp2 could write its footer."
  } >> "$log_file"
}

_AppendInstallProbeCommand() {
  local command_line="$1"
  local first_word="${command_line%% *}"

  if ! command -v "$first_word" >/dev/null 2>&1; then
    return
  fi
  if [[ "$first_word" == "sysctl" ]] && ! zsh -c "$command_line" >/dev/null 2>&1; then
    return
  fi

  {
    printf '### `%s`\n' "$command_line"
    echo
    echo '```console'
    printf '$ %s\n' "$command_line"
  } >> "$MKEXP2_INSTALL_LOG_FILE"

  set +e
  zsh -c "$command_line" 2>&1 | _StripInstallLogAnsi >> "$MKEXP2_INSTALL_LOG_FILE"
  local rc=${pipestatus[1]}
  set -e

  {
    echo '```'
    if (( rc != 0 )); then
      echo
      echo "- Exit code: $rc"
    fi
    echo
  } >> "$MKEXP2_INSTALL_LOG_FILE"
}

_AppendInstallMachineInfo() {
  {
    echo "## Machine info"
    echo
    printf -- '- Hostname: `%s`\n' "$(_CleanInstallLogText "$(hostname 2>/dev/null || echo unknown)")"
    printf -- '- Working directory: `%s`\n' "$PWD"
    printf -- '- mkexp2 PID: `%s`\n' "$$"
    echo
  } >> "$MKEXP2_INSTALL_LOG_FILE"

  _AppendInstallProbeCommand "uname -a"
  _AppendInstallProbeCommand "lscpu"
  _AppendInstallProbeCommand "lsmem"
  _AppendInstallProbeCommand "free -h"
  _AppendInstallProbeCommand "nproc"
  _AppendInstallProbeCommand "sysctl -n machdep.cpu.brand_string hw.ncpu hw.memsize"
  _AppendInstallProbeCommand "vm_stat"
}

_AppendInstallCommandHeader() {
  local cmd_display="$1"
  local title="$2"

  {
    echo
    printf '## `%s`\n' "$title"
    echo
    echo "- Started: $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo
    echo '```console'
    printf '$ %s\n' "$cmd_display"
  } >> "$MKEXP2_INSTALL_LOG_FILE"
}

_AppendInstallCommandFooter() {
  local exit_code="$1"

  {
    echo
    echo '```'
    echo
    echo "- Finished: $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo "- Exit code: $exit_code"
  } >> "$MKEXP2_INSTALL_LOG_FILE"
}

_StripInstallLogAnsi() {
  if command -v perl >/dev/null 2>&1; then
    perl -pe 's/\e\[[0-?]*[ -\/]*[@-~]//g; s/\e\][^\a]*(?:\a|\e\\)//g; s/\e[P^_].*?\e\\//g; s/\e[@-_]//g'
    return
  fi

  local esc=$'\033'
  sed -E "s/${esc}\\[[0-?]*[ -\\/]*[@-~]//g; s/${esc}[@-_]//g"
}

_CleanInstallLogText() {
  local text="$1"
  printf '%s' "$text" | _StripInstallLogAnsi
}

_RunWithSpinner() {
  local label="$1"
  local log_file="$2"
  shift 2

  local exit_code=0
  local -a spinner=('|' '/' '-' "\\")
  local idx=1
  local tmp_file=""
  if ! tmp_file=$(mktemp "${TMPDIR:-/tmp}/mkexp2-run.XXXXXX"); then
    EchoWarn "could not create temporary install log file"
    return 1
  fi

  if [[ -t 1 ]]; then
    set +e
    "$@" >> "$tmp_file" 2>&1 &
    local pid=$!
    while kill -0 "$pid" 2>/dev/null; do
      local spinner_label="$label"
      local cols="${COLUMNS:-0}"
      if [[ "$cols" == <-> ]] && (( cols > 12 )); then
        # Keep the live spinner status on one terminal line; full command is still
        # printed after completion in [ok]/[fail] output.
        local max_label_len=$((cols - 6))
        if (( ${#spinner_label} > max_label_len )); then
          spinner_label="${spinner_label[1,$((max_label_len - 3))]}..."
        fi
      fi
      InitUi
      printf "\r\033[K  %s[%s]%s %s" "${MKEXP2_UI_CYAN}${MKEXP2_UI_BOLD}" "${spinner[$idx]}" "$MKEXP2_UI_RESET" "$spinner_label"
      idx=$((idx + 1))
      if (( idx > ${#spinner[@]} )); then
        idx=1
      fi
      sleep 0.1
    done
    wait "$pid"
    exit_code=$?
    set -e
    printf "\r\033[K"
  else
    set +e
    "$@" >> "$tmp_file" 2>&1
    exit_code=$?
    set -e
  fi

  _StripInstallLogAnsi < "$tmp_file" >> "$log_file"
  rm -f "$tmp_file"

  if (( exit_code == 0 )); then
    _UiTag ok
    echo "  $MKEXP2_UI_TAG $label"
    return 0
  fi

  _UiTag fail
  echo "  $MKEXP2_UI_TAG $label"
  return "$exit_code"
}

Run() {
  local -a cmd=("$@")
  local cmd_display="${(j: :)cmd}"
  cmd_display="$(_CleanInstallLogText "$cmd_display")"
  local label="${cmd_display:-command}"
  local title="${cmd[1]:-command}"
  title="$(_CleanInstallLogText "$title")"
  [[ -n "$title" ]] || title="command"

  PrepareInstallLogFile
  _AppendInstallCommandHeader "$cmd_display" "$title"

  if (( MKEXP2_RUN_VERBOSE )); then
    _UiTag run
    echo "  $MKEXP2_UI_TAG $cmd_display"
    set +e
    "$@" 2>&1 | _StripInstallLogAnsi | tee -a "$MKEXP2_INSTALL_LOG_FILE" | sed "s/^/    ${MKEXP2_UI_DIM}|${MKEXP2_UI_RESET} /"
    local rc=${pipestatus[1]}
    set -e
    _AppendInstallCommandFooter "$rc"

    if (( rc == 0 )); then
      _UiTag ok
      echo "  $MKEXP2_UI_TAG $label"
    else
      _UiTag fail
      echo "  $MKEXP2_UI_TAG $label (exit $rc)"
    fi
    return "$rc"
  fi

  set +e
  _RunWithSpinner "$label" "$MKEXP2_INSTALL_LOG_FILE" "$@"
  local rc=$?
  set -e
  _AppendInstallCommandFooter "$rc"
  if (( rc != 0 )); then
    EchoWarn "log: $MKEXP2_INSTALL_LOG_FILE"
    tail -n 120 "$MKEXP2_INSTALL_LOG_FILE" | sed 's/^/    | /'
  fi
  return "$rc"
}

FunctionExists() {
  typeset -f "$1" >/dev/null 2>&1
}

DiscoverExperimentFunctions() {
  local experiment_file="$1"
  awk '
    /^Experiment[[:alnum:]_]*[[:space:]]*\(\)[[:space:]]*\{/ {
      fn = $1
      sub(/\(.*/, "", fn)
      print fn
    }
  ' "$experiment_file"
}

HashString() {
  local input="$1"
  if command -v sha1sum >/dev/null 2>&1; then
    printf '%s' "$input" | sha1sum | awk '{print $1}'
  else
    printf '%s' "$input" | shasum | awk '{print $1}'
  fi
}

ParseNodes() {
  if [[ "$1" == *x*x* ]]; then
    echo "${1%%x*}"
  else
    echo "1"
  fi
}

ParseMpis() {
  if [[ "$1" == *x*x* ]]; then
    local without_threads="${1%x*}"
    echo "${without_threads#*x}"
  else
    echo "1"
  fi
}

ParseThreads() {
  if [[ "$1" == *x*x* ]]; then
    echo "${1##*x}"
  else
    echo "$1"
  fi
}

IsValidTopology() {
  local topology="$1"
  local nodes="1"
  local mpis="1"
  local threads="$topology"

  if [[ "$topology" == *x*x* ]]; then
    nodes="${topology%%x*}"
    local without_threads="${topology%x*}"
    mpis="${without_threads#*x}"
    threads="${topology##*x}"
  fi

  [[ "$nodes" == <-> && "$mpis" == <-> && "$threads" == <-> ]] || return 1
  (( nodes > 0 && mpis > 0 && threads > 0 ))
}

NormalizeTopology() {
  local topology="$1"

  if [[ "$topology" == *x*x* ]]; then
    echo "$topology"
  else
    echo "1x1x$topology"
  fi
}

ParseTimelimitToSeconds() {
  local time="$1"
  local seconds="${time##*:}"
  local minutes=0
  local hours=0
  local days=0

  if [[ "$time" == *:* ]]; then
    time="${time%:*}"
    minutes="${time##*:}"
  fi
  if [[ "$time" == *:* ]]; then
    time="${time%:*}"
    hours="${time##*:}"
  fi
  if [[ "$time" == *:* ]]; then
    time="${time%:*}"
    days="$time"
  fi

  echo $((seconds + 60 * minutes + 3600 * hours + 86400 * days))
}

SafeName() {
  local s="$1"
  s="${s// /_}"
  s="${s//\//_}"
  s="${s//:/_}"
  echo "$s"
}

GenericGitFetch() {
  local repo_url="$1"
  local repo_ref="$2"
  local src_dir="$3"

  mkdir -p "$(dirname "$src_dir")"
  if [[ ! -d "$src_dir/.git" ]]; then
    EchoStep "Cloning $repo_url"
    Run git clone "$repo_url" "$src_dir"
  fi

  EchoStep "Updating $src_dir"
  Run git -C "$src_dir" fetch --all --tags

  if [[ -n "$repo_ref" && "$repo_ref" != "latest" ]]; then
    Run git -C "$src_dir" checkout "$repo_ref"
  else
    Run git -C "$src_dir" checkout main
    Run git -C "$src_dir" pull --ff-only origin main
  fi

  Run git -C "$src_dir" submodule update --init --recursive
}

ShellQuote() {
  printf '%q' "$1"
}

ResolveRunArgPlaceholders() {
  local args="$1"
  RUN_args="$args"
  if [[ -z "$args" || "$args" != *'{{'* ]]; then
    return
  fi

  local nodes="${RUN_nodes:-1}"
  local mpis="${RUN_mpis:-1}"
  local threads="${RUN_threads:-1}"
  if [[ "$nodes" != <-> ]]; then
    nodes=1
  fi
  if [[ "$mpis" != <-> ]]; then
    mpis=1
  fi
  if [[ "$threads" != <-> ]]; then
    threads=1
  fi

  local topology="${RUN_topology:-${nodes}x${mpis}x${threads}}"
  local total_cores=$(( nodes * mpis * threads ))
  local graph_name="${RUN_graph:t}"
  local quoted=""
  quoted="${(q)RUN_algorithm}"
  args="${args//\{\{algorithm\}\}/$quoted}"
  quoted="${(q)RUN_base}"
  args="${args//\{\{base\}\}/$quoted}"
  quoted="${(q)RUN_binary_path}"
  args="${args//\{\{binary_path\}\}/$quoted}"
  quoted="${(q)RUN_graph}"
  args="${args//\{\{graph\}\}/$quoted}"
  args="${args//\{\{graph_path\}\}/$quoted}"
  quoted="${(q)graph_name}"
  args="${args//\{\{graph_name\}\}/$quoted}"
  args="${args//\{\{graph_basename\}\}/$quoted}"
  quoted="${(q)RUN_k}"
  args="${args//\{\{k\}\}/$quoted}"
  quoted="${(q)RUN_epsilon}"
  args="${args//\{\{epsilon\}\}/$quoted}"
  args="${args//\{\{eps\}\}/$quoted}"
  quoted="${(q)RUN_seed}"
  args="${args//\{\{seed\}\}/$quoted}"
  quoted="${(q)topology}"
  args="${args//\{\{topology\}\}/$quoted}"
  quoted="${(q)nodes}"
  args="${args//\{\{nodes\}\}/$quoted}"
  quoted="${(q)mpis}"
  args="${args//\{\{mpis\}\}/$quoted}"
  args="${args//\{\{mpi\}\}/$quoted}"
  quoted="${(q)threads}"
  args="${args//\{\{threads\}\}/$quoted}"
  quoted="${(q)total_cores}"
  args="${args//\{\{cores\}\}/$quoted}"
  args="${args//\{\{total_threads\}\}/$quoted}"
  quoted="${(q)RUN_instance_id}"
  args="${args//\{\{instance\}\}/$quoted}"
  args="${args//\{\{instance_id\}\}/$quoted}"
  quoted="${(q)RUN_log_file}"
  args="${args//\{\{log_file\}\}/$quoted}"

  RUN_args="$args"
}

# Resolve an algorithm property for the currently active plugin context.
# Intended for PartitionerFetch_*/PartitionerBuild_*/PartitionerInvoke_* helpers.
PartitionerProperty() {
  local key="$1"
  local fallback="${2:-}"
  local algorithm=""

  if [[ -n "$MKEXP2_ACTIVE_ALGORITHM" ]]; then
    algorithm="$MKEXP2_ACTIVE_ALGORITHM"
  elif [[ -n "$RUN_algorithm" ]]; then
    algorithm="$RUN_algorithm"
  elif [[ -n "$CTX_algorithm" ]]; then
    algorithm="$CTX_algorithm"
  else
    EchoFatal "PartitionerProperty called without an active algorithm context"
    exit 1
  fi

  ResolveAlgorithmProperty "$algorithm" "$key" "$fallback"
}
