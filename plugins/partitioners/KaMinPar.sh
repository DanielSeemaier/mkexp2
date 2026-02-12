#!/usr/bin/env zsh

PartitionerDefaults_KaMinPar() {
  SetPartitionerDefault "KaMinPar" "repo_url" "https://github.com/KaHIP/KaMinPar.git"
  SetPartitionerDefault "KaMinPar" "repo_ref" "main"
  SetPartitionerDefault "KaMinPar" "cmake_flags" ""
  SetPartitionerDefault "KaMinPar" "supports_distributed" "false"
  SetPartitionerDefault "KaMinPar" "use_openmp_env" "false"
  SetPartitionerDefault "KaMinPar" "build_target" "KaMinParApp"
}

PartitionerFetch_KaMinPar() {
  GenericGitFetch "$CTX_repo_url" "$CTX_repo_ref" "$CTX_source_dir"
}

PartitionerBuild_KaMinPar() {
  local -a cmake_args
  cmake_args=(
    -DCMAKE_BUILD_TYPE=Release
    -DKAMINPAR_BUILD_DISTRIBUTED=Off
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
  local build_target=""
  build_target=$(PartitionerProperty "build_target" "KaMinParApp")

  Run cmake -S "$CTX_source_dir" -B "$CTX_source_dir/build" "${cmake_args[@]}"
  Run cmake --build "$CTX_source_dir/build" --target "$build_target" "${build_parallel_args[@]}"
  Run cp "$CTX_source_dir/build/apps/KaMinPar" "$CTX_binary_path"
}

PartitionerInvoke_KaMinPar() {
  local graph="$RUN_graph"
  if [[ -f "$RUN_graph.graph" ]]; then
    graph="$RUN_graph.graph"
  elif [[ -f "$RUN_graph.metis" ]]; then
    graph="$RUN_graph.metis"
  elif [[ -f "$RUN_graph.parhip" ]]; then
    graph="$RUN_graph.parhip"
  fi

  local cmd=""
  cmd="$(ShellQuote "$RUN_binary_path")"
  cmd+=" -G $(ShellQuote "$graph")"
  cmd+=" -k $(ShellQuote "$RUN_k")"
  cmd+=" -e $(ShellQuote "$RUN_epsilon")"
  cmd+=" --seed=$(ShellQuote "$RUN_seed")"
  cmd+=" -t $(ShellQuote "$RUN_threads")"
  cmd+=" -v -T"
  if [[ -n "$RUN_args" ]]; then
    cmd+=" $RUN_args"
  fi
  echo "$cmd"
}
