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
  -j, --build-max-cores N  Limit build parallelism to N cores
HELP
}

ParseCli() {
  local command_set=0
  local init_preset_set=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      all)
        if (( command_set )); then
          echo "fatal: multiple commands provided"
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
          echo "fatal: multiple commands provided"
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
          echo "fatal: multiple commands provided"
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
          echo "fatal: multiple commands provided"
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
          echo "fatal: multiple commands provided"
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
          echo "fatal: missing value for --build-max-cores"
          exit 1
        fi
        MKEXP2_BUILD_MAX_CORES="$1"
        shift
        ;;
      --build-max-cores=*)
        MKEXP2_BUILD_MAX_CORES="${1#*=}"
        shift
        ;;
      *)
        if [[ "$MKEXP2_MODE" == "init" && "$1" != -* && $init_preset_set -eq 0 ]]; then
          MKEXP2_INIT_PRESET="$1"
          init_preset_set=1
          shift
          continue
        fi
        echo "fatal: unknown argument '$1'"
        PrintHelp
        exit 1
        ;;
    esac
  done

  if [[ -n "$MKEXP2_BUILD_MAX_CORES" ]]; then
    if [[ "$MKEXP2_BUILD_MAX_CORES" != <-> ]] || (( MKEXP2_BUILD_MAX_CORES <= 0 )); then
      echo "fatal: --build-max-cores must be a positive integer, got '$MKEXP2_BUILD_MAX_CORES'"
      exit 1
    fi
  fi
}

InitExperiment() {
  local preset_file="$MKEXP2_HOME/presets/$MKEXP2_INIT_PRESET"
  if [[ ! -f "$preset_file" ]]; then
    echo "fatal: preset '$MKEXP2_INIT_PRESET' not found in $MKEXP2_HOME/presets"
    exit 1
  fi
  if [[ -f "$PWD/Experiment" ]]; then
    echo "fatal: Experiment already exists in $PWD"
    exit 1
  fi

  cp "$preset_file" "$PWD/Experiment"
  EchoStep "Created $PWD/Experiment from preset '$MKEXP2_INIT_PRESET'"
}
