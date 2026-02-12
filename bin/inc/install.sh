#!/usr/bin/env zsh

PopulateBuildContext() {
  local algorithm="$1"

  local base=""
  base="${FLAT_ALGO_BASE["$algorithm"]:-}"
  if [[ -z "$base" ]]; then
    base=$(GetAlgorithmBase "$algorithm")
  fi
  LoadPartitionerPlugin "$base"

  local version=""
  version="${FLAT_ALGO_VERSION["$algorithm"]:-}"
  if [[ -z "$version" ]]; then
    version=$(GetAlgorithmVersion "$algorithm")
  fi
  local default_ref="$version"
  if [[ "$default_ref" == "latest" ]]; then
    default_ref="main"
  fi

  local inherited_build=""
  inherited_build="${FLAT_ALGO_BUILD["$algorithm"]:-}"
  if [[ -z "$inherited_build" ]]; then
    inherited_build=$(GetAlgorithmBuildOptions "$algorithm")
    inherited_build="${(j: :)=inherited_build}"
  fi
  local inherited_args=""
  inherited_args="${FLAT_ALGO_ARGS["$algorithm"]:-}"
  if [[ -z "$inherited_args" ]]; then
    inherited_args=$(GetAlgorithmArgs "$algorithm")
    inherited_args="${(j: :)=inherited_args}"
  fi

  CTX_algorithm="$algorithm"
  CTX_base="$base"
  CTX_args="$inherited_args"
  CTX_build_opts="$inherited_build"
  CTX_repo_url="$(ResolveAlgorithmProperty "$algorithm" repo_url "")"
  CTX_repo_ref="$(ResolveAlgorithmProperty "$algorithm" repo_ref "$default_ref")"
  CTX_cmake_flags="$(ResolveAlgorithmProperty "$algorithm" cmake_flags "")"
  CTX_supports_distributed="$(ResolveAlgorithmProperty "$algorithm" supports_distributed "false")"
  CTX_use_openmp_env="$(ResolveAlgorithmProperty "$algorithm" use_openmp_env "false")"
  CTX_build_max_cores="$MKEXP2_BUILD_MAX_CORES"
  if [[ -n "$CTX_build_max_cores" ]]; then
    if [[ "$CTX_build_max_cores" != <-> ]] || (( CTX_build_max_cores <= 0 )); then
      EchoFatal "--build-max-cores must be a positive integer, got '$CTX_build_max_cores'"
      exit 1
    fi
  fi

  local build_identity="${CTX_base}|${CTX_repo_url}|${CTX_repo_ref}|${CTX_build_opts}|${CTX_cmake_flags}"
  CTX_build_key="$(HashString "$build_identity")"
  CTX_source_dir="$MKEXP2_WORK_DIR/src/${CTX_base}-${CTX_build_key}"
  CTX_binary_path="$MKEXP2_WORK_DIR/bin/${CTX_base}-${CTX_build_key}"
}

InstallCurrentExperiment() {
  local experiment_name="$1"
  mkdir -p "$MKEXP2_WORK_DIR/src" "$MKEXP2_WORK_DIR/bin"
  mkdir -p "$PWD/logs/install"
  PrepareInstallLogDir

  EchoStep "Installing dependencies for $experiment_name"
  EchoInfo "logs: $MKEXP2_INSTALL_LOG_DIR"

  local algorithm=""
  for algorithm in "${_algorithms[@]}"; do
    PopulateBuildContext "$algorithm"

    if [[ -n "${INSTALLED_BUILDS["$CTX_build_key"]:-}" ]]; then
      local skip_tag
      skip_tag=$(_UiTag skip)
      echo "  $skip_tag $algorithm (already built in this run)"
      continue
    fi

    local build_tag
    build_tag=$(_UiTag build)
    echo "  $build_tag $algorithm"
    if [[ -n "$CTX_build_max_cores" ]]; then
      EchoInfo "build cores: $CTX_build_max_cores"
    else
      EchoInfo "build cores: all available"
    fi

    local fetch_fn="PartitionerFetch_${CTX_base}"
    local build_fn="PartitionerBuild_${CTX_base}"

    if FunctionExists "$fetch_fn"; then
      "$fetch_fn"
    fi
    if FunctionExists "$build_fn"; then
      "$build_fn"
    else
      EchoFatal "plugin ${CTX_base} is missing $build_fn"
      exit 1
    fi

    INSTALLED_BUILDS["$CTX_build_key"]="$CTX_binary_path"
  done
}
