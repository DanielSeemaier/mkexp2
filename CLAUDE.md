# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

# Parse logs into CSV (run after jobs finish):
./bin/mkexp2 parse

# Generate plots from CSV results (requires Docker / Colima):
./bin/mkexp2 plot                        # all algorithms, all plots
./bin/mkexp2 plot KaMinPar-FM KaMinPar-LP  # explicit algorithm list
./bin/mkexp2 plot --performance-profile  # subset of plots
./bin/mkexp2 plot --speedup --running-time

# Tests
./tests/run-all-tests.zsh       # run all tests
./tests/run-probe-tests.zsh     # probe/inspection tests only
./tests/run-e2e-tests.zsh       # end-to-end pipeline tests only
```

Tests require `jq`. They use TAP-style output and call the actual `bin/mkexp2` binary against fixture `Experiment` files — they are integration tests, not unit tests.

There is no linting configuration or CI setup.

## Architecture

### Entry Point & Module Loading

`bin/mkexp2` sources all modules from `bin/inc/` (state, util, dsl, props, plugins, expand, install, generate, parse, plot, check, probe, cli), parses the CLI, discovers `Experiment*()` functions in the user's `Experiment` file, and loops over them.

### Data Flow

1. **DSL layer** (`bin/inc/dsl.sh`): The user's `Experiment` file calls DSL commands (`System`, `Property`, `Algorithms`, `Graphs`, `Threads`, `Ks`, `Seeds`, etc.) which populate global zsh associative arrays (`_algorithms`, `_graphs`, `_ks`, `PROP_GLOBAL`, `PROP_ALGORITHM`, etc.).

2. **Property resolution** (`bin/inc/props.sh`): Six-level priority chain (lowest→highest): partitioner plugin defaults → system plugin defaults → global `Property` → system-level `SystemProperty` → algorithm base `AlgorithmProperty` → algorithm instance `AlgorithmProperty`.

3. **Plugin system** (`bin/inc/plugins.sh`): Shell files in `plugins/partitioners/` and `plugins/launchers/` are lazy-loaded. Each partitioner plugin defines hooks: `PartitionerDefaults_X`, `PartitionerAliases_X`, `PartitionerFetch_X`, `PartitionerBuild_X`, `PartitionerInvoke_X`. Each launcher defines: `LauncherDefaults_Y`, `LauncherWrapCommand_Y`, `LauncherWriteJob_Y`.

4. **Build context** (`bin/inc/install.sh`): `PopulateBuildContext` computes `CTX_*` variables and a content-addressed `CTX_build_key` (SHA1 of `base|url|ref|build_opts|cmake_flags`) for caching. Binaries go to `.mkexp2/bin/<base>-<hash>`.

5. **Expansion engine** (`bin/inc/expand.sh`): `ExpandCurrentExperiment` iterates the Cartesian product of `(topology × algorithm × seed × epsilon × k × graph)`, calling plugin hooks to produce commands. Results go into `EXPAND_CALL` / `EXPAND_JOB` maps.

6. **Generate** (`bin/inc/generate.sh`): Writes `.cmds` files (one line per invocation) and job scripts, then builds a master `submit.sh`.

7. **Parse** (`bin/inc/parse.sh`): Streams log files through awk parsers (`plugins/parsers/*.awk`) using a `__BEGIN_FILE__ <marker>` / `__END_FILE__` protocol. The shared `plugins/parsers/.csv.awk` provides helpers for CSV output.

8. **Probe** (`bin/inc/probe.sh`): Runs expansion in probe mode (`MKEXP2_PROBE_MODE=1`) and serializes the model as JSON.

9. **Plot** (`bin/inc/plot.sh`): Reads the list of active algorithms from the `Experiment` file (or CLI args), writes a Docker Compose file to `.mkexp2/plots-compose.yml`, installs R packages into `plots/.r-libs` on first run (cached), then runs `plots/mkplots.R` inside the container to produce `plots.pdf` in the experiment directory.

### Key Conventions

- **Global state via associative arrays.** All experiment state is in module-level zsh associative arrays. `ResetExperiment` clears them between `Experiment*()` function calls.

- **Output buffers instead of subshells.** Plugin hooks write results to `PARTITIONER_INVOKE_CMD` and `LAUNCHER_WRAPPED_CMD` global variables rather than stdout, to avoid subshell overhead in the generate hot path.

- **Topology encoding.** `T` = 1 node, 1 MPI, T threads. `NxMxT` = N nodes, M MPI ranks per node, T threads per rank. `ParseNodes`, `ParseMpis`, `ParseThreads` in `util.sh` decode this.

- **Log file naming.** `logs/<algorithm>/<experiment_label>/<graph>___k<K>_seed<S>_eps<E>_P<topology>.log` — the filename encodes all parameters needed by the parser.

- **Hidden plugins.** Dot-prefixed files (`.TestHarness.sh`, `.TestHarness.awk`) are internal test fixtures excluded from `--list-*` output.

- **`PartitionerProperty key [fallback]`** — inside plugin hooks, resolves a property for the currently active algorithm. Works consistently during install, generate, and probe phases.

- **TAP test output.** `pass` / `fail` helpers in `tests/lib/test_framework.zsh` print `ok N - msg` / `not ok - msg`. Driver scripts print `1..$TEST_COUNT` at the end.

- **Plot submodule.** `plots/` is a git submodule. R plotting code lives there. The submodule's `.gitignore` excludes `.r-libs/` (cached packages) and `.cache/`. The generated `.mkexp2/` directory in experiment dirs is excluded by the main repo's `.gitignore`. The output `plots.pdf` is added to the experiment's `.gitignore` automatically by `mkexp2 plot`.

- **Docker for plots.** `mkexp2 plot` requires Docker (tested with Colima on macOS). If using Colima, set `DOCKER_HOST="unix://${HOME}/.colima/default/docker.sock"` in your shell profile.

## Instructions for Claude

- **Always update `CLAUDE.md`** after completing any non-trivial task — add new commands, modules, conventions, or environmental notes so the file stays accurate.
