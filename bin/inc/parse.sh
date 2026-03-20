#!/usr/bin/env zsh

RegisterCurrentExperimentParsers() {
  local algorithm=""
  for algorithm in "${_algorithms[@]}"; do
    local parser=""
    local base=""
    base="${FLAT_ALGO_BASE["$algorithm"]:-}"
    if [[ -z "$base" ]]; then
      base=$(GetAlgorithmBase "$algorithm")
    fi
    parser=$(ResolveAlgorithmProperty "$algorithm" "parser" "$base")

    PARSE_ALGO_PARSER["$algorithm"]="$parser"
    if (( ${PARSE_ALGOS[(Ie)$algorithm]} == 0 )); then
      PARSE_ALGOS+=("$algorithm")
    fi
  done
}

DiscoverLogAlgorithms() {
  local -a dirs=("$PWD/logs"/*(N/))
  local dir=""
  for dir in "${dirs[@]}"; do
    echo "$dir:t"
  done
}

ResolveParserForAlgorithm() {
  local algorithm="$1"

  if [[ -n "${PARSE_ALGO_PARSER["$algorithm"]:-}" ]]; then
    echo "${PARSE_ALGO_PARSER["$algorithm"]}"
    return
  fi

  echo "$algorithm"
}

ResolveParserScriptPath() {
  local parser_spec="$1"
  local parser_file=""
  local filename="$parser_spec"

  if [[ -z "$parser_spec" ]]; then
    return 1
  fi

  # Absolute parser path.
  if [[ "$parser_spec" == /* ]]; then
    if [[ -f "$parser_spec" ]]; then
      echo "$parser_spec"
      return 0
    fi
    return 1
  fi

  # Relative parser path from the experiment directory.
  if [[ "$parser_spec" == *"/"* ]]; then
    parser_file="$PWD/$parser_spec"
    if [[ -f "$parser_file" ]]; then
      echo "$parser_file"
      return 0
    fi
    return 1
  fi

  # Bare parser name: check bundled parsers first, then experiment-local parsers.
  if [[ "$filename" != *.awk ]]; then
    filename="${filename}.awk"
  fi

  local -a candidates=(
    "$MKEXP2_HOME/parsers/$filename"
    "$MKEXP2_HOME/parsers/.$filename"
    "$PWD/parsers/$filename"
    "$PWD/$filename"
  )
  local candidate=""
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" ]]; then
      echo "$candidate"
      return 0
    fi
  done

  return 1
}

_StreamLogsForParser() {
  local algorithm="$1"
  local -a log_files=("$PWD/logs/$algorithm"/**/*.log(N))
  local file=""
  local marker=""
  local ansi_strip_expr=$'s/\x1B\\[[0-9;]*[[:alpha:]]//g'

  for file in "${log_files[@]}"; do
    marker="${file:t:r}"
    printf '__BEGIN_FILE__ %s\n' "$marker"
    sed -E "$ansi_strip_expr" "$file"
    printf '__END_FILE__\n'
  done
}

ParseAlgorithmLogs() {
  local algorithm="$1"
  local parser_spec="$2"

  local parser_file=""
  parser_file=$(ResolveParserScriptPath "$parser_spec")
  if [[ -z "$parser_file" ]]; then
    EchoWarn "no parser script for $algorithm (spec='$parser_spec'), skipping"
    return 2
  fi

  local -a log_files=("$PWD/logs/$algorithm"/**/*.log(N))
  if (( ${#log_files[@]} == 0 )); then
    EchoWarn "no log files for $algorithm, skipping"
    return 3
  fi

  mkdir -p "$PWD/results"
  local csv_file="$PWD/results/${algorithm}.csv"

  local awk_bin="awk"
  if ! command -v "$awk_bin" >/dev/null 2>&1; then
    EchoFatal "awk not found in PATH"
    return 1
  fi

  local -a awk_args=()
  local lib_file="${parser_file:h}/csv.awk"
  if [[ -f "$lib_file" ]]; then
    awk_args+=(-f "$lib_file")
  fi
  awk_args+=(-f "$parser_file")

  EchoStep "Parsing logs for $algorithm"
  EchoInfo "parser: $parser_file"
  if ! "$awk_bin" "${awk_args[@]}" < <(_StreamLogsForParser "$algorithm") > "$csv_file"; then
    EchoWarn "failed to parse $algorithm logs with parser $parser_spec"
    return 1
  fi

  local rows=0
  rows=$(wc -l < "$csv_file" | tr -d ' ')
  if [[ "$rows" == <-> ]] && (( rows > 0 )); then
    rows=$((rows - 1))
  fi

  EchoInfo "csv: $csv_file"
  EchoInfo "rows: $rows"
  return 0
}

ParseLogs() {
  local -a algorithms=()

  if (( ${#PARSE_ALGOS[@]} > 0 )); then
    algorithms=("${PARSE_ALGOS[@]}")
  else
    algorithms=($(DiscoverLogAlgorithms))
  fi

  if (( ${#algorithms[@]} == 0 )); then
    EchoWarn "no log directories found under $PWD/logs"
    return 0
  fi

  local parsed_count=0
  local skipped_count=0
  local failed_count=0
  local algorithm=""

  for algorithm in "${algorithms[@]}"; do
    local parser_name=""
    parser_name=$(ResolveParserForAlgorithm "$algorithm")

    local rc=0
    ParseAlgorithmLogs "$algorithm" "$parser_name" || rc=$?
    case "$rc" in
      0) parsed_count=$((parsed_count + 1)) ;;
      2|3) skipped_count=$((skipped_count + 1)) ;;
      *) failed_count=$((failed_count + 1)) ;;
    esac
  done

  EchoStep "Parse summary: parsed=$parsed_count skipped=$skipped_count failed=$failed_count"
  if (( failed_count > 0 )); then
    return 1
  fi
}
