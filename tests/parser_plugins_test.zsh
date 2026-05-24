#!/usr/bin/env zsh

run_parser_fixture() {
  local algorithm="$1"
  local fixture_dir="$2"
  local message="$3"
  local tmp=""

  tmp=$(mktemp -d)

  mkdir -p "$tmp/logs/$algorithm/ParserFixtures"
  cp "$fixture_dir"/*.log "$tmp/logs/$algorithm/ParserFixtures/"

  cat > "$tmp/Experiment" <<EOF
System local

ExperimentParserFixtures() {
  Algorithms $algorithm
}
EOF

  (
    cd "$tmp"
    "$MKEXP2" parse > parse.out
    assert_path_exists "results/${algorithm}.csv" "$message writes CSV"
    assert_file_eq "results/${algorithm}.csv" "$fixture_dir/expected.csv" "$message matches expected CSV"
  )
}

test_parser_mt_kahypar_example() {
  run_parser_fixture \
    "MtKaHyPar-G-Default" \
    "$ROOT/tests/fixtures/parsers/MtKaHyPar" \
    "Mt-KaHyPar parser"

  pass "Mt-KaHyPar parser fixture"
}

test_parser_kaminpar_example() {
  run_parser_fixture \
    "KaMinPar" \
    "$ROOT/tests/fixtures/parsers/KaMinPar" \
    "KaMinPar parser"

  pass "KaMinPar parser fixture"
}

test_parser_kahip_example() {
  run_parser_fixture \
    "KaHIP" \
    "$ROOT/tests/fixtures/parsers/KaHIP" \
    "KaHIP parser"

  pass "KaHIP parser fixture"
}

test_parser_parhip_example() {
  run_parser_fixture \
    "ParHIP" \
    "$ROOT/tests/fixtures/parsers/ParHIP" \
    "ParHIP parser"

  pass "ParHIP parser fixture"
}
