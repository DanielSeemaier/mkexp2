#!/usr/bin/env zsh

PartitionerDefaults_Mock() {
  SetPartitionerDefault "Mock" "supports_distributed" "true" "enum:true|false"
}

PartitionerBuild_Mock() {
  mkdir -p "$(dirname "$CTX_binary_path")"

  cat > "$CTX_binary_path" <<'SCRIPT'
#!/usr/bin/env zsh
echo "mock-partitioner: $*"
SCRIPT
  chmod +x "$CTX_binary_path"
}

PartitionerInvoke_Mock() {
  local cmd=""
  cmd="${(q)RUN_binary_path}"
  cmd+=" --graph ${(q)RUN_graph}"
  cmd+=" --k ${(q)RUN_k}"
  cmd+=" --epsilon ${(q)RUN_epsilon}"
  cmd+=" --seed ${(q)RUN_seed}"
  cmd+=" --threads ${(q)RUN_threads}"
  if [[ -n "$RUN_args" ]]; then
    cmd+=" $RUN_args"
  fi
  PARTITIONER_INVOKE_CMD="$cmd"
}
