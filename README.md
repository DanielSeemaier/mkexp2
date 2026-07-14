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

For `Experiment` files with multiple `Experiment*()` functions, submit selected
algorithms only for selected experiment functions:

```bash
./submit.sh --select ExperimentA:KaMinPar-FM --select ExperimentB:KaMinPar-LP

cat > selection.tsv <<EOF
ExperimentA	KaMinPar-FM
ExperimentB	KaMinPar-LP
EOF
./submit.sh --selection-file selection.tsv
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

Summarize parsed CSV results:

```bash
mkexp2 stats
mkexp2 stats --json
```

`mkexp2 stats` is backed by the R/tidyverse code in the `plots/` submodule.
The JSON output keeps the legacy geometric-mean `avg_cut` and `avg_time`
fields, and also reports failure/timeout/crash/imbalance counts plus common
subsets that worked for every algorithm: all successful cuts, balanced cuts,
and successful runtimes. In the web UI these common subsets are labeled as
fair sets and are computed only on rows that have valid data for every
algorithm.

Validate an `Experiment` without generating jobs:

```bash
mkexp2 check
mkexp2 check --json
```

Check run completion progress:

```bash
mkexp2 progress
mkexp2 progress --json
```

Remove generated state from an experiment directory while keeping only
`Experiment`:

```bash
mkexp2 purge
```

Inspect experiments as JSON:

```bash
mkexp2 probe
mkexp2 probe Baseline
mkexp2 probe Baseline --algorithms
mkexp2 probe --all --algorithms
mkexp2 probe Baseline --jobs
mkexp2 probe Baseline --calls
mkexp2 probe --presets
mkexp2 probe Baseline --property KaMinPar-Fast
mkexp2 probe Baseline --property KaMinPar-Fast.supports_distributed
```

Inspect a plugin (partitioner or system/launcher):

```bash
mkexp2 describe MtKaHIP
mkexp2 describe MtKaHIP --json
mkexp2 describe --all --json
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
For local development on Daniel's MacBook, the server can be started with
`--allow-empty-token` to skip the token prompt; do not use that flag for cluster
runs.

Start the local MCP bridge for Codex after the SSH tunnel is up:

```bash
mkexp2 mcp --url http://127.0.0.1:8765 --token "$MKEXP2_WEB_TOKEN"
# If the web server was started with --allow-empty-token:
mkexp2 mcp --url http://127.0.0.1:8765
```

The MCP bridge talks only to the web API. It can guide Codex through writing an
`Experiment` file, creating an experiment directory, saving/checking/probing it,
submitting selected algorithms, polling submit/progress actions, parsing
results, and reading `mkexp2 stats --json`.

## DSL essentials

```bash
System local
Property slurm.partition cpuonly
Property slurm.call_wrapper srun
Property slurm.minimal_header false
Property spack.environment x86
# For local launcher:
# Property local.call_wrapper taskset
SystemProperty slurm.qos normal
AlgorithmProperty KaMinPar repo_url https://github.com/KaHIP/KaMinPar.git
Property slurm.install.mode job
Property slurm.install.timelimit 02:00:00
Property parse.auto true
Property postprocess.auto true
Property postprocess.email.to results@example.org
Property postprocess.plots 'performance-profile running-time-box'

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
- Slurm array execution is configurable:
  - `Property slurm.array.mode auto|scheduler|packed` (default: `auto`)
  - `Property slurm.array.max_parallel N` caps concurrent array commands
    (default: `1`)
  - `scheduler` emits a native `#SBATCH --array=...%N` job.
  - `packed` submits one Slurm allocation sized for up to
    `slurm.array.max_parallel` concurrent commands and fans them out inside that
    allocation with `srun`; this is useful for whole-node partitions where
    separate array elements cannot share one node.
  - `auto` uses packed mode when `scontrol show partition` reports
    `OverSubscribe=NO` and `SelectTypeParameters=NONE`, otherwise native
    scheduler arrays. When mkexp2 can infer CPUs per node from Slurm partition
    metadata, packed mode is capped to the number of commands that fit on one
    node; if only one command fits, `auto` falls back to scheduler arrays.
- Slurm header controls:
  - `Property slurm.minimal_header true|false` (default: `false`)
  - when `true`, Slurm run jobs only emit `--job-name`, `--partition`, `--output`, and `--error` (plus `--array` if applicable)
- Slurm run, install, and parse job scripts are generated under `jobs/`.
- `Property spack.environment <name-or-path>` activates that Spack environment
  for direct installs and generated local/Slurm install, run, and parse jobs.
  The `spack` executable must be available in the login environment `PATH`.
  The configured environment is included in the build cache identity.
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
- Generated `submit.sh` creates `.mkexp2/submit.lock` before submission. Local
  submissions remove it when the script exits. Slurm submissions keep it until a
  final scheduler cleanup job, submitted with `afterany` dependencies on the
  generated jobs, removes it. Delete the lock manually only after confirming the
  submitted jobs are gone.
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
- Postprocess support:
  - `Property postprocess.auto true` makes the generated cleanup step parse, create managed plot artifacts, and optionally send email
  - `Property postprocess.plots default|all|'<plot ids>'` selects managed plot ids from `mkexp2 plot --list --json`
  - `Property postprocess.email.to user@example.org` sends a completion email with generated PDFs attached when a mailer is available
  - optional templates: `postprocess.email.subject` and `postprocess.email.body` support placeholders such as `{status}`, `{experiment_id}`, `{experiment_path}`, and `{plots}`
- Probe support:
  - `mkexp2 probe` lists all `Experiment*` functions as JSON
  - `mkexp2 probe <experiment>` returns declared and resolved experiment state as JSON
  - `mkexp2 probe --all [aspect flags]` returns one payload per experiment function in a single process
  - selectors accept either the display name (`Baseline`) or function name (`ExperimentBaseline`)
  - aspect flags narrow the payload:
    - `--algorithms`
    - `--graphs`
    - `--topologies`
    - `--run-properties`
    - `--jobs`
    - `--calls`
  - algorithm/graph/topology/run-property probes avoid full run-matrix expansion; jobs/calls and the default detailed probe still expand
  - `--property <Algorithm>` prints the resolved property map for that algorithm as a JSON object
  - `--property <Algorithm>.<property>` prints a single resolved algorithm property as JSON
- `mkexp2 init` adds `.mkexp2/`, `logs/*`, `!logs/install.md`, and `slurm/` to `.gitignore`; CSV results, `plots.pdf`, and the install log are intentionally not ignored.

## Plotting backend

`mkexp2 plot` prefers Docker when the Docker daemon and Docker Compose are
available. If Docker is not available, it falls back to native `Rscript` on the
host. The native path runs the same `plots/install.R` installer and
`plots/mkexp.R plot` entrypoint, but passes host paths through environment
variables instead of using the container's `/data`, `/cache`, and `/output`
mounts.

Native R fallback requires `Rscript` on `PATH`. Missing R packages are installed
into `plots/.r-libs-native`, and native plot cache files go to
`plots/.cache-native`. The native runner prepends this cache to
`R_LIBS` and `R_LIBS_USER`, preserves existing R library paths, and tries
`spack load --sh` for known plotting packages when Spack is available so
Spack-installed R packages can satisfy plotting dependencies. The resolved
Spack `R_LIBS` value is cached in `plots/.cache-native/spack-r-libs.txt` so
repeated native plot runs do not repeatedly invoke slow Spack environment
resolution. `mkexp2 web` warms this cache at startup; use
`mkexp2 plot --refresh-spack-r-libs` to force-refresh it after Spack
environment changes. Native package installation is guarded by a filesystem
lock so overlapping plot runs wait instead of racing on R's `00LOCK-*`
directories. To force the native backend even when Docker works, run:

```bash
mkexp2 plot --no-docker
```

The supported managed plot types are discoverable:

```bash
mkexp2 plot --list --json
```

Legacy `mkexp2 plot` without managed options still writes `plots.pdf` in the
experiment directory. Managed plot generation writes to an explicit output path
and accepts one or more data sources:

```bash
mkexp2 plot --plot running-time-box --output plots/box.pdf KaMinPar-FM KaMinPar-LP
mkexp2 plot --plot speedup --output plots/speedup.pdf KaMinPar-FM
mkexp2 plot --plot imbalance --output plots/imbalance.pdf KaMinPar-FM KaMinPar-LP
mkexp2 plot --plot performance-profile --output plots/profile.pdf \
  Current=results/KaMinPar-FM.csv Other=/path/to/other/results/KaMinPar-FM.csv
```

Source tokens can be an algorithm name, an absolute or experiment-relative CSV
path, or `Alias=/path/to/file.csv`. External CSVs are staged into
`.mkexp2/plot-inputs/<run-id>/` before plotting so Docker and native R consume
the same resolved inputs. The managed imbalance plot shows rows whose observed
imbalance exceeds the requested epsilon. The managed speedup plot takes exactly
one source, uses the smallest available `Cores` value as the baseline, and plots
geometric mean speedup curves for larger core counts over baseline running-time
thresholds.

The web UI exposes managed artifacts first in the Plots tab. Use the Plots tab's
Add button to open the generation dialog, choose plot types, sources, label, and
the "No docker" backend option, then Generate. If the backend cannot use Docker,
the checkbox is checked and disabled automatically. Generation progress is shown
in the Plots tab header, followed by a success/error icon with command details in
the tooltip. Web-triggered plot actions have a two-hour timeout to allow
first-run native R dependency setup on shared filesystems. Web-generated plot
artifacts are immutable timestamped PDFs under each experiment's `plots/`
directory, with metadata in `plots/index.json`; the legacy `plots.pdf` remains
readable for older experiments.

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

Pass `--web-token TOKEN` to reuse a fixed session token across restarts.
Set `MKEXP2_WEB_PUBLIC_HOST` when the machine's short hostname is not the SSH
host colleagues should use in generated share commands.

The web frontend is intentionally kept outside the Python backend: markup lives
in `bin/mkexp2_web_assets/index.html`, styles in
`bin/mkexp2_web_assets/styles.css`, and JavaScript in ordered chunks under
`bin/mkexp2_web_assets/js/`. `bin/mkexp2_web.py` loads and inlines those assets
at startup so the browser still receives one self-contained page.

The UI can:

- list repo-relative experiment directories containing an `Experiment` file,
  including nested year/group folders collapsed by default in the sidebar; rows
  and folders are sorted newest-first by filesystem creation time where the
  platform exposes it
- pin experiments to a flat top section in the sidebar; the pinned ids are
  persisted server-side in `.mkexp2/web-pins.json` under the configured
  experiment repo
- resize the sidebar; the chosen width is stored in browser local storage
- rename experiments by moving the experiment directory inside the repo; renames
  are rejected while the experiment has `.mkexp2/submit.lock`
- archive experiments by renaming the experiment directory leaf to
  `<name>.archived`; archived experiments are hidden from the sidebar and can
  be searched, opened read-only, and restored from the header archive dialog;
  archive/delete actions are also rejected while `.mkexp2/submit.lock` exists
- purge generated files from the Experiment page Danger Zone; this removes
  everything in that experiment directory except the root `Experiment` file and
  is rejected while `.mkexp2/submit.lock` exists
- inspect the configured experiment Git repo from the header Git button, review
  added/modified/deleted files, enter a commit message, and run `git add -A`,
  `git commit`, and `git push`
- create tokenless share links from the Experiment page; a share link opens a
  single-experiment view without the sidebar, editor writes, submit controls,
  and Danger Zone, but still shows the Reference panel and allows the viewer to
  parse logs and generate plot artifacts for that shared experiment only. The
  share dialog shows both the browser URL and a copy/paste SSH tunnel command
  with a generic `<user>`
  placeholder, plus a colleague username field that generates a single
  copyable command to start the tunnel in the background and open the share
  link in the browser.
- download an experiment from the top navigation after choosing which
  top-level subdirectories to include; root files such as `Experiment` are
  always included in the archive, and Settings can choose `tar.zstd` (default),
  `tar.gz`, or `zip` as the archive format; if the preferred format is not
  available on the server, downloads fall back to the next available format
- open the header settings button to manage the session token, dark mode,
  experiment tags, bulk-archive active experiments in a selected subdirectory
  while skipping starred or submit-locked experiments, and the native plotting
  Spack/R cache; settings also include an optional benchmark-set base path that
  powers guided Graph autocomplete
- show a disabled busy state with a spinner on controls that trigger backend
  work, including reloads, checks, probes, parsing, plotting, Git push, and
  destructive recovery actions
- create a new directory from the header `+` dialog, selecting a preset and
  optionally overriding the configured name template for that experiment
- copy the selected experiment from the top navigation into a new directory,
  using the selected experiment's current `Experiment` file as the starting
  template
- edit the raw `Experiment` file with lightweight syntax highlighting, or switch
  to Guided editing, which runs `mkexp2 probe --all` plus
  `mkexp2 describe --all --json`, builds a generic form for systems,
  algorithms, algorithm properties, experiment functions, graph inputs, and run
  parameters, then regenerates the `Experiment` file from that form on Save.
  Switching back to Text renders the current form as an unsaved text preview;
  switching back to Guided reruns mkexp2 metadata instead of parsing the text in
  the browser.
- open the collapsed Reference panel on demand, which runs one
  `mkexp2 describe --all --json` command and renders searchable/filterable DSL
  commands, systems, partitioners, aliases, defaults, parsers, and presets for
  manual experiment authoring; clicking a property value chip copies its value
  with a short visual acknowledgement
- save the current editor contents before running `mkexp2 check --json`, then
  render per-experiment errors, warnings, and summary counts with clear
  pass/fail messages
- save the current editor contents before Submit as well, then refresh the
  algorithm list from the saved file before deriving the selected subset
- run `mkexp2 probe` from the Experiment page to render enabled algorithms
  below the editor as compact rows that emphasize branch/ref settings and CLI
  arguments, with resolved settings shown inline
- fetch bundled init presets with `mkexp2 probe --presets` for new experiments
- fetch algorithm names with `probe`, group them by experiment function, select
  all by default, and submit only the checked per-experiment subset when the
  user deselects variants
- run `mkexp2 generate`, then `./submit.sh --install [algorithms...]` or
  `./submit.sh --install --selection-file <tsv>` for per-experiment selections
- commit submitted state to Git after submission
- run `mkexp2 parse` from the Results tab and reload CSV results when parsing
  succeeds
- persist Results column visibility in the experiment repo backend, so shown
  and hidden CSV columns survive browser changes and reloads
- generate failure-aware stats on demand at the top of Results, backed by `mkexp2 stats
  --json`, including row quality counters, all successful cuts, balanced cuts,
  successful runtimes, and fair-set/common subsets that worked for every
  algorithm
- refresh run progress from the Experiment page; selecting an experiment runs
  progress once automatically, the web backend uses generated
  `jobs/*.cmds.meta.tsv` files when available and falls back to `mkexp2
  progress --json`, and incomplete runs auto-refresh every 15 seconds while
  `.mkexp2/submit.lock` keeps the Submit button disabled
- clear `.mkexp2/submit.lock` from the Submit panel to recover from crashed or
  abandoned submissions
- browse run logs lazily from the Logs tab: the first two run-log directory
  levels are collapsed into `Algorithm/Experiment` entries, `logs/install.md`
  appears as a normal root log entry and is rendered as Markdown when selected,
  and file contents are read only after selecting a specific log file
- display CSV results in the Results tab with parsed tables, remembered column
  visibility, and multi-select algorithm buttons; selecting two or more CSVs
  automatically shows them side by side, locks scrolling across every selected
  table, requires matching row counts, and supports header-click numeric
  coloring for lower-is-better or higher-is-better columns, including orange
  middle values in three-way-or-larger comparisons; reloading CSVs preserves
  the selected algorithm set when those files still exist and clears stale
  stats
- manage plots from the Plots tab: browse artifacts first, open Add to discover
  supported plot types from `mkexp2 plot --list --json`, select
  current-experiment CSV sources or CSVs from other experiments, validate
  source-count restrictions, generate one immutable PDF artifact per selected
  plot type, and preview/list generated artifacts while retaining legacy
  `plots.pdf` support
- show compact Slurm node status in the sidebar, sorted by CPU count, from
  `sinfo -lN -p all` while the status API also attaches live job/user data from
  `squeue`; displayed CPU counts are divided by two and labeled as cores
- open the Slurm queue popup from the Nodes header, render `squeue` as a table,
  and show compact cancel buttons that `scancel` only jobs owned by the user
  running the web server after backend ownership revalidation

The web backend uses Python's standard library only. It rejects experiment ids
that would escape the configured repo root and invokes commands with argv arrays
rather than shell command strings. It does not provide a general shell endpoint.
When `sinfo` is not installed, the status API returns a built-in sample of the
expected i10 node table so local development on macOS still renders the panel.
`--allow-empty-token` is an explicit local-development bypass that accepts an
empty API token and makes the UI auto-load without a token prompt.

## MCP bridge

`mkexp2 mcp` starts a stdio MCP server for Codex or another MCP client. Unlike
`mkexp2 web`, it does not run in the experiment repo and does not execute
cluster commands directly. It is a client-side bridge to a running `mkexp2 web`
server, usually reached through the same SSH tunnel as the browser UI:

```bash
mkexp2 mcp \
  --url http://127.0.0.1:8765 \
  --token "$MKEXP2_WEB_TOKEN"
```

The token is the session token printed by `mkexp2 web`; it may also be supplied
with `MKEXP2_MCP_TOKEN`. If the web server was started with
`--allow-empty-token`, omit `--token` or leave `MKEXP2_MCP_TOKEN` empty.
`MKEXP2_MCP_URL` can set the default URL.

The bridge exposes fixed MCP tools for:

- experiment authoring guidance, including a minimal `Experiment` example
- listing presets and existing experiments
- creating, reading, and writing `Experiment` files through the web API
- running `check --json` and `probe`
- submitting all or selected algorithms, then polling the returned action id
- polling `progress --json`
- running `parse` and reading failure-aware `stats --json` once jobs finish
- fetching parsed CSV results and clearing a submit lock after a crash

It intentionally does not expose arbitrary shell execution. All cluster-side
work stays inside the existing web backend command wrappers.

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

Run only the MCP bridge tests with:

```bash
./tests/run-mcp-tests.zsh
```
