#!/usr/bin/env zsh

test_probe_resolution_and_flags() {
  local tmp=""
  tmp=$(mktemp -d)
  mkdir -p "$tmp/graphs" "$tmp/parsers"
  : > "$tmp/graphs/demo.metis"
  cat > "$tmp/parsers/mock.awk" <<'EOF'
BEGIN { print "graph,k"; }
EOF
  cat > "$tmp/Experiment" <<'EOF'
System local
Property local.call_wrapper none
SystemProperty local.call_wrapper taskset
DefineAlgorithm MockFast Mock --fast-mode
AlgorithmProperty MockFast parser ./parsers/mock.awk
AlgorithmProperty MockFast use_openmp_env true

ExperimentInspect() {
  Algorithms MockFast
  Graph graphs/demo
  Ks 2
  Seeds 7
  Epsilons 0.05
  Threads 3 1x2x4
}
EOF

  (
    cd "$tmp"
    "$MKEXP2" probe Inspect > full.json
    assert_eq "$(json_value full.json '.resolved.algorithms[0].base')" 'Mock' "resolved algorithm base is included"
    assert_eq "$(json_value full.json '.resolved.algorithms[0].args')" '--fast-mode' "resolved algorithm args are inherited"
    assert_eq "$(json_value full.json '.resolved.algorithms[0].parser.found')" "true" "parser resolution reports found parser"
    assert_eq "$(json_value full.json '.resolved.algorithms[0].properties.use_openmp_env')" "true" "resolved algorithm property includes override"
    assert_eq "$(json_value full.json '.resolved.run_properties["local.call_wrapper"]')" 'taskset' "SystemProperty overrides Property"
    assert_eq "$(json_value full.json '.resolved.topologies[] | select(.spec=="1x2x4") | .distributed')" "true" "distributed topology is detected"
    assert_eq "$(json_value full.json '.resolved.graphs[0].resolved_path | endswith("graphs/demo.metis")')" "true" "graph metadata resolves extension candidates"

    "$MKEXP2" probe Inspect --algorithms > algorithms.json
    assert_eq "$(json_value algorithms.json 'has("declared")')" "false" "narrow algorithms output omits declared block"
    assert_eq "$(json_value algorithms.json '.resolved | keys | sort')" '["algorithms"]' "narrow algorithms output only contains algorithms"

    "$MKEXP2" probe Inspect --run-properties > run-properties.json
    assert_eq "$(json_value run-properties.json '.resolved | keys | sort')" '["run_properties"]' "narrow run-properties output only contains run properties"

    "$MKEXP2" probe Inspect --jobs > jobs.json
    assert_eq "$(json_value jobs.json 'has("resolved")')" "false" "jobs-only output omits resolved block"
    assert_eq "$(json_value jobs.json '.jobs.summary.count')" "2" "jobs-only output includes detailed jobs"
    assert_eq "$(json_value jobs.json '.jobs.run_jobs | length')" "2" "jobs-only output returns run job details"

    "$MKEXP2" probe Inspect --calls > calls.json
    assert_eq "$(json_value calls.json 'has("jobs")')" "false" "calls-only output omits jobs block"
    assert_eq "$(json_value calls.json '.calls | length')" "2" "calls-only output returns expanded calls"

    assert_eq "$("$MKEXP2" probe Inspect --property MockFast.use_openmp_env)" "true" "property probe returns JSON scalar"
  )

  pass "resolved model and narrow flags"
}
