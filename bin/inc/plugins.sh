#!/usr/bin/env zsh

ResolvePartitionerPluginFile() {
  local base="$1"
  local visible_file="$MKEXP2_HOME/plugins/partitioners/$base.sh"
  local hidden_file="$MKEXP2_HOME/plugins/partitioners/.$base.sh"

  if [[ -f "$visible_file" ]]; then
    echo "$visible_file"
    return 0
  fi
  if [[ -f "$hidden_file" ]]; then
    echo "$hidden_file"
    return 0
  fi

  return 1
}

LoadPartitionerPlugin() {
  local base="$1"
  if [[ -n "${LOADED_PARTITIONERS["$base"]:-}" ]]; then
    return
  fi

  local plugin_file=""
  plugin_file=$(ResolvePartitionerPluginFile "$base")
  if [[ -z "$plugin_file" ]]; then
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

  for plugin_file in "$MKEXP2_HOME/plugins/partitioners/"*.sh(N) "$MKEXP2_HOME/plugins/partitioners"/.*.sh(N); do
    base="${plugin_file:t:r}"
    base="${base#.}"
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

DescribePartitioner() {
  local base="$1"
  if [[ -z "$base" ]]; then
    EchoFatal "describe requires a partitioner name, e.g. 'mkexp2 describe MtKaHIP'"
    return 1
  fi

  local plugin_file=""
  plugin_file=$(ResolvePartitionerPluginFile "$base")
  if [[ -z "$plugin_file" ]]; then
    EchoFatal "unknown partitioner '$base' ($plugin_file not found)"
    return 1
  fi

  ResetExperiment
  . "$plugin_file"

  local defaults_fn="PartitionerDefaults_${base}"
  local alias_fn="PartitionerAliases_${base}"
  local fetch_fn="PartitionerFetch_${base}"
  local build_fn="PartitionerBuild_${base}"
  local invoke_fn="PartitionerInvoke_${base}"
  local describe_fn="PartitionerDescribe_${base}"

  if FunctionExists "$defaults_fn"; then
    "$defaults_fn"
  fi

  EchoStep "Partitioner: $base"
  EchoInfo "plugin: $plugin_file"

  local -a hooks=()
  if FunctionExists "$defaults_fn"; then hooks+=("defaults"); fi
  if FunctionExists "$alias_fn"; then hooks+=("aliases"); fi
  if FunctionExists "$fetch_fn"; then hooks+=("fetch"); fi
  if FunctionExists "$build_fn"; then hooks+=("build"); fi
  if FunctionExists "$invoke_fn"; then hooks+=("invoke"); fi
  if FunctionExists "$describe_fn"; then hooks+=("describe"); fi
  EchoInfo "hooks: ${(j:, :)hooks}"

  local -a default_lines=()
  local key=""
  for key in ${(k)PARTITIONER_DEFAULTS}; do
    key="${key#\"}"
    key="${key%\"}"
    if [[ "$key" != "${base}::"* ]]; then
      continue
    fi
    local prop="${key#${base}::}"
    local line="$prop=${PARTITIONER_DEFAULTS["$key"]}"
    local allowed="${PARTITIONER_PROP_ALLOWED["$key"]:-any}"
    local when_note="${PARTITIONER_PROP_WHEN["$key"]:-}"
    if [[ "$allowed" == enum:* ]]; then
      line+=" | values: ${allowed#enum:} (closed)"
    else
      line+=" | values: $allowed"
    fi
    if [[ -n "$when_note" ]]; then
      line+=" | when: $when_note"
    fi
    default_lines+=("$line")
  done

  if (( ${#default_lines[@]} > 0 )); then
    default_lines=("${(@on)default_lines}")
    EchoInfo "defaults:"
    local line=""
    for line in "${default_lines[@]}"; do
      echo "    - $line"
    done
  else
    EchoInfo "defaults: (none)"
  fi

  local -a aliases=()
  if FunctionExists "$alias_fn"; then
    "$alias_fn"
    local alias_name=""
    for alias_name in ${(k)ALG_DEF_BASE}; do
      alias_name="${alias_name#\"}"
      alias_name="${alias_name%\"}"
      if [[ "${ALG_DEF_BASE["$alias_name"]:-}" == "$base" ]]; then
        aliases+=("$alias_name")
      fi
    done
  fi

  if (( ${#aliases[@]} == 0 )); then
    EchoInfo "aliases: (none)"
  else
    aliases=("${(@on)aliases}")
    EchoInfo "aliases:"
    local alias_name=""
    for alias_name in "${aliases[@]}"; do
      echo "    - $alias_name"

      local alias_args="${ALG_DEF_ARGS["$alias_name"]:-}"

      if [[ -n "$alias_args" ]]; then
        echo "      args: $alias_args"
      fi

      local -a alias_props=()
      for key in ${(k)PROP_ALGORITHM}; do
        key="${key#\"}"
        key="${key%\"}"
        if [[ "$key" == "${alias_name}::"* ]]; then
          local prop_key="${key#${alias_name}::}"
          alias_props+=("$prop_key=${PROP_ALGORITHM["$key"]}")
        fi
      done
      if (( ${#alias_props[@]} > 0 )); then
        alias_props=("${(@on)alias_props}")
        local prop_line=""
        for prop_line in "${alias_props[@]}"; do
          echo "      property: $prop_line"
        done
      fi
    done
  fi

  if FunctionExists "$describe_fn"; then
    "$describe_fn"
  fi
}

DescribeSystem() {
  local launcher="$1"
  if [[ -z "$launcher" ]]; then
    EchoFatal "describe requires a system name, e.g. 'mkexp2 describe local --system'"
    return 1
  fi

  local plugin_file="$MKEXP2_HOME/plugins/launchers/$launcher.sh"
  if [[ ! -f "$plugin_file" ]]; then
    EchoFatal "unknown system '$launcher' ($plugin_file not found)"
    return 1
  fi

  ResetExperiment
  . "$plugin_file"

  local defaults_fn="LauncherDefaults_${launcher}"
  local wrap_fn="LauncherWrapCommand_${launcher}"
  local write_fn="LauncherWriteJob_${launcher}"
  local describe_fn="LauncherDescribe_${launcher}"

  if FunctionExists "$defaults_fn"; then
    "$defaults_fn"
  fi

  EchoStep "System: $launcher"
  EchoInfo "plugin: $plugin_file"

  local -a hooks=()
  if FunctionExists "$defaults_fn"; then hooks+=("defaults"); fi
  if FunctionExists "$wrap_fn"; then hooks+=("wrap"); fi
  if FunctionExists "$write_fn"; then hooks+=("write"); fi
  if FunctionExists "$describe_fn"; then hooks+=("describe"); fi
  EchoInfo "hooks: ${(j:, :)hooks}"

  local -a default_lines=()
  local key=""
  for key in ${(k)SYSTEM_DEFAULTS}; do
    key="${key#\"}"
    key="${key%\"}"
    local line="$key=${SYSTEM_DEFAULTS["$key"]}"
    local allowed="${SYSTEM_PROP_ALLOWED["$key"]:-any}"
    local when_note="${SYSTEM_PROP_WHEN["$key"]:-}"
    if [[ "$allowed" == enum:* ]]; then
      line+=" | values: ${allowed#enum:} (closed)"
    else
      line+=" | values: $allowed"
    fi
    if [[ -n "$when_note" ]]; then
      line+=" | when: $when_note"
    fi
    default_lines+=("$line")
  done

  if (( ${#default_lines[@]} > 0 )); then
    default_lines=("${(@on)default_lines}")
    EchoInfo "defaults:"
    local line=""
    for line in "${default_lines[@]}"; do
      echo "    - $line"
    done
  else
    EchoInfo "defaults: (none)"
  fi

  if FunctionExists "$describe_fn"; then
    "$describe_fn"
  fi
}

DescribePlugin() {
  local name="$1"
  local kind="${2:-}"

  local part_file=""
  local sys_file="$MKEXP2_HOME/plugins/launchers/$name.sh"
  local has_part=0
  local has_system=0

  part_file=$(ResolvePartitionerPluginFile "$name" || true)
  [[ -n "$part_file" ]] && has_part=1
  [[ -f "$sys_file" ]] && has_system=1

  case "$kind" in
    partitioner)
      if (( ! has_part )); then
        EchoFatal "unknown partitioner '$name' ($part_file not found)"
        return 1
      fi
      DescribePartitioner "$name"
      return $?
      ;;
    system)
      if (( ! has_system )); then
        EchoFatal "unknown system '$name' ($sys_file not found)"
        return 1
      fi
      DescribeSystem "$name"
      return $?
      ;;
    "")
      if (( has_part && has_system )); then
        EchoFatal "'$name' matches both a partitioner and a system plugin"
        EchoInfo "use one of:"
        echo "    mkexp2 describe $name --partitioner"
        echo "    mkexp2 describe $name --system"
        return 1
      fi
      if (( has_part )); then
        DescribePartitioner "$name"
        return $?
      fi
      if (( has_system )); then
        DescribeSystem "$name"
        return $?
      fi
      EchoFatal "unknown plugin '$name' (not found in partitioners or launchers)"
      return 1
      ;;
    *)
      EchoFatal "invalid describe kind '$kind' (expected 'partitioner' or 'system')"
      return 1
      ;;
  esac
}
