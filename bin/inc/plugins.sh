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
  plugin_file=$(ResolvePartitionerPluginFile "$base" || true)
  if [[ -z "$plugin_file" ]]; then
    EchoFatal "unknown partitioner plugin '$base'"
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
  plugin_file=$(ResolvePartitionerPluginFile "$base" || true)
  if [[ -z "$plugin_file" ]]; then
    EchoFatal "unknown partitioner '$base'"
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
      if [[ "$(GetAlgorithmBase "$alias_name")" == "$base" ]]; then
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

      local alias_args=""
      alias_args=$(GetAlgorithmArgs "$alias_name")

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

DescribeCleanKey() {
  local key="$1"
  key="${key#\"}"
  key="${key%\"}"
  printf '%s' "$key"
}

DescribePartitionerNames() {
  local file=""
  local -a names=()
  for file in "$MKEXP2_HOME/plugins/partitioners/"*.sh(N); do
    names+=("${file:t:r}")
  done
  names=("${(@on)names}")
  print -r -l -- "${names[@]}"
}

DescribeSystemNames() {
  local file=""
  local -a names=()
  for file in "$MKEXP2_HOME/plugins/launchers/"*.sh(N); do
    names+=("${file:t:r}")
  done
  names=("${(@on)names}")
  print -r -l -- "${names[@]}"
}

DescribeEmitHooksJson() {
  local sep=""
  local hook=""
  printf '['
  for hook in "$@"; do
    printf '%s%s' "$sep" "$(JsonString "$hook")"
    sep=","
  done
  printf ']'
}

DescribeEmitPropertiesJson() {
  local owner="$1"
  local source="${2:-partitioner}"
  local -a keys=()
  local key=""
  local sep=""

  if [[ "$source" == "partitioner" ]]; then
    keys=("${(@k)PARTITIONER_DEFAULTS}")
  else
    keys=("${(@k)SYSTEM_DEFAULTS}")
  fi
  keys=("${(@on)keys}")
  keys=("${(@Q)keys}")

  printf '['
  for key in "${keys[@]}"; do
    key=$(DescribeCleanKey "$key")
    local prop="$key"
    local value=""
    local allowed="any"
    local when_note=""
    if [[ "$source" == "partitioner" ]]; then
      [[ "$key" == "${owner}::"* ]] || continue
      prop="${key#${owner}::}"
      value="${PARTITIONER_DEFAULTS["$key"]}"
      allowed="${PARTITIONER_PROP_ALLOWED["$key"]:-any}"
      when_note="${PARTITIONER_PROP_WHEN["$key"]:-}"
    else
      value="${SYSTEM_DEFAULTS["$key"]}"
      allowed="${SYSTEM_PROP_ALLOWED["$key"]:-any}"
      when_note="${SYSTEM_PROP_WHEN["$key"]:-}"
    fi
    local closed="false"
    local allowed_values="[]"
    if [[ "$allowed" == enum:* ]]; then
      closed="true"
      local values_text="${allowed#enum:}"
      local -a values=("${(@s:|:)values_text}")
      allowed_values="$(ProbeEmitStringArray "${values[@]}")"
    fi
    printf '%s{"key":%s,"value":%s,"allowed":%s,"closed":%s,"values":%s,"when":%s}' \
      "$sep" \
      "$(JsonString "$prop")" \
      "$(JsonString "$value")" \
      "$(JsonString "$allowed")" \
      "$closed" \
      "$allowed_values" \
      "$(JsonString "$when_note")"
    sep=","
  done
  printf ']'
}

DescribeEmitAliasPropertiesJson() {
  local alias_name="$1"
  local -a keys=("${(@k)PROP_ALGORITHM}")
  local key=""
  local sep=""
  keys=("${(@on)keys}")
  keys=("${(@Q)keys}")

  printf '['
  for key in "${keys[@]}"; do
    key=$(DescribeCleanKey "$key")
    [[ "$key" == "${alias_name}::"* ]] || continue
    local prop="${key#${alias_name}::}"
    printf '%s{"key":%s,"value":%s}' \
      "$sep" \
      "$(JsonString "$prop")" \
      "$(JsonString "${PROP_ALGORITHM["$key"]}")"
    sep=","
  done
  printf ']'
}

DescribePartitionerJsonObject() {
  local base="$1"
  local plugin_file=""
  plugin_file=$(ResolvePartitionerPluginFile "$base" || true)
  if [[ -z "$plugin_file" ]]; then
    EchoFatal "unknown partitioner '$base'"
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

  local -a hooks=()
  if FunctionExists "$defaults_fn"; then hooks+=("defaults"); fi
  if FunctionExists "$alias_fn"; then hooks+=("aliases"); fi
  if FunctionExists "$fetch_fn"; then hooks+=("fetch"); fi
  if FunctionExists "$build_fn"; then hooks+=("build"); fi
  if FunctionExists "$invoke_fn"; then hooks+=("invoke"); fi
  if FunctionExists "$describe_fn"; then hooks+=("describe"); fi

  local notes=""
  if FunctionExists "$describe_fn"; then
    notes=$("$describe_fn")
  fi

  local -a aliases=()
  if FunctionExists "$alias_fn"; then
    "$alias_fn"
    local alias_name=""
    for alias_name in ${(k)ALG_DEF_BASE}; do
      alias_name=$(DescribeCleanKey "$alias_name")
      if [[ "$(GetAlgorithmBase "$alias_name")" == "$base" ]]; then
        aliases+=("$alias_name")
      fi
    done
  fi
  aliases=("${(@on)aliases}")
  aliases=("${(@Q)aliases}")

  printf '{"name":%s,"kind":"partitioner","plugin":%s,"hooks":%s,"defaults":%s,"aliases":[' \
    "$(JsonString "$base")" \
    "$(JsonString "$plugin_file")" \
    "$(DescribeEmitHooksJson "${hooks[@]}")" \
    "$(DescribeEmitPropertiesJson "$base" partitioner)"

  local sep=""
  local alias_name=""
  for alias_name in "${aliases[@]}"; do
    local parent="${ALG_DEF_BASE["$alias_name"]:-}"
    local own_args="${ALG_DEF_ARGS["$alias_name"]:-}"
    local args=""
    args=$(GetAlgorithmArgs "$alias_name")
    args="${(j: :)=args}"
    printf '%s{"name":%s,"base":%s,"parent":%s,"args":%s,"own_args":%s,"properties":%s}' \
      "$sep" \
      "$(JsonString "$alias_name")" \
      "$(JsonString "$base")" \
      "$(JsonString "$parent")" \
      "$(JsonString "$args")" \
      "$(JsonString "$own_args")" \
      "$(DescribeEmitAliasPropertiesJson "$alias_name")"
    sep=","
  done

  printf '],"notes":%s}' "$(JsonString "$notes")"
}

DescribeSystemJsonObject() {
  local launcher="$1"
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

  local -a hooks=()
  if FunctionExists "$defaults_fn"; then hooks+=("defaults"); fi
  if FunctionExists "$wrap_fn"; then hooks+=("wrap"); fi
  if FunctionExists "$write_fn"; then hooks+=("write"); fi
  if FunctionExists "$describe_fn"; then hooks+=("describe"); fi

  local notes=""
  if FunctionExists "$describe_fn"; then
    notes=$("$describe_fn")
  fi

  printf '{"name":%s,"kind":"system","plugin":%s,"hooks":%s,"defaults":%s,"notes":%s}' \
    "$(JsonString "$launcher")" \
    "$(JsonString "$plugin_file")" \
    "$(DescribeEmitHooksJson "${hooks[@]}")" \
    "$(DescribeEmitPropertiesJson "$launcher" system)" \
    "$(JsonString "$notes")"
}

DescribeParsersJsonArray() {
  local file=""
  local sep=""
  local -a files=("$MKEXP2_HOME/plugins/parsers/"*.awk(N))
  files=("${(@on)files}")
  printf '['
  for file in "${files[@]}"; do
    printf '%s{"name":%s,"path":%s}' "$sep" "$(JsonString "${file:t:r}")" "$(JsonString "$file")"
    sep=","
  done
  printf ']'
}

DescribePresetsJsonArray() {
  local file=""
  local sep=""
  local -a files=("$MKEXP2_HOME/presets/"*(N))
  files=("${(@on)files}")
  printf '['
  for file in "${files[@]}"; do
    [[ -f "$file" ]] || continue
    printf '%s{"name":%s,"path":%s}' "$sep" "$(JsonString "${file:t}")" "$(JsonString "$file")"
    sep=","
  done
  printf ']'
}

DescribeDslJsonObject() {
  cat <<'JSON'
{"commands":[{"name":"System","usage":"System local|slurm","description":"Selects the execution backend for the experiment."},{"name":"Property","usage":"Property key value","description":"Sets a global run or algorithm property."},{"name":"SystemProperty","usage":"SystemProperty key value","description":"Overrides a property for the selected system."},{"name":"DefineAlgorithm","usage":"DefineAlgorithm Name Base [CLI args...]","description":"Creates an algorithm alias or variant from a partitioner or another alias."},{"name":"AlgorithmProperty","usage":"AlgorithmProperty Name key value","description":"Overrides one property for an algorithm alias."},{"name":"Algorithms","usage":"Algorithms Algo1 Algo2 ...","description":"Selects algorithms used by the following experiment function."},{"name":"Graphs","usage":"Graphs /path/to/graphs [ext]","description":"Adds every file in a graph directory, optionally restricted by extension."},{"name":"Graph","usage":"Graph /path/to/graph","description":"Adds one graph file."},{"name":"Ks","usage":"Ks 2 4 8 ...","description":"Sets partition counts."},{"name":"Seeds","usage":"Seeds 1 2 ...","description":"Sets random seeds."},{"name":"Epsilons","usage":"Epsilons 0.03 ...","description":"Sets imbalance tolerances."},{"name":"Threads","usage":"Threads T|NxMxT ...","description":"Sets local thread or distributed topology entries."}],"common_properties":["parser","repo_url","repo_ref","build_opts","cmake_flags","slurm.partition","slurm.use_array","slurm.array.mode","slurm.array.max_parallel","timelimit.per_instance","parse.auto","postprocess.auto","postprocess.parse","postprocess.plots","postprocess.plot.no_docker","postprocess.plot.threads","postprocess.email.to","postprocess.email.from","postprocess.email.subject","postprocess.email.body","postprocess.email.attach_plots","local.call_wrapper","slurm.call_wrapper"]}
JSON
}

DescribePluginJson() {
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
      DescribePartitionerJsonObject "$name"
      ;;
    system)
      DescribeSystemJsonObject "$name"
      ;;
    "")
      if (( has_part && has_system )); then
        EchoFatal "'$name' matches both a partitioner and a system plugin"
        return 1
      fi
      if (( has_part )); then
        DescribePartitionerJsonObject "$name"
        return $?
      fi
      if (( has_system )); then
        DescribeSystemJsonObject "$name"
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

DescribeAllJson() {
  local -a partitioners=($(DescribePartitionerNames))
  local -a systems=($(DescribeSystemNames))
  local sep=""
  local name=""

  printf '{"ok":true,"partitioners":['
  for name in "${partitioners[@]}"; do
    printf '%s%s' "$sep" "$(DescribePartitionerJsonObject "$name")"
    sep=","
  done

  printf '],"systems":['
  sep=""
  for name in "${systems[@]}"; do
    printf '%s%s' "$sep" "$(DescribeSystemJsonObject "$name")"
    sep=","
  done

  printf '],"parsers":%s,"presets":%s,"dsl":%s}\n' \
    "$(DescribeParsersJsonArray)" \
    "$(DescribePresetsJsonArray)" \
    "$(DescribeDslJsonObject)"
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
