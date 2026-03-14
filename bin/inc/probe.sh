#!/usr/bin/env zsh

typeset -ga PROBE_CORE_RUN_KEYS=(
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

typeset -ga PROBE_CORE_ALGORITHM_KEYS=(
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

JsonEscape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  s="${s//$'\t'/\\t}"
  s="${s//$'\f'/\\f}"
  s="${s//$'\b'/\\b}"
  printf '%s' "$s"
}

JsonString() {
  printf '"%s"' "$(JsonEscape "$1")"
}

JsonScalar() {
  local value="$1"
  if [[ "$value" == "true" || "$value" == "false" ]]; then
    printf '%s' "$value"
    return
  fi
  if [[ "$value" == <-> || "$value" == -<-> ]]; then
    printf '%s' "$value"
    return
  fi
  if [[ "$value" =~ '^-?[0-9]+\.[0-9]+$' ]]; then
    printf '%s' "$value"
    return
  fi
  JsonString "$value"
}

ProbeCleanKey() {
  local key="$1"
  key="${key#\"}"
  key="${key%\"}"
  printf '%s' "$key"
}

ProbeCollectRunPropertyKeys() {
  local -A seen=()
  local -a keys=()
  local key=""

  for key in "${PROBE_CORE_RUN_KEYS[@]}"; do
    seen["$key"]=1
  done
  for key in ${(k)SYSTEM_DEFAULTS}; do
    key=$(ProbeCleanKey "$key")
    seen["$key"]=1
  done
  for key in ${(k)PROP_GLOBAL}; do
    key=$(ProbeCleanKey "$key")
    seen["$key"]=1
  done
  for key in ${(k)PROP_SYSTEM}; do
    key=$(ProbeCleanKey "$key")
    seen["$key"]=1
  done

  keys=("${(@k)seen}")
  keys=("${(@on)keys}")
  keys=("${(@Q)keys}")
  print -r -l -- "${keys[@]}"
}

ProbeCollectAlgorithmPropertyKeys() {
  local algorithm="$1"
  local base="$2"
  local -A seen=()
  local -a keys=()
  local key=""
  local full_key=""

  for key in "${PROBE_CORE_ALGORITHM_KEYS[@]}"; do
    seen["$key"]=1
  done
  for full_key in ${(k)PARTITIONER_DEFAULTS}; do
    full_key=$(ProbeCleanKey "$full_key")
    if [[ "$full_key" == "${base}::"* ]]; then
      key="${full_key#${base}::}"
      seen["$key"]=1
    fi
  done
  for full_key in ${(k)PROP_ALGORITHM}; do
    full_key=$(ProbeCleanKey "$full_key")
    if [[ "$full_key" == "${base}::"* || "$full_key" == "${algorithm}::"* ]]; then
      key="${full_key#*::}"
      seen["$key"]=1
    fi
  done

  keys=("${(@k)seen}")
  keys=("${(@on)keys}")
  keys=("${(@Q)keys}")
  print -r -l -- "${keys[@]}"
}

ProbeResolveExperimentFunction() {
  local selector="$1"
  shift
  local -a experiment_functions=("$@")
  local fn=""
  local -a display_matches=()

  for fn in "${experiment_functions[@]}"; do
    if [[ "$fn" == "$selector" ]]; then
      echo "$fn"
      return 0
    fi
  done

  for fn in "${experiment_functions[@]}"; do
    if [[ "$(DisplayExperimentName "$fn")" == "$selector" ]]; then
      display_matches+=("$fn")
    fi
  done

  if (( ${#display_matches[@]} == 1 )); then
    echo "${display_matches[1]}"
    return 0
  fi
  if (( ${#display_matches[@]} > 1 )); then
    EchoFatal "probe selector '$selector' matches multiple experiments: ${(j:, :)display_matches}"
    return 1
  fi

  EchoFatal "unknown experiment '$selector'"
  return 1
}

ProbeEmitStringArray() {
  local sep=""
  local item=""
  printf '['
  for item in "$@"; do
    printf '%s%s' "$sep" "$(JsonString "$item")"
    sep=","
  done
  printf ']'
}

ProbeEmitScalarArray() {
  local sep=""
  local item=""
  printf '['
  for item in "$@"; do
    printf '%s%s' "$sep" "$(JsonScalar "$item")"
    sep=","
  done
  printf ']'
}

ProbeEmitRunProperties() {
  local -a keys=()
  local key=""
  local sep=""

  keys=($(ProbeCollectRunPropertyKeys))
  printf '{'
  for key in "${keys[@]}"; do
    printf '%s%s:%s' "$sep" "$(JsonString "$key")" "$(JsonScalar "$(ResolveRunProperty "$key" "")")"
    sep=","
  done
  printf '}'
}

ProbeEmitDeclaredAlgorithmDefinitions() {
  local -a names=("${(@k)ALG_DEF_BASE}")
  local sep=""
  local name=""
  names=("${(@on)names}")
  names=("${(@Q)names}")

  printf '['
  for name in "${names[@]}"; do
    name=$(ProbeCleanKey "$name")
    printf '%s{"name":%s,"base":%s,"args":%s}' \
      "$sep" \
      "$(JsonString "$name")" \
      "$(JsonString "${ALG_DEF_BASE["$name"]}")" \
      "$(JsonString "${ALG_DEF_ARGS["$name"]:-}")"
    sep=","
  done
  printf ']'
}

ProbeEmitDeclaredGlobalProperties() {
  local -a keys=("${(@k)PROP_GLOBAL}")
  local key=""
  local sep=""
  keys=("${(@on)keys}")
  keys=("${(@Q)keys}")

  printf '{'
  for key in "${keys[@]}"; do
    key=$(ProbeCleanKey "$key")
    printf '%s%s:%s' "$sep" "$(JsonString "$key")" "$(JsonString "${PROP_GLOBAL["$key"]}")"
    sep=","
  done
  printf '}'
}

ProbeEmitDeclaredSystemProperties() {
  local -a keys=("${(@k)PROP_SYSTEM}")
  local key=""
  local sep=""
  keys=("${(@on)keys}")
  keys=("${(@Q)keys}")

  printf '{'
  for key in "${keys[@]}"; do
    key=$(ProbeCleanKey "$key")
    printf '%s%s:%s' "$sep" "$(JsonString "$key")" "$(JsonString "${PROP_SYSTEM["$key"]}")"
    sep=","
  done
  printf '}'
}

ProbeEmitDeclaredAlgorithmProperties() {
  local -A algorithms_seen=()
  local -a algorithms=()
  local full_key=""
  local algorithm=""
  local key=""
  local sep_alg=""

  for full_key in ${(k)PROP_ALGORITHM}; do
    full_key=$(ProbeCleanKey "$full_key")
    algorithm="${full_key%%::*}"
    algorithms_seen["$algorithm"]=1
  done
  algorithms=("${(@k)algorithms_seen}")
  algorithms=("${(@on)algorithms}")
  algorithms=("${(@Q)algorithms}")

  printf '{'
  for algorithm in "${algorithms[@]}"; do
    local -a prop_keys=()
    local sep_prop=""
    for full_key in ${(k)PROP_ALGORITHM}; do
      full_key=$(ProbeCleanKey "$full_key")
      if [[ "$full_key" == "${algorithm}::"* ]]; then
        key="${full_key#${algorithm}::}"
        prop_keys+=("$key")
      fi
    done
    prop_keys=("${(@ou)prop_keys}")
    prop_keys=("${(@Q)prop_keys}")

    printf '%s%s:{' "$sep_alg" "$(JsonString "$algorithm")"
    for key in "${prop_keys[@]}"; do
      printf '%s%s:%s' "$sep_prop" "$(JsonString "$key")" "$(JsonString "${PROP_ALGORITHM["$algorithm::$key"]}")"
      sep_prop=","
    done
    printf '}'
    sep_alg=","
  done
  printf '}'
}

ProbeEmitDeclaredSection() {
  printf '{'
  printf '"algorithms":%s,' "$(ProbeEmitStringArray "${_algorithms[@]}")"
  printf '"graphs":%s,' "$(ProbeEmitStringArray "${_graphs[@]}")"
  printf '"ks":%s,' "$(ProbeEmitScalarArray "${_ks[@]}")"
  printf '"seeds":%s,' "$(ProbeEmitScalarArray "${_seeds[@]}")"
  printf '"epsilons":%s,' "$(ProbeEmitScalarArray "${_epsilons[@]}")"
  printf '"topologies":%s,' "$(ProbeEmitStringArray "${_threads[@]}")"
  printf '"timelimit":%s,' "$(JsonString "$_timelimit")"
  printf '"timelimit_per_instance":%s,' "$(JsonString "$_timelimit_per_instance")"
  printf '"algorithm_definitions":%s,' "$(ProbeEmitDeclaredAlgorithmDefinitions)"
  printf '"global_properties":%s,' "$(ProbeEmitDeclaredGlobalProperties)"
  printf '"system_properties":%s,' "$(ProbeEmitDeclaredSystemProperties)"
  printf '"algorithm_properties":%s' "$(ProbeEmitDeclaredAlgorithmProperties)"
  printf '}'
}

ProbeGraphCandidates() {
  local graph="$1"
  local -A seen=()
  local -a candidates=()
  local candidate=""

  candidates+=("$graph")
  candidates+=("${graph}.graph")
  candidates+=("${graph}.metis")
  candidates+=("${graph}.parhip")

  for candidate in "${candidates[@]}"; do
    if [[ -z "${seen["$candidate"]:-}" ]]; then
      seen["$candidate"]=1
      print -r -- "$candidate"
    fi
  done
}

ProbeEmitResolvedGraphs() {
  local sep=""
  local graph=""

  printf '['
  for graph in "${_graphs[@]}"; do
    local -a candidates=()
    local resolved=""
    local candidate=""
    local candidate_sep=""
    local existing_sep=""

    candidates=($(ProbeGraphCandidates "$graph"))
    for candidate in "${candidates[@]}"; do
      if [[ -f "$candidate" ]]; then
        resolved="$candidate"
        break
      fi
    done

    printf '%s{' "$sep"
    printf '"spec":%s,' "$(JsonString "$graph")"
    printf '"basename":%s,' "$(JsonString "${graph:t}")"
    printf '"candidates":['
    for candidate in "${candidates[@]}"; do
      printf '%s%s' "$candidate_sep" "$(JsonString "$candidate")"
      candidate_sep=","
    done
    printf '],'
    printf '"existing_candidates":['
    for candidate in "${candidates[@]}"; do
      if [[ -f "$candidate" ]]; then
        printf '%s%s' "$existing_sep" "$(JsonString "$candidate")"
        existing_sep=","
      fi
    done
    printf '],'
    printf '"resolved_path":%s,' "$(JsonString "$resolved")"
    if [[ -n "$resolved" ]]; then
      printf '"exists":true'
    else
      printf '"exists":false'
    fi
    printf '}'
    sep=","
  done
  printf ']'
}

ProbeEmitResolvedTopologies() {
  local sep=""
  local topology=""

  printf '['
  for topology in "${_threads[@]}"; do
    local nodes=""
    local mpis=""
    local threads=""
    local distributed="false"

    nodes=$(ParseNodes "$topology")
    mpis=$(ParseMpis "$topology")
    threads=$(ParseThreads "$topology")
    if (( nodes > 1 || mpis > 1 )); then
      distributed="true"
    fi

    printf '%s{"spec":%s,"nodes":%s,"mpis":%s,"threads":%s,"distributed":%s}' \
      "$sep" \
      "$(JsonString "$topology")" \
      "$nodes" \
      "$mpis" \
      "$threads" \
      "$distributed"
    sep=","
  done
  printf ']'
}

ProbeEmitResolvedAlgorithmProperties() {
  local algorithm="$1"
  local base="$2"
  local -a keys=()
  local key=""
  local sep=""

  keys=($(ProbeCollectAlgorithmPropertyKeys "$algorithm" "$base"))
  printf '{'
  for key in "${keys[@]}"; do
    printf '%s%s:%s' "$sep" "$(JsonString "$key")" "$(JsonScalar "$(ResolveAlgorithmProperty "$algorithm" "$key" "")")"
    sep=","
  done
  printf '}'
}

ProbeEmitResolvedAlgorithms() {
  local sep=""
  local algorithm=""

  printf '['
  for algorithm in "${_algorithms[@]}"; do
    local base=""
    local parser_spec=""
    local parser_path=""

    PopulateBuildContext "$algorithm"
    base="$CTX_base"
    parser_spec=$(ResolveParserForAlgorithm "$algorithm")
    parser_path=$(ResolveParserScriptPath "$parser_spec" 2>/dev/null || true)

    printf '%s{' "$sep"
    printf '"name":%s,' "$(JsonString "$algorithm")"
    printf '"base":%s,' "$(JsonString "$base")"
    printf '"args":%s,' "$(JsonString "$CTX_args")"
    printf '"build_key":%s,' "$(JsonString "$CTX_build_key")"
    printf '"binary_path":%s,' "$(JsonString "$CTX_binary_path")"
    printf '"source_dir":%s,' "$(JsonString "$CTX_source_dir")"
    printf '"parser":{'
    printf '"spec":%s,' "$(JsonString "$parser_spec")"
    printf '"resolved_path":%s,' "$(JsonString "$parser_path")"
    if [[ -n "$parser_path" ]]; then
      printf '"found":true'
    else
      printf '"found":false'
    fi
    printf '},'
    printf '"properties":%s' "$(ProbeEmitResolvedAlgorithmProperties "$algorithm" "$base")"
    printf '}'
    sep=","
  done
  printf ']'
}

ProbeEmitMatrixSummary() {
  local sep_alg=""
  local sep_top=""
  local algorithm=""
  local topology=""

  printf '{'
  printf '"total_calls":%s,' "$EXPAND_TOTAL_CALLS"
  printf '"job_count":%s,' "${#EXPAND_JOB_KEYS[@]}"
  printf '"generated_topologies":%s,' "$(ProbeEmitStringArray "${EXPAND_GENERATED_TOPOLOGIES[@]}")"
  printf '"per_algorithm":{'
  for algorithm in "${_algorithms[@]}"; do
    printf '%s%s:%s' "$sep_alg" "$(JsonString "$algorithm")" "${EXPAND_CALL_COUNT_BY_ALGORITHM["$algorithm"]:-0}"
    sep_alg=","
  done
  printf '},'
  printf '"per_topology":{'
  for topology in "${EXPAND_GENERATED_TOPOLOGIES[@]}"; do
    printf '%s%s:%s' "$sep_top" "$(JsonString "$topology")" "${EXPAND_CALL_COUNT_BY_TOPOLOGY["$topology"]:-0}"
    sep_top=","
  done
  printf '}'
  printf '}'
}

ProbeEmitResolvedSection() {
  local include_algorithms="$1"
  local include_graphs="$2"
  local include_topologies="$3"
  local include_run_properties="$4"
  local include_matrix="$5"
  local sep=""

  printf '{'
  if (( include_run_properties )); then
    printf '%s"run_properties":%s' "$sep" "$(ProbeEmitRunProperties)"
    sep=","
  fi
  if (( include_algorithms )); then
    printf '%s"algorithms":%s' "$sep" "$(ProbeEmitResolvedAlgorithms)"
    sep=","
  fi
  if (( include_graphs )); then
    printf '%s"graphs":%s' "$sep" "$(ProbeEmitResolvedGraphs)"
    sep=","
  fi
  if (( include_topologies )); then
    printf '%s"topologies":%s' "$sep" "$(ProbeEmitResolvedTopologies)"
    sep=","
  fi
  if (( include_matrix )); then
    printf '%s"matrix":%s' "$sep" "$(ProbeEmitMatrixSummary)"
  fi
  printf '}'
}

ProbeEmitInstallJobSummary() {
  printf '{'
  printf '"type":"install",'
  printf '"launcher":"slurm",'
  printf '"mode":"job",'
  printf '"partition":%s,' "$(JsonString "$(ResolveRunProperty "slurm.partition" "default")")"
  printf '"qos":%s,' "$(JsonString "$(ResolveRunProperty "slurm.qos" "")")"
  printf '"account":%s,' "$(JsonString "$(ResolveRunProperty "slurm.account" "")")"
  printf '"constraint":%s,' "$(JsonString "$(ResolveRunProperty "slurm.constraint" "")")"
  printf '"timelimit":%s' "$(JsonString "$(ResolveRunProperty "slurm.install.timelimit" "")")"
  printf '}'
}

ProbeEmitParseJobSummary() {
  printf '{'
  printf '"type":"parse",'
  if [[ "$_system" == "slurm" && ${#EXPAND_JOB_KEYS[@]} -gt 0 ]]; then
    printf '"launcher":"slurm",'
    printf '"partition":%s,' "$(JsonString "$(ResolveRunProperty "slurm.partition" "default")")"
    printf '"qos":%s,' "$(JsonString "$(ResolveRunProperty "slurm.qos" "")")"
    printf '"account":%s,' "$(JsonString "$(ResolveRunProperty "slurm.account" "")")"
    printf '"constraint":%s,' "$(JsonString "$(ResolveRunProperty "slurm.constraint" "")")"
    printf '"timelimit":%s' "$(JsonString "$(ResolveRunProperty "parse.slurm.timelimit" "")")"
  else
    printf '"launcher":"local",'
    printf '"command":%s' "$(JsonString "$(ShellQuote "$MKEXP2_HOME/bin/mkexp2") parse")"
  fi
  printf '}'
}

ProbeEmitJobsSection() {
  local detailed="$1"
  local use_array=""
  local max_parallel=""
  local sep=""
  local job_key=""

  use_array=$(ResolveRunProperty "slurm.use_array" "false")
  max_parallel=$(ResolveRunProperty "slurm.array.max_parallel" "32")

  printf '{'
  printf '"summary":{'
  printf '"count":%s,' "${#EXPAND_JOB_KEYS[@]}"
  printf '"generated_topologies":%s' "$(ProbeEmitStringArray "${EXPAND_GENERATED_TOPOLOGIES[@]}")"
  printf '}'

  if (( detailed )); then
    printf ',"run_jobs":['
    for job_key in "${EXPAND_JOB_KEYS[@]}"; do
      local array_enabled="false"
      if [[ "$_system" == "slurm" && "$use_array" == "true" && ${EXPAND_JOB["$job_key::cmd_count"]} -gt 1 ]]; then
        array_enabled="true"
      fi
      printf '%s{' "$sep"
      printf '"key":%s,' "$(JsonString "$job_key")"
      printf '"job_name":%s,' "$(JsonString "${EXPAND_JOB["$job_key::job_name"]}")"
      printf '"launcher_job_name":%s,' "$(JsonString "${EXPAND_JOB["$job_key::launcher_job_name"]}")"
      printf '"system":%s,' "$(JsonString "${EXPAND_JOB["$job_key::system"]}")"
      printf '"topology":%s,' "$(JsonString "${EXPAND_JOB["$job_key::topology"]}")"
      printf '"nodes":%s,' "${EXPAND_JOB["$job_key::nodes"]}"
      printf '"mpis":%s,' "${EXPAND_JOB["$job_key::mpis"]}"
      printf '"threads":%s,' "${EXPAND_JOB["$job_key::threads"]}"
      printf '"distributed":%s,' "${EXPAND_JOB["$job_key::distributed"]}"
      printf '"command_count":%s,' "${EXPAND_JOB["$job_key::cmd_count"]}"
      printf '"dependency_key":%s,' "$(JsonString "${EXPAND_JOB["$job_key::dependency_key"]}")"
      printf '"cmd_file":%s,' "$(JsonString "${EXPAND_JOB["$job_key::cmd_file"]}")"
      printf '"job_script":%s,' "$(JsonString "${EXPAND_JOB["$job_key::job_script"]}")"
      printf '"array_enabled":%s,' "$array_enabled"
      printf '"array_max_parallel":%s' "$(JsonScalar "$max_parallel")"
      printf '}'
      sep=","
    done
    printf ']'
  fi

  if [[ "$_system" == "slurm" && "$EXPAND_SLURM_INSTALL_MODE" == "job" ]]; then
    printf ',"install_job":%s' "$(ProbeEmitInstallJobSummary)"
  fi
  if [[ "$EXPAND_PARSE_AUTO" == "true" ]]; then
    printf ',"parse_job":%s' "$(ProbeEmitParseJobSummary)"
  fi
  printf '}'
}

ProbeEmitCallsSection() {
  local sep=""
  local call_id=""

  printf '['
  for call_id in "${EXPAND_CALL_IDS[@]}"; do
    printf '%s{' "$sep"
    printf '"algorithm":%s,' "$(JsonString "${EXPAND_CALL["$call_id::algorithm"]}")"
    printf '"base":%s,' "$(JsonString "${EXPAND_CALL["$call_id::base"]}")"
    printf '"graph":%s,' "$(JsonString "${EXPAND_CALL["$call_id::graph"]}")"
    printf '"graph_name":%s,' "$(JsonString "${EXPAND_CALL["$call_id::graph_name"]}")"
    printf '"k":%s,' "$(JsonScalar "${EXPAND_CALL["$call_id::k"]}")"
    printf '"epsilon":%s,' "$(JsonScalar "${EXPAND_CALL["$call_id::epsilon"]}")"
    printf '"seed":%s,' "$(JsonScalar "${EXPAND_CALL["$call_id::seed"]}")"
    printf '"topology":%s,' "$(JsonString "${EXPAND_CALL["$call_id::topology"]}")"
    printf '"nodes":%s,' "${EXPAND_CALL["$call_id::nodes"]}"
    printf '"mpis":%s,' "${EXPAND_CALL["$call_id::mpis"]}"
    printf '"threads":%s,' "${EXPAND_CALL["$call_id::threads"]}"
    printf '"distributed":%s,' "${EXPAND_CALL["$call_id::distributed"]}"
    printf '"raw_command":%s,' "$(JsonString "${EXPAND_CALL["$call_id::raw_command"]}")"
    printf '"wrapped_command":%s,' "$(JsonString "${EXPAND_CALL["$call_id::wrapped_command"]}")"
    printf '"final_command":%s,' "$(JsonString "${EXPAND_CALL["$call_id::final_command"]}")"
    printf '"log_file":%s' "$(JsonString "${EXPAND_CALL["$call_id::log_file"]}")"
    printf '}'
    sep=","
  done
  printf ']'
}

ProbePrintExperimentList() {
  local -a experiment_functions=("$@")
  local sep=""
  local fn=""

  printf '{"experiments":['
  for fn in "${experiment_functions[@]}"; do
    printf '%s{"name":%s,"function":%s}' \
      "$sep" \
      "$(JsonString "$(DisplayExperimentName "$fn")")" \
      "$(JsonString "$fn")"
    sep=","
  done
  printf ']}'
}

ProbePrintPropertyValue() {
  local selector="$1"
  local algorithm="${selector%%.*}"
  local property="${selector#*.}"
  local base=""
  local -a keys=()

  if [[ -z "$algorithm" || -z "$property" || "$algorithm" == "$selector" ]]; then
    EchoFatal "invalid --property selector '$selector' (expected <algorithm>.<property>)"
    return 1
  fi
  if (( ${_algorithms[(Ie)$algorithm]} == 0 )); then
    EchoFatal "unknown algorithm '$algorithm' in --property selector"
    return 1
  fi

  base="${FLAT_ALGO_BASE["$algorithm"]:-}"
  if [[ -z "$base" ]]; then
    base=$(GetAlgorithmBase "$algorithm")
  fi
  keys=($(ProbeCollectAlgorithmPropertyKeys "$algorithm" "$base"))
  if (( ${keys[(Ie)$property]} == 0 )); then
    EchoFatal "unknown property '$property' for algorithm '$algorithm'"
    return 1
  fi

  printf '%s\n' "$(JsonScalar "$(ResolveAlgorithmProperty "$algorithm" "$property" "")")"
}

ProbePrintExperimentJson() {
  local experiment_file="$1"
  local include_algorithms=0
  local include_graphs=0
  local include_topologies=0
  local include_run_properties=0
  local include_jobs=0
  local include_calls=0
  local include_declared=0
  local include_matrix=0
  local aspect_count=0
  local sep=""

  aspect_count=$((MKEXP2_PROBE_ALGORITHMS + MKEXP2_PROBE_GRAPHS + MKEXP2_PROBE_TOPOLOGIES + MKEXP2_PROBE_RUN_PROPERTIES + MKEXP2_PROBE_JOBS + MKEXP2_PROBE_CALLS))

  if (( aspect_count == 0 )); then
    include_algorithms=1
    include_graphs=1
    include_topologies=1
    include_run_properties=1
    include_jobs=1
    include_declared=1
    include_matrix=1
  else
    include_algorithms=$MKEXP2_PROBE_ALGORITHMS
    include_graphs=$MKEXP2_PROBE_GRAPHS
    include_topologies=$MKEXP2_PROBE_TOPOLOGIES
    include_run_properties=$MKEXP2_PROBE_RUN_PROPERTIES
    include_jobs=$MKEXP2_PROBE_JOBS
    include_calls=$MKEXP2_PROBE_CALLS
  fi

  printf '{'
  printf '"experiment_file":%s,' "$(JsonString "$experiment_file")"
  printf '"experiment":{"name":%s,"function":%s,"system":%s}' \
    "$(JsonString "$EXPAND_EXPERIMENT_DISPLAY")" \
    "$(JsonString "$EXPAND_EXPERIMENT_NAME")" \
    "$(JsonString "$_system")"
  sep=","

  if (( include_declared )); then
    printf '%s"declared":%s' "$sep" "$(ProbeEmitDeclaredSection)"
    sep=","
  fi

  if (( include_algorithms || include_graphs || include_topologies || include_run_properties || include_matrix )); then
    printf '%s"resolved":%s' "$sep" "$(ProbeEmitResolvedSection "$include_algorithms" "$include_graphs" "$include_topologies" "$include_run_properties" "$include_matrix")"
    sep=","
  fi

  if (( include_jobs )); then
    local detailed_jobs=0
    if (( MKEXP2_PROBE_JOBS )); then
      detailed_jobs=1
    fi
    printf '%s"jobs":%s' "$sep" "$(ProbeEmitJobsSection "$detailed_jobs")"
    sep=","
  fi

  if (( include_calls )); then
    printf '%s"calls":%s' "$sep" "$(ProbeEmitCallsSection)"
  fi

  printf '}'
}

ProbeCommand() {
  local experiment_file="$1"
  shift
  local -a experiment_functions=("$@")
  local selected_fn=""

  if [[ -z "$MKEXP2_PROBE_TARGET" ]]; then
    ProbePrintExperimentList "${experiment_functions[@]}"
    printf '\n'
    return 0
  fi

  selected_fn=$(ProbeResolveExperimentFunction "$MKEXP2_PROBE_TARGET" "${experiment_functions[@]}") || return 1
  LoadExperimentFunctionState "$experiment_file" "$selected_fn"
  ExpandCurrentExperiment "$selected_fn" "probe"

  if [[ -n "$MKEXP2_PROBE_PROPERTY" ]]; then
    ProbePrintPropertyValue "$MKEXP2_PROBE_PROPERTY"
    return $?
  fi

  ProbePrintExperimentJson "$experiment_file"
  printf '\n'
}
