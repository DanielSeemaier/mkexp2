#!/usr/bin/env zsh

PartitionerDefaults_MtKaHIP() {
  SetPartitionerDefault "MtKaHIP" "repo_url" "https://github.com/DanielSeemaier/mt-KaHIP.git"
  SetPartitionerDefault "MtKaHIP" "repo_ref" "main"
  SetPartitionerDefault "MtKaHIP" "cmake_flags" ""
  SetPartitionerDefault "MtKaHIP" "supports_distributed" "false"
  SetPartitionerDefault "MtKaHIP" "use_openmp_env" "false"
  SetPartitionerDefault "MtKaHIP" "binary" "mtkahip"
  SetPartitionerDefault "MtKaHIP" "preconfiguration" "socialparallel"
  SetPartitionerDefault "MtKaHIP" "seed_flag" "--seed"
  SetPartitionerDefault "MtKaHIP" "epsilon_flag" "--imbalance"
}

PartitionerFetch_MtKaHIP() {
  GenericGitFetch "$CTX_repo_url" "$CTX_repo_ref" "$CTX_source_dir"
}

PartitionerBuild_MtKaHIP() {
  local -a cmake_args
  cmake_args=(
    -DCMAKE_BUILD_TYPE=Release
  )

  if [[ -n "$CTX_cmake_flags" ]]; then
    cmake_args+=( ${=CTX_cmake_flags} )
  fi
  if [[ -n "$CTX_build_opts" ]]; then
    cmake_args+=( ${=CTX_build_opts} )
  fi

  local -a build_parallel_args
  build_parallel_args=(--parallel)
  if [[ -n "$CTX_build_max_cores" ]]; then
    build_parallel_args+=("$CTX_build_max_cores")
  fi

  local binary_name=""
  binary_name=$(PartitionerProperty "binary" "mtkahip")

  Run cmake -S "$CTX_source_dir" -B "$CTX_source_dir/build" "${cmake_args[@]}"
  Run cmake --build "$CTX_source_dir/build" "${build_parallel_args[@]}"

  local candidate=""
  local -a candidates
  candidates=(
    "$CTX_source_dir/build/$binary_name"
    "$CTX_source_dir/build/deploy/$binary_name"
    "$CTX_source_dir/build/app/$binary_name"
    "$CTX_source_dir/build/apps/$binary_name"
    "$CTX_source_dir/deploy/$binary_name"
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

PartitionerInvoke_MtKaHIP() {
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
  local seed_flag=""
  local epsilon_flag=""
  preconfiguration=$(PartitionerProperty "preconfiguration" "socialparallel")
  seed_flag=$(PartitionerProperty "seed_flag" "--seed")
  epsilon_flag=$(PartitionerProperty "epsilon_flag" "--imbalance")

  local cmd=""
  cmd="${(q)RUN_binary_path}"
  cmd+=" ${(q)graph}"
  cmd+=" --k ${(q)RUN_k}"
  cmd+=" --num_threads=${(q)RUN_threads}"
  if [[ -n "$preconfiguration" ]]; then
    cmd+=" --preconfiguration=${(q)preconfiguration}"
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
