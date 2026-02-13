#!/usr/bin/env zsh

LoadPartitionerPlugin() {
  local base="$1"
  if [[ -n "${LOADED_PARTITIONERS["$base"]:-}" ]]; then
    return
  fi

  local plugin_file="$MKEXP2_HOME/plugins/partitioners/$base.sh"
  if [[ ! -f "$plugin_file" ]]; then
    EchoFatal "unknown partitioner plugin '$base' ($plugin_file not found)"
    exit 1
  fi

  . "$plugin_file"
  LOADED_PARTITIONERS["$base"]=1

  local defaults_fn="PartitionerDefaults_${base}"
  if FunctionExists "$defaults_fn"; then
    "$defaults_fn"
  fi
}

LoadPartitionerAliasHooks() {
  local plugin_file=""
  local base=""
  local alias_fn=""

  for plugin_file in "$MKEXP2_HOME/plugins/partitioners/"*.sh(N); do
    base="${plugin_file:t:r}"
    . "$plugin_file"

    alias_fn="PartitionerAliases_${base}"
    if FunctionExists "$alias_fn"; then
      "$alias_fn"
    fi
  done
}

LoadLauncherPlugin() {
  local launcher="$1"
  if [[ -n "${LOADED_LAUNCHERS["$launcher"]:-}" ]]; then
    return
  fi

  local plugin_file="$MKEXP2_HOME/plugins/launchers/$launcher.sh"
  if [[ ! -f "$plugin_file" ]]; then
    EchoFatal "unknown launcher plugin '$launcher' ($plugin_file not found)"
    exit 1
  fi

  . "$plugin_file"
  LOADED_LAUNCHERS["$launcher"]=1

  local defaults_fn="LauncherDefaults_${launcher}"
  if FunctionExists "$defaults_fn"; then
    "$defaults_fn"
  fi
}
