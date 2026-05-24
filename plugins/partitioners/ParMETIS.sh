#!/usr/bin/env zsh

PartitionerDefaults_ParMETIS() {
  SetPartitionerDefault "ParMETIS" "repo_url" "https://github.com/KarypisLab/ParMETIS.git" "any"
  SetPartitionerDefault "ParMETIS" "repo_ref" "master" "any"
  SetPartitionerDefault "ParMETIS" "supports_distributed" "true" "enum:true|false"
  SetPartitionerDefault "ParMETIS" "use_openmp_env" "false" "enum:true|false"
  SetPartitionerDefault "ParMETIS" "binary" "parmetis" "any"
}

PartitionerFetch_ParMETIS() {
  GenericGitFetch "$CTX_repo_url" "$CTX_repo_ref" "$CTX_source_dir"
}

PartitionerBuild_ParMETIS() {
  local current_pwd="$PWD"
  local arch_dir=""
  local binary_name=""
  local -a config_args
  local -a make_args

  arch_dir="$(uname)-$(uname -m)"
  binary_name=$(PartitionerProperty "binary" "parmetis")

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
    "$CTX_source_dir/build"/*/programs/"$binary_name"(N)
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

PartitionerInvoke_ParMETIS() {
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

  local cmd=""
  cmd="${(q)RUN_binary_path}"
  if [[ -n "$RUN_args" ]]; then
    cmd+=" $RUN_args"
  fi
  cmd+=" ${(q)graph}"
  cmd+=" ${(q)RUN_k}"

  PARTITIONER_INVOKE_CMD="$cmd"
}
