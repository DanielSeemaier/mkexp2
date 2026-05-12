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

    "$MKEXP2" probe Inspect --property MockFast > property-map.json
    assert_eq "$(json_value property-map.json '.use_openmp_env')" "true" "algorithm-only property probe returns property map"
    assert_eq "$(json_value property-map.json '.parser')" './parsers/mock.awk' "algorithm-only property probe includes resolved parser value"
    assert_eq "$("$MKEXP2" probe Inspect --property MockFast.use_openmp_env)" "true" "property probe returns JSON scalar"
  )

  pass "resolved model and narrow flags"
}

test_probe_algorithm_property_inheritance_chain() {
  local tmp=""
  tmp=$(mktemp -d)
  mkdir -p "$tmp/graphs"
  : > "$tmp/graphs/demo.metis"
  cat > "$tmp/Experiment" <<'EOF'
System local
DefineAlgorithm Baseline TestHarness

DefineAlgorithm Opt-v1 TestHarness
AlgorithmProperty Opt-v1 repo_ref origin/codex/optimize-label-propagation-hotpath
AlgorithmProperty Opt-v1 mode custom

DefineAlgorithm Opt-v1-Light Opt-v1 --c-lp-fast-mode light
DefineAlgorithm Opt-v1-Aggressive Opt-v1 --c-lp-fast-mode aggressive
AlgorithmProperty Opt-v1-Aggressive repo_ref origin/codex/aggressive-override

ExperimentInheritedProperties() {
  Algorithms Baseline Opt-v1-Light Opt-v1-Aggressive
  Graph graphs/demo
  Ks 2
  Seeds 1
  Epsilons 0.03
  Threads 1
}
EOF

  (
    cd "$tmp"
    "$MKEXP2" probe InheritedProperties > inherited.json
    assert_eq "$(json_value inherited.json '.resolved.algorithms[] | select(.name=="Baseline") | .properties.repo_ref')" "" "baseline does not inherit sibling repo_ref"
    assert_eq "$(json_value inherited.json '.resolved.algorithms[] | select(.name=="Opt-v1-Light") | .properties.repo_ref')" "origin/codex/optimize-label-propagation-hotpath" "child algorithm inherits parent repo_ref"
    assert_eq "$(json_value inherited.json '.resolved.algorithms[] | select(.name=="Opt-v1-Light") | .properties.mode')" "custom" "child algorithm inherits parent non-core property"
    assert_eq "$(json_value inherited.json '.resolved.algorithms[] | select(.name=="Opt-v1-Aggressive") | .properties.repo_ref')" "origin/codex/aggressive-override" "child algorithm property overrides parent repo_ref"
    assert_eq "$("$MKEXP2" probe InheritedProperties --property Opt-v1-Light.repo_ref)" '"origin/codex/optimize-label-propagation-hotpath"' "property probe returns inherited parent repo_ref"
  )

  pass "algorithm property inheritance follows DefineAlgorithm chain"
}
