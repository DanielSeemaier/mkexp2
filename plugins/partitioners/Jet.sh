#!/usr/bin/env zsh

PartitionerDefaults_Jet() {
  SetPartitionerDefault "Jet" "supports_distributed" "false" "enum:true|false"
  SetPartitionerDefault "Jet" "use_openmp_env" "true" "enum:true|false"
  SetPartitionerDefault "Jet" "global_binary" "$HOME/local.$(hostname)/bin/jet" "any"
  SetPartitionerDefault "Jet" "config_dir" ".jet/" "any"
}

PartitionerFetch_Jet() {
    return
}

PartitionerBuild_Jet() {
    return
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

  config_dir=$(PartitionerProperty "config_dir" ".jet/")
  config_dir_path="${config_dir%/}"
  global_binary=$(PartitionerProperty "global_binary" "$HOME/local.$(hostname)/bin/jet")

  if [[ ! -d "$config_dir" ]]; then
      echo "$config_dir" >> .gitignore
      mkdir -p "$config_dir"
  fi

  config="${(q)config_dir_path}/${(q)RUN_k}.cfg"
  if [[ ! -f "$config" ]]; then
    echo "0" >> "$config"
    echo "${(q)RUN_k}" >> "$config"
    echo "1" >> "$config"
    echo "$((RUN_epsilon+1))" >> "$config"
    echo "0" >> "$config"
  fi


  local cmd="${(q)global_binary}"
  if [[ -n "$RUN_args" ]]; then
    cmd+=" $RUN_args"
  fi
  cmd+=" ${(q)graph}"
  cmd+=" ${(q)config}"

  PARTITIONER_INVOKE_CMD="$cmd"
}
