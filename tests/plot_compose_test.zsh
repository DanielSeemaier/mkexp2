#!/usr/bin/env zsh

. "$ROOT/bin/inc/util.sh"
. "$ROOT/bin/inc/plot.sh"

test_plot_compose_uses_keyed_image_and_small_context() {
  local tmp=""
  tmp=$(mktemp -d)

  local plots_dir="$tmp/plots"
  local results_dir="$tmp/results"
  local build_context="$tmp/.mkexp2/plots-image"
  local compose_file="$tmp/plots-compose.yml"
  local image=""

  mkdir -p "$plots_dir" "$results_dir"
  cat > "$plots_dir/Dockerfile" <<'EOF'
FROM scratch
WORKDIR /work
EOF

  image=$(_PlotsDockerImage "$plots_dir")
  _PreparePlotsBuildContext "$plots_dir" "$build_context"
  _WritePlotsComposeFile "$plots_dir" "$results_dir" "$tmp" "$image" "$build_context" "$compose_file"

  assert_file_contains "$compose_file" "image: $image" "plot compose pins a Dockerfile-keyed image tag"
  assert_file_contains "$compose_file" "context: $build_context" "plot compose uses generated build context"
  assert_file_not_contains "$compose_file" "context: $plots_dir" "plot compose does not use plots directory as build context"
  assert_file_contains "$compose_file" "$plots_dir:/work:ro" "plot compose still mounts plot scripts at runtime"
  assert_file_eq "$build_context/Dockerfile" "$plots_dir/Dockerfile" "plot build context contains Dockerfile"
  assert_file_contains "$build_context/.dockerignore" "*" "plot build context ignores all non-Dockerfile files"
  assert_file_contains "$build_context/.dockerignore" "!Dockerfile" "plot build context keeps Dockerfile"

  pass "plot compose image cache and build context"
}
