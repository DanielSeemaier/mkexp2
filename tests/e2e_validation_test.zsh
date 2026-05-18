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
    assert_cmd_fails "check --json fails for invalid closed-set property value" "$MKEXP2" check --json
    "$MKEXP2" check --json > check.json 2> check-json.err || true
    assert_eq "$(jq -r '.ok' check.json)" "false" "check --json reports failed status"
    assert_eq "$(jq -r '.errors' check.json)" "1" "check --json reports error count"
    assert_eq "$(jq -r '.experiments[0].messages[] | select(.severity == "error") | .severity' check.json)" "error" "check --json reports message severity"
    assert_contains "$(jq -r '.experiments[0].messages[] | select(.severity == "error") | .message' check.json)" "invalid AlgorithmProperty 'mode'" "check --json reports invalid algorithm property"
    assert_eq "$(<check-json.err)" "" "check --json keeps stderr empty"
  )

  cat > "$tmp/Experiment" <<'EOF'
System local

DefineAlgorithm Known TestHarness

ExperimentUnknown() {
  Algorithms Missing
  Graph missing_graph
  Ks 2
  Seeds 1
  Epsilons 0.03
  Threads 1x1x1
}
EOF

  (
    cd "$tmp"
    assert_cmd_fails "install fails for unknown algorithm with a fatal message" "$MKEXP2" install
    "$MKEXP2" install > install.out 2>&1 || true
    assert_file_contains install.out "unknown partitioner plugin 'Missing'" "install reports unknown algorithm"
  )

  (
    cd "$ROOT"
    "$MKEXP2" describe TestHarness > describe.out
    assert_file_contains describe.out "Partitioner: TestHarness" "describe shows test harness plugin"
    assert_file_contains describe.out "mode=baseline | values: baseline|debug|custom|stress (closed)" "describe prints closed-set defaults"
    assert_file_contains describe.out "TestHarness-Dbg" "describe prints plugin alias"
  )

  cat > "$tmp/Experiment" <<'EOF'
System local

DefineAlgorithm Known TestHarness

ExperimentProgress() {
  Algorithms Known
  Graph missing_graph
  Ks 2
  Seeds 1
  Epsilons 0.03
  Threads 1
}
EOF

  (
    cd "$tmp"
    "$MKEXP2" progress --json > progress.json
    assert_eq "$(jq -r '.ok' progress.json)" "true" "progress --json reports ok"
    assert_eq "$(jq -r '.complete' progress.json)" "false" "progress --json reports incomplete"
    assert_eq "$(jq -r '.experiments[0].algorithms[0].name' progress.json)" "Known" "progress --json reports algorithm name"
    assert_eq "$(jq -r '.experiments[0].algorithms[0].total' progress.json)" "1" "progress --json reports expected calls"
  )

  (
    cd "$tmp"
    cat > broken-install.md <<'EOF'
# mkexp2 install log

## `git`

```console
$ git checkout missing
error: pathspec 'missing' did not match any file(s) known to git
EOF
    source "$ROOT/bin/inc/state.sh"
    source "$ROOT/bin/inc/util.sh"
    MKEXP2_INSTALL_LOG_FILE="$tmp/broken-install.md"
    MKEXP2_INSTALL_LOG_INITIALIZED=""
    PrepareInstallLogFile
    assert_file_contains broken-install.md "Previous install log entry was interrupted" "install log repair records interrupted entry"
    local fence_count=""
    fence_count=$(awk '/^```/ { count += 1 } END { print count + 0 }' broken-install.md)
    assert_eq "$(( fence_count % 2 ))" "0" "install log repair balances markdown fences"
  )

  pass "check and describe"
}
