#!/usr/bin/env python3
import importlib.util
import inspect
import json
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mkexp2_web", ROOT / "bin" / "mkexp2_web.py")
mkexp2_web = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mkexp2_web)


class WebBackendTest(unittest.TestCase):
    def test_run_command_closes_child_stdin(self):
        original_run = mkexp2_web.subprocess.run
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(kwargs)
            return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

        mkexp2_web.subprocess.run = fake_run
        try:
            result = mkexp2_web.run_command(["mkexp2", "check"])
        finally:
            mkexp2_web.subprocess.run = original_run

        self.assertEqual(result["returncode"], 0)
        self.assertEqual(calls[0]["stdin"], subprocess.DEVNULL)

    def test_action_store_reuses_running_unique_action(self):
        store = mkexp2_web.ActionStore()
        started = threading.Event()
        release = threading.Event()

        def target():
            started.set()
            release.wait(timeout=2)
            return {"ok": True}

        first = store.start_unique("plot:run", "plot run", target)
        self.assertTrue(started.wait(timeout=1))
        second = store.start_unique("plot:run", "plot duplicate", lambda: {"ok": False})
        self.assertEqual(first["id"], second["id"])

        release.set()
        for _ in range(20):
            if store.get(first["id"])["status"] == "completed":
                break
            time.sleep(0.05)
        self.assertEqual(store.get(first["id"])["status"], "completed")

        third = store.start_unique("plot:run", "plot run again", lambda: {"ok": True})
        self.assertNotEqual(first["id"], third["id"])

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
            (repo / "2026" / "run-a" / ".mkexp2").mkdir()
            (repo / "2026" / "run-a" / ".mkexp2" / "submit.lock").write_text("started_at=2026-05-22T12:00:00Z\n")
            (repo / "2026" / "old.archived").mkdir(parents=True)
            (repo / "2026" / "old.archived" / "Experiment").write_text("ExperimentOld() { :; }\n")
            (repo / ".git" / "ignored").mkdir(parents=True)
            (repo / ".git" / "ignored" / "Experiment").write_text("ignored\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "%Y.%m.%d-<name>", "token")

            experiments = app.list_experiments()
            ids = [item["id"] for item in experiments]
            archived = app.list_archived_experiments()

            self.assertEqual(ids, ["2026/run-a", "flat"])
            self.assertEqual([item["id"] for item in archived], ["2026/old.archived"])
            self.assertEqual(archived[0]["name"], "old")
            self.assertTrue(archived[0]["archived"])
            nested = experiments[0]
            self.assertEqual(nested["name"], "run-a")
            self.assertEqual(nested["parent"], "2026")
            self.assertEqual(nested["depth"], 2)
            self.assertIn("created_at", nested)
            self.assertIsInstance(nested["created_at_epoch"], float)
            self.assertTrue(nested["submit_lock"]["locked"])
            self.assertEqual(nested["submit_lock"]["fields"]["started_at"], "2026-05-22T12:00:00Z")

    def test_list_experiments_prefers_git_index_and_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            (repo / "tracked").mkdir()
            (repo / "tracked" / "Experiment").write_text("ExperimentTracked() { :; }\n")
            subprocess.run(["git", "add", "tracked/Experiment"], cwd=repo, check=True)
            (repo / "untracked").mkdir()
            (repo / "untracked" / "Experiment").write_text("ExperimentUntracked() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "%Y.%m.%d-<name>", "token")

            first = app.list_experiments()
            self.assertEqual([item["id"] for item in first], ["tracked", "untracked"])
            self.assertFalse(first[0]["submit_lock"]["locked"])

            (repo / "tracked" / ".mkexp2").mkdir()
            (repo / "tracked" / ".mkexp2" / "submit.lock").write_text("algorithms=Mock\n")
            cached_with_lock = app.list_experiments()
            tracked = next(item for item in cached_with_lock if item["id"] == "tracked")
            self.assertTrue(tracked["submit_lock"]["locked"])
            self.assertEqual(tracked["submit_lock"]["fields"]["algorithms"], "Mock")

            (repo / "later").mkdir()
            (repo / "later" / "Experiment").write_text("ExperimentLater() { :; }\n")
            cached = app.list_experiments()
            self.assertEqual([item["id"] for item in cached], ["tracked", "untracked"])
            refreshed = app.list_experiments(force=True)
            self.assertEqual([item["id"] for item in refreshed], ["later", "tracked", "untracked"])

    def test_experiment_discovery_skips_stale_git_paths_after_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
            (repo / "tracked").mkdir()
            (repo / "tracked" / "Experiment").write_text("ExperimentTracked() { :; }\n")
            subprocess.run(["git", "add", "tracked/Experiment"], cwd=repo, check=True)
            (repo / "tracked").rename(repo / "tracked.archived")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "%Y.%m.%d-<name>", "token")

            self.assertEqual(app.list_experiments(force=True), [])
            archived = app.list_archived_experiments(force=True)
            self.assertEqual([item["id"] for item in archived], ["tracked.archived"])

    def test_create_experiment_uses_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "fixed-<name>", "token")
            created = app.create_experiment({"name": "My Run"})

            self.assertEqual(created["id"], "fixed-my-run")
            self.assertTrue((repo / "fixed-my-run" / "Experiment").is_file())
            self.assertIn("ExperimentMyRun", (repo / "fixed-my-run" / "Experiment").read_text())
            self.assertEqual(app.config()["name_template"], "fixed-<name>")

            overridden = app.create_experiment({"name": "Other Run", "name_template": "custom/<name>"})
            self.assertEqual(overridden["id"], "custom/other-run")
            self.assertTrue((repo / "custom" / "other-run" / "Experiment").is_file())

            with self.assertRaises(ValueError):
                app.create_experiment({"name": "Hidden", "name_template": "<name>.archived"})

    def test_copy_experiment_uses_source_experiment_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            source = repo / "source"
            source.mkdir()
            source_text = "ExperimentSource() {\n  Algorithms Feature\n}\n"
            (source / "Experiment").write_text(source_text, encoding="utf-8")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "fixed-<name>", "token")

            copied = app.copy_experiment("source", {"name": "Fork Run", "name_template": "forks/<name>"})

            self.assertEqual(copied["id"], "forks/fork-run")
            self.assertEqual(copied["source_id"], "source")
            self.assertEqual((repo / "forks" / "fork-run" / "Experiment").read_text(encoding="utf-8"), source_text)
            self.assertEqual((repo / "source" / "Experiment").read_text(encoding="utf-8"), source_text)
            self.assertIn("forks/fork-run", [item["id"] for item in app.list_experiments(force=True)])

            with self.assertRaises(ValueError):
                app.copy_experiment("source", {"name": "Fork Run", "name_template": "forks/<name>"})
            with self.assertRaises(ValueError):
                app.copy_experiment("missing", {"name": "Other"})
            with self.assertRaises(ValueError):
                app.copy_experiment("source", {"name": "Hidden", "name_template": "<name>.archived"})

    def test_experiment_archive_can_filter_top_level_directories(self):
        original_which = mkexp2_web.shutil.which
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            (exp / "results").mkdir(parents=True)
            (exp / "logs").mkdir()
            (exp / "jobs").mkdir()
            (exp / "Experiment").write_text("ExperimentA() { :; }\n", encoding="utf-8")
            (exp / "description.md").write_text("notes\n", encoding="utf-8")
            (exp / "results" / "A.csv").write_text("x\n", encoding="utf-8")
            (exp / "logs" / "A.log").write_text("log\n", encoding="utf-8")
            (exp / "jobs" / "run.sh").write_text("job\n", encoding="utf-8")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "%Y.%m.%d-<name>", "token")
            mkexp2_web.shutil.which = lambda _name: None
            try:
                options = app.experiment_download_options("exp")
                self.assertEqual([item["name"] for item in options["directories"]], ["jobs", "logs", "results"])
                self.assertIn("Experiment", options["root_files"])

                archive = app.experiment_archive("exp", include_dirs=["results"])
                try:
                    with zipfile.ZipFile(archive["path"]) as zip_file:
                        names = set(zip_file.namelist())
                    self.assertIn("exp/Experiment", names)
                    self.assertIn("exp/description.md", names)
                    self.assertIn("exp/results/A.csv", names)
                    self.assertNotIn("exp/logs/A.log", names)
                    self.assertNotIn("exp/jobs/run.sh", names)
                finally:
                    archive["path"].unlink(missing_ok=True)

                with self.assertRaises(ValueError):
                    app.experiment_archive("exp", include_dirs=["../logs"])
                with self.assertRaises(ValueError):
                    app.experiment_archive("exp", include_dirs=["missing"])
            finally:
                mkexp2_web.shutil.which = original_which

    def test_column_visibility_is_server_side_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            exp.mkdir()
            (exp / "Experiment").write_text("ExperimentA() { :; }\n", encoding="utf-8")
            other = repo / "other"
            other.mkdir()
            (other / "Experiment").write_text("ExperimentA() { :; }\n", encoding="utf-8")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "%Y.%m.%d-<name>", "token")

            saved = app.write_column_visibility("exp", {
                "visibility": {
                    "A\u001fB\u001fC": ["A", "C", "A"],
                    "D": [],
                }
            })

            self.assertTrue(saved["saved"])
            self.assertEqual(saved["visibility"]["A\u001fB\u001fC"], ["A", "C"])
            self.assertEqual(saved["visibility"]["D"], [])
            self.assertEqual(app.column_visibility("exp")["visibility"]["A\u001fB\u001fC"], ["A", "C"])
            self.assertEqual(app.column_visibility("other")["visibility"]["A\u001fB\u001fC"], ["A", "C"])
            self.assertTrue((repo / ".mkexp2" / "web-column-visibility.json").is_file())
            self.assertEqual(app.global_column_visibility()["visibility"]["A\u001fB\u001fC"], ["A", "C"])

            global_saved = app.write_global_column_visibility({"visibility": {"A\u001fB\u001fC": ["A", "B", "C"]}})
            self.assertTrue(global_saved["saved"])
            self.assertEqual(app.column_visibility("other")["visibility"]["A\u001fB\u001fC"], ["A", "B", "C"])

            renamed = app.rename_experiment("exp", {"new_id": "renamed"})
            self.assertEqual(renamed["new_id"], "renamed")
            self.assertEqual(app.column_visibility("renamed")["visibility"]["A\u001fB\u001fC"], ["A", "B", "C"])
            self.assertEqual(app.column_visibility("other")["visibility"]["A\u001fB\u001fC"], ["A", "B", "C"])
            archived = app.archive_experiment("renamed")
            self.assertEqual(archived["archived_id"], "renamed.archived")
            unarchived = app.unarchive_experiment("renamed.archived")
            self.assertEqual(unarchived["active_id"], "renamed")
            self.assertEqual(app.column_visibility("renamed")["visibility"]["A\u001fB\u001fC"], ["A", "B", "C"])

            app.delete_experiment("renamed")
            self.assertEqual(app.column_visibility("other")["visibility"]["A\u001fB\u001fC"], ["A", "B", "C"])

            with self.assertRaises(ValueError):
                app.write_column_visibility("missing", {"visibility": {}})
            with self.assertRaises(ValueError):
                app.write_column_visibility("exp", {"visibility": []})
            with self.assertRaises(ValueError):
                app.write_global_column_visibility({"visibility": []})

    def test_legacy_column_visibility_file_is_read_as_global(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            exp.mkdir()
            (exp / "Experiment").write_text("ExperimentA() { :; }\n", encoding="utf-8")
            other = repo / "other"
            other.mkdir()
            (other / "Experiment").write_text("ExperimentA() { :; }\n", encoding="utf-8")
            state_dir = repo / ".mkexp2"
            state_dir.mkdir()
            (state_dir / "web-column-visibility.json").write_text(json.dumps({
                "experiments": {
                    "exp": {"A\u001fB": ["A"]},
                }
            }), encoding="utf-8")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "%Y.%m.%d-<name>", "token")

            self.assertEqual(app.column_visibility("other")["visibility"]["A\u001fB"], ["A"])

    def test_web_settings_persist_theme(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "%Y.%m.%d-<name>", "token")

            self.assertEqual(app.read_settings()["theme"], "light")
            saved = app.write_settings({"theme": "dark"})
            self.assertTrue(saved["saved"])
            self.assertEqual(saved["theme"], "dark")
            self.assertEqual(app.read_settings()["theme"], "dark")
            self.assertTrue((repo / ".mkexp2" / "web-settings.json").is_file())
            self.assertEqual(app.write_settings({"theme": "bad"})["theme"], "light")

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

    def test_describe_catalog_uses_single_json_command(self):
        original_run_command = mkexp2_web.run_command
        calls = []

        def fake_run_command(argv, cwd=None, timeout=60):
            calls.append((list(argv), str(cwd) if cwd else None, timeout))
            return {
                "returncode": 0,
                "stdout": '{"ok":true,"partitioners":[{"name":"KaMinPar","aliases":[]}],"systems":[{"name":"local"}]}',
                "stderr": "",
            }

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            app = mkexp2_web.Mkexp2WebApp(repo, "/fake/mkexp2", "fixed-<name>", "token")
            mkexp2_web.run_command = fake_run_command
            try:
                catalog = app.describe_catalog()
            finally:
                mkexp2_web.run_command = original_run_command

        self.assertEqual(catalog["partitioners"][0]["name"], "KaMinPar")
        self.assertEqual(calls, [(["/fake/mkexp2", "describe", "--all", "--json"], str(repo.resolve()), 45)])

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
        self.assertNotIn('id="probe-summary"', mkexp2_web.HTML)
        self.assertNotIn('id="progress-summary"', mkexp2_web.HTML)
        self.assertNotIn('id="danger-summary"', mkexp2_web.HTML)
        self.assertNotIn("No probe loaded.", mkexp2_web.HTML)
        self.assertNotIn("No progress loaded.", mkexp2_web.HTML)
        self.assertNotIn("Manual recovery, rename", mkexp2_web.HTML)
        self.assertIn("Running mkexp2 probe...", mkexp2_web.HTML)
        self.assertNotIn("Saving and probing", mkexp2_web.HTML)
        self.assertNotIn("Saving the Experiment file and running mkexp2 probe", mkexp2_web.HTML)
        self.assertIn("function probeChainText", mkexp2_web.HTML)
        self.assertIn("probe-algorithm-row", mkexp2_web.HTML)
        self.assertIn("probe-primary-field", mkexp2_web.HTML)
        self.assertIn("probe-detail-row", mkexp2_web.HTML)
        self.assertNotIn("function renderProbeRawJson", mkexp2_web.HTML)
        self.assertNotIn("Raw algorithm JSON", mkexp2_web.HTML)
        self.assertIn("function renderProbeInputs", mkexp2_web.HTML)
        self.assertIn("probe-input-grid", mkexp2_web.HTML)
        self.assertIn("renderProbeInputCard('Graphs'", mkexp2_web.HTML)
        self.assertIn("renderProbeInputCard('K'", mkexp2_web.HTML)
        self.assertIn("renderProbeInputCard('Eps'", mkexp2_web.HTML)
        self.assertIn("renderProbeInputCard('Seeds'", mkexp2_web.HTML)
        self.assertIn("Resolved settings", mkexp2_web.HTML)
        self.assertNotIn("Resolved settings (", mkexp2_web.HTML)
        self.assertIn("white-space: pre-wrap", mkexp2_web.HTML)
        self.assertNotIn("probe-algorithm-grid", mkexp2_web.HTML)
        self.assertNotIn("Probe JSON", mkexp2_web.HTML)
        self.assertIn("async function persistExperiment", mkexp2_web.HTML)
        self.assertIn("function renderCheckResult", mkexp2_web.HTML)
        self.assertIn('class="panel describe-panel"', mkexp2_web.HTML)
        self.assertIn('id="describe-toggle"', mkexp2_web.HTML)
        self.assertIn('id="describe-search"', mkexp2_web.HTML)
        self.assertIn('data-describe-filter="algorithms"', mkexp2_web.HTML)
        self.assertIn("describeFilter: 'algorithms'", mkexp2_web.HTML)
        self.assertIn("margin-bottom: 12px;", mkexp2_web.HTML)
        self.assertIn('id="describe-output"', mkexp2_web.HTML)
        self.assertIn("async function loadDescribeCatalog", mkexp2_web.HTML)
        self.assertIn("function describeAliasMatches", mkexp2_web.HTML)
        self.assertNotIn("Direct plugin name, no alias CLI arguments.", mkexp2_web.HTML)
        self.assertIn(".describe-chip.copyable", mkexp2_web.HTML)
        self.assertIn(".describe-chip.copied", mkexp2_web.HTML)
        self.assertIn("Copied value", mkexp2_web.HTML)
        self.assertIn("copyTextToClipboard(value)", mkexp2_web.HTML)
        self.assertIn("{ copyValue: value }", mkexp2_web.HTML)
        self.assertNotIn("base ${alias.base", mkexp2_web.HTML)
        self.assertIn("/api/describe", mkexp2_web.HTML)
        self.assertIn("function parseCheckJson", mkexp2_web.HTML)
        self.assertIn("flags: ['--all', '--algorithms']", mkexp2_web.HTML)
        self.assertIn("selectMostRecent: true", mkexp2_web.HTML)
        self.assertIn("function mostRecentExperiment", mkexp2_web.HTML)
        self.assertIn("selectExperiment(latest.id)", mkexp2_web.HTML)
        self.assertIn("function formatExperimentDate", mkexp2_web.HTML)
        self.assertIn("${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()}", mkexp2_web.HTML)
        self.assertIn("date.textContent = created || 'unknown'", mkexp2_web.HTML)
        self.assertIn("selectedPathText(data.path, data)", mkexp2_web.HTML)
        self.assertIn('id="copy-experiment"', mkexp2_web.HTML)
        self.assertIn('id="copy-modal"', mkexp2_web.HTML)
        self.assertIn("async function copyExperiment", mkexp2_web.HTML)
        self.assertIn("/copy", mkexp2_web.HTML)
        self.assertIn("Create a new experiment from the current Experiment file.", mkexp2_web.HTML)
        self.assertIn('id="download-modal"', mkexp2_web.HTML)
        self.assertIn('id="download-directories"', mkexp2_web.HTML)
        self.assertIn("download-options", mkexp2_web.HTML)
        self.assertIn("function selectedDownloadDirectories", mkexp2_web.HTML)
        self.assertIn("Root files are always included", mkexp2_web.HTML)
        self.assertIn("columnVisibility: {}", mkexp2_web.HTML)
        self.assertIn("function columnSignature", mkexp2_web.HTML)
        self.assertIn('id="settings-hidden-columns"', mkexp2_web.HTML)
        self.assertIn("function hiddenColumnGroups", mkexp2_web.HTML)
        self.assertIn("async function removeHiddenColumnDefault", mkexp2_web.HTML)
        self.assertIn("/api/columns", mkexp2_web.HTML)
        self.assertIn("No globally hidden columns.", mkexp2_web.HTML)
        self.assertIn("/columns", mkexp2_web.HTML)
        self.assertIn("Column visibility save failed", mkexp2_web.HTML)
        self.assertNotIn("mkexp2-columns:", mkexp2_web.HTML)
        self.assertIn('id="check-indicator"', mkexp2_web.HTML)
        self.assertIn("function setCheckIndicator", mkexp2_web.HTML)
        self.assertIn("function clearCheckIndicator", mkexp2_web.HTML)
        self.assertIn("mkexp2 check passed.", mkexp2_web.HTML)
        self.assertIn("Saving and checking...", mkexp2_web.HTML)
        self.assertIn("Check passed with warnings", mkexp2_web.HTML)
        self.assertIn("check-experiments", mkexp2_web.HTML)
        self.assertNotIn("Action JSON", mkexp2_web.HTML)
        self.assertIn('id="parse-results"', mkexp2_web.HTML)
        self.assertIn('id="plot-results"', mkexp2_web.HTML)
        self.assertIn('id="plot-indicator" class="check-indicator hidden"', mkexp2_web.HTML)
        self.assertIn('data-view="plots-view"', mkexp2_web.HTML)
        self.assertNotIn('data-view="stats-view"', mkexp2_web.HTML)
        self.assertIn('id="stats-output"', mkexp2_web.HTML)
        self.assertIn('aria-label="Generate stats"', mkexp2_web.HTML)
        self.assertIn("Generate stats to summarize parsed CSV results.", mkexp2_web.HTML)
        self.assertIn("Generating...", mkexp2_web.HTML)
        self.assertLess(
            mkexp2_web.HTML.index('id="stats-output"'),
            mkexp2_web.HTML.index('class="csv-tools"'),
        )
        self.assertIn("Run Quality", mkexp2_web.HTML)
        self.assertIn("Cut Quality", mkexp2_web.HTML)
        self.assertIn("Fair-set values are computed only on common rows", mkexp2_web.HTML)
        self.assertIn("Fair balanced", mkexp2_web.HTML)
        self.assertNotIn("async function ensureStatsLoaded", mkexp2_web.HTML)
        self.assertNotIn("Measured n", mkexp2_web.HTML)
        self.assertIn("function renderStatsWorkspace", mkexp2_web.HTML)
        self.assertIn("async function loadStats", mkexp2_web.HTML)
        self.assertIn("/stats", mkexp2_web.HTML)
        self.assertIn('data-view="logs-view"', mkexp2_web.HTML)
        self.assertIn('id="logs-list"', mkexp2_web.HTML)
        self.assertIn('id="log-content"', mkexp2_web.HTML)
        self.assertIn('<div id="logs-path" class="logs-path"></div>', mkexp2_web.HTML)
        self.assertIn("pathLabel.textContent = dir ? `logs/${dir}/` : 'logs/'", mkexp2_web.HTML)
        self.assertNotIn('id="logs-summary"', mkexp2_web.HTML)
        self.assertIn('aria-label="Reload logs"', mkexp2_web.HTML)
        self.assertIn("async function loadLogs", mkexp2_web.HTML)
        self.assertIn("async function loadLogFile", mkexp2_web.HTML)
        self.assertIn("ensureLogsLoaded", mkexp2_web.HTML)
        self.assertNotIn('data-view="install-log-view"', mkexp2_web.HTML)
        self.assertNotIn('id="load-install-log"', mkexp2_web.HTML)
        self.assertIn("setView('experiment-view').catch", mkexp2_web.HTML)
        self.assertNotIn('id="plots-summary"', mkexp2_web.HTML)
        self.assertNotIn('id="plot-action-output"', mkexp2_web.HTML)
        self.assertIn('id="plot-add-open"', mkexp2_web.HTML)
        self.assertIn('aria-label="Add plot artifacts"', mkexp2_web.HTML)
        self.assertIn('id="plot-running" class="plot-generation-status hidden"', mkexp2_web.HTML)
        self.assertIn('id="plot-generate-modal"', mkexp2_web.HTML)
        self.assertIn('id="plot-generate-modal-title" class="modal-title">Add Plot Artifacts</div>', mkexp2_web.HTML)
        self.assertIn('id="plot-generate-close"', mkexp2_web.HTML)
        self.assertIn('id="plot-generate-cancel"', mkexp2_web.HTML)
        self.assertIn('id="plot-no-docker"', mkexp2_web.HTML)
        self.assertIn("No docker", mkexp2_web.HTML)
        self.assertLess(mkexp2_web.HTML.index('id="plot-no-docker-label"'), mkexp2_web.HTML.index('id="plot-results"'))
        self.assertLess(mkexp2_web.HTML.index('id="plot-running"'), mkexp2_web.HTML.index('id="plot-indicator"'))
        self.assertLess(mkexp2_web.HTML.index('id="plot-indicator"'), mkexp2_web.HTML.index('id="plot-add-open"'))
        self.assertIn("function setPlotIndicator", mkexp2_web.HTML)
        self.assertIn("function clearPlotIndicator", mkexp2_web.HTML)
        self.assertIn("plotActionTooltip", mkexp2_web.HTML)
        self.assertIn("setPlotIndicator(actionSucceeded(action, 'plot-artifacts')", mkexp2_web.HTML)
        self.assertNotIn("renderActionStatus", mkexp2_web.HTML)
        self.assertNotIn("action-card", mkexp2_web.HTML)
        self.assertNotIn(".plot-box {\n      display: grid;\n      gap: 8px;\n      min-width: 0;\n      padding:", mkexp2_web.HTML)
        self.assertIn("async function loadPlotBackendStatus", mkexp2_web.HTML)
        self.assertIn("function applyPlotBackendStatus", mkexp2_web.HTML)
        self.assertIn("/api/plot/backend", mkexp2_web.HTML)
        self.assertIn("/api/plots/catalog", mkexp2_web.HTML)
        self.assertIn("/plot-sources", mkexp2_web.HTML)
        self.assertIn("/plot-artifacts", mkexp2_web.HTML)
        self.assertIn('id="plot-catalog"', mkexp2_web.HTML)
        self.assertIn('id="plot-sources"', mkexp2_web.HTML)
        self.assertIn('id="plot-artifacts"', mkexp2_web.HTML)
        self.assertIn('id="plot-source-modal"', mkexp2_web.HTML)
        self.assertIn("async function openPlotGenerateDialog", mkexp2_web.HTML)
        self.assertIn("function closePlotGenerateDialog", mkexp2_web.HTML)
        self.assertIn("state.plotGenerationRunning", mkexp2_web.HTML)
        self.assertIn("plotSourceOpenDirs", mkexp2_web.HTML)
        self.assertIn("renderPlotSourceTree", mkexp2_web.HTML)
        self.assertIn("selectedPlotSourceObjects", mkexp2_web.HTML)
        self.assertIn("removeExternalPlotSource", mkexp2_web.HTML)
        self.assertIn("plotArtifactView", mkexp2_web.HTML)
        self.assertIn("plot-view-sets", mkexp2_web.HTML)
        self.assertIn("plot-view-types", mkexp2_web.HTML)
        self.assertIn('class="plot-artifact-browser"', mkexp2_web.HTML)
        self.assertIn('class="plot-box plot-artifact-sidebar"', mkexp2_web.HTML)
        self.assertLess(mkexp2_web.HTML.index('id="plot-artifacts"'), mkexp2_web.HTML.index('id="plot-file"'))
        self.assertIn("grid-template-columns: minmax(220px, 26%) minmax(0, 1fr);", mkexp2_web.HTML)
        self.assertIn(".plot-artifact-sidebar .plot-artifact-toolbar", mkexp2_web.HTML)
        self.assertIn("flex-direction: row;", mkexp2_web.HTML)
        self.assertIn("plot-artifact-open", mkexp2_web.HTML)
        self.assertIn("renamePlotArtifactSet", mkexp2_web.HTML)
        self.assertIn("deletePlotArtifactSet", mkexp2_web.HTML)
        self.assertIn("deletePlotArtifact", mkexp2_web.HTML)
        self.assertIn("/plot-artifact-sets/", mkexp2_web.HTML)
        self.assertIn("plots: Array.from(state.selectedPlotTypes)", mkexp2_web.HTML)
        self.assertIn("const PLOT_RELOAD_DELAY_MS = 5000", mkexp2_web.HTML)
        self.assertIn("if (action?.status === 'running')", mkexp2_web.HTML)
        self.assertIn('id="clear-submit-lock"', mkexp2_web.HTML)
        self.assertIn('id="rename-experiment"', mkexp2_web.HTML)
        self.assertIn('id="delete-experiment"', mkexp2_web.HTML)
        self.assertIn('class="view-tabs"', mkexp2_web.HTML)
        self.assertIn('class="view-tabs-spacer"', mkexp2_web.HTML)
        self.assertIn('id="share-experiment" class="icon-button" aria-label="Share experiment"', mkexp2_web.HTML)
        self.assertIn('id="download-experiment" class="icon-button" aria-label="Download experiment archive"', mkexp2_web.HTML)
        self.assertIn("async function downloadExperiment", mkexp2_web.HTML)
        self.assertIn("function fetchDownload", mkexp2_web.HTML)
        self.assertIn("/download", mkexp2_web.HTML)
        self.assertNotIn('<button id="share-experiment">Share</button>', mkexp2_web.HTML)
        self.assertIn('id="share-modal"', mkexp2_web.HTML)
        self.assertIn('id="share-ssh"', mkexp2_web.HTML)
        self.assertIn('id="share-link"', mkexp2_web.HTML)
        self.assertIn('id="share-username"', mkexp2_web.HTML)
        self.assertIn('id="share-command"', mkexp2_web.HTML)
        self.assertIn('id="share-copy-command"', mkexp2_web.HTML)
        self.assertIn("function renderShareCommand", mkexp2_web.HTML)
        self.assertIn("colleague_command_template", mkexp2_web.HTML)
        self.assertIn("__SHARE_ID__", mkexp2_web.HTML)
        self.assertIn("share-mode", mkexp2_web.HTML)
        self.assertNotIn(".app.share-mode .probe-panel", mkexp2_web.HTML)
        self.assertNotIn(".app.share-mode .describe-panel", mkexp2_web.HTML)
        self.assertIn("if (path === '/api/describe') return `/api/share/${encodeURIComponent(state.shareId)}/describe`;", mkexp2_web.HTML)
        self.assertIn('if tail == "describe":', (ROOT / "bin" / "mkexp2_web.py").read_text(encoding="utf-8"))
        self.assertNotIn(".app.share-mode #download-experiment", mkexp2_web.HTML)
        self.assertIn(".app.share-mode .editor-shell", mkexp2_web.HTML)
        self.assertIn(".app.share-mode #experiment-editor", mkexp2_web.HTML)
        self.assertIn("background: transparent;", mkexp2_web.HTML)
        self.assertIn("editor.readOnly = true", mkexp2_web.HTML)
        self.assertIn("setEditorValue(data.experiment)", mkexp2_web.HTML)
        self.assertIn("/share", mkexp2_web.HTML)
        self.assertNotIn("Shared experiment view. Editing, submission, and destructive actions are disabled.", mkexp2_web.HTML)
        self.assertIn('id="archive-open"', mkexp2_web.HTML)
        self.assertIn('aria-label="Archived experiments"', mkexp2_web.HTML)
        self.assertIn('id="archive-modal"', mkexp2_web.HTML)
        self.assertIn('id="archive-refresh"', mkexp2_web.HTML)
        self.assertIn('id="archive-experiment"', mkexp2_web.HTML)
        self.assertIn("archivedOpenDirs", mkexp2_web.HTML)
        self.assertIn("renderArchivedExperimentTree", mkexp2_web.HTML)
        self.assertIn("/api/experiments/archived", mkexp2_web.HTML)
        self.assertIn("/archive", mkexp2_web.HTML)
        self.assertIn("/unarchive", mkexp2_web.HTML)
        self.assertIn('Danger Zone', mkexp2_web.HTML)
        self.assertIn("margin-top: 14px;", mkexp2_web.HTML)
        self.assertIn("Type the full experiment name to delete it", mkexp2_web.HTML)
        self.assertIn("function renderSubmitButton", mkexp2_web.HTML)
        self.assertIn(".experiment-row.locked", mkexp2_web.HTML)
        self.assertNotIn("border-left: 4px solid transparent", mkexp2_web.HTML)
        self.assertNotIn(".experiment-row.active {\n      border-color: var(--accent);\n      border-left: 4px solid var(--accent)", mkexp2_web.HTML)
        self.assertNotIn(".experiment-row.locked {\n      border-left: 4px solid var(--danger)", mkexp2_web.HTML)
        self.assertNotIn(".experiment-row.active.locked", mkexp2_web.HTML)
        self.assertIn(".experiment-row.locked .experiment-name", mkexp2_web.HTML)
        self.assertIn("color: var(--danger);", mkexp2_web.HTML)
        self.assertIn('id="experiment-tag-select"', mkexp2_web.HTML)
        self.assertNotIn('id="tag-modal"', mkexp2_web.HTML)
        self.assertNotIn('id="tag-open"', mkexp2_web.HTML)
        self.assertIn('id="tag-save"', mkexp2_web.HTML)
        self.assertIn('id="tag-color" type="hidden"', mkexp2_web.HTML)
        self.assertIn('id="tag-color-palette"', mkexp2_web.HTML)
        self.assertIn("DEFAULT_TAG_COLOR_PALETTE", mkexp2_web.HTML)
        self.assertNotIn('type="color"', mkexp2_web.HTML)
        self.assertIn('class="settings-section"', mkexp2_web.HTML)
        self.assertIn("async function deleteTag", mkexp2_web.HTML)
        self.assertIn("/api/tags/${encodeURIComponent(name)}", mkexp2_web.HTML)
        self.assertIn('id="archive-codex-experiments"', mkexp2_web.HTML)
        self.assertIn("async function archiveCodexExperiments", mkexp2_web.HTML)
        self.assertIn("/api/tags/Codex/archive-experiments", mkexp2_web.HTML)
        self.assertIn("starred or submit-locked", mkexp2_web.HTML)
        self.assertLess(mkexp2_web.HTML.index('class="tag-controls"'), mkexp2_web.HTML.index('id="share-experiment"'))
        self.assertIn(".experiment-row.tagged", mkexp2_web.HTML)
        self.assertIn("border-left: 4px solid var(--experiment-tag-color)", mkexp2_web.HTML)
        self.assertIn("/api/tags", mkexp2_web.HTML)
        self.assertIn("/tag", mkexp2_web.HTML)
        self.assertIn("function updateSelectedExperimentLock", mkexp2_web.HTML)
        self.assertIn("function submitLockText", mkexp2_web.HTML)
        self.assertIn("async function renameExperiment", mkexp2_web.HTML)
        self.assertIn("async function shareExperiment", mkexp2_web.HTML)
        self.assertIn("/rename", mkexp2_web.HTML)
        self.assertIn("/share", mkexp2_web.HTML)
        self.assertIn("Cannot rename while submit is locked.", mkexp2_web.HTML)
        self.assertIn("Cannot archive while submit is locked.", mkexp2_web.HTML)
        self.assertIn("Cannot delete while submit is locked.", mkexp2_web.HTML)
        self.assertIn("state.submitBusy", mkexp2_web.HTML)
        self.assertIn("algorithmLoading", mkexp2_web.HTML)
        self.assertIn("selectionSeq", mkexp2_web.HTML)
        self.assertIn("function renderAlgorithmLoading", mkexp2_web.HTML)
        self.assertIn("Loading algorithms...", mkexp2_web.HTML)
        self.assertIn("state.selected !== experimentId", mkexp2_web.HTML)
        self.assertIn("button.is-busy", mkexp2_web.HTML)
        self.assertIn('id="refresh-progress"', mkexp2_web.HTML)
        self.assertIn('id="progress-output"', mkexp2_web.HTML)
        self.assertIn("async function parseExperiment", mkexp2_web.HTML)
        self.assertIn("async function loadProgress", mkexp2_web.HTML)
        self.assertIn("function renderProgressLoading", mkexp2_web.HTML)
        self.assertIn("Loading progress...", mkexp2_web.HTML)
        self.assertIn("progressLoadSeq", mkexp2_web.HTML)
        self.assertIn("loadProgress({ experimentId: id })", mkexp2_web.HTML)
        self.assertIn("progress_json", mkexp2_web.HTML)
        self.assertIn("progress-experiment", mkexp2_web.HTML)
        self.assertIn(".progress-experiment + .progress-experiment", mkexp2_web.HTML)
        self.assertIn("}, 15000)", mkexp2_web.HTML)
        self.assertIn("async function clearSubmitLock", mkexp2_web.HTML)
        self.assertIn("function renderPlotPanel", mkexp2_web.HTML)
        self.assertIn("async function loadPlotInfo", mkexp2_web.HTML)
        self.assertIn("async function loadPlotPdf", mkexp2_web.HTML)
        self.assertIn("function fetchBlob", mkexp2_web.HTML)
        self.assertIn("function clearPlotPdfUrl", mkexp2_web.HTML)
        self.assertIn("URL.createObjectURL", mkexp2_web.HTML)
        self.assertIn("URL.revokeObjectURL", mkexp2_web.HTML)
        self.assertIn("/plots", mkexp2_web.HTML)
        self.assertIn('class="plot-pdf"', mkexp2_web.HTML)
        self.assertIn("Generate a plot artifact to preview it here.", mkexp2_web.HTML)
        self.assertIn('id="git-open"', mkexp2_web.HTML)
        self.assertIn('aria-label="Git status"', mkexp2_web.HTML)
        self.assertIn('id="settings-open"', mkexp2_web.HTML)
        self.assertIn('aria-label="Settings"', mkexp2_web.HTML)
        self.assertIn('id="settings-modal"', mkexp2_web.HTML)
        self.assertIn('id="settings-close"', mkexp2_web.HTML)
        self.assertIn('<label for="token">Session token</label>', mkexp2_web.HTML)
        self.assertIn('id="theme-dark-toggle"', mkexp2_web.HTML)
        self.assertIn('Dark mode', mkexp2_web.HTML)
        self.assertIn('data-theme="dark"', mkexp2_web.HTML)
        self.assertIn("--tab-active-bg: #0f766e;", mkexp2_web.HTML)
        self.assertIn("background: var(--tab-active-bg);", mkexp2_web.HTML)
        self.assertIn("color: var(--tab-active-text);", mkexp2_web.HTML)
        self.assertIn("THEME_STORAGE_KEY", mkexp2_web.HTML)
        self.assertIn("async function loadUiSettings", mkexp2_web.HTML)
        self.assertIn("async function saveTheme", mkexp2_web.HTML)
        self.assertIn("/api/settings", mkexp2_web.HTML)
        self.assertIn('id="spack-cache-refresh"', mkexp2_web.HTML)
        self.assertIn("Resolve Spack R cache", mkexp2_web.HTML)
        self.assertIn("/api/plot/spack-r-libs", mkexp2_web.HTML)
        self.assertIn("/api/plot/spack-r-libs/resolve", mkexp2_web.HTML)
        self.assertIn('aria-label="Close CSV source dialog"', mkexp2_web.HTML)
        self.assertNotIn('>x</button>', mkexp2_web.HTML)
        self.assertNotIn('id="console-log"', mkexp2_web.HTML)
        self.assertNotIn('id="console-summary"', mkexp2_web.HTML)
        self.assertNotIn('id="console-clear"', mkexp2_web.HTML)
        self.assertIn("function setButtonBusy", mkexp2_web.HTML)
        self.assertIn("async function withBusyButton", mkexp2_web.HTML)
        self.assertIn("button.setAttribute('aria-busy', 'true')", mkexp2_web.HTML)
        self.assertIn("withBusyButton('refresh-status'", mkexp2_web.HTML)
        self.assertIn("withBusyButton('plot-add-open'", mkexp2_web.HTML)
        self.assertIn("withBusyButton('plot-results'", mkexp2_web.HTML)
        self.assertIn("withBusyButton('parse-results'", mkexp2_web.HTML)
        self.assertIn("function logApiCommands", mkexp2_web.HTML)
        self.assertIn("function collectCommandResults", mkexp2_web.HTML)
        self.assertIn("const allowEmptyToken = __ALLOW_EMPTY_TOKEN__;", mkexp2_web.HTML)
        self.assertIn("token() || allowEmptyToken", mkexp2_web.HTML)
        self.assertIn('id="create-open" class="icon-button" aria-label="Create experiment"', mkexp2_web.HTML)
        self.assertIn('class="panel-header experiment-editor-header"', mkexp2_web.HTML)
        self.assertIn('class="experiment-editor-heading"', mkexp2_web.HTML)
        self.assertIn(".experiment-editor-header .check-action", mkexp2_web.HTML)
        self.assertIn("overflow-wrap: anywhere;", mkexp2_web.HTML)
        self.assertIn('id="create-modal"', mkexp2_web.HTML)
        self.assertIn('id="create-name-prefix"', mkexp2_web.HTML)
        self.assertIn('id="create-name"', mkexp2_web.HTML)
        self.assertIn('id="create-preset"', mkexp2_web.HTML)
        self.assertIn('id="create-template-override"', mkexp2_web.HTML)
        self.assertIn('id="create-template"', mkexp2_web.HTML)
        self.assertIn("function renderTemplateForDate", mkexp2_web.HTML)
        self.assertIn("function openCreateDialog", mkexp2_web.HTML)
        self.assertIn("name_template: nameTemplate", mkexp2_web.HTML)
        self.assertIn("/api/config", mkexp2_web.HTML)
        self.assertNotIn('id="refresh" class="icon-button" aria-label="Refresh experiments"', mkexp2_web.HTML)
        self.assertIn('id="refresh-status" class="icon-button" aria-label="Reload node status"', mkexp2_web.HTML)
        self.assertIn('id="queue-open" class="icon-button" aria-label="Show Slurm queue"', mkexp2_web.HTML)
        self.assertIn('id="queue-modal"', mkexp2_web.HTML)
        self.assertIn('id="queue-refresh" class="icon-button" aria-label="Reload Slurm queue"', mkexp2_web.HTML)
        self.assertIn('id="queue-cancel-all" class="queue-cancel-all"', mkexp2_web.HTML)
        self.assertIn('id="sidebar-resizer"', mkexp2_web.HTML)
        self.assertIn('aria-label="Resize sidebar"', mkexp2_web.HTML)
        self.assertIn("const SIDEBAR_WIDTH_KEY = 'mkexp2-sidebar-width'", mkexp2_web.HTML)
        self.assertIn("function initSidebarResize", mkexp2_web.HTML)
        self.assertIn("compareExperimentsByCreatedDesc", mkexp2_web.HTML)
        self.assertIn("latest: 0", mkexp2_web.HTML)
        self.assertIn("function renderQueue", mkexp2_web.HTML)
        self.assertIn("function cancelQueueJob", mkexp2_web.HTML)
        self.assertIn("function cancelAllQueueJobs", mkexp2_web.HTML)
        self.assertIn("api('/api/status/squeue/cancel'", mkexp2_web.HTML)
        self.assertIn("api('/api/status/squeue/cancel-all'", mkexp2_web.HTML)
        self.assertIn("This runs: scancel -u", mkexp2_web.HTML)
        self.assertIn("confirm_user: owner", mkexp2_web.HTML)
        self.assertIn("row.user === data.server_user", mkexp2_web.HTML)
        self.assertIn("button.textContent = 'x'", mkexp2_web.HTML)
        self.assertIn("button.setAttribute('aria-label'", mkexp2_web.HTML)
        self.assertIn("/api/status/squeue", mkexp2_web.HTML)
        self.assertIn('id="git-refresh" class="icon-button" aria-label="Reload Git status"', mkexp2_web.HTML)
        self.assertIn('id="refresh-progress" class="icon-button" aria-label="Reload progress"', mkexp2_web.HTML)
        self.assertLess(mkexp2_web.HTML.index('id="create-open"'), mkexp2_web.HTML.index('id="git-open"'))
        self.assertLess(mkexp2_web.HTML.index('id="git-open"'), mkexp2_web.HTML.index('id="settings-open"'))
        self.assertIn("pinnedExperiments", mkexp2_web.HTML)
        self.assertIn("function togglePinnedExperiment", mkexp2_web.HTML)
        self.assertIn("function renderPinnedExperiments", mkexp2_web.HTML)
        self.assertIn("/api/pins", mkexp2_web.HTML)
        self.assertNotIn('id="new-name"', mkexp2_web.HTML)
        self.assertNotIn('id="new-preset"', mkexp2_web.HTML)
        self.assertNotIn('id="create">Create</button>', mkexp2_web.HTML)
        self.assertNotIn('id="refresh"', mkexp2_web.HTML)
        self.assertNotIn('Refresh experiments', mkexp2_web.HTML)
        self.assertNotIn('id="refresh-status" class="small-button">Update</button>', mkexp2_web.HTML)
        self.assertNotIn('<button id="git-refresh">Refresh</button>', mkexp2_web.HTML)
        self.assertNotIn('<button id="refresh-progress">Refresh</button>', mkexp2_web.HTML)
        self.assertNotIn('id="output"', mkexp2_web.HTML)
        self.assertNotIn('<div class="panel-title">Output</div>', mkexp2_web.HTML)
        self.assertIn('id="git-modal"', mkexp2_web.HTML)
        self.assertIn('id="git-message"', mkexp2_web.HTML)
        self.assertIn('id="git-push"', mkexp2_web.HTML)
        self.assertIn(".git-file-kind", mkexp2_web.HTML)
        self.assertIn("No added, modified, or deleted files.", mkexp2_web.HTML)
        self.assertIn("item.className = `git-file ${key}`", mkexp2_web.HTML)
        self.assertNotIn(".git-status-column.added", mkexp2_web.HTML)
        self.assertIn("async function openGitDialog", mkexp2_web.HTML)
        self.assertIn("async function pushGitChanges", mkexp2_web.HTML)
        self.assertIn("function closeVisibleModal", mkexp2_web.HTML)
        self.assertIn("event.key === 'Escape'", mkexp2_web.HTML)
        self.assertIn("/api/git/status", mkexp2_web.HTML)
        self.assertIn("/api/git/push", mkexp2_web.HTML)
        self.assertNotIn('id="plot"', mkexp2_web.HTML)
        self.assertIn("Select at least one algorithm.", mkexp2_web.HTML)
        self.assertIn("Submit locked", mkexp2_web.HTML)
        self.assertNotIn("Submit is unlocked", mkexp2_web.HTML)
        self.assertNotIn('id="submit-lock-status"', mkexp2_web.HTML)
        self.assertIn("mkexp2 check failed. Submit anyway?", mkexp2_web.HTML)
        self.assertIn("state.editorDirty = true", mkexp2_web.HTML)
        self.assertIn("await persistExperiment();", mkexp2_web.HTML)
        self.assertIn("loadAlgorithms(experimentId, {", mkexp2_web.HTML)
        self.assertIn("function collectSubmitSelections", mkexp2_web.HTML)
        self.assertIn("selectedSelections: allSelectedBeforeSave ? null : priorSelection.selections", mkexp2_web.HTML)
        self.assertIn("data-experiment", mkexp2_web.HTML)
        self.assertIn("submit-algorithm-group", mkexp2_web.HTML)
        self.assertIn('id="submit" aria-label="Submit selected algorithms"', mkexp2_web.HTML)
        self.assertIn("function submitPlayIconHtml", mkexp2_web.HTML)
        self.assertNotIn("Submit Selected", mkexp2_web.HTML)
        self.assertNotIn(".submit-algorithm-group {\n      display: grid;\n      gap: 8px;\n      min-width: 0;\n      border: 1px solid var(--border);", mkexp2_web.HTML)
        self.assertIn("JSON.stringify({ selections, force })", mkexp2_web.HTML)

    def test_empty_token_bypass_is_explicitly_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            default_app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")
            default_handler = mkexp2_web.make_handler(default_app)
            default_request = default_handler.__new__(default_handler)
            default_request.headers = {"X-MKEXP2-Token": ""}
            self.assertFalse(default_request.require_token())

            dev_app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token", allow_empty_token=True)
            dev_handler = mkexp2_web.make_handler(dev_app)
            dev_request = dev_handler.__new__(dev_handler)
            dev_request.headers = {"X-MKEXP2-Token": ""}
            self.assertTrue(dev_request.require_token())

    def test_delete_routes_share_one_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")
            handler = mkexp2_web.make_handler(app)
            source = inspect.getsource(handler.do_DELETE)

        file_source = (ROOT / "bin" / "mkexp2_web.py").read_text(encoding="utf-8")
        self.assertEqual(file_source.count("def do_DELETE(self):"), 1)
        self.assertIn("/plot-artifacts/", source)
        self.assertIn("/plot-artifact-sets/", source)
        self.assertIn("/submit-lock", source)
        self.assertIn("app.delete_experiment", source)

    def test_html_contains_csv_tabs_and_comparison_view(self):
        self.assertIn('data-view="results-view"', mkexp2_web.HTML)
        self.assertNotIn('data-view="compare-view"', mkexp2_web.HTML)
        self.assertIn('id="result-file-tabs"', mkexp2_web.HTML)
        self.assertNotIn('id="add-compare"', mkexp2_web.HTML)
        self.assertNotIn('id="clear-compare"', mkexp2_web.HTML)
        self.assertNotIn('id="compare-controls"', mkexp2_web.HTML)
        self.assertNotIn('id="compare-left"', mkexp2_web.HTML)
        self.assertNotIn('id="compare-right"', mkexp2_web.HTML)
        self.assertIn('aria-label="Reload CSVs"', mkexp2_web.HTML)
        self.assertNotIn(">Load CSVs</button>", mkexp2_web.HTML)
        self.assertIn("function parseCsv", mkexp2_web.HTML)
        self.assertIn("function csvLabel", mkexp2_web.HTML)
        self.assertIn("function syncCompareScroll", mkexp2_web.HTML)
        self.assertIn("function cycleCompareColumn", mkexp2_web.HTML)
        self.assertIn("compare-good", mkexp2_web.HTML)
        self.assertIn("compare-equal", mkexp2_web.HTML)
        self.assertIn("compare-mid", mkexp2_web.HTML)
        self.assertIn("Cannot compare: row counts differ", mkexp2_web.HTML)
        self.assertIn("function columnSignature", mkexp2_web.HTML)
        self.assertIn("Column visibility save failed", mkexp2_web.HTML)
        self.assertNotIn("mkexp2-columns:", mkexp2_web.HTML)
        self.assertIn("renderCsvTable", mkexp2_web.HTML)
        self.assertIn("selectedResults", mkexp2_web.HTML)
        self.assertIn("previousSelection", mkexp2_web.HTML)
        self.assertIn("preservedSelection", mkexp2_web.HTML)
        self.assertIn("aria-pressed", mkexp2_web.HTML)
        self.assertIn("results-stats", mkexp2_web.HTML)
        self.assertNotIn("state.compareEnabled", mkexp2_web.HTML)

    def test_html_renders_install_markdown_from_logs_view(self):
        self.assertNotIn('data-view="install-log-view"', mkexp2_web.HTML)
        self.assertNotIn('id="load-install-log"', mkexp2_web.HTML)
        self.assertNotIn("/install-log", mkexp2_web.HTML)
        self.assertIn("function renderMarkdown", mkexp2_web.HTML)
        self.assertIn("/\\.md$/i.test(state.logContent.relative_path || '')", mkexp2_web.HTML)
        self.assertIn("renderMarkdown(state.logContent.content || '', content)", mkexp2_web.HTML)
        self.assertIn("markdown-doc", mkexp2_web.HTML)
        self.assertNotIn(".markdown-doc {\n      border:", mkexp2_web.HTML)
        self.assertIn("cores", mkexp2_web.HTML)

    def test_html_contains_description_panel(self):
        self.assertIn("Description", mkexp2_web.HTML)
        self.assertNotIn('id="description-summary"', mkexp2_web.HTML)
        self.assertIn('id="description-rendered"', mkexp2_web.HTML)
        self.assertIn('id="description-editor"', mkexp2_web.HTML)
        self.assertIn('id="description-edit"', mkexp2_web.HTML)
        self.assertIn('id="description-save"', mkexp2_web.HTML)
        self.assertIn('id="description-cancel"', mkexp2_web.HTML)
        self.assertIn("async function loadDescription", mkexp2_web.HTML)
        self.assertIn("function renderDescriptionWorkspace", mkexp2_web.HTML)
        self.assertIn("function editDescription", mkexp2_web.HTML)
        self.assertIn("async function saveDescription", mkexp2_web.HTML)
        self.assertIn("/description", mkexp2_web.HTML)
        self.assertNotIn("description.md does not exist yet.", mkexp2_web.HTML)
        self.assertIn(".app.share-mode .description-edit-actions", mkexp2_web.HTML)

    def test_install_log_is_regular_markdown_log_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            (exp / "logs").mkdir(parents=True)
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")

            empty = app.list_logs("exp")
            self.assertTrue(empty["exists"])
            self.assertEqual(empty["entries"], [])

            (exp / "logs" / "install.md").write_text("# mkexp2 install log\n\n```console\nok\n```\n")
            root = app.list_logs("exp")
            self.assertIn("install.md", [entry["path"] for entry in root["entries"]])
            existing = app.log_file("exp", "install.md")
            self.assertIn("# mkexp2 install log", existing["content"])
            self.assertFalse(existing["truncated"])

    def test_description_result_handles_missing_existing_and_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            exp.mkdir()
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")

            missing = app.description("exp")
            self.assertFalse(missing["exists"])
            self.assertTrue(missing["path"].endswith("description.md"))

            saved = app.write_description("exp", "# Why\n\nSome **notes**.\n")
            self.assertTrue(saved["saved"])
            self.assertTrue(saved["exists"])
            self.assertEqual((exp / "description.md").read_text(), "# Why\n\nSome **notes**.\n")

            existing = app.description("exp")
            self.assertIn("# Why", existing["content"])
            self.assertFalse(existing["truncated"])

    def test_experiment_archive_download_contains_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            exp.mkdir()
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            (exp / "description.md").write_text("# Notes\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")

            archive = app.experiment_archive("exp")
            try:
                self.assertTrue(archive["path"].is_file())
                self.assertIn(archive["format"], {"tar.zst", "zip"})
                self.assertTrue(archive["filename"].startswith("exp."))
                if archive["format"] == "zip":
                    with zipfile.ZipFile(archive["path"]) as zip_file:
                        self.assertIn("exp/Experiment", zip_file.namelist())
                        self.assertIn("exp/description.md", zip_file.namelist())
            finally:
                archive["path"].unlink(missing_ok=True)

    def test_plots_info_handles_missing_and_existing_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            exp.mkdir()
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")

            missing = app.plots_info("exp")
            self.assertFalse(missing["exists"])
            self.assertTrue(missing["path"].endswith("plots.pdf"))

            (exp / "plots.pdf").write_bytes(b"%PDF-1.4\n")
            existing = app.plots_info("exp")
            self.assertTrue(existing["exists"])
            self.assertEqual(existing["size"], len(b"%PDF-1.4\n"))
            self.assertIn("modified_at", existing)

    def test_logs_are_listed_and_loaded_on_demand(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            log_dir = exp / "logs" / "Algo" / "Run"
            log_dir.mkdir(parents=True)
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            (exp / "logs" / "install.md").write_text("# install log\n")
            (log_dir / "a.log").write_text("first log\n")
            (log_dir / "b.log").write_text("second log\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")

            root = app.list_logs("exp")
            self.assertTrue(root["exists"])
            self.assertEqual(root["entries"][0]["type"], "dir")
            self.assertEqual(root["entries"][0]["name"], "Algo/Run")
            self.assertEqual(root["entries"][0]["path"], "Algo/Run")
            self.assertIn("install.md", [entry["path"] for entry in root["entries"]])
            self.assertNotIn("content", root["entries"][0])

            nested = app.list_logs("exp", "Algo/Run")
            self.assertEqual([entry["name"] for entry in nested["entries"]], ["a.log", "b.log"])
            self.assertNotIn("content", nested["entries"][0])

            loaded = app.log_file("exp", "Algo/Run/a.log")
            self.assertIn("first log", loaded["content"])
            self.assertEqual(loaded["relative_path"], "Algo/Run/a.log")

            with self.assertRaises(ValueError):
                app.log_file("exp", "../Experiment")

    def test_pinned_experiments_are_stored_server_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "a").mkdir()
            (repo / "b").mkdir()
            (repo / "a" / "Experiment").write_text("ExperimentA() { :; }\n")
            (repo / "b" / "Experiment").write_text("ExperimentB() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")

            saved = app.write_pins(["b", "missing", "a", "b"])
            self.assertEqual(saved["pinned"], ["b", "a"])
            self.assertTrue((repo / ".mkexp2" / "web-pins.json").is_file())

            loaded = app.read_pins()
            self.assertEqual(loaded["pinned"], ["b", "a"])

    def test_experiment_tags_are_stored_server_side(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "a").mkdir()
            (repo / "b").mkdir()
            (repo / "a" / "Experiment").write_text("ExperimentA() { :; }\n")
            (repo / "b" / "Experiment").write_text("ExperimentB() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")

            defaults = app.read_tags()
            self.assertIn({"name": "Codex", "color": "#2563eb"}, defaults["tags"])
            self.assertIn({"name": "Blue", "color": "#2563eb"}, defaults["palette"])
            self.assertIn("Codex", defaults["default_tags"])

            updated = app.upsert_tag({"name": "Review", "color": "#0f766e"})
            self.assertIn({"name": "Review", "color": "#0f766e"}, updated["tags"])
            assigned = app.assign_experiment_tag("a", "Codex")
            self.assertEqual(assigned["tag"]["name"], "Codex")
            listed = {item["id"]: item for item in app.list_experiments(force=True)}
            self.assertEqual(listed["a"]["tag"]["name"], "Codex")
            self.assertEqual(listed["a"]["tag"]["color"], "#2563eb")

            renamed = app.rename_experiment("a", {"new_id": "renamed"})
            self.assertEqual(renamed["new_id"], "renamed")
            self.assertIsNone(app.tag_for_experiment("a"))
            self.assertEqual(app.tag_for_experiment("renamed")["name"], "Codex")

            created = app.create_experiment({"name": "Generated", "tag": "Codex"})
            self.assertEqual(created["tag"]["name"], "Codex")
            self.assertEqual(app.tag_for_experiment(created["id"])["color"], "#2563eb")

            app.assign_experiment_tag("b", "Review")
            deleted = app.delete_tag("Review")
            self.assertNotIn({"name": "Review", "color": "#0f766e"}, deleted["tags"])
            self.assertIsNone(app.tag_for_experiment("b"))
            with self.assertRaisesRegex(ValueError, "default tag cannot be deleted"):
                app.delete_tag("Codex")

            cleared = app.assign_experiment_tag("renamed", "")
            self.assertIsNone(cleared["tag"])
            self.assertNotIn("renamed", app.read_tags()["assignments"])

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

    def test_share_experiment_persists_tokenless_read_only_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "2026" / "exp"
            exp.mkdir(parents=True)
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(
                repo,
                ROOT / "bin" / "mkexp2",
                "x-<name>",
                "token",
                web_host="127.0.0.1",
                web_port=9876,
            )

            result = app.share_experiment("2026/exp")
            share_id = result["share"]["id"]
            self.assertEqual(result["share"]["experiment_id"], "2026/exp")
            self.assertIn(f"http://127.0.0.1:9876/share/{share_id}", result["share_url"])
            self.assertIn("ssh -L 9876:127.0.0.1:9876", result["ssh_tunnel"])
            self.assertIn("<user>@", result["ssh_tunnel"])
            self.assertIn("ssh -fN -L 9876:127.0.0.1:9876", result["colleague_command_template"])
            self.assertIn("<user>@", result["colleague_command_template"])
            self.assertIn("python3 -m webbrowser", result["colleague_command_template"])
            self.assertIn(result["share_url"], result["colleague_command_template"])
            self.assertEqual(app.resolve_share(share_id)["experiment_id"], "2026/exp")
            metadata = app.share_metadata(share_id)
            self.assertTrue(metadata["read_only"])
            self.assertEqual(metadata["experiment"]["id"], "2026/exp")

    def test_shared_plot_sources_are_limited_to_shared_experiment(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for name in ["exp", "other"]:
                path = repo / name
                (path / "results").mkdir(parents=True)
                (path / "Experiment").write_text("ExperimentX() { :; }\n")
                (path / "results" / "Algo.csv").write_text("Graph,Cores,Time\nG,1,1\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")

            with self.assertRaisesRegex(ValueError, "shared plot sources"):
                app.create_shared_plot_artifacts_action(
                    "exp",
                    {"plots": ["running-time-box"], "sources": [{"kind": "csv", "experiment_id": "other", "file": "Algo.csv"}]},
                )
            with self.assertRaisesRegex(ValueError, "shared plot sources"):
                app.create_shared_plot_artifacts_action(
                    "exp",
                    {"plots": ["running-time-box"], "sources": ["other/Algo.csv"]},
                )

    def test_delete_experiment_removes_directory_and_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "2026" / "exp"
            exp.mkdir(parents=True)
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            (exp / "logs").mkdir()
            (exp / "logs" / "run.log").write_text("done\n")
            other = repo / "other"
            other.mkdir()
            (other / "Experiment").write_text("ExperimentY() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")
            app.write_pins(["2026/exp", "other"])

            deleted = app.delete_experiment("2026/exp")
            self.assertTrue(deleted["deleted"])
            self.assertFalse(exp.exists())
            self.assertEqual(app.read_pins()["pinned"], ["other"])

            with self.assertRaises(ValueError):
                app.delete_experiment("2026/exp")

    def test_delete_experiment_rejects_submit_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            exp.mkdir()
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            (exp / ".mkexp2").mkdir()
            (exp / ".mkexp2" / "submit.lock").write_text("started_at=now\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")

            with self.assertRaisesRegex(ValueError, "submit is locked"):
                app.delete_experiment("exp")
            self.assertTrue((exp / "Experiment").is_file())

    def test_rename_experiment_moves_directory_and_updates_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "2026" / "exp"
            exp.mkdir(parents=True)
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            other = repo / "other"
            other.mkdir()
            (other / "Experiment").write_text("ExperimentY() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")
            app.write_pins(["2026/exp", "other"])

            renamed = app.rename_experiment("2026/exp", {"new_id": "2026/renamed"})
            self.assertTrue(renamed["renamed"])
            self.assertEqual(renamed["new_id"], "2026/renamed")
            self.assertFalse(exp.exists())
            self.assertTrue((repo / "2026" / "renamed" / "Experiment").is_file())
            self.assertEqual([item["id"] for item in app.list_experiments(force=True)], ["2026/renamed", "other"])
            self.assertEqual(app.read_pins()["pinned"], ["2026/renamed", "other"])

    def test_rename_experiment_rejects_locks_collisions_and_bad_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for path in [repo / "exp", repo / "target"]:
                path.mkdir()
                (path / "Experiment").write_text("ExperimentX() { :; }\n")
            (repo / "exp" / ".mkexp2").mkdir()
            (repo / "exp" / ".mkexp2" / "submit.lock").write_text("started_at=now\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")

            with self.assertRaises(ValueError):
                app.rename_experiment("exp", {"new_id": "new"})
            (repo / "exp" / ".mkexp2" / "submit.lock").unlink()
            with self.assertRaises(ValueError):
                app.rename_experiment("exp", {"new_id": "target"})
            with self.assertRaises(ValueError):
                app.rename_experiment("exp", {"new_id": "../escape"})
            with self.assertRaises(ValueError):
                app.rename_experiment("exp", {"new_id": "exp.archived"})
            with self.assertRaises(ValueError):
                app.rename_experiment("exp", {"new_id": ".hidden/exp"})

    def test_archive_and_unarchive_experiment_renames_leaf_and_updates_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "2026" / "exp"
            exp.mkdir(parents=True)
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            other = repo / "other"
            other.mkdir()
            (other / "Experiment").write_text("ExperimentY() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")
            app.write_pins(["2026/exp", "other"])

            archived = app.archive_experiment("2026/exp")
            self.assertTrue(archived["archived"])
            self.assertEqual(archived["archived_id"], "2026/exp.archived")
            self.assertFalse(exp.exists())
            self.assertTrue((repo / "2026" / "exp.archived" / "Experiment").is_file())
            self.assertEqual([item["id"] for item in app.list_experiments(force=True)], ["other"])
            self.assertEqual([item["id"] for item in app.list_archived_experiments(force=True)], ["2026/exp.archived"])
            self.assertEqual(app.read_pins()["pinned"], ["other"])
            with self.assertRaises(ValueError):
                app.results("2026/exp.archived")

            restored = app.unarchive_experiment("2026/exp.archived")
            self.assertTrue(restored["unarchived"])
            self.assertEqual(restored["active_id"], "2026/exp")
            self.assertTrue((repo / "2026" / "exp" / "Experiment").is_file())
            self.assertEqual([item["id"] for item in app.list_archived_experiments(force=True)], [])

    def test_archive_experiment_rejects_submit_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            exp.mkdir()
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            (exp / ".mkexp2").mkdir()
            (exp / ".mkexp2" / "submit.lock").write_text("started_at=now\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")

            with self.assertRaisesRegex(ValueError, "submit is locked"):
                app.archive_experiment("exp")
            self.assertTrue((exp / "Experiment").is_file())
            self.assertFalse((repo / "exp.archived").exists())

    def test_archive_tagged_experiments_skips_pinned_and_locked(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for name in ["codex-free", "codex-pinned", "codex-locked", "manual"]:
                path = repo / name
                path.mkdir()
                (path / "Experiment").write_text(f"Experiment{name.replace('-', '')}() {{ :; }}\n")
            (repo / "codex-locked" / ".mkexp2").mkdir()
            (repo / "codex-locked" / ".mkexp2" / "submit.lock").write_text("started_at=now\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")
            for experiment_id in ["codex-free", "codex-pinned", "codex-locked"]:
                app.assign_experiment_tag(experiment_id, "Codex")
            app.write_pins(["codex-pinned"])

            result = app.archive_tagged_experiments("Codex")

            self.assertEqual(result["matching"], 3)
            self.assertEqual([item["id"] for item in result["archived"]], ["codex-free"])
            self.assertEqual([item["id"] for item in result["skipped_pinned"]], ["codex-pinned"])
            self.assertEqual([item["id"] for item in result["skipped_locked"]], ["codex-locked"])
            self.assertEqual(result["failed"], [])
            self.assertTrue((repo / "codex-free.archived" / "Experiment").is_file())
            self.assertTrue((repo / "codex-pinned" / "Experiment").is_file())
            self.assertTrue((repo / "codex-locked" / "Experiment").is_file())
            self.assertTrue((repo / "manual" / "Experiment").is_file())
            self.assertEqual(app.read_pins()["pinned"], ["codex-pinned"])
            self.assertEqual(app.tag_for_experiment("codex-free.archived")["name"], "Codex")
            self.assertEqual(
                [item["id"] for item in app.list_experiments(force=True)],
                ["codex-locked", "codex-pinned", "manual"],
            )

    def test_archive_and_unarchive_reject_collisions_and_bad_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for path in [repo / "exp", repo / "exp.archived"]:
                path.mkdir()
                (path / "Experiment").write_text("ExperimentX() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, ROOT / "bin" / "mkexp2", "x-<name>", "token")

            with self.assertRaises(ValueError):
                app.archive_experiment("exp")
            with self.assertRaises(ValueError):
                app.unarchive_experiment("exp.archived")
            with self.assertRaises(ValueError):
                app.archive_experiment("../escape")
            with self.assertRaises(ValueError):
                app.unarchive_experiment("../escape.archived")

    def test_progress_uses_json_argv_array_and_strips_ansi(self):
        calls = []
        original_run_command = mkexp2_web.run_command

        def fake_run_command(argv, cwd=None, timeout=60):
            self.assertIsInstance(argv, list)
            calls.append((list(argv), str(cwd) if cwd else None, timeout))
            return {
                "returncode": 0,
                "stdout": '\x1b[32m{"ok":true,"done":1,"total":2,"percent":50,"complete":false,"experiments":[]}\x1b[0m\n',
                "stderr": "",
                "elapsed_seconds": 0.1,
            }

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
        self.assertEqual(result["progress_json"]["done"], 1)
        self.assertFalse(result["progress_json"]["complete"])
        self.assertEqual(calls, [(["/fake/mkexp2", "progress", "--json"], str((repo / "exp").resolve()), 60)])

    def test_progress_uses_generated_metadata_when_available(self):
        original_run_command = mkexp2_web.run_command

        def fake_run_command(argv, cwd=None, timeout=60):
            raise AssertionError("metadata progress should not invoke mkexp2")

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            exp.mkdir()
            (exp / "Experiment").write_text("ExperimentMeta() { :; }\n")
            (exp / "jobs").mkdir()
            (exp / "logs" / "Fast" / "ExperimentMeta").mkdir(parents=True)
            existing = exp / "logs" / "Fast" / "ExperimentMeta" / "a.log"
            existing.write_text("done\n")
            missing = exp / "logs" / "Slow" / "ExperimentMeta" / "b.log"
            (exp / "jobs" / "ExperimentMeta__1x1x1.cmds.meta.tsv").write_text(
                f"0\tFast\tMock\tExperimentMeta\t1x1x1\t{existing}\n"
                f"1\tSlow\tMock\tExperimentMeta\t1x1x1\t{missing}\n"
            )
            app = mkexp2_web.Mkexp2WebApp(repo, "/fake/mkexp2", "x-<name>", "token")
            mkexp2_web.run_command = fake_run_command
            try:
                result = app.progress("exp")
            finally:
                mkexp2_web.run_command = original_run_command

        self.assertTrue(result["ok"])
        self.assertEqual(result["progress"]["argv"], ["metadata-progress"])
        self.assertEqual(result["progress_json"]["done"], 1)
        self.assertEqual(result["progress_json"]["total"], 2)
        self.assertEqual(result["progress_json"]["experiments"][0]["function"], "ExperimentMeta")
        self.assertEqual(result["progress_json"]["experiments"][0]["algorithms"][0]["name"], "Fast")
        self.assertEqual(result["progress_json"]["experiments"][0]["algorithms"][0]["done"], 1)
        self.assertEqual(result["progress_json"]["experiments"][0]["algorithms"][1]["name"], "Slow")
        self.assertEqual(result["progress_json"]["experiments"][0]["algorithms"][1]["done"], 0)

    def test_probe_payload_uses_json_argv_array_and_parses_payload(self):
        calls = []
        original_run_command = mkexp2_web.run_command

        def fake_run_command(argv, cwd=None, timeout=60):
            self.assertIsInstance(argv, list)
            calls.append((list(argv), str(cwd) if cwd else None, timeout))
            return {
                "returncode": 0,
                "stdout": '{"experiments":[{"name":"ExperimentX"}]}\n',
                "stderr": "",
                "elapsed_seconds": 0.1,
            }

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "exp").mkdir()
            (repo / "exp" / "Experiment").write_text("ExperimentX() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, "/fake/mkexp2", "x-<name>", "token")
            mkexp2_web.run_command = fake_run_command
            try:
                result = app.probe_payload("exp", {"selector": "ExperimentX", "flags": ["--algorithms"]})
                with self.assertRaisesRegex(ValueError, "unsupported probe flag"):
                    app.probe_payload("exp", {"flags": ["--bad"]})
            finally:
                mkexp2_web.run_command = original_run_command

        self.assertEqual(result["experiments"][0]["name"], "ExperimentX")
        self.assertIn("_command", result)
        self.assertEqual(calls, [(["/fake/mkexp2", "probe", "ExperimentX", "--algorithms"], str((repo / "exp").resolve()), 60)])

    def test_probe_payload_all_uses_single_bulk_argv(self):
        calls = []
        original_run_command = mkexp2_web.run_command

        def fake_run_command(argv, cwd=None, timeout=60):
            calls.append((list(argv), str(cwd) if cwd else None, timeout))
            return {
                "returncode": 0,
                "stdout": '{"experiments":[{"experiment":{"name":"A"},"resolved":{"algorithms":[{"name":"Fast"}]}}]}\n',
                "stderr": "",
                "elapsed_seconds": 0.1,
            }

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "exp").mkdir()
            (repo / "exp" / "Experiment").write_text("ExperimentA() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, "/fake/mkexp2", "x-<name>", "token")
            mkexp2_web.run_command = fake_run_command
            try:
                result = app.probe_payload("exp", {"flags": ["--all", "--algorithms"]})
            finally:
                mkexp2_web.run_command = original_run_command

        self.assertEqual(result["experiments"][0]["resolved"]["algorithms"][0]["name"], "Fast")
        self.assertEqual(calls, [(["/fake/mkexp2", "probe", "--all", "--algorithms"], str((repo / "exp").resolve()), 60)])

    def test_stats_uses_json_argv_array_and_parses_payload(self):
        calls = []
        original_run_command = mkexp2_web.run_command

        def fake_run_command(argv, cwd=None, timeout=60):
            self.assertIsInstance(argv, list)
            calls.append((list(argv), str(cwd) if cwd else None, timeout))
            return {
                "returncode": 0,
                "stdout": '{"ok":true,"algorithms":[{"algorithm":"A","rows":2,"failed":0,"avg_cut":10,"avg_time":1.5}]}\n',
                "stderr": "",
                "elapsed_seconds": 0.1,
            }

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "exp").mkdir()
            (repo / "exp" / "Experiment").write_text("ExperimentX() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, "/fake/mkexp2", "x-<name>", "token")
            mkexp2_web.run_command = fake_run_command
            try:
                result = app.stats("exp")
            finally:
                mkexp2_web.run_command = original_run_command

        self.assertTrue(result["ok"])
        self.assertEqual(result["stats_json"]["algorithms"][0]["algorithm"], "A")
        self.assertEqual(calls, [(["/fake/mkexp2", "stats", "--json"], str((repo / "exp").resolve()), 60)])

    def test_git_status_parser_groups_files(self):
        parsed = mkexp2_web.parse_git_status("?? new.txt\n M edited.txt\nD  gone.txt\nR  old.txt -> moved.txt\n")
        self.assertTrue(parsed["dirty"])
        self.assertEqual([item["path"] for item in parsed["groups"]["added"]], ["new.txt"])
        self.assertEqual([item["path"] for item in parsed["groups"]["modified"]], ["edited.txt", "moved.txt"])
        self.assertEqual([item["path"] for item in parsed["groups"]["deleted"]], ["gone.txt"])

    def test_git_commit_push_uses_experiment_repo_argv_arrays(self):
        calls = []
        original_run_command = mkexp2_web.run_command

        def fake_run_command(argv, cwd=None, timeout=60):
            self.assertIsInstance(argv, list)
            calls.append((list(argv), str(cwd) if cwd else None, timeout))
            if argv[:2] == ["git", "status"]:
                return {"returncode": 0, "stdout": " M exp/Experiment\n?? exp/results/out.csv\n", "stderr": ""}
            if argv[:3] == ["git", "rev-parse", "--abbrev-ref"]:
                return {"returncode": 0, "stdout": "main\n", "stderr": ""}
            if argv[:3] == ["git", "diff", "--cached"]:
                return {"returncode": 1, "stdout": "", "stderr": ""}
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            app = mkexp2_web.Mkexp2WebApp(repo, "/fake/mkexp2", "x-<name>", "token")
            mkexp2_web.run_command = fake_run_command
            try:
                result = app.git_commit_push("chore: save experiment")
            finally:
                mkexp2_web.run_command = original_run_command

        repo_cwd = str(repo.resolve())
        self.assertTrue(result["ok"])
        self.assertIn((["git", "add", "-A"], repo_cwd, 60), calls)
        self.assertIn((["git", "diff", "--cached", "--quiet"], repo_cwd, 60), calls)
        self.assertIn((["git", "commit", "-m", "chore: save experiment"], repo_cwd, 120), calls)
        self.assertIn((["git", "push"], repo_cwd, 180), calls)

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

    def test_submit_action_writes_per_experiment_selection_file(self):
        calls = []
        captured_selection = ""
        original_run_command = mkexp2_web.run_command

        def fake_run_command(argv, cwd=None, timeout=60):
            nonlocal captured_selection
            self.assertIsInstance(argv, list)
            calls.append((list(argv), str(cwd) if cwd else None))
            if argv[:2] == ["git", "diff"]:
                return {"returncode": 1, "stdout": "", "stderr": ""}
            if argv[:3] == ["zsh", "./submit.sh", "--install"]:
                self.assertIn("--selection-file", argv)
                selection_path = Path(argv[argv.index("--selection-file") + 1])
                captured_selection = selection_path.read_text(encoding="utf-8")
            return {"returncode": 0, "stdout": '{"experiments":[]}', "stderr": ""}

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "exp").mkdir()
            (repo / "exp" / "Experiment").write_text("ExperimentA() { :; }\n")
            app = mkexp2_web.Mkexp2WebApp(repo, "/fake/mkexp2", "x-<name>", "token")
            mkexp2_web.run_command = fake_run_command
            try:
                action = app.submit_action("exp", {
                    "selections": [
                        {"experiment": "ExperimentA", "algorithms": ["MockA"]},
                        {"experiment": "ExperimentB", "algorithm": "MockB"},
                    ],
                    "force": False,
                })
                for _ in range(100):
                    current = app.actions.get(action["id"])
                    if current["status"] != "running":
                        break
                    time.sleep(0.02)
                self.assertEqual(app.actions.get(action["id"])["status"], "completed")
            finally:
                mkexp2_web.run_command = original_run_command

        exp_cwd = str((repo / "exp").resolve())
        self.assertIn("ExperimentA\tMockA\n", captured_selection)
        self.assertIn("ExperimentB\tMockB\n", captured_selection)
        self.assertTrue(any(call[0][:4] == ["zsh", "./submit.sh", "--install", "--selection-file"] for call in calls))
        self.assertTrue(any(call[0][:3] == ["git", "commit", "-m"] and "ExperimentA:MockA" in call[0][3] for call in calls))
        self.assertTrue(all(call[1] in (exp_cwd, str(repo.resolve())) for call in calls))

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

    def test_plot_action_can_force_native_r_with_argv_array(self):
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
                action = app.plot_action("exp", {"no_docker": True, "flags": ["--running-time"], "algorithms": ["MockA"]})
                for _ in range(100):
                    current = app.actions.get(action["id"])
                    if current["status"] != "running":
                        break
                    time.sleep(0.02)
                result = app.actions.get(action["id"])["result"]
            finally:
                mkexp2_web.run_command = original_run_command

        self.assertTrue(result["plotted"])
        self.assertEqual(
            calls,
            [
                (
                    ["/fake/mkexp2", "plot", "--no-docker", "--running-time", "MockA"],
                    str((repo / "exp").resolve()),
                    mkexp2_web.PLOT_ACTION_TIMEOUT_SECONDS,
                )
            ],
        )

    def test_plot_sources_and_external_csv_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            (exp / "results").mkdir(parents=True)
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            (exp / "results" / "MockA.csv").write_text("Algorithm,Graph,K,Time,Cut\n")
            app = mkexp2_web.Mkexp2WebApp(repo, "/fake/mkexp2", "x-<name>", "token")

            sources = app.plot_sources("exp")
            self.assertEqual(sources["current"][0]["kind"], "algorithm")
            self.assertEqual(sources["current"][0]["name"], "MockA")

            resolved = app.resolve_plot_source(
                "exp",
                {"kind": "csv", "experiment_id": "exp", "file": "MockA.csv", "alias": "Other"},
            )
            self.assertTrue(resolved["token"].startswith("Other="))
            self.assertIn("/results/MockA.csv", resolved["token"])

            with self.assertRaises(ValueError):
                app.resolve_plot_source("exp", {"kind": "csv", "experiment_id": "exp", "file": "../MockA.csv"})

    def test_create_plot_artifacts_uses_catalog_and_argv_array(self):
        calls = []
        original_run_command = mkexp2_web.run_command

        catalog = {
            "plots": [
                {
                    "id": "running-time-box",
                    "name": "Running Time Box Plot",
                    "description": "desc",
                    "min_sources": 1,
                    "max_sources": None,
                    "default_selected": True,
                    "expensive": False,
                    "legacy_flags": ["--running-time"],
                }
            ]
        }

        def fake_run_command(argv, cwd=None, timeout=60):
            self.assertIsInstance(argv, list)
            calls.append((list(argv), str(cwd) if cwd else None, timeout))
            if argv[1:4] == ["plot", "--list", "--json"]:
                return {"returncode": 0, "stdout": json.dumps(catalog), "stderr": ""}
            if "plot" in argv:
                output = argv[argv.index("--output") + 1]
                (Path(cwd) / output).parent.mkdir(parents=True, exist_ok=True)
                (Path(cwd) / output).write_bytes(b"%PDF-1.4\n")
            return {"returncode": 0, "stdout": "", "stderr": ""}

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            (exp / "results").mkdir(parents=True)
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            (exp / "results" / "MockA.csv").write_text("Algorithm,Graph,K,Time,Cut\n")
            app = mkexp2_web.Mkexp2WebApp(repo, "/fake/mkexp2", "x-<name>", "token")
            mkexp2_web.run_command = fake_run_command
            try:
                action = app.create_plot_artifacts_action(
                    "exp",
                    {
                        "plots": ["running-time-box"],
                        "sources": [{"kind": "algorithm", "name": "MockA"}],
                        "label": "Test Plot",
                        "no_docker": True,
                    },
                )
                for _ in range(100):
                    current = app.actions.get(action["id"])
                    if current["status"] != "running":
                        break
                    time.sleep(0.02)
                result = app.actions.get(action["id"])["result"]
                index_exists = (repo / "exp" / "plots" / "index.json").is_file()
            finally:
                mkexp2_web.run_command = original_run_command

        self.assertTrue(result["plotted"])
        self.assertEqual(len(result["created"]), 1)
        self.assertTrue(index_exists)
        plot_calls = [call for call in calls if call[0][1:2] == ["plot"] and "--output" in call[0]]
        self.assertEqual(len(plot_calls), 1)
        self.assertEqual(plot_calls[0][0][0:4], ["/fake/mkexp2", "plot", "--no-docker", "--plot"])
        self.assertIn("running-time-box", plot_calls[0][0])
        self.assertIn("MockA", plot_calls[0][0])
        self.assertTrue(result["created"][0]["plot_set_id"])
        self.assertEqual(result["created"][0]["plot_set_label"], "Test Plot")

    def test_plot_artifact_sets_can_be_renamed_and_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            exp = repo / "exp"
            plots = exp / "plots"
            plots.mkdir(parents=True)
            (exp / "Experiment").write_text("ExperimentX() { :; }\n")
            (plots / "a.pdf").write_bytes(b"%PDF-1.4 a\n")
            (plots / "b.pdf").write_bytes(b"%PDF-1.4 b\n")
            (plots / "index.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "artifacts": [
                            {
                                "id": "a",
                                "label": "Old - A",
                                "plot_id": "speedup",
                                "plot_name": "Speedup",
                                "plot_set_id": "set-1",
                                "plot_set_label": "Old",
                                "path": "plots/a.pdf",
                                "created_at": "2026-05-22T10:00:00",
                                "sources": [],
                            },
                            {
                                "id": "b",
                                "label": "Old - B",
                                "plot_id": "running-time-box",
                                "plot_name": "Running Time Box",
                                "plot_set_id": "set-1",
                                "plot_set_label": "Old",
                                "path": "plots/b.pdf",
                                "created_at": "2026-05-22T10:00:01",
                                "sources": [],
                            },
                        ],
                    }
                )
            )
            app = mkexp2_web.Mkexp2WebApp(repo, "/fake/mkexp2", "x-<name>", "token")

            renamed = app.rename_plot_artifact_set("exp", "set-1", {"label": "New Name"})
            artifacts = renamed["artifacts"]["artifacts"]
            self.assertEqual({item["plot_set_label"] for item in artifacts}, {"New Name"})
            self.assertEqual({item["label"] for item in artifacts}, {"New Name - Speedup", "New Name - Running Time Box"})

            deleted_one = app.delete_plot_artifact("exp", "a")
            self.assertEqual(deleted_one["deleted"], ["a"])
            self.assertFalse((plots / "a.pdf").exists())
            self.assertTrue((plots / "b.pdf").exists())

            deleted_set = app.delete_plot_artifact_set("exp", "set-1")
            self.assertEqual(deleted_set["deleted"], ["b"])
            self.assertFalse((plots / "b.pdf").exists())
            self.assertEqual(deleted_set["artifacts"]["artifacts"], [])

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

        queue = mkexp2_web.parse_squeue_table(mkexp2_web.SQUEUE_FALLBACK)
        self.assertEqual(len(queue), 3)
        self.assertEqual(queue[0]["job_id"], "67633")
        self.assertEqual(queue[0]["state"], "PD")
        self.assertEqual(queue[0]["nodelist"], "(Dependency)")

        queue_with_time_limit = mkexp2_web.parse_squeue_table(
            """             JOBID PARTITION     NAME     USER STATE       TIME TIME_LIMIT  NODES NODELIST(REASON)
             70819       all submit-l seemaier PENDING       0:00  UNLIMITED      1 (Dependency)
       70818_[0-87%1]   hellman UecoRoll seemaier PENDING       0:00  UNLIMITED      1 (Resources)
             70816   hellman mkexp2-i seemaier RUNNING       3:11 365-00:00:00      1 hellman
"""
        )
        self.assertEqual(len(queue_with_time_limit), 3)
        self.assertEqual(queue_with_time_limit[0]["nodes"], "1")
        self.assertEqual(queue_with_time_limit[0]["nodelist"], "(Dependency)")
        self.assertEqual(queue_with_time_limit[0]["time_limit"], "UNLIMITED")
        self.assertEqual(queue_with_time_limit[1]["job_id"], "70818_[0-87%1]")
        self.assertEqual(queue_with_time_limit[2]["nodelist"], "hellman")

        queue_delimited = mkexp2_web.parse_squeue_table(
            "70818_[0-87%1]|hellman|UecoRoll|seemaier|PENDING|0:00|1|(Dependency)\n"
            "70816|hellman|mkexp2-i|seemaier|RUNNING|3:11|1|hellman\n"
        )
        self.assertEqual(len(queue_delimited), 2)
        self.assertEqual(queue_delimited[0]["job_id"], "70818_[0-87%1]")
        self.assertEqual(queue_delimited[0]["nodelist"], "(Dependency)")
        self.assertEqual(queue_delimited[1]["state"], "RUNNING")

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

    def test_slurm_status_does_not_create_unassigned_node_for_pending_jobs(self):
        calls = []
        original_run_command = mkexp2_web.run_command

        def fake_run_command(argv, cwd=None, timeout=60):
            calls.append(list(argv))
            if argv[:4] == ["sinfo", "-lN", "-p", "all"]:
                return {"returncode": 127, "stdout": "", "stderr": "sinfo not found"}
            if argv == ["squeue", "-h", "-o", mkexp2_web.SQUEUE_NODE_FORMAT]:
                return {
                    "returncode": 0,
                    "stdout": (
                        "70819||seemaier|submit-l|PENDING|Unknown|0:00\n"
                        "70818|(Resources)|seemaier|UecoRoll|PENDING|Unknown|0:00\n"
                        "70816|hellman|seemaier|mkexp2-i|RUNNING|2026-05-21T19:26:00|3:11\n"
                    ),
                    "stderr": "",
                }
            return {"returncode": 0, "stdout": "", "stderr": ""}

        mkexp2_web.run_command = fake_run_command
        try:
            status = mkexp2_web.SlurmStatus().get()
        finally:
            mkexp2_web.run_command = original_run_command

        names = [node["name"] for node in status["nodes"]]
        self.assertNotIn("unassigned", names)
        self.assertNotIn("(Resources)", names)
        hellman = next(node for node in status["nodes"] if node["name"] == "hellman")
        self.assertEqual(len(hellman["jobs"]), 1)
        self.assertEqual(hellman["jobs"][0]["job_id"], "70816")

    def test_squeue_status_falls_back_when_squeue_is_missing(self):
        calls = []
        original_run_command = mkexp2_web.run_command

        def fake_run_command(argv, cwd=None, timeout=60):
            calls.append(list(argv))
            if argv == ["squeue", "-h", "-o", mkexp2_web.SQUEUE_TABLE_FORMAT]:
                return {"returncode": 127, "stdout": "", "stderr": "squeue not found"}
            return {"returncode": 0, "stdout": "", "stderr": ""}

        mkexp2_web.run_command = fake_run_command
        try:
            queue = mkexp2_web.SlurmStatus().queue()
        finally:
            mkexp2_web.run_command = original_run_command

        self.assertIn(["squeue", "-h", "-o", mkexp2_web.SQUEUE_TABLE_FORMAT], calls)
        self.assertEqual(queue["source"], "fallback sample: squeue not installed")
        self.assertEqual(len(queue["rows"]), 3)
        self.assertEqual(queue["rows"][1]["partition"], "diffie")

    def test_squeue_cancel_requires_server_user_ownership(self):
        calls = []
        original_run_command = mkexp2_web.run_command
        original_getuser = mkexp2_web.getpass.getuser

        def fake_run_command(argv, cwd=None, timeout=60):
            calls.append((list(argv), timeout))
            if argv == ["squeue", "-h", "-o", mkexp2_web.SQUEUE_TABLE_FORMAT]:
                return {
                    "returncode": 0,
                    "stdout": (
                        "123|all|mine|owner|RUNNING|0:10|1|node01\n"
                        "124|all|other|alice|RUNNING|0:20|1|node02\n"
                        "125_[0-3%1]|all|array|owner|PENDING|0:00|1|(Resources)\n"
                    ),
                    "stderr": "",
                }
            if argv == ["scancel", "123"]:
                return {"returncode": 0, "stdout": "", "stderr": ""}
            if argv == ["scancel", "125_[0-3%1]"]:
                return {"returncode": 0, "stdout": "", "stderr": ""}
            if argv == ["scancel", "-u", "owner"]:
                return {"returncode": 0, "stdout": "", "stderr": ""}
            return {"returncode": 99, "stdout": "", "stderr": "unexpected"}

        mkexp2_web.run_command = fake_run_command
        mkexp2_web.getpass.getuser = lambda: "owner"
        try:
            queue = mkexp2_web.SlurmStatus().queue()
            result = mkexp2_web.SlurmStatus().cancel_job({"job_id": "123"})
            array_result = mkexp2_web.SlurmStatus().cancel_job({"job_id": "125_[0-3%1]"})
            all_result = mkexp2_web.SlurmStatus().cancel_user_jobs({"confirm_user": "owner"})
            with self.assertRaises(ValueError):
                mkexp2_web.SlurmStatus().cancel_job({"job_id": "124"})
            with self.assertRaisesRegex(ValueError, "confirmation"):
                mkexp2_web.SlurmStatus().cancel_user_jobs({"confirm_user": "alice"})
        finally:
            mkexp2_web.run_command = original_run_command
            mkexp2_web.getpass.getuser = original_getuser

        self.assertEqual(queue["server_user"], "owner")
        self.assertTrue(result["ok"])
        self.assertEqual(result["job"]["user"], "owner")
        self.assertTrue(array_result["ok"])
        self.assertTrue(all_result["ok"])
        self.assertEqual(all_result["server_user"], "owner")
        self.assertIn((["scancel", "123"], 30), calls)
        self.assertIn((["scancel", "125_[0-3%1]"], 30), calls)
        self.assertIn((["scancel", "-u", "owner"], 30), calls)

    def test_spack_plot_cache_action_uses_fixed_argv(self):
        calls = []
        original_run_command = mkexp2_web.run_command

        def fake_run_command(argv, cwd=None, timeout=60):
            calls.append((list(argv), str(cwd) if cwd else None, timeout))
            return {"returncode": 0, "stdout": "resolved", "stderr": ""}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            mkexp2 = root / "bin" / "mkexp2"
            cache = root / "plots" / ".cache-native"
            repo.mkdir()
            mkexp2.parent.mkdir()
            mkexp2.write_text("#!/usr/bin/env zsh\n")
            cache.mkdir(parents=True)
            (cache / "spack-r-libs.txt").write_text("/spack/a:/spack/b\n")

            app = mkexp2_web.Mkexp2WebApp(repo, mkexp2, "x-<name>", "token")
            mkexp2_web.run_command = fake_run_command
            try:
                action = app.resolve_spack_plot_cache_action(force=True)
                for _ in range(100):
                    current = app.actions.get(action["id"])
                    if current["status"] != "running":
                        break
                    time.sleep(0.02)
                result = app.actions.get(action["id"])["result"]
            finally:
                mkexp2_web.run_command = original_run_command

        self.assertTrue(result["resolved"])
        self.assertEqual(result["cache"]["entry_count"], 2)
        self.assertEqual(
            calls,
            [
                (
                    [str(mkexp2.resolve()), "plot", "--resolve-spack-r-libs", "--refresh-spack-r-libs"],
                    str(root.resolve()),
                    180,
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
