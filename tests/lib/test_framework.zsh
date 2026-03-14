#!/usr/bin/env zsh

ROOT=$(cd "$(dirname "${(%):-%N}")/../.." && pwd)
MKEXP2="$ROOT/bin/mkexp2"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required for tests" >&2
  exit 1
fi

TEST_COUNT=0

fail() {
  echo "not ok - $1" >&2
  exit 1
}

pass() {
  TEST_COUNT=$((TEST_COUNT + 1))
  echo "ok $TEST_COUNT - $1"
}

assert_eq() {
  local actual="$1"
  local expected="$2"
  local message="$3"
  if [[ "$actual" != "$expected" ]]; then
    echo "expected: $expected" >&2
    echo "actual:   $actual" >&2
    fail "$message"
  fi
}

assert_file_eq() {
  local actual_file="$1"
  local expected_file="$2"
  local message="$3"
  local actual=""
  local expected=""
  actual=$(<"$actual_file")
  expected=$(<"$expected_file")
  assert_eq "$actual" "$expected" "$message"
}

assert_cmd_fails() {
  local message="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    fail "$message"
  fi
}

json_value() {
  local file="$1"
  local filter="$2"
  jq -cer "$filter" "$file"
}
