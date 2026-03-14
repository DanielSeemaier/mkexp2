#!/usr/bin/env zsh

test_e2e_check_and_describe() {
  local tmp=""
  tmp=$(mktemp -d)

  cat > "$tmp/Experiment" <<'EOF'
System local

DefineAlgorithm TestHarness-Bad TestHarness --bad
AlgorithmProperty TestHarness-Bad mode impossible

ExperimentInvalid() {
  Algorithms TestHarness-Bad
  Graph missing_graph
  Ks 2
  Seeds 1
  Epsilons 0.03
  Threads 1x1x1
}
EOF

  (
    cd "$tmp"
    assert_cmd_fails "check fails for invalid closed-set property value" "$MKEXP2" check
    "$MKEXP2" check > check.out 2>&1 || true
    assert_file_contains check.out "invalid AlgorithmProperty 'mode'" "check reports invalid algorithm property"
  )

  (
    cd "$ROOT"
    "$MKEXP2" describe TestHarness > describe.out
    assert_file_contains describe.out "Partitioner: TestHarness" "describe shows test harness plugin"
    assert_file_contains describe.out "mode=baseline | values: baseline|debug|custom|stress (closed)" "describe prints closed-set defaults"
    assert_file_contains describe.out "TestHarness-Dbg" "describe prints plugin alias"
  )

  pass "check and describe"
}
