#!/usr/bin/env zsh

LauncherDefaults_local() {
  SetSystemDefault "timelimit" "01:00:00"
}

LauncherWrapCommand_local() {
  local cmd="$1"
  local nodes="$2"
  local mpis="$3"
  local threads="$4"
  local _distributed="$5"

  if (( nodes > 1 )); then
    echo "fatal: launcher 'local' does not support nodes > 1" >&2
    exit 1
  fi

  local wrapped="$cmd"
  if (( mpis > 1 )); then
    wrapped="mpirun -n $mpis $wrapped"
  fi
  if (( threads > 1 )); then
    wrapped="OMP_NUM_THREADS=$threads OMP_PROC_BIND=spread OMP_PLACES=threads $wrapped"
  fi

  echo "$wrapped"
}

LauncherWriteJob_local() {
  local job_script="$1"
  local cmd_file="$2"
  local _job_name="$3"
  local _nodes="$4"
  local _mpis="$5"
  local _threads="$6"
  local _timelimit="$7"
  local _subexp="$8"
  local _cmd_count="$9"

  cat > "$job_script" <<SCRIPT
#!/usr/bin/env zsh
set -euo pipefail

while IFS= read -r line; do
  [[ -z "\$line" ]] && continue
  echo "+ \$line"
  eval "\$line"
done < "${cmd_file}"
SCRIPT
}
