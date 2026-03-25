#!/usr/bin/env zsh

PartitionerDefaults_Jet() {
  SetPartitionerDefault "Jet" "repo_url" "https://github.com/sandialabs/Jet-Partitioner.git" "any"
  SetPartitionerDefault "Jet" "repo_ref" "main" "any"

  SetPartitionerDefault "Jet" "kokkos_version" "4.7.02" "any"
  SetPartitionerDefault "Jet" "kokkos_kernels_version" "4.7.02" "any"

  SetPartitionerDefault "Jet" "metis_repo_url" "https://github.com/KarypisLab/METIS.git" "any"
  SetPartitionerDefault "Jet" "metis_repo_ref" "master" "any"

  SetPartitionerDefault "Jet" "gklib_repo_url" "https://github.com/KarypisLab/GKlib.git" "any"
  SetPartitionerDefault "Jet" "gklib_repo_ref" "master" "any"

  SetPartitionerDefault "Jet" "supports_distributed" "false" "enum:true|false"
  SetPartitionerDefault "Jet" "use_openmp_env" "true" "enum:true|false"
  SetPartitionerDefault "Jet" "config_dir" ".jet/" "any"

  SetPartitionerDefault "Jet" "binary" "jet" "any"
}

PartitionerFetch_Jet() {
  repo_url=$(PartitionerProperty "repo_url")
  repo_ref=$(PartitionerProperty "repo_ref")
  GenericGitFetch "$repo_url" "$repo_ref" "$CTX_source_dir/jet"

  kokkos_version=$(PartitionerProperty "kokkos_version")
  if [[ ! -d "$CTX_source_dir/kokkos" ]]; then
    Run wget "https://github.com/kokkos/kokkos/releases/download/$kokkos_version/kokkos-$kokkos_version.tar.gz" \
        -P "$CTX_source_dir/"
    Run tar -xzf "$CTX_source_dir/kokkos-$kokkos_version.tar.gz" \
        -C "$CTX_source_dir/"
    Run mv "$CTX_source_dir/kokkos-$kokkos_version" "$CTX_source_dir/kokkos"
  fi

  kokkos_kernels_version=$(PartitionerProperty "kokkos_kernels_version")
  if [[ ! -d "$CTX_source_dir/kokkos-kernels" ]]; then
    Run wget "https://github.com/kokkos/kokkos-kernels/releases/download/$kokkos_kernels_version/kokkos-kernels-$kokkos_kernels_version.tar.gz" \
        -P "$CTX_source_dir/"
    Run tar -xzf "$CTX_source_dir/kokkos-kernels-$kokkos_kernels_version.tar.gz" \
        -C "$CTX_source_dir/"
    Run mv "$CTX_source_dir/kokkos-kernels-$kokkos_kernels_version" "$CTX_source_dir/kokkos-kernels"
  fi

  if [[ ! -d "$CTX_source_dir/metis" ]]; then
    metis_repo_url=$(PartitionerProperty "metis_repo_url")
    metis_repo_ref=$(PartitionerProperty "metis_repo_ref")
    GenericGitFetch "$metis_repo_url" "$metis_repo_ref" "$CTX_source_dir/metis"
  fi

  if [[ ! -d "$CTX_source_dir/gklib" ]]; then
    gklib_repo_url=$(PartitionerProperty "gklib_repo_url")
    gklib_repo_ref=$(PartitionerProperty "gklib_repo_ref")
    GenericGitFetch "$gklib_repo_url" "$gklib_repo_ref" "$CTX_source_dir/gklib"
  fi
}

PartitionerBuild_Jet() {
  local -x PATH="$CTX_usr_dir/bin:$PATH"
  local -x C_INCLUDE_PATH="$CTX_usr_dir/include:${C_INCLUDE_PATH:+:$C_INCLUDE_PATH}"
  local -x CPLUS_INCLUDE_PATH="$CTX_usr_dir/include:${CPLUS_INCLUDE_PATH:+:$CPLUS_INCLUDE_PATH}"
  local -x LIBRARY_PATH="$CTX_usr_dir/lib:${LIBRARY_PATH:+:$LIBRARY_PATH}"
  local -x LIBRARY_PATH="$CTX_usr_dir/lib64:${LIBRARY_PATH:+:$LIBRARY_PATH}"
  local -x LD_LIBRARY_PATH="$CTX_usr_dir/lib:${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  local -x LD_LIBRARY_PATH="$CTX_usr_dir/lib64:${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  local -x CMAKE_PREFIX_PATH="$CTX_usr_dir:${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"

  EchoStep "Installing GKlib"
  Run make -C "$CTX_source_dir/gklib" config prefix="$CTX_usr_dir"
  Run make -C "$CTX_source_dir/gklib" install -j ${CTX_build_max_cores:-}

  EchoStep "Installing METIS"
  Run make -C "$CTX_source_dir/metis" config prefix="$CTX_usr_dir"
  Run make -C "$CTX_source_dir/metis" install -j ${CTX_build_max_cores:-}

  EchoStep "Installing Kokkos"
  Run cmake -S "$CTX_source_dir/kokkos/" \
      -B "$CTX_source_dir/kokkos/build" \
      -G "Unix Makefiles" \
      -DCMAKE_BUILD_TYPE=Release \
      -DKokkos_ENABLE_OPENMP=On \
      -DKokkos_ENABLE_SERIAL=On \
      -DBUILD_TESTING=Off \
      -DCMAKE_INSTALL_PREFIX="$CTX_usr_dir"
  Run cmake --build "$CTX_source_dir/kokkos/build/" \
      --parallel ${CTX_build_max_cores:-}
  Run cmake --install "$CTX_source_dir/kokkos/build"

  EchoStep "Installing Kokkos Kernels"
  Run cmake -S "$CTX_source_dir/kokkos-kernels/" \
      -B "$CTX_source_dir/kokkos-kernels/build/" \
      -G "Unix Makefiles" \
      -DCMAKE_BUILD_TYPE=Release \
      -DBUILD_TESTING=Off \
      -DCMAKE_INSTALL_PREFIX="$CTX_usr_dir" \
      -DKokkos_DIR="$CTX_usr_dir"
  Run cmake --build "$CTX_source_dir/kokkos-kernels/build/" \
      --parallel ${CTX_build_max_cores:-}
  Run cmake --install "$CTX_source_dir/kokkos-kernels/build"

  binary=$(PartitionerProperty "binary")

  EchoStep "Installing Jet"
  Run cmake -S "$CTX_source_dir/jet" \
      -B "$CTX_source_dir/jet/build" \
      -G "Unix Makefiles" \
      -DCMAKE_BUILD_TYPE=Release \
      -DLINK_GKLIB=On
  Run cmake --build "$CTX_source_dir/jet/build/" \
      --parallel ${CTX_build_max_cores:-}
  Run cp "$CTX_source_dir/jet/build/app/$binary" "$CTX_binary_path"
}

PartitionerInvoke_Jet() {
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

  local config_dir=""
  local config_dir_path=""
  local global_binary=""
  local config=""

  config_dir=$(PartitionerProperty "config_dir")
  config_dir_path="${config_dir%/}"
  global_binary=$(PartitionerProperty "global_binary")

  config="${(q)config_dir_path}/${(q)RUN_k}.cfg"
  if (( ! MKEXP2_PROBE_MODE )); then
    if [[ ! -d "$config_dir" ]]; then
        echo "$config_dir" >> .gitignore
        mkdir -p "$config_dir"
    fi

    if [[ ! -f "$config" ]]; then
      echo "0" >> "$config"
      echo "${(q)RUN_k}" >> "$config"
      echo "1" >> "$config"
      echo "$((RUN_epsilon+1))" >> "$config"
      echo "0" >> "$config"
    fi
  fi


  local cmd="${(q)global_binary}"
  if [[ -n "$RUN_args" ]]; then
    cmd+=" $RUN_args"
  fi
  cmd+=" ${(q)graph}"
  cmd+=" ${(q)config}"
  PARTITIONER_INVOKE_CMD="$cmd"
}
