#!/usr/bin/env python3
import argparse
import datetime as _dt
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


MAX_TEXT_RESPONSE = 1024 * 1024
MAX_LOG_LIST_ENTRIES = 500
SLURM_CACHE_SECONDS = 15
EXPERIMENT_CACHE_SECONDS = 60
WEB_STATE_DIR = ".mkexp2"
WEB_PINS_FILE = "web-pins.json"
EXPERIMENT_SKIP_DIRS = {".git", ".mkexp2", "jobs", "logs", "results", "slurm"}
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
SINFO_LONG_FALLBACK = """Sat May 16 15:57:01 2026
NODELIST    NODES PARTITION       STATE CPUS    S:C:T MEMORY TMP_DISK WEIGHT AVAIL_FE REASON
backus          1      all*   allocated 128    1:64:2 101939        0      1   (null) none
blum            1      all*       idle~ 64     1:32:2 515390        0      1   (null) none
cook            1      all*   allocated 32     1:16:2  64228        0      1   (null) none
diffie          1      all*       idle~ 192    1:96:2 154732        0      1   (null) none
dijkstra        1      all*       idle~ 64     2:16:2 515857        0      1   (null) none
feigenbaum      1      all*       idle~ 16      1:8:2  64129        0      1      gpu none
floyd           1      all*       idle~ 128    2:32:2 309609        0      1   (null) none
hamming         1      all*       down~ 64      4:8:2 483595        0      1   (null) ResumeTimeout reache
hellman         1      all*       idle~ 192    1:96:2 154741        0      1   (null) none
hoare           1      all*       idle~ 160    4:20:2 741136        0      1   (null) none
iverson         1      all*       idle~ 80     80:1:1 256767        0      1   (null) none
karp            1      all*       idle~ 64     1:32:2 257803        0      1   (null) none
liskov          1      all*       idle~ 256    2:64:2 206405        0      1   (null) none
naur            1      all*   allocated 128    1:64:2 101943        0      1   (null) none
rabin           1      all*       idle~ 32      2:8:2  64363        0      1   (null) none
shamir          1      all*       idle~ 64      4:8:2 515851        0      1   (null) none
yao             1      all*       down~ 48     2:12:2 128794        0      1   (null) ResumeTimeout reache
"""


def slugify(value):
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-._")
    return value or "experiment"


def render_name_template(template, name, now=None):
    now = now or _dt.datetime.now()
    return now.strftime(template).replace("<name>", slugify(name))


def json_response(handler, status, payload):
    data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def text_response(handler, status, body, content_type="text/plain; charset=utf-8"):
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def read_json(handler):
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    body = handler.rfile.read(length)
    return json.loads(body.decode("utf-8"))


def strip_ansi(value):
    return ANSI_RE.sub("", str(value or ""))


def run_command(argv, cwd=None, timeout=60):
    started = time.time()
    try:
        proc = subprocess.run(
            list(argv),
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            shell=False,
        )
        return {
            "argv": list(argv),
            "cwd": str(cwd) if cwd else None,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-MAX_TEXT_RESPONSE:],
            "stderr": proc.stderr[-MAX_TEXT_RESPONSE:],
            "elapsed_seconds": round(time.time() - started, 3),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": list(argv),
            "cwd": str(cwd) if cwd else None,
            "returncode": 124,
            "stdout": (exc.stdout or "")[-MAX_TEXT_RESPONSE:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-MAX_TEXT_RESPONSE:] if isinstance(exc.stderr, str) else "",
            "elapsed_seconds": round(time.time() - started, 3),
            "timed_out": True,
        }
    except FileNotFoundError as exc:
        return {
            "argv": list(argv),
            "cwd": str(cwd) if cwd else None,
            "returncode": 127,
            "stdout": "",
            "stderr": str(exc),
            "elapsed_seconds": round(time.time() - started, 3),
            "timed_out": False,
        }


def parse_key_value_block(block):
    data = {}
    for token in re.findall(r'(\S+?=(?:"[^"]*"|\S+))', block):
        key, value = token.split("=", 1)
        data[key] = value.strip('"')
    return data


def parse_git_status(text):
    groups = {"added": [], "modified": [], "deleted": []}
    files = []
    for raw_line in str(text or "").splitlines():
        if len(raw_line) < 3:
            continue
        code = raw_line[:2]
        path = raw_line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]

        if "D" in code:
            category = "deleted"
        elif "A" in code or "?" in code:
            category = "added"
        else:
            category = "modified"

        item = {"path": path, "status": code, "category": category}
        groups[category].append(item)
        files.append(item)
    return {"files": files, "groups": groups, "dirty": bool(files)}


def parse_scontrol_nodes(text):
    nodes = {}
    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        if not block.strip():
            continue
        fields = parse_key_value_block(block.replace("\n", " "))
        name = fields.get("NodeName")
        if not name:
            continue
        nodes[name] = {
            "name": name,
            "partition": fields.get("Partitions", ""),
            "cpus": fields.get("CPUTot") or fields.get("CPUs", ""),
            "memory_mb": fields.get("RealMemory", ""),
            "gres": fields.get("Gres", ""),
            "features": fields.get("AvailableFeatures", ""),
            "state": fields.get("State", ""),
            "reason": fields.get("Reason", ""),
            "jobs": [],
        }
    return nodes


def parse_sinfo_nodes(text):
    nodes = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 8:
            continue
        name, partition, cpu_info, memory, gres, features, state, reason = parts[:8]
        nodes[name] = {
            "name": name,
            "partition": partition,
            "cpu_info": cpu_info,
            "memory_mb": memory,
            "gres": gres,
            "features": features,
            "state": state,
            "reason": reason,
            "jobs": [],
        }
    return nodes


def _clean_slurm_null(value):
    if value in ("(null)", "N/A", "null"):
        return ""
    return value


def _node_availability(state):
    normalized = state.rstrip("~*").lower()
    if normalized.startswith("idle"):
        return "idle"
    if "alloc" in normalized or normalized.startswith("mix"):
        return "used"
    if normalized.startswith("down") or normalized.startswith("drain"):
        return "down"
    return normalized or "unknown"


def parse_sinfo_long_nodes(text):
    nodes = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("NODELIST"):
            continue
        if re.match(r"^[A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d+", line):
            continue

        parts = line.split(maxsplit=10)
        if len(parts) < 11:
            continue

        nodelist, _nodes, partition, state, cpus, cpu_topology, memory, tmp_disk, weight, avail_fe, reason = parts
        names = expand_nodelist(nodelist) or [nodelist]
        features = _clean_slurm_null(avail_fe)
        for name in names:
            nodes[name] = {
                "name": name,
                "partition": partition.rstrip("*"),
                "partition_raw": partition,
                "state": state,
                "state_normalized": state.rstrip("~*").lower(),
                "availability": _node_availability(state),
                "cpus": cpus,
                "cpu_topology": cpu_topology,
                "sockets_cores_threads": cpu_topology,
                "memory_mb": memory,
                "tmp_disk": tmp_disk,
                "weight": weight,
                "available_features": features,
                "features": features,
                "reason": "" if reason == "none" else reason,
                "jobs": [],
            }
    return nodes


def expand_nodelist(value):
    if not value or value in ("(null)", "None"):
        return []
    out = []
    parts = []
    current = []
    depth = 0
    for char in value:
        if char == "[":
            depth += 1
        elif char == "]" and depth > 0:
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    for part in parts:
        match = re.match(r"^([^\[]+)\[([^\]]+)\]$", part)
        if not match:
            out.append(part)
            continue
        prefix, ranges = match.groups()
        for item in ranges.split(","):
            if "-" in item:
                start, end = item.split("-", 1)
                width = max(len(start), len(end))
                for number in range(int(start), int(end) + 1):
                    out.append(f"{prefix}{number:0{width}d}")
            else:
                out.append(prefix + item)
    return out


def parse_squeue_jobs(text):
    jobs = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 7:
            continue
        job_id, nodes, user, name, state, start_time, elapsed = parts[:7]
        jobs.append(
            {
                "job_id": job_id,
                "nodes": nodes,
                "node_names": expand_nodelist(nodes),
                "user": user,
                "job_name": name,
                "state": state,
                "start_time": start_time,
                "elapsed": elapsed,
            }
        )
    return jobs


class SlurmStatus:
    def __init__(self):
        self._cache_until = 0
        self._cache = None
        self._lock = threading.Lock()

    def get(self):
        with self._lock:
            now = time.time()
            if self._cache and now < self._cache_until:
                return self._cache
            payload = self._collect()
            self._cache = payload
            self._cache_until = now + SLURM_CACHE_SECONDS
            return payload

    def _collect(self):
        commands = {}
        nodes = {}
        source = "sinfo -lN -p all"

        sinfo = run_command(["sinfo", "-lN", "-p", "all"], timeout=8)
        commands["sinfo_long"] = sinfo
        if sinfo["returncode"] == 0:
            nodes.update(parse_sinfo_long_nodes(sinfo["stdout"]))
        elif sinfo["returncode"] == 127:
            source = "fallback sample: sinfo not installed"
            nodes.update(parse_sinfo_long_nodes(SINFO_LONG_FALLBACK))

        squeue = run_command(
            ["squeue", "-h", "-o", "%i|%N|%u|%j|%T|%S|%M"],
            timeout=8,
        )
        commands["squeue"] = squeue
        jobs = parse_squeue_jobs(squeue["stdout"]) if squeue["returncode"] == 0 else []

        for job in jobs:
            attached = False
            for node_name in job["node_names"]:
                if node_name in nodes:
                    nodes[node_name].setdefault("jobs", []).append(job)
                    attached = True
            if not attached:
                name = job["nodes"] or "unassigned"
                nodes.setdefault(
                    name,
                    {
                        "name": name,
                        "partition": "",
                        "cpus": "",
                        "memory_mb": "",
                        "gres": "",
                        "features": "",
                        "state": job["state"],
                        "reason": "",
                        "jobs": [],
                    },
                )["jobs"].append(job)

        node_list = sorted(nodes.values(), key=lambda item: item["name"])
        return {
            "ok": bool(nodes) or any(result["returncode"] == 0 for result in commands.values()),
            "cached_seconds": SLURM_CACHE_SECONDS,
            "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "nodes": node_list,
            "commands": commands,
        }


class ActionStore:
    def __init__(self):
        self._actions = {}
        self._lock = threading.Lock()

    def start(self, label, target):
        action_id = secrets.token_urlsafe(10)
        payload = {
            "id": action_id,
            "label": label,
            "status": "running",
            "started_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "result": None,
            "error": None,
        }
        with self._lock:
            self._actions[action_id] = payload

        def runner():
            try:
                result = target()
                with self._lock:
                    payload["status"] = "completed"
                    payload["result"] = result
                    payload["finished_at"] = _dt.datetime.now().isoformat(timespec="seconds")
            except Exception as exc:
                with self._lock:
                    payload["status"] = "failed"
                    payload["error"] = str(exc)
                    payload["finished_at"] = _dt.datetime.now().isoformat(timespec="seconds")

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        return payload

    def get(self, action_id):
        with self._lock:
            return self._actions.get(action_id)


class Mkexp2WebApp:
    def __init__(self, repo, mkexp2, name_template, token, allow_empty_token=False):
        self.repo = Path(repo).resolve()
        self.mkexp2 = Path(mkexp2).resolve()
        self.name_template = name_template
        self.token = token
        self.allow_empty_token = allow_empty_token
        self.actions = ActionStore()
        self.slurm = SlurmStatus()
        self._plot_backend_cache = None
        self._plot_backend_cache_at = 0.0
        self._experiments_cache = None
        self._experiments_cache_at = 0.0

    def experiment_path(self, experiment_id):
        parts = str(experiment_id or "").split("/")
        if (
            not parts
            or any(part in ("", ".", "..") for part in parts)
            or not all(re.match(r"^[A-Za-z0-9._-]+$", part) for part in parts)
        ):
            raise ValueError("invalid experiment id")
        path = (self.repo / Path(*parts)).resolve()
        if path != self.repo and self.repo not in path.parents:
            raise ValueError("experiment path escapes repo")
        return path

    def _git_experiment_files(self):
        tracked = run_command(
            ["git", "ls-files", "-z", "--", "Experiment", "*/Experiment"],
            cwd=self.repo,
            timeout=30,
        )
        if tracked["returncode"] != 0:
            return None
        untracked = run_command(
            ["git", "ls-files", "-z", "--others", "--exclude-standard", "--", "Experiment", "*/Experiment"],
            cwd=self.repo,
            timeout=30,
        )
        if untracked["returncode"] != 0:
            return None
        files = set()
        for output in (tracked["stdout"], untracked["stdout"]):
            for item in output.split("\0"):
                item = item.strip("/")
                if item == "Experiment" or item.endswith("/Experiment"):
                    files.add(item)
        return sorted(files)

    def _walk_experiment_files(self):
        files = []
        experiments = []
        for root, dirnames, filenames in os.walk(self.repo):
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in EXPERIMENT_SKIP_DIRS and not name.startswith(".")
            )
            if "Experiment" not in filenames:
                continue
            files.append((Path(root) / "Experiment").resolve().relative_to(self.repo).as_posix())
        return files

    def _discover_experiments(self):
        experiments = []
        experiment_files = self._git_experiment_files()
        if experiment_files is None:
            experiment_files = self._walk_experiment_files()
        for rel_file in experiment_files:
            rel_dir = str(Path(rel_file).parent)
            if rel_dir in ("", "."):
                continue
            parts = rel_dir.split("/")
            if any(part in EXPERIMENT_SKIP_DIRS or part.startswith(".") for part in parts):
                continue
            self.experiment_path(rel_dir)
            exp_file = self.repo / rel_file
            path = exp_file.parent.resolve()
            stat = exp_file.stat()
            experiments.append(
                {
                    "id": rel_dir,
                    "name": parts[-1],
                    "parent": "/".join(parts[:-1]),
                    "depth": len(parts),
                    "path": str(path),
                    "modified_at": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    "has_results": (path / "results").is_dir(),
                    "has_plots_pdf": (path / "plots.pdf").is_file(),
                }
            )
        experiments.sort(key=lambda item: item["id"])
        return experiments

    def list_experiments(self, force=False):
        now = time.time()
        if (
            not force
            and self._experiments_cache is not None
            and now - self._experiments_cache_at < EXPERIMENT_CACHE_SECONDS
        ):
            return self._experiments_cache
        experiments = self._discover_experiments()
        self._experiments_cache = experiments
        self._experiments_cache_at = now
        return experiments

    def invalidate_experiments_cache(self):
        self._experiments_cache = None
        self._experiments_cache_at = 0.0

    def pins_path(self):
        return self.repo / WEB_STATE_DIR / WEB_PINS_FILE

    def read_pins(self):
        path = self.pins_path()
        if not path.is_file():
            return {"pinned": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid pins JSON: {exc}") from exc
        pinned = payload.get("pinned") or []
        if not isinstance(pinned, list):
            raise ValueError("invalid pins JSON: pinned is not an array")
        filtered = []
        seen = set()
        for experiment_id in pinned:
            experiment_id = str(experiment_id)
            if experiment_id not in seen:
                filtered.append(experiment_id)
                seen.add(experiment_id)
        return {"pinned": filtered, "path": str(path)}

    def write_pins(self, pinned):
        if not isinstance(pinned, list):
            raise ValueError("pinned must be an array")
        valid = {experiment["id"] for experiment in self.list_experiments()}
        filtered = []
        seen = set()
        for experiment_id in pinned:
            experiment_id = str(experiment_id)
            if experiment_id in valid and experiment_id not in seen:
                filtered.append(experiment_id)
                seen.add(experiment_id)
        path = self.pins_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps({"pinned": filtered}, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return {"pinned": filtered, "path": str(path), "saved": True}

    def list_presets(self):
        result = run_command([str(self.mkexp2), "probe", "--presets"], cwd=self.repo, timeout=30)
        if result["returncode"] != 0:
            message = result["stderr"].strip() or result["stdout"].strip() or "mkexp2 probe --presets failed"
            raise ValueError(message)
        try:
            payload = json.loads(result["stdout"] or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid preset JSON: {exc}") from exc
        presets = payload.get("presets") or []
        if not isinstance(presets, list):
            raise ValueError("invalid preset JSON: presets is not an array")
        return presets

    def create_experiment(self, payload):
        name = payload.get("name") or "experiment"
        template = payload.get("name_template") or self.name_template
        experiment_id = render_name_template(template, name)
        path = self.experiment_path(experiment_id)
        if path.exists():
            raise ValueError(f"experiment already exists: {experiment_id}")
        preset = str(payload.get("preset") or "").strip()
        path.mkdir(parents=True)
        try:
            if preset:
                init = run_command([str(self.mkexp2), "init", preset], cwd=path, timeout=30)
                if init["returncode"] != 0:
                    message = init["stderr"].strip() or init["stdout"].strip() or f"failed to initialize preset {preset}"
                    raise ValueError(message)
                self.invalidate_experiments_cache()
                return {"id": experiment_id, "path": str(path), "preset": preset, "init": init}

            raw = payload.get("experiment")
            if not raw:
                raw = experiment_from_form(name, payload.get("form") or {})
            (path / "Experiment").write_text(raw, encoding="utf-8")
            self.invalidate_experiments_cache()
            return {"id": experiment_id, "path": str(path)}
        except Exception:
            shutil.rmtree(path, ignore_errors=True)
            raise

    def command(self, experiment_id, argv, timeout=60):
        return run_command([str(self.mkexp2), *argv], cwd=self.experiment_path(experiment_id), timeout=timeout)

    def plot_backend_status(self):
        now = time.time()
        if self._plot_backend_cache and now - self._plot_backend_cache_at < 15:
            return self._plot_backend_cache

        docker_command = shutil.which("docker")
        compose_command = shutil.which("docker-compose")
        docker_info = None
        docker_compose = None
        docker_available = False
        if docker_command:
            docker_info = run_command(["docker", "info"], timeout=5)
            if docker_info["returncode"] == 0:
                docker_compose = run_command(["docker", "compose", "version"], timeout=5)
                if docker_compose["returncode"] != 0 and compose_command:
                    docker_compose = run_command(["docker-compose", "version"], timeout=5)
                docker_available = docker_compose is not None and docker_compose["returncode"] == 0

        payload = {
            "docker_available": docker_available,
            "native_r_available": shutil.which("Rscript") is not None,
            "default_no_docker": not docker_available,
            "docker_command": docker_command or "",
            "docker_info": docker_info,
            "docker_compose": docker_compose,
        }
        self._plot_backend_cache = payload
        self._plot_backend_cache_at = now
        return payload

    def submit_lock_path(self, experiment_id):
        return self.experiment_path(experiment_id) / ".mkexp2" / "submit.lock"

    def submit_lock(self, experiment_id):
        path = self.submit_lock_path(experiment_id)
        if not path.is_file():
            return {"locked": False, "path": str(path), "content": "", "fields": {}}
        content = path.read_text(encoding="utf-8", errors="replace")
        fields = {}
        for line in content.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                fields[key] = value
        stat = path.stat()
        return {
            "locked": True,
            "path": str(path),
            "content": content,
            "fields": fields,
            "modified_at": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        }

    def clear_submit_lock(self, experiment_id):
        path = self.submit_lock_path(experiment_id)
        existed = path.exists()
        path.unlink(missing_ok=True)
        return {"cleared": existed, "submit_lock": self.submit_lock(experiment_id)}

    def progress(self, experiment_id):
        result = self.command(experiment_id, ["progress", "--json"], timeout=60)
        result["stdout"] = strip_ansi(result.get("stdout", ""))
        result["stderr"] = strip_ansi(result.get("stderr", ""))
        progress_json = None
        if result["returncode"] == 0 and result["stdout"].strip():
            try:
                progress_json = json.loads(result["stdout"])
            except json.JSONDecodeError:
                progress_json = None
        return {
            "ok": result["returncode"] == 0,
            "progress": result,
            "progress_json": progress_json,
            "submit_lock": self.submit_lock(experiment_id),
        }

    def stats(self, experiment_id):
        result = self.command(experiment_id, ["stats", "--json"], timeout=60)
        result["stdout"] = strip_ansi(result.get("stdout", ""))
        result["stderr"] = strip_ansi(result.get("stderr", ""))
        stats_json = None
        if result["returncode"] == 0 and result["stdout"].strip():
            try:
                stats_json = json.loads(result["stdout"])
            except json.JSONDecodeError:
                stats_json = None
        return {
            "ok": result["returncode"] == 0,
            "stats": result,
            "stats_json": stats_json,
        }

    def submit_action(self, experiment_id, payload):
        algorithms = payload.get("algorithms") or []
        force = bool(payload.get("force"))
        if not isinstance(algorithms, list) or not all(isinstance(item, str) for item in algorithms):
            raise ValueError("algorithms must be an array of strings")

        def action():
            check = self.command(experiment_id, ["check", "--json"], timeout=60)
            probe = self.command(experiment_id, ["probe"], timeout=60)
            if check["returncode"] != 0 and not force:
                return {
                    "submitted": False,
                    "blocked": "check failed",
                    "check": check,
                    "probe": probe,
                }

            generate = self.command(experiment_id, ["generate"], timeout=120)
            if generate["returncode"] != 0:
                return {
                    "submitted": False,
                    "blocked": "generate failed",
                    "check": check,
                    "probe": probe,
                    "generate": generate,
                }

            submit = run_command(
                ["zsh", "./submit.sh", "--install", *algorithms],
                cwd=self.experiment_path(experiment_id),
                timeout=120,
            )
            if submit["returncode"] == 0:
                commit = self.git_commit_submission(experiment_id, algorithms, force)
            else:
                commit = {"committed": False, "message": "submit failed; no commit created"}
            return {
                "submitted": submit["returncode"] == 0,
                "algorithms": algorithms,
                "force": force,
                "check": check,
                "probe": probe,
                "generate": generate,
                "submit": submit,
                "git": commit,
                "submit_lock": self.submit_lock(experiment_id),
            }

        return self.actions.start(f"submit {experiment_id}", action)

    def parse_action(self, experiment_id):
        def action():
            parse = self.command(experiment_id, ["parse"], timeout=600)
            return {"parsed": parse["returncode"] == 0, "parse": parse}

        return self.actions.start(f"parse {experiment_id}", action)

    def plot_action(self, experiment_id, payload):
        algorithms = payload.get("algorithms") or []
        flags = payload.get("flags") or []
        allowed_flags = {"--performance-profile", "--speedup", "--running-time"}
        argv = ["plot"]
        if payload.get("no_docker"):
            argv.append("--no-docker")
        for flag in flags:
            if flag not in allowed_flags:
                raise ValueError(f"unsupported plot flag: {flag}")
            argv.append(flag)
        threads = payload.get("threads")
        if threads:
            argv.extend(["--threads", str(threads)])
        argv.extend(str(item) for item in algorithms)

        def action():
            plot = self.command(experiment_id, argv, timeout=600)
            return {"plotted": plot["returncode"] == 0, "plot": plot}

        return self.actions.start(f"plot {experiment_id}", action)

    def git_commit_submission(self, experiment_id, algorithms, force):
        rel = experiment_id
        add = run_command(["git", "add", "-A", "--", rel], cwd=self.repo, timeout=60)
        diff = run_command(["git", "diff", "--cached", "--quiet", "--", rel], cwd=self.repo, timeout=60)
        if diff["returncode"] == 0:
            return {"committed": False, "add": add, "message": "nothing to commit"}
        algo_text = ", ".join(algorithms) if algorithms else "all algorithms"
        suffix = " (manual override)" if force else ""
        message = f"chore: submit {experiment_id} ({algo_text}){suffix}"
        commit = run_command(["git", "commit", "-m", message, "--", rel], cwd=self.repo, timeout=60)
        return {"committed": commit["returncode"] == 0, "add": add, "commit": commit, "message": message}

    def git_status(self):
        status = run_command(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=self.repo, timeout=30)
        branch = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.repo, timeout=30)
        parsed = parse_git_status(status["stdout"] if status["returncode"] == 0 else "")
        return {
            "ok": status["returncode"] == 0,
            "repo": str(self.repo),
            "branch": branch["stdout"].strip() if branch["returncode"] == 0 else "",
            "status": status,
            "branch_command": branch,
            **parsed,
        }

    def git_commit_push(self, message):
        message = str(message or "").strip()
        if not message:
            raise ValueError("commit message is required")

        before = self.git_status()
        add = run_command(["git", "add", "-A"], cwd=self.repo, timeout=60)
        if add["returncode"] != 0:
            return {"ok": False, "repo": str(self.repo), "before": before, "add": add, "message": message}

        diff = run_command(["git", "diff", "--cached", "--quiet"], cwd=self.repo, timeout=60)
        commit = {"committed": False, "message": "nothing to commit"}
        if diff["returncode"] != 0:
            commit = run_command(["git", "commit", "-m", message], cwd=self.repo, timeout=120)
            if commit["returncode"] != 0:
                return {
                    "ok": False,
                    "repo": str(self.repo),
                    "before": before,
                    "add": add,
                    "diff": diff,
                    "commit": commit,
                    "message": message,
                }

        push = run_command(["git", "push"], cwd=self.repo, timeout=180)
        after = self.git_status()
        return {
            "ok": push["returncode"] == 0,
            "repo": str(self.repo),
            "before": before,
            "after": after,
            "add": add,
            "diff": diff,
            "commit": commit,
            "push": push,
            "message": message,
        }

    def results(self, experiment_id):
        path = self.experiment_path(experiment_id)
        results_dir = path / "results"
        files = []
        if results_dir.is_dir():
            for csv_file in sorted(results_dir.glob("*.csv")):
                stat = csv_file.stat()
                content = csv_file.read_text(encoding="utf-8", errors="replace")
                files.append(
                    {
                        "name": csv_file.name,
                        "size": stat.st_size,
                        "modified_at": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                        "content": content[:MAX_TEXT_RESPONSE],
                        "truncated": len(content) > MAX_TEXT_RESPONSE,
                    }
                )
        return {"files": files}

    def install_log(self, experiment_id):
        path = self.experiment_path(experiment_id)
        log_file = path / "logs" / "install.md"
        if not log_file.is_file():
            return {"exists": False, "path": str(log_file), "content": ""}
        stat = log_file.stat()
        content = log_file.read_text(encoding="utf-8", errors="replace")
        return {
            "exists": True,
            "path": str(log_file),
            "size": stat.st_size,
            "modified_at": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "content": content[:MAX_TEXT_RESPONSE],
            "truncated": len(content) > MAX_TEXT_RESPONSE,
        }

    def plots_info(self, experiment_id):
        path = self.experiment_path(experiment_id)
        pdf = path / "plots.pdf"
        if not pdf.is_file():
            return {"exists": False, "path": str(pdf)}
        stat = pdf.stat()
        return {
            "exists": True,
            "path": str(pdf),
            "size": stat.st_size,
            "modified_at": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        }

    def logs_root(self, experiment_id):
        return self.experiment_path(experiment_id) / "logs"

    def log_path(self, experiment_id, rel_path):
        logs_root = self.logs_root(experiment_id).resolve()
        rel_text = str(rel_path or "").strip("/")
        if not rel_text:
            return logs_root
        parts = rel_text.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("invalid log path")
        path = (logs_root / Path(*parts)).resolve()
        if path != logs_root and logs_root not in path.parents:
            raise ValueError("log path escapes logs directory")
        return path

    def list_logs(self, experiment_id, rel_dir="", limit=MAX_LOG_LIST_ENTRIES, offset=0):
        logs_root = self.logs_root(experiment_id)
        directory = self.log_path(experiment_id, rel_dir)
        if not logs_root.is_dir():
            return {
                "exists": False,
                "dir": str(rel_dir or ""),
                "path": str(logs_root),
                "entries": [],
                "total": 0,
                "offset": 0,
                "limit": limit,
                "has_more": False,
            }
        if not directory.is_dir():
            raise ValueError("log path is not a directory")

        def make_entry(child):
            stat = child.stat()
            rel = child.resolve().relative_to(logs_root.resolve()).as_posix()
            return {
                "name": rel if not str(rel_dir or "") else child.name,
                "path": rel,
                "type": "dir" if child.is_dir() else "file",
                "size": stat.st_size if child.is_file() else None,
                "modified_at": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            }

        entries = []
        if not str(rel_dir or ""):
            for child in directory.iterdir():
                if child.name.startswith(".") or child.name == "install.md":
                    continue
                if not child.is_dir():
                    entries.append(make_entry(child))
                    continue
                visible_children = sorted(
                    (grandchild for grandchild in child.iterdir() if not grandchild.name.startswith(".")),
                    key=lambda item: (not item.is_dir(), item.name),
                )
                if not visible_children:
                    entries.append(make_entry(child))
                    continue
                entries.extend(make_entry(grandchild) for grandchild in visible_children)
        else:
            for child in directory.iterdir():
                if child.name.startswith("."):
                    continue
                entries.append(make_entry(child))
        entries.sort(key=lambda item: (item["type"] != "dir", item["name"]))
        total = len(entries)
        offset = max(0, int(offset or 0))
        limit = max(1, min(MAX_LOG_LIST_ENTRIES, int(limit or MAX_LOG_LIST_ENTRIES)))
        return {
            "exists": True,
            "dir": str(rel_dir or ""),
            "path": str(directory),
            "entries": entries[offset : offset + limit],
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < total,
        }

    def log_file(self, experiment_id, rel_path):
        path = self.log_path(experiment_id, rel_path)
        if not path.is_file():
            raise ValueError("log path is not a file")
        stat = path.stat()
        content = path.read_text(encoding="utf-8", errors="replace")
        return {
            "exists": True,
            "path": str(path),
            "relative_path": str(rel_path or ""),
            "size": stat.st_size,
            "modified_at": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "content": content[:MAX_TEXT_RESPONSE],
            "truncated": len(content) > MAX_TEXT_RESPONSE,
        }


def experiment_from_form(name, form):
    function = "Experiment" + re.sub(r"[^A-Za-z0-9]+", "", name.title())
    if function == "Experiment":
        function = "ExperimentWeb"
    system = form.get("system") or "slurm"
    algorithms = form.get("algorithms") or ["Mock"]
    graphs = form.get("graphs") or ["graphs"]
    ks = form.get("ks") or ["2"]
    seeds = form.get("seeds") or ["1"]
    epsilons = form.get("epsilons") or ["0.03"]
    threads = form.get("threads") or ["1x1x1"]
    properties = form.get("properties") or []

    lines = [f"System {system}"]
    for prop in properties:
        key = str(prop.get("key", "")).strip()
        value = str(prop.get("value", "")).strip()
        if key:
            lines.append(f"Property {key} {value}")
    lines.extend(["", f"{function}() {{"])
    lines.append("  Algorithms " + " ".join(map(str, algorithms)))
    lines.append("  Graphs " + " ".join(map(str, graphs)))
    lines.append("  Ks " + " ".join(map(str, ks)))
    lines.append("  Seeds " + " ".join(map(str, seeds)))
    lines.append("  Epsilons " + " ".join(map(str, epsilons)))
    lines.append("  Threads " + " ".join(map(str, threads)))
    lines.append("}")
    return "\n".join(lines) + "\n"


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>mkexp2</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7f8;
      --panel: #ffffff;
      --panel-2: #edf2f4;
      --text: #182024;
      --muted: #68767f;
      --border: #d8e0e5;
      --accent: #0f766e;
      --danger: #b42318;
      --ok: #127443;
      --shadow: 0 8px 24px rgba(16, 24, 40, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.4;
      color: var(--text);
      background: var(--bg);
    }
    button, input, textarea, select {
      font: inherit;
    }
    button {
      border: 1px solid var(--border);
      background: var(--panel);
      color: var(--text);
      height: 34px;
      padding: 0 12px;
      border-radius: 6px;
      cursor: pointer;
    }
    button.primary {
      background: var(--accent);
      color: white;
      border-color: var(--accent);
    }
    button.danger {
      color: var(--danger);
      border-color: #f1b7b1;
    }
    .icon-button {
      width: 34px;
      padding: 0;
      display: inline-grid;
      place-items: center;
    }
    .icon-button svg {
      width: 16px;
      height: 16px;
      stroke: currentColor;
      stroke-width: 2;
      stroke-linecap: round;
      stroke-linejoin: round;
      fill: none;
    }
    input, textarea, select {
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 9px 10px;
      background: white;
      color: var(--text);
    }
    input[type="checkbox"] {
      width: 16px;
      height: 16px;
      flex: 0 0 auto;
      margin: 0;
      padding: 0;
    }
    textarea {
      min-height: 420px;
      resize: vertical;
      font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    table {
      width: 100%;
      border-collapse: collapse;
    }
    th, td {
      padding: 8px 10px;
      border-bottom: 1px solid var(--border);
      vertical-align: top;
      text-align: left;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      text-transform: uppercase;
    }
    .app {
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      min-height: 100vh;
    }
    .sidebar {
      border-right: 1px solid var(--border);
      background: #ffffff;
      padding: 18px;
    }
    .brand {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      margin-bottom: 16px;
    }
    .brand-actions {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      flex: 0 0 auto;
    }
    .brand h1 {
      margin: 0;
      font-size: 18px;
      line-height: 1.1;
      letter-spacing: 0;
    }
    .experiment-list {
      display: grid;
      gap: 6px;
      margin-top: 12px;
    }
    .pinned-experiments {
      display: grid;
      gap: 6px;
      margin-bottom: 10px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--border);
    }
    .pinned-title {
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
      letter-spacing: 0;
      text-transform: uppercase;
    }
    .experiment-folder {
      min-width: 0;
    }
    .folder-summary {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      min-height: 38px;
      padding: 8px 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #f8fafb;
      cursor: pointer;
      list-style: none;
    }
    .folder-summary::-webkit-details-marker {
      display: none;
    }
    .folder-name {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 650;
    }
    .folder-name::before {
      content: "> ";
      color: var(--muted);
      font-weight: 750;
    }
    .experiment-folder[open] > .folder-summary .folder-name::before {
      content: "v ";
    }
    .folder-count {
      color: var(--muted);
      white-space: nowrap;
      font-size: 12px;
      font-variant-numeric: tabular-nums;
    }
    .folder-children {
      display: grid;
      gap: 6px;
      margin-top: 6px;
      padding-left: 12px;
      border-left: 1px solid var(--border);
    }
    .experiment-item {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 34px;
      gap: 6px;
      align-items: stretch;
      min-width: 0;
    }
    .experiment-row {
      width: 100%;
      text-align: left;
      height: auto;
      min-height: 38px;
      padding: 8px 10px;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .experiment-row.active {
      border-color: var(--accent);
      background: #e8f5f3;
    }
    .pin-button {
      min-height: 38px;
      height: auto;
      padding: 0;
      color: var(--muted);
      font-size: 15px;
      line-height: 1;
    }
    .pin-button.active {
      color: #a16207;
      border-color: #facc15;
      background: #fef9c3;
    }
    .sidebar-nodes {
      margin-top: 18px;
      padding-top: 14px;
      border-top: 1px solid var(--border);
    }
    .sidebar-section-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
    }
    .sidebar-section-title {
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
      letter-spacing: 0;
      text-transform: uppercase;
    }
    .small-button {
      height: 28px;
      padding: 0 8px;
      font-size: 12px;
    }
    .node-list {
      display: grid;
      gap: 6px;
    }
    .node-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      min-height: 32px;
      min-width: 0;
      padding: 7px 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #f8fafb;
      color: var(--text);
      font-size: 12px;
      line-height: 1.2;
    }
    .node-name {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 750;
    }
    .node-spec {
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }
    .node-state-allocated {
      background: #f9d4d4;
      border-color: #ef9a9a;
      color: #7f1d1d;
    }
    .node-state-idle-reserved {
      background: #14532d;
      border-color: #14532d;
      color: #ecfdf5;
    }
    .node-state-idle {
      background: #dcfce7;
      border-color: #86efac;
      color: #14532d;
    }
    .node-state-down {
      background: #050505;
      border-color: #050505;
      color: #ffffff;
    }
    .main {
      padding: 18px;
      display: grid;
      gap: 14px;
      align-content: start;
    }
    .toolbar, .panel-header, .actions {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
      min-width: 0;
    }
    .panel-header {
      padding: 12px 14px;
      border-bottom: 1px solid var(--border);
      background: #fbfcfd;
    }
    .panel-title {
      font-weight: 700;
    }
    .panel-body {
      padding: 14px;
      min-width: 0;
    }
    .editor-shell {
      display: grid;
      min-height: 420px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: white;
      overflow: hidden;
    }
    .editor-shell:focus-within {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.12);
    }
    .editor-shell textarea,
    .editor-highlight {
      grid-area: 1 / 1;
      width: 100%;
      min-height: 420px;
      margin: 0;
      padding: 9px 10px;
      border: 0;
      border-radius: 0;
      font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      letter-spacing: 0;
      tab-size: 2;
      white-space: pre;
      overflow: auto;
    }
    .editor-shell textarea {
      position: relative;
      z-index: 1;
      background: transparent;
      color: transparent;
      -webkit-text-fill-color: transparent;
      caret-color: var(--text);
      outline: none;
    }
    .editor-shell textarea::selection {
      background: rgba(15, 118, 110, 0.22);
      color: transparent;
      -webkit-text-fill-color: transparent;
    }
    .editor-highlight {
      pointer-events: none;
      color: #25313a;
    }
    .tok-comment { color: #7a8790; font-style: italic; }
    .tok-keyword { color: #0f766e; font-weight: 750; }
    .tok-shell { color: #7c3aed; font-weight: 650; }
    .tok-function { color: #b45309; font-weight: 750; }
    .tok-string { color: #0f5f86; }
    .tok-variable { color: #8a4b00; }
    .tok-number { color: #9f1239; }
    .grid {
      display: grid;
      grid-template-columns: minmax(520px, 1.1fr) minmax(360px, 0.9fr);
      gap: 14px;
      align-items: start;
      min-width: 0;
    }
    .view-tabs {
      display: flex;
      gap: 6px;
      align-items: center;
      flex-wrap: wrap;
    }
    .view-tab {
      height: 34px;
      min-width: 92px;
      background: transparent;
    }
    .view-tab.icon-tab {
      min-width: 38px;
      width: 38px;
      padding: 0;
      font-weight: 800;
    }
    .view-tab.active {
      background: var(--text);
      border-color: var(--text);
      color: white;
    }
    .view-panel {
      display: none;
      min-width: 0;
    }
    .view-panel.active {
      display: block;
    }
    .grid > *, .stack {
      min-width: 0;
    }
    .stack { display: grid; gap: 12px; }
    .muted { color: var(--muted); }
    .output {
      white-space: pre-wrap;
      word-break: break-word;
      background: #101820;
      color: #e7eef2;
      border-radius: 6px;
      padding: 10px;
      max-height: 260px;
      overflow: auto;
      overflow-wrap: anywhere;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .output:empty {
      display: none;
    }
    .rendered-output {
      white-space: normal;
      word-break: normal;
      background: white;
      color: var(--text);
      border: 1px solid var(--border);
      max-height: 420px;
      font: 13px/1.45 system-ui, -apple-system, Segoe UI, sans-serif;
    }
    .check-card {
      display: grid;
      gap: 10px;
    }
    .check-card.ok {
      border-left: 4px solid var(--ok);
      padding-left: 10px;
    }
    .check-card.fail {
      border-left: 4px solid var(--danger);
      padding-left: 10px;
    }
    .check-card.warn {
      border-left: 4px solid #f59e0b;
      padding-left: 10px;
    }
    .check-card h3 {
      margin: 0;
      font-size: 14px;
    }
    .check-message {
      color: var(--muted);
    }
    .check-facts {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 8px;
    }
    .check-fact {
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px;
      background: #fbfcfd;
    }
    .check-fact-label {
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .check-fact-value {
      margin-top: 3px;
      font-weight: 700;
    }
    .check-lines {
      display: grid;
      gap: 5px;
    }
    .check-line {
      border-radius: 5px;
      padding: 6px 8px;
      background: #f8fafc;
      border: 1px solid var(--border);
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .check-line.fail {
      background: #fff1f2;
      border-color: #fecdd3;
      color: #9f1239;
    }
    .check-line.warn {
      background: #fffbeb;
      border-color: #fde68a;
      color: #92400e;
    }
    .check-experiments {
      display: grid;
      gap: 8px;
    }
    .check-experiment {
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #fbfcfd;
      padding: 10px;
    }
    .check-experiment-title {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      font-weight: 700;
      margin-bottom: 8px;
    }
    .check-experiment-status {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .submit-lock {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 10px;
      background: #fbfcfd;
      color: var(--muted);
      font-size: 12px;
    }
    .submit-lock.locked {
      background: #fff7ed;
      border-color: #fed7aa;
      color: #9a3412;
    }
    .submit-lock-text {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .progress-output {
      overflow: auto;
      max-height: 260px;
      border-radius: 6px;
      display: grid;
      gap: 10px;
    }
    .progress-experiment {
      display: grid;
      gap: 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #fbfcfd;
      padding: 10px;
    }
    .progress-experiment-header,
    .progress-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(140px, 34%) 58px;
      align-items: center;
      gap: 10px;
    }
    .progress-experiment-name {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-weight: 750;
    }
    .progress-row-name {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .progress-bar {
      height: 9px;
      border-radius: 999px;
      background: #dbe3e8;
      overflow: hidden;
    }
    .progress-bar-fill {
      height: 100%;
      border-radius: inherit;
      background: var(--accent);
    }
    .progress-count {
      white-space: nowrap;
      color: var(--muted);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
      text-align: right;
    }
    .stats-table td:nth-child(n+2),
    .stats-table th:nth-child(n+2) {
      text-align: right;
      font-variant-numeric: tabular-nums;
    }
    .stats-files {
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .check-json pre {
      max-height: 180px;
      overflow: auto;
      overflow-wrap: anywhere;
      white-space: pre-wrap;
      margin: 8px 0 0;
      padding: 8px;
      border-radius: 6px;
      background: #101820;
      color: #e7eef2;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .action-output:empty {
      display: none;
    }
    .action-card {
      display: grid;
      gap: 8px;
      margin-bottom: 12px;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-left: 4px solid var(--border);
      border-radius: 6px;
      background: #fbfcfd;
    }
    .action-card.running {
      border-left-color: var(--accent);
    }
    .action-card.ok {
      border-left-color: var(--ok);
    }
    .action-card.fail {
      border-left-color: var(--danger);
    }
    .action-title {
      font-weight: 750;
    }
    .action-card.running .action-title {
      color: var(--accent);
    }
    .action-card.ok .action-title {
      color: var(--ok);
    }
    .action-card.fail .action-title {
      color: var(--danger);
    }
    .action-message {
      color: var(--muted);
    }
    .action-json pre {
      max-height: 180px;
      overflow: auto;
      overflow-wrap: anywhere;
      white-space: pre-wrap;
      margin: 8px 0 0;
      padding: 8px;
      border-radius: 6px;
      background: #101820;
      color: #e7eef2;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .probe-output {
      display: grid;
      gap: 16px;
      font-size: 14px;
      min-width: 0;
      max-width: 100%;
    }
    .probe-panel {
      margin-top: 14px;
    }
    .probe-output > * {
      min-width: 0;
    }
    .probe-placeholder {
      color: var(--muted);
      border: 1px dashed var(--border);
      border-radius: 8px;
      padding: 20px;
      background: #fbfcfd;
    }
    .probe-section {
      display: grid;
      gap: 12px;
    }
    .probe-section-header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }
    .probe-section h3 {
      margin: 0;
      font-size: 17px;
    }
    .probe-section-meta {
      color: var(--muted);
      font-size: 13px;
    }
    .probe-algorithm-list {
      display: grid;
      gap: 10px;
    }
    .probe-algorithm-row {
      display: grid;
      gap: 10px;
      border: 1px solid var(--border);
      border-left: 4px solid var(--accent);
      border-radius: 8px;
      background: white;
      padding: 12px;
    }
    .probe-algorithm-main {
      display: grid;
      grid-template-columns: minmax(180px, 0.8fr) minmax(220px, 1fr) minmax(320px, 1.45fr);
      gap: 12px;
      align-items: stretch;
      min-width: 0;
    }
    .probe-identity {
      display: grid;
      align-content: start;
      gap: 5px;
      min-width: 0;
    }
    .probe-algorithm-title {
      display: grid;
      gap: 4px;
      font-weight: 750;
      font-size: 15px;
    }
    .probe-algorithm-base {
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
    }
    .probe-chain {
      color: var(--muted);
      overflow-wrap: anywhere;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .probe-primary-field {
      min-width: 0;
      display: grid;
      align-content: start;
      gap: 6px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: #f8fafc;
      padding: 10px;
    }
    .probe-primary-label {
      color: var(--muted);
      font-size: 11px;
      font-weight: 750;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .probe-primary-value {
      overflow-wrap: anywhere;
      white-space: pre-wrap;
      font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .probe-empty {
      color: var(--muted);
      font-family: system-ui, -apple-system, Segoe UI, sans-serif;
    }
    .probe-detail-row {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
      border-top: 1px solid var(--border);
      padding-top: 8px;
    }
    .probe-detail-row details > summary {
      cursor: pointer;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .probe-setting-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }
    .probe-setting-chip {
      display: inline-flex;
      max-width: 100%;
      gap: 4px;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: #f8fafc;
      padding: 4px 8px;
      font: 12px/1.35 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      overflow: hidden;
      white-space: nowrap;
    }
    .probe-setting-key {
      flex: 0 0 auto;
      color: var(--muted);
      font-family: system-ui, -apple-system, Segoe UI, sans-serif;
      font-weight: 650;
    }
    .probe-setting-value {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .probe-extra-settings {
      margin-top: 8px;
    }
    .probe-extra-settings pre {
      max-height: 220px;
      overflow: auto;
      overflow-wrap: anywhere;
      white-space: pre-wrap;
      margin: 8px 0 0;
      padding: 8px;
      border-radius: 6px;
      background: #101820;
      color: #e7eef2;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    @media (max-width: 1000px) {
      .probe-algorithm-main {
        grid-template-columns: 1fr;
      }
    }
    .chips {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 8px;
      min-width: 0;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 6px 8px;
      background: var(--panel-2);
      min-width: 0;
      max-width: 100%;
      min-height: 34px;
    }
    .chip-label {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .status-ok { color: var(--ok); font-weight: 650; }
    .status-bad { color: var(--danger); font-weight: 650; }
    .result-toolbar {
      display: flex;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
    }
    .result-file-tabs {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      min-width: 0;
    }
    .csv-file-tab {
      max-width: 240px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .csv-file-tab.active {
      background: #e8f5f3;
      border-color: var(--accent);
      color: var(--text);
      font-weight: 650;
    }
    .csv-tools {
      display: grid;
      gap: 10px;
      margin-bottom: 12px;
    }
    .csv-selectors {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px;
    }
    .compare-select {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .column-selector {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 6px;
      padding: 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #fbfcfd;
      max-height: 170px;
      overflow: auto;
    }
    .column-actions {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }
    .column-actions button {
      height: 28px;
      font-size: 12px;
      padding: 0 8px;
    }
    .csv-table-wrap {
      overflow: auto;
      max-height: calc(100vh - 260px);
      border: 1px solid var(--border);
      border-radius: 6px;
      background: white;
    }
    .csv-table {
      width: max-content;
      min-width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }
    .csv-table th {
      position: sticky;
      top: 0;
      z-index: 1;
      background: #eef4f5;
    }
    .csv-table th.compare-clickable {
      cursor: pointer;
      user-select: none;
    }
    .csv-table th.compare-clickable:hover {
      background: #ddebec;
    }
    .csv-table th.compare-lower-good,
    .csv-table th.compare-higher-good {
      background: #d9e8ea;
      color: var(--text);
    }
    .csv-table th.compare-lower-good::after {
      content: " ↓";
      color: #166534;
      font-weight: 800;
    }
    .csv-table th.compare-higher-good::after {
      content: " ↑";
      color: #166534;
      font-weight: 800;
    }
    .csv-table th,
    .csv-table td {
      max-width: 280px;
      padding: 6px 8px;
      border-bottom: 1px solid var(--border);
      border-right: 1px solid var(--border);
      text-align: left;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      font-variant-numeric: tabular-nums;
    }
    .csv-table td.compare-good {
      background: #dcfce7;
    }
    .csv-table td.compare-bad {
      background: #fee2e2;
    }
    .csv-table td.compare-equal {
      background: #dbeafe;
    }
    .csv-table td.compare-mid {
      background: #ffedd5;
    }
    .csv-summary {
      color: var(--muted);
      font-size: 12px;
    }
    .plot-actions {
      justify-content: flex-end;
    }
    .plot-option {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      height: 34px;
      padding: 0 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: white;
      color: var(--text);
      font-size: 12px;
      white-space: nowrap;
      user-select: none;
    }
    .plot-option input {
      margin: 0;
    }
    .plot-option.disabled {
      color: var(--muted);
      background: #f2f4f7;
      cursor: not-allowed;
    }
    .plot-pdf {
      width: 100%;
      min-height: min(78vh, 900px);
      border: 1px solid var(--border);
      border-radius: 6px;
      background: white;
    }
    .compare-grid {
      display: flex;
      gap: 12px;
      align-items: start;
      overflow-x: auto;
      padding-bottom: 4px;
    }
    .compare-pane {
      display: grid;
      gap: 8px;
      flex: 1 0 min(520px, 100%);
      min-width: 0;
    }
    .compare-pane-title {
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .results-stats {
      display: grid;
      gap: 10px;
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid var(--border);
    }
    .stats-inline-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }
    .csv-empty {
      color: var(--muted);
      padding: 14px;
      border: 1px dashed var(--border);
      border-radius: 6px;
      background: #fbfcfd;
    }
    .modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 20;
      display: grid;
      place-items: center;
      padding: 20px;
      background: rgba(15, 23, 42, 0.42);
    }
    .modal {
      width: min(760px, 100%);
      max-height: calc(100vh - 40px);
      overflow: auto;
      background: white;
      border: 1px solid var(--border);
      border-radius: 8px;
      box-shadow: 0 20px 50px rgba(15, 23, 42, 0.24);
    }
    .modal-header,
    .modal-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      padding: 12px 14px;
      border-bottom: 1px solid var(--border);
      background: #fbfcfd;
    }
    .modal-footer {
      border-top: 1px solid var(--border);
      border-bottom: 0;
    }
    .modal-title {
      font-weight: 750;
    }
    .modal-body {
      display: grid;
      gap: 12px;
      padding: 14px;
    }
    .git-status-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 10px;
    }
    .git-status-column {
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #fbfcfd;
      padding: 10px;
    }
    .git-status-title {
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
      text-transform: uppercase;
    }
    .git-file-list {
      display: grid;
      gap: 5px;
      max-height: 190px;
      overflow: auto;
    }
    .git-file {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .git-message {
      display: grid;
      gap: 6px;
    }
    .git-message textarea {
      min-height: 82px;
    }
    .console-modal {
      width: min(1040px, 100%);
    }
    .console-log {
      display: grid;
      gap: 10px;
      max-height: calc(100vh - 190px);
      overflow: auto;
    }
    .console-entry {
      display: grid;
      gap: 6px;
      min-width: 0;
      padding: 10px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #fbfcfd;
    }
    .console-entry-header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 10px;
      color: var(--muted);
      font-size: 12px;
    }
    .console-entry-title {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--text);
      font-weight: 750;
    }
    .console-entry pre {
      margin: 0;
      max-height: 320px;
      overflow: auto;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      border-radius: 6px;
      background: #101820;
      color: #e7eef2;
      padding: 10px;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .logs-browser {
      display: grid;
      grid-template-columns: minmax(260px, 0.42fr) minmax(0, 1fr);
      gap: 12px;
      min-height: calc(100vh - 210px);
    }
    .logs-sidebar {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 8px;
      min-width: 0;
    }
    .logs-path {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--muted);
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .logs-list {
      display: grid;
      align-content: start;
      gap: 6px;
      min-height: 0;
      overflow: auto;
      padding-right: 4px;
    }
    .log-entry {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      align-items: center;
      gap: 8px;
      width: 100%;
      height: auto;
      min-height: 34px;
      padding: 7px 9px;
      text-align: left;
    }
    .log-entry.active {
      border-color: var(--accent);
      background: #e8f5f3;
    }
    .log-entry-name {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .log-entry-meta {
      color: var(--muted);
      font-size: 11px;
      white-space: nowrap;
    }
    .log-content {
      min-width: 0;
      min-height: 0;
    }
    .log-content pre {
      margin: 0;
      max-height: calc(100vh - 230px);
      overflow: auto;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      border-radius: 6px;
      background: #101820;
      color: #e7eef2;
      padding: 12px;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .markdown-doc {
      color: var(--text);
      max-height: calc(100vh - 210px);
      overflow: auto;
    }
    .markdown-doc h1,
    .markdown-doc h2,
    .markdown-doc h3,
    .markdown-doc h4 {
      margin: 18px 0 8px;
      letter-spacing: 0;
    }
    .markdown-doc h1:first-child,
    .markdown-doc h2:first-child,
    .markdown-doc h3:first-child {
      margin-top: 0;
    }
    .markdown-doc h1 { font-size: 20px; }
    .markdown-doc h2 { font-size: 16px; border-bottom: 1px solid var(--border); padding-bottom: 5px; }
    .markdown-doc h3 { font-size: 14px; }
    .markdown-doc p {
      margin: 7px 0;
      color: #334155;
    }
    .markdown-doc ul {
      margin: 7px 0 7px 20px;
      padding: 0;
    }
    .markdown-doc li {
      margin: 4px 0;
    }
    .markdown-doc code {
      border: 1px solid var(--border);
      border-radius: 5px;
      background: #f8fafc;
      padding: 1px 4px;
      font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .markdown-doc pre {
      margin: 10px 0;
      padding: 10px;
      border-radius: 6px;
      background: #101820;
      color: #e7eef2;
      overflow: auto;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .markdown-doc pre code {
      border: 0;
      background: transparent;
      padding: 0;
      color: inherit;
    }
    .form-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .hidden { display: none; }
    @media (max-width: 980px) {
      .app { grid-template-columns: 1fr; }
      .sidebar { border-right: 0; border-bottom: 1px solid var(--border); }
      .grid { grid-template-columns: 1fr; }
      .compare-grid { grid-template-columns: 1fr; }
      .logs-browser { grid-template-columns: 1fr; }
      .chips { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">
        <h1>mkexp2</h1>
        <div class="brand-actions">
          <button id="refresh" class="icon-button" aria-label="Refresh experiments" title="Refresh experiments">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M16 8h5V3"/></svg>
          </button>
          <button id="git-open" class="icon-button" aria-label="Git status" title="Git status">
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><circle cx="6" cy="18" r="3"/><path d="M6 9v6"/><path d="M8.5 7.5 16 15"/></svg>
          </button>
          <button id="console-open" class="icon-button" aria-label="Console log" title="Console log">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 17 6-6-6-6"/><path d="M12 19h8"/></svg>
          </button>
        </div>
      </div>
      <div class="stack">
        <input id="token" type="password" placeholder="Session token">
        <div class="form-grid">
          <input id="new-name" placeholder="new experiment name">
          <button id="create">Create</button>
        </div>
        <select id="new-preset">
          <option value="">Loading presets...</option>
        </select>
      </div>
      <div id="experiments" class="experiment-list"></div>
      <section class="sidebar-nodes">
        <div class="sidebar-section-header">
          <div class="sidebar-section-title">Nodes</div>
          <button id="refresh-status" class="small-button">Update</button>
        </div>
        <div id="slurm-status" class="node-list muted">No status loaded.</div>
      </section>
    </aside>
    <div id="git-modal" class="modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="git-modal-title">
      <div class="modal">
        <div class="modal-header">
          <div>
            <div id="git-modal-title" class="modal-title">Experiment Repo Git</div>
            <div id="git-repo-summary" class="csv-summary">No status loaded.</div>
          </div>
          <button id="git-close" class="icon-button" aria-label="Close Git dialog" title="Close">x</button>
        </div>
        <div class="modal-body">
          <div id="git-status" class="git-status-grid"></div>
          <label class="git-message">
            <span class="csv-summary">Commit message</span>
            <textarea id="git-message" placeholder="chore: update experiment results"></textarea>
          </label>
          <div id="git-output" class="csv-empty">Open the dialog to load repository status.</div>
        </div>
        <div class="modal-footer">
          <button id="git-refresh">Refresh</button>
          <button id="git-push" class="primary">Push</button>
        </div>
      </div>
    </div>
    <div id="console-modal" class="modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="console-modal-title">
      <div class="modal console-modal">
        <div class="modal-header">
          <div>
            <div id="console-modal-title" class="modal-title">Console Log</div>
            <div id="console-summary" class="csv-summary">No commands logged yet.</div>
          </div>
          <button id="console-close" class="icon-button" aria-label="Close console log" title="Close">x</button>
        </div>
        <div class="modal-body">
          <div id="console-log" class="console-log"></div>
        </div>
        <div class="modal-footer">
          <button id="console-clear">Clear</button>
        </div>
      </div>
    </div>
    <main class="main">
      <div class="view-tabs">
        <button class="view-tab icon-tab" data-view="install-log-view" aria-label="Install Log" title="Install Log">?</button>
        <button class="view-tab active" data-view="experiment-view">Experiment</button>
        <button class="view-tab" data-view="results-view">Results</button>
        <button class="view-tab" data-view="logs-view">Logs</button>
        <button class="view-tab" data-view="plots-view">Plots</button>
      </div>
      <section id="experiment-view" class="view-panel active">
        <section class="grid">
          <div class="panel">
            <div class="panel-header">
              <div>
                <div class="panel-title" id="selected-title">Experiment</div>
                <div class="muted" id="selected-path"></div>
              </div>
              <div class="actions">
                <button id="check">Check</button>
              </div>
            </div>
            <div class="panel-body">
              <div class="editor-shell">
                <pre id="experiment-highlight" class="editor-highlight" aria-hidden="true"></pre>
                <textarea id="experiment-editor" spellcheck="false" wrap="off"></textarea>
              </div>
            </div>
          </div>
          <div class="stack">
            <section class="panel">
              <div class="panel-header">
                <div class="panel-title">Submit</div>
              </div>
              <div class="panel-body stack">
                <div id="algorithm-list" class="chips"></div>
                <div id="submit-lock-status" class="submit-lock hidden">
                  <span class="submit-lock-text"></span>
                  <button id="clear-submit-lock" class="hidden">Unlock</button>
                </div>
                <button class="primary" id="submit">Submit Selected</button>
              </div>
            </section>
            <section class="panel">
              <div class="panel-header">
                <div>
                  <div class="panel-title">Progress</div>
                  <div id="progress-summary" class="csv-summary">No progress loaded.</div>
                </div>
                <button id="refresh-progress">Refresh</button>
              </div>
              <div class="panel-body">
                <div id="progress-output" class="csv-empty">Run progress to count finished log files against expected runs.</div>
              </div>
            </section>
          </div>
        </section>
        <section class="panel probe-panel">
          <div class="panel-header">
            <div>
              <div class="panel-title">Probe</div>
              <div id="probe-summary" class="csv-summary">No probe loaded.</div>
            </div>
            <button id="probe-run">Run Probe</button>
          </div>
          <div class="panel-body">
            <div id="probe-output" class="probe-output">
              <div class="probe-placeholder">Run Probe to inspect enabled algorithms, branch settings, CLI arguments, and resolved properties.</div>
            </div>
          </div>
        </section>
      </section>
      <section id="results-view" class="view-panel">
        <section class="panel">
          <div class="panel-header">
            <div>
              <div class="panel-title">Results</div>
              <div id="results-summary" class="csv-summary">No results loaded.</div>
            </div>
            <div class="actions">
              <button id="parse-results">Parse Logs</button>
              <button id="load-results" class="icon-button" aria-label="Reload CSVs" title="Reload CSVs">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M16 8h5V3"/></svg>
              </button>
            </div>
          </div>
          <div class="panel-body">
            <div class="csv-tools">
              <div class="result-toolbar">
                <div id="result-file-tabs" class="result-file-tabs"></div>
                <div class="column-actions">
                  <button id="columns-all">All columns</button>
                  <button id="columns-none">No columns</button>
                </div>
              </div>
              <div id="column-selector" class="column-selector"></div>
            </div>
            <div id="results" class="csv-empty">Select an experiment, then load CSV results.</div>
            <section class="results-stats">
              <div class="stats-inline-header">
                <div>
                  <div class="panel-title">Stats</div>
                  <div id="stats-summary" class="csv-summary">No stats loaded.</div>
                </div>
                <button id="load-stats" class="icon-button" aria-label="Reload stats" title="Reload stats">
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M16 8h5V3"/></svg>
                </button>
              </div>
              <div id="stats-output" class="csv-empty">Load results to summarize parsed CSV results.</div>
            </section>
          </div>
        </section>
      </section>
      <section id="install-log-view" class="view-panel">
        <section class="panel">
          <div class="panel-header">
            <div>
              <div class="panel-title">Install Log</div>
              <div id="install-log-summary" class="csv-summary">No install log loaded.</div>
            </div>
            <button id="load-install-log" class="icon-button" aria-label="Reload install log" title="Reload install log">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M16 8h5V3"/></svg>
            </button>
          </div>
          <div class="panel-body">
            <div id="install-log" class="csv-empty">Select an experiment, then reload the install log.</div>
          </div>
        </section>
      </section>
      <section id="logs-view" class="view-panel">
        <section class="panel">
          <div class="panel-header">
            <div>
              <div class="panel-title">Logs</div>
              <div id="logs-summary" class="csv-summary">No log directory loaded.</div>
            </div>
            <button id="reload-logs" class="icon-button" aria-label="Reload logs" title="Reload logs">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M16 8h5V3"/></svg>
            </button>
          </div>
          <div class="panel-body">
            <div class="logs-browser">
              <div class="logs-sidebar">
                <div id="logs-path" class="logs-path">logs/</div>
                <div id="logs-list" class="logs-list">
                  <div class="csv-empty">Open the Logs tab to load the log directory.</div>
                </div>
              </div>
              <div id="log-content" class="log-content">
                <div class="csv-empty">Select a log file to load its content.</div>
              </div>
            </div>
          </div>
        </section>
      </section>
      <section id="plots-view" class="view-panel">
        <section class="panel">
          <div class="panel-header">
            <div>
              <div class="panel-title">Plots</div>
              <div id="plots-summary" class="csv-summary">No plot action started.</div>
            </div>
            <div class="actions plot-actions">
              <label class="plot-option" id="plot-no-docker-label" title="Use host R instead of Docker">
                <input id="plot-no-docker" type="checkbox">
                <span>No docker</span>
              </label>
              <button id="plot-results">Generate Plots</button>
            </div>
          </div>
          <div class="panel-body">
            <div id="plot-action-output" class="action-output"></div>
            <div id="plot-file" class="csv-empty">Generate plots to update plots.pdf.</div>
          </div>
        </section>
      </section>
    </main>
  </div>
  <script>
    const state = {
      experiments: [],
      selected: null,
      pinnedExperiments: new Set(),
      algorithms: [],
      presets: [],
      openDirs: new Set(),
      results: [],
      resultsFor: null,
      stats: null,
      statsFor: null,
      selectedResults: [],
      compareColumnModes: {},
      installLog: null,
      installLogFor: null,
      logsDir: '',
      logsListing: null,
      logsFor: null,
      selectedLog: '',
      logContent: null,
      submitLock: null,
      plotBackend: null,
      plotInfo: null,
      plotInfoFor: null,
      plotPdfUrl: '',
      plotPdfUrlFor: null,
      plotPdfVersion: '',
      plotNoDockerTouched: false,
      consoleEntries: [],
      consoleOpen: false,
      progressTimer: null,
      activeView: 'experiment-view'
    };
    const allowEmptyToken = __ALLOW_EMPTY_TOKEN__;
    const tokenInput = document.getElementById('token');
    const editor = document.getElementById('experiment-editor');
    const editorHighlight = document.getElementById('experiment-highlight');
    tokenInput.value = localStorage.getItem('mkexp2-token') || '';
    tokenInput.addEventListener('change', () => {
      localStorage.setItem('mkexp2-token', tokenInput.value);
      out('');
      if (token() || allowEmptyToken) {
        refreshPresets().catch(err => out(String(err)));
        refreshExperiments().catch(err => out(String(err)));
        refreshStatus().catch(err => out(String(err)));
      }
    });

    function token() { return tokenInput.value; }
    function consoleText(value) {
      return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    }
    function appendConsoleLog(title, value) {
      state.consoleEntries.push({
        time: new Date().toLocaleTimeString(),
        title,
        text: consoleText(value)
      });
      if (state.consoleEntries.length > 300) state.consoleEntries.splice(0, state.consoleEntries.length - 300);
      renderConsoleLog();
    }
    function out(value) {
      appendConsoleLog('Message', value);
    }
    function renderConsoleLog() {
      const summary = document.getElementById('console-summary');
      const box = document.getElementById('console-log');
      if (!summary || !box) return;
      summary.textContent = state.consoleEntries.length
        ? `${state.consoleEntries.length} log entr${state.consoleEntries.length === 1 ? 'y' : 'ies'} in this browser session.`
        : 'No commands logged yet.';
      box.innerHTML = '';
      if (!state.consoleEntries.length) {
        const empty = document.createElement('div');
        empty.className = 'csv-empty';
        empty.textContent = 'Commands and their output will appear here after actions run.';
        box.appendChild(empty);
        return;
      }
      for (const entry of state.consoleEntries) {
        const item = document.createElement('section');
        item.className = 'console-entry';
        const header = document.createElement('div');
        header.className = 'console-entry-header';
        const title = document.createElement('div');
        title.className = 'console-entry-title';
        title.textContent = entry.title;
        const time = document.createElement('div');
        time.textContent = entry.time;
        header.appendChild(title);
        header.appendChild(time);
        const pre = document.createElement('pre');
        pre.textContent = entry.text;
        item.appendChild(header);
        item.appendChild(pre);
        box.appendChild(item);
      }
      box.scrollTop = box.scrollHeight;
    }
    function clearTransientOutput() {
      state.consoleEntries = state.consoleEntries.filter(entry => !/session token|missing or invalid token/i.test(entry.text));
      renderConsoleLog();
    }
    function stripAnsi(text) {
      return String(text || '').replace(/\x1b\[[0-9;]*m/g, '');
    }
    function checkCount(text, label) {
      const matches = Array.from(stripAnsi(text).matchAll(new RegExp(`${label}:\\s*(\\d+)`, 'gi')));
      if (!matches.length) return null;
      return matches[matches.length - 1][1];
    }
    function appendCheckFact(container, label, value) {
      const item = document.createElement('div');
      item.className = 'check-fact';
      const itemLabel = document.createElement('div');
      itemLabel.className = 'check-fact-label';
      itemLabel.textContent = label;
      const itemValue = document.createElement('div');
      itemValue.className = 'check-fact-value';
      itemValue.textContent = value;
      item.appendChild(itemLabel);
      item.appendChild(itemValue);
      container.appendChild(item);
    }
    function parseCheckJson(result) {
      const text = stripAnsi(result?.stdout || '').trim();
      if (!text) return null;
      try {
        const parsed = JSON.parse(text);
        return parsed && typeof parsed === 'object' ? parsed : null;
      } catch {
        return null;
      }
    }
    function actionSucceeded(action, kind) {
      if (action?.status !== 'completed') return false;
      if (kind === 'parse') return Boolean(action.result?.parsed);
      if (kind === 'plot') return Boolean(action.result?.plotted);
      return true;
    }
    function actionCommand(action, kind) {
      if (kind === 'parse') return action?.result?.parse;
      if (kind === 'plot') return action?.result?.plot;
      return null;
    }
    function renderActionStatus(targetId, title, action, kind) {
      const target = document.getElementById(targetId);
      if (!target) return;
      const done = action?.status && action.status !== 'running';
      const ok = done && actionSucceeded(action, kind);
      const failed = done && !ok;
      const command = actionCommand(action, kind);
      target.innerHTML = '';
      const card = document.createElement('div');
      card.className = 'action-card ' + (action?.status === 'running' ? 'running' : ok ? 'ok' : failed ? 'fail' : 'running');
      const header = document.createElement('div');
      header.className = 'action-title';
      header.textContent = action?.status === 'running'
        ? `${title} running...`
        : ok
          ? `${title} completed`
          : `${title} failed`;
      card.appendChild(header);
      const message = document.createElement('div');
      message.className = 'action-message';
      if (action?.status === 'running') {
        message.textContent = 'The command is still running on the server.';
      } else if (command) {
        message.textContent = `Return code ${command.returncode}; elapsed ${command.elapsed_seconds ?? '?'}s.`;
      } else {
        message.textContent = 'No command details available.';
      }
      card.appendChild(message);
      const details = document.createElement('details');
      details.className = 'action-json';
      const summary = document.createElement('summary');
      summary.textContent = 'Action JSON';
      const pre = document.createElement('pre');
      pre.textContent = JSON.stringify(action, null, 2);
      details.appendChild(summary);
      details.appendChild(pre);
      card.appendChild(details);
      target.appendChild(card);
    }
    function renderCheckResult(result, saveResult) {
      const payload = parseCheckJson(result);
      const ok = payload ? Boolean(payload.ok) : Number(result.returncode) === 0;
      const warningOnly = ok && Number(payload?.warnings || 0) > 0;
      const combined = `${result.stdout || ''}\n${result.stderr || ''}`;
      const cleanOutput = stripAnsi(combined);
      const importantLines = cleanOutput
        .split(/\r?\n/)
        .map(line => line.trim())
        .filter(line => /\[(fail|warn)\]/i.test(line));
      appendConsoleLog(ok ? (warningOnly ? 'Check passed with warnings' : 'Check passed') : 'Check failed', {
        message: ok
          ? 'Saved the Experiment file and validated the experiment configuration.'
          : 'Saved the Experiment file, but mkexp2 check reported problems.',
        saved: saveResult?.path || null,
        returncode: result.returncode,
        elapsed_seconds: result.elapsed_seconds,
        errors: payload?.errors ?? checkCount(cleanOutput, 'errors') ?? (ok ? '0' : 'unknown'),
        warnings: payload?.warnings ?? checkCount(cleanOutput, 'warnings') ?? '0',
        important_lines: importantLines,
        check: result,
        parsed: payload
      });
    }
    function algorithmDefinitionMap(declared) {
      const map = new Map();
      for (const definition of declared?.algorithm_definitions || []) {
        map.set(definition.name, definition);
      }
      return map;
    }
    function algorithmChain(name, definitionMap) {
      const chain = [];
      const seen = new Set();
      let current = name;
      while (current && !seen.has(current)) {
        seen.add(current);
        const definition = definitionMap.get(current);
        if (!definition) {
          chain.push({ name: current, base: '', args: '', plugin: true });
          break;
        }
        chain.push(definition);
        current = definition.base;
      }
      return chain;
    }
    function probeDisplayValue(value) {
      if (value === '' || value === null || value === undefined) return '(none)';
      return String(value);
    }
    function probeChainText(algorithm, declared) {
      const definitions = algorithmDefinitionMap(declared);
      return algorithmChain(algorithm.name, definitions).map(node => node.name).join(' -> ');
    }
    function renderProbeIdentity(algorithm, declared) {
      const identity = document.createElement('div');
      identity.className = 'probe-identity';
      const title = document.createElement('div');
      title.className = 'probe-algorithm-title';
      const name = document.createElement('div');
      name.textContent = algorithm.name;
      const base = document.createElement('div');
      base.className = 'probe-algorithm-base';
      base.textContent = `base ${algorithm.base || '(none)'}`;
      title.appendChild(name);
      title.appendChild(base);
      const chain = document.createElement('div');
      chain.className = 'probe-chain';
      chain.textContent = probeChainText(algorithm, declared);
      identity.appendChild(title);
      identity.appendChild(chain);
      return identity;
    }
    function renderProbePrimaryField(label, value) {
      const field = document.createElement('div');
      field.className = 'probe-primary-field';
      const fieldLabel = document.createElement('div');
      fieldLabel.className = 'probe-primary-label';
      fieldLabel.textContent = label;
      const fieldValue = document.createElement('div');
      fieldValue.className = 'probe-primary-value';
      fieldValue.textContent = probeDisplayValue(value);
      if (fieldValue.textContent === '(none)') fieldValue.classList.add('probe-empty');
      field.appendChild(fieldLabel);
      field.appendChild(fieldValue);
      return field;
    }
    function probeSettingPairs(algorithm) {
      const pairs = [];
      const add = (key, value) => {
        if (value === '' || value === null || value === undefined) return;
        pairs.push([key, String(value)]);
      };
      add('parser', algorithm.parser?.spec);
      const properties = algorithm.properties || {};
      for (const key of Object.keys(properties).sort()) {
        if (key === 'repo_ref') continue;
        if (key === 'build_key' || key === 'binary_path') continue;
        add(key, properties[key]);
      }
      return pairs;
    }
    function renderProbeSettings(algorithm) {
      const wrapper = document.createElement('details');
      wrapper.className = 'probe-settings-details';
      const settingsSummary = document.createElement('summary');
      settingsSummary.textContent = `Resolved settings (${probeSettingPairs(algorithm).length})`;
      wrapper.appendChild(settingsSummary);
      const chips = document.createElement('div');
      chips.className = 'probe-setting-chips';
      const pairs = probeSettingPairs(algorithm);
      if (!pairs.length) {
        const empty = document.createElement('span');
        empty.className = 'probe-empty';
        empty.textContent = '(none)';
        chips.appendChild(empty);
      }
      for (const [key, value] of pairs) {
        const chip = document.createElement('span');
        chip.className = 'probe-setting-chip';
        const keyNode = document.createElement('span');
        keyNode.className = 'probe-setting-key';
        keyNode.textContent = `${key}=`;
        const valueNode = document.createElement('span');
        valueNode.className = 'probe-setting-value';
        valueNode.title = value;
        valueNode.textContent = value;
        chip.appendChild(keyNode);
        chip.appendChild(valueNode);
        chips.appendChild(chip);
      }
      wrapper.appendChild(chips);
      return wrapper;
    }
    function renderProbeRawJson(algorithm) {
      const details = document.createElement('details');
      details.className = 'probe-extra-settings';
      const summary = document.createElement('summary');
      summary.textContent = 'Raw algorithm JSON';
      const pre = document.createElement('pre');
      pre.textContent = JSON.stringify(algorithm, null, 2);
      details.appendChild(summary);
      details.appendChild(pre);
      return details;
    }
    function renderProbeResult(results, saveResult) {
      const box = document.getElementById('probe-output');
      const summaryBox = document.getElementById('probe-summary');
      box.innerHTML = '';
      box.className = 'probe-output';
      const root = document.createElement('div');
      root.className = 'probe-output';

      const functionCount = results.length;
      const algorithmCount = results.reduce((sum, result) => sum + (result.resolved?.algorithms?.length || 0), 0);
      summaryBox.textContent = `Probed ${functionCount} experiment function(s), ${algorithmCount} enabled algorithm(s).`;
      if (saveResult?.path) summaryBox.textContent += ` Source: ${saveResult.path}`;

      for (const result of results) {
        const section = document.createElement('section');
        section.className = 'probe-section';
        const sectionHeader = document.createElement('div');
        sectionHeader.className = 'probe-section-header';
        const title = document.createElement('h3');
        title.textContent = `${result.experiment?.name || 'Experiment'} (${result.experiment?.function || 'unknown'})`;
        sectionHeader.appendChild(title);

        const details = document.createElement('div');
        details.className = 'probe-section-meta';
        details.textContent = `System ${result.experiment?.system || 'unknown'}; ${result.resolved?.algorithms?.length || 0} enabled algorithm(s).`;
        sectionHeader.appendChild(details);
        section.appendChild(sectionHeader);

        const list = document.createElement('div');
        list.className = 'probe-algorithm-list';

        for (const algorithm of result.resolved?.algorithms || []) {
          const row = document.createElement('article');
          row.className = 'probe-algorithm-row';
          const main = document.createElement('div');
          main.className = 'probe-algorithm-main';
          main.appendChild(renderProbeIdentity(algorithm, result.declared || {}));
          main.appendChild(renderProbePrimaryField('Branch', algorithm.properties?.repo_ref || ''));
          main.appendChild(renderProbePrimaryField('CLI arguments', algorithm.args || ''));
          row.appendChild(main);
          const detailRow = document.createElement('div');
          detailRow.className = 'probe-detail-row';
          detailRow.appendChild(renderProbeSettings(algorithm));
          detailRow.appendChild(renderProbeRawJson(algorithm));
          row.appendChild(detailRow);
          list.appendChild(row);
        }
        section.appendChild(list);
        root.appendChild(section);
      }

      const raw = document.createElement('details');
      raw.className = 'action-json';
      const summary = document.createElement('summary');
      summary.textContent = 'Probe JSON';
      const pre = document.createElement('pre');
      pre.textContent = JSON.stringify(results, null, 2);
      raw.appendChild(summary);
      raw.appendChild(pre);
      root.appendChild(raw);
      box.appendChild(root);
    }
    function isCommandResult(value) {
      return value && typeof value === 'object'
        && (Array.isArray(value.argv) || typeof value.cmd === 'string')
        && ('returncode' in value || 'stdout' in value || 'stderr' in value);
    }
    function collectCommandResults(value, prefix = '', out = []) {
      if (!value || typeof value !== 'object') return out;
      if (isCommandResult(value)) {
        out.push({ label: prefix || 'command', command: value });
        return out;
      }
      if (Array.isArray(value)) {
        value.forEach((item, index) => collectCommandResults(item, `${prefix}[${index}]`, out));
        return out;
      }
      for (const [key, child] of Object.entries(value)) {
        const label = prefix ? `${prefix}.${key}` : key;
        collectCommandResults(child, label, out);
      }
      return out;
    }
    function logApiCommands(method, path, payload) {
      const commands = collectCommandResults(payload);
      for (const item of commands) {
        appendConsoleLog(`${method} ${path} :: ${item.label}`, item.command);
      }
    }
    async function api(path, options = {}) {
      const method = options.method || 'GET';
      const headers = Object.assign({ 'X-MKEXP2-Token': token() }, options.headers || {});
      if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
      const response = await fetch(path, Object.assign({}, options, { headers }));
      if (!response.ok) {
        const text = await response.text();
        appendConsoleLog(`${method} ${path} failed`, text);
        throw new Error(text);
      }
      const payload = response.headers.get('content-type')?.includes('application/json')
        ? response.json()
        : response.text();
      const data = await payload;
      logApiCommands(method, path, data);
      return data;
    }
    async function fetchBlob(path) {
      const response = await fetch(path, { headers: { 'X-MKEXP2-Token': token() } });
      if (!response.ok) {
        const text = await response.text();
        appendConsoleLog(`GET ${path} failed`, text);
        throw new Error(text);
      }
      return await response.blob();
    }
    function clearPlotPdfUrl() {
      if (state.plotPdfUrl) URL.revokeObjectURL(state.plotPdfUrl);
      state.plotPdfUrl = '';
      state.plotPdfUrlFor = null;
      state.plotPdfVersion = '';
    }
    function esc(value) {
      return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[char]));
    }
    function renderGitStatus(status) {
      const repoSummary = document.getElementById('git-repo-summary');
      const grid = document.getElementById('git-status');
      const output = document.getElementById('git-output');
      repoSummary.textContent = `${status.repo || 'experiment repo'}${status.branch ? ` on ${status.branch}` : ''}`;
      grid.innerHTML = '';
      const groups = status.groups || {};
      for (const [key, label] of [['added', 'Added'], ['modified', 'Modified'], ['deleted', 'Deleted']]) {
        const column = document.createElement('section');
        column.className = 'git-status-column';
        const title = document.createElement('div');
        title.className = 'git-status-title';
        const files = groups[key] || [];
        title.textContent = `${label} (${files.length})`;
        const list = document.createElement('div');
        list.className = 'git-file-list';
        if (!files.length) {
          const empty = document.createElement('div');
          empty.className = 'csv-summary';
          empty.textContent = 'None';
          list.appendChild(empty);
        }
        for (const file of files) {
          const item = document.createElement('div');
          item.className = 'git-file';
          item.textContent = file.path;
          item.title = `${file.status} ${file.path}`;
          list.appendChild(item);
        }
        column.appendChild(title);
        column.appendChild(list);
        grid.appendChild(column);
      }
      output.className = status.dirty ? 'csv-empty' : 'csv-empty status-ok';
      output.textContent = status.dirty
        ? 'Enter a commit message, then push to commit and push the experiment repo.'
        : 'No local experiment repo changes. Push will still run git push.';
    }
    async function loadGitStatus() {
      const output = document.getElementById('git-output');
      output.className = 'csv-empty';
      output.textContent = 'Loading experiment repo status...';
      const status = await api('/api/git/status');
      renderGitStatus(status);
      return status;
    }
    async function openGitDialog() {
      document.getElementById('git-modal').classList.remove('hidden');
      await loadGitStatus().catch(err => {
        const output = document.getElementById('git-output');
        output.className = 'csv-empty status-bad';
        output.textContent = String(err);
      });
    }
    function closeGitDialog() {
      document.getElementById('git-modal').classList.add('hidden');
    }
    function openConsoleDialog() {
      state.consoleOpen = true;
      document.getElementById('console-modal').classList.remove('hidden');
      renderConsoleLog();
    }
    function closeConsoleDialog() {
      state.consoleOpen = false;
      document.getElementById('console-modal').classList.add('hidden');
    }
    function clearConsoleLog() {
      state.consoleEntries = [];
      renderConsoleLog();
    }
    async function pushGitChanges() {
      const message = document.getElementById('git-message').value.trim();
      const output = document.getElementById('git-output');
      const button = document.getElementById('git-push');
      if (!message) {
        output.className = 'csv-empty status-bad';
        output.textContent = 'Commit message is required.';
        return;
      }
      button.disabled = true;
      output.className = 'csv-empty';
      output.textContent = 'Committing and pushing experiment repo...';
      try {
        const result = await api('/api/git/push', {
          method: 'POST',
          body: JSON.stringify({ message })
        });
        if (result.ok) {
          closeGitDialog();
          out('Experiment repo pushed.');
        } else {
          output.className = 'output rendered-output';
          output.textContent = JSON.stringify(result, null, 2);
        }
      } catch (err) {
        output.className = 'csv-empty status-bad';
        output.textContent = String(err);
      } finally {
        button.disabled = false;
      }
    }
    function appendInlineMarkdown(parent, text) {
      const pattern = /(`[^`]+`|\*\*[^*]+\*\*)/g;
      let cursor = 0;
      for (const match of text.matchAll(pattern)) {
        if (match.index > cursor) parent.appendChild(document.createTextNode(text.slice(cursor, match.index)));
        const value = match[0];
        const node = value.startsWith('`') ? document.createElement('code') : document.createElement('strong');
        node.textContent = value.startsWith('`') ? value.slice(1, -1) : value.slice(2, -2);
        parent.appendChild(node);
        cursor = match.index + value.length;
      }
      if (cursor < text.length) parent.appendChild(document.createTextNode(text.slice(cursor)));
    }
    function renderMarkdown(markdown, target) {
      target.innerHTML = '';
      target.className = 'markdown-doc';
      const lines = String(markdown || '').split(/\r?\n/);
      let paragraph = [];
      let list = null;
      let code = null;

      const flushParagraph = () => {
        if (!paragraph.length) return;
        const p = document.createElement('p');
        appendInlineMarkdown(p, paragraph.join(' '));
        target.appendChild(p);
        paragraph = [];
      };
      const flushList = () => {
        if (!list) return;
        target.appendChild(list);
        list = null;
      };
      const flushCode = () => {
        if (!code) return;
        const pre = document.createElement('pre');
        const codeNode = document.createElement('code');
        codeNode.textContent = code.join('\n');
        pre.appendChild(codeNode);
        target.appendChild(pre);
        code = null;
      };

      for (const line of lines) {
        if (code) {
          if (/^```/.test(line)) flushCode();
          else code.push(line);
          continue;
        }
        if (/^```/.test(line)) {
          flushParagraph();
          flushList();
          code = [];
          continue;
        }
        const heading = line.match(/^(#{1,4})\s+(.+)$/);
        if (heading) {
          flushParagraph();
          flushList();
          const level = Math.min(heading[1].length, 4);
          const h = document.createElement(`h${level}`);
          appendInlineMarkdown(h, heading[2]);
          target.appendChild(h);
          continue;
        }
        const bullet = line.match(/^\s*[-*]\s+(.+)$/);
        if (bullet) {
          flushParagraph();
          if (!list) list = document.createElement('ul');
          const item = document.createElement('li');
          appendInlineMarkdown(item, bullet[1]);
          list.appendChild(item);
          continue;
        }
        if (!line.trim()) {
          flushParagraph();
          flushList();
          continue;
        }
        flushList();
        paragraph.push(line.trim());
      }
      flushCode();
      flushParagraph();
      flushList();
    }
    function span(className, value) {
      return `<span class="${className}">${esc(value)}</span>`;
    }
    const experimentKeywords = new Set([
      'System', 'Wrapper', 'Property', 'SystemProperty', 'AlgorithmProperty',
      'DefineAlgorithm', 'Algorithms', 'Threads', 'Seeds', 'Ks', 'Epsilons',
      'Timelimit', 'TimelimitPerInstance', 'Graphs', 'Graph'
    ]);
    const shellKeywords = new Set([
      'if', 'then', 'elif', 'else', 'fi', 'for', 'while', 'do', 'done', 'case',
      'esac', 'in', 'function', 'local', 'typeset', 'return', 'true', 'false'
    ]);
    function commentIndex(line) {
      let quote = '';
      for (let index = 0; index < line.length; index += 1) {
        const char = line[index];
        const previous = line[index - 1];
        if (quote) {
          if (char === quote && previous !== '\\') quote = '';
          continue;
        }
        if (char === '"' || char === "'") {
          quote = char;
          continue;
        }
        if (char === '#') return index;
      }
      return -1;
    }
    function readVariable(code, start) {
      if (code[start + 1] === '{') {
        const end = code.indexOf('}', start + 2);
        return end === -1 ? code.length : end + 1;
      }
      if (code[start + 1] === '(') {
        let depth = 1;
        for (let index = start + 2; index < code.length; index += 1) {
          if (code[index] === '(') depth += 1;
          if (code[index] === ')') depth -= 1;
          if (depth === 0) return index + 1;
        }
        return code.length;
      }
      const match = code.slice(start).match(/^\$[A-Za-z_][A-Za-z0-9_]*/);
      return match ? start + match[0].length : start + 1;
    }
    function highlightCode(code) {
      let html = '';
      for (let index = 0; index < code.length;) {
        const char = code[index];
        if (char === '"' || char === "'") {
          let end = index + 1;
          while (end < code.length) {
            const current = code[end];
            const previous = code[end - 1];
            end += 1;
            if (current === char && previous !== '\\') break;
          }
          html += span('tok-string', code.slice(index, end));
          index = end;
          continue;
        }
        if (char === '$') {
          const end = readVariable(code, index);
          html += span('tok-variable', code.slice(index, end));
          index = end;
          continue;
        }
        const number = code.slice(index).match(/^[0-9]+(?:\.[0-9]+)?(?:x[0-9]+)*/);
        if (number) {
          html += span('tok-number', number[0]);
          index += number[0].length;
          continue;
        }
        const word = code.slice(index).match(/^[A-Za-z_][A-Za-z0-9_.-]*/);
        if (word) {
          const value = word[0];
          const rest = code.slice(index + value.length);
          if (experimentKeywords.has(value)) {
            html += span('tok-keyword', value);
          } else if (shellKeywords.has(value)) {
            html += span('tok-shell', value);
          } else if (/^Experiment[A-Za-z0-9_]*$/.test(value) && /^\s*\(\)/.test(rest)) {
            html += span('tok-function', value);
          } else {
            html += esc(value);
          }
          index += value.length;
          continue;
        }
        html += esc(char);
        index += 1;
      }
      return html;
    }
    function highlightExperiment(text) {
      return text.split('\n').map(line => {
        if (line.startsWith('#!')) return span('tok-comment', line);
        const hash = commentIndex(line);
        if (hash === -1) return highlightCode(line);
        return highlightCode(line.slice(0, hash)) + span('tok-comment', line.slice(hash));
      }).join('\n') + '\n';
    }
    function syncEditorHighlight() {
      editorHighlight.scrollTop = editor.scrollTop;
      editorHighlight.scrollLeft = editor.scrollLeft;
    }
    function updateEditorHighlight() {
      editorHighlight.innerHTML = highlightExperiment(editor.value);
      syncEditorHighlight();
    }
    function setEditorValue(value) {
      editor.value = value;
      updateEditorHighlight();
    }
    editor.addEventListener('input', updateEditorHighlight);
    editor.addEventListener('scroll', syncEditorHighlight);
    updateEditorHighlight();
    function parseCsv(text) {
      const rows = [];
      let row = [];
      let field = '';
      let quoted = false;
      for (let index = 0; index < text.length; index += 1) {
        const char = text[index];
        if (quoted) {
          if (char === '"') {
            if (text[index + 1] === '"') {
              field += '"';
              index += 1;
            } else {
              quoted = false;
            }
          } else {
            field += char;
          }
          continue;
        }
        if (char === '"') {
          quoted = true;
          continue;
        }
        if (char === ',') {
          row.push(field);
          field = '';
          continue;
        }
        if (char === '\n') {
          row.push(field);
          rows.push(row);
          row = [];
          field = '';
          continue;
        }
        if (char === '\r') continue;
        field += char;
      }
      row.push(field);
      rows.push(row);
      if (rows.length && rows[rows.length - 1].every(cell => cell === '') && /[\r\n]$/.test(text)) {
        rows.pop();
      }
      return rows;
    }
    function prepareCsvFile(file) {
      const parsed = parseCsv(file.content || '');
      return Object.assign({}, file, {
        headers: parsed[0] || [],
        rows: parsed.slice(1)
      });
    }
    function uniqueHeaders(headers) {
      const seen = new Set();
      const out = [];
      for (const header of headers) {
        const text = String(header ?? '');
        if (!seen.has(text)) {
          seen.add(text);
          out.push(text);
        }
      }
      return out;
    }
    function headersForFiles(files) {
      return uniqueHeaders(files.flatMap(file => file?.headers || []));
    }
    function columnStorageKey(headers) {
      const signature = encodeURIComponent(uniqueHeaders(headers).join('\u001f'));
      return `mkexp2-columns:${state.selected || 'none'}:${signature}`;
    }
    function visibleColumns(headers) {
      const all = uniqueHeaders(headers);
      const raw = localStorage.getItem(columnStorageKey(all));
      if (!raw) return all;
      try {
        const saved = JSON.parse(raw);
        if (!Array.isArray(saved)) return all;
        const allowed = new Set(all);
        return saved.filter(name => allowed.has(name));
      } catch (_err) {
        return all;
      }
    }
    function saveVisibleColumns(headers, columns) {
      localStorage.setItem(columnStorageKey(headers), JSON.stringify(uniqueHeaders(columns)));
    }
    function findResult(name) {
      return state.results.find(file => file.name === name) || null;
    }
    function csvLabel(name) {
      return String(name || '').replace(/\.csv$/i, '');
    }
    function numericCsvValue(value) {
      const text = String(value ?? '').trim();
      if (!text) return null;
      const parsed = Number(text);
      return Number.isFinite(parsed) ? parsed : null;
    }
    function compareCellClass(value, peerValues, mode) {
      if (!mode) return '';
      const current = numericCsvValue(value);
      if (current === null) return '';
      const peers = (Array.isArray(peerValues) ? peerValues : [peerValues])
        .map(numericCsvValue)
        .filter(item => item !== null);
      if (!peers.length) return '';
      const values = [current, ...peers];
      const min = Math.min(...values);
      const max = Math.max(...values);
      if (min === max) return 'compare-equal';
      if (mode === 1) {
        if (current === min) return 'compare-good';
        if (current === max) return 'compare-bad';
      } else if (mode === 2) {
        if (current === max) return 'compare-good';
        if (current === min) return 'compare-bad';
      }
      return values.length > 2 ? 'compare-mid' : '';
    }
    function cycleCompareColumn(header) {
      const current = state.compareColumnModes[header] || 0;
      const next = (current + 1) % 3;
      if (next === 0) {
        delete state.compareColumnModes[header];
      } else {
        state.compareColumnModes[header] = next;
      }
      setTimeout(renderResultsWorkspace, 0);
    }
    function syncCompareScroll(...boxes) {
      let syncing = false;
      const sync = (source, target) => {
        if (syncing) return;
        syncing = true;
        for (const box of boxes) {
          if (box === source) continue;
          box.scrollTop = source.scrollTop;
          box.scrollLeft = source.scrollLeft;
        }
        requestAnimationFrame(() => {
          syncing = false;
        });
      };
      for (const box of boxes) {
        box.onscroll = () => sync(box);
      }
    }
    function renderColumnSelector(container, headers, onChange) {
      const all = uniqueHeaders(headers);
      const visible = new Set(visibleColumns(all));
      container.innerHTML = '';
      if (!all.length) {
        container.className = 'csv-empty';
        container.textContent = 'No CSV columns.';
        return;
      }
      container.className = 'column-selector';
      for (const header of all) {
        const label = document.createElement('label');
        label.className = 'chip';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = header;
        checkbox.checked = visible.has(header);
        checkbox.onchange = () => {
          const selected = Array.from(container.querySelectorAll('input:checked')).map(item => item.value);
          saveVisibleColumns(all, selected);
          onChange();
        };
        const text = document.createElement('span');
        text.className = 'chip-label';
        text.textContent = header || '(empty)';
        text.title = header;
        label.appendChild(checkbox);
        label.appendChild(text);
        container.appendChild(label);
      }
    }
    function setAllColumns(headers, selected, onChange) {
      const all = uniqueHeaders(headers);
      saveVisibleColumns(all, selected ? all : []);
      onChange();
    }
    function renderCsvTable(file, container, headers, options = {}) {
      container.innerHTML = '';
      container.onscroll = null;
      if (!file) {
        container.className = 'csv-empty';
        container.textContent = 'No CSV selected.';
        return;
      }
      const allHeaders = uniqueHeaders(headers);
      const shown = visibleColumns(allHeaders);
      if (!shown.length) {
        container.className = 'csv-empty';
        container.textContent = 'No columns selected.';
        return;
      }
      container.className = 'csv-table-wrap';
      const headerIndex = new Map();
      file.headers.forEach((header, index) => {
        if (!headerIndex.has(header)) headerIndex.set(header, index);
      });
      const peers = options.peers || (options.peer ? [options.peer] : []);
      const peerHeaderIndexes = peers.map(peer => {
        const indexMap = new Map();
        peer.headers.forEach((header, index) => {
          if (!indexMap.has(header)) indexMap.set(header, index);
        });
        return indexMap;
      });
      const table = document.createElement('table');
      table.className = 'csv-table';
      const thead = document.createElement('thead');
      const headRow = document.createElement('tr');
      for (const header of shown) {
        const th = document.createElement('th');
        th.textContent = header || '(empty)';
        th.title = header;
        if (options.compare) {
          const mode = state.compareColumnModes[header] || 0;
          th.classList.add('compare-clickable');
          if (mode === 1) th.classList.add('compare-lower-good');
          if (mode === 2) th.classList.add('compare-higher-good');
          th.tabIndex = 0;
          th.title = mode === 1
            ? `${header} - lower values are green; click to prefer higher values`
            : mode === 2
              ? `${header} - higher values are green; click to clear`
              : `${header} - click to color lower values green`;
          th.onclick = () => cycleCompareColumn(header);
          th.onkeydown = event => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              cycleCompareColumn(header);
            }
          };
        }
        headRow.appendChild(th);
      }
      thead.appendChild(headRow);
      table.appendChild(thead);
      const tbody = document.createElement('tbody');
      file.rows.forEach((row, rowIndex) => {
        const tr = document.createElement('tr');
        for (const header of shown) {
          const td = document.createElement('td');
          const index = headerIndex.has(header) ? headerIndex.get(header) : -1;
          const value = index >= 0 ? (row[index] ?? '') : '';
          if (options.compare && peers.length) {
            const peerValues = peers.map((peer, peerNumber) => {
              const peerHeaderIndex = peerHeaderIndexes[peerNumber];
              const peerIndex = peerHeaderIndex.has(header) ? peerHeaderIndex.get(header) : -1;
              const peerRow = peer.rows[rowIndex] || [];
              return peerIndex >= 0 ? (peerRow[peerIndex] ?? '') : '';
            });
            const cellClass = compareCellClass(value, peerValues, state.compareColumnModes[header] || 0);
            if (cellClass) td.classList.add(cellClass);
          }
          td.textContent = value;
          td.title = value;
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      container.appendChild(table);
      if (file.truncated) {
        const note = document.createElement('div');
        note.className = 'csv-summary';
        note.textContent = 'File preview is truncated by the backend response limit.';
        container.appendChild(note);
      }
    }
    function renderResultFileTabs() {
      const tabs = document.getElementById('result-file-tabs');
      tabs.innerHTML = '';
      const selected = new Set(state.selectedResults || []);
      for (const file of state.results) {
        const button = document.createElement('button');
        button.className = 'csv-file-tab' + (selected.has(file.name) ? ' active' : '');
        button.setAttribute('aria-pressed', selected.has(file.name) ? 'true' : 'false');
        button.textContent = csvLabel(file.name);
        button.title = csvLabel(file.name);
        button.onclick = () => {
          const current = new Set(state.selectedResults || []);
          if (current.has(file.name)) {
            current.delete(file.name);
          } else {
            current.add(file.name);
          }
          state.selectedResults = state.results
            .map(item => item.name)
            .filter(name => current.has(name));
          renderResultsWorkspace();
        };
        tabs.appendChild(button);
      }
    }
    function renderResultsWorkspace() {
      const summary = document.getElementById('results-summary');
      const box = document.getElementById('results');
      const selector = document.getElementById('column-selector');
      const allButton = document.getElementById('columns-all');
      const noneButton = document.getElementById('columns-none');
      state.selectedResults = (state.selectedResults || []).filter(name => findResult(name));
      renderResultFileTabs();
      allButton.disabled = true;
      noneButton.disabled = true;
      if (!state.selected) {
        summary.textContent = 'No experiment selected.';
        box.className = 'csv-empty';
        box.textContent = 'Select an experiment first.';
        selector.className = 'column-selector';
        selector.innerHTML = '';
        return;
      }
      if (!state.results.length) {
        summary.textContent = 'No CSV files loaded.';
        box.className = 'csv-empty';
        box.textContent = 'No CSV files loaded.';
        selector.className = 'column-selector';
        selector.innerHTML = '';
        return;
      }
      const selectedFiles = state.selectedResults.map(findResult).filter(Boolean);
      if (!selectedFiles.length) {
        state.compareColumnModes = {};
        summary.textContent = `${state.results.length} CSV file(s) loaded.`;
        box.onscroll = null;
        box.className = 'csv-empty';
        box.textContent = 'Select one or more algorithms above.';
        selector.className = 'column-selector';
        selector.innerHTML = '';
        return;
      }

      if (selectedFiles.length === 1) {
        state.compareColumnModes = {};
        const file = selectedFiles[0];
        summary.textContent = `${state.results.length} CSV file(s), ${file.rows.length} row(s) in ${csvLabel(file.name)}`;
        renderColumnSelector(selector, file.headers, renderResultsWorkspace);
        allButton.disabled = false;
        noneButton.disabled = false;
        allButton.onclick = () => setAllColumns(file.headers, true, renderResultsWorkspace);
        noneButton.onclick = () => setAllColumns(file.headers, false, renderResultsWorkspace);
        renderCsvTable(file, box, file.headers);
        return;
      }

      const headers = headersForFiles(selectedFiles);
      summary.textContent = `${selectedFiles.length}-way comparison: ${selectedFiles.map(file => csvLabel(file.name)).join(' vs ')}`;
      const rowCount = selectedFiles[0].rows.length;
      const mismatch = selectedFiles.find(file => file.rows.length !== rowCount);
      if (mismatch) {
        const details = selectedFiles.map(file => `${csvLabel(file.name)} has ${file.rows.length}`).join(', ');
        const message = `Cannot compare: row counts differ (${details}).`;
        summary.textContent = message;
        selector.className = 'csv-empty status-bad';
        selector.textContent = 'Row-wise comparison is disabled until all selected CSV files have the same number of rows.';
        box.onscroll = null;
        box.className = 'csv-empty status-bad';
        box.textContent = message;
        return;
      }
      renderColumnSelector(selector, headers, renderResultsWorkspace);
      allButton.disabled = false;
      noneButton.disabled = false;
      allButton.onclick = () => setAllColumns(headers, true, renderResultsWorkspace);
      noneButton.onclick = () => setAllColumns(headers, false, renderResultsWorkspace);

      box.onscroll = null;
      box.className = 'compare-grid';
      box.innerHTML = '';
      const scrollBoxes = [];
      for (const file of selectedFiles) {
        const pane = document.createElement('div');
        pane.className = 'compare-pane';
        const title = document.createElement('div');
        title.className = 'compare-pane-title';
        title.textContent = csvLabel(file.name);
        title.title = csvLabel(file.name);
        const tableBox = document.createElement('div');
        tableBox.className = 'csv-empty';
        pane.appendChild(title);
        pane.appendChild(tableBox);
        box.appendChild(pane);
        renderCsvTable(file, tableBox, headers, {
          compare: true,
          peers: selectedFiles.filter(peer => peer !== file),
        });
        scrollBoxes.push(tableBox);
      }
      syncCompareScroll(...scrollBoxes);
    }
    function formatStatNumber(value) {
      const parsed = Number(value);
      if (!Number.isFinite(parsed)) return 'n/a';
      if (Math.abs(parsed) >= 1000) return parsed.toLocaleString(undefined, { maximumFractionDigits: 2 });
      return parsed.toLocaleString(undefined, { maximumSignificantDigits: 6 });
    }
    function renderStatsWorkspace() {
      const summary = document.getElementById('stats-summary');
      const box = document.getElementById('stats-output');
      if (!state.selected) {
        summary.textContent = 'No experiment selected.';
        box.className = 'csv-empty';
        box.textContent = 'Select an experiment first.';
        return;
      }
      if (state.statsFor !== state.selected || !state.stats) {
        summary.textContent = 'No stats loaded.';
        box.className = 'csv-empty';
        box.textContent = 'Reload stats to summarize parsed CSV results.';
        return;
      }
      const stats = state.stats.stats_json || null;
      const algorithms = stats?.algorithms || [];
      if (!stats || !algorithms.length) {
        summary.textContent = 'No stats available.';
        box.className = 'csv-empty';
        box.textContent = 'No parsed CSV results found. Run Parse Logs first.';
        return;
      }
      summary.textContent = `${algorithms.length} algorithm(s), ${algorithms.reduce((sum, item) => sum + Number(item.rows || 0), 0)} row(s)`;
      box.className = 'csv-table-wrap';
      box.innerHTML = '';
      const table = document.createElement('table');
      table.className = 'stats-table';
      const thead = document.createElement('thead');
      const head = document.createElement('tr');
      for (const label of ['Algorithm', 'Rows', 'Failed', 'GMean Cut', 'GMean Time', 'Files']) {
        const th = document.createElement('th');
        th.textContent = label;
        head.appendChild(th);
      }
      thead.appendChild(head);
      table.appendChild(thead);
      const tbody = document.createElement('tbody');
      for (const item of algorithms) {
        const tr = document.createElement('tr');
        const cells = [
          item.algorithm || '',
          String(item.rows ?? 0),
          String(item.failed ?? 0),
          formatStatNumber(item.avg_cut),
          formatStatNumber(item.avg_time),
          (item.files || []).map(csvLabel).join(', ')
        ];
        for (const [index, value] of cells.entries()) {
          const td = document.createElement('td');
          td.textContent = value;
          td.title = value;
          if (index === 5) td.className = 'stats-files';
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      box.appendChild(table);
    }
    function renderInstallLogWorkspace() {
      const summary = document.getElementById('install-log-summary');
      const box = document.getElementById('install-log');
      if (!state.selected) {
        summary.textContent = 'No experiment selected.';
        box.className = 'csv-empty';
        box.textContent = 'Select an experiment first.';
        return;
      }
      if (state.installLogFor !== state.selected || !state.installLog) {
        summary.textContent = 'No install log loaded.';
        box.className = 'csv-empty';
        box.textContent = 'Reload the install log for this experiment.';
        return;
      }
      if (!state.installLog.exists) {
        summary.textContent = 'logs/install.md does not exist.';
        box.className = 'csv-empty';
        box.textContent = 'No install log exists for this experiment yet. Run install or submit with install first.';
        return;
      }
      const suffix = state.installLog.truncated ? ' (truncated)' : '';
      summary.textContent = `logs/install.md, ${state.installLog.size || 0} bytes, modified ${state.installLog.modified_at || 'unknown'}${suffix}`;
      renderMarkdown(state.installLog.content || '', box);
    }
    function formatBytes(value) {
      const size = Number(value);
      if (!Number.isFinite(size)) return '';
      if (size < 1024) return `${size} B`;
      if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`;
      if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MiB`;
      return `${(size / 1024 / 1024 / 1024).toFixed(1)} GiB`;
    }
    function parentLogDir(dir) {
      const parts = String(dir || '').split('/').filter(Boolean);
      if (parts.length <= 2) return '';
      parts.pop();
      return parts.join('/');
    }
    function renderLogsWorkspace() {
      const summary = document.getElementById('logs-summary');
      const pathLabel = document.getElementById('logs-path');
      const list = document.getElementById('logs-list');
      const content = document.getElementById('log-content');
      if (!state.selected) {
        summary.textContent = 'No experiment selected.';
        pathLabel.textContent = 'logs/';
        list.innerHTML = '<div class="csv-empty">Select an experiment first.</div>';
        content.innerHTML = '<div class="csv-empty">Select an experiment first.</div>';
        return;
      }
      if (state.logsFor !== state.selected || !state.logsListing) {
        summary.textContent = 'No log directory loaded.';
        pathLabel.textContent = 'logs/';
        list.innerHTML = '<div class="csv-empty">Open the Logs tab to load the log directory.</div>';
        content.innerHTML = '<div class="csv-empty">Select a log file to load its content.</div>';
        return;
      }
      const listing = state.logsListing;
      const dir = listing.dir || '';
      pathLabel.textContent = `logs/${dir}${dir ? '/' : ''}`;
      if (!listing.exists) {
        summary.textContent = 'logs/ does not exist.';
        list.innerHTML = '<div class="csv-empty">No logs directory exists for this experiment yet.</div>';
        content.innerHTML = '<div class="csv-empty">Run experiments first, then reload logs.</div>';
        return;
      }
      const countText = listing.has_more
        ? `${listing.entries.length} of ${listing.total} entries shown`
        : `${listing.total} entries`;
      summary.textContent = `${pathLabel.textContent}, ${countText}`;
      list.innerHTML = '';
      if (dir) {
        const up = document.createElement('button');
        up.className = 'log-entry';
        up.innerHTML = '<span>..</span><span class="log-entry-name">Parent directory</span><span class="log-entry-meta"></span>';
        up.onclick = () => loadLogs(parentLogDir(dir));
        list.appendChild(up);
      }
      if (!listing.entries.length && !dir) {
        list.innerHTML = '<div class="csv-empty">No log files found.</div>';
      } else if (!listing.entries.length) {
        const empty = document.createElement('div');
        empty.className = 'csv-empty';
        empty.textContent = 'This log directory is empty.';
        list.appendChild(empty);
      }
      for (const entry of listing.entries) {
        const button = document.createElement('button');
        button.className = 'log-entry' + (entry.type === 'file' && state.selectedLog === entry.path ? ' active' : '');
        const icon = document.createElement('span');
        icon.textContent = entry.type === 'dir' ? '>' : '';
        const name = document.createElement('span');
        name.className = 'log-entry-name';
        name.textContent = entry.name;
        name.title = entry.path;
        const meta = document.createElement('span');
        meta.className = 'log-entry-meta';
        meta.textContent = entry.type === 'dir' ? 'dir' : formatBytes(entry.size);
        button.appendChild(icon);
        button.appendChild(name);
        button.appendChild(meta);
        button.onclick = () => {
          if (entry.type === 'dir') loadLogs(entry.path);
          else loadLogFile(entry.path);
        };
        list.appendChild(button);
      }
      if (listing.has_more) {
        const more = document.createElement('div');
        more.className = 'csv-empty';
        more.textContent = `Showing the first ${listing.entries.length} entries. Open a subdirectory to narrow the list.`;
        list.appendChild(more);
      }
      if (state.logContent && state.logContent.relative_path === state.selectedLog) {
        const suffix = state.logContent.truncated ? ' (truncated)' : '';
        content.innerHTML = `<pre>${esc(state.logContent.content || '')}</pre>`;
        summary.textContent = `${state.logContent.relative_path}, ${formatBytes(state.logContent.size)}${suffix}`;
      } else {
        content.innerHTML = '<div class="csv-empty">Select a log file to load its content.</div>';
      }
    }
    async function loadLogs(dir = state.logsDir || '') {
      if (!state.selected) return;
      state.logsDir = dir || '';
      const query = new URLSearchParams({ dir: state.logsDir, limit: '500' });
      state.logsListing = await api(`/api/experiments/${encodeURIComponent(state.selected)}/logs?${query.toString()}`);
      state.logsFor = state.selected;
      renderLogsWorkspace();
    }
    async function loadLogFile(path) {
      if (!state.selected) return;
      state.selectedLog = path;
      const query = new URLSearchParams({ path });
      state.logContent = await api(`/api/experiments/${encodeURIComponent(state.selected)}/log?${query.toString()}`);
      renderLogsWorkspace();
    }
    async function ensureLogsLoaded() {
      if (!state.selected) return;
      if (state.logsFor !== state.selected || !state.logsListing) await loadLogs('');
    }
    async function ensureResultsLoaded() {
      if (!state.selected) return;
      if (state.resultsFor !== state.selected) await loadResults();
    }
    async function ensureStatsLoaded() {
      if (!state.selected) return;
      if (state.statsFor !== state.selected || !state.stats) await loadStats();
    }
    async function ensureInstallLogLoaded() {
      if (!state.selected) return;
      if (state.installLogFor !== state.selected) await loadInstallLog();
    }
    async function activateCsvView(viewId) {
      await ensureResultsLoaded();
      await ensureStatsLoaded();
      if (viewId === 'results-view') {
        renderResultsWorkspace();
        renderStatsWorkspace();
      }
    }
    function setView(viewId) {
      state.activeView = viewId;
      document.querySelectorAll('.view-tab').forEach(button => {
        button.classList.toggle('active', button.dataset.view === viewId);
      });
      document.querySelectorAll('.view-panel').forEach(panel => {
        panel.classList.toggle('active', panel.id === viewId);
      });
      if (viewId === 'results-view') {
        activateCsvView(viewId).catch(err => out(String(err)));
      }
      if (viewId === 'install-log-view') {
        ensureInstallLogLoaded().catch(err => out(String(err)));
      }
      if (viewId === 'logs-view') {
        ensureLogsLoaded().catch(err => out(String(err)));
      }
      if (viewId === 'plots-view') {
        loadPlotBackendStatus().catch(err => out(String(err)));
        loadPlotInfo().catch(err => out(String(err)));
        renderPlotPanel();
      }
    }
    function treeNode() {
      return { folders: new Map(), experiments: [], count: 0 };
    }
    function experimentTree(experiments) {
      const root = treeNode();
      const sorted = Array.from(experiments).sort((left, right) => left.id.localeCompare(right.id));
      for (const exp of sorted) {
        const parts = exp.id.split('/').filter(Boolean);
        if (!parts.length) continue;
        let node = root;
        node.count += 1;
        for (let index = 0; index < parts.length - 1; index += 1) {
          const part = parts[index];
          if (!node.folders.has(part)) node.folders.set(part, treeNode());
          node = node.folders.get(part);
          node.count += 1;
        }
        node.experiments.push(Object.assign({}, exp, { label: exp.name || parts[parts.length - 1] }));
      }
      return root;
    }
    function openExperimentAncestors(id) {
      const parts = id.split('/').filter(Boolean);
      let current = '';
      for (let index = 0; index < parts.length - 1; index += 1) {
        current = current ? `${current}/${parts[index]}` : parts[index];
        state.openDirs.add(current);
      }
    }
    function renderExperimentTree(container, node, prefix = '') {
      const folders = Array.from(node.folders.entries()).sort((left, right) => left[0].localeCompare(right[0]));
      for (const [name, child] of folders) {
        const id = prefix ? `${prefix}/${name}` : name;
        const details = document.createElement('details');
        details.className = 'experiment-folder';
        details.open = state.openDirs.has(id) || Boolean(state.selected && state.selected.startsWith(`${id}/`));
        details.addEventListener('toggle', () => {
          if (details.open) state.openDirs.add(id);
          else state.openDirs.delete(id);
        });
        const summary = document.createElement('summary');
        summary.className = 'folder-summary';
        const label = document.createElement('span');
        label.className = 'folder-name';
        label.textContent = name;
        const count = document.createElement('span');
        count.className = 'folder-count';
        count.textContent = `${child.count}`;
        summary.appendChild(label);
        summary.appendChild(count);
        const children = document.createElement('div');
        children.className = 'folder-children';
        renderExperimentTree(children, child, id);
        details.appendChild(summary);
        details.appendChild(children);
        container.appendChild(details);
      }
      const experiments = Array.from(node.experiments).sort((left, right) => left.label.localeCompare(right.label));
      for (const exp of experiments) {
        renderExperimentItem(container, exp, exp.label);
      }
    }
    function renderExperimentItem(container, exp, label) {
      const pinned = state.pinnedExperiments.has(exp.id);
      const item = document.createElement('div');
      item.className = 'experiment-item';
      const button = document.createElement('button');
      button.className = 'experiment-row' + (state.selected === exp.id ? ' active' : '');
      button.textContent = label;
      button.title = exp.id;
      button.onclick = () => selectExperiment(exp.id);
      const pin = document.createElement('button');
      pin.className = 'pin-button' + (pinned ? ' active' : '');
      pin.type = 'button';
      pin.textContent = pinned ? '★' : '☆';
      pin.title = pinned ? 'Unpin experiment' : 'Pin experiment';
      pin.setAttribute('aria-label', `${pinned ? 'Unpin' : 'Pin'} ${exp.id}`);
      pin.onclick = () => togglePinnedExperiment(exp.id);
      item.appendChild(button);
      item.appendChild(pin);
      container.appendChild(item);
    }
    function renderPinnedExperiments(container) {
      const order = Array.from(state.pinnedExperiments);
      const byId = new Map(state.experiments.map(exp => [exp.id, exp]));
      const pinned = order.map(id => byId.get(id)).filter(Boolean);
      if (!pinned.length) return;
      const section = document.createElement('section');
      section.className = 'pinned-experiments';
      const title = document.createElement('div');
      title.className = 'pinned-title';
      title.textContent = 'Pinned';
      section.appendChild(title);
      for (const exp of pinned) {
        renderExperimentItem(section, exp, exp.id);
      }
      container.appendChild(section);
    }
    function renderExperimentsList() {
      const list = document.getElementById('experiments');
      list.innerHTML = '';
      renderPinnedExperiments(list);
      const unpinned = state.experiments.filter(exp => !state.pinnedExperiments.has(exp.id));
      renderExperimentTree(list, experimentTree(unpinned));
    }
    async function togglePinnedExperiment(id) {
      if (state.pinnedExperiments.has(id)) state.pinnedExperiments.delete(id);
      else state.pinnedExperiments.add(id);
      renderExperimentsList();
      try {
        const result = await api('/api/pins', {
          method: 'PUT',
          body: JSON.stringify({ pinned: Array.from(state.pinnedExperiments) })
        });
        state.pinnedExperiments = new Set(result.pinned || []);
      } finally {
        renderExperimentsList();
      }
    }
    async function refreshExperiments(options = {}) {
      const query = options.force ? '?refresh=1' : '';
      const [data, pins] = await Promise.all([
        api(`/api/experiments${query}`),
        api('/api/pins')
      ]);
      clearTransientOutput();
      state.experiments = data.experiments;
      state.pinnedExperiments = new Set(pins.pinned || []);
      renderExperimentsList();
    }
    async function refreshPresets() {
      const data = await api('/api/presets');
      clearTransientOutput();
      state.presets = data.presets || [];
      const select = document.getElementById('new-preset');
      select.innerHTML = '';
      if (!state.presets.length) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = 'No presets found';
        option.disabled = true;
        select.appendChild(option);
        return;
      }
      for (const preset of state.presets) {
        const option = document.createElement('option');
        option.value = preset.name;
        option.textContent = preset.name;
        select.appendChild(option);
      }
    }
    function renderSubmitLock(lock) {
      state.submitLock = lock || { locked: false };
      const status = document.getElementById('submit-lock-status');
      const text = status.querySelector('.submit-lock-text');
      const clearButton = document.getElementById('clear-submit-lock');
      const submitButton = document.getElementById('submit');
      const locked = Boolean(state.submitLock.locked);
      status.classList.toggle('hidden', !locked);
      status.classList.toggle('locked', locked);
      clearButton.classList.toggle('hidden', !locked);
      submitButton.disabled = locked;
      if (locked) {
        const fields = state.submitLock.fields || {};
        const started = fields.started_at ? ` since ${fields.started_at}` : '';
        const algorithms = fields.algorithms ? ` (${fields.algorithms})` : '';
        text.textContent = `Submit locked${started}${algorithms}`;
        text.title = state.submitLock.content || state.submitLock.path || '';
      } else {
        text.textContent = '';
        text.title = '';
      }
    }
    async function refreshSubmitLock() {
      if (!state.selected) {
        renderSubmitLock({ locked: false });
        return;
      }
      const lock = await api(`/api/experiments/${encodeURIComponent(state.selected)}/submit-lock`);
      renderSubmitLock(lock);
    }
    async function clearSubmitLock() {
      if (!state.selected) return;
      const result = await api(`/api/experiments/${encodeURIComponent(state.selected)}/submit-lock`, { method: 'DELETE' });
      renderSubmitLock(result.submit_lock);
    }
    function startProgressPolling() {
      if (state.progressTimer) return;
      state.progressTimer = setInterval(() => {
        if (state.selected) loadProgress({ quiet: true }).catch(err => out(String(err)));
      }, 2000);
    }
    function stopProgressPolling() {
      if (!state.progressTimer) return;
      clearInterval(state.progressTimer);
      state.progressTimer = null;
    }
    function renderProgress(result) {
      const summary = document.getElementById('progress-summary');
      const box = document.getElementById('progress-output');
      const command = result?.progress || result;
      const progress = result?.progress_json || null;
      if (!state.selected) {
        stopProgressPolling();
        summary.textContent = 'No experiment selected.';
        box.className = 'csv-empty';
        box.textContent = 'Select an experiment first.';
        return;
      }
      if (!result) {
        stopProgressPolling();
        summary.textContent = 'No progress loaded.';
        box.className = 'csv-empty';
        box.textContent = 'Run progress to count finished log files against expected runs.';
        return;
      }
      if (progress) {
        const done = Number(progress.done || 0);
        const total = Number(progress.total || 0);
        const percent = Number(progress.percent || 0);
        summary.textContent = `${done} / ${total} finished (${percent}%). Refreshed in ${command?.elapsed_seconds ?? '?'}s.`;
        box.className = 'progress-output';
        box.innerHTML = '';
        for (const experiment of progress.experiments || []) {
          const card = document.createElement('section');
          card.className = 'progress-experiment';
          const header = document.createElement('div');
          header.className = 'progress-experiment-header';
          const name = document.createElement('div');
          name.className = 'progress-experiment-name';
          name.textContent = experiment.name || experiment.function || 'Experiment';
          const bar = document.createElement('div');
          bar.className = 'progress-bar';
          const fill = document.createElement('div');
          fill.className = 'progress-bar-fill';
          fill.style.width = `${Math.max(0, Math.min(100, Number(experiment.percent || 0)))}%`;
          bar.appendChild(fill);
          const count = document.createElement('div');
          count.className = 'progress-count';
          count.textContent = `${experiment.done || 0} / ${experiment.total || 0}`;
          header.appendChild(name);
          header.appendChild(bar);
          header.appendChild(count);
          card.appendChild(header);

          for (const algorithm of experiment.algorithms || []) {
            const row = document.createElement('div');
            row.className = 'progress-row';
            const rowName = document.createElement('div');
            rowName.className = 'progress-row-name';
            rowName.textContent = algorithm.name || '';
            const rowBar = document.createElement('div');
            rowBar.className = 'progress-bar';
            const rowFill = document.createElement('div');
            rowFill.className = 'progress-bar-fill';
            rowFill.style.width = `${Math.max(0, Math.min(100, Number(algorithm.percent || 0)))}%`;
            rowBar.appendChild(rowFill);
            const rowCount = document.createElement('div');
            rowCount.className = 'progress-count';
            rowCount.textContent = `${algorithm.done || 0} / ${algorithm.total || 0}`;
            row.appendChild(rowName);
            row.appendChild(rowBar);
            row.appendChild(rowCount);
            card.appendChild(row);
          }
          box.appendChild(card);
        }
        if (progress.complete) stopProgressPolling();
        else startProgressPolling();
        return;
      }
      stopProgressPolling();
      const text = stripAnsi(`${command?.stdout || ''}${command?.stderr ? `\n${command.stderr}` : ''}`).trim();
      summary.textContent = command?.returncode === 0
        ? `Progress refreshed in ${command.elapsed_seconds ?? '?'}s.`
        : `Progress failed with return code ${command?.returncode ?? 'unknown'}.`;
      box.className = text ? 'progress-output' : 'csv-empty';
      box.textContent = text || 'No progress output.';
    }
    async function loadProgress(options = {}) {
      if (!state.selected) return;
      const summary = document.getElementById('progress-summary');
      if (!options.quiet) summary.textContent = 'Refreshing progress...';
      const result = await api(`/api/experiments/${encodeURIComponent(state.selected)}/progress`);
      renderSubmitLock(result.submit_lock);
      renderProgress(result);
    }
    async function selectExperiment(id) {
      state.selected = id;
      state.results = [];
      state.resultsFor = null;
      state.stats = null;
      state.statsFor = null;
      state.selectedResults = [];
      state.compareColumnModes = {};
      state.installLog = null;
      state.installLogFor = null;
      state.logsDir = '';
      state.logsListing = null;
      state.logsFor = null;
      state.selectedLog = '';
      state.logContent = null;
      state.submitLock = null;
      state.plotInfo = null;
      state.plotInfoFor = null;
      clearPlotPdfUrl();
      setView('experiment-view');
      renderResultsWorkspace();
      renderStatsWorkspace();
      renderInstallLogWorkspace();
      renderLogsWorkspace();
      renderSubmitLock({ locked: false });
      renderProgress(null);
      document.getElementById('probe-summary').textContent = 'No probe loaded.';
      document.getElementById('probe-output').innerHTML = '<div class="probe-placeholder">Run Probe to inspect enabled algorithms, branch settings, CLI arguments, and resolved properties.</div>';
      openExperimentAncestors(id);
      renderExperimentsList();
      const data = await api(`/api/experiments/${encodeURIComponent(id)}/experiment`);
      clearTransientOutput();
      document.getElementById('selected-title').textContent = id;
      document.getElementById('selected-path').textContent = data.path;
      setEditorValue(data.experiment);
      renderSubmitLock(data.submit_lock);
      await loadAlgorithms();
    }
    async function persistExperiment() {
      if (!state.selected) return;
      const experiment = document.getElementById('experiment-editor').value;
      return await api(`/api/experiments/${encodeURIComponent(state.selected)}/experiment`, {
        method: 'PUT',
        body: JSON.stringify({ experiment })
      });
    }
    async function createExperiment() {
      const name = document.getElementById('new-name').value || 'experiment';
      const preset = document.getElementById('new-preset').value;
      const data = await api('/api/experiments', {
        method: 'POST',
        body: JSON.stringify({ name, preset })
      });
      await refreshExperiments();
      await selectExperiment(data.id);
    }
    async function checkExperiment() {
      if (!state.selected) return;
      const button = document.getElementById('check');
      button.disabled = true;
      out('Saving and checking...');
      try {
        const saved = await persistExperiment();
        const result = await api(`/api/experiments/${encodeURIComponent(state.selected)}/check`, { method: 'POST' });
        renderCheckResult(result, saved);
        try {
          await loadAlgorithms();
        } catch (err) {
          out(`Algorithm refresh failed after check: ${String(err)}`);
        }
      } finally {
        button.disabled = false;
      }
    }
    async function probeExperiment() {
      if (!state.selected) return;
      setActionButtons(['probe-run'], true);
      document.getElementById('probe-summary').textContent = 'Running mkexp2 probe...';
      document.getElementById('probe-output').innerHTML = '<div class="probe-placeholder">Running mkexp2 probe...</div>';
      try {
        const listing = await api(`/api/experiments/${encodeURIComponent(state.selected)}/probe`, {
          method: 'POST',
          body: JSON.stringify({})
        });
        const results = [];
        for (const item of listing.experiments || []) {
          const detail = await api(`/api/experiments/${encodeURIComponent(state.selected)}/probe`, {
            method: 'POST',
            body: JSON.stringify({ selector: item.name })
          });
          results.push(detail);
        }
        renderProbeResult(results, null);
        await loadAlgorithms();
      } finally {
        setActionButtons(['probe-run'], false);
      }
    }
    async function loadAlgorithms() {
      state.algorithms = [];
      const list = document.getElementById('algorithm-list');
      list.innerHTML = '';
      if (!state.selected) return;
      const probe = await api(`/api/experiments/${encodeURIComponent(state.selected)}/probe`, {
        method: 'POST',
        body: JSON.stringify({})
      });
      const experiments = probe.experiments || [];
      const names = new Set();
      for (const item of experiments) {
        const details = await api(`/api/experiments/${encodeURIComponent(state.selected)}/probe`, {
          method: 'POST',
          body: JSON.stringify({ selector: item.name, flags: ['--algorithms'] })
        });
        for (const alg of (details.resolved?.algorithms || [])) names.add(alg.name);
      }
      state.algorithms = Array.from(names).sort();
      for (const name of state.algorithms) {
        const label = document.createElement('label');
        label.className = 'chip';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = true;
        checkbox.value = name;
        const text = document.createElement('span');
        text.className = 'chip-label';
        text.textContent = name;
        text.title = name;
        label.appendChild(checkbox);
        label.appendChild(text);
        list.appendChild(label);
      }
    }
    async function submitExperiment(force = false) {
      if (!state.selected) return;
      if (state.submitLock?.locked) {
        out('Submit is locked for this experiment. Clear the lock only if the previous run is gone.');
        return;
      }
      const selectedAlgorithms = Array.from(document.querySelectorAll('#algorithm-list input:checked')).map(item => item.value);
      if (state.algorithms.length && selectedAlgorithms.length === 0) {
        out('Select at least one algorithm.');
        return;
      }
      const algorithms = selectedAlgorithms.length === state.algorithms.length ? [] : selectedAlgorithms;
      const action = await api(`/api/experiments/${encodeURIComponent(state.selected)}/submit`, {
        method: 'POST',
        body: JSON.stringify({ algorithms, force })
      });
      const completed = await watchAction(action.id);
      if (!force && completed?.status === 'completed' && completed.result?.blocked === 'check failed') {
        if (confirm('mkexp2 check failed. Submit anyway?')) {
          await submitExperiment(true);
        }
      }
      await refreshSubmitLock();
      await loadProgress({ quiet: true }).catch(() => {});
    }
    function setActionButtons(ids, disabled) {
      for (const id of ids) {
        const button = document.getElementById(id);
        if (button) button.disabled = disabled;
      }
    }
    async function parseExperiment() {
      if (!state.selected) return;
      setActionButtons(['parse-results'], true);
      try {
        const action = await api(`/api/experiments/${encodeURIComponent(state.selected)}/parse`, {
          method: 'POST',
          body: JSON.stringify({})
        });
        const completed = await watchAction(action.id);
        if (completed?.status === 'completed' && completed.result?.parsed) {
          await loadResults();
          await loadStats();
        }
      } finally {
        setActionButtons(['parse-results'], false);
      }
    }
    function renderPlotPanel(action = null) {
      const summary = document.getElementById('plots-summary');
      const file = document.getElementById('plot-file');
      if (!summary || !file) return;
      applyPlotBackendStatus();
      if (!state.selected) {
        summary.textContent = 'No experiment selected.';
        file.className = 'csv-empty';
        file.textContent = 'Select an experiment first.';
        return;
      }
      if (action) {
        renderActionStatus('plot-action-output', 'Plot generation', action, 'plot');
      }
      summary.textContent = action?.status === 'running'
        ? 'Plot generation is running.'
        : action?.status === 'completed'
          ? (actionSucceeded(action, 'plot') ? 'Plot generation completed.' : 'Plot generation failed.')
          : (state.plotInfoFor === state.selected && state.plotInfo?.exists
              ? `plots.pdf, ${formatBytes(state.plotInfo.size || 0)}, modified ${state.plotInfo.modified_at || 'unknown time'}`
              : 'Generate plots for the selected experiment.');
      const pdfUrl = `/api/experiments/${encodeURIComponent(state.selected)}/plots.pdf`;
      if (state.plotInfoFor === state.selected && state.plotInfo?.exists) {
        const version = encodeURIComponent(`${state.plotInfo.modified_at || ''}-${state.plotInfo.size || ''}`);
        if (state.plotPdfUrlFor === state.selected && state.plotPdfVersion === version && state.plotPdfUrl) {
          file.className = 'plot-preview';
          file.innerHTML = `
            <iframe class="plot-pdf" src="${esc(state.plotPdfUrl)}" title="plots.pdf"></iframe>
            <div class="csv-summary"><a href="${esc(state.plotPdfUrl)}" target="_blank" rel="noreferrer">Open plots.pdf</a></div>
          `;
        } else {
          file.className = 'csv-empty';
          file.textContent = 'Loading plots.pdf...';
          loadPlotPdf(pdfUrl, version).catch(err => {
            file.className = 'csv-empty status-bad';
            file.textContent = `Could not load plots.pdf: ${err.message || err}`;
          });
        }
      } else {
        file.className = 'csv-empty';
        file.textContent = state.plotInfoFor === state.selected
          ? 'plots.pdf does not exist yet.'
          : 'Checking for plots.pdf...';
      }
    }
    function applyPlotBackendStatus() {
      const checkbox = document.getElementById('plot-no-docker');
      const label = document.getElementById('plot-no-docker-label');
      if (!checkbox || !label) return;
      const backend = state.plotBackend;
      if (!backend) {
        checkbox.disabled = false;
        label.classList.remove('disabled');
        label.title = 'Use host R instead of Docker';
        return;
      }
      if (!backend.docker_available) {
        checkbox.checked = true;
        checkbox.disabled = true;
        label.classList.add('disabled');
        label.title = backend.native_r_available
          ? 'Docker is not available; native R will be used.'
          : 'Docker is not available, and Rscript was not found.';
        return;
      }
      checkbox.disabled = false;
      label.classList.remove('disabled');
      if (!state.plotNoDockerTouched) checkbox.checked = false;
      label.title = 'Use host R instead of Docker';
    }
    async function loadPlotBackendStatus() {
      state.plotBackend = await api('/api/plot/backend');
      applyPlotBackendStatus();
      return state.plotBackend;
    }
    async function loadPlotInfo() {
      if (!state.selected) return null;
      state.plotInfo = await api(`/api/experiments/${encodeURIComponent(state.selected)}/plots`);
      state.plotInfoFor = state.selected;
      renderPlotPanel();
      return state.plotInfo;
    }
    async function loadPlotPdf(pdfUrl, version) {
      if (!state.selected) return null;
      const selected = state.selected;
      const blob = await fetchBlob(`${pdfUrl}?v=${version}`);
      if (state.selected !== selected) return null;
      clearPlotPdfUrl();
      state.plotPdfUrl = URL.createObjectURL(blob);
      state.plotPdfUrlFor = selected;
      state.plotPdfVersion = version;
      renderPlotPanel();
      return state.plotPdfUrl;
    }
    async function plotExperiment() {
      if (!state.selected) return;
      setView('plots-view');
      applyPlotBackendStatus();
      setActionButtons(['plot-results'], true);
      try {
        const noDocker = document.getElementById('plot-no-docker')?.checked || false;
        const action = await api(`/api/experiments/${encodeURIComponent(state.selected)}/plot`, {
          method: 'POST',
          body: JSON.stringify({ no_docker: noDocker })
        });
        renderPlotPanel({ status: 'running', id: action.id });
        const completed = await watchAction(action.id, current => renderPlotPanel(current));
        if (completed?.status === 'completed' && completed.result?.plotted) {
          await loadPlotInfo();
        }
      } finally {
        setActionButtons(['plot-results'], false);
      }
    }
    async function watchAction(id, onUpdate = null) {
      let action = null;
      for (;;) {
        action = await api(`/api/actions/${encodeURIComponent(id)}`);
        if (onUpdate) onUpdate(action);
        else out(action);
        if (action.status !== 'running') break;
        await new Promise(resolve => setTimeout(resolve, 1200));
      }
      return action;
    }
    async function loadResults() {
      if (!state.selected) return;
      const data = await api(`/api/experiments/${encodeURIComponent(state.selected)}/results`);
      clearTransientOutput();
      state.results = (data.files || []).map(prepareCsvFile);
      state.resultsFor = state.selected;
      state.selectedResults = state.results[0] ? [state.results[0].name] : [];
      state.compareColumnModes = {};
      renderResultsWorkspace();
    }
    async function loadStats() {
      if (!state.selected) return;
      const data = await api(`/api/experiments/${encodeURIComponent(state.selected)}/stats`);
      clearTransientOutput();
      state.stats = data;
      state.statsFor = state.selected;
      renderStatsWorkspace();
    }
    async function loadInstallLog() {
      if (!state.selected) return;
      const data = await api(`/api/experiments/${encodeURIComponent(state.selected)}/install-log`);
      clearTransientOutput();
      state.installLog = data;
      state.installLogFor = state.selected;
      renderInstallLogWorkspace();
    }
    function cpuCount(value) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : NaN;
    }
    function formatCpuCount(value) {
      const parsed = cpuCount(value);
      if (!Number.isFinite(parsed)) return 'n/a';
      const cores = parsed / 2;
      const text = Number.isInteger(cores) ? String(cores) : cores.toFixed(1);
      return `${text} cores`;
    }
    function nodeStateClass(state) {
      const raw = String(state ?? '').trim().toLowerCase();
      const normalized = raw.replace(/[+*$]+$/g, '');
      if (normalized.startsWith('down')) return 'node-state-down';
      if (raw.startsWith('idle~')) return 'node-state-idle-reserved';
      if (normalized === 'idle') return 'node-state-idle';
      if (normalized.startsWith('allocated') || normalized.includes('alloc')) return 'node-state-allocated';
      return '';
    }
    async function refreshStatus() {
      const data = await api('/api/status/slurm');
      clearTransientOutput();
      const box = document.getElementById('slurm-status');
      if (!data.nodes.length) {
        box.className = 'node-list muted';
        box.textContent = 'No Slurm nodes found.';
        return;
      }
      box.className = 'node-list';
      const nodes = Array.from(data.nodes).sort((left, right) => {
        const rightCpus = cpuCount(right.cpus || right.cpu_info);
        const leftCpus = cpuCount(left.cpus || left.cpu_info);
        return (Number.isFinite(rightCpus) ? rightCpus : -1) - (Number.isFinite(leftCpus) ? leftCpus : -1)
          || String(left.name ?? '').localeCompare(String(right.name ?? ''));
      });
      const rows = nodes.map(node => {
        const state = node.state || node.availability || '';
        const stateClass = nodeStateClass(state);
        return `<div class="node-row ${esc(stateClass)}" title="${esc(state)}"><span class="node-name">${esc(node.name)}</span><span class="node-spec">${esc(formatCpuCount(node.cpus || node.cpu_info))}</span></div>`;
      }).join('');
      box.innerHTML = rows;
    }
    document.getElementById('refresh').onclick = () => {
      refreshPresets().catch(err => out(String(err)));
      refreshExperiments({ force: true }).catch(err => out(String(err)));
    };
    document.getElementById('refresh-status').onclick = refreshStatus;
    document.getElementById('git-open').onclick = openGitDialog;
    document.getElementById('git-close').onclick = closeGitDialog;
    document.getElementById('git-refresh').onclick = () => loadGitStatus().catch(err => out(String(err)));
    document.getElementById('git-push').onclick = pushGitChanges;
    document.getElementById('console-open').onclick = openConsoleDialog;
    document.getElementById('console-close').onclick = closeConsoleDialog;
    document.getElementById('console-clear').onclick = clearConsoleLog;
    document.getElementById('create').onclick = createExperiment;
    document.getElementById('check').onclick = checkExperiment;
    document.getElementById('probe-run').onclick = probeExperiment;
    document.getElementById('submit').onclick = submitExperiment;
    document.getElementById('clear-submit-lock').onclick = clearSubmitLock;
    document.getElementById('refresh-progress').onclick = () => loadProgress();
    document.getElementById('parse-results').onclick = parseExperiment;
    document.getElementById('plot-results').onclick = plotExperiment;
    document.getElementById('plot-no-docker').onchange = () => {
      state.plotNoDockerTouched = true;
    };
    document.getElementById('load-results').onclick = loadResults;
    document.getElementById('load-stats').onclick = loadStats;
    document.getElementById('load-install-log').onclick = loadInstallLog;
    document.getElementById('reload-logs').onclick = () => loadLogs(state.logsDir || '');
    document.querySelectorAll('.view-tab').forEach(button => {
      button.onclick = () => setView(button.dataset.view);
    });
    if (token() || allowEmptyToken) {
      refreshPresets().catch(err => out(String(err)));
      refreshExperiments().catch(err => out(String(err)));
      refreshStatus().catch(err => out(String(err)));
    } else {
      out('Enter the session token printed by mkexp2 web.');
    }
  </script>
</body>
</html>
"""


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "mkexp2-web/1"

        def log_message(self, fmt, *args):
            print("[%s] %s" % (self.log_date_time_string(), fmt % args))

        def require_token(self):
            supplied = self.headers.get("X-MKEXP2-Token", "")
            if app.allow_empty_token and supplied == "":
                return True
            return secrets.compare_digest(supplied, app.token)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            try:
                if path == "/":
                    html = HTML.replace("__ALLOW_EMPTY_TOKEN__", "true" if app.allow_empty_token else "false")
                    text_response(self, 200, html, "text/html; charset=utf-8")
                    return
                if path.startswith("/api/") and not self.require_token():
                    json_response(self, 401, {"error": "missing or invalid token"})
                    return
                if path == "/api/status/slurm":
                    json_response(self, 200, app.slurm.get())
                    return
                if path == "/api/presets":
                    json_response(self, 200, {"presets": app.list_presets()})
                    return
                if path == "/api/git/status":
                    json_response(self, 200, app.git_status())
                    return
                if path == "/api/pins":
                    json_response(self, 200, app.read_pins())
                    return
                if path == "/api/plot/backend":
                    json_response(self, 200, app.plot_backend_status())
                    return
                if path == "/api/experiments":
                    query = urllib.parse.parse_qs(parsed.query)
                    force = (query.get("refresh") or ["0"])[0] in ("1", "true", "yes")
                    json_response(self, 200, {"experiments": app.list_experiments(force=force)})
                    return
                match = re.match(r"^/api/experiments/([^/]+)/experiment$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    exp_path = app.experiment_path(experiment_id)
                    json_response(
                        self,
                        200,
                        {
                            "id": experiment_id,
                            "path": str(exp_path),
                            "experiment": (exp_path / "Experiment").read_text(encoding="utf-8"),
                            "submit_lock": app.submit_lock(experiment_id),
                        },
                    )
                    return
                match = re.match(r"^/api/experiments/([^/]+)/results$", path)
                if match:
                    json_response(self, 200, app.results(urllib.parse.unquote(match.group(1))))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/(submit-lock|progress)$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    action = match.group(2)
                    if action == "submit-lock":
                        json_response(self, 200, app.submit_lock(experiment_id))
                    else:
                        json_response(self, 200, app.progress(experiment_id))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/plots$", path)
                if match:
                    json_response(self, 200, app.plots_info(urllib.parse.unquote(match.group(1))))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/stats$", path)
                if match:
                    json_response(self, 200, app.stats(urllib.parse.unquote(match.group(1))))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/install-log$", path)
                if match:
                    json_response(self, 200, app.install_log(urllib.parse.unquote(match.group(1))))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/(logs|log)$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    action = match.group(2)
                    query = urllib.parse.parse_qs(parsed.query)
                    if action == "logs":
                        limit = int((query.get("limit") or [MAX_LOG_LIST_ENTRIES])[0])
                        offset = int((query.get("offset") or [0])[0])
                        rel_dir = (query.get("dir") or [""])[0]
                        json_response(self, 200, app.list_logs(experiment_id, rel_dir, limit=limit, offset=offset))
                    else:
                        rel_path = (query.get("path") or [""])[0]
                        json_response(self, 200, app.log_file(experiment_id, rel_path))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/plots\.pdf$", path)
                if match:
                    exp_path = app.experiment_path(urllib.parse.unquote(match.group(1)))
                    pdf = exp_path / "plots.pdf"
                    if not pdf.is_file():
                        json_response(self, 404, {"error": "plots.pdf not found"})
                        return
                    data = pdf.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.types_map.get(".pdf", "application/pdf"))
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                match = re.match(r"^/api/actions/([^/]+)$", path)
                if match:
                    action = app.actions.get(urllib.parse.unquote(match.group(1)))
                    if not action:
                        json_response(self, 404, {"error": "action not found"})
                    else:
                        json_response(self, 200, action)
                    return
                json_response(self, 404, {"error": "not found"})
            except Exception as exc:
                json_response(self, 400, {"error": str(exc)})

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            try:
                if path.startswith("/api/") and not self.require_token():
                    json_response(self, 401, {"error": "missing or invalid token"})
                    return
                payload = read_json(self)
                if path == "/api/experiments":
                    json_response(self, 201, app.create_experiment(payload))
                    return
                if path == "/api/git/push":
                    json_response(self, 200, app.git_commit_push(payload.get("message", "")))
                    return
                if path == "/api/pins":
                    json_response(self, 200, app.write_pins(payload.get("pinned") or []))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/(check|probe|submit|parse|plot)$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    action = match.group(2)
                    if action == "check":
                        json_response(self, 200, app.command(experiment_id, ["check", "--json"], timeout=60))
                        return
                    if action == "probe":
                        selector = payload.get("selector")
                        flags = payload.get("flags") or []
                        allowed_flags = {
                            "--algorithms",
                            "--graphs",
                            "--topologies",
                            "--run-properties",
                            "--jobs",
                            "--calls",
                        }
                        argv = ["probe"]
                        if selector:
                            argv.append(str(selector))
                        for flag in flags:
                            if flag not in allowed_flags:
                                raise ValueError(f"unsupported probe flag: {flag}")
                            argv.append(flag)
                        result = app.command(experiment_id, argv, timeout=60)
                        if result["returncode"] == 0 and result["stdout"].strip():
                            try:
                                payload = json.loads(result["stdout"])
                            except json.JSONDecodeError:
                                payload = {"raw": result["stdout"]}
                            payload["_command"] = result
                            json_response(self, 200, payload)
                        else:
                            json_response(self, 200, result)
                        return
                    if action == "submit":
                        json_response(self, 202, app.submit_action(experiment_id, payload))
                        return
                    if action == "parse":
                        json_response(self, 202, app.parse_action(experiment_id))
                        return
                    if action == "plot":
                        json_response(self, 202, app.plot_action(experiment_id, payload))
                        return
                json_response(self, 404, {"error": "not found"})
            except Exception as exc:
                json_response(self, 400, {"error": str(exc)})

        def do_PUT(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            try:
                if path.startswith("/api/") and not self.require_token():
                    json_response(self, 401, {"error": "missing or invalid token"})
                    return
                payload = read_json(self)
                if path == "/api/pins":
                    json_response(self, 200, app.write_pins(payload.get("pinned") or []))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/experiment$", path)
                if not match:
                    json_response(self, 404, {"error": "not found"})
                    return
                experiment_id = urllib.parse.unquote(match.group(1))
                exp_path = app.experiment_path(experiment_id)
                (exp_path / "Experiment").write_text(payload.get("experiment", ""), encoding="utf-8")
                json_response(self, 200, {"saved": True, "id": experiment_id})
            except Exception as exc:
                json_response(self, 400, {"error": str(exc)})

        def do_DELETE(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            try:
                if path.startswith("/api/") and not self.require_token():
                    json_response(self, 401, {"error": "missing or invalid token"})
                    return
                match = re.match(r"^/api/experiments/([^/]+)/submit-lock$", path)
                if not match:
                    json_response(self, 404, {"error": "not found"})
                    return
                experiment_id = urllib.parse.unquote(match.group(1))
                json_response(self, 200, app.clear_submit_lock(experiment_id))
            except Exception as exc:
                json_response(self, 400, {"error": str(exc)})

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mkexp2", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--name-template", default="%Y.%m.%d-<name>")
    parser.add_argument("--allow-empty-token", action="store_true")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        raise SystemExit(f"repo does not exist: {repo}")
    if shutil.which("git") is None:
        raise SystemExit("git not found")
    git_probe = run_command(["git", "rev-parse", "--show-toplevel"], cwd=repo, timeout=10)
    if git_probe["returncode"] != 0:
        raise SystemExit(f"repo is not a Git repository: {repo}")

    token = secrets.token_urlsafe(24)
    app = Mkexp2WebApp(repo, args.mkexp2, args.name_template, token, allow_empty_token=args.allow_empty_token)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    print(f"mkexp2 web: http://{args.host}:{args.port}", flush=True)
    print(f"session token: {token}", flush=True)
    if args.allow_empty_token:
        print("empty token bypass: enabled", flush=True)
    print(f"ssh tunnel: ssh -L {args.port}:{args.host}:{args.port} <user>@<cluster-login>", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
