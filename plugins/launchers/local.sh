#!/usr/bin/env zsh

LauncherDefaults_local() {
  SetSystemDefault "local.call_wrapper" "taskset" "enum:taskset|none"
}

LauncherWrapCommand_local() {
  local cmd="$1"
  local nodes="$2"
  local mpis="$3"
  local threads="$4"
  local _distributed="$5"
  local use_openmp_env="$6"

  if (( nodes > 1 )); then
    EchoFatal "launcher 'local' does not support nodes > 1"
    exit 1
  fi

  local wrapped="$cmd"
  if (( mpis > 1 )); then
    wrapped="mpirun -n $mpis $wrapped"
  fi

  local call_wrapper=""
  call_wrapper=$(ResolveRunProperty "local.call_wrapper" "taskset")
  case "$call_wrapper" in
    taskset)
      local nproc=$((mpis * threads))
      if (( nproc <= 0 )); then
        EchoFatal "invalid topology for taskset wrapper: mpis=$mpis threads=$threads"
        exit 1
      fi
      local cpu_end=$((nproc - 1))
      wrapped="taskset -c 0-${cpu_end} $wrapped"
      ;;
    none)
      ;;
    *)
      EchoFatal "invalid local.call_wrapper '$call_wrapper' (expected 'taskset' or 'none')"
      exit 1
      ;;
  esac

  if (( threads > 1 )) && [[ "$use_openmp_env" == "true" ]]; then
    wrapped="OMP_NUM_THREADS=$threads OMP_PROC_BIND=spread OMP_PLACES=threads $wrapped"
  fi

  LAUNCHER_WRAPPED_CMD="$wrapped"
}

LauncherWriteJob_local() {
  local job_script="$1"
  local cmd_file="$2"
  local _job_name="$3"
  local _nodes="$4"
  local _mpis="$5"
  local _threads="$6"
  local _timelimit="$7"
  local _cmd_count="$8"

  cat > "$job_script" <<SCRIPT
#!/usr/bin/env zsh
set -euo pipefail

while IFS= read -r line; do
  [[ -z "\$line" ]] && continue
  echo "+ \$line"
  eval "\$line" < /dev/null || true
done < "${cmd_file}"
SCRIPT
}
