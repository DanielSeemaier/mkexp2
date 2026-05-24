#!/usr/bin/env zsh

PartitionerDefaults_KaHIP() {
  SetPartitionerDefault "KaHIP" "repo_url" "https://github.com/KaHIP/KaHIP.git" "any"
  SetPartitionerDefault "KaHIP" "repo_ref" "master" "any"
  SetPartitionerDefault "KaHIP" "cmake_flags" "" "any"
  SetPartitionerDefault "KaHIP" "supports_distributed" "false" "enum:true|false"
  SetPartitionerDefault "KaHIP" "use_openmp_env" "false" "enum:true|false"
  SetPartitionerDefault "KaHIP" "binary" "kaffpa" "any"
  SetPartitionerDefault "KaHIP" "build_backend" "auto" "enum:auto|cmake|compile_withcmake|compile_sh"
  SetPartitionerDefault "KaHIP" "preconfiguration" "eco" "enum:fast|eco|strong|fsocial|esocial|ssocial"
}

PartitionerAliases_KaHIP() {
  DefineAlgorithm KaHIP-Fast KaHIP
  AlgorithmProperty KaHIP-Fast "preconfiguration" "fast"

  DefineAlgorithm KaHIP-Eco KaHIP
  AlgorithmProperty KaHIP-Eco "preconfiguration" "eco"

  DefineAlgorithm KaHIP-Strong KaHIP
  AlgorithmProperty KaHIP-Strong "preconfiguration" "strong"

  DefineAlgorithm KaHIP-SocialFast KaHIP
  AlgorithmProperty KaHIP-SocialFast "preconfiguration" "fsocial"

  DefineAlgorithm KaHIP-SocialEco KaHIP
  AlgorithmProperty KaHIP-SocialEco "preconfiguration" "esocial"

  DefineAlgorithm KaHIP-SocialStrong KaHIP
  AlgorithmProperty KaHIP-SocialStrong "preconfiguration" "ssocial"
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

_KaHIPBuildViaCompileWithCMake() {
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

PartitionerBuild_KaHIP() {
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
      _KaHIPBuildViaCMake
      ;;
    compile_withcmake)
      if [[ ! -x "$CTX_source_dir/compile_withcmake.sh" ]]; then
        EchoFatal "KaHIP build_backend=compile_withcmake but '$CTX_source_dir/compile_withcmake.sh' is missing or not executable"
        exit 1
      fi
      _KaHIPBuildViaCompileWithCMake
      ;;
    compile_sh)
      if [[ ! -x "$CTX_source_dir/compile.sh" ]]; then
        EchoFatal "KaHIP build_backend=compile_sh but '$CTX_source_dir/compile.sh' is missing or not executable"
        exit 1
      fi
      _KaHIPBuildViaCompileScript
      ;;
    *)
      EchoFatal "invalid KaHIP build_backend '$backend' (expected auto|cmake|compile_withcmake|compile_sh)"
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

_KaHIPPercentImbalance() {
  local value=""
  printf -v value "%.12g" "$(( RUN_epsilon * 100.0 ))"
  echo "$value"
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
  local epsilon_percent=""
  preconfiguration=$(PartitionerProperty "preconfiguration" "")
  epsilon_percent=$(_KaHIPPercentImbalance)

  local cmd=""
  cmd="${(q)RUN_binary_path}"
  cmd+=" ${(q)graph}"
  cmd+=" --k ${(q)RUN_k}"
  cmd+=" --num_threads=${(q)RUN_threads}"
  cmd+=" --preconfiguration=${(q)preconfiguration}"
  cmd+=" --seed=${(q)RUN_seed}"
  cmd+=" --imbalance=${(q)epsilon_percent}"
  if [[ -n "$RUN_args" ]]; then
    cmd+=" $RUN_args"
  fi

  PARTITIONER_INVOKE_CMD="$cmd"
}
