#!/usr/bin/env zsh

PartitionerDefaults_KaMinPar() {
  SetPartitionerDefault "KaMinPar" "repo_url" "https://github.com/KaHIP/KaMinPar.git" "any"
  SetPartitionerDefault "KaMinPar" "repo_ref" "main" "any"
  SetPartitionerDefault "KaMinPar" "cmake_flags" "" "any"
  SetPartitionerDefault "KaMinPar" "supports_distributed" "false" "enum:true|false"
  SetPartitionerDefault "KaMinPar" "use_openmp_env" "false" "enum:true|false"
  SetPartitionerDefault "KaMinPar" "build_target" "KaMinParApp" "any"
  SetPartitionerDefault "KaMinPar" "binary" "KaMinPar" "any"
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
  local binary=""
  build_target=$(PartitionerProperty "build_target")
  binary=$(PartitionerProperty "binary")

  Run cmake -S "$CTX_source_dir" -B "$CTX_source_dir/build" "${cmake_args[@]}"
  Run cmake --build "$CTX_source_dir/build" --target "$build_target" "${build_parallel_args[@]}"
  Run cp "$CTX_source_dir/build/apps/$binary" "$CTX_binary_path"
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
  cmd="${(q)RUN_binary_path}"
  cmd+=" -G ${(q)graph}"
  cmd+=" -k ${(q)RUN_k}"
  cmd+=" -e ${(q)RUN_epsilon}"
  cmd+=" --seed=${(q)RUN_seed}"
  cmd+=" -t ${(q)RUN_threads}"
  cmd+=" -v -T"
  if [[ -n "$RUN_args" ]]; then
    cmd+=" $RUN_args"
  fi
  PARTITIONER_INVOKE_CMD="$cmd"
}
