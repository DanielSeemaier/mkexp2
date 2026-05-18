#!/usr/bin/env zsh

_StatsJsonNumber() {
  local value="$1"
  if [[ -z "$value" ]]; then
    printf 'null'
  else
    printf '%s' "$value"
  fi
}

StatsCommand() {
  local -a csv_files=("$PWD/results"/*.csv(N))

  if (( ${#csv_files[@]} == 0 )); then
    if (( MKEXP2_STATS_JSON )); then
      printf '{"ok":true,"results_dir":%s,"algorithms":[]}\n' "$(JsonString "$PWD/results")"
    else
      EchoWarn "no CSV files found under $PWD/results"
    fi
    return 0
  fi

  local stats_tsv=""
  stats_tsv=$(awk -F, '
    function trim(value) {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      return value
    }
    function basename(path, value) {
      value = path
      sub(/^.*\//, "", value)
      sub(/\.csv$/, "", value)
      return value
    }
    function numeric(value) {
      value = trim(value)
      return value != "" && value ~ /^[-+]?[0-9]+([.][0-9]+)?([eE][-+]?[0-9]+)?$/
    }
    function add_gmean(prefix, algo, value, numeric_value) {
      numeric_value = value + 0
      if (numeric_value < 0) return
      if (prefix == "cut") {
        cut_count[algo] += 1
        if (numeric_value == 0) cut_zero[algo] = 1
        else cut_log_sum[algo] += log(numeric_value)
      } else if (prefix == "time") {
        time_count[algo] += 1
        if (numeric_value == 0) time_zero[algo] = 1
        else time_log_sum[algo] += log(numeric_value)
      }
    }
    function failed_value(value) {
      value = tolower(trim(value))
      return value == "1" || value == "true" || value == "yes" || value == "failed"
    }
    function note_file(algo, file, key) {
      key = algo SUBSEP file
      if (!(key in seen_file)) {
        seen_file[key] = 1
        files[algo] = files[algo] ? files[algo] "," file : file
      }
    }
    FNR == 1 {
      default_algo = basename(FILENAME)
      file_name = FILENAME
      sub(/^.*\//, "", file_name)
      cut_col = 0
      time_col = 0
      failed_col = 0
      algorithm_col = 0
      for (i = 1; i <= NF; i++) {
        header = tolower(trim($i))
        if (header == "cut") cut_col = i
        else if (header == "time") time_col = i
        else if (header == "failed") failed_col = i
        else if (header == "algorithm") algorithm_col = i
      }
      next
    }
    NF == 0 { next }
    {
      algo = algorithm_col ? trim($algorithm_col) : default_algo
      if (algo == "") algo = default_algo
      rows[algo] += 1
      note_file(algo, file_name)
      failed = failed_col && failed_value($failed_col)
      if (failed) failed_rows[algo] += 1
      if (!failed && cut_col && numeric($cut_col)) {
        add_gmean("cut", algo, $cut_col)
      }
      if (!failed && time_col && numeric($time_col)) {
        add_gmean("time", algo, $time_col)
      }
    }
    END {
      for (algo in rows) {
        avg_cut = cut_count[algo] ? (cut_zero[algo] ? 0 : exp(cut_log_sum[algo] / cut_count[algo])) : ""
        avg_time = time_count[algo] ? (time_zero[algo] ? 0 : exp(time_log_sum[algo] / time_count[algo])) : ""
        printf "%s\t%d\t%d\t%d\t%s\t%d\t%s\t%s\n", algo, rows[algo], failed_rows[algo] + 0, cut_count[algo] + 0, avg_cut, time_count[algo] + 0, avg_time, files[algo]
      }
    }
  ' "${csv_files[@]}" | sort)

  if (( MKEXP2_STATS_JSON )); then
    local sep=""
    local algorithms_json="["
    local line=""
    while IFS=$'\t' read -r algorithm rows failed cut_count avg_cut time_count avg_time files; do
      [[ -n "$algorithm" ]] || continue
      local files_json="["
      local file_sep=""
      local -a file_items=("${(@s:,:)files}")
      local file=""
      for file in "${file_items[@]}"; do
        [[ -n "$file" ]] || continue
        files_json+="$file_sep$(JsonString "$file")"
        file_sep=","
      done
      files_json+="]"

      algorithms_json+="$sep{"
      algorithms_json+='"algorithm":'
      algorithms_json+="$(JsonString "$algorithm")"
      algorithms_json+=',"rows":'
      algorithms_json+="${rows:-0}"
      algorithms_json+=',"failed":'
      algorithms_json+="${failed:-0}"
      algorithms_json+=',"cut_count":'
      algorithms_json+="${cut_count:-0}"
      algorithms_json+=',"avg_cut":'
      algorithms_json+="$(_StatsJsonNumber "$avg_cut")"
      algorithms_json+=',"time_count":'
      algorithms_json+="${time_count:-0}"
      algorithms_json+=',"avg_time":'
      algorithms_json+="$(_StatsJsonNumber "$avg_time")"
      algorithms_json+=',"files":'
      algorithms_json+="$files_json"
      algorithms_json+='}'
      sep=","
    done <<< "$stats_tsv"
    algorithms_json+="]"
    printf '{"ok":true,"results_dir":%s,"algorithms":%s}\n' "$(JsonString "$PWD/results")" "$algorithms_json"
    return 0
  fi

  printf '%-32s %8s %8s %14s %14s\n' "Algorithm" "Rows" "Failed" "GMean Cut" "GMean Time"
  local line=""
  while IFS=$'\t' read -r algorithm rows failed cut_count avg_cut time_count avg_time files; do
    [[ -n "$algorithm" ]] || continue
    [[ -n "$avg_cut" ]] || avg_cut="n/a"
    [[ -n "$avg_time" ]] || avg_time="n/a"
    printf '%-32s %8s %8s %14s %14s\n' "$algorithm" "$rows" "$failed" "$avg_cut" "$avg_time"
  done <<< "$stats_tsv"
}
