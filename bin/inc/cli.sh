#!/usr/bin/env zsh

PrintHelp() {
  cat <<'HELP'
Usage: mkexp2 [command] [options]

Commands:
  all       Install + generate (default)
  install   Only fetch/build configured partitioners
  generate  Only generate job files and submit script
  init      Create ./Experiment from a preset
  help      Show this help

Options:
  -j, --build-max-cores N    Limit build parallelism to N cores
  --list-systems             List supported values for `System ...`
  --list-partitioners        List available partitioner plugin names
  --list-presets             List installable init presets
  --list-all                 List all of the above
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
  fi
}

ParseCli() {
  local command_set=0
  local init_preset_set=0
  local list_flag_set=0

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
      --build-max-cores=*)
        MKEXP2_BUILD_MAX_CORES="${1#*=}"
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
      --list-all)
        MKEXP2_LIST_SYSTEMS=1
        MKEXP2_LIST_PARTITIONERS=1
        MKEXP2_LIST_PRESETS=1
        list_flag_set=1
        shift
        ;;
      *)
        if [[ "$MKEXP2_MODE" == "init" && "$1" != -* && $init_preset_set -eq 0 ]]; then
          MKEXP2_INIT_PRESET="$1"
          init_preset_set=1
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
