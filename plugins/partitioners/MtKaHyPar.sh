#!/usr/bin/env zsh

PartitionerDefaults_MtKaHyPar() {
  SetPartitionerDefault "MtKaHyPar" "repo_url" "https://github.com/kahypar/mt-kahypar.git" "any"
  SetPartitionerDefault "MtKaHyPar" "repo_ref" "master" "any"
  SetPartitionerDefault "MtKaHyPar" "cmake_flags" "" "any"
  SetPartitionerDefault "MtKaHyPar" "supports_distributed" "false" "enum:true|false"
  SetPartitionerDefault "MtKaHyPar" "use_openmp_env" "false" "enum:true|false"
  SetPartitionerDefault "MtKaHyPar" "build_target" "MtKaHyPar" "any"
  SetPartitionerDefault "MtKaHyPar" "binary" "mt-kahypar/application/MtKaHyPar" "any"

  SetPartitionerDefault "MtKaHyPar" "objective" "km1" "enum:cut|km1|soed|steiner_tree"
  SetPartitionerDefault "MtKaHyPar" "file_format" "hmetis" "enum:hmetis|metis"
  SetPartitionerDefault "MtKaHyPar" "instance_type" "hypergraph" "enum:graph|hypergraph"
}

PartitionerAliases_MtKaHyPar() {
  DefineAlgorithm MtKaHyPar-H-Default MtKaHyPar --preset-type=default
  DefineAlgorithm MtKaHyPar-H-Quality MtKaHyPar --preset-type=quality
  DefineAlgorithm MtKaHyPar-H-HighestQuality MtKaHyPar --preset-type=highest_quality
  DefineAlgorithm MtKaHyPar-H-Deterministic MtKaHyPar --preset-type=deterministic
  DefineAlgorithm MtKaHyPar-H-DeterministicQuality MtKaHyPar --preset-type=deterministic_quality
  DefineAlgorithm MtKaHyPar-H-LargeK MtKaHyPar --preset-type=large_k

  for preset in Default Quality HighestQuality Deterministic DeterministicQuality LargeK; do
    DefineAlgorithm "MtKaHyPar-G-$preset" "MtKaHyPar-H-$preset"
    AlgorithmProperty "MtKaHyPar-G-$preset" "file_format" "metis"
    AlgorithmProperty "MtKaHyPar-G-$preset" "instance_type" "graph"
  done
}

PartitionerFetch_MtKaHyPar() {
  GenericGitFetch "$CTX_repo_url" "$CTX_repo_ref" "$CTX_source_dir"
}

PartitionerBuild_MtKaHyPar() {
  local -a cmake_args
  cmake_args=(
    --preset=default
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
  Run cp "$CTX_source_dir/build/$binary" "$CTX_binary_path"
}

PartitionerInvoke_MtKaHyPar() {
  local objective=$(PartitionerProperty "objective")
  local file_format=$(PartitionerProperty "file_format")
  local instance_type=$(PartitionerProperty "instance_type")

  local graph="$RUN_graph"
  if [[ -f "$RUN_graph.graph" ]]; then
    graph="$RUN_graph.graph"
  elif [[ -f "$RUN_graph.metis" ]]; then
    graph="$RUN_graph.metis"
  elif [[ -f "$RUN_graph.hmetis" ]]; then
    graph="$RUN_graph.hmetis"
  elif [[ -f "$RUN_graph.hgr" ]]; then
    graph="$RUN_graph.hgr"
  fi

  local cmd=""
  cmd="${(q)RUN_binary_path}"
  cmd+=" -h ${(q)graph}"
  cmd+=" -t ${(q)RUN_threads}"
  cmd+=" -k ${(q)RUN_k}"
  cmd+=" -e ${(q)RUN_epsilon}"
  cmd+=" --seed=${(q)RUN_seed}"
  cmd+=" -o $objective"
  cmd+=" --file-format=$file_format"
  cmd+=" --instance-type=$instance_type"
  if [[ -n "$RUN_args" ]]; then
    cmd+=" $RUN_args"
  fi
  PARTITIONER_INVOKE_CMD="$cmd"
}
