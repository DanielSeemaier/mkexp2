#!/usr/bin/env zsh

PartitionerDefaults_TestHarness() {
  SetPartitionerDefault "TestHarness" "supports_distributed" "true" "enum:true|false"
  SetPartitionerDefault "TestHarness" "use_openmp_env" "false" "enum:true|false"
  SetPartitionerDefault "TestHarness" "mode" "baseline" "enum:baseline|debug|custom|stress"
  SetPartitionerDefault "TestHarness" "extra" "" "any"
}

PartitionerAliases_TestHarness() {
  DefineAlgorithm TestHarness-Dbg TestHarness --dbg
  AlgorithmProperty TestHarness-Dbg mode debug
}

PartitionerBuild_TestHarness() {
  mkdir -p "$(dirname "$CTX_binary_path")"

  cat > "$CTX_binary_path" <<'SCRIPT'
#!/usr/bin/env zsh
echo "test-harness: $*"
SCRIPT
  chmod +x "$CTX_binary_path"
}

PartitionerInvoke_TestHarness() {
  local mode=""
  local extra=""
  local cmd=""

  mode=$(PartitionerProperty "mode" "baseline")
  extra=$(PartitionerProperty "extra" "")

  cmd="${(q)RUN_binary_path}"
  cmd+=" --graph ${(q)RUN_graph}"
  cmd+=" --k ${(q)RUN_k}"
  cmd+=" --epsilon ${(q)RUN_epsilon}"
  cmd+=" --seed ${(q)RUN_seed}"
  cmd+=" --threads ${(q)RUN_threads}"
  cmd+=" --mode ${(q)mode}"
  if [[ -n "$extra" ]]; then
    cmd+=" --extra ${(q)extra}"
  fi
  if [[ -n "$RUN_args" ]]; then
    cmd+=" $RUN_args"
  fi

  PARTITIONER_INVOKE_CMD="$cmd"
}
