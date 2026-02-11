#!/usr/bin/env zsh

MKEXP2_MODE="all"
MKEXP2_DO_INSTALL=1
MKEXP2_DO_GENERATE=1
MKEXP2_INIT_PRESET="Default"
MKEXP2_RUN_ID="$(date +%Y%m%d-%H%M%S)"
MKEXP2_BUILD_MAX_CORES=""

_system="local"

# User-facing experiment DSL state.
typeset -A ALG_DEF_BASE=()
typeset -A ALG_DEF_ARGS=()
typeset -A ALG_DEF_VERSION=()
typeset -A ALG_DEF_BUILD=()
typeset -A FLAT_ALGO_BASE=()
typeset -A FLAT_ALGO_ARGS=()
typeset -A FLAT_ALGO_VERSION=()
typeset -A FLAT_ALGO_BUILD=()

typeset -a _algorithms=()
typeset -a _threads=()
typeset -a _seeds=()
typeset -a _ks=()
typeset -a _epsilons=()
typeset -a _graphs=()
typeset -a _subexperiments=()

typeset -A PROP_GLOBAL=()
typeset -A PROP_SYSTEM=()
typeset -A PROP_ALGORITHM=()
typeset -A PROP_SUBEXPERIMENT=()

typeset -A PARTITIONER_DEFAULTS=()
typeset -A SYSTEM_DEFAULTS=()

typeset -A LOADED_PARTITIONERS=()
typeset -A LOADED_LAUNCHERS=()

typeset -A INSTALLED_BUILDS=()
typeset -A GENERATED_JOB_META=()
typeset -a GENERATED_JOB_KEYS=()

_timelimit=""
_timelimit_per_instance=""

MKEXP2_WORK_DIR="$PWD/.mkexp2"
MKEXP2_INSTALL_LOG_DIR="${MKEXP2_INSTALL_LOG_DIR:-}"
MKEXP2_RUN_VERBOSE="${MKEXP2_RUN_VERBOSE:-0}"
MKEXP2_INSTALL_COUNTER=0
MKEXP2_SLURM_INSTALL_JOB_REQUIRED=0
MKEXP2_SLURM_INSTALL_JOB_SCRIPT=""
MKEXP2_SLURM_INSTALL_JOB_KEY="__install__"

# Shared build context (avoids shell-specific nameref usage).
CTX_algorithm=""
CTX_base=""
CTX_subexp=""
CTX_args=""
CTX_build_opts=""
CTX_repo_url=""
CTX_repo_ref=""
CTX_cmake_flags=""
CTX_supports_distributed=""
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

ResetExperiment() {
  _system="local"

  ALG_DEF_BASE=()
  ALG_DEF_ARGS=()
  ALG_DEF_VERSION=()
  ALG_DEF_BUILD=()
  FLAT_ALGO_BASE=()
  FLAT_ALGO_ARGS=()
  FLAT_ALGO_VERSION=()
  FLAT_ALGO_BUILD=()

  _algorithms=()
  _threads=()
  _seeds=()
  _ks=()
  _epsilons=()
  _graphs=()
  _subexperiments=()

  PROP_GLOBAL=()
  PROP_SYSTEM=()
  PROP_ALGORITHM=()
  PROP_SUBEXPERIMENT=()

  PARTITIONER_DEFAULTS=()
  SYSTEM_DEFAULTS=()
  LOADED_PARTITIONERS=()
  LOADED_LAUNCHERS=()

  _timelimit=""
  _timelimit_per_instance=""

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
  if [[ ${#_subexperiments[@]} -eq 0 ]]; then
    _subexperiments=("main")
  fi
  if [[ -z "$_timelimit" ]]; then
    _timelimit="01:00:00"
  fi
}
