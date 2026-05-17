#!/usr/bin/env zsh

LauncherDefaults_slurm() {
  SetSystemDefault "slurm.partition" "default" "any"
  SetSystemDefault "slurm.qos" "" "any" "used when slurm.minimal_header=false"
  SetSystemDefault "slurm.account" "" "any" "used when slurm.minimal_header=false"
  SetSystemDefault "slurm.constraint" "" "any" "used when slurm.minimal_header=false"
  SetSystemDefault "slurm.use_array" "false" "enum:true|false"
  SetSystemDefault "slurm.array.max_parallel" "32" "integer>=1" "used when slurm.use_array=true and command count > 1"
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
  local max_parallel=""
  local minimal_header=""

  partition=$(ResolveRunProperty "slurm.partition" "default")
  qos=$(ResolveRunProperty "slurm.qos" "")
  account=$(ResolveRunProperty "slurm.account" "")
  constraint=$(ResolveRunProperty "slurm.constraint" "")
  use_array=$(ResolveRunProperty "slurm.use_array" "false")
  max_parallel=$(ResolveRunProperty "slurm.array.max_parallel" "32")
  minimal_header=$(ResolveRunProperty "slurm.minimal_header" "false")

  local slurm_output="slurm/slurm-%j.out"
  if [[ "$use_array" == "true" && "$cmd_count" -gt 1 ]]; then
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
#SBATCH --nodes=${nodes}
#SBATCH --ntasks=${total_tasks}
#SBATCH --ntasks-per-node=${mpis}
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

  if [[ "$use_array" == "true" && "$cmd_count" -gt 1 ]]; then
    local end=$((cmd_count - 1))
    echo "#SBATCH --array=0-${end}%${max_parallel}" >> "$job_script"
    cat >> "$job_script" <<SCRIPT
set -euo pipefail

cmd_file="\${MKEXP2_CMD_FILE:-${cmd_file}}"
meta_file="\${MKEXP2_META_FILE:-${meta_file}}"

mkexp2_algorithm_allowed() {
  local algorithm="\$1"
  local selected_file="\${MKEXP2_SUBMIT_ALGORITHMS_FILE:-}"
  [[ -n "\$selected_file" && -f "\$selected_file" ]] || return 0

  local selected=""
  while IFS= read -r selected; do
    [[ "\$algorithm" == "\$selected" ]] && return 0
  done < "\$selected_file"
  return 1
}

metadata_line=\$(sed -n "\$((SLURM_ARRAY_TASK_ID + 1))p" "\$meta_file" 2>/dev/null || true)
if [[ -n "\$metadata_line" ]]; then
  IFS=\$'\\t' read -r _mkexp2_index _mkexp2_algorithm _mkexp2_base _mkexp2_experiment _mkexp2_topology _mkexp2_log_file <<< "\$metadata_line"
  if ! mkexp2_algorithm_allowed "\$_mkexp2_algorithm"; then
    echo "skipping array task \$SLURM_ARRAY_TASK_ID for algorithm \$_mkexp2_algorithm"
    exit 0
  fi
fi

line=\$(sed -n "\$((SLURM_ARRAY_TASK_ID + 1))p" "\$cmd_file")
[[ -z "\$line" ]] && { echo "No command for array task \$SLURM_ARRAY_TASK_ID"; exit 1; }
echo "+ \$line"
eval "\$line" < /dev/null || true
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
