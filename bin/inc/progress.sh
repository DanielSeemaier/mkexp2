#!/usr/bin/env zsh

_ProgressBar() {
  local done="$1"
  local total="$2"
  local width=20
  local filled=0
  local empty=0

  if (( total > 0 )); then
    filled=$(( done * width / total ))
  fi
  empty=$(( width - filled ))

  printf '%s[' "$MKEXP2_UI_DIM"
  local i=0
  if (( filled > 0 )); then
    printf '%s' "${MKEXP2_UI_RESET}${MKEXP2_UI_GREEN}"
    for (( i = 0; i < filled; i++ )); do printf '█'; done
  fi
  if (( empty > 0 )); then
    printf '%s' "${MKEXP2_UI_RESET}${MKEXP2_UI_DIM}"
    for (( i = 0; i < empty; i++ )); do printf '░'; done
  fi
  printf '%s]' "$MKEXP2_UI_RESET"
}

ProgressCommand() {
  local experiment_file="$1"
  shift
  local -a experiment_functions=("$@")
  local local_fn=""

  InitUi

  local first=1
  for local_fn in "${experiment_functions[@]}"; do
    if (( ! first )); then
      printf '\n'
    fi
    first=0

    LoadExperimentFunctionState "$experiment_file" "$local_fn"
    ExpandCurrentExperiment "$local_fn" "probe"

    local -A algo_done=()
    local -A algo_total=()
    local call_id=""
    local algorithm=""

    for algorithm in "${_algorithms[@]}"; do
      algo_done["$algorithm"]=0
      algo_total["$algorithm"]=0
    done

    for call_id in "${EXPAND_CALL_IDS[@]}"; do
      algorithm="${EXPAND_CALL["$call_id::algorithm"]}"
      local log_file="${EXPAND_CALL["$call_id::log_file"]}"
      algo_total["$algorithm"]=$(( ${algo_total["$algorithm"]:-0} + 1 ))
      if [[ -f "$log_file" ]]; then
        algo_done["$algorithm"]=$(( ${algo_done["$algorithm"]:-0} + 1 ))
      fi
    done

    local total_done=0
    local total_total=0
    for algorithm in "${_algorithms[@]}"; do
      total_done=$(( total_done + ${algo_done["$algorithm"]:-0} ))
      total_total=$(( total_total + ${algo_total["$algorithm"]:-0} ))
    done

    local display_name=""
    display_name=$(DisplayExperimentName "$local_fn")

    local max_name_len=0
    for algorithm in "${_algorithms[@]}"; do
      if (( ${#algorithm} > max_name_len )); then
        max_name_len=${#algorithm}
      fi
    done

    local total_digits=${#total_total}
    (( total_digits < 1 )) && total_digits=1

    local overall_pct=0
    if (( total_total > 0 )); then
      overall_pct=$(( total_done * 100 / total_total ))
    fi

    printf '%s%s%s' "$MKEXP2_UI_BOLD" "$display_name" "$MKEXP2_UI_RESET"
    printf '  %s%d / %d%s' "$MKEXP2_UI_DIM" "$total_done" "$total_total" "$MKEXP2_UI_RESET"
    if (( total_total > 0 )); then
      printf '  (%d%%)' "$overall_pct"
    fi
    printf '\n'

    for algorithm in "${_algorithms[@]}"; do
      local done=${algo_done["$algorithm"]:-0}
      local total=${algo_total["$algorithm"]:-0}
      local pct=0
      if (( total > 0 )); then
        pct=$(( done * 100 / total ))
      fi

      printf '  %-*s  ' "$max_name_len" "$algorithm"
      _ProgressBar "$done" "$total"
      printf '  %*d / %-*d  %3d%%\n' \
        "$total_digits" "$done" \
        "$total_digits" "$total" \
        "$pct"
    done
  done
}
