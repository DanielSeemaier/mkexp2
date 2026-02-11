#!/usr/bin/env zsh

SetPartitionerDefault() {
  local base="$1"
  local key="$2"
  local value="$3"
  PARTITIONER_DEFAULTS["$base::$key"]="$value"
}

SetSystemDefault() {
  local key="$1"
  local value="$2"
  SYSTEM_DEFAULTS["$key"]="$value"
}

ResolveRunProperty() {
  local subexp="$1"
  local key="$2"
  local fallback="${3:-}"
  local value="$fallback"
  local sub_key="$subexp::$key"

  if [[ -n "${SYSTEM_DEFAULTS["$key"]:-}" ]]; then
    value="${SYSTEM_DEFAULTS["$key"]}"
  fi
  if [[ -n "${PROP_GLOBAL["$key"]:-}" ]]; then
    value="${PROP_GLOBAL["$key"]}"
  fi
  if [[ -n "${PROP_SYSTEM["$key"]:-}" ]]; then
    value="${PROP_SYSTEM["$key"]}"
  fi
  if [[ -n "${PROP_SUBEXPERIMENT["$sub_key"]:-}" ]]; then
    value="${PROP_SUBEXPERIMENT["$sub_key"]}"
  fi

  echo "$value"
}

ResolveAlgorithmProperty() {
  local algorithm="$1"
  local subexp="$2"
  local key="$3"
  local fallback="${4:-}"
  local value="$fallback"
  local base=""
  local base_key=""
  local algo_key=""
  local sub_key=""

  base="${FLAT_ALGO_BASE["$algorithm"]:-}"
  if [[ -z "$base" ]]; then
    base=$(GetAlgorithmBase "$algorithm")
  fi
  base_key="$base::$key"
  algo_key="$algorithm::$key"
  sub_key="$subexp::$key"

  if [[ -n "${PARTITIONER_DEFAULTS["$base_key"]:-}" ]]; then
    value="${PARTITIONER_DEFAULTS["$base_key"]}"
  fi
  if [[ -n "${SYSTEM_DEFAULTS["$key"]:-}" ]]; then
    value="${SYSTEM_DEFAULTS["$key"]}"
  fi
  if [[ -n "${PROP_GLOBAL["$key"]:-}" ]]; then
    value="${PROP_GLOBAL["$key"]}"
  fi
  if [[ -n "${PROP_SYSTEM["$key"]:-}" ]]; then
    value="${PROP_SYSTEM["$key"]}"
  fi
  if [[ -n "${PROP_ALGORITHM["$base_key"]:-}" ]]; then
    value="${PROP_ALGORITHM["$base_key"]}"
  fi
  if [[ -n "${PROP_ALGORITHM["$algo_key"]:-}" ]]; then
    value="${PROP_ALGORITHM["$algo_key"]}"
  fi
  if [[ -n "${PROP_SUBEXPERIMENT["$sub_key"]:-}" ]]; then
    value="${PROP_SUBEXPERIMENT["$sub_key"]}"
  fi

  echo "$value"
}
