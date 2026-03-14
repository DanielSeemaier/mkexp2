#!/usr/bin/env zsh

test_e2e_init_and_discoverability() {
  local tmp=""
  tmp=$(mktemp -d)

  (
    cd "$tmp"
    "$MKEXP2" init Default > init.out

    assert_path_exists Experiment "init creates Experiment"
    assert_path_exists .gitignore "init creates .gitignore"
    assert_file_contains .gitignore ".mkexp2/" "init adds .mkexp2/ to .gitignore"
    assert_file_contains .gitignore "logs/" "init adds logs/ to .gitignore"

    "$MKEXP2" --list-partitioners > partitioners.out
    assert_file_contains partitioners.out "Mock" "list-partitioners includes Mock"
    assert_file_contains partitioners.out "TestHarness" "list-partitioners includes TestHarness test plugin"

    "$MKEXP2" --list-presets > presets.out
    assert_file_contains presets.out "Default" "list-presets includes Default"
  )

  pass "init and discoverability"
}
