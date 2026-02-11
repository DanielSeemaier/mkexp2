#!/usr/bin/env zsh

PartitionerDefaults_dKaMinPar() {
  SetPartitionerDefault "dKaMinPar" "repo_url" "https://github.com/KaHIP/KaMinPar.git"
  SetPartitionerDefault "dKaMinPar" "repo_ref" "main"
  SetPartitionerDefault "dKaMinPar" "cmake_flags" ""
  SetPartitionerDefault "dKaMinPar" "supports_distributed" "true"
  SetPartitionerDefault "dKaMinPar" "use_openmp_env" "true"
}

PartitionerFetch_dKaMinPar() {
  GenericGitFetch "$CTX_repo_url" "$CTX_repo_ref" "$CTX_source_dir"
}

PartitionerBuild_dKaMinPar() {
  local -a cmake_args
  cmake_args=(
    -DCMAKE_BUILD_TYPE=Release
    -DKAMINPAR_BUILD_DISTRIBUTED=On
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

  Run cmake -S "$CTX_source_dir" -B "$CTX_source_dir/build" "${cmake_args[@]}"
  Run cmake --build "$CTX_source_dir/build" --target dKaMinPar "${build_parallel_args[@]}"
  Run cp "$CTX_source_dir/build/apps/dKaMinPar" "$CTX_binary_path"
}

PartitionerInvoke_dKaMinPar() {
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
  cmd+=" -T"
  if [[ -n "$RUN_args" ]]; then
    cmd+=" $RUN_args"
  fi
  echo "$cmd"
}
