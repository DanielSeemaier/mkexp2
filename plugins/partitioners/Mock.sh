#!/usr/bin/env zsh

PartitionerDefaults_Mock() {
  SetPartitionerDefault "Mock" "supports_distributed" "true"
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
  cmd="$(ShellQuote "$RUN_binary_path")"
  cmd+=" --graph $(ShellQuote "$RUN_graph")"
  cmd+=" --k $(ShellQuote "$RUN_k")"
  cmd+=" --epsilon $(ShellQuote "$RUN_epsilon")"
  cmd+=" --seed $(ShellQuote "$RUN_seed")"
  cmd+=" --threads $(ShellQuote "$RUN_threads")"
  if [[ -n "$RUN_args" ]]; then
    cmd+=" $RUN_args"
  fi
  echo "$cmd"
}
