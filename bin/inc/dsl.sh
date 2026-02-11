#!/usr/bin/env zsh

System() {
  _system="$1"
}

Property() {
  local key="$1"
  shift
  PROP_GLOBAL["$key"]="$*"
}

SystemProperty() {
  local key="$1"
  shift
  PROP_SYSTEM["$key"]="$*"
}

AlgorithmProperty() {
  local algorithm="$1"
  local key="$2"
  shift 2
  PROP_ALGORITHM["$algorithm::$key"]="$*"
}

DefineAlgorithm() {
  local name="$1"
  local base="$2"
  shift 2

  ALG_DEF_BASE["$name"]="$base"
  ALG_DEF_ARGS["$name"]="$*"
  ALG_DEF_VERSION["$name"]=""
  ALG_DEF_BUILD["$name"]=""
}

DefineAlgorithmVersion() {
  local name="$1"
  local base="$2"
  local version="$3"

  ALG_DEF_BASE["$name"]="$base"
  ALG_DEF_ARGS["$name"]=""
  ALG_DEF_VERSION["$name"]="$version"
  ALG_DEF_BUILD["$name"]=""
}

DefineAlgorithmBuild() {
  local name="$1"
  local base="$2"
  shift 2

  ALG_DEF_BASE["$name"]="$base"
  ALG_DEF_ARGS["$name"]=""
  ALG_DEF_VERSION["$name"]=""
  ALG_DEF_BUILD["$name"]="$*"
}

Algorithms() {
  _algorithms+=("$@")
}

Threads() {
  _threads+=("$@")
}

Seeds() {
  _seeds+=("$@")
}

Ks() {
  _ks+=("$@")
}

Epsilons() {
  _epsilons+=("$@")
}

Timelimit() {
  _timelimit="$1"
}

TimelimitPerInstance() {
  _timelimit_per_instance="$1"
}

Graphs() {
  local dir="${1%/}"
  local ext="${2:-}"
  local filename=""

  if [[ -n "$ext" ]]; then
    for filename in "$dir"/*."$ext"(N); do
      _graphs+=("${filename%.*}")
    done
  else
    for filename in "$dir"/*(N); do
      [[ -f "$filename" ]] || continue
      _graphs+=("${filename%.*}")
    done
  fi
}

Graph() {
  _graphs+=("${1%.*}")
}

GetAlgorithmBase() {
  local algorithm="$1"
  if [[ -n "${ALG_DEF_BASE["$algorithm"]:-}" ]]; then
    GetAlgorithmBase "${ALG_DEF_BASE["$algorithm"]}"
  else
    echo "$algorithm"
  fi
}

GetAlgorithmArgs() {
  local algorithm="$1"
  if [[ -n "${ALG_DEF_BASE["$algorithm"]:-}" ]]; then
    local base="${ALG_DEF_BASE["$algorithm"]}"
    local inherited
    inherited=$(GetAlgorithmArgs "$base")
    echo "${ALG_DEF_ARGS["$algorithm"]:-} $inherited"
  else
    echo ""
  fi
}

GetAlgorithmVersion() {
  local algorithm="$1"
  if [[ -n "${ALG_DEF_BASE["$algorithm"]:-}" ]]; then
    if [[ -n "${ALG_DEF_VERSION["$algorithm"]:-}" ]]; then
      echo "${ALG_DEF_VERSION["$algorithm"]}"
    else
      GetAlgorithmVersion "${ALG_DEF_BASE["$algorithm"]}"
    fi
  else
    echo "latest"
  fi
}

GetAlgorithmBuildOptions() {
  local algorithm="$1"
  if [[ -n "${ALG_DEF_BASE["$algorithm"]:-}" ]]; then
    local inherited
    inherited=$(GetAlgorithmBuildOptions "${ALG_DEF_BASE["$algorithm"]}")
    echo "${ALG_DEF_BUILD["$algorithm"]:-} $inherited"
  else
    echo ""
  fi
}

FlattenAlgorithmHierarchy() {
  FLAT_ALGO_BASE=()
  FLAT_ALGO_ARGS=()
  FLAT_ALGO_VERSION=()
  FLAT_ALGO_BUILD=()

  local algorithm=""
  for algorithm in "${_algorithms[@]}"; do
    local base=""
    local args=""
    local version=""
    local build_opts=""

    base=$(GetAlgorithmBase "$algorithm")
    args=$(GetAlgorithmArgs "$algorithm")
    version=$(GetAlgorithmVersion "$algorithm")
    build_opts=$(GetAlgorithmBuildOptions "$algorithm")

    # Normalize whitespace once so we don't rework these strings during command generation.
    args="${(j: :)=args}"
    build_opts="${(j: :)=build_opts}"

    FLAT_ALGO_BASE["$algorithm"]="$base"
    FLAT_ALGO_ARGS["$algorithm"]="$args"
    FLAT_ALGO_VERSION["$algorithm"]="$version"
    FLAT_ALGO_BUILD["$algorithm"]="$build_opts"
  done
}
