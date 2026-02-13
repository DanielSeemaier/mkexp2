#!/usr/bin/env zsh

PartitionerDefaults_KaHIP() {
  SetPartitionerDefault "KaHIP" "repo_url" "https://github.com/KaHIP/KaHIP.git" "any"
  SetPartitionerDefault "KaHIP" "repo_ref" "master" "any"
  SetPartitionerDefault "KaHIP" "cmake_flags" "" "any"
  SetPartitionerDefault "KaHIP" "supports_distributed" "false" "enum:true|false"
  SetPartitionerDefault "KaHIP" "use_openmp_env" "false" "enum:true|false"
  SetPartitionerDefault "KaHIP" "binary" "kaffpa" "any"
  SetPartitionerDefault "KaHIP" "build_backend" "auto" "enum:auto|cmake|compile_sh"
  SetPartitionerDefault "KaHIP" "preconfiguration" "" "any"
  SetPartitionerDefault "KaHIP" "preconfiguration_flag" "--preconfiguration" "any"
  SetPartitionerDefault "KaHIP" "k_flag" "--k" "any"
  SetPartitionerDefault "KaHIP" "seed_flag" "--seed" "any"
  SetPartitionerDefault "KaHIP" "epsilon_flag" "--imbalance" "any"
  SetPartitionerDefault "KaHIP" "threads_flag" "--num_threads" "any"
}

PartitionerAliases_KaHIP() {
  DefineAlgorithm KaHIP-Eco KaHIP
  AlgorithmProperty KaHIP-Eco "preconfiguration" "eco"

  DefineAlgorithm KaHIP-Strong KaHIP
  AlgorithmProperty KaHIP-Strong "preconfiguration" "strong"
}

PartitionerFetch_KaHIP() {
  GenericGitFetch "$CTX_repo_url" "$CTX_repo_ref" "$CTX_source_dir"
}

_KaHIPBuildViaCMake() {
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

_KaHIPBuildViaCompileScript() {
  local current_pwd="$PWD"
  cd "$CTX_source_dir"
  if [[ -n "$CTX_build_opts" ]]; then
    Run ./compile.sh ${=CTX_build_opts}
  else
    Run ./compile.sh
  fi
  cd "$current_pwd"
}

PartitionerBuild_KaHIP() {
  local backend=""
  backend=$(PartitionerProperty "build_backend" "auto")

  if [[ "$backend" == "auto" ]]; then
    if [[ -x "$CTX_source_dir/compile.sh" ]]; then
      backend="compile_sh"
    else
      backend="cmake"
    fi
  fi

  case "$backend" in
    cmake)
      _KaHIPBuildViaCMake
      ;;
    compile_sh)
      if [[ ! -x "$CTX_source_dir/compile.sh" ]]; then
        EchoFatal "KaHIP build_backend=compile_sh but '$CTX_source_dir/compile.sh' is missing or not executable"
        exit 1
      fi
      _KaHIPBuildViaCompileScript
      ;;
    *)
      EchoFatal "invalid KaHIP build_backend '$backend' (expected auto|cmake|compile_sh)"
      exit 1
      ;;
  esac

  local binary_name=""
  binary_name=$(PartitionerProperty "binary" "kaffpa")

  local candidate=""
  local -a candidates
  candidates=(
    "$CTX_source_dir/build/$binary_name"
    "$CTX_source_dir/build/deploy/$binary_name"
    "$CTX_source_dir/build/app/$binary_name"
    "$CTX_source_dir/build/apps/$binary_name"
    "$CTX_source_dir/deploy/$binary_name"
    "$CTX_source_dir/$binary_name"
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

PartitionerInvoke_KaHIP() {
  local graph="$RUN_graph"
  if [[ -f "$RUN_graph.graph" ]]; then
    graph="$RUN_graph.graph"
  elif [[ -f "$RUN_graph.metis" ]]; then
    graph="$RUN_graph.metis"
  elif [[ -f "$RUN_graph.parhip" ]]; then
    graph="$RUN_graph.parhip"
  fi

  if [[ ! -f "$graph" ]]; then
    EchoWarn "graph file not found: $graph"
    return 1
  fi

  local preconfiguration=""
  local preconfiguration_flag=""
  local k_flag=""
  local seed_flag=""
  local epsilon_flag=""
  local threads_flag=""
  preconfiguration=$(PartitionerProperty "preconfiguration" "")
  preconfiguration_flag=$(PartitionerProperty "preconfiguration_flag" "--preconfiguration")
  k_flag=$(PartitionerProperty "k_flag" "--k")
  seed_flag=$(PartitionerProperty "seed_flag" "--seed")
  epsilon_flag=$(PartitionerProperty "epsilon_flag" "--imbalance")
  threads_flag=$(PartitionerProperty "threads_flag" "--num_threads")

  local cmd=""
  cmd="${(q)RUN_binary_path}"
  cmd+=" ${(q)graph}"
  if [[ -n "$k_flag" ]]; then
    cmd+=" ${k_flag} ${(q)RUN_k}"
  fi
  if [[ -n "$threads_flag" ]]; then
    cmd+=" ${threads_flag}=${(q)RUN_threads}"
  fi
  if [[ -n "$preconfiguration_flag" && -n "$preconfiguration" ]]; then
    cmd+=" ${preconfiguration_flag}=${(q)preconfiguration}"
  fi
  if [[ -n "$seed_flag" ]]; then
    cmd+=" ${seed_flag}=${(q)RUN_seed}"
  fi
  if [[ -n "$epsilon_flag" ]]; then
    cmd+=" ${epsilon_flag}=${(q)RUN_epsilon}"
  fi
  if [[ -n "$RUN_args" ]]; then
    cmd+=" $RUN_args"
  fi

  PARTITIONER_INVOKE_CMD="$cmd"
}
