#!/usr/bin/env zsh

test_probe_listing_and_selectors() {
  local tmp=""
  local noexp=""
  local by_name=""
  local by_function=""
  tmp=$(mktemp -d)
  noexp=$(mktemp -d)

  (
    cd "$noexp"
    "$MKEXP2" probe --presets > presets-noexp.json
    assert_eq "$(json_value presets-noexp.json '.presets | map(.name) | index("Default") != null')" "true" "probe --presets works without an Experiment file"
  )

  cp "$ROOT/presets/Default" "$tmp/Experiment"

  (
    cd "$tmp"
    "$MKEXP2" probe > list.json
    assert_eq "$(json_value list.json '.experiments | length')" "2" "probe lists all experiments"
    assert_eq "$(json_value list.json '.experiments[0].name')" 'Baseline' "probe list includes display name"
    assert_eq "$(json_value list.json '.experiments[0].function')" 'ExperimentBaseline' "probe list includes function name"

    "$MKEXP2" probe --presets > presets.json
    assert_eq "$(json_value presets.json '.presets | map(.name) | index("Default") != null')" "true" "probe --presets lists bundled presets as JSON"

    "$MKEXP2" probe --all --algorithms > all-algorithms.json
    assert_eq "$(json_value all-algorithms.json '.experiments | length')" "2" "probe --all applies aspect flags to all experiments"
    assert_eq "$(json_value all-algorithms.json '.experiments[0].resolved.algorithms | length > 0')" "true" "probe --all --algorithms includes resolved algorithms"
    assert_eq "$(json_value all-algorithms.json '.experiments[0] | has("jobs")')" "false" "probe --all --algorithms avoids job expansion output"

    "$MKEXP2" probe Baseline > by-name.json
    "$MKEXP2" probe ExperimentBaseline > by-function.json
    by_name=$(jq -cS . by-name.json)
    by_function=$(jq -cS . by-function.json)
    assert_eq "$by_name" "$by_function" "display-name and function-name selectors resolve identically"

    assert_cmd_fails "unknown experiment fails" "$MKEXP2" probe Missing
    assert_cmd_fails "probe flags require a selector" "$MKEXP2" probe --algorithms
    assert_cmd_fails "probe --all rejects selector" "$MKEXP2" probe Baseline --all --algorithms
    assert_cmd_fails "preset probe rejects experiment selector" "$MKEXP2" probe Baseline --presets
    assert_cmd_fails "malformed property selector fails" "$MKEXP2" probe Baseline --property .broken
  )

  pass "list mode and selectors"
}
