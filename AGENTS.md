# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What This Project Is

`mkexp2` is a zsh shell-based experiment orchestration scaffold for benchmarking graph partitioning algorithms. It automates the full lifecycle: fetching/building algorithm binaries, expanding the combinatorial parameter space (algorithms × graphs × k values × seeds × epsilons × thread topologies), generating job scripts, submitting to a local machine or Slurm HPC cluster, and parsing output logs into CSV.

**There is no build step** — the project is pure zsh scripts.

## Running Commands

```zsh
# Run/install
./bin/mkexp2 install    # fetch and build partitioner binaries
./bin/mkexp2 generate   # generate job scripts
./bin/mkexp2            # all (install + generate)

# After generate, submit jobs:
./submit.sh
./submit.sh KaMinPar-FM KaMinPar-LP  # submit only selected algorithms
./submit.sh --install KaMinPar-FM    # install/build first, then submit selected algorithms

# Parse logs into CSV (run after jobs finish):
./bin/mkexp2 parse

# Summarize parsed CSV results:
./bin/mkexp2 stats
./bin/mkexp2 stats --json

# Check run completion progress (counts existing log files vs. expected):
./bin/mkexp2 progress
./bin/mkexp2 progress --json

# Generate plots from CSV results (uses Docker when available, otherwise native R):
./bin/mkexp2 plot                        # all algorithms, all plots
./bin/mkexp2 plot KaMinPar-FM KaMinPar-LP  # explicit algorithm list
./bin/mkexp2 plot --performance-profile  # subset of plots
./bin/mkexp2 plot --speedup --running-time
./bin/mkexp2 plot --threads 4            # plot only data for topology 1x1x4
./bin/mkexp2 plot --list --json          # JSON catalog of managed plot types
./bin/mkexp2 plot --plot running-time-box --output plots/box.pdf KaMinPar-FM KaMinPar-LP
./bin/mkexp2 plot --plot speedup --output plots/speedup.pdf KaMinPar-FM
./bin/mkexp2 plot --plot performance-profile Alias=/path/to/Other.csv KaMinPar-FM
./bin/mkexp2 plot --no-docker            # force native R backend
./bin/mkexp2 plot --resolve-spack-r-libs # warm native-R Spack library cache
./bin/mkexp2 plot --refresh-spack-r-libs # force-refresh native-R Spack library cache
./bin/mkexp2 probe --presets             # JSON list of init presets
./bin/mkexp2 probe --all --algorithms    # resolved algorithms for every Experiment* function

# Tunnel-only web UI (run on cluster/login node, access via ssh -L):
./bin/mkexp2 web --repo /path/to/experiment-repo
# Reuse a fixed web token across restarts:
./bin/mkexp2 web --repo /path/to/experiment-repo --web-token "$MKEXP2_WEB_TOKEN"

# Local web UI on Daniel's MacBook: always start with the dev-only empty-token bypass:
./bin/mkexp2 web --repo ~/i10-experiments --allow-empty-token

# Local stdio MCP bridge to a running mkexp2 web server:
./bin/mkexp2 mcp --url http://127.0.0.1:8765 --token "$MKEXP2_WEB_TOKEN"
# If the local web server uses --allow-empty-token, omit --token:
./bin/mkexp2 mcp --url http://127.0.0.1:8765

# Tests
./tests/run-all-tests.zsh       # run all tests
./tests/run-probe-tests.zsh     # probe/inspection tests only
./tests/run-e2e-tests.zsh       # end-to-end pipeline tests only
./tests/run-web-tests.zsh       # Python stdlib web backend tests
./tests/run-mcp-tests.zsh       # stdio MCP bridge tests
```

Shell tests require `jq`. They use TAP-style output and call the actual `bin/mkexp2` binary against fixture `Experiment` files — they are integration tests, not unit tests. Web backend and MCP bridge tests use Python `unittest`.

There is no linting configuration or CI setup.

## Architecture

### Entry Point & Module Loading

`bin/mkexp2` sources all modules from `bin/inc/` (state, util, dsl, props, plugins, expand, install, generate, parse, stats, plot, check, probe, progress, cli), parses the CLI, discovers `Experiment*()` functions in the user's `Experiment` file, and loops over them.

### Data Flow

1. **DSL layer** (`bin/inc/dsl.sh`): The user's `Experiment` file calls DSL commands (`System`, `Property`, `Algorithms`, `Graphs`, `Threads`, `Ks`, `Seeds`, etc.) which populate global zsh associative arrays (`_algorithms`, `_graphs`, `_ks`, `PROP_GLOBAL`, `PROP_ALGORITHM`, etc.).

2. **Property resolution** (`bin/inc/props.sh`): Six-level priority chain (lowest→highest): partitioner plugin defaults → system plugin defaults → global `Property` → system-level `SystemProperty` → algorithm base `AlgorithmProperty` → algorithm instance `AlgorithmProperty`.

3. **Plugin system** (`bin/inc/plugins.sh`): Shell files in `plugins/partitioners/` and `plugins/launchers/` are lazy-loaded. Each partitioner plugin defines hooks: `PartitionerDefaults_X`, `PartitionerAliases_X`, `PartitionerFetch_X`, `PartitionerBuild_X`, `PartitionerInvoke_X`. Each launcher defines: `LauncherDefaults_Y`, `LauncherWrapCommand_Y`, `LauncherWriteJob_Y`.

4. **Build context** (`bin/inc/install.sh`): `PopulateBuildContext` computes `CTX_*` variables and a content-addressed `CTX_build_key` (SHA1 of `base|url|ref|build_opts|cmake_flags`) for caching. Binaries go to `.mkexp2/bin/<base>-<hash>`.

   Install/build commands executed through `Run` append command sections and output to one Markdown log at `logs/install.md`; individual per-command install log files are not generated. Install log initialization includes machine probes such as `uname -a`, `lscpu`, `lsmem`, memory/core counts, and macOS/BSD fallbacks when available. Install log headings use only the command name, the full invocation is kept inside the fenced console block, and captured command output is stripped of ANSI color/control escapes before writing Markdown. If a previous install was interrupted before a command footer was written, the next install-log initialization closes the dangling Markdown fence and records an interruption note before appending the resumed section.

5. **Expansion engine** (`bin/inc/expand.sh`): `ExpandCurrentExperiment` iterates the Cartesian product of `(topology × algorithm × seed × epsilon × k × graph)`, calling plugin hooks to produce commands. Results go into `EXPAND_CALL` / `EXPAND_JOB` maps.

6. **Generate** (`bin/inc/generate.sh`): Writes `.cmds` files (one line per invocation), `.cmds.meta.tsv` sidecars, and job scripts under `jobs/`, then builds a master `submit.sh`. The sidecar rows are zero-based command index, algorithm, base partitioner, experiment function, topology, and log path. Generated submit scripts accept `--install` before optional algorithm filters; for local jobs this runs `mkexp2 install` on the same machine before the selected runs, and for Slurm jobs this submits the generated install job and wires run jobs after it. Generated submit scripts create `.mkexp2/submit.lock`; local submits remove it on exit, while Slurm submits keep it until a final `afterany` cleanup job removes it after the submitted dependency chain finishes.

7. **Parse** (`bin/inc/parse.sh`): Streams log files through awk parsers (`plugins/parsers/*.awk`) using a `__BEGIN_FILE__ <marker>` / `__END_FILE__` protocol. The shared `plugins/parsers/.csv.awk` provides helpers for CSV output. Bundled parsers include `KaMinPar`, `dKaMinPar`, and `MtKaHyPar`.

8. **Stats** (`bin/inc/stats.sh`): Reads parsed CSV files under `results/` and reports per-algorithm row counts, failed row counts, geometric-mean `Cut`, and geometric-mean `Time`. `mkexp2 stats --json` emits structured data for the Stats section below the web Results tab. Successful averages ignore rows whose `Failed` column is truthy.

9. **Probe** (`bin/inc/probe.sh`): Runs expansion in probe mode (`MKEXP2_PROBE_MODE=1`) and serializes the model as JSON. `mkexp2 probe --presets` is a metadata-only JSON command that lists bundled init presets without requiring an `Experiment` file. `mkexp2 probe --all [aspect flags]` applies a probe to every discovered `Experiment*()` function in one process; the web UI uses `mkexp2 probe --all --algorithms` for submit checkbox loading.

10. **Progress** (`bin/inc/progress.sh`): Runs expansion in probe mode across all experiment functions and compares expected log file paths against existing files on disk, printing a per-algorithm progress bar table or structured totals with `mkexp2 progress --json`. The web backend uses generated `jobs/*.cmds.meta.tsv` metadata for progress when available, avoiding a fresh expansion and per-log-file stat path on NFS.

11. **Plot** (`bin/inc/plot.sh`): `mkexp2 plot --list --json` emits the managed plot catalog with ids, names, descriptions, source-count limits, default-selection hints, expensive flags, and legacy CLI aliases. Legacy `mkexp2 plot` still reads active algorithms from the `Experiment` file (or CLI args) and writes `plots.pdf` in the experiment directory. Managed plot mode accepts `--plot <id>` one or more times, `--output <path>`, and positional sources. A source can be an algorithm name (`results/<Algorithm>.csv`), an absolute or experiment-relative CSV path, or `Alias=/path/to/file.csv`; external CSVs are staged into `.mkexp2/plot-inputs/<run-id>/` so Docker and native R consume the same resolved files. Plotting uses Docker when `docker info` and Docker Compose are available unless `--no-docker` is passed, otherwise it falls back to native `Rscript`. The Docker path writes `.mkexp2/plots-compose.yml`, builds a Docker image tagged from the `plots/Dockerfile` content only when that tag is missing, installs R packages into `plots/.r-libs` on first run (cached), then runs `plots/mkplots.R` inside the container. The native R path runs the same `plots/install.R` and `plots/mkplots.R` on the host, with `MKEXP2_PLOTS_DATA_DIR`, `MKEXP2_PLOTS_CACHE_DIR`, and `--output` pointing at the experiment directory; native packages/cache live in `plots/.r-libs-native` and `plots/.cache-native`, the runner prepends `.r-libs-native` to both `R_LIBS` and `R_LIBS_USER` while preserving existing search paths even when they are unset, it opportunistically evaluates `spack load --sh` for known plotting R packages when Spack is available, caches the resolved Spack `R_LIBS` value in `.cache-native/spack-r-libs.txt` for later native plot runs, and native package installation is guarded by a filesystem lock to avoid concurrent R `00LOCK-*` races. After either backend returns, mkexp2 verifies the requested PDF output exists and is non-empty before printing success. `mkexp2 plot --threads T|NxMxT` filters each CSV before aggregation; bare `T` is normalized to `1x1xT`. The generated Docker build context lives under `.mkexp2/plots-image/` and contains only the Dockerfile, so cached package directories such as `plots/.r-libs` are not streamed to Docker during image builds. The managed catalog currently includes performance profile, running-time box, running-time by core, relative cut/time graph grids, and a single-source speedup plot. The speedup plot uses the smallest available `Cores` value as the baseline, matches instances by graph/k/epsilon/topology fields, renders per-instance speedups as points, and overlays cumulative geometric-mean speedup curves for larger core counts over baseline running-time thresholds.

12. **Web UI** (`bin/mkexp2_web.py`): `mkexp2 web --repo DIR` starts a Python stdlib HTTP server bound to `127.0.0.1:8765` by default for SSH-tunnel access. Pass `--web-token TOKEN` to reuse a fixed web session token across restarts. It lists repo-relative experiment directories containing an `Experiment` file, including nested year/group folders, creates new experiments from dynamically fetched `mkexp2 probe --presets` presets, edits raw `Experiment` files with a synchronized syntax-highlight overlay, runs `check --json`/`probe`/`generate`/`submit`/`parse`/`stats --json`/`plot` through fixed argv arrays with child stdin closed, commits submitted state to Git, exposes a header Git dialog for the configured experiment repo that renders `git status --porcelain` output as one compact colored file list with A/M/D status markers and runs `git add -A`, `git commit -m`, and `git push`, exposes a header settings dialog that manages the session token, manages experiment tags with a fixed color palette and custom-tag deletion, shows and force-refreshes the native plot Spack R library cache, shows disabled spinner busy states on backend-triggering controls, serves CSV/legacy `plots.pdf`/managed plot artifacts, auto-loads and renders `logs/install.md` in the icon-only Install Log tab when present, lazily browses run logs in the Logs tab by collapsing the first run-log directory levels into `Algorithm/Experiment` entries, hiding `logs/install.md` because it has its own tab, and reading file contents only after a user selects a file, and collects Slurm node status from `sinfo -lN -p all` with live job/user data attached from fixed-format `squeue -h -o` output. Selecting a different experiment always returns the main view to the Experiment tab and fetches only that experiment file, not the full tree; initial page load preselects the most recent experiment by creation metadata when one exists, and creation date/times are shown in sidebar rows plus the selected experiment header. The experiment list uses `git ls-files` plus untracked `Experiment` files when the repo is a Git worktree, falls back to `os.walk` for non-Git repos, skips stale tracked paths after archive renames, hides experiment directories whose leaf ends in `.archived`, returns filesystem creation metadata plus current submit-lock summaries when available, and caches the base list briefly to avoid slow NFS rescans. The Submit panel exposes Check, which saves the current editor content before running `mkexp2 check --json`, shows a green success check or tooltip-backed error indicator; the Experiment page also includes a Run Probe panel below the editor that renders experiment inputs and enabled algorithms as compact rows that emphasize branch/ref settings, CLI arguments, and always-expanded resolved settings. The experiment sidebar renders nested folders collapsed by default, sorts rows and folders newest-first by creation metadata, marks submit-locked experiments with a red experiment-name label, is resizable with browser-local persistence, supports server-side pinned experiments stored in `.mkexp2/web-pins.json`, and supports server-side experiment tags stored in `.mkexp2/web-tags.json` with a default blue `Codex` tag whose assignment colors the sidebar row border. The icon-only top navigation has a tag selector plus share and download actions; share links are stored in `.mkexp2/web-shares.json`, and authenticated downloads stream a temporary experiment archive using `tar --zstd` when available with a fast ZIP fallback; `/share/<id>` opens a tokenless single-experiment view with no sidebar, editor writes, submit controls, or Danger Zone, while still allowing download, probe, parse, and managed plot-artifact generation for CSVs from that shared experiment only, and the share dialog prints both the browser URL and a copy/paste SSH tunnel command with a generic `<user>` placeholder. Experiment creation lives in a header `+` modal that fetches presets and server config, shows the active name template around the name input, and can send a one-off `name_template` override to the create API. The header archive dialog lists `.archived` experiments in the same collapsed hierarchy and can unarchive them; the Experiment Danger Zone archives the selected experiment by renaming only the directory leaf to `<name>.archived` and removing the id from pins, without committing. Results is a full-page tab with an icon-only CSV reload button; CSV files are parsed into tables, column visibility is stored in browser local storage per experiment/header set, and algorithm buttons are multi-select: one selected CSV renders a single table, while two or more selected CSVs automatically render side-by-side synchronized comparison panes. The Results tab can trigger `mkexp2 parse` and reload CSVs after a successful parse, and it renders `mkexp2 stats --json` below the CSV tables. Multi-way comparison tables require matching row counts before row-wise comparison is offered, and header clicks cycle numeric coloring between lower-is-better, higher-is-better, and off; equal values are blue, best/worst values are green/red, and middle values in three-way-or-larger comparisons are orange. The Experiment page refreshes progress with `mkexp2 progress --json`, renders structured progress bars, and after progress has been loaded it polls every 15 seconds until all expected logs exist; each experiment can also store Markdown notes in `description.md`, rendered below Progress by default with an authenticated edit mode; `.mkexp2/submit.lock` disables Submit with the lock message as the button tooltip, while the Experiment page Danger Zone exposes explicit unlock plus unlocked-only rename, archive, and delete actions for manual recovery. The Plots tab loads the managed catalog from `mkexp2 plot --list --json`, renders plot-type checkboxes and descriptions, lists current-experiment result CSVs as selectable sources, opens a lazy external-CSV selector for other experiments' `results/*.csv` files using the same collapsed directory hierarchy as the sidebar, lets external CSV sources be removed after adding, allows editable aliases and artifact labels, validates source-count restrictions in the UI and backend, passes `--no-docker` when checked or when Docker is unavailable, generates one immutable timestamped PDF per selected plot type under `plots/` with metadata in `plots/index.json`, records a shared plot-set id/label for each generation run, supports authenticated plot-set rename plus individual plot or whole-set deletion, refreshes the artifact list after completion, provides orthogonal artifact navigation by plot set or plot type, embeds selected artifact PDFs, and still exposes legacy `plots.pdf` when present. The submit UI shows a loading spinner while probing algorithm choices, uses one bulk `probe --all --algorithms` call, ignores stale algorithm-probe responses after experiment switches, and checks all probed algorithms by default; all-selected submissions use the generated `submit.sh --install` path, while deselecting variants sends an explicit algorithm subset. If `mkexp2 check` blocks submission, the UI asks for a one-time validation override instead of showing a permanent checkbox. The node sidebar renders compact name/core rows sorted by CPU count, divides displayed CPU counts by two, colors rows by Slurm state (`allocated`, `idle~`, `idle`, `down`), attaches only jobs with real assigned Slurm nodes, and has an icon-only Slurm queue dialog that calls fixed-format `squeue -h -o`, tolerates default table output with or without `TIME_LIMIT`, and falls back to a built-in sample when `squeue` is unavailable for local development. The queue dialog only shows compact cancel buttons for jobs owned by the OS user running the web server, and the backend re-reads fixed-format `squeue` to verify ownership before invoking `scancel <jobid>`. When `sinfo` is not installed, the web status API returns a built-in i10 sample table for local macOS development. It requires a startup session token for API requests unless explicitly started with `--allow-empty-token`, which is only for local MacBook development, and rejects experiment ids that escape the configured repo root.

   The Slurm queue dialog also has a footer `Cancel all` action that asks for explicit confirmation and sends a matching backend `confirm_user` before invoking `scancel -u <server_user>`.

   The Plots tab uses a compact header status indicator for plot generation success/failure instead of an inline action-output card, and its Plot Types, Sources, and Artifacts sections are unframed within the main panel to avoid nested card hierarchy. Managed plot artifacts render as a left-side vertical selector at roughly 25% width, with the PDF preview taking the remaining width. Keep all web DELETE API routes in the single `do_DELETE` handler so plot-artifact deletion, plot-set deletion, submit-lock clearing, and experiment deletion remain reachable together.

13. **MCP bridge** (`bin/mkexp2_mcp.py`): `mkexp2 mcp --url URL [--token TOKEN]` starts a Python stdlib stdio MCP server intended to run next to Codex, not on the cluster. It routes every operation through a running `mkexp2 web` backend, typically over the user's SSH tunnel. The token may be omitted when the target web server was started with `--allow-empty-token`; otherwise use the session token printed by `mkexp2 web`. The bridge exposes fixed tools for Experiment authoring guidance, preset/experiment listing, create/read/write of `Experiment` files, `check --json`, `probe`, submit with optional algorithm selection, action polling, `progress --json`, parse actions, `stats --json`, CSV result fetches, and submit-lock clearing. Experiments created through the MCP bridge are automatically tagged as `Codex` in the web backend. It does not expose arbitrary shell execution; cluster-side work stays inside the web backend's argv-array command wrappers.

### Key Conventions

- **Global state via associative arrays.** All experiment state is in module-level zsh associative arrays. `ResetExperiment` clears them between `Experiment*()` function calls.

- **Output buffers instead of subshells.** Plugin hooks write results to `PARTITIONER_INVOKE_CMD` and `LAUNCHER_WRAPPED_CMD` global variables rather than stdout, to avoid subshell overhead in the generate hot path.

- **Topology encoding.** `T` = 1 node, 1 MPI, T threads. `NxMxT` = N nodes, M MPI ranks per node, T threads per rank. `ParseNodes`, `ParseMpis`, `ParseThreads` in `util.sh` decode this.

- **Log file naming.** `logs/<algorithm>/<experiment_label>/<graph>___k<K>_seed<S>_eps<E>_P<topology>.log` — the filename encodes all parameters needed by the parser.

- **Algorithm-filtered submit.** Generated `submit.sh` accepts optional exact algorithm names plus `--install`. With no algorithm args it submits all commands. With args it validates against `jobs/*.cmds.meta.tsv`, filters local/non-array jobs via generated manifests under `.mkexp2/submit-filter-*`, and filters Slurm arrays by overriding `sbatch --array` with selected original command indices. With `--install`, local submits first run `mkexp2 install` on the same machine; Slurm submits first submit the generated install job and use it as a dependency. The submit script uses `.mkexp2/submit.lock` to prevent duplicate submissions; do not remove it manually unless the corresponding run is known to be gone.

- **Generated `.gitignore`.** `mkexp2 init` ignores generated log contents with `logs/*` but explicitly unignores `logs/install.md`, so the single install log remains visible for debugging failed setup runs. It does not ignore `plots.pdf` by default. Managed web plot artifacts live under the experiment-local `plots/` directory and are added to `.gitignore` by `mkexp2 plot` because they are generated PDFs plus `plots/index.json` metadata.

- **Slurm artifacts.** Slurm run/install/parse job scripts are generated under `jobs/`. Slurm scheduler stdout/stderr is directed to `slurm/slurm-%j.out` for regular jobs and `slurm/slurm-%A_%a.out` for array tasks, while mkexp2 command manifests also remain under `jobs/*.cmds` with matching `*.cmds.meta.tsv` files.

- **Hidden plugins.** Dot-prefixed files (`.TestHarness.sh`, `.TestHarness.awk`) are internal test fixtures excluded from `--list-*` output.

- **`PartitionerProperty key [fallback]`** — inside plugin hooks, resolves a property for the currently active algorithm. Works consistently during install, generate, and probe phases. `AlgorithmProperty` values inherit through the full `DefineAlgorithm` chain, so a child alias inherits parent alias properties such as `repo_ref` unless it overrides them.

- **`mkexp2 describe <partitioner>` alias listing.** Describe output includes every `DefineAlgorithm` alias whose recursive `GetAlgorithmBase` resolves to the described partitioner, not just direct aliases. Nested aliases print their effective inherited argument string via `GetAlgorithmArgs` plus any properties declared directly on that alias.

- **Unknown algorithm names.** An algorithm not created with `DefineAlgorithm` resolves to a partitioner plugin of the same name; if no such plugin exists, `mkexp2 install/generate` should fail with a clear `unknown partitioner plugin '<name>'` fatal message.

- **TAP test output.** `pass` / `fail` helpers in `tests/lib/test_framework.zsh` print `ok N - msg` / `not ok - msg`. Driver scripts print `1..$TEST_COUNT` at the end.

- **Parser fixture tests.** Parser regression tests live in `tests/parser_plugins_test.zsh`. Fixtures are grouped under `tests/fixtures/parsers/<ParserName>/` as one or more `.log` files plus `expected.csv`; the helper copies those logs into a temporary experiment and runs `mkexp2 parse`. Current fixtures cover `KaMinPar` and `MtKaHyPar`.

- **Plot submodule.** `plots/` is a git submodule. R plotting code lives there and is mounted into the plot container at runtime rather than copied into the image, or run directly by native-R fallback. Plotting-related changes may require editing files inside `plots/`; when committing those submodule changes, always commit and push them to the `mkexp` branch of the plots repository, never to its `main` branch. The submodule's `.gitignore` excludes `.r-libs/` / `.r-libs-native/` (cached packages) and `.cache/` / `.cache-native/`. The generated `.mkexp2/` directory in experiment dirs is excluded by the main repo's `.gitignore`. Legacy output `plots.pdf`, managed output directory `plots/`, and staged external CSVs under `.mkexp2/plot-inputs/` are added to the experiment's `.gitignore` automatically by `mkexp2 plot`.

- **Docker/native R for plots.** `mkexp2 plot` prefers Docker (tested with Colima on macOS). If using Colima, set `DOCKER_HOST="unix://${HOME}/.colima/default/docker.sock"` in your shell profile. If Docker is not available but `Rscript` is on `PATH`, mkexp2 falls back to native R, prepends `plots/.r-libs-native` to `R_LIBS` and `R_LIBS_USER`, preserves existing R library path variables, tries `spack load --sh` for known plotting R packages when available, caches the resolved Spack `R_LIBS` value in `plots/.cache-native/spack-r-libs.txt`, warms that cache at web-server startup, exposes a Settings action to force-refresh it, and installs any still-missing R packages into `plots/.r-libs-native`.

- **Deployment on login.ae.iti.kit.edu.** Do not hotpatch or hotdeploy files on the server. For server updates, commit and push locally first, then SSH to `seemaier@login.ae.iti.kit.edu`, `cd ~/mkexp2`, pull/fast-forward from `origin/main`, and restart the `mkexp2-web` tmux session from the checked-out code. Start tmux through interactive zsh so Spack's `.zshrc` setup is loaded: `zsh -ic 'tmux ...'`. The server web command uses `./bin/mkexp2 web --repo ~/i10-experiments --host 127.0.0.1 --port 8765`.

## Instructions for Codex

- **Always update `AGENTS.md`** after completing any non-trivial task — add new commands, modules, conventions, or environmental notes so the file stays accurate.
