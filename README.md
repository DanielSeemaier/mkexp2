# mkexp v2 (Bash-style scaffold, macOS-friendly)

`mkexp-v2` is a shell-first scaffold for experiment orchestration across graph partitioners.

Requirements:
- `zsh` 5+ (default on macOS)

## What is implemented

- Makefile-style `Experiment` DSL.
- Property layering for flexible overrides:
  - plugin defaults
  - launcher defaults
  - experiment global (`Property`)
  - system-level (`SystemProperty`)
  - algorithm-level (`AlgorithmProperty`)
- Plugin APIs for:
  - partitioners (`plugins/partitioners/*.sh`)
  - launchers (`plugins/launchers/*.sh`)
- Working launchers:
  - `local` (single machine, shared-memory)
  - `slurm` (supports arrays and dependencies between `Experiment*` functions)
- Working partitioner plugins:
  - `Mock` (local smoke tests)
  - `KaMinPar`
  - `dKaMinPar`
  - `Metis`
  - `ParMETIS`
  - `KaHIP`
  - `ParHIP`
  - `MtKaHIP`
  - `MtMetis`

## Quickstart

1. Add `mkexp-v2/bin` to your `PATH` or call it directly.
2. In an experiment directory, run:

```bash
/Users/Daniel/Documents/New\ project/mkexp-v2/bin/mkexp2 init
```

3. Edit `Experiment`.
4. Generate jobs:

```bash
/Users/Daniel/Documents/New\ project/mkexp-v2/bin/mkexp2 generate
```

5. Submit/run jobs:

```bash
./submit.sh
```

Discover available systems/partitioners/presets from the CLI:

```bash
mkexp2 --list-all
# or:
mkexp2 --list-systems
mkexp2 --list-partitioners
mkexp2 --list-parsers
mkexp2 --list-presets
```

Parse finished logs into CSV:

```bash
mkexp2 parse
```

Validate an `Experiment` without generating jobs:

```bash
mkexp2 check
```

Inspect a plugin (partitioner or system/launcher):

```bash
mkexp2 describe MtKaHIP
# alias:
mkexp2 describe-partitioner MtKaHIP
# system:
mkexp2 describe slurm --system
# alias:
mkexp2 describe-system slurm
```

## DSL essentials

```bash
System local
Property slurm.partition cpuonly
Property slurm.call_wrapper srun
Property slurm.minimal_header false
# For local launcher:
# Property local.call_wrapper taskset
SystemProperty slurm.qos normal
AlgorithmProperty KaMinPar repo_url https://github.com/KaHIP/KaMinPar.git
Property slurm.install.mode job
Property slurm.install.timelimit 02:00:00
Property parse.auto true

DefineAlgorithm KaMinPar-Dev KaMinPar
AlgorithmProperty KaMinPar-Dev repo_ref origin/my/branch
DefineAlgorithm KaMinPar-Dbg KaMinPar
AlgorithmProperty KaMinPar-Dbg build_opts -DCMAKE_BUILD_TYPE=Debug
DefineAlgorithm KaMinPar-FM KaMinPar -P fm

ExperimentBaseline() {
  Algorithms KaMinPar-Dev dKaMinPar
  Graphs /path/to/graphs metis
  Ks 2 4 8
  Seeds 1 2 3
  Threads 1x1x16 2x2x16
  Property timelimit 00:30:00
}

ExperimentStress() {
  Property slurm.dependency afterok:ExperimentBaseline
  Algorithms KaMinPar-FM
  Graphs /path/to/graphs metis
  Ks 2 4 8
  Seeds 1 2 3
  Threads 1x1x16 2x2x16
  Property timelimit 02:00:00
}
```

## Plugin contract

Partitioner plugin `X` should define:

- `PartitionerDefaults_X` (optional)
- `PartitionerAliases_X` (optional; predefine algorithm aliases/properties)
- `PartitionerFetch_X` (optional)
- `PartitionerBuild_X` (required for install)
- `PartitionerInvoke_X` (required for generate; set `PARTITIONER_INVOKE_CMD`)

Launcher plugin `Y` should define:

- `LauncherDefaults_Y` (optional)
- `LauncherWrapCommand_Y` (required; set `LAUNCHER_WRAPPED_CMD`)
- `LauncherWriteJob_Y` (required)

Property defaults can also declare value domains in-plugin:
- `SetSystemDefault "<key>" "<default>" "<allowed-values>" ["<when-note>"]`
- `SetPartitionerDefault "<base>" "<key>" "<default>" "<allowed-values>" ["<when-note>"]`
- closed set: `enum:a|b|c`; open set: `any` (or descriptive text like `integer>=1`)
- `mkexp2 describe` prints these value domains, and `mkexp2 check` enforces closed sets.

Inside `PartitionerFetch_*`, `PartitionerBuild_*`, and `PartitionerInvoke_*`, use:
- `PartitionerProperty <key> [fallback]`

Example:
- in plugin defaults: `SetPartitionerDefault "KaMinPar" "build_target" "KaMinParApp"`
- in Experiment: `AlgorithmProperty KaMinPar build_target KaMinParApp`
- in plugin build hook: `build_target=$(PartitionerProperty build_target KaMinParApp)`

## Notes

- This is a scaffold intended to be extended with more partitioners, systems, parsers, and plotting.
- OpenMP env var prefixing (`OMP_NUM_THREADS`, `OMP_PROC_BIND`, `OMP_PLACES`) is opt-in per algorithm via `use_openmp_env`.
  - Default is `false` unless a partitioner plugin sets a default.
  - Override with `AlgorithmProperty <AlgorithmName> use_openmp_env true|false`.
- Command wrappers are configurable per launcher:
  - `Property slurm.call_wrapper srun|taskset` (default: `srun`)
  - `Property local.call_wrapper taskset|none` (default: `taskset`)
  - `taskset` expands to `taskset -c 0-<nproc-1> <cmd>`
- Slurm header controls:
  - `Property slurm.minimal_header true|false` (default: `false`)
  - when `true`, Slurm run jobs only emit `--job-name` and `--partition` (plus `--array` if applicable)
- No timelimit is applied by default.
  - Set `Property timelimit <DD:HH:MM:SS|HH:MM:SS>` to add a Slurm job timelimit.
  - Set `Property timelimit.per_instance <DD:HH:MM:SS|HH:MM:SS>` to wrap each run with `timeout`.
- `timelimit.per_instance` maps to `timeout` seconds in generated commands.
- Install command output is concise by default and writes per-command logs to:
  `logs/install/`
  with filenames prefixed by the current date/time.
- Use `mkexp2 ... --verbose` (or `-v`) to stream full stdout/stderr of each
  command with prefixed, readable output.
- On install failures, `mkexp2` prints the failing command log inline.
- Build parallelism defaults to all available cores (`cmake --parallel`).
- To limit build cores, pass a CLI option:
  - `mkexp2 install --build-max-cores <N>`
  - short form: `mkexp2 install -j <N>`
- Slurm can run install as a dedicated dependency job before compute jobs:
  - `Property slurm.install.mode job`
  - optional: `Property slurm.install.timelimit 02:00:00` (otherwise no `#SBATCH --time`)
  - command logs still go to: `logs/install/`
  - the Slurm wrapper output also goes to: `logs/install/<run-id>-install.log`
- Parse support:
  - `mkexp2 parse` writes CSV files to `results/<algorithm>.csv`
  - `Property parse.auto true` appends parsing automatically after generated runs complete
  - optional: `Property parse.slurm.timelimit 00:30:00` for auto-parse Slurm jobs
  - parser lookup defaults to algorithm base name (e.g. `KaMinPar`, `dKaMinPar`)
  - per-algorithm override from `Experiment`:
    - `AlgorithmProperty <AlgorithmName> parser <name>`
    - `AlgorithmProperty <AlgorithmName> parser ./parsers/<file>.awk`
  - parser `<spec>` resolution order:
    - absolute path
    - relative path from experiment directory
    - bundled parser name in `mkexp2/parsers/`
    - local parser name in `./parsers/` or `./`
- `mkexp2 init` adds `.mkexp2/` and `logs/` to `.gitignore`; CSV results are intentionally not ignored.
