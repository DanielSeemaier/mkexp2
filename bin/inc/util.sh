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

PrepareInstallLogDir() {
  if [[ -z "$MKEXP2_INSTALL_LOG_DIR" ]]; then
    MKEXP2_INSTALL_LOG_DIR="$PWD/logs/install/local/$MKEXP2_RUN_ID/commands"
  fi
  mkdir -p "$MKEXP2_INSTALL_LOG_DIR"
}

_NextInstallLogFile() {
  MKEXP2_INSTALL_COUNTER=$((MKEXP2_INSTALL_COUNTER + 1))
  printf '%s/%04d.log' "$MKEXP2_INSTALL_LOG_DIR" "$MKEXP2_INSTALL_COUNTER"
}

_RunWithSpinner() {
  local label="$1"
  local log_file="$2"
  shift 2

  local exit_code=0
  local -a spinner=('|' '/' '-' "\\")
  local idx=1

  if [[ -t 1 ]]; then
    set +e
    "$@" >"$log_file" 2>&1 &
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
    "$@" >"$log_file" 2>&1
    exit_code=$?
    set -e
  fi

  if (( exit_code == 0 )); then
    _UiTag ok
    echo "  $MKEXP2_UI_TAG $label"
    return 0
  fi

  _UiTag fail
  echo "  $MKEXP2_UI_TAG $label"
  EchoWarn "log: $log_file"
  sed 's/^/    | /' "$log_file"
  return "$exit_code"
}

Run() {
  local -a cmd=("$@")
  local cmd_display="${(j: :)cmd}"
  local label="${cmd_display:-command}"

  if (( MKEXP2_RUN_VERBOSE )); then
    _UiTag run
    echo "  $MKEXP2_UI_TAG $cmd_display"
    "$@"
    return
  fi

  PrepareInstallLogDir
  local log_file
  log_file=$(_NextInstallLogFile)
  _RunWithSpinner "$label" "$log_file" "$@"
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

# Resolve an algorithm property for the currently active plugin context.
# Intended for PartitionerFetch_*/PartitionerBuild_*/PartitionerInvoke_* helpers.
PartitionerProperty() {
  local key="$1"
  local fallback="${2:-}"

  if [[ -z "$CTX_algorithm" ]]; then
    EchoFatal "PartitionerProperty called without an active CTX_algorithm"
    exit 1
  fi

  ResolveAlgorithmProperty "$CTX_algorithm" "$key" "$fallback"
}
