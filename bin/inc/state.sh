#!/usr/bin/env zsh

MKEXP2_MODE="all"
MKEXP2_DO_INSTALL=1
MKEXP2_DO_GENERATE=1
MKEXP2_DO_PARSE=0
MKEXP2_DO_CHECK=0
MKEXP2_DO_DESCRIBE=0
MKEXP2_DO_PROBE=0
MKEXP2_DO_PLOT=0
MKEXP2_INIT_PRESET="Default"
MKEXP2_RUN_ID="$(date +%Y%m%d-%H%M%S)"
MKEXP2_BUILD_MAX_CORES=""
MKEXP2_DESCRIBE_PARTITIONER=""
MKEXP2_DESCRIBE_TARGET=""
MKEXP2_DESCRIBE_KIND=""
MKEXP2_PROBE_TARGET=""
MKEXP2_PROBE_ALGORITHMS=0
MKEXP2_PROBE_GRAPHS=0
MKEXP2_PROBE_TOPOLOGIES=0
MKEXP2_PROBE_RUN_PROPERTIES=0
MKEXP2_PROBE_JOBS=0
MKEXP2_PROBE_CALLS=0
MKEXP2_PROBE_PROPERTY=""
MKEXP2_PROBE_MODE=0
MKEXP2_LIST_SYSTEMS=0
MKEXP2_LIST_PARTITIONERS=0
MKEXP2_LIST_PRESETS=0
MKEXP2_LIST_PARSERS=0
MKEXP2_PARSE_AUTO_REQUIRED=0
MKEXP2_CHECK_ERROR_COUNT=0
MKEXP2_CHECK_WARN_COUNT=0
typeset -a MKEXP2_PLOT_ALGORITHMS=()
MKEXP2_PLOT_PERFORMANCE_PROFILE=0
MKEXP2_PLOT_SPEEDUP=0
MKEXP2_PLOT_RUNNING_TIME=0
MKEXP2_PLOT_EXPLICIT_SELECTION=0

_system="local"

# User-facing experiment DSL state.
typeset -A ALG_DEF_BASE=()
typeset -A ALG_DEF_ARGS=()
typeset -A FLAT_ALGO_BASE=()
typeset -A FLAT_ALGO_ARGS=()

typeset -a _algorithms=()
typeset -a _threads=()
typeset -a _seeds=()
typeset -a _ks=()
typeset -a _epsilons=()
typeset -a _graphs=()

typeset -A PROP_GLOBAL=()
typeset -A PROP_SYSTEM=()
typeset -A PROP_ALGORITHM=()

typeset -A PARTITIONER_DEFAULTS=()
typeset -A SYSTEM_DEFAULTS=()
typeset -A PARTITIONER_PROP_ALLOWED=()
typeset -A PARTITIONER_PROP_WHEN=()
typeset -A SYSTEM_PROP_ALLOWED=()
typeset -A SYSTEM_PROP_WHEN=()

typeset -A LOADED_PARTITIONERS=()
typeset -A LOADED_LAUNCHERS=()

typeset -A INSTALLED_BUILDS=()
typeset -A GENERATED_JOB_META=()
typeset -a GENERATED_JOB_KEYS=()
typeset -A PARSE_ALGO_PARSER=()
typeset -a PARSE_ALGOS=()
typeset -a EXPAND_CALL_IDS=()
typeset -A EXPAND_CALL=()
typeset -a EXPAND_JOB_KEYS=()
typeset -A EXPAND_JOB=()
typeset -A EXPAND_CALL_COUNT_BY_ALGORITHM=()
typeset -A EXPAND_CALL_COUNT_BY_TOPOLOGY=()
typeset -a EXPAND_GENERATED_TOPOLOGIES=()
typeset -a EXPAND_ALGORITHM_LABELS=()
typeset -a EXPAND_PARTITIONERS=()
typeset -a EXPAND_GRAPH_NAMES=()

EXPAND_EXPERIMENT_NAME=""
EXPAND_EXPERIMENT_LABEL=""
EXPAND_EXPERIMENT_DISPLAY=""
EXPAND_TIMEOUT_PREFIX=""
EXPAND_TIMELIMIT=""
EXPAND_WRAP_FN=""
EXPAND_TOTAL_CALLS=0
EXPAND_SLURM_INSTALL_MODE=""
EXPAND_PARSE_AUTO=""

_timelimit=""
_timelimit_per_instance=""

MKEXP2_WORK_DIR="$PWD/.mkexp2"
MKEXP2_INSTALL_LOG_DIR="${MKEXP2_INSTALL_LOG_DIR:-}"
MKEXP2_RUN_VERBOSE="${MKEXP2_RUN_VERBOSE:-0}"
MKEXP2_INSTALL_COUNTER=0
MKEXP2_LAST_INSTALL_LOG_FILE=""
MKEXP2_ACTIVE_ALGORITHM=""
MKEXP2_SLURM_INSTALL_JOB_REQUIRED=0
MKEXP2_SLURM_INSTALL_JOB_SCRIPT=""
MKEXP2_SLURM_INSTALL_JOB_KEY="__install__"
MKEXP2_SLURM_HAS_RUN_JOBS=0
MKEXP2_SLURM_PARSE_JOB_SCRIPT=""

# Shared build context (avoids shell-specific nameref usage).
CTX_algorithm=""
CTX_base=""
CTX_args=""
CTX_build_opts=""
CTX_repo_url=""
CTX_repo_ref=""
CTX_cmake_flags=""
CTX_supports_distributed=""
CTX_use_openmp_env=""
CTX_build_max_cores=""
CTX_build_key=""
CTX_source_dir=""
CTX_binary_path=""

# Shared run context for per-instance invocation.
RUN_algorithm=""
RUN_base=""
RUN_binary_path=""
RUN_args=""
RUN_graph=""
RUN_k=""
RUN_epsilon=""
RUN_seed=""
RUN_nodes=""
RUN_mpis=""
RUN_threads=""

# Shared command output buffers to avoid per-instance subshell command substitution
# in the generate hot path.
PARTITIONER_INVOKE_CMD=""
LAUNCHER_WRAPPED_CMD=""

ResetExperiment() {
  _system="local"

  ALG_DEF_BASE=()
  ALG_DEF_ARGS=()
  FLAT_ALGO_BASE=()
  FLAT_ALGO_ARGS=()

  _algorithms=()
  _threads=()
  _seeds=()
  _ks=()
  _epsilons=()
  _graphs=()

  PROP_GLOBAL=()
  PROP_SYSTEM=()
  PROP_ALGORITHM=()

  PARTITIONER_DEFAULTS=()
  SYSTEM_DEFAULTS=()
  PARTITIONER_PROP_ALLOWED=()
  PARTITIONER_PROP_WHEN=()
  SYSTEM_PROP_ALLOWED=()
  SYSTEM_PROP_WHEN=()
  LOADED_PARTITIONERS=()
  LOADED_LAUNCHERS=()

  _timelimit=""
  _timelimit_per_instance=""
  MKEXP2_ACTIVE_ALGORITHM=""

  CTX_algorithm=""
  CTX_base=""
  CTX_args=""
  CTX_build_opts=""
  CTX_repo_url=""
  CTX_repo_ref=""
  CTX_cmake_flags=""
  CTX_supports_distributed=""
  CTX_use_openmp_env=""
  CTX_build_max_cores=""
  CTX_build_key=""
  CTX_source_dir=""
  CTX_binary_path=""

  RUN_algorithm=""
  RUN_base=""
  RUN_binary_path=""
  RUN_args=""
  RUN_graph=""
  RUN_k=""
  RUN_epsilon=""
  RUN_seed=""
  RUN_nodes=""
  RUN_mpis=""
  RUN_threads=""

  PARTITIONER_INVOKE_CMD=""
  LAUNCHER_WRAPPED_CMD=""
  EXPAND_CALL_IDS=()
  EXPAND_CALL=()
  EXPAND_JOB_KEYS=()
  EXPAND_JOB=()
  EXPAND_CALL_COUNT_BY_ALGORITHM=()
  EXPAND_CALL_COUNT_BY_TOPOLOGY=()
  EXPAND_GENERATED_TOPOLOGIES=()
  EXPAND_ALGORITHM_LABELS=()
  EXPAND_PARTITIONERS=()
  EXPAND_GRAPH_NAMES=()
  EXPAND_EXPERIMENT_NAME=""
  EXPAND_EXPERIMENT_LABEL=""
  EXPAND_EXPERIMENT_DISPLAY=""
  EXPAND_TIMEOUT_PREFIX=""
  EXPAND_TIMELIMIT=""
  EXPAND_WRAP_FN=""
  EXPAND_TOTAL_CALLS=0
  EXPAND_SLURM_INSTALL_MODE=""
  EXPAND_PARSE_AUTO=""

  # Keep logging settings stable across Experiment* blocks inside one run.
  MKEXP2_INSTALL_COUNTER=0
}

EnsureExperimentDefaults() {
  if [[ ${#_threads[@]} -eq 0 ]]; then
    _threads=("1x1x1")
  fi
  if [[ ${#_seeds[@]} -eq 0 ]]; then
    _seeds=("1")
  fi
  if [[ ${#_ks[@]} -eq 0 ]]; then
    _ks=("2")
  fi
  if [[ ${#_epsilons[@]} -eq 0 ]]; then
    _epsilons=("0.03")
  fi
}
