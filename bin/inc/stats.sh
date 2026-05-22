#!/usr/bin/env zsh

StatsCommand() {
  local home="${MKEXP2_HOME:-}"
  if [[ -z "$home" ]]; then
    EchoFatal "MKEXP2_HOME is not set; cannot locate stats backend"
    return 1
  fi

  local stats_script="$home/plots/stats.R"
  if [[ ! -f "$stats_script" ]]; then
    EchoFatal "stats backend not found: $stats_script"
    return 1
  fi

  if ! command -v Rscript >/dev/null 2>&1; then
    EchoFatal "Rscript not found; mkexp2 stats requires R with tidyverse/jsonlite"
    return 1
  fi

  local -a args
  args=(--results "$PWD/results")
  if (( MKEXP2_STATS_JSON )); then
    args+=(--json)
  fi

  env -u R_HOME Rscript "$stats_script" "${args[@]}"
}
