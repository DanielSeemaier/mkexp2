#!/usr/bin/env zsh

PartitionerDefaults_ParHIP() {
  SetPartitionerDefault "ParHIP" "repo_url" "https://github.com/KaHIP/KaHIP.git" "any"
  SetPartitionerDefault "ParHIP" "repo_ref" "master" "any"
  SetPartitionerDefault "ParHIP" "cmake_flags" "" "any"
  SetPartitionerDefault "ParHIP" "supports_distributed" "true" "enum:true|false"
  SetPartitionerDefault "ParHIP" "use_openmp_env" "false" "enum:true|false"
  SetPartitionerDefault "ParHIP" "binary" "parhip" "any"
  SetPartitionerDefault "ParHIP" "build_backend" "auto" "enum:auto|cmake|compile_withcmake|compile_sh"
  SetPartitionerDefault "ParHIP" "preconfiguration" "fastmesh" "enum:fastmesh|ecomesh|ultrafastmesh|fastsocial|ecosocial|ultrafastsocial"
}

PartitionerAliases_ParHIP() {
  DefineAlgorithm ParHIP-Fast ParHIP
  AlgorithmProperty ParHIP-Fast "preconfiguration" "fastmesh"

  DefineAlgorithm ParHIP-Eco ParHIP
  AlgorithmProperty ParHIP-Eco "preconfiguration" "ecomesh"

  DefineAlgorithm ParHIP-SocialFast ParHIP
  AlgorithmProperty ParHIP-SocialFast "preconfiguration" "fastsocial"

  DefineAlgorithm ParHIP-SocialEco ParHIP
  AlgorithmProperty ParHIP-SocialEco "preconfiguration" "ecosocial"
}

PartitionerFetch_ParHIP() {
  GenericGitFetch "$CTX_repo_url" "$CTX_repo_ref" "$CTX_source_dir"
}

_ParHIPBuildViaCMake() {
  local -a cmake_args
  local -a build_parallel_args

  cmake_args=(-DCMAKE_BUILD_TYPE=Release)
  if [[ -n "$CTX_cmake_flags" ]]; then
    cmake_args+=( ${=CTX_cmake_flags} )
  fi
  if [[ -n "$CTX_build_opts" ]]; then
    cmake_args+=( ${=CTX_build_opts} )
  fi

  build_parallel_args=(--parallel)
  if [[ -n "$CTX_build_max_cores" ]]; then
    build_parallel_args+=("$CTX_build_max_cores")
  fi

  Run cmake -S "$CTX_source_dir" -B "$CTX_source_dir/build" "${cmake_args[@]}"
  Run cmake --build "$CTX_source_dir/build" "${build_parallel_args[@]}"
}

_ParHIPBuildViaCompileScript() {
  local current_pwd="$PWD"
  cd "$CTX_source_dir"
  if [[ -n "$CTX_build_opts" ]]; then
    Run ./compile.sh ${=CTX_build_opts}
  else
    Run ./compile.sh
  fi
  cd "$current_pwd"
}

_ParHIPBuildViaCompileWithCMake() {
  local current_pwd="$PWD"
  local -a compile_cmd
  local -a extra_args

  compile_cmd=()
  if [[ -n "$CTX_build_max_cores" ]]; then
    compile_cmd=(env "NCORES=$CTX_build_max_cores")
  fi
  compile_cmd+=("./compile_withcmake.sh")

  extra_args=()
  if [[ -n "$CTX_cmake_flags" ]]; then
    extra_args+=( ${=CTX_cmake_flags} )
  fi
  if [[ -n "$CTX_build_opts" ]]; then
    extra_args+=( ${=CTX_build_opts} )
  fi

  cd "$CTX_source_dir"
  Run "${compile_cmd[@]}" "${extra_args[@]}"
  cd "$current_pwd"
}

PartitionerBuild_ParHIP() {
  local backend=""
  backend=$(PartitionerProperty "build_backend" "auto")

  if [[ "$backend" == "auto" ]]; then
    if [[ -x "$CTX_source_dir/compile_withcmake.sh" ]]; then
      backend="compile_withcmake"
    elif [[ -x "$CTX_source_dir/compile.sh" ]]; then
      backend="compile_sh"
    else
      backend="cmake"
    fi
  fi

  case "$backend" in
    cmake)
      _ParHIPBuildViaCMake
      ;;
    compile_withcmake)
      if [[ ! -x "$CTX_source_dir/compile_withcmake.sh" ]]; then
        EchoFatal "ParHIP build_backend=compile_withcmake but '$CTX_source_dir/compile_withcmake.sh' is missing or not executable"
        exit 1
      fi
      _ParHIPBuildViaCompileWithCMake
      ;;
    compile_sh)
      if [[ ! -x "$CTX_source_dir/compile.sh" ]]; then
        EchoFatal "ParHIP build_backend=compile_sh but '$CTX_source_dir/compile.sh' is missing or not executable"
        exit 1
      fi
      _ParHIPBuildViaCompileScript
      ;;
    *)
      EchoFatal "invalid ParHIP build_backend '$backend' (expected auto|cmake|compile_withcmake|compile_sh)"
      exit 1
      ;;
  esac

  local binary_name=""
  binary_name=$(PartitionerProperty "binary" "parhip")

  local candidate=""
  local -a candidates
  candidates=(
    "$CTX_source_dir/build/$binary_name"
    "$CTX_source_dir/build/deploy/$binary_name"
    "$CTX_source_dir/build/app/$binary_name"
    "$CTX_source_dir/build/apps/$binary_name"
    "$CTX_source_dir/deploy/$binary_name"
    "$CTX_source_dir/$binary_name"
    "$CTX_source_dir/build/parallel/parallel_src/$binary_name"
    "$CTX_source_dir"/*/"$binary_name"(N)
  )

  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      Run cp "$candidate" "$CTX_binary_path"
      return
    fi
  done

  EchoFatal "could not find built binary '$binary_name' in expected locations"
  EchoInfo "checked: ${candidates[*]}"
  exit 1
}

_ParHIPPercentImbalance() {
  local value=""
  printf -v value "%.12g" "$(( RUN_epsilon * 100.0 ))"
  echo "$value"
}

PartitionerInvoke_ParHIP() {
  local graph="$RUN_graph"
  if [[ -f "$RUN_graph.parhip" ]]; then
    graph="$RUN_graph.parhip"
  elif [[ -f "$RUN_graph.graph" ]]; then
    graph="$RUN_graph.graph"
  elif [[ -f "$RUN_graph.metis" ]]; then
    graph="$RUN_graph.metis"
  fi

  if [[ ! -f "$graph" ]]; then
    EchoWarn "graph file not found: $graph"
    return 1
  fi

  local preconfiguration=""
  local epsilon_percent=""
  preconfiguration=$(PartitionerProperty "preconfiguration" "")
  epsilon_percent=$(_ParHIPPercentImbalance)

  local cmd=""
  cmd="${(q)RUN_binary_path}"
  cmd+=" ${(q)graph}"
  cmd+=" --k ${(q)RUN_k}"
  cmd+=" --preconfiguration=${(q)preconfiguration}"
  cmd+=" --seed=${(q)RUN_seed}"
  cmd+=" --imbalance=${(q)epsilon_percent}"
  if [[ -n "$RUN_args" ]]; then
    cmd+=" $RUN_args"
  fi

  PARTITIONER_INVOKE_CMD="$cmd"
}
