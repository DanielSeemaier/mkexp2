#!/usr/bin/env zsh

PrintHelp() {
  cat <<'HELP'
Usage: mkexp2 [command] [options]

Commands:
  all       Install + generate (default)
  install   Only fetch/build configured partitioners
  generate  Only generate job files and submit script
  parse     Parse logs into CSV files under ./results
  check     Validate Experiment configuration without generating jobs
  probe     Inspect Experiment definitions and print JSON
  describe  Show plugin defaults/hooks (partitioners + systems)
  init      Create ./Experiment from a preset
  help      Show this help

Options:
  -v, --verbose              Stream full command output while running
  -j, --build-max-cores N    Limit build parallelism to N cores
  --partitioner              With `describe`, force partitioner plugin lookup
  --system                   With `describe`, force system/launcher plugin lookup
  --list-systems             List supported values for `System ...`
  --list-partitioners        List available partitioner plugin names
  --list-parsers             List available parser names
  --list-presets             List installable init presets
  --list-all                 List all of the above
  --algorithms               With `probe`, return resolved algorithms only
  --graphs                   With `probe`, return graph metadata only
  --topologies               With `probe`, return topology metadata only
  --run-properties           With `probe`, return resolved run properties only
  --jobs                     With `probe`, return detailed job metadata
  --calls                    With `probe`, return expanded call details
  --property A[.B]           With `probe`, print algorithm properties or one resolved property
HELP
}

ListNamesFromDir() {
  local dir="$1"
  local title="$2"
  local mode="${3:-basename}"
  local file=""
  local -a names=()

  for file in "$dir"/*.sh(N); do
    if [[ "$mode" == "basename" ]]; then
      names+=("${file:t:r}")
    else
      names+=("${file:t}")
    fi
  done

  if (( ${#names[@]} == 0 )); then
    echo "$title:"
    echo "  (none)"
    return
  fi

  names=("${(@on)names}")
  echo "$title:"
  local name=""
  for name in "${names[@]}"; do
    echo "  $name"
  done
}

ListPresets() {
  local file=""
  local -a names=()
  for file in "$MKEXP2_HOME/presets/"*(N); do
    [[ -f "$file" ]] || continue
    names+=("${file:t}")
  done

  if (( ${#names[@]} == 0 )); then
    echo "Presets:"
    echo "  (none)"
    return
  fi

  names=("${(@on)names}")
  echo "Presets:"
  local name=""
  for name in "${names[@]}"; do
    echo "  $name"
  done
}

ListParsers() {
  local file=""
  local -a names=()
  for file in "$MKEXP2_HOME/parsers/"*.awk(N); do
    names+=("${file:t:r}")
  done

  if (( ${#names[@]} == 0 )); then
    echo "Parsers:"
    echo "  (none)"
    return
  fi

  names=("${(@on)names}")
  echo "Parsers:"
  local name=""
  for name in "${names[@]}"; do
    echo "  $name"
  done
}

PrintDiscoverabilityLists() {
  local printed=0

  if (( MKEXP2_LIST_SYSTEMS )); then
    ListNamesFromDir "$MKEXP2_HOME/plugins/launchers" "Systems (launchers)"
    printed=1
  fi
  if (( MKEXP2_LIST_PARTITIONERS )); then
    if (( printed )); then
      echo ""
    fi
    ListNamesFromDir "$MKEXP2_HOME/plugins/partitioners" "Partitioners"
    printed=1
  fi
  if (( MKEXP2_LIST_PRESETS )); then
    if (( printed )); then
      echo ""
    fi
    ListPresets
    printed=1
  fi
  if (( MKEXP2_LIST_PARSERS )); then
    if (( printed )); then
      echo ""
    fi
    ListParsers
  fi
}

ParseCli() {
  local command_set=0
  local init_preset_set=0
  local list_flag_set=0
  local describe_target_set=0
  local probe_target_set=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      all)
        if (( command_set )); then
          EchoFatal "multiple commands provided"
          PrintHelp
          exit 1
        fi
        MKEXP2_MODE="all"
        MKEXP2_DO_INSTALL=1
        MKEXP2_DO_GENERATE=1
        command_set=1
        shift
        ;;
      install)
        if (( command_set )); then
          EchoFatal "multiple commands provided"
          PrintHelp
          exit 1
        fi
        MKEXP2_MODE="install"
        MKEXP2_DO_INSTALL=1
        MKEXP2_DO_GENERATE=0
        command_set=1
        shift
        ;;
      generate)
        if (( command_set )); then
          EchoFatal "multiple commands provided"
          PrintHelp
          exit 1
        fi
        MKEXP2_MODE="generate"
        MKEXP2_DO_INSTALL=0
        MKEXP2_DO_GENERATE=1
        command_set=1
        shift
        ;;
      parse)
        if (( command_set )); then
          EchoFatal "multiple commands provided"
          PrintHelp
          exit 1
        fi
        MKEXP2_MODE="parse"
        MKEXP2_DO_INSTALL=0
        MKEXP2_DO_GENERATE=0
        MKEXP2_DO_PARSE=1
        command_set=1
        shift
        ;;
      check)
        if (( command_set )); then
          EchoFatal "multiple commands provided"
          PrintHelp
          exit 1
        fi
        MKEXP2_MODE="check"
        MKEXP2_DO_INSTALL=0
        MKEXP2_DO_GENERATE=0
        MKEXP2_DO_PARSE=0
        MKEXP2_DO_CHECK=1
        command_set=1
        shift
        ;;
      probe)
        if (( command_set )); then
          EchoFatal "multiple commands provided"
          PrintHelp
          exit 1
        fi
        MKEXP2_MODE="probe"
        MKEXP2_DO_INSTALL=0
        MKEXP2_DO_GENERATE=0
        MKEXP2_DO_PARSE=0
        MKEXP2_DO_CHECK=0
        MKEXP2_DO_PROBE=1
        command_set=1
        shift
        ;;
      describe|describe-partitioner)
        if (( command_set )); then
          EchoFatal "multiple commands provided"
          PrintHelp
          exit 1
        fi
        MKEXP2_MODE="describe"
        MKEXP2_DO_INSTALL=0
        MKEXP2_DO_GENERATE=0
        MKEXP2_DO_PARSE=0
        MKEXP2_DO_DESCRIBE=1
        command_set=1
        if [[ "$1" == "describe-partitioner" ]]; then
          MKEXP2_DESCRIBE_KIND="partitioner"
        fi
        shift
        ;;
      describe-system)
        if (( command_set )); then
          EchoFatal "multiple commands provided"
          PrintHelp
          exit 1
        fi
        MKEXP2_MODE="describe"
        MKEXP2_DO_INSTALL=0
        MKEXP2_DO_GENERATE=0
        MKEXP2_DO_PARSE=0
        MKEXP2_DO_DESCRIBE=1
        MKEXP2_DESCRIBE_KIND="system"
        command_set=1
        shift
        ;;
      --partitioner)
        if [[ -n "$MKEXP2_DESCRIBE_KIND" && "$MKEXP2_DESCRIBE_KIND" != "partitioner" ]]; then
          EchoFatal "cannot combine --partitioner and --system"
          exit 1
        fi
        MKEXP2_DESCRIBE_KIND="partitioner"
        shift
        ;;
      --system)
        if [[ -n "$MKEXP2_DESCRIBE_KIND" && "$MKEXP2_DESCRIBE_KIND" != "system" ]]; then
          EchoFatal "cannot combine --partitioner and --system"
          exit 1
        fi
        MKEXP2_DESCRIBE_KIND="system"
        shift
        ;;
      init)
        if (( command_set )); then
          EchoFatal "multiple commands provided"
          PrintHelp
          exit 1
        fi
        MKEXP2_MODE="init"
        MKEXP2_DO_INSTALL=0
        MKEXP2_DO_GENERATE=0
        command_set=1
        shift
        if [[ $# -gt 0 && "$1" != -* && $init_preset_set -eq 0 ]]; then
          MKEXP2_INIT_PRESET="$1"
          init_preset_set=1
          shift
        fi
        ;;
      help)
        if (( command_set )); then
          EchoFatal "multiple commands provided"
          PrintHelp
          exit 1
        fi
        MKEXP2_MODE="help"
        MKEXP2_DO_INSTALL=0
        MKEXP2_DO_GENERATE=0
        command_set=1
        shift
        ;;
      -j|--build-max-cores)
        shift
        if [[ $# -eq 0 ]]; then
          EchoFatal "missing value for --build-max-cores"
          exit 1
        fi
        MKEXP2_BUILD_MAX_CORES="$1"
        shift
        ;;
      -v|--verbose)
        MKEXP2_RUN_VERBOSE=1
        shift
        ;;
      -j*)
        MKEXP2_BUILD_MAX_CORES="${1#-j}"
        if [[ -z "$MKEXP2_BUILD_MAX_CORES" ]]; then
          EchoFatal "missing value for --build-max-cores"
          exit 1
        fi
        shift
        ;;
      --build-max-cores=*)
        MKEXP2_BUILD_MAX_CORES="${1#*=}"
        shift
        ;;
      --algorithms)
        MKEXP2_PROBE_ALGORITHMS=1
        shift
        ;;
      --graphs)
        MKEXP2_PROBE_GRAPHS=1
        shift
        ;;
      --topologies)
        MKEXP2_PROBE_TOPOLOGIES=1
        shift
        ;;
      --run-properties)
        MKEXP2_PROBE_RUN_PROPERTIES=1
        shift
        ;;
      --jobs)
        MKEXP2_PROBE_JOBS=1
        shift
        ;;
      --calls)
        MKEXP2_PROBE_CALLS=1
        shift
        ;;
      --property)
        shift
        if [[ $# -eq 0 ]]; then
          EchoFatal "missing value for --property"
          exit 1
        fi
        MKEXP2_PROBE_PROPERTY="$1"
        shift
        ;;
      --property=*)
        MKEXP2_PROBE_PROPERTY="${1#*=}"
        shift
        ;;
      --list-systems)
        MKEXP2_LIST_SYSTEMS=1
        list_flag_set=1
        shift
        ;;
      --list-partitioners)
        MKEXP2_LIST_PARTITIONERS=1
        list_flag_set=1
        shift
        ;;
      --list-presets)
        MKEXP2_LIST_PRESETS=1
        list_flag_set=1
        shift
        ;;
      --list-parsers)
        MKEXP2_LIST_PARSERS=1
        list_flag_set=1
        shift
        ;;
      --list-all)
        MKEXP2_LIST_SYSTEMS=1
        MKEXP2_LIST_PARTITIONERS=1
        MKEXP2_LIST_PRESETS=1
        MKEXP2_LIST_PARSERS=1
        list_flag_set=1
        shift
        ;;
      *)
        if [[ "$MKEXP2_MODE" == "describe" && "$1" != -* ]]; then
          if (( describe_target_set )); then
            EchoFatal "describe accepts exactly one plugin name"
            exit 1
          fi
          MKEXP2_DESCRIBE_TARGET="$1"
          MKEXP2_DESCRIBE_PARTITIONER="$1"
          describe_target_set=1
          shift
          continue
        fi
        if [[ "$MKEXP2_MODE" == "init" && "$1" != -* && $init_preset_set -eq 0 ]]; then
          MKEXP2_INIT_PRESET="$1"
          init_preset_set=1
          shift
          continue
        fi
        if [[ "$MKEXP2_MODE" == "probe" && "$1" != -* ]]; then
          if (( probe_target_set )); then
            EchoFatal "probe accepts at most one experiment selector"
            exit 1
          fi
          MKEXP2_PROBE_TARGET="$1"
          probe_target_set=1
          shift
          continue
        fi
        EchoFatal "unknown argument '$1'"
        PrintHelp
        exit 1
      ;;
    esac
  done

  if (( list_flag_set )); then
    if (( command_set )); then
      EchoFatal "list flags cannot be combined with a command"
      PrintHelp
      exit 1
    fi
    MKEXP2_MODE="list"
    MKEXP2_DO_INSTALL=0
    MKEXP2_DO_GENERATE=0
    MKEXP2_DO_PARSE=0
  fi

  if [[ "$MKEXP2_MODE" == "describe" ]]; then
    if [[ -z "$MKEXP2_DESCRIBE_TARGET" ]]; then
      EchoFatal "describe requires a plugin name (e.g. mkexp2 describe MtKaHIP)"
      exit 1
    fi
  elif [[ "$MKEXP2_MODE" == "probe" ]]; then
    local probe_aspect_count=0
    probe_aspect_count=$((MKEXP2_PROBE_ALGORITHMS + MKEXP2_PROBE_GRAPHS + MKEXP2_PROBE_TOPOLOGIES + MKEXP2_PROBE_RUN_PROPERTIES + MKEXP2_PROBE_JOBS + MKEXP2_PROBE_CALLS))

    if (( probe_aspect_count > 0 )) && [[ -z "$MKEXP2_PROBE_TARGET" ]]; then
      EchoFatal "probe aspect flags require an experiment selector"
      exit 1
    fi
    if [[ -n "$MKEXP2_PROBE_PROPERTY" && -z "$MKEXP2_PROBE_TARGET" ]]; then
      EchoFatal "--property requires an experiment selector"
      exit 1
    fi
    if [[ -n "$MKEXP2_PROBE_PROPERTY" && $probe_aspect_count -gt 0 ]]; then
      EchoFatal "--property cannot be combined with other probe aspect flags"
      exit 1
    fi
  elif [[ -n "$MKEXP2_DESCRIBE_KIND" ]]; then
    EchoFatal "--partitioner/--system can only be used with describe"
    exit 1
  fi

  if (( MKEXP2_PROBE_ALGORITHMS || MKEXP2_PROBE_GRAPHS || MKEXP2_PROBE_TOPOLOGIES || MKEXP2_PROBE_RUN_PROPERTIES || MKEXP2_PROBE_JOBS || MKEXP2_PROBE_CALLS )) && [[ "$MKEXP2_MODE" != "probe" ]]; then
    EchoFatal "probe aspect flags can only be used with probe"
    exit 1
  fi
  if [[ -n "$MKEXP2_PROBE_PROPERTY" && "$MKEXP2_MODE" != "probe" ]]; then
    EchoFatal "--property can only be used with probe"
    exit 1
  fi

  if [[ -n "$MKEXP2_BUILD_MAX_CORES" ]]; then
    if [[ "$MKEXP2_BUILD_MAX_CORES" != <-> ]] || (( MKEXP2_BUILD_MAX_CORES <= 0 )); then
      EchoFatal "--build-max-cores must be a positive integer, got '$MKEXP2_BUILD_MAX_CORES'"
      exit 1
    fi
  fi

}

InitExperiment() {
  local preset_file="$MKEXP2_HOME/presets/$MKEXP2_INIT_PRESET"
  if [[ ! -f "$preset_file" ]]; then
    EchoFatal "preset '$MKEXP2_INIT_PRESET' not found in $MKEXP2_HOME/presets"
    exit 1
  fi
  if [[ -f "$PWD/Experiment" ]]; then
    EchoFatal "Experiment already exists in $PWD"
    exit 1
  fi

  cp "$preset_file" "$PWD/Experiment"
  EchoStep "Created $PWD/Experiment from preset '$MKEXP2_INIT_PRESET'"

  EnsureExperimentGitignore
}

EnsureExperimentGitignore() {
  local gitignore_file="$PWD/.gitignore"
  local changed=0

  if [[ ! -f "$gitignore_file" ]]; then
    : > "$gitignore_file"
  fi

  if ! grep -qxF ".mkexp2/" "$gitignore_file"; then
    printf '%s\n' ".mkexp2/" >> "$gitignore_file"
    changed=1
  fi

  # Experiment run logs can grow very large; keep them out of git by default.
  if ! grep -qxF "logs/" "$gitignore_file"; then
    printf '%s\n' "logs/" >> "$gitignore_file"
    changed=1
  fi

  if (( changed )); then
    EchoStep "Updated $gitignore_file with mkexp2 ignores"
  fi
}
