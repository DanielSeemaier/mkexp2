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

_ProgressPercent() {
  local done="$1"
  local total="$2"
  if (( total > 0 )); then
    printf '%d' $(( done * 100 / total ))
  else
    printf '0'
  fi
}

ProgressCommand() {
  local experiment_file="$1"
  shift
  local -a experiment_functions=("$@")
  local local_fn=""

  if (( ! MKEXP2_PROGRESS_JSON )); then
    InitUi
  fi

  local first=1
  local all_done=0
  local all_total=0
  local -a experiment_json=()
  for local_fn in "${experiment_functions[@]}"; do
    if (( ! MKEXP2_PROGRESS_JSON && ! first )); then
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
    all_done=$(( all_done + total_done ))
    all_total=$(( all_total + total_total ))

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

    if (( MKEXP2_PROGRESS_JSON )); then
      local sep_alg=""
      local algorithms_json="["
      for algorithm in "${_algorithms[@]}"; do
        local done=${algo_done["$algorithm"]:-0}
        local total=${algo_total["$algorithm"]:-0}
        local pct=$(_ProgressPercent "$done" "$total")
        algorithms_json+="$sep_alg{"
        algorithms_json+='"name":'
        algorithms_json+="$(JsonString "$algorithm")"
        algorithms_json+=',"done":'
        algorithms_json+="$done"
        algorithms_json+=',"total":'
        algorithms_json+="$total"
        algorithms_json+=',"percent":'
        algorithms_json+="$pct"
        algorithms_json+=',"complete":'
        if (( total > 0 && done >= total )); then
          algorithms_json+='true'
        else
          algorithms_json+='false'
        fi
        algorithms_json+='}'
        sep_alg=","
      done
      algorithms_json+=']'

      local experiment_complete=false
      if (( total_total > 0 && total_done >= total_total )); then
        experiment_complete=true
      fi

      local json="{"
      json+='"name":'
      json+="$(JsonString "$display_name")"
      json+=',"function":'
      json+="$(JsonString "$local_fn")"
      json+=',"done":'
      json+="$total_done"
      json+=',"total":'
      json+="$total_total"
      json+=',"percent":'
      json+="$overall_pct"
      json+=',"complete":'
      json+="$experiment_complete"
      json+=',"algorithms":'
      json+="$algorithms_json"
      json+='}'
      experiment_json+=("$json")
      continue
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

  if (( MKEXP2_PROGRESS_JSON )); then
    local overall_percent=$(_ProgressPercent "$all_done" "$all_total")
    local complete=false
    if (( all_total > 0 && all_done >= all_total )); then
      complete=true
    fi
    local sep=""
    local item=""
    printf '{"ok":true'
    printf ',"done":%d' "$all_done"
    printf ',"total":%d' "$all_total"
    printf ',"percent":%d' "$overall_percent"
    printf ',"complete":%s' "$complete"
    printf ',"experiments":['
    for item in "${experiment_json[@]}"; do
      printf '%s%s' "$sep" "$item"
      sep=","
    done
    printf ']}\n'
  fi
}
