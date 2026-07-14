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

ActivateConfiguredSpackEnvironment() {
  local environment=""
  local activation=""
  environment=$(ResolveRunProperty "spack.environment" "")

  if [[ -z "$environment" ]]; then
    if [[ -n "${MKEXP2_SPACK_ENVIRONMENT:-}" && -n "${SPACK_ENV:-}" ]]; then
      if ! command -v spack >/dev/null 2>&1; then
        EchoFatal "cannot deactivate the inherited Spack environment because spack was not found in PATH"
        return 1
      fi
      if ! activation=$(spack env deactivate --sh 2>&1); then
        EchoFatal "failed to deactivate the inherited Spack environment: $activation"
        return 1
      fi
      eval "$activation"
    fi
    unset MKEXP2_SPACK_ENVIRONMENT
    return 0
  fi
  if [[ "${MKEXP2_SPACK_ENVIRONMENT:-}" == "$environment" ]]; then
    return 0
  fi
  if ! command -v spack >/dev/null 2>&1; then
    EchoFatal "spack.environment is '$environment', but spack was not found in PATH"
    return 1
  fi

  if [[ -n "${SPACK_ENV:-}" ]]; then
    if ! activation=$(spack env deactivate --sh 2>&1); then
      EchoFatal "failed to deactivate the inherited Spack environment: $activation"
      return 1
    fi
    eval "$activation"
  fi

  if ! activation=$(spack env activate --sh "$environment" 2>&1); then
    EchoFatal "failed to activate Spack environment '$environment': $activation"
    return 1
  fi
  eval "$activation"
  export MKEXP2_SPACK_ENVIRONMENT="$environment"
  EchoInfo "Spack environment: $environment"
}

AppendConfiguredSpackEnvironmentActivation() {
  local output_file="$1"
  local environment=""
  local quoted_environment=""
  environment=$(ResolveRunProperty "spack.environment" "")

  [[ -n "$environment" ]] || return 0
  quoted_environment=$(ShellQuote "$environment")

  cat >> "$output_file" <<SCRIPT
if [[ "\${MKEXP2_SPACK_ENVIRONMENT:-}" != $quoted_environment ]]; then
  if ! command -v spack >/dev/null 2>&1; then
    echo "error: spack.environment is $quoted_environment, but spack was not found in PATH" >&2
    exit 127
  fi
  if [[ -n "\${SPACK_ENV:-}" ]]; then
    mkexp2_spack_activation=\$(spack env deactivate --sh) || {
      echo "error: failed to deactivate the inherited Spack environment" >&2
      exit 1
    }
    eval "\$mkexp2_spack_activation"
  fi
  mkexp2_spack_activation=\$(spack env activate --sh $quoted_environment) || {
    echo "error: failed to activate Spack environment $quoted_environment" >&2
    exit 1
  }
  eval "\$mkexp2_spack_activation"
  unset mkexp2_spack_activation
  export MKEXP2_SPACK_ENVIRONMENT=$quoted_environment
  echo "[mkexp2] Spack environment: $quoted_environment"
fi
SCRIPT
}

ResolveAlgorithmProperty() {
  local algorithm="$1"
  local key="$2"
  local fallback="${3:-}"
  local value="$fallback"
  local base=""
  local base_key=""
  local prop_algorithm=""
  local prop_key=""
  local -a property_chain=()

  base="${FLAT_ALGO_BASE["$algorithm"]:-}"
  if [[ -z "$base" ]]; then
    base=$(GetAlgorithmBase "$algorithm")
  fi
  base_key="$base::$key"

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
  property_chain=($(GetAlgorithmPropertyChain "$algorithm"))
  for prop_algorithm in "${property_chain[@]}"; do
    prop_key="$prop_algorithm::$key"
    if [[ -n "${PROP_ALGORITHM["$prop_key"]:-}" ]]; then
      value="${PROP_ALGORITHM["$prop_key"]}"
    fi
  done

  echo "$value"
}
