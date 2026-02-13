#!/usr/bin/env zsh

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

CheckCurrentExperiment() {
  local experiment_display="$1"
  local errors_before="$MKEXP2_CHECK_ERROR_COUNT"
  local warns_before="$MKEXP2_CHECK_WARN_COUNT"

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

  local experiment_errors=$((MKEXP2_CHECK_ERROR_COUNT - errors_before))
  local experiment_warns=$((MKEXP2_CHECK_WARN_COUNT - warns_before))

  EchoStep "Check summary for $experiment_display: errors=$experiment_errors warnings=$experiment_warns"
  EchoInfo "launcher: $_system"
  EchoInfo "algorithms: ${#_algorithms[@]} | graphs: ${#_graphs[@]} | topologies: ${#_threads[@]}"
}

FinalizeChecks() {
  EchoStep "Check totals: errors=$MKEXP2_CHECK_ERROR_COUNT warnings=$MKEXP2_CHECK_WARN_COUNT"
  if (( MKEXP2_CHECK_ERROR_COUNT > 0 )); then
    return 1
  fi
  return 0
}
