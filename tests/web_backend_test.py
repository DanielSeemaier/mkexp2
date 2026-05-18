#!/usr/bin/env python3
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mkexp2_web", ROOT / "bin" / "mkexp2_web.py")
mkexp2_web = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mkexp2_web)


class WebBackendTest(unittest.TestCase):
    def test_name_template_and_path_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "%Y.%m.%d-<name>", "token")

            name = mkexp2_web.render_name_template(
                "%Y.%m.%d-<name>",
                "KaMinPar FM Sweep",
                now=mkexp2_web._dt.datetime(2026, 5, 16, 12, 0, 0),
            )
            self.assertEqual(name, "2026.05.16-kaminpar-fm-sweep")

            with self.assertRaises(ValueError):
                app.experiment_path("../escape")
            with self.assertRaises(ValueError):
                app.experiment_path("nested/../escape")
            with self.assertRaises(ValueError):
                app.experiment_path("/escape")
            with self.assertRaises(ValueError):
                app.experiment_path("nested//path")
            self.assertEqual(app.experiment_path("nested/path"), (repo / "nested" / "path").resolve())

    def test_list_experiments_detects_nested_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "flat").mkdir()
            (repo / "flat" / "Experiment").write_text("ExperimentFlat() { :; }\n")
            (repo / "2026" / "run-a").mkdir(parents=True)
            (repo / "2026" / "run-a" / "Experiment").write_text("ExperimentA() { :; }\n")
            (repo / ".git" / "ignored").mkdir(parents=True)
            (repo / ".git" / "ignored" / "Experiment").write_text("ignored\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "%Y.%m.%d-<name>", "token")

            experiments = app.list_experiments()
            ids = [item["id"] for item in experiments]

            self.assertEqual(ids, ["2026/run-a", "flat"])
            nested = experiments[0]
            self.assertEqual(nested["name"], "run-a")
            self.assertEqual(nested["parent"], "2026")
            self.assertEqual(nested["depth"], 2)

    def test_create_experiment_uses_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "fixed-<name>", "token")
            created = app.create_experiment({"name": "My Run"})

            self.assertEqual(created["id"], "fixed-my-run")
            self.assertTrue((repo / "fixed-my-run" / "Experiment").is_file())
            self.assertIn("ExperimentMyRun", (repo / "fixed-my-run" / "Experiment").read_text())

    def test_list_presets_uses_probe_json(self):
        original_run_command = mkexp2_web.run_command
        calls = []

        def fake_run_command(argv, cwd=None, timeout=60):
            calls.append((list(argv), str(cwd) if cwd else None))
            return {
                "returncode": 0,
                "stdout": '{"presets":[{"name":"Default","path":"/mkexp2/presets/Default"}]}',
                "stderr": "",
            }

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            app = mkexp2_web.Mkexp2WebApp(repo, "/fake/mkexp2", "fixed-<name>", "token")
            mkexp2_web.run_command = fake_run_command
            try:
                presets = app.list_presets()
            finally:
                mkexp2_web.run_command = original_run_command

        self.assertEqual(presets[0]["name"], "Default")
        self.assertEqual(calls, [(["/fake/mkexp2", "probe", "--presets"], str(repo.resolve()))])

    def test_create_experiment_from_preset_uses_mkexp2_init(self):
        original_run_command = mkexp2_web.run_command
        calls = []

        def fake_run_command(argv, cwd=None, timeout=60):
            calls.append((list(argv), str(cwd) if cwd else None))
            Path(cwd, "Experiment").write_text("# preset experiment\n")
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            app = mkexp2_web.Mkexp2WebApp(repo, "/fake/mkexp2", "fixed-<name>", "token")
            mkexp2_web.run_command = fake_run_command
            try:
                created = app.create_experiment({"name": "Preset Run", "preset": "Default"})
            finally:
                mkexp2_web.run_command = original_run_command

            exp_path = repo / "fixed-preset-run"
            self.assertEqual(created["id"], "fixed-preset-run")
            self.assertEqual(created["preset"], "Default")
            self.assertEqual((exp_path / "Experiment").read_text(), "# preset experiment\n")

        self.assertEqual(calls, [(["/fake/mkexp2", "init", "Default"], str(exp_path.resolve()))])

    def test_html_contains_syntax_highlighting_editor(self):
        self.assertIn('id="experiment-highlight"', mkexp2_web.HTML)
        self.assertIn("function highlightExperiment", mkexp2_web.HTML)
        self.assertIn("tok-keyword", mkexp2_web.HTML)
        self.assertIn("editor.addEventListener('input'", mkexp2_web.HTML)
        self.assertNotIn("Manual override", mkexp2_web.HTML)
        self.assertNotIn('id="force"', mkexp2_web.HTML)
        self.assertNotIn('id="save"', mkexp2_web.HTML)
        self.assertNotIn('id="probe"', mkexp2_web.HTML)
        self.assertNotIn("async function saveExperiment", mkexp2_web.HTML)
        self.assertIn("async function probeExperiment", mkexp2_web.HTML)
        self.assertIn("function renderProbeResult", mkexp2_web.HTML)
        self.assertNotIn('data-view="probe-view"', mkexp2_web.HTML)
        self.assertIn('class="panel probe-panel"', mkexp2_web.HTML)
        self.assertIn('id="probe-output"', mkexp2_web.HTML)
        self.assertIn('id="probe-run"', mkexp2_web.HTML)
        self.assertIn("Running mkexp2 probe...", mkexp2_web.HTML)
        self.assertNotIn("Saving and probing", mkexp2_web.HTML)
        self.assertNotIn("Saving the Experiment file and running mkexp2 probe", mkexp2_web.HTML)
        self.assertIn("function probeChainText", mkexp2_web.HTML)
        self.assertIn("probe-algorithm-row", mkexp2_web.HTML)
        self.assertIn("probe-primary-field", mkexp2_web.HTML)
        self.assertIn("probe-detail-row", mkexp2_web.HTML)
        self.assertIn("function renderProbeRawJson", mkexp2_web.HTML)
        self.assertIn("Resolved settings (", mkexp2_web.HTML)
        self.assertIn("white-space: pre-wrap", mkexp2_web.HTML)
        self.assertNotIn("probe-algorithm-grid", mkexp2_web.HTML)
        self.assertIn("Probe JSON", mkexp2_web.HTML)
        self.assertIn("async function persistExperiment", mkexp2_web.HTML)
        self.assertIn("function renderCheckResult", mkexp2_web.HTML)
        self.assertIn("function parseCheckJson", mkexp2_web.HTML)
        self.assertIn("Saving and checking...", mkexp2_web.HTML)
        self.assertIn("Check passed with warnings", mkexp2_web.HTML)
        self.assertIn("check-experiments", mkexp2_web.HTML)
        self.assertIn("Command JSON", mkexp2_web.HTML)
        self.assertIn('id="parse-results"', mkexp2_web.HTML)
        self.assertIn('id="plot-results"', mkexp2_web.HTML)
        self.assertIn('data-view="plots-view"', mkexp2_web.HTML)
        self.assertIn('data-view="logs-view"', mkexp2_web.HTML)
        self.assertIn('id="logs-list"', mkexp2_web.HTML)
        self.assertIn('id="log-content"', mkexp2_web.HTML)
        self.assertIn('aria-label="Reload logs"', mkexp2_web.HTML)
        self.assertIn("async function loadLogs", mkexp2_web.HTML)
        self.assertIn("async function loadLogFile", mkexp2_web.HTML)
        self.assertIn("ensureLogsLoaded", mkexp2_web.HTML)
        self.assertLess(
            mkexp2_web.HTML.index('data-view="install-log-view"'),
            mkexp2_web.HTML.index('data-view="experiment-view"'),
        )
        self.assertIn('aria-label="Install Log"', mkexp2_web.HTML)
        self.assertNotIn('data-view="install-log-view">Install Log</button>', mkexp2_web.HTML)
        self.assertIn("setView('experiment-view');", mkexp2_web.HTML)
        self.assertIn('id="plot-action-output"', mkexp2_web.HTML)
        self.assertIn('id="submit-lock-status"', mkexp2_web.HTML)
        self.assertIn('id="clear-submit-lock"', mkexp2_web.HTML)
        self.assertIn('id="refresh-progress"', mkexp2_web.HTML)
        self.assertIn('id="progress-output"', mkexp2_web.HTML)
        self.assertIn("async function parseExperiment", mkexp2_web.HTML)
        self.assertIn("async function loadProgress", mkexp2_web.HTML)
        self.assertIn("async function clearSubmitLock", mkexp2_web.HTML)
        self.assertIn("function renderPlotPanel", mkexp2_web.HTML)
        self.assertNotIn('id="plot"', mkexp2_web.HTML)
        self.assertIn("Select at least one algorithm.", mkexp2_web.HTML)
        self.assertIn("Submit is locked for this experiment", mkexp2_web.HTML)
        self.assertNotIn("Submit is unlocked", mkexp2_web.HTML)
        self.assertIn("mkexp2 check failed. Submit anyway?", mkexp2_web.HTML)

    def test_html_contains_csv_tabs_and_comparison_view(self):
        self.assertIn('data-view="results-view"', mkexp2_web.HTML)
        self.assertNotIn('data-view="compare-view"', mkexp2_web.HTML)
        self.assertIn('id="result-file-tabs"', mkexp2_web.HTML)
        self.assertIn('id="add-compare"', mkexp2_web.HTML)
        self.assertIn('id="clear-compare"', mkexp2_web.HTML)
        self.assertIn('id="compare-controls"', mkexp2_web.HTML)
        self.assertNotIn('id="compare-left"', mkexp2_web.HTML)
        self.assertIn('id="compare-right"', mkexp2_web.HTML)
        self.assertIn('aria-label="Reload CSVs"', mkexp2_web.HTML)
        self.assertNotIn(">Load CSVs</button>", mkexp2_web.HTML)
        self.assertIn("function parseCsv", mkexp2_web.HTML)
        self.assertIn("function csvLabel", mkexp2_web.HTML)
        self.assertIn("function syncCompareScroll", mkexp2_web.HTML)
        self.assertIn("function cycleCompareColumn", mkexp2_web.HTML)
        self.assertIn("compare-good", mkexp2_web.HTML)
        self.assertIn("compare-equal", mkexp2_web.HTML)
        self.assertIn("Cannot compare: row counts differ", mkexp2_web.HTML)
        self.assertIn("mkexp2-columns:", mkexp2_web.HTML)
        self.assertIn("renderCsvTable", mkexp2_web.HTML)
        self.assertIn("state.compareEnabled", mkexp2_web.HTML)

    def test_html_contains_install_log_view(self):
        self.assertIn('data-view="install-log-view"', mkexp2_web.HTML)
        self.assertIn('id="load-install-log"', mkexp2_web.HTML)
        self.assertIn('aria-label="Reload install log"', mkexp2_web.HTML)
        self.assertIn("function renderMarkdown", mkexp2_web.HTML)
        self.assertIn("async function loadInstallLog", mkexp2_web.HTML)
        self.assertIn("ensureInstallLogLoaded", mkexp2_web.HTML)
        self.assertIn("viewId === 'install-log-view'", mkexp2_web.HTML)
        self.assertIn("/install-log", mkexp2_web.HTML)
        self.assertIn("logs/install.md does not exist.", mkexp2_web.HTML)
        self.assertIn("markdown-doc", mkexp2_web.HTML)
        self.assertNotIn(".markdown-doc {\n      border:", mkexp2_web.HTML)
        self.assertIn("cores", mkexp2_web.HTML)

    def test_install_log_result_handles_missing_and_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            (exp / "logs").mkdir(parents=True)
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")

            missing = app.install_log("exp")
            self.assertFalse(missing["exists"])
            self.assertTrue(missing["path"].endswith("logs/install.md"))

            (exp / "logs" / "install.md").write_text("# mkexp2 install log\n\n```console\nok\n```\n")
            existing = app.install_log("exp")
            self.assertTrue(existing["exists"])
            self.assertIn("# mkexp2 install log", existing["content"])
            self.assertFalse(existing["truncated"])

    def test_logs_are_listed_and_loaded_on_demand(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            log_dir = exp / "logs" / "Algo" / "Run"
            log_dir.mkdir(parents=True)
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            (log_dir / "a.log").write_text("first log\n")
            (log_dir / "b.log").write_text("second log\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")

            root = app.list_logs("exp")
            self.assertTrue(root["exists"])
            self.assertEqual(root["entries"][0]["type"], "dir")
            self.assertEqual(root["entries"][0]["path"], "Algo")
            self.assertNotIn("content", root["entries"][0])

            nested = app.list_logs("exp", "Algo/Run")
            self.assertEqual([entry["name"] for entry in nested["entries"]], ["a.log", "b.log"])
            self.assertNotIn("content", nested["entries"][0])

            loaded = app.log_file("exp", "Algo/Run/a.log")
            self.assertIn("first log", loaded["content"])
            self.assertEqual(loaded["relative_path"], "Algo/Run/a.log")

            with self.assertRaises(ValueError):
                app.log_file("exp", "../Experiment")

    def test_submit_lock_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            (exp / ".mkexp2").mkdir(parents=True)
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")

            unlocked = app.submit_lock("exp")
            self.assertFalse(unlocked["locked"])

            (exp / ".mkexp2" / "submit.lock").write_text("started_at=2026-05-18T10:00:00Z\nalgorithms=Mock\n")
            locked = app.submit_lock("exp")
            self.assertTrue(locked["locked"])
            self.assertEqual(locked["fields"]["algorithms"], "Mock")

            cleared = app.clear_submit_lock("exp")
            self.assertTrue(cleared["cleared"])
            self.assertFalse(cleared["submit_lock"]["locked"])

    def test_progress_uses_argv_array_and_strips_ansi(self):
        calls = []
        original_run_command = mkexp2_web.run_command

        def fake_run_command(argv, cwd=None, timeout=60):
            self.assertIsInstance(argv, list)
            calls.append((list(argv), str(cwd) if cwd else None, timeout))
            return {"returncode": 0, "stdout": "\x1b[32mExperiment\x1b[0m  1 / 2\n", "stderr": "", "elapsed_seconds": 0.1}

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "exp").mkdir()
            (repo / "exp" / "Experiment").write_text("ExperimentX() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, "/fake/mkexp2", "x-<name>", "token")
            mkexp2_web.run_command = fake_run_command
            try:
                result = app.progress("exp")
            finally:
                mkexp2_web.run_command = original_run_command

        self.assertTrue(result["ok"])
        self.assertEqual(result["progress"]["stdout"], "Experiment  1 / 2\n")
        self.assertEqual(calls, [(["/fake/mkexp2", "progress"], str((repo / "exp").resolve()), 60)])

    def test_submit_action_uses_argv_arrays(self):
        calls = []
        original_run_command = mkexp2_web.run_command

        def fake_run_command(argv, cwd=None, timeout=60):
            self.assertIsInstance(argv, list)
            calls.append((list(argv), str(cwd) if cwd else None))
            if argv[:2] == ["git", "diff"]:
                return {"returncode": 1, "stdout": "", "stderr": ""}
            return {"returncode": 0, "stdout": '{"experiments":[]}', "stderr": ""}

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "exp").mkdir()
            (repo / "exp" / "Experiment").write_text("ExperimentX() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, "/fake/mkexp2", "x-<name>", "token")
            mkexp2_web.run_command = fake_run_command
            try:
                action = app.submit_action("exp", {"algorithms": ["MockA"], "force": False})
                for _ in range(100):
                    current = app.actions.get(action["id"])
                    if current["status"] != "running":
                        break
                    time.sleep(0.02)
                self.assertEqual(app.actions.get(action["id"])["status"], "completed")
            finally:
                mkexp2_web.run_command = original_run_command

        exp_cwd = str((repo / "exp").resolve())
        self.assertIn((["/fake/mkexp2", "check", "--json"], exp_cwd), calls)
        self.assertIn((["/fake/mkexp2", "generate"], exp_cwd), calls)
        self.assertIn((["zsh", "./submit.sh", "--install", "MockA"], exp_cwd), calls)
        self.assertTrue(any(call[0][:3] == ["git", "commit", "-m"] for call in calls))

    def test_parse_action_uses_argv_array(self):
        calls = []
        original_run_command = mkexp2_web.run_command

        def fake_run_command(argv, cwd=None, timeout=60):
            self.assertIsInstance(argv, list)
            calls.append((list(argv), str(cwd) if cwd else None, timeout))
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "exp").mkdir()
            (repo / "exp" / "Experiment").write_text("ExperimentX() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, "/fake/mkexp2", "x-<name>", "token")
            mkexp2_web.run_command = fake_run_command
            try:
                action = app.parse_action("exp")
                for _ in range(100):
                    current = app.actions.get(action["id"])
                    if current["status"] != "running":
                        break
                    time.sleep(0.02)
                result = app.actions.get(action["id"])["result"]
            finally:
                mkexp2_web.run_command = original_run_command

        self.assertTrue(result["parsed"])
        self.assertEqual(calls, [(["/fake/mkexp2", "parse"], str((repo / "exp").resolve()), 600)])

    def test_slurm_parsers(self):
        scontrol = """NodeName=node01 Arch=x86_64 CPUTot=64 RealMemory=257000 State=ALLOCATED
   Partitions=cpu Gres=gpu:2 AvailableFeatures=zen4 Reason=None

NodeName=node02 Arch=x86_64 CPUTot=64 RealMemory=257000 State=IDLE
   Partitions=cpu Gres=(null) AvailableFeatures=zen4 Reason=None
"""
        nodes = mkexp2_web.parse_scontrol_nodes(scontrol)
        self.assertEqual(nodes["node01"]["cpus"], "64")
        self.assertEqual(nodes["node02"]["state"], "IDLE")

        jobs = mkexp2_web.parse_squeue_jobs("42|node[01-02]|alice|bench|RUNNING|2026-05-16T10:00:00|12:34\n")
        self.assertEqual(jobs[0]["node_names"], ["node01", "node02"])
        self.assertEqual(jobs[0]["user"], "alice")

        sinfo = mkexp2_web.parse_sinfo_nodes("node01|cpu|32/32/0/64|257000|gpu:2|zen4|mix|none\n")
        self.assertEqual(sinfo["node01"]["partition"], "cpu")
        self.assertEqual(sinfo["node01"]["cpu_info"], "32/32/0/64")

        long_sinfo = mkexp2_web.parse_sinfo_long_nodes(mkexp2_web.SINFO_LONG_FALLBACK)
        self.assertEqual(len(long_sinfo), 17)
        self.assertEqual(long_sinfo["diffie"]["cpus"], "192")
        self.assertEqual(long_sinfo["diffie"]["availability"], "idle")
        self.assertEqual(long_sinfo["backus"]["availability"], "used")
        self.assertEqual(long_sinfo["feigenbaum"]["features"], "gpu")
        self.assertEqual(long_sinfo["hamming"]["reason"], "ResumeTimeout reache")

    def test_slurm_status_falls_back_when_sinfo_is_missing(self):
        calls = []
        original_run_command = mkexp2_web.run_command

        def fake_run_command(argv, cwd=None, timeout=60):
            calls.append(list(argv))
            if argv[:4] == ["sinfo", "-lN", "-p", "all"]:
                return {"returncode": 127, "stdout": "", "stderr": "sinfo not found"}
            if argv and argv[0] == "squeue":
                return {
                    "returncode": 0,
                    "stdout": "99|backus|alice|bench|RUNNING|2026-05-16T15:00:00|57:01\n",
                    "stderr": "",
                }
            return {"returncode": 0, "stdout": "", "stderr": ""}

        mkexp2_web.run_command = fake_run_command
        try:
            status = mkexp2_web.SlurmStatus().get()
        finally:
            mkexp2_web.run_command = original_run_command

        self.assertIn(["sinfo", "-lN", "-p", "all"], calls)
        self.assertEqual(status["source"], "fallback sample: sinfo not installed")
        self.assertEqual(len(status["nodes"]), 17)
        backus = next(node for node in status["nodes"] if node["name"] == "backus")
        self.assertEqual(backus["jobs"][0]["user"], "alice")
        self.assertEqual(backus["jobs"][0]["start_time"], "2026-05-16T15:00:00")


if __name__ == "__main__":
    unittest.main()
