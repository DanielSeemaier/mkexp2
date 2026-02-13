#!/usr/bin/env zsh

SetPartitionerDefault() {
  local base="$1"
  local key="$2"
  local value="$3"
  local allowed="${4:-any}"
  local when_note="${5:-}"
  PARTITIONER_DEFAULTS["$base::$key"]="$value"
  PARTITIONER_PROP_ALLOWED["$base::$key"]="$allowed"
  PARTITIONER_PROP_WHEN["$base::$key"]="$when_note"
}

SetSystemDefault() {
  local key="$1"
  local value="$2"
  local allowed="${3:-any}"
  local when_note="${4:-}"
  SYSTEM_DEFAULTS["$key"]="$value"
  SYSTEM_PROP_ALLOWED["$key"]="$allowed"
  SYSTEM_PROP_WHEN["$key"]="$when_note"
}

ResolveRunProperty() {
  local key="$1"
  local fallback="${2:-}"
  local value="$fallback"

  if [[ -n "${SYSTEM_DEFAULTS["$key"]:-}" ]]; then
    value="${SYSTEM_DEFAULTS["$key"]}"
  fi
  if [[ -n "${PROP_GLOBAL["$key"]:-}" ]]; then
    value="${PROP_GLOBAL["$key"]}"
  fi
  if [[ -n "${PROP_SYSTEM["$key"]:-}" ]]; then
    value="${PROP_SYSTEM["$key"]}"
  fi

  echo "$value"
}

ResolveAlgorithmProperty() {
  local algorithm="$1"
  local key="$2"
  local fallback="${3:-}"
  local value="$fallback"
  local base=""
  local base_key=""
  local algo_key=""

  base="${FLAT_ALGO_BASE["$algorithm"]:-}"
  if [[ -z "$base" ]]; then
    base=$(GetAlgorithmBase "$algorithm")
  fi
  base_key="$base::$key"
  algo_key="$algorithm::$key"

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

  echo "$value"
}
