#!/usr/bin/env zsh

LauncherDefaults_slurm() {
  SetSystemDefault "slurm.partition" "default" "any"
  SetSystemDefault "slurm.qos" "" "any" "used when slurm.minimal_header=false"
  SetSystemDefault "slurm.account" "" "any" "used when slurm.minimal_header=false"
  SetSystemDefault "slurm.constraint" "" "any" "used when slurm.minimal_header=false"
  SetSystemDefault "slurm.use_array" "false" "enum:true|false"
  SetSystemDefault "slurm.array.mode" "auto" "enum:auto|scheduler|packed" "auto packs commands into one allocation on whole-node Slurm partitions"
  SetSystemDefault "slurm.array.max_parallel" "1" "integer>=1" "used when slurm.use_array=true and command count > 1"
  SetSystemDefault "slurm.call_wrapper" "srun" "enum:srun|taskset"
  SetSystemDefault "slurm.minimal_header" "false" "enum:true|false"
}

LauncherWrapCommand_slurm() {
  local cmd="$1"
  local nodes="$2"
  local mpis="$3"
  local threads="$4"
  local _distributed="$5"
  local _use_openmp_env="$6"

  local call_wrapper=""
  call_wrapper=$(ResolveRunProperty "slurm.call_wrapper" "srun")

  case "$call_wrapper" in
    srun)
      local total_tasks=$((nodes * mpis))
      LAUNCHER_WRAPPED_CMD="srun --nodes=$nodes --ntasks=$total_tasks --ntasks-per-node=$mpis --cpus-per-task=$threads --cpu-bind=cores $cmd"
      ;;
    taskset)
      local nproc=$((nodes * mpis * threads))
      if (( nproc <= 0 )); then
        EchoFatal "invalid topology for taskset wrapper: nodes=$nodes mpis=$mpis threads=$threads"
        exit 1
      fi
      local cpu_end=$((nproc - 1))
      LAUNCHER_WRAPPED_CMD="taskset -c 0-${cpu_end} $cmd"
      ;;
    *)
      EchoFatal "invalid slurm.call_wrapper '$call_wrapper' (expected 'srun' or 'taskset')"
      exit 1
      ;;
  esac

  if (( threads > 1 )) && [[ "$use_openmp_env" == "true" ]]; then
    LAUNCHER_WRAPPED_CMD="OMP_NUM_THREADS=$threads OMP_PROC_BIND=spread OMP_PLACES=threads $LAUNCHER_WRAPPED_CMD"
  fi
}

SlurmPartitionUsesWholeNodeArrays() {
  local partition="$1"
  local details="${2:-}"

  if [[ -z "$partition" ]]; then
    return 1
  fi

  if [[ -z "$details" ]]; then
    if ! command -v scontrol >/dev/null 2>&1; then
      return 1
    fi
    details=$(scontrol show partition "$partition" 2>/dev/null || true)
  fi
  [[ -n "$details" ]] || return 1

  [[ "$details" == *"OverSubscribe=NO"* && "$details" == *"SelectTypeParameters=NONE"* ]]
}

SlurmPartitionCpusPerNode() {
  local partition="$1"
  local details="${2:-}"
  local total_cpus=""
  local total_nodes=""

  if [[ -z "$partition" ]]; then
    return 1
  fi

  if [[ -z "$details" ]]; then
    if ! command -v scontrol >/dev/null 2>&1; then
      return 1
    fi
    details=$(scontrol show partition "$partition" 2>/dev/null || true)
  fi
  [[ -n "$details" ]] || return 1

  if [[ "$details" =~ 'TotalCPUs=([0-9]+)' ]]; then
    total_cpus="$match[1]"
  fi
  if [[ "$details" =~ 'TotalNodes=([0-9]+)' ]]; then
    total_nodes="$match[1]"
  fi

  if [[ "$total_cpus" == <-> && "$total_nodes" == <-> ]] && (( total_nodes > 0 )); then
    echo $((total_cpus / total_nodes))
    return 0
  fi

  return 1
}

SlurmArrayMode() {
  local partition="$1"
  local requested="${2:-auto}"
  local details=""

  case "$requested" in
    auto)
      if [[ -n "$partition" ]] && command -v scontrol >/dev/null 2>&1; then
        details=$(scontrol show partition "$partition" 2>/dev/null || true)
      fi
      if SlurmPartitionUsesWholeNodeArrays "$partition" "$details"; then
        echo "packed"
      else
        echo "scheduler"
      fi
      ;;
    scheduler|packed)
      echo "$requested"
      ;;
    *)
      EchoFatal "invalid slurm.array.mode '$requested' (expected 'auto', 'scheduler', or 'packed')"
      exit 1
      ;;
  esac
}

SlurmArrayParallelLimit() {
  local max_parallel="$1"
  local cmd_count="$2"

  if ! [[ "$max_parallel" == <-> ]] || (( max_parallel < 1 )); then
    EchoFatal "invalid slurm.array.max_parallel '$max_parallel' (expected integer >= 1)"
    exit 1
  fi

  if (( cmd_count < max_parallel )); then
    echo "$cmd_count"
  else
    echo "$max_parallel"
  fi
}

SlurmPackedArrayParallelLimit() {
  local partition="$1"
  local nodes="$2"
  local mpis="$3"
  local threads="$4"
  local requested_parallel="$5"
  local effective_parallel="$requested_parallel"

  if (( nodes == 1 )); then
    local cpus_per_node=""
    local cpus_per_command=$((mpis * threads))
    cpus_per_node=$(SlurmPartitionCpusPerNode "$partition" || true)
    if [[ "$cpus_per_node" == <-> ]] && (( cpus_per_node > 0 && cpus_per_command > 0 )); then
      local cpu_limited_parallel=$((cpus_per_node / cpus_per_command))
      if (( cpu_limited_parallel < 1 )); then
        cpu_limited_parallel=1
      fi
      if (( cpu_limited_parallel < effective_parallel )); then
        effective_parallel="$cpu_limited_parallel"
      fi
    fi
  fi

  echo "$effective_parallel"
}

SlurmResolveArrayMode() {
  local partition="$1"
  local requested="$2"
  local max_parallel="$3"
  local cmd_count="$4"
  local nodes="$5"
  local mpis="$6"
  local threads="$7"
  local minimal_header="$8"
  local call_wrapper="$9"

  SLURM_ARRAY_MODE="none"
  SLURM_ARRAY_EFFECTIVE_PARALLEL=1

  if (( cmd_count <= 1 )); then
    return 0
  fi

  SLURM_ARRAY_MODE=$(SlurmArrayMode "$partition" "$requested")
  SLURM_ARRAY_EFFECTIVE_PARALLEL=$(SlurmArrayParallelLimit "$max_parallel" "$cmd_count")

  if [[ "$SLURM_ARRAY_MODE" == "packed" && "$minimal_header" == "true" ]]; then
    if [[ "$requested" == "auto" ]]; then
      SLURM_ARRAY_MODE="scheduler"
    else
      EchoFatal "slurm.array.mode=packed requires slurm.minimal_header=false so mkexp2 can request enough CPUs"
      exit 1
    fi
  fi

  if [[ "$SLURM_ARRAY_MODE" == "packed" && "$call_wrapper" != "srun" ]]; then
    if [[ "$requested" == "auto" ]]; then
      SLURM_ARRAY_MODE="scheduler"
    else
      EchoFatal "slurm.array.mode=packed requires slurm.call_wrapper=srun"
      exit 1
    fi
  fi

  if [[ "$SLURM_ARRAY_MODE" == "packed" ]]; then
    SLURM_ARRAY_EFFECTIVE_PARALLEL=$(SlurmPackedArrayParallelLimit "$partition" "$nodes" "$mpis" "$threads" "$SLURM_ARRAY_EFFECTIVE_PARALLEL")
    if [[ "$requested" == "auto" ]] && (( SLURM_ARRAY_EFFECTIVE_PARALLEL <= 1 )); then
      SLURM_ARRAY_MODE="scheduler"
      SLURM_ARRAY_EFFECTIVE_PARALLEL=1
    fi
  fi
}

LauncherWriteJob_slurm() {
  local job_script="$1"
  local cmd_file="$2"
  local job_name="$3"
  local nodes="$4"
  local mpis="$5"
  local threads="$6"
  local timelimit="$7"
  local cmd_count="$8"
  local meta_file="${9:-${cmd_file}.meta.tsv}"

  local total_tasks=$((nodes * mpis))
  local partition=""
  local qos=""
  local account=""
  local constraint=""
  local use_array=""
  local array_mode_requested=""
  local array_mode="none"
  local max_parallel=""
  local effective_parallel=1
  local minimal_header=""
  local call_wrapper=""

  partition=$(ResolveRunProperty "slurm.partition" "default")
  qos=$(ResolveRunProperty "slurm.qos" "")
  account=$(ResolveRunProperty "slurm.account" "")
  constraint=$(ResolveRunProperty "slurm.constraint" "")
  use_array=$(ResolveRunProperty "slurm.use_array" "false")
  array_mode_requested=$(ResolveRunProperty "slurm.array.mode" "auto")
  max_parallel=$(ResolveRunProperty "slurm.array.max_parallel" "1")
  minimal_header=$(ResolveRunProperty "slurm.minimal_header" "false")
  call_wrapper=$(ResolveRunProperty "slurm.call_wrapper" "srun")

  if [[ "$use_array" == "true" && "$cmd_count" -gt 1 ]]; then
    SlurmResolveArrayMode "$partition" "$array_mode_requested" "$max_parallel" "$cmd_count" "$nodes" "$mpis" "$threads" "$minimal_header" "$call_wrapper"
    array_mode="$SLURM_ARRAY_MODE"
    effective_parallel="$SLURM_ARRAY_EFFECTIVE_PARALLEL"
  fi

  local allocation_nodes="$nodes"
  local allocation_tasks="$total_tasks"
  local allocation_tasks_per_node="$mpis"
  if [[ "$array_mode" == "packed" ]]; then
    if (( nodes == 1 )); then
      allocation_tasks=$((total_tasks * effective_parallel))
      allocation_tasks_per_node=$((mpis * effective_parallel))
    else
      allocation_nodes=$((nodes * effective_parallel))
      allocation_tasks=$((total_tasks * effective_parallel))
    fi
  fi

  local slurm_output="slurm/slurm-%j.out"
  if [[ "$array_mode" == "scheduler" ]]; then
    slurm_output="slurm/slurm-%A_%a.out"
  fi

  cat > "$job_script" <<SCRIPT
#!/usr/bin/env zsh
#SBATCH --job-name=${job_name}
#SBATCH --partition=${partition}
#SBATCH --output=${slurm_output}
#SBATCH --error=${slurm_output}
SCRIPT

  if [[ "$minimal_header" != "true" ]]; then
    cat >> "$job_script" <<SCRIPT
#SBATCH --nodes=${allocation_nodes}
#SBATCH --ntasks=${allocation_tasks}
#SBATCH --ntasks-per-node=${allocation_tasks_per_node}
#SBATCH --cpus-per-task=${threads}
SCRIPT

    if [[ -n "$timelimit" ]]; then
      echo "#SBATCH --time=$timelimit" >> "$job_script"
    fi

    if [[ -n "$qos" ]]; then
      echo "#SBATCH --qos=$qos" >> "$job_script"
    fi
    if [[ -n "$account" ]]; then
      echo "#SBATCH --account=$account" >> "$job_script"
    fi
    if [[ -n "$constraint" ]]; then
      echo "#SBATCH --constraint=$constraint" >> "$job_script"
    fi
  fi

  if [[ "$array_mode" == "scheduler" ]]; then
    local end=$((cmd_count - 1))
    echo "#SBATCH --array=0-${end}%${max_parallel}" >> "$job_script"
    cat >> "$job_script" <<SCRIPT
set -euo pipefail

cmd_file="\${MKEXP2_CMD_FILE:-${cmd_file}}"
meta_file="\${MKEXP2_META_FILE:-${meta_file}}"

mkexp2_command_allowed() {
  local experiment="\$1"
  local algorithm="\$2"
  local selected_file="\${MKEXP2_SUBMIT_ALGORITHMS_FILE:-}"
  [[ -n "\$selected_file" && -f "\$selected_file" ]] || return 0

  local selected_experiment=""
  local selected_algorithm=""
  while IFS=\$'\\t' read -r selected_experiment selected_algorithm _rest; do
    [[ -n "\$selected_experiment" ]] || continue
    if [[ -z "\$selected_algorithm" ]]; then
      [[ "\$algorithm" == "\$selected_experiment" ]] && return 0
    elif [[ "\$experiment" == "\$selected_experiment" && "\$algorithm" == "\$selected_algorithm" ]]; then
      return 0
    fi
  done < "\$selected_file"
  return 1
}

metadata_line=\$(sed -n "\$((SLURM_ARRAY_TASK_ID + 1))p" "\$meta_file" 2>/dev/null || true)
if [[ -n "\$metadata_line" ]]; then
  IFS=\$'\\t' read -r _mkexp2_index _mkexp2_algorithm _mkexp2_base _mkexp2_experiment _mkexp2_topology _mkexp2_log_file <<< "\$metadata_line"
  if ! mkexp2_command_allowed "\$_mkexp2_experiment" "\$_mkexp2_algorithm"; then
    echo "skipping array task \$SLURM_ARRAY_TASK_ID for \$_mkexp2_experiment/\$_mkexp2_algorithm"
    exit 0
  fi
fi

line=\$(sed -n "\$((SLURM_ARRAY_TASK_ID + 1))p" "\$cmd_file")
[[ -z "\$line" ]] && { echo "No command for array task \$SLURM_ARRAY_TASK_ID"; exit 1; }
echo "+ \$line"
eval "\$line" < /dev/null || true
SCRIPT
  elif [[ "$array_mode" == "packed" ]]; then
    cat >> "$job_script" <<SCRIPT
# mkexp2 array mode: packed (${effective_parallel} concurrent command(s) in one Slurm allocation)
set -euo pipefail

cmd_file="\${MKEXP2_CMD_FILE:-${cmd_file}}"
meta_file="\${MKEXP2_META_FILE:-${meta_file}}"

mkexp2_command_allowed() {
  local experiment="\$1"
  local algorithm="\$2"
  local selected_file="\${MKEXP2_SUBMIT_ALGORITHMS_FILE:-}"
  [[ -n "\$selected_file" && -f "\$selected_file" ]] || return 0

  local selected_experiment=""
  local selected_algorithm=""
  while IFS=\$'\\t' read -r selected_experiment selected_algorithm _rest; do
    [[ -n "\$selected_experiment" ]] || continue
    if [[ -z "\$selected_algorithm" ]]; then
      [[ "\$algorithm" == "\$selected_experiment" ]] && return 0
    elif [[ "\$experiment" == "\$selected_experiment" && "\$algorithm" == "\$selected_algorithm" ]]; then
      return 0
    fi
  done < "\$selected_file"
  return 1
}

mkexp2_init_semaphore() {
  local slots="\$1"
  local i=0
  mkexp2_sem_fifo="\${TMPDIR:-/tmp}/mkexp2-sem-\${SLURM_JOB_ID:-\$\$}.\$\$"
  mkfifo "\$mkexp2_sem_fifo"
  exec {mkexp2_sem_fd}<>"\$mkexp2_sem_fifo"
  rm -f "\$mkexp2_sem_fifo"

  while (( i < slots )); do
    print -r -- . >&\$mkexp2_sem_fd
    i=\$((i + 1))
  done
}

mkexp2_acquire_slot() {
  local _token=""
  IFS= read -r -u \$mkexp2_sem_fd _token
}

mkexp2_release_slot() {
  print -r -- . >&\$mkexp2_sem_fd
}

mkexp2_close_semaphore() {
  exec {mkexp2_sem_fd}>&-
}

mkexp2_init_semaphore ${effective_parallel}

line_number=0
while IFS= read -r line; do
  line_number=\$((line_number + 1))
  [[ -z "\$line" ]] && continue

  metadata_line=\$(sed -n "\${line_number}p" "\$meta_file" 2>/dev/null || true)
  if [[ -n "\$metadata_line" ]]; then
    IFS=\$'\\t' read -r _mkexp2_index _mkexp2_algorithm _mkexp2_base _mkexp2_experiment _mkexp2_topology _mkexp2_log_file <<< "\$metadata_line"
    if ! mkexp2_command_allowed "\$_mkexp2_experiment" "\$_mkexp2_algorithm"; then
      echo "skipping command \$((line_number - 1)) for \$_mkexp2_experiment/\$_mkexp2_algorithm"
      continue
    fi
  fi

  mkexp2_acquire_slot
  {
    echo "+ [\$((line_number - 1))] \$line"
    eval "\$line" < /dev/null || true
    mkexp2_release_slot
  } &
done < "\$cmd_file"

wait || true
mkexp2_close_semaphore
SCRIPT
  else
    cat >> "$job_script" <<SCRIPT
set -euo pipefail

cmd_file="\${MKEXP2_CMD_FILE:-${cmd_file}}"

while IFS= read -r line; do
  [[ -z "\$line" ]] && continue
  echo "+ \$line"
  eval "\$line" < /dev/null || true
done < "\$cmd_file"
SCRIPT
  fi
}
