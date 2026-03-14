#!/usr/bin/env zsh

test_probe_listing_and_selectors() {
  local tmp=""
  local by_name=""
  local by_function=""
  tmp=$(mktemp -d)
  cp "$ROOT/presets/Default" "$tmp/Experiment"

  (
    cd "$tmp"
    "$MKEXP2" probe > list.json
    assert_eq "$(json_value list.json '.experiments | length')" "2" "probe lists all experiments"
    assert_eq "$(json_value list.json '.experiments[0].name')" 'Baseline' "probe list includes display name"
    assert_eq "$(json_value list.json '.experiments[0].function')" 'ExperimentBaseline' "probe list includes function name"

    "$MKEXP2" probe Baseline > by-name.json
    "$MKEXP2" probe ExperimentBaseline > by-function.json
    by_name=$(jq -cS . by-name.json)
    by_function=$(jq -cS . by-function.json)
    assert_eq "$by_name" "$by_function" "display-name and function-name selectors resolve identically"

    assert_cmd_fails "unknown experiment fails" "$MKEXP2" probe Missing
    assert_cmd_fails "probe flags require a selector" "$MKEXP2" probe --algorithms
    assert_cmd_fails "malformed property selector fails" "$MKEXP2" probe Baseline --property malformed
  )

  pass "list mode and selectors"
}
