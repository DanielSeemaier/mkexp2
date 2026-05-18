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

Or submit only selected algorithm variants from the generated command set:

```bash
./submit.sh KaMinPar-FM KaMinPar-LP
```

To install/build first as part of the same submit action:

```bash
./submit.sh --install [KaMinPar-FM KaMinPar-LP]
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

Inspect experiments as JSON:

```bash
mkexp2 probe
mkexp2 probe Baseline
mkexp2 probe Baseline --algorithms
mkexp2 probe Baseline --jobs
mkexp2 probe Baseline --calls
mkexp2 probe --presets
mkexp2 probe Baseline --property Mock
mkexp2 probe Baseline --property Mock.supports_distributed
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

Start the tunnel-only web UI on a cluster/login node:

```bash
mkexp2 web --repo /path/to/experiment-repo
# Then from your laptop:
ssh -L 8765:127.0.0.1:8765 user@cluster-login
```

The web server binds to `127.0.0.1:8765` by default and prints a session token
on startup. Paste that token into the UI before using the API-backed controls.

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

`PartitionerProperty` resolves against the active algorithm for the current phase:
- fetch/build hooks see the algorithm currently being installed
- invoke hooks see the algorithm currently being generated/run

That means `AlgorithmProperty <AlgorithmName> ...` overrides apply consistently in all three hook phases, not just at install time.

Example:
- in plugin defaults: `SetPartitionerDefault "KaMinPar" "build_target" "KaMinParApp"`
- in Experiment: `AlgorithmProperty KaMinPar build_target KaMinParApp`
- in plugin build hook: `build_target=$(PartitionerProperty build_target KaMinParApp)`
- in Experiment: `AlgorithmProperty Jet-Custom global_binary /path/to/jet`
- in plugin invoke hook: `global_binary=$(PartitionerProperty global_binary /path/to/default/jet)`

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
  - when `true`, Slurm run jobs only emit `--job-name`, `--partition`, `--output`, and `--error` (plus `--array` if applicable)
- Slurm run, install, and parse job scripts are generated under `jobs/`.
- Slurm scheduler stdout/stderr files are written under `slurm/` (`slurm-%j.out` for regular jobs, `slurm-%A_%a.out` for array tasks).
- `generate` writes one command manifest per job as `jobs/<job>.cmds` and a
  sidecar `jobs/<job>.cmds.meta.tsv` with zero-based command index, algorithm,
  base partitioner, experiment function, topology, and log path.
- Generated `submit.sh` accepts optional algorithm filters and an `--install`
  flag. Without algorithm arguments it submits every generated command. With
  algorithm arguments, it validates exact algorithm names against the sidecar
  metadata and submits only matching commands. With `--install`, local submit
  first runs `mkexp2 install` on the current machine, while Slurm submit first
  submits the generated install job and makes run jobs depend on it. Slurm array
  jobs are submitted with filtered array indices; local and non-array Slurm jobs
  use filtered temporary manifests under `.mkexp2/`.
- No timelimit is applied by default.
  - Set `Property timelimit <DD:HH:MM:SS|HH:MM:SS>` to add a Slurm job timelimit.
  - Set `Property timelimit.per_instance <DD:HH:MM:SS|HH:MM:SS>` to wrap each run with `timeout`.
- `timelimit.per_instance` maps to `timeout` seconds in generated commands.
- Install command output is concise by default and appends every build command
  and its output to one Markdown log: `logs/install.md`.
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
  - command logs still go to: `logs/install.md`
  - the Slurm wrapper output goes to the generated Slurm output file under `slurm/`
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
- Probe support:
  - `mkexp2 probe` lists all `Experiment*` functions as JSON
  - `mkexp2 probe <experiment>` returns declared and resolved experiment state as JSON
  - selectors accept either the display name (`Baseline`) or function name (`ExperimentBaseline`)
  - aspect flags narrow the payload:
    - `--algorithms`
    - `--graphs`
    - `--topologies`
    - `--run-properties`
    - `--jobs`
    - `--calls`
  - `--property <Algorithm>` prints the resolved property map for that algorithm as a JSON object
  - `--property <Algorithm>.<property>` prints a single resolved algorithm property as JSON
- `mkexp2 init` adds `.mkexp2/`, `logs/*`, `!logs/install.md`, and `slurm/` to `.gitignore`; CSV results, `plots.pdf`, and the install log are intentionally not ignored.

## Web UI

`mkexp2 web` serves a single-user management UI for a Git repository containing
experiment directories with `Experiment` files, including nested year/group
folders. It is intended to run on a cluster/login node and be reached only
through SSH port forwarding:

```bash
mkexp2 web --repo /path/to/experiment-repo \
  --host 127.0.0.1 \
  --port 8765 \
  --name-template '%Y.%m.%d-<name>'
```

The UI can:

- list repo-relative experiment directories containing an `Experiment` file,
  including nested year/group folders collapsed by default in the sidebar
- create a new directory from the name template
- edit the raw `Experiment` file with lightweight syntax highlighting
- save the current editor contents before running `mkexp2 check`, then render
  the command JSON with clear pass/fail messages
- run `mkexp2 probe` from the Experiment page to render enabled algorithms
  below the editor as compact rows that emphasize branch/ref settings and CLI
  arguments, with resolved settings and raw algorithm JSON collapsed by default
- fetch bundled init presets with `mkexp2 probe --presets` for new experiments
- fetch algorithm names with `probe`, select all by default, and submit only the
  checked subset when the user deselects variants
- run `mkexp2 generate`, then `./submit.sh --install [algorithms...]`
- commit submitted state to Git after submission
- run `mkexp2 parse` from the Results tab and reload CSV results when parsing
  succeeds
- auto-load and render `logs/install.md` from the Install Log tab, with a
  reload action and an empty state when the log does not exist yet
- display CSV results in the Results tab with parsed tables, remembered column
  visibility, and an Add comparison control that dynamically adds a second CSV
  side by side; comparison tables lock scrolling, require matching row counts,
  and support header-click numeric coloring for lower-is-better or
  higher-is-better columns
- serve `plots.pdf`
- run `mkexp2 plot` on demand from the Plots tab and show plot action status
  there
- show compact Slurm node status in the sidebar, sorted by CPU count, from
  `sinfo -lN -p all` while the status API also attaches live job/user data from
  `squeue`; displayed CPU counts are divided by two and labeled as cores

The web backend uses Python's standard library only. It rejects experiment ids
that would escape the configured repo root and invokes commands with argv arrays
rather than shell command strings. It does not provide a general shell endpoint.
When `sinfo` is not installed, the status API returns a built-in sample of the
expected i10 node table so local development on macOS still renders the panel.

## Tests

Run the probe-focused regression suite with:

```bash
./tests/run-probe-tests.zsh
```

Run the end-to-end suite with:

```bash
./tests/run-e2e-tests.zsh
```

Run everything with:

```bash
./tests/run-all-tests.zsh
```

Run only the web backend tests with:

```bash
./tests/run-web-tests.zsh
```
