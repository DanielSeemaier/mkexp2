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
  - sub-experiment-level (`SubExperimentProperty`)
- Plugin APIs for:
  - partitioners (`plugins/partitioners/*.sh`)
  - launchers (`plugins/launchers/*.sh`)
- Working launchers:
  - `local` (single machine, shared-memory)
  - `slurm` (supports arrays and sub-experiment dependencies)
- Working partitioner plugins:
  - `Mock` (local smoke tests)
  - `KaMinPar`
  - `dKaMinPar`

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

## DSL essentials

```bash
System local
Property slurm.partition cpuonly
SystemProperty slurm.qos normal
AlgorithmProperty KaMinPar repo_url https://github.com/KaHIP/KaMinPar.git
SubExperiments baseline stress
SubExperimentProperty stress slurm.dependency afterok:baseline
Property slurm.install.mode job
Property slurm.install.timelimit 02:00:00

DefineAlgorithmVersion KaMinPar-Dev KaMinPar origin/my/branch
DefineAlgorithmBuild KaMinPar-Dbg KaMinPar -DCMAKE_BUILD_TYPE=Debug
DefineAlgorithm KaMinPar-FM KaMinPar -P fm

ExperimentMyRun() {
  Algorithms KaMinPar-Dev dKaMinPar
  Graphs /path/to/graphs metis
  Ks 2 4 8
  Seeds 1 2 3
  Threads 1x1x16 2x2x16
}
```

## Plugin contract

Partitioner plugin `X` should define:

- `PartitionerDefaults_X` (optional)
- `PartitionerFetch_X` (optional)
- `PartitionerBuild_X` (required for install)
- `PartitionerInvoke_X` (required for generate)

Launcher plugin `Y` should define:

- `LauncherDefaults_Y` (optional)
- `LauncherWrapCommand_Y` (required)
- `LauncherWriteJob_Y` (required)

## Notes

- This is a scaffold intended to be extended with more partitioners, systems, parsers, and plotting.
- `timelimit.per_instance` maps to `timeout` seconds in generated commands.
- Install command output is concise by default and writes per-command logs to:
  `logs/install/local/<run-id>/commands/`
- On install failures, `mkexp2` prints the failing command log inline.
- Build parallelism defaults to all available cores (`cmake --parallel`).
- To limit build cores, pass a CLI option:
  - `mkexp2 install --build-max-cores <N>`
  - short form: `mkexp2 install -j <N>`
- Slurm can run install as a dedicated dependency job before compute jobs:
  - `Property slurm.install.mode job`
  - optional: `Property slurm.install.timelimit 02:00:00`
  - logs go to: `logs/install/slurm/<run-id>/`
