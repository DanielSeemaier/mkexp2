#!/usr/bin/env zsh

_CheckDivider() {
  InitUi
  printf "  %s%s%s\n" "$MKEXP2_UI_DIM" "------------------------------------------------------------" "$MKEXP2_UI_RESET"
}

_CheckInfoKV() {
  local key="$1"
  local value="$2"
  _UiTag info
  printf "  %s %-14s %s\n" "$MKEXP2_UI_TAG" "${key}:" "$value"
}

CheckError() {
  _UiTag fail
  echo "  $MKEXP2_UI_TAG $*"
  MKEXP2_CHECK_ERROR_COUNT=$((MKEXP2_CHECK_ERROR_COUNT + 1))
}

CheckWarn() {
  _UiTag warn
  echo "  $MKEXP2_UI_TAG $*"
  MKEXP2_CHECK_WARN_COUNT=$((MKEXP2_CHECK_WARN_COUNT + 1))
}

_CheckGraphExists() {
  local graph="$1"

  if [[ -f "$graph" ]]; then
    return 0
  fi
  if [[ -f "$graph.graph" ]]; then
    return 0
  fi
  if [[ -f "$graph.metis" ]]; then
    return 0
  fi
  if [[ -f "$graph.parhip" ]]; then
    return 0
  fi

  return 1
}

_CheckValidateAllowedValue() {
  local label="$1"
  local value="$2"
  local allowed="$3"

  if [[ "$allowed" != enum:* ]]; then
    return 0
  fi

  local choices="${allowed#enum:}"
  local -a options=("${(@s:|:)choices}")

  local option=""
  for option in "${options[@]}"; do
    if [[ "$value" == "$option" ]]; then
      return 0
    fi
  done

  CheckError "invalid $label '$value' (expected one of: $choices)"
  return 0
}

_CheckValidateKnownProperties() {
  local -A known_run_keys=()
  local -A known_algorithm_keys=()

  local key=""
  local prop_key=""
  local algorithm=""
  local base=""
  local full_key=""

  for key in ${(k)SYSTEM_DEFAULTS}; do
    key="${key#\"}"
    key="${key%\"}"
    known_run_keys["$key"]=1
  done

  local -a core_run_keys=(
    timelimit
    timelimit.per_instance
    parse.auto
    parse.slurm.timelimit
    slurm.install.mode
    slurm.install.timelimit
    slurm.dependency
    slurm.partition
    slurm.qos
    slurm.account
    slurm.constraint
    slurm.use_array
    slurm.array.max_parallel
    slurm.call_wrapper
    slurm.minimal_header
    local.call_wrapper
  )
  for key in "${core_run_keys[@]}"; do
    known_run_keys["$key"]=1
  done

  for full_key in ${(k)PARTITIONER_DEFAULTS}; do
    full_key="${full_key#\"}"
    full_key="${full_key%\"}"
    prop_key="${full_key#*::}"
    known_algorithm_keys["$prop_key"]=1
  done

  local -a core_algorithm_keys=(
    parser
    build_opts
    build_options
    repo_url
    repo_ref
    cmake_flags
    supports_distributed
    use_openmp_env
    version
  )
  for key in "${core_algorithm_keys[@]}"; do
    known_algorithm_keys["$key"]=1
  done

  for key in ${(ok)PROP_GLOBAL}; do
    key="${key#\"}"
    key="${key%\"}"
    if [[ -z "${known_run_keys["$key"]:-}" ]]; then
      CheckWarn "unknown Property '$key'"
      continue
    fi

    if [[ -n "${SYSTEM_PROP_ALLOWED["$key"]:-}" ]]; then
      _CheckValidateAllowedValue "Property '$key'" "${PROP_GLOBAL["$key"]}" "${SYSTEM_PROP_ALLOWED["$key"]}"
    fi
  done

  for key in ${(ok)PROP_SYSTEM}; do
    key="${key#\"}"
    key="${key%\"}"
    if [[ -z "${known_run_keys["$key"]:-}" ]]; then
      CheckWarn "unknown SystemProperty '$key'"
      continue
    fi

    if [[ -n "${SYSTEM_PROP_ALLOWED["$key"]:-}" ]]; then
      _CheckValidateAllowedValue "SystemProperty '$key'" "${PROP_SYSTEM["$key"]}" "${SYSTEM_PROP_ALLOWED["$key"]}"
    fi
  done

  for full_key in ${(ok)PROP_ALGORITHM}; do
    full_key="${full_key#\"}"
    full_key="${full_key%\"}"
    algorithm="${full_key%%::*}"
    prop_key="${full_key#*::}"

    if (( ${_algorithms[(Ie)$algorithm]} == 0 )); then
      continue
    fi

    base="${FLAT_ALGO_BASE["$algorithm"]:-}"
    if [[ -z "$base" ]]; then
      base=$(GetAlgorithmBase "$algorithm")
    fi

    if [[ -z "${known_algorithm_keys["$prop_key"]:-}" ]] && [[ -z "${PARTITIONER_DEFAULTS["$base::$prop_key"]:-}" ]]; then
      CheckWarn "unknown AlgorithmProperty '$prop_key' for '$algorithm' [$base]"
      continue
    fi

    if [[ -n "${PARTITIONER_PROP_ALLOWED["$base::$prop_key"]:-}" ]]; then
      _CheckValidateAllowedValue \
        "AlgorithmProperty '$prop_key' for '$algorithm' [$base]" \
        "${PROP_ALGORITHM["$full_key"]}" \
        "${PARTITIONER_PROP_ALLOWED["$base::$prop_key"]}"
    fi
  done
}

CheckCurrentExperiment() {
  local experiment_display="$1"
  local errors_before="$MKEXP2_CHECK_ERROR_COUNT"
  local warns_before="$MKEXP2_CHECK_WARN_COUNT"

  EchoExperiment "Check: $experiment_display"
  _CheckDivider

  local launcher_file="$MKEXP2_HOME/plugins/launchers/${_system}.sh"
  if [[ ! -f "$launcher_file" ]]; then
    CheckError "unknown launcher '$_system' ($launcher_file not found)"
  else
    LoadLauncherPlugin "$_system"
    local wrap_fn="LauncherWrapCommand_${_system}"
    local write_fn="LauncherWriteJob_${_system}"
    if ! FunctionExists "$wrap_fn"; then
      CheckError "launcher $_system is missing $wrap_fn"
    fi
    if ! FunctionExists "$write_fn"; then
      CheckError "launcher $_system is missing $write_fn"
    fi

  fi

  if (( ${#_algorithms[@]} == 0 )); then
    CheckError "no Algorithms specified"
  fi
  if (( ${#_graphs[@]} == 0 )); then
    CheckError "no Graph/Graphs entries specified"
  fi

  local -A seen_algorithms=()
  local -A algorithm_base=()
  local algorithm=""
  for algorithm in "${_algorithms[@]}"; do
    if [[ -n "${seen_algorithms["$algorithm"]:-}" ]]; then
      CheckWarn "duplicate algorithm entry '$algorithm'"
      continue
    fi
    seen_algorithms["$algorithm"]=1

    local base="${FLAT_ALGO_BASE["$algorithm"]:-}"
    if [[ -z "$base" ]]; then
      base=$(GetAlgorithmBase "$algorithm")
    fi
    algorithm_base["$algorithm"]="$base"

    local plugin_file="$MKEXP2_HOME/plugins/partitioners/${base}.sh"
    if [[ ! -f "$plugin_file" ]]; then
      CheckError "algorithm '$algorithm' resolves to unknown partitioner '$base'"
      continue
    fi

    LoadPartitionerPlugin "$base"

    local build_fn="PartitionerBuild_${base}"
    local invoke_fn="PartitionerInvoke_${base}"
    if ! FunctionExists "$build_fn"; then
      CheckError "partitioner '$base' is missing required hook $build_fn"
    fi
    if ! FunctionExists "$invoke_fn"; then
      CheckError "partitioner '$base' is missing required hook $invoke_fn"
    fi
  done

  local parse_auto=""
  parse_auto=$(ResolveRunProperty "parse.auto" "false")
  if [[ "$parse_auto" == "true" ]]; then
    local parser_spec=""
    for algorithm in "${_algorithms[@]}"; do
      parser_spec=$(ResolveParserForAlgorithm "$algorithm")
      if ! ResolveParserScriptPath "$parser_spec" >/dev/null; then
        CheckWarn "parse.auto=true but parser '$parser_spec' for '$algorithm' was not found"
      fi
    done
  fi

  local -A seen_graphs=()
  local graph=""
  for graph in "${_graphs[@]}"; do
    local graph_name="${graph:t}"
    if [[ -n "${seen_graphs["$graph"]:-}" ]]; then
      CheckWarn "duplicate graph entry '$graph_name'"
      continue
    fi
    seen_graphs["$graph"]=1

    if ! _CheckGraphExists "$graph"; then
      CheckWarn "graph '$graph' was not found (.graph/.metis/.parhip also checked)"
    fi
  done

  local -A seen_topologies=()
  local topology=""
  for topology in "${_threads[@]}"; do
    if [[ -n "${seen_topologies["$topology"]:-}" ]]; then
      CheckWarn "duplicate topology '$topology'"
      continue
    fi
    seen_topologies["$topology"]=1

    local nodes="1"
    local mpis="1"
    local threads="$topology"

    if [[ "$topology" == *x*x* ]]; then
      nodes="${topology%%x*}"
      local without_threads="${topology%x*}"
      mpis="${without_threads#*x}"
      threads="${topology##*x}"
    fi

    if [[ "$nodes" != <-> || "$mpis" != <-> || "$threads" != <-> ]]; then
      CheckError "invalid Threads entry '$topology' (expected T or NxMxT with positive integers)"
      continue
    fi
    if (( nodes <= 0 || mpis <= 0 || threads <= 0 )); then
      CheckError "invalid Threads entry '$topology' (values must be > 0)"
      continue
    fi

    if (( nodes > 1 || mpis > 1 )); then
      for algorithm in "${_algorithms[@]}"; do
        local supports_distributed="false"
        supports_distributed=$(ResolveAlgorithmProperty "$algorithm" "supports_distributed" "false")
        if [[ "$supports_distributed" != "true" ]]; then
          local base="${algorithm_base["$algorithm"]:-$algorithm}"
          CheckError "$algorithm [$base] does not support distributed topology '$topology'"
        fi
      done
    fi
  done

  local k=""
  for k in "${_ks[@]}"; do
    if [[ "$k" != <-> ]] || (( k <= 0 )); then
      CheckError "invalid k value '$k' (must be a positive integer)"
    fi
  done

  local seed=""
  for seed in "${_seeds[@]}"; do
    if [[ "$seed" != <-> ]] || (( seed <= 0 )); then
      CheckError "invalid seed '$seed' (must be a positive integer)"
    fi
  done

  _CheckValidateKnownProperties

  local experiment_errors=$((MKEXP2_CHECK_ERROR_COUNT - errors_before))
  local experiment_warns=$((MKEXP2_CHECK_WARN_COUNT - warns_before))

  local summary_status="PASS"
  local status_tag="ok"
  if (( experiment_errors > 0 )); then
    summary_status="FAIL"
    status_tag="fail"
  elif (( experiment_warns > 0 )); then
    summary_status="WARN"
    status_tag="warn"
  fi

  _UiTag "$status_tag"
  printf "  %s %s\n" "$MKEXP2_UI_TAG" "Summary ($experiment_display): $summary_status"
  _CheckInfoKV "launcher" "$_system"
  _CheckInfoKV "algorithms" "${#_algorithms[@]}"
  _CheckInfoKV "graphs" "${#_graphs[@]}"
  _CheckInfoKV "topologies" "${#_threads[@]}"
  _CheckInfoKV "errors" "$experiment_errors"
  _CheckInfoKV "warnings" "$experiment_warns"
  _CheckDivider
  echo ""
}

FinalizeChecks() {
  local final_status="PASS"
  local status_tag="ok"
  if (( MKEXP2_CHECK_ERROR_COUNT > 0 )); then
    final_status="FAIL"
    status_tag="fail"
  elif (( MKEXP2_CHECK_WARN_COUNT > 0 )); then
    final_status="WARN"
    status_tag="warn"
  fi

  _UiTag "$status_tag"
  printf "%s %s\n" "$MKEXP2_UI_TAG" "Check totals: $final_status"
  _CheckInfoKV "errors" "$MKEXP2_CHECK_ERROR_COUNT"
  _CheckInfoKV "warnings" "$MKEXP2_CHECK_WARN_COUNT"
  if (( MKEXP2_CHECK_ERROR_COUNT > 0 )); then
    return 1
  fi
  return 0
}
