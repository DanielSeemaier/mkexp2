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

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local message="$3"
  if [[ "$haystack" != *"$needle"* ]]; then
    echo "expected to find: $needle" >&2
    fail "$message"
  fi
}

assert_file_contains() {
  local file="$1"
  local needle="$2"
  local message="$3"
  assert_contains "$(<"$file")" "$needle" "$message"
}

assert_path_exists() {
  local path="$1"
  local message="$2"
  if [[ ! -e "$path" ]]; then
    fail "$message"
  fi
}

assert_line_count() {
  local file="$1"
  local expected="$2"
  local message="$3"
  local actual=""
  actual=$(wc -l < "$file" | tr -d ' ')
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
