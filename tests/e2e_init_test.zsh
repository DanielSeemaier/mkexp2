#!/usr/bin/env zsh

test_e2e_init_and_discoverability() {
  local tmp=""
  tmp=$(mktemp -d)

  (
    cd "$tmp"
    git init -q
    "$MKEXP2" init Default > init.out

    assert_path_exists Experiment "init creates Experiment"
    assert_path_exists .gitignore "init creates .gitignore"
    assert_file_contains .gitignore ".mkexp2/" "init adds .mkexp2/ to .gitignore"
    assert_file_contains .gitignore "logs/*" "init ignores generated log contents"
    assert_file_contains .gitignore "!logs/install.md" "init leaves install log unignored"
    assert_file_contains .gitignore "slurm/" "init adds slurm/ to .gitignore"
    assert_file_not_contains .gitignore "plots.pdf" "init does not ignore plots.pdf"

    mkdir -p logs
    : > logs/install.md
    : > logs/run.log
    assert_cmd_fails "init does not ignore the install log" git check-ignore -q logs/install.md
    git check-ignore -q logs/run.log || fail "init ignores generated run logs"

    "$MKEXP2" --list-partitioners > partitioners.out
    assert_file_not_contains partitioners.out "TestHarness" "hidden test plugin is not listed by discoverability"

    "$MKEXP2" --list-presets > presets.out
    assert_file_contains presets.out "Default" "list-presets includes Default"
  )

  pass "init and discoverability"
}
