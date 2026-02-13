#!/usr/bin/env zsh

MKEXP2_MODE="all"
MKEXP2_DO_INSTALL=1
MKEXP2_DO_GENERATE=1
MKEXP2_DO_PARSE=0
MKEXP2_DO_CHECK=0
MKEXP2_DO_DESCRIBE=0
MKEXP2_INIT_PRESET="Default"
MKEXP2_RUN_ID="$(date +%Y%m%d-%H%M%S)"
MKEXP2_BUILD_MAX_CORES=""
MKEXP2_DESCRIBE_PARTITIONER=""
MKEXP2_DESCRIBE_TARGET=""
MKEXP2_DESCRIBE_KIND=""
MKEXP2_LIST_SYSTEMS=0
MKEXP2_LIST_PARTITIONERS=0
MKEXP2_LIST_PRESETS=0
MKEXP2_LIST_PARSERS=0
MKEXP2_PARSE_AUTO_REQUIRED=0
MKEXP2_CHECK_ERROR_COUNT=0
MKEXP2_CHECK_WARN_COUNT=0

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

typeset -A LOADED_PARTITIONERS=()
typeset -A LOADED_LAUNCHERS=()

typeset -A INSTALLED_BUILDS=()
typeset -A GENERATED_JOB_META=()
typeset -a GENERATED_JOB_KEYS=()
typeset -A PARSE_ALGO_PARSER=()
typeset -a PARSE_ALGOS=()

_timelimit=""
_timelimit_per_instance=""

MKEXP2_WORK_DIR="$PWD/.mkexp2"
MKEXP2_INSTALL_LOG_DIR="${MKEXP2_INSTALL_LOG_DIR:-}"
MKEXP2_RUN_VERBOSE="${MKEXP2_RUN_VERBOSE:-0}"
MKEXP2_INSTALL_COUNTER=0
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
  LOADED_PARTITIONERS=()
  LOADED_LAUNCHERS=()

  _timelimit=""
  _timelimit_per_instance=""
  PARTITIONER_INVOKE_CMD=""
  LAUNCHER_WRAPPED_CMD=""

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
