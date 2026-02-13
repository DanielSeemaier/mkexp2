#!/usr/bin/env zsh

PartitionerDefaults_Metis() {
  SetPartitionerDefault "Metis" "repo_url" "https://github.com/KarypisLab/METIS.git" "any"
  SetPartitionerDefault "Metis" "repo_ref" "master" "any"
  SetPartitionerDefault "Metis" "supports_distributed" "false" "enum:true|false"
  SetPartitionerDefault "Metis" "use_openmp_env" "false" "enum:true|false"
  SetPartitionerDefault "Metis" "binary" "gpmetis" "any"
  SetPartitionerDefault "Metis" "seed_flag" "-seed" "any"
  SetPartitionerDefault "Metis" "imbalance_flag" "-ufactor" "any"
  SetPartitionerDefault "Metis" "ufactor_scale" "1000" "integer>=1" "used when imbalance_flag is non-empty"
}

PartitionerFetch_Metis() {
  GenericGitFetch "$CTX_repo_url" "$CTX_repo_ref" "$CTX_source_dir"
}

PartitionerBuild_Metis() {
  local current_pwd="$PWD"
  local arch_dir=""
  local binary_name=""
  local -a config_args
  local -a make_args

  arch_dir="$(uname)-$(uname -m)"
  binary_name=$(PartitionerProperty "binary" "gpmetis")

  config_args=(shared=0 prefix="$CTX_source_dir/install")
  if [[ -n "$CTX_build_opts" ]]; then
    config_args+=( ${=CTX_build_opts} )
  fi

  make_args=(-j)
  if [[ -n "$CTX_build_max_cores" ]]; then
    make_args=(-j "$CTX_build_max_cores")
  fi

  cd "$CTX_source_dir"
  Run make config "${config_args[@]}"
  Run make "${make_args[@]}"

  local candidate=""
  local -a candidates
  candidates=(
    "$CTX_source_dir/build/$arch_dir/programs/$binary_name"
    "$CTX_source_dir/build/programs/$binary_name"
    "$CTX_source_dir/programs/$binary_name"
  )

  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      Run cp "$candidate" "$CTX_binary_path"
      cd "$current_pwd"
      return
    fi
  done

  cd "$current_pwd"
  EchoFatal "could not find built binary '$binary_name' in expected locations"
  EchoInfo "checked: ${candidates[*]}"
  exit 1
}

PartitionerInvoke_Metis() {
  local graph="$RUN_graph"
  if [[ -f "$RUN_graph.graph" ]]; then
    graph="$RUN_graph.graph"
  elif [[ -f "$RUN_graph.metis" ]]; then
    graph="$RUN_graph.metis"
  fi

  if [[ ! -f "$graph" ]]; then
    EchoWarn "graph file not found: $graph"
    return 1
  fi

  local seed_flag=""
  local imbalance_flag=""
  local ufactor_scale=""
  local ufactor="0"

  seed_flag=$(PartitionerProperty "seed_flag" "-seed")
  imbalance_flag=$(PartitionerProperty "imbalance_flag" "-ufactor")
  ufactor_scale=$(PartitionerProperty "ufactor_scale" "1000")
  if [[ "$ufactor_scale" != <-> ]] || (( ufactor_scale <= 0 )); then
    EchoFatal "Metis property 'ufactor_scale' must be a positive integer, got '$ufactor_scale'"
    exit 1
  fi
  printf -v ufactor "%.0f" "$((RUN_epsilon * ufactor_scale))"
  if (( ufactor < 1 )); then
    ufactor=1
  fi

  local cmd=""
  cmd="${(q)RUN_binary_path}"
  if [[ -n "$seed_flag" ]]; then
    cmd+=" ${seed_flag}=${(q)RUN_seed}"
  fi
  if [[ -n "$imbalance_flag" ]]; then
    cmd+=" ${imbalance_flag}=${(q)ufactor}"
  fi
  if [[ -n "$RUN_args" ]]; then
    cmd+=" $RUN_args"
  fi
  cmd+=" ${(q)graph}"
  cmd+=" ${(q)RUN_k}"

  PARTITIONER_INVOKE_CMD="$cmd"
}
