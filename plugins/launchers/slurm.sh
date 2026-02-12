#!/usr/bin/env zsh

LauncherDefaults_slurm() {
  SetSystemDefault "slurm.partition" "default"
  SetSystemDefault "slurm.qos" ""
  SetSystemDefault "slurm.account" ""
  SetSystemDefault "slurm.constraint" ""
  SetSystemDefault "slurm.use_array" "false"
  SetSystemDefault "slurm.array.max_parallel" "32"
}

LauncherWrapCommand_slurm() {
  local cmd="$1"
  local nodes="$2"
  local mpis="$3"
  local threads="$4"
  local _distributed="$5"
  local _use_openmp_env="$6"

  local total_tasks=$((nodes * mpis))
  LAUNCHER_WRAPPED_CMD="srun --nodes=$nodes --ntasks=$total_tasks --ntasks-per-node=$mpis --cpus-per-task=$threads --cpu-bind=cores $cmd"
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

  local total_tasks=$((nodes * mpis))
  local partition=""
  local qos=""
  local account=""
  local constraint=""
  local use_array=""
  local max_parallel=""

  partition=$(ResolveRunProperty "slurm.partition" "default")
  qos=$(ResolveRunProperty "slurm.qos" "")
  account=$(ResolveRunProperty "slurm.account" "")
  constraint=$(ResolveRunProperty "slurm.constraint" "")
  use_array=$(ResolveRunProperty "slurm.use_array" "false")
  max_parallel=$(ResolveRunProperty "slurm.array.max_parallel" "32")

  cat > "$job_script" <<SCRIPT
#!/usr/bin/env zsh
#SBATCH --job-name=${job_name}
#SBATCH --nodes=${nodes}
#SBATCH --ntasks=${total_tasks}
#SBATCH --ntasks-per-node=${mpis}
#SBATCH --cpus-per-task=${threads}
#SBATCH --time=${timelimit}
#SBATCH --partition=${partition}
SCRIPT

  if [[ -n "$qos" ]]; then
    echo "#SBATCH --qos=$qos" >> "$job_script"
  fi
  if [[ -n "$account" ]]; then
    echo "#SBATCH --account=$account" >> "$job_script"
  fi
  if [[ -n "$constraint" ]]; then
    echo "#SBATCH --constraint=$constraint" >> "$job_script"
  fi

  if [[ "$use_array" == "true" && "$cmd_count" -gt 1 ]]; then
    local end=$((cmd_count - 1))
    echo "#SBATCH --array=0-${end}%${max_parallel}" >> "$job_script"
    cat >> "$job_script" <<SCRIPT
set -euo pipefail
line=\$(sed -n "\$((SLURM_ARRAY_TASK_ID + 1))p" "${cmd_file}")
[[ -z "\$line" ]] && { echo "No command for array task \$SLURM_ARRAY_TASK_ID"; exit 1; }
echo "+ \$line"
eval "\$line"
SCRIPT
  else
    cat >> "$job_script" <<SCRIPT
set -euo pipefail
while IFS= read -r line; do
  [[ -z "\$line" ]] && continue
  echo "+ \$line"
  eval "\$line"
done < "${cmd_file}"
SCRIPT
  fi
}
