#!/usr/bin/env zsh

_MtMetisResolveVersion() {
  local configured_version=""
  configured_version=$(PartitionerProperty "version" "")
  if [[ -n "$configured_version" ]]; then
    echo "$configured_version"
    return
  fi

  # mkexp2 defaults unresolved versions to "main"; keep mt-metis on its
  # historical release unless explicitly overridden.
  if [[ -n "$CTX_repo_ref" && "$CTX_repo_ref" != "main" && "$CTX_repo_ref" != "latest" ]]; then
    echo "$CTX_repo_ref"
  else
    echo "0.7.2"
  fi
}

PartitionerDefaults_MtMetis() {
  SetPartitionerDefault "MtMetis" "supports_distributed" "false"
  SetPartitionerDefault "MtMetis" "use_openmp_env" "false"
  SetPartitionerDefault "MtMetis" "tar_url" ""
  SetPartitionerDefault "MtMetis" "binary" "mtmetis"
  SetPartitionerDefault "MtMetis" "verbosity" "medium"
}

PartitionerFetch_MtMetis() {
  local version=""
  local tar_url=""
  local tar_path=""
  local source_root=""

  version=$(_MtMetisResolveVersion)
  source_root="$CTX_source_dir/mt-metis-$version"
  tar_url=$(PartitionerProperty "tar_url" "")
  if [[ -z "$tar_url" ]]; then
    tar_url="https://dlasalle.github.io/mt-metis/releases/mt-metis-$version.tar.gz"
  fi

  if [[ -d "$source_root" ]]; then
    EchoInfo "source directory already exists, skipping download: $source_root"
    return
  fi

  mkdir -p "$CTX_source_dir"
  tar_path="$CTX_source_dir/mtmetis.tar.gz"

  EchoStep "Downloading mt-metis $version"
  Run curl -L --fail -o "$tar_path" "$tar_url"
  Run tar -xzf "$tar_path" -C "$CTX_source_dir"
}

PartitionerBuild_MtMetis() {
  local version=""
  local source_root=""
  local arch_dir=""
  local binary_name=""
  local current_pwd="$PWD"
  local -a configure_args
  local -a make_args

  version=$(_MtMetisResolveVersion)
  source_root="$CTX_source_dir/mt-metis-$version"
  arch_dir="$(uname)-$(uname -m)"
  binary_name=$(PartitionerProperty "binary" "mtmetis")

  if [[ ! -d "$source_root" ]]; then
    EchoFatal "mt-metis source tree missing: $source_root"
    exit 1
  fi

  configure_args=()
  if [[ -n "$CTX_build_opts" ]]; then
    configure_args+=( ${=CTX_build_opts} )
  fi

  make_args=(-j)
  if [[ -n "$CTX_build_max_cores" ]]; then
    make_args=(-j "$CTX_build_max_cores")
  fi

  cd "$source_root"
  Run ./configure "${configure_args[@]}"

  cd "$source_root/build/$arch_dir"
  Run make "${make_args[@]}"
  Run cp "$source_root/build/$arch_dir/bin/$binary_name" "$CTX_binary_path"

  cd "$current_pwd"
}

PartitionerInvoke_MtMetis() {
  local graph="$RUN_graph"
  local imbalance=""
  local cmd=""

  if [[ -f "$RUN_graph.graph" ]]; then
    graph="$RUN_graph.graph"
  elif [[ -f "$RUN_graph.metis" ]]; then
    graph="$RUN_graph.metis"
  fi

  if [[ ! -f "$graph" ]]; then
    EchoWarn "graph file not found: $graph"
    return 1
  fi

  imbalance=$(awk -v e="$RUN_epsilon" 'BEGIN { printf "%.12g", 1 + e }')

  local verbosity=""
  verbosity=$(PartitionerProperty "verbosity" "medium")

  cmd="${(q)RUN_binary_path}"
  if [[ -n "$RUN_args" ]]; then
    cmd+=" $RUN_args"
  fi
  cmd+=" --seed=${(q)RUN_seed}"
  cmd+=" --verbosity=${(q)verbosity}"
  cmd+=" -T${(q)RUN_threads}"
  cmd+=" -C -t"
  cmd+=" -b${(q)imbalance}"
  cmd+=" ${(q)graph}"
  cmd+=" ${(q)RUN_k}"

  PARTITIONER_INVOKE_CMD="$cmd"
}
