#!/usr/bin/env python3
import argparse
import datetime as _dt
import getpass
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


MAX_TEXT_RESPONSE = 1024 * 1024
MAX_LOG_LIST_ENTRIES = 500
SLURM_CACHE_SECONDS = 15
EXPERIMENT_CACHE_SECONDS = 60
PLOT_ACTION_TIMEOUT_SECONDS = 7200
WEB_STATE_DIR = ".mkexp2"
WEB_PINS_FILE = "web-pins.json"
WEB_SHARES_FILE = "web-shares.json"
PLOT_INDEX_FILE = "index.json"
ARCHIVE_SUFFIX = ".archived"
EXPERIMENT_SKIP_DIRS = {".git", ".mkexp2", "jobs", "logs", "plots", "results", "slurm"}
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
SQUEUE_FALLBACK = """             JOBID PARTITION     NAME     USER ST       TIME  NODES NODELIST(REASON)
             67633       all submit-l seemaier PD       0:00      1 (Dependency)
             67632    diffie Experime seemaier  R       4:13      1 diffie
             67630    liskov run_cost laupichl  R    1:45:10      1 liskov
"""


def slugify(value):
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-._")
    return value or "experiment"


def download_filename(value):
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "experiment")).strip("-._")
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


def stat_creation_time(stat):
    return getattr(stat, "st_birthtime", None) or stat.st_ctime


def iso_from_timestamp(timestamp):
    return _dt.datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")


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


def parse_squeue_table(text):
    rows = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("JOBID"):
            continue
        parts = line.split(None, 7)
        if len(parts) < 8:
            continue
        job_id, partition, name, user, state, elapsed, nodes, nodelist = parts
        rows.append(
            {
                "job_id": job_id,
                "partition": partition,
                "name": name,
                "user": user,
                "state": state,
                "time": elapsed,
                "nodes": nodes,
                "nodelist": nodelist,
            }
        )
    return rows


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

    def queue(self):
        command = run_command(["squeue"], timeout=8)
        source = "squeue"
        raw = command["stdout"]
        if command["returncode"] == 127:
            source = "fallback sample: squeue not installed"
            raw = SQUEUE_FALLBACK
        return {
            "ok": command["returncode"] == 0 or command["returncode"] == 127,
            "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "source": source,
            "raw": raw,
            "rows": parse_squeue_table(raw),
            "server_user": getpass.getuser(),
            "command": command,
        }

    def cancel_job(self, payload):
        job_id = str((payload or {}).get("job_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", job_id):
            raise ValueError("invalid Slurm job id")

        owner = getpass.getuser()
        squeue = run_command(["squeue"], timeout=8)
        if squeue["returncode"] != 0:
            raise ValueError("cannot verify Slurm job ownership before scancel")
        rows = parse_squeue_table(squeue["stdout"])
        job = next((row for row in rows if row["job_id"] == job_id), None)
        if not job:
            raise ValueError(f"Slurm job not found: {job_id}")
        if job.get("user") != owner:
            raise ValueError(f"refusing to cancel job {job_id}: owner is {job.get('user')}, server user is {owner}")

        scancel = run_command(["scancel", job_id], timeout=30)
        self._cache_until = 0
        if scancel["returncode"] != 0:
            raise ValueError(scancel["stderr"] or scancel["stdout"] or f"scancel failed for job {job_id}")
        return {
            "ok": scancel["returncode"] == 0,
            "job": job,
            "server_user": owner,
            "verify": squeue,
            "scancel": scancel,
        }


class ActionStore:
    def __init__(self):
        self._actions = {}
        self._lock = threading.Lock()

    def _new_payload(self, label, key=None):
        action_id = secrets.token_urlsafe(10)
        return {
            "id": action_id,
            "key": key,
            "label": label,
            "status": "running",
            "started_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "result": None,
            "error": None,
        }

    def _run(self, payload, target):
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

    def start(self, label, target, key=None):
        payload = self._new_payload(label, key=key)
        with self._lock:
            self._actions[payload["id"]] = payload
        self._run(payload, target)
        return payload

    def start_unique(self, key, label, target):
        with self._lock:
            for payload in self._actions.values():
                if payload.get("key") == key and payload.get("status") == "running":
                    return payload
            payload = self._new_payload(label, key=key)
            self._actions[payload["id"]] = payload
        self._run(payload, target)
        return payload

    def get(self, action_id):
        with self._lock:
            return self._actions.get(action_id)


class Mkexp2WebApp:
    def __init__(self, repo, mkexp2, name_template, token, allow_empty_token=False, web_host="127.0.0.1", web_port=8765):
        self.repo = Path(repo).resolve()
        self.mkexp2 = Path(mkexp2).resolve()
        self.name_template = name_template
        self.token = token
        self.allow_empty_token = allow_empty_token
        self.web_host = web_host
        self.web_port = int(web_port)
        self.actions = ActionStore()
        self.slurm = SlurmStatus()
        self._plot_backend_cache = None
        self._plot_backend_cache_at = 0.0
        self._experiments_cache = None
        self._experiments_cache_at = 0.0
        self._archived_experiments_cache = None
        self._archived_experiments_cache_at = 0.0
        self._startup_spack_cache_action = None

    def mkexp2_root(self):
        return self.mkexp2.parent.parent

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

    def is_archived_experiment_id(self, experiment_id):
        return Path(str(experiment_id or "")).name.endswith(ARCHIVE_SUFFIX)

    def active_experiment_path(self, experiment_id):
        if self.is_archived_experiment_id(experiment_id):
            raise ValueError(f"experiment is archived: {experiment_id}")
        return self.experiment_path(experiment_id)

    def validate_visible_experiment_id(self, experiment_id):
        parts = str(experiment_id or "").split("/")
        if any(part in EXPERIMENT_SKIP_DIRS or part.startswith(".") for part in parts):
            raise ValueError("experiment id uses a reserved or hidden path component")
        if self.is_archived_experiment_id(experiment_id):
            raise ValueError(f"experiment name cannot end with {ARCHIVE_SUFFIX}")

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

    def _discover_experiments(self, archived=False):
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
            if not exp_file.is_file():
                continue
            path = exp_file.parent.resolve()
            file_stat = exp_file.stat()
            dir_stat = path.stat()
            created_at = stat_creation_time(dir_stat)
            is_archived = parts[-1].endswith(ARCHIVE_SUFFIX)
            if is_archived != archived:
                continue
            name = parts[-1]
            if is_archived:
                name = name[: -len(ARCHIVE_SUFFIX)]
            experiments.append(
                {
                    "id": rel_dir,
                    "name": name,
                    "parent": "/".join(parts[:-1]),
                    "depth": len(parts),
                    "path": str(path),
                    "created_at": iso_from_timestamp(created_at),
                    "created_at_epoch": created_at,
                    "modified_at": iso_from_timestamp(file_stat.st_mtime),
                    "has_results": (path / "results").is_dir(),
                    "has_plots_pdf": (path / "plots.pdf").is_file(),
                    "archived": is_archived,
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
        experiments = self._discover_experiments(archived=False)
        self._experiments_cache = experiments
        self._experiments_cache_at = now
        return experiments

    def list_archived_experiments(self, force=False):
        now = time.time()
        if (
            not force
            and self._archived_experiments_cache is not None
            and now - self._archived_experiments_cache_at < EXPERIMENT_CACHE_SECONDS
        ):
            return self._archived_experiments_cache
        experiments = self._discover_experiments(archived=True)
        self._archived_experiments_cache = experiments
        self._archived_experiments_cache_at = now
        return experiments

    def invalidate_experiments_cache(self):
        self._experiments_cache = None
        self._experiments_cache_at = 0.0
        self._archived_experiments_cache = None
        self._archived_experiments_cache_at = 0.0

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

    def shares_path(self):
        return self.repo / WEB_STATE_DIR / WEB_SHARES_FILE

    def read_shares(self):
        path = self.shares_path()
        if not path.is_file():
            return {"shares": [], "path": str(path)}
        try:
            payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid shares JSON: {exc}") from exc
        shares = payload.get("shares") or []
        if isinstance(shares, dict):
            shares = [dict(value, id=key) for key, value in shares.items()]
        if not isinstance(shares, list):
            raise ValueError("invalid shares JSON: shares is not an array")
        filtered = []
        seen = set()
        for item in shares:
            if not isinstance(item, dict):
                continue
            share_id = str(item.get("id") or "")
            experiment_id = str(item.get("experiment_id") or "")
            if not re.match(r"^[A-Za-z0-9_-]+$", share_id) or not experiment_id or share_id in seen:
                continue
            filtered.append(dict(item, id=share_id, experiment_id=experiment_id))
            seen.add(share_id)
        return {"shares": filtered, "path": str(path)}

    def write_shares(self, shares):
        path = self.shares_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps({"shares": shares}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        return {"shares": shares, "path": str(path), "saved": True}

    def share_public_url(self, share_id):
        host = self.web_host if self.web_host not in ("", "0.0.0.0", "::") else "127.0.0.1"
        if host in ("localhost", "::1"):
            host = "127.0.0.1"
        return f"http://{host}:{self.web_port}/share/{share_id}"

    def share_ssh_tunnel_command(self):
        remote_host = socket.gethostname() or os.environ.get("HOSTNAME") or "<cluster-login>"
        return f"ssh -L {self.web_port}:127.0.0.1:{self.web_port} <user>@{remote_host}"

    def share_experiment(self, experiment_id):
        path = self.active_experiment_path(experiment_id)
        known = {experiment["id"] for experiment in self.list_experiments(force=True)}
        if experiment_id not in known or not path.is_dir() or not (path / "Experiment").is_file():
            raise ValueError(f"experiment not found: {experiment_id}")
        shares = self.read_shares().get("shares") or []
        share_id = secrets.token_urlsafe(18)
        while any(item.get("id") == share_id for item in shares):
            share_id = secrets.token_urlsafe(18)
        share = {
            "id": share_id,
            "experiment_id": experiment_id,
            "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "created_by": getpass.getuser(),
        }
        shares.append(share)
        self.write_shares(shares)
        return {
            "share": share,
            "share_url": self.share_public_url(share_id),
            "ssh_tunnel": self.share_ssh_tunnel_command(),
        }

    def resolve_share(self, share_id):
        share_id = str(share_id or "")
        if not re.match(r"^[A-Za-z0-9_-]+$", share_id):
            raise ValueError("invalid share id")
        for share in self.read_shares().get("shares") or []:
            if share.get("id") != share_id:
                continue
            experiment_id = share.get("experiment_id")
            path = self.active_experiment_path(experiment_id)
            if not path.is_dir() or not (path / "Experiment").is_file():
                raise ValueError("shared experiment not found")
            return {"share": share, "experiment_id": experiment_id, "path": path}
        raise ValueError("share not found")

    def share_metadata(self, share_id):
        context = self.resolve_share(share_id)
        experiment_id = context["experiment_id"]
        path = context["path"]
        stat = (path / "Experiment").stat()
        return {
            "share": context["share"],
            "experiment": {
                "id": experiment_id,
                "name": Path(experiment_id).name,
                "path": str(path),
                "modified_at": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            },
            "read_only": True,
        }

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

    def config(self):
        return {
            "repo": str(self.repo),
            "name_template": self.name_template,
            "web_host": self.web_host,
            "web_port": self.web_port,
        }

    def create_experiment(self, payload):
        name = payload.get("name") or "experiment"
        template = payload.get("name_template") or self.name_template
        experiment_id = render_name_template(template, name)
        self.validate_visible_experiment_id(experiment_id)
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
        return run_command([str(self.mkexp2), *argv], cwd=self.active_experiment_path(experiment_id), timeout=timeout)

    def probe_payload(self, experiment_id, payload):
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
        result = self.command(experiment_id, argv, timeout=60)
        if result["returncode"] == 0 and result["stdout"].strip():
            try:
                parsed = json.loads(result["stdout"])
            except json.JSONDecodeError:
                parsed = {"raw": result["stdout"]}
            parsed["_command"] = result
            return parsed
        return result

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
        return self.active_experiment_path(experiment_id) / ".mkexp2" / "submit.lock"

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

    def require_submit_unlocked(self, experiment_id, action):
        if self.submit_lock(experiment_id).get("locked"):
            raise ValueError(f"cannot {action} an experiment while submit is locked")

    def delete_experiment(self, experiment_id):
        path = self.active_experiment_path(experiment_id)
        known = {experiment["id"] for experiment in self.list_experiments(force=True)}
        if experiment_id not in known:
            raise ValueError(f"experiment not found: {experiment_id}")
        if not path.is_dir() or not (path / "Experiment").is_file():
            raise ValueError(f"experiment not found: {experiment_id}")
        self.require_submit_unlocked(experiment_id, "delete")
        shutil.rmtree(path)
        self.invalidate_experiments_cache()
        pins = self.read_pins().get("pinned") or []
        if experiment_id in pins:
            self.write_pins([item for item in pins if item != experiment_id])
        return {"deleted": True, "id": experiment_id, "path": str(path)}

    def rename_experiment(self, experiment_id, payload):
        path = self.active_experiment_path(experiment_id)
        known = {experiment["id"] for experiment in self.list_experiments(force=True)}
        if experiment_id not in known:
            raise ValueError(f"experiment not found: {experiment_id}")
        if not path.is_dir() or not (path / "Experiment").is_file():
            raise ValueError(f"experiment not found: {experiment_id}")
        self.require_submit_unlocked(experiment_id, "rename")

        new_id = str(payload.get("new_id") or payload.get("id") or "").strip().strip("/")
        if not new_id:
            raise ValueError("new_id is required")
        self.validate_visible_experiment_id(new_id)
        target_path = self.experiment_path(new_id)
        if target_path == path:
            raise ValueError("new experiment id is unchanged")
        if path in target_path.parents:
            raise ValueError("rename target cannot be inside the experiment directory")
        if target_path.exists():
            raise ValueError(f"rename target already exists: {new_id}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target_path)
        self.invalidate_experiments_cache()
        pins = self.read_pins().get("pinned") or []
        if experiment_id in pins:
            self.write_pins([new_id if item == experiment_id else item for item in pins])
        return {
            "renamed": True,
            "id": experiment_id,
            "new_id": new_id,
            "path": str(path),
            "new_path": str(target_path),
        }

    def archive_experiment(self, experiment_id):
        path = self.experiment_path(experiment_id)
        if path.name.endswith(ARCHIVE_SUFFIX):
            raise ValueError(f"experiment is already archived: {experiment_id}")
        known = {experiment["id"] for experiment in self.list_experiments(force=True)}
        if experiment_id not in known:
            raise ValueError(f"experiment not found: {experiment_id}")
        if not path.is_dir() or not (path / "Experiment").is_file():
            raise ValueError(f"experiment not found: {experiment_id}")
        self.require_submit_unlocked(experiment_id, "archive")
        archived_path = path.with_name(path.name + ARCHIVE_SUFFIX)
        if archived_path.exists():
            raise ValueError(f"archive target already exists: {archived_path.relative_to(self.repo).as_posix()}")
        archived_id = archived_path.relative_to(self.repo).as_posix()
        path.rename(archived_path)
        self.invalidate_experiments_cache()
        pins = self.read_pins().get("pinned") or []
        if experiment_id in pins:
            self.write_pins([item for item in pins if item != experiment_id])
        return {
            "archived": True,
            "id": experiment_id,
            "archived_id": archived_id,
            "path": str(path),
            "archived_path": str(archived_path),
        }

    def unarchive_experiment(self, experiment_id):
        path = self.experiment_path(experiment_id)
        if not path.name.endswith(ARCHIVE_SUFFIX):
            raise ValueError(f"experiment is not archived: {experiment_id}")
        if not path.is_dir() or not (path / "Experiment").is_file():
            raise ValueError(f"archived experiment not found: {experiment_id}")
        target_name = path.name[: -len(ARCHIVE_SUFFIX)]
        if not target_name:
            raise ValueError("invalid archived experiment name")
        target_path = path.with_name(target_name)
        if target_path.exists():
            raise ValueError(f"unarchive target already exists: {target_path.relative_to(self.repo).as_posix()}")
        target_id = target_path.relative_to(self.repo).as_posix()
        path.rename(target_path)
        self.invalidate_experiments_cache()
        return {
            "unarchived": True,
            "id": experiment_id,
            "active_id": target_id,
            "path": str(path),
            "active_path": str(target_path),
        }

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
                cwd=self.active_experiment_path(experiment_id),
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
            plot = self.command(experiment_id, argv, timeout=PLOT_ACTION_TIMEOUT_SECONDS)
            return {"plotted": plot["returncode"] == 0, "plot": plot}

        return self.actions.start_unique(f"plot:{experiment_id}", f"plot {experiment_id}", action)

    def spack_plot_cache_path(self):
        return self.mkexp2_root() / "plots" / ".cache-native" / "spack-r-libs.txt"

    def spack_plot_cache_info(self):
        cache = self.spack_plot_cache_path()
        exists = cache.is_file() and cache.stat().st_size > 0
        entries = []
        modified_at = None
        size = 0
        if exists:
            stat = cache.stat()
            size = stat.st_size
            modified_at = _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
            entries = [item for item in cache.read_text(encoding="utf-8").strip().split(":") if item]
        return {
            "exists": exists,
            "path": str(cache),
            "size": size,
            "modified_at": modified_at,
            "entries": entries,
            "entry_count": len(entries),
            "startup_action": self._startup_spack_cache_action,
        }

    def resolve_spack_plot_cache_action(self, force=False, label=None):
        argv = ["plot", "--resolve-spack-r-libs"]
        if force:
            argv.append("--refresh-spack-r-libs")

        def action():
            resolve = run_command([str(self.mkexp2)] + argv, cwd=self.mkexp2_root(), timeout=180)
            return {
                "resolved": resolve["returncode"] == 0,
                "force": bool(force),
                "cache": self.spack_plot_cache_info(),
                "resolve": resolve,
            }

        key = "plot-spack-r-libs-refresh" if force else "plot-spack-r-libs"
        return self.actions.start_unique(key, label or "resolve Spack R library cache", action)

    def warm_spack_plot_cache(self):
        self._startup_spack_cache_action = self.resolve_spack_plot_cache_action(
            force=False,
            label="warm Spack R library cache",
        )["id"]
        return self._startup_spack_cache_action

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
        path = self.active_experiment_path(experiment_id)
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
        path = self.active_experiment_path(experiment_id)
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

    def description(self, experiment_id):
        path = self.active_experiment_path(experiment_id)
        description_file = path / "description.md"
        if not description_file.is_file():
            return {"exists": False, "path": str(description_file), "content": ""}
        stat = description_file.stat()
        content = description_file.read_text(encoding="utf-8", errors="replace")
        return {
            "exists": True,
            "path": str(description_file),
            "size": stat.st_size,
            "modified_at": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            "content": content[:MAX_TEXT_RESPONSE],
            "truncated": len(content) > MAX_TEXT_RESPONSE,
        }

    def write_description(self, experiment_id, content):
        path = self.active_experiment_path(experiment_id)
        description_file = path / "description.md"
        description_file.write_text(str(content or ""), encoding="utf-8")
        result = self.description(experiment_id)
        result["saved"] = True
        return result

    def experiment_archive(self, experiment_id):
        path = self.active_experiment_path(experiment_id)
        base = download_filename(path.name)
        tar_command = shutil.which("tar")
        zstd_command = shutil.which("zstd")
        if tar_command and zstd_command:
            archive = tempfile.NamedTemporaryFile(prefix=f"{base}-", suffix=".tar.zst", delete=False)
            archive.close()
            archive_path = Path(archive.name)
            result = run_command(
                [tar_command, "--zstd", "-cf", str(archive_path), "-C", str(path.parent), path.name],
                timeout=3600,
            )
            if result["returncode"] == 0 and archive_path.is_file() and archive_path.stat().st_size > 0:
                return {
                    "path": archive_path,
                    "filename": f"{base}.tar.zst",
                    "content_type": "application/zstd",
                    "format": "tar.zst",
                }
            archive_path.unlink(missing_ok=True)

        archive = tempfile.NamedTemporaryFile(prefix=f"{base}-", suffix=".zip", delete=False)
        archive.close()
        archive_path = Path(archive.name)
        try:
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zip_file:
                for root, dirs, files in os.walk(path):
                    dirs.sort()
                    files.sort()
                    root_path = Path(root)
                    for name in files:
                        file_path = root_path / name
                        rel_path = Path(path.name) / file_path.relative_to(path)
                        zip_file.write(file_path, rel_path.as_posix())
            return {
                "path": archive_path,
                "filename": f"{base}.zip",
                "content_type": "application/zip",
                "format": "zip",
            }
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise

    def plots_info(self, experiment_id):
        path = self.active_experiment_path(experiment_id)
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

    def plot_catalog(self):
        result = run_command([str(self.mkexp2), "plot", "--list", "--json"], cwd=self.mkexp2_root(), timeout=30)
        if result["returncode"] != 0:
            raise ValueError("could not load plot catalog")
        try:
            payload = json.loads(result["stdout"] or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid plot catalog JSON: {exc}") from exc
        payload["_command"] = result
        return payload

    def plot_catalog_map(self):
        return {item["id"]: item for item in self.plot_catalog().get("plots", [])}

    def plot_sources(self, experiment_id, include_all=False):
        def csv_entry(exp_id, csv_file):
            stat = csv_file.stat()
            return {
                "kind": "csv",
                "experiment_id": exp_id,
                "file": csv_file.name,
                "name": csv_file.stem,
                "alias": csv_file.stem if exp_id == experiment_id else f"{exp_id}/{csv_file.stem}",
                "size": stat.st_size,
                "modified_at": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            }

        current_path = self.active_experiment_path(experiment_id)
        current = []
        results_dir = current_path / "results"
        if results_dir.is_dir():
            for csv_file in sorted(results_dir.glob("*.csv")):
                item = csv_entry(experiment_id, csv_file)
                item["kind"] = "algorithm"
                current.append(item)

        payload = {"current": current}
        if include_all:
            experiments = []
            for exp in self.list_experiments():
                exp_results = self.active_experiment_path(exp["id"]) / "results"
                files = []
                if exp_results.is_dir():
                    files = [csv_entry(exp["id"], csv_file) for csv_file in sorted(exp_results.glob("*.csv"))]
                if files:
                    experiments.append({"id": exp["id"], "name": exp.get("name") or exp["id"], "files": files})
            payload["experiments"] = experiments
        return payload

    def plot_artifacts_dir(self, experiment_id):
        return self.active_experiment_path(experiment_id) / "plots"

    def plot_artifacts_index_path(self, experiment_id):
        return self.plot_artifacts_dir(experiment_id) / PLOT_INDEX_FILE

    def read_plot_artifacts_index(self, experiment_id):
        path = self.plot_artifacts_index_path(experiment_id)
        if not path.is_file():
            return {"version": 1, "artifacts": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {}
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, list):
            artifacts = []
        return {"version": 1, "artifacts": artifacts}

    def write_plot_artifacts_index(self, experiment_id, index):
        directory = self.plot_artifacts_dir(experiment_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = self.plot_artifacts_index_path(experiment_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)

    def list_plot_artifacts(self, experiment_id):
        index = self.read_plot_artifacts_index(experiment_id)
        directory = self.plot_artifacts_dir(experiment_id)
        artifacts = []
        for artifact in index.get("artifacts", []):
            rel_path = str(artifact.get("path") or "")
            pdf = (self.active_experiment_path(experiment_id) / rel_path).resolve()
            if not rel_path.startswith("plots/") or not pdf.is_file():
                continue
            stat = pdf.stat()
            item = dict(artifact)
            item.update(
                {
                    "exists": True,
                    "size": stat.st_size,
                    "modified_at": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                }
            )
            artifacts.append(item)
        artifacts.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return {"artifacts": artifacts, "legacy": self.plots_info(experiment_id), "index_path": str(directory / PLOT_INDEX_FILE)}

    def plot_artifact_pdf(self, experiment_id, artifact_id):
        if not re.match(r"^[A-Za-z0-9._-]+$", artifact_id or ""):
            raise ValueError("invalid plot artifact id")
        for artifact in self.list_plot_artifacts(experiment_id).get("artifacts", []):
            if artifact.get("id") == artifact_id:
                path = (self.active_experiment_path(experiment_id) / artifact.get("path", "")).resolve()
                root = self.active_experiment_path(experiment_id).resolve()
                if root not in path.parents:
                    raise ValueError("plot artifact path escapes experiment")
                return path
        raise ValueError("plot artifact not found")

    def resolve_plot_source(self, experiment_id, source):
        if isinstance(source, str):
            return {
                "token": source,
                "metadata": {"kind": "algorithm", "name": source, "alias": source, "experiment_id": experiment_id},
            }
        if not isinstance(source, dict):
            raise ValueError("plot sources must be strings or objects")

        kind = source.get("kind") or source.get("type") or "algorithm"
        alias = str(source.get("alias") or source.get("name") or "").strip()
        if kind == "algorithm":
            name = str(source.get("name") or source.get("file") or "").strip()
            if not name:
                raise ValueError("algorithm plot source requires name")
            return {
                "token": name,
                "metadata": {"kind": "algorithm", "name": name, "alias": alias or name, "experiment_id": experiment_id},
            }

        if kind != "csv":
            raise ValueError(f"unsupported plot source kind: {kind}")
        source_experiment = str(source.get("experiment_id") or experiment_id)
        file_name = str(source.get("file") or "").strip()
        if not file_name or Path(file_name).name != file_name or not file_name.endswith(".csv"):
            raise ValueError("CSV plot source requires a results/*.csv file name")
        exp_path = self.active_experiment_path(source_experiment)
        csv_path = (exp_path / "results" / file_name).resolve()
        results_root = (exp_path / "results").resolve()
        if results_root not in csv_path.parents or not csv_path.is_file():
            raise ValueError("CSV plot source must exist under an experiment results directory")
        alias = (alias or f"{source_experiment}/{Path(file_name).stem}").replace("=", "-")
        return {
            "token": f"{alias}={csv_path}",
            "metadata": {
                "kind": "csv",
                "experiment_id": source_experiment,
                "file": file_name,
                "alias": alias,
                "path": str(csv_path),
            },
        }

    def validate_plot_request(self, plot_ids, sources):
        catalog = self.plot_catalog_map()
        if not plot_ids:
            raise ValueError("at least one plot type must be selected")
        if not sources:
            raise ValueError("at least one plot source must be selected")
        for plot_id in plot_ids:
            if plot_id not in catalog:
                raise ValueError(f"unknown plot type: {plot_id}")
            entry = catalog[plot_id]
            count = len(sources)
            min_sources = int(entry.get("min_sources") or 0)
            max_sources = entry.get("max_sources")
            if count < min_sources:
                raise ValueError(f"{entry.get('name', plot_id)} requires at least {min_sources} source(s)")
            if max_sources is not None and count > int(max_sources):
                raise ValueError(f"{entry.get('name', plot_id)} accepts at most {max_sources} source(s)")
        return catalog

    def create_plot_artifacts_action(self, experiment_id, payload):
        plot_ids = [str(item) for item in (payload.get("plots") or [])]
        source_payloads = payload.get("sources") or []
        resolved_sources = [self.resolve_plot_source(experiment_id, item) for item in source_payloads]
        catalog = self.validate_plot_request(plot_ids, resolved_sources)
        label = str(payload.get("label") or "").strip()
        no_docker = bool(payload.get("no_docker"))
        threads = str(payload.get("threads") or "").strip()

        def action():
            created = []
            commands = []
            index = self.read_plot_artifacts_index(experiment_id)
            for plot_id in plot_ids:
                entry = catalog[plot_id]
                created_at = _dt.datetime.now().isoformat(timespec="seconds")
                label_text = label or f"{entry.get('name', plot_id)} - {', '.join(src['metadata'].get('alias') or src['metadata'].get('name') or '' for src in resolved_sources)}"
                if len(plot_ids) > 1 and label:
                    label_text = f"{label} - {entry.get('name', plot_id)}"
                artifact_id = "-".join(
                    [
                        _dt.datetime.now().strftime("%Y%m%d-%H%M%S"),
                        slugify(plot_id),
                        slugify(label_text)[:48],
                        secrets.token_urlsafe(4).replace("_", "-"),
                    ]
                )
                rel_output = f"plots/{artifact_id}.pdf"
                argv = ["plot"]
                if no_docker:
                    argv.append("--no-docker")
                argv.extend(["--plot", plot_id, "--output", rel_output])
                if threads:
                    argv.extend(["--threads", threads])
                argv.extend(source["token"] for source in resolved_sources)
                command = self.command(experiment_id, argv, timeout=PLOT_ACTION_TIMEOUT_SECONDS)
                commands.append({"plot_id": plot_id, "plot_name": entry.get("name", plot_id), "command": command})
                pdf = self.active_experiment_path(experiment_id) / rel_output
                if command["returncode"] == 0 and pdf.is_file() and pdf.stat().st_size > 0:
                    stat = pdf.stat()
                    artifact = {
                        "id": artifact_id,
                        "label": label_text,
                        "plot_id": plot_id,
                        "plot_name": entry.get("name", plot_id),
                        "description": entry.get("description", ""),
                        "sources": [source["metadata"] for source in resolved_sources],
                        "path": rel_output,
                        "created_at": created_at,
                        "size": stat.st_size,
                        "modified_at": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    }
                    index.setdefault("artifacts", []).append(artifact)
                    created.append(artifact)
            self.write_plot_artifacts_index(experiment_id, index)
            return {
                "plotted": len(created) == len(plot_ids),
                "created": created,
                "commands": commands,
                "artifacts": self.list_plot_artifacts(experiment_id),
            }

        return self.actions.start_unique(f"plot-artifacts:{experiment_id}", f"plot artifacts {experiment_id}", action)

    def create_shared_plot_artifacts_action(self, experiment_id, payload):
        current_sources = self.plot_sources(experiment_id, include_all=False).get("current") or []
        allowed_names = {source.get("name") for source in current_sources}
        allowed_files = {source.get("file") for source in current_sources}
        for source in payload.get("sources") or []:
            if isinstance(source, str):
                if source not in allowed_names:
                    raise ValueError("shared plot sources must come from the shared experiment")
                continue
            if not isinstance(source, dict):
                raise ValueError("invalid shared plot source")
            kind = source.get("kind") or source.get("type") or "algorithm"
            if kind == "algorithm":
                if source.get("name") not in allowed_names:
                    raise ValueError("shared plot sources must come from the shared experiment")
            elif kind == "csv":
                if source.get("experiment_id") != experiment_id or source.get("file") not in allowed_files:
                    raise ValueError("shared plot sources must come from the shared experiment")
            else:
                raise ValueError("invalid shared plot source")
        return self.create_plot_artifacts_action(experiment_id, payload)

    def logs_root(self, experiment_id):
        return self.active_experiment_path(experiment_id) / "logs"

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
      --sidebar-width: 320px;
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
    button:disabled {
      cursor: not-allowed;
      opacity: 0.58;
    }
    button.primary {
      background: var(--accent);
      color: white;
      border-color: var(--accent);
    }
    button.primary:disabled {
      background: #9fb8b4;
      border-color: #9fb8b4;
      color: #f8fbfb;
    }
    button.is-busy {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
    }
    button.is-busy::before {
      content: "";
      width: 13px;
      height: 13px;
      border: 2px solid currentColor;
      border-right-color: transparent;
      border-radius: 999px;
      animation: spin 0.75s linear infinite;
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
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
      grid-template-columns: var(--sidebar-width) 8px minmax(0, 1fr);
      min-height: 100vh;
    }
    .app.share-mode {
      grid-template-columns: minmax(0, 1fr);
    }
    .app.share-mode .sidebar,
    .app.share-mode .sidebar-resizer,
    .app.share-mode .submit-panel,
    .app.share-mode .danger-zone,
    .app.share-mode #check,
    .app.share-mode #share-experiment,
    .app.share-mode #add-plot-source {
      display: none !important;
    }
    .app.share-mode .main {
      grid-column: 1;
    }
    .app.share-mode .editor-shell {
      background: #fbfcfd;
    }
    .app.share-mode #experiment-editor {
      background: transparent;
    }
    .sidebar {
      border-right: 1px solid var(--border);
      background: #ffffff;
      padding: 18px;
      min-width: 0;
      overflow: auto;
    }
    .sidebar-resizer {
      width: 8px;
      cursor: col-resize;
      background: transparent;
      border-right: 1px solid var(--border);
      touch-action: none;
    }
    .sidebar-resizer:hover,
    .sidebar-resizer:focus-visible,
    .app.resizing .sidebar-resizer {
      background: #dce6ea;
      outline: none;
    }
    .app.resizing {
      cursor: col-resize;
      user-select: none;
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
    .archive-list {
      display: grid;
      gap: 8px;
      max-height: 60vh;
      overflow: auto;
    }
    .archive-item {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      min-width: 0;
      padding: 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #fbfcfd;
    }
    .archive-name {
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .archive-path {
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .share-fields {
      display: grid;
      gap: 12px;
    }
    .share-field {
      display: grid;
      gap: 6px;
    }
    .share-field-label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
      text-transform: uppercase;
    }
    .share-field textarea {
      min-height: 78px;
      resize: vertical;
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
    .sidebar-section-actions {
      display: flex;
      align-items: center;
      gap: 6px;
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
      grid-column: 3;
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
    .view-tabs-spacer {
      flex: 1 1 auto;
      min-width: 20px;
    }
    .view-tabs .icon-button {
      flex: 0 0 auto;
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
    .danger-zone {
      border-color: #f1b7b1;
      margin-top: 14px;
    }
    .danger-actions {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
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
    .description-body {
      display: grid;
      gap: 10px;
    }
    .description-editor {
      min-height: 160px;
      resize: vertical;
      font: 13px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .description-actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      flex-wrap: wrap;
    }
    .app.share-mode .description-edit-actions {
      display: none !important;
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
    .probe-input-grid {
      display: grid;
      grid-template-columns: minmax(260px, 1.5fr) repeat(3, minmax(120px, 0.7fr));
      gap: 8px;
      min-width: 0;
    }
    .probe-input-card {
      display: grid;
      align-content: start;
      gap: 6px;
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 10px;
    }
    .probe-input-title,
    .probe-settings-title {
      color: var(--muted);
      font-size: 11px;
      font-weight: 750;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .probe-input-values {
      min-width: 0;
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
    }
    .probe-input-values.graphs {
      display: block;
      max-height: 150px;
      overflow: auto;
      white-space: pre;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: white;
      padding: 7px 8px;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .probe-input-chip {
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: white;
      padding: 3px 7px;
      font: 12px/1.35 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      white-space: nowrap;
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
    .probe-settings {
      display: grid;
      gap: 8px;
    }
    .probe-setting-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
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
    @media (max-width: 1000px) {
      .probe-input-grid,
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
    .algorithm-loading {
      grid-column: 1 / -1;
      color: var(--muted);
      justify-content: flex-start;
    }
    .loading-spinner {
      width: 13px;
      height: 13px;
      border: 2px solid currentColor;
      border-right-color: transparent;
      border-radius: 999px;
      animation: spin 0.75s linear infinite;
      flex: 0 0 auto;
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
    .plot-manager {
      display: grid;
      gap: 14px;
    }
    .plot-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 12px;
      align-items: start;
    }
    .plot-box {
      display: grid;
      gap: 8px;
      min-width: 0;
      padding: 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #fbfcfd;
    }
    .plot-box-title {
      font-weight: 750;
    }
    .plot-choice,
    .plot-source-row,
    .plot-artifact-row {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      gap: 8px;
      align-items: start;
      min-width: 0;
      padding: 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: white;
    }
    .plot-choice.expensive {
      border-color: #fed7aa;
      background: #fff7ed;
    }
    .plot-choice-title,
    .plot-source-title,
    .plot-artifact-title {
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .plot-choice-desc,
    .plot-source-meta,
    .plot-artifact-meta {
      color: var(--muted);
      font-size: 12px;
    }
    .plot-source-actions {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .plot-source-alias {
      height: 30px;
      padding: 5px 8px;
      font-size: 12px;
    }
    .plot-artifact-list {
      display: grid;
      gap: 8px;
    }
    .plot-artifact-row {
      grid-template-columns: minmax(0, 1fr) auto;
      text-align: left;
      height: auto;
    }
    .plot-artifact-row.active {
      border-color: var(--accent);
      background: #e8f5f3;
    }
    .plot-preview {
      display: grid;
      gap: 8px;
      min-width: 0;
    }
    .plot-source-modal-list {
      display: grid;
      gap: 8px;
      max-height: 55vh;
      overflow: auto;
    }
    .plot-source-modal-list .experiment-folder {
      display: grid;
      gap: 6px;
    }
    .plot-source-modal-exp {
      display: grid;
      gap: 6px;
      padding: 8px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #fbfcfd;
    }
    .plot-source-modal-files {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
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
      gap: 10px;
    }
    .git-file-list {
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #fbfcfd;
      padding: 6px;
      display: grid;
      gap: 3px;
    }
    .git-file {
      display: grid;
      grid-template-columns: 18px minmax(0, 1fr);
      align-items: start;
      gap: 6px;
      min-width: 0;
      overflow-wrap: anywhere;
      border-radius: 5px;
      padding: 2px 5px;
      font: 12px/1.3 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }
    .git-file-kind {
      font: 11px/1.3 ui-sans-serif, system-ui, sans-serif;
      font-weight: 750;
      text-transform: uppercase;
      text-align: center;
    }
    .git-file-path {
      min-width: 0;
    }
    .git-file.added {
      background: #dcfce7;
      color: #14532d;
    }
    .git-file.modified {
      background: #dbeafe;
      color: #1e3a8a;
    }
    .git-file.deleted {
      background: #fee2e2;
      color: #7f1d1d;
    }
    .queue-table-wrap {
      overflow: auto;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #fbfcfd;
    }
    .queue-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
    }
    .queue-table th,
    .queue-table td {
      padding: 7px 8px;
      border-bottom: 1px solid var(--border);
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }
    .queue-table th {
      color: var(--muted);
      font-size: 11px;
      font-weight: 750;
      text-transform: uppercase;
      background: #f8fafb;
    }
    .queue-table tr:last-child td {
      border-bottom: 0;
    }
    .queue-state {
      font-weight: 750;
      font-variant-numeric: tabular-nums;
    }
    .queue-state-running { color: var(--ok); }
    .queue-state-pending { color: #9a3412; }
    .queue-state-other { color: var(--muted); }
    .queue-cancel {
      min-width: 26px;
      height: 26px;
      padding: 0 8px;
      color: var(--danger);
      border-color: #f1b7b1;
      font-size: 12px;
    }
    .git-message {
      display: grid;
      gap: 6px;
    }
    .git-message textarea {
      min-height: 82px;
    }
    .settings-modal {
      width: min(1040px, 100%);
    }
    .settings-token {
      display: grid;
      gap: 6px;
      margin-bottom: 12px;
    }
    .settings-token label {
      font-weight: 750;
      font-size: 12px;
    }
    .settings-tool {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px;
      margin-bottom: 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: #fbfcfd;
    }
    .settings-tool-title {
      font-weight: 750;
      font-size: 12px;
      margin-bottom: 3px;
    }
    .settings-tool button {
      flex: 0 0 auto;
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
    .create-form {
      display: grid;
      gap: 12px;
    }
    .create-field {
      display: grid;
      gap: 6px;
    }
    .create-field-label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
      text-transform: uppercase;
    }
    .template-name-row {
      display: grid;
      grid-template-columns: auto minmax(160px, 1fr) auto;
      align-items: center;
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: white;
      overflow: hidden;
    }
    .template-name-part {
      min-width: 0;
      padding: 0 10px;
      color: var(--muted);
      font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      overflow-wrap: anywhere;
    }
    .template-name-row input {
      border: 0;
      border-left: 1px solid var(--border);
      border-right: 1px solid var(--border);
      border-radius: 0;
    }
    .template-controls {
      display: grid;
      gap: 8px;
    }
    .checkbox-line {
      display: flex;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }
    .checkbox-line input {
      width: auto;
    }
    .hidden { display: none; }
    @media (max-width: 980px) {
      .app { grid-template-columns: 1fr; }
      .sidebar { border-right: 0; border-bottom: 1px solid var(--border); }
      .sidebar-resizer { display: none; }
      .main { grid-column: auto; }
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
          <button id="create-open" class="icon-button" aria-label="Create experiment" title="Create experiment">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14"/><path d="M5 12h14"/></svg>
          </button>
          <button id="archive-open" class="icon-button" aria-label="Archived experiments" title="Archived experiments">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 8v13H3V8"/><path d="M1 3h22v5H1z"/><path d="M10 12h4"/></svg>
          </button>
          <button id="git-open" class="icon-button" aria-label="Git status" title="Git status">
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><circle cx="6" cy="18" r="3"/><path d="M6 9v6"/><path d="M8.5 7.5 16 15"/></svg>
          </button>
          <button id="settings-open" class="icon-button" aria-label="Settings" title="Settings">
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06A1.65 1.65 0 0 0 15 19.4a1.65 1.65 0 0 0-1 .6 1.65 1.65 0 0 0-.35 1.05V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 8.6 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-.6-1 1.65 1.65 0 0 0-1.05-.35H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 8.6a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-.6 1.65 1.65 0 0 0 .35-1.05V3a2 2 0 1 1 4 0v.09A1.65 1.65 0 0 0 15.4 4.6a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 .6 1 1.65 1.65 0 0 0 1.05.35H21a2 2 0 1 1 0 4h-.09A1.65 1.65 0 0 0 19.4 15z"/></svg>
          </button>
        </div>
      </div>
      <div id="experiments" class="experiment-list"></div>
      <section class="sidebar-nodes">
        <div class="sidebar-section-header">
          <div class="sidebar-section-title">Nodes</div>
          <div class="sidebar-section-actions">
            <button id="queue-open" class="icon-button" aria-label="Show Slurm queue" title="Show Slurm queue">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/></svg>
            </button>
            <button id="refresh-status" class="icon-button" aria-label="Reload node status" title="Reload node status">
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M16 8h5V3"/></svg>
            </button>
          </div>
        </div>
        <div id="slurm-status" class="node-list muted">No status loaded.</div>
      </section>
    </aside>
    <div id="sidebar-resizer" class="sidebar-resizer" role="separator" aria-orientation="vertical" aria-label="Resize sidebar" tabindex="0"></div>
    <div id="create-modal" class="modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="create-modal-title">
      <div class="modal">
        <div class="modal-header">
          <div>
            <div id="create-modal-title" class="modal-title">Create Experiment</div>
            <div id="create-summary" class="csv-summary">Choose a name and preset.</div>
          </div>
          <button id="create-close" class="icon-button" aria-label="Close create dialog" title="Close">x</button>
        </div>
        <div class="modal-body">
          <div class="create-form">
            <label class="create-field">
              <span class="create-field-label">Experiment name</span>
              <div class="template-name-row">
                <span id="create-name-prefix" class="template-name-part"></span>
                <input id="create-name" placeholder="new experiment">
                <span id="create-name-suffix" class="template-name-part"></span>
              </div>
            </label>
            <label class="create-field">
              <span class="create-field-label">Preset</span>
              <select id="create-preset">
                <option value="">Loading presets...</option>
              </select>
            </label>
            <label class="checkbox-line">
              <input id="create-template-override" type="checkbox">
              <span>Override name template</span>
            </label>
            <div id="create-template-controls" class="template-controls hidden">
              <label class="create-field">
                <span class="create-field-label">Name template</span>
                <input id="create-template" spellcheck="false">
              </label>
            </div>
            <div id="create-preview" class="csv-summary"></div>
          </div>
        </div>
        <div class="modal-footer">
          <button id="create-cancel">Cancel</button>
          <button id="create-submit" class="primary">Create</button>
        </div>
      </div>
    </div>
    <div id="archive-modal" class="modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="archive-modal-title">
      <div class="modal">
        <div class="modal-header">
          <div>
            <div id="archive-modal-title" class="modal-title">Archived Experiments</div>
            <div id="archive-summary" class="csv-summary">No archived experiments loaded.</div>
          </div>
          <button id="archive-close" class="icon-button" aria-label="Close archived experiments" title="Close">x</button>
        </div>
        <div class="modal-body">
          <div id="archive-list" class="archive-list csv-empty">Open the dialog to load archived experiments.</div>
        </div>
        <div class="modal-footer">
          <button id="archive-refresh" class="icon-button" aria-label="Reload archived experiments" title="Reload archived experiments">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M16 8h5V3"/></svg>
          </button>
        </div>
      </div>
    </div>
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
          <button id="git-refresh" class="icon-button" aria-label="Reload Git status" title="Reload Git status">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M16 8h5V3"/></svg>
          </button>
          <button id="git-push" class="primary">Push</button>
        </div>
      </div>
    </div>
    <div id="share-modal" class="modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="share-modal-title">
      <div class="modal">
        <div class="modal-header">
          <div>
            <div id="share-modal-title" class="modal-title">Share Experiment</div>
            <div id="share-summary" class="csv-summary">Create a link for viewing this experiment without a token.</div>
          </div>
          <button id="share-close" class="icon-button" aria-label="Close share dialog" title="Close">x</button>
        </div>
        <div class="modal-body">
          <div class="share-fields">
            <label class="share-field">
              <span class="share-field-label">SSH tunnel</span>
              <textarea id="share-ssh" readonly spellcheck="false"></textarea>
            </label>
            <label class="share-field">
              <span class="share-field-label">Share link</span>
              <input id="share-link" readonly spellcheck="false">
            </label>
          </div>
        </div>
      </div>
    </div>
    <div id="queue-modal" class="modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="queue-modal-title">
      <div class="modal">
        <div class="modal-header">
          <div>
            <div id="queue-modal-title" class="modal-title">Slurm Queue</div>
            <div id="queue-summary" class="csv-summary">No queue loaded.</div>
          </div>
          <button id="queue-close" class="icon-button" aria-label="Close Slurm queue" title="Close">x</button>
        </div>
        <div class="modal-body">
          <div id="queue-output" class="csv-empty">Open the dialog to load squeue output.</div>
        </div>
        <div class="modal-footer">
          <button id="queue-refresh" class="icon-button" aria-label="Reload Slurm queue" title="Reload Slurm queue">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M16 8h5V3"/></svg>
          </button>
        </div>
      </div>
    </div>
    <div id="settings-modal" class="modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="settings-modal-title">
      <div class="modal settings-modal">
        <div class="modal-header">
          <div>
            <div id="settings-modal-title" class="modal-title">Settings</div>
            <div id="console-summary" class="csv-summary">No commands logged yet.</div>
          </div>
          <button id="settings-close" class="icon-button" aria-label="Close settings" title="Close">x</button>
        </div>
        <div class="modal-body">
          <div class="settings-token">
            <label for="token">Session token</label>
            <input id="token" type="password" placeholder="Session token">
          </div>
          <div class="settings-tool">
            <div>
              <div class="settings-tool-title">Spack R library cache</div>
              <div id="spack-cache-summary" class="csv-summary">Not loaded.</div>
            </div>
            <button id="spack-cache-refresh">Refresh Spack R library cache</button>
          </div>
          <div id="console-log" class="console-log"></div>
        </div>
        <div class="modal-footer">
          <button id="console-clear">Clear</button>
        </div>
      </div>
    </div>
    <div id="plot-source-modal" class="modal-backdrop hidden" role="dialog" aria-modal="true" aria-labelledby="plot-source-modal-title">
      <div class="modal">
        <div class="modal-header">
          <div>
            <div id="plot-source-modal-title" class="modal-title">Add CSV Source</div>
            <div id="plot-source-modal-summary" class="csv-summary">Load CSV files from other experiments.</div>
          </div>
          <button id="plot-source-close" class="icon-button" title="Close">x</button>
        </div>
        <div class="modal-body">
          <div id="plot-source-modal-list" class="plot-source-modal-list csv-empty">No CSV files loaded.</div>
        </div>
        <div class="modal-footer">
          <button id="plot-source-close-footer">Close</button>
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
        <span class="view-tabs-spacer"></span>
        <button id="share-experiment" class="icon-button" aria-label="Share experiment" title="Share experiment">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 10.6 6.8-4.2"/><path d="m8.6 13.4 6.8 4.2"/></svg>
        </button>
        <button id="download-experiment" class="icon-button" aria-label="Download experiment archive" title="Download experiment archive">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>
        </button>
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
            <section class="panel submit-panel">
              <div class="panel-header">
                <div class="panel-title">Submit</div>
              </div>
              <div class="panel-body stack">
                <div id="algorithm-list" class="chips"></div>
                <button class="primary" id="submit">Submit Selected</button>
              </div>
            </section>
            <section class="panel">
              <div class="panel-header">
                <div>
                  <div class="panel-title">Progress</div>
                  <div id="progress-summary" class="csv-summary">No progress loaded.</div>
                </div>
                <button id="refresh-progress" class="icon-button" aria-label="Reload progress" title="Reload progress">
                  <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M16 8h5V3"/></svg>
                </button>
              </div>
              <div class="panel-body">
                <div id="progress-output" class="csv-empty">Run progress to count finished log files against expected runs.</div>
              </div>
            </section>
            <section class="panel">
              <div class="panel-header">
                <div>
                  <div class="panel-title">Description</div>
                  <div id="description-summary" class="csv-summary">No description loaded.</div>
                </div>
                <div class="actions description-edit-actions">
                  <button id="description-edit">Edit</button>
                </div>
              </div>
              <div class="panel-body description-body">
                <div id="description-rendered" class="csv-empty">No description yet.</div>
                <textarea id="description-editor" class="description-editor hidden" spellcheck="true"></textarea>
                <div id="description-actions" class="description-actions hidden">
                  <button id="description-cancel">Cancel</button>
                  <button id="description-save" class="primary">Save</button>
                </div>
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
        <section class="panel danger-zone">
          <div class="panel-header">
            <div>
              <div class="panel-title">Danger Zone</div>
              <div id="danger-summary" class="csv-summary">Manual recovery, rename, and deletion actions.</div>
            </div>
          </div>
          <div class="panel-body">
            <div class="danger-actions">
              <button id="clear-submit-lock">Unlock submit</button>
              <button id="rename-experiment">Rename Experiment</button>
              <button id="archive-experiment">Archive Experiment</button>
              <button id="delete-experiment" class="danger">Delete Experiment</button>
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
          <div class="panel-body plot-manager">
            <div class="plot-grid">
              <section class="plot-box">
                <div class="plot-box-title">Plot Types</div>
                <div id="plot-catalog" class="csv-empty">Loading plot types...</div>
              </section>
              <section class="plot-box">
                <div class="plot-source-actions">
                  <div class="plot-box-title">Sources</div>
                  <button id="add-plot-source" class="small-button">Add CSV</button>
                </div>
                <div id="plot-sources" class="csv-empty">Loading sources...</div>
              </section>
            </div>
            <label>
              <span class="csv-summary">Artifact label</span>
              <input id="plot-label" type="text" placeholder="Auto-generated label">
            </label>
            <div id="plot-action-output" class="action-output"></div>
            <section class="plot-box">
              <div class="plot-box-title">Artifacts</div>
              <div id="plot-artifacts" class="csv-empty">No plot artifacts loaded.</div>
            </section>
            <div id="plot-file" class="csv-empty">Select a plot artifact to preview it.</div>
          </div>
        </section>
      </section>
    </main>
  </div>
  <script>
    const state = {
      experiments: [],
      archivedExperiments: [],
      selected: null,
      pinnedExperiments: new Set(),
      algorithms: [],
      algorithmLoading: false,
      algorithmLoadingFor: '',
      algorithmLoadSeq: 0,
      selectionSeq: 0,
      presets: [],
      config: { name_template: '%Y.%m.%d-<name>' },
      openDirs: new Set(),
      archivedOpenDirs: new Set(),
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
      submitBusy: false,
      plotBackend: null,
      plotCatalog: null,
      plotCatalogInitialized: false,
      plotSources: null,
      plotSourcesFor: null,
      plotSourcesInitializedFor: null,
      plotSourceOpenDirs: new Set(),
      selectedPlotTypes: new Set(),
      selectedPlotSources: new Set(),
      externalPlotSources: [],
      plotArtifacts: null,
      plotArtifactsFor: null,
      selectedPlotArtifact: '',
      plotPdfUrl: '',
      plotPdfUrlFor: null,
      plotPdfVersion: '',
      plotLabelTouched: false,
      plotNoDockerTouched: false,
      spackCache: null,
      consoleEntries: [],
      consoleOpen: false,
      progressTimer: null,
      description: null,
      descriptionFor: null,
      descriptionEditing: false,
      activeView: 'experiment-view',
      shared: false,
      shareId: ''
    };
    const PLOT_RELOAD_DELAY_MS = 5000;
    const SIDEBAR_WIDTH_KEY = 'mkexp2-sidebar-width';
    const DEFAULT_SIDEBAR_WIDTH = 320;
    const MIN_SIDEBAR_WIDTH = 260;
    const MAX_SIDEBAR_WIDTH = 560;
    const allowEmptyToken = __ALLOW_EMPTY_TOKEN__;
    const initialShareId = __SHARE_ID__;
    const tokenInput = document.getElementById('token');
    const editor = document.getElementById('experiment-editor');
    const editorHighlight = document.getElementById('experiment-highlight');
    tokenInput.value = localStorage.getItem('mkexp2-token') || '';
    tokenInput.addEventListener('change', () => {
      localStorage.setItem('mkexp2-token', tokenInput.value);
      out('');
      if (token() || allowEmptyToken) {
        refreshConfig().catch(err => out(String(err)));
        refreshPresets().catch(err => out(String(err)));
        refreshExperiments().catch(err => out(String(err)));
        refreshStatus().catch(err => out(String(err)));
      }
    });

    function token() { return tokenInput.value; }
    function apiPath(path) {
      if (!state.shared) return path;
      if (path.startsWith('/api/actions/')) {
        return `/api/share/${encodeURIComponent(state.shareId)}/actions/${path.split('/').pop()}`;
      }
      if (path === '/api/plot/backend') return `/api/share/${encodeURIComponent(state.shareId)}/plot/backend`;
      if (path === '/api/plots/catalog') return `/api/share/${encodeURIComponent(state.shareId)}/plots/catalog`;
      const match = path.match(/^\/api\/experiments\/[^/]+(?:\/([^?]+))?(\?.*)?$/);
      if (!match) return path;
      const tail = match[1] || 'metadata';
      const query = match[2] || '';
      return `/api/share/${encodeURIComponent(state.shareId)}/${tail}${query}`;
    }
    function clampSidebarWidth(width) {
      const viewportLimit = Math.max(MIN_SIDEBAR_WIDTH, Math.min(MAX_SIDEBAR_WIDTH, Math.round(window.innerWidth * 0.48)));
      return Math.max(MIN_SIDEBAR_WIDTH, Math.min(viewportLimit, Math.round(width)));
    }
    function setSidebarWidth(width, persist = true) {
      const clamped = clampSidebarWidth(width);
      document.documentElement.style.setProperty('--sidebar-width', `${clamped}px`);
      if (persist) localStorage.setItem(SIDEBAR_WIDTH_KEY, String(clamped));
      return clamped;
    }
    function initSidebarResize() {
      const saved = Number(localStorage.getItem(SIDEBAR_WIDTH_KEY));
      setSidebarWidth(Number.isFinite(saved) && saved > 0 ? saved : DEFAULT_SIDEBAR_WIDTH, false);
      const app = document.querySelector('.app');
      const resizer = document.getElementById('sidebar-resizer');
      let resizing = false;
      function stopResize(event) {
        if (!resizing) return;
        resizing = false;
        app.classList.remove('resizing');
        if (event?.pointerId !== undefined && resizer.hasPointerCapture(event.pointerId)) {
          resizer.releasePointerCapture(event.pointerId);
        }
      }
      resizer.addEventListener('pointerdown', event => {
        if (window.matchMedia('(max-width: 980px)').matches) return;
        resizing = true;
        app.classList.add('resizing');
        resizer.setPointerCapture(event.pointerId);
        setSidebarWidth(event.clientX);
        event.preventDefault();
      });
      resizer.addEventListener('pointermove', event => {
        if (!resizing) return;
        setSidebarWidth(event.clientX);
      });
      resizer.addEventListener('pointerup', stopResize);
      resizer.addEventListener('pointercancel', stopResize);
      resizer.addEventListener('keydown', event => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        const current = Number(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width').replace('px', '')) || DEFAULT_SIDEBAR_WIDTH;
        setSidebarWidth(current + (event.key === 'ArrowRight' ? 24 : -24));
        event.preventDefault();
      });
      window.addEventListener('resize', () => {
        const current = Number(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width').replace('px', '')) || DEFAULT_SIDEBAR_WIDTH;
        setSidebarWidth(current, false);
      });
    }
    function consoleText(value) {
      return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    }
    function setButtonBusy(buttonOrId, label = undefined) {
      const button = typeof buttonOrId === 'string' ? document.getElementById(buttonOrId) : buttonOrId;
      if (!button) return () => {};
      if (button.dataset.busy === '1') return () => {};
      const previous = {
        disabled: button.disabled,
        html: button.innerHTML,
        title: button.title || '',
      };
      button.dataset.busy = '1';
      button.disabled = true;
      button.classList.add('is-busy');
      button.setAttribute('aria-busy', 'true');
      const nextLabel = label === undefined
        ? (button.classList.contains('icon-button') ? '' : 'Working...')
        : label;
      button.textContent = nextLabel;
      return () => {
        button.disabled = previous.disabled;
        button.innerHTML = previous.html;
        button.title = previous.title;
        button.classList.remove('is-busy');
        button.removeAttribute('aria-busy');
        delete button.dataset.busy;
      };
    }
    async function withBusyButton(buttonOrId, label, task) {
      const restore = setButtonBusy(buttonOrId, label);
      try {
        return await task();
      } finally {
        restore();
      }
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
      if (kind === 'plot-artifacts') return Boolean(action.result?.plotted);
      return true;
    }
    function actionCommand(action, kind) {
      if (kind === 'parse') return action?.result?.parse;
      if (kind === 'plot') return action?.result?.plot;
      if (kind === 'plot-artifacts') return action?.result?.commands?.[0]?.command || null;
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
      const wrapper = document.createElement('div');
      wrapper.className = 'probe-settings';
      const settingsTitle = document.createElement('div');
      settingsTitle.className = 'probe-settings-title';
      settingsTitle.textContent = 'Resolved settings';
      wrapper.appendChild(settingsTitle);
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
    function probeArrayValues(values) {
      return Array.isArray(values)
        ? values.filter(value => value !== null && value !== undefined && value !== '').map(value => String(value))
        : [];
    }
    function probeGraphs(result) {
      const declared = probeArrayValues(result.declared?.graphs);
      if (declared.length) return declared;
      return probeArrayValues((result.resolved?.graphs || []).map(graph => graph.spec || graph.basename || graph.resolved_path));
    }
    function renderProbeInputCard(label, values, options = {}) {
      const card = document.createElement('div');
      card.className = 'probe-input-card';
      const title = document.createElement('div');
      title.className = 'probe-input-title';
      title.textContent = `${label} (${values.length})`;
      card.appendChild(title);
      const list = document.createElement('div');
      list.className = `probe-input-values${options.graphs ? ' graphs' : ''}`;
      if (!values.length) {
        list.textContent = '(none)';
        list.classList.add('probe-empty');
      } else if (options.graphs) {
        list.textContent = values.join('\n');
      } else {
        for (const value of values) {
          const chip = document.createElement('span');
          chip.className = 'probe-input-chip';
          chip.title = value;
          chip.textContent = value;
          list.appendChild(chip);
        }
      }
      card.appendChild(list);
      return card;
    }
    function renderProbeInputs(result) {
      const grid = document.createElement('div');
      grid.className = 'probe-input-grid';
      grid.appendChild(renderProbeInputCard('Graphs', probeGraphs(result), { graphs: true }));
      grid.appendChild(renderProbeInputCard('K', probeArrayValues(result.declared?.ks)));
      grid.appendChild(renderProbeInputCard('Eps', probeArrayValues(result.declared?.epsilons)));
      grid.appendChild(renderProbeInputCard('Seeds', probeArrayValues(result.declared?.seeds)));
      return grid;
    }
    function renderProbeResult(results, saveResult) {
      const box = document.getElementById('probe-output');
      const summaryBox = document.getElementById('probe-summary');
      box.innerHTML = '';
      box.className = 'probe-output';
      const root = document.createElement('div');
      root.className = 'probe-output';

      summaryBox.textContent = saveResult?.path ? `Source: ${saveResult.path}` : '';

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
        section.appendChild(renderProbeInputs(result));

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
          row.appendChild(detailRow);
          list.appendChild(row);
        }
        section.appendChild(list);
        root.appendChild(section);
      }
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
      const requestPath = apiPath(path);
      const response = await fetch(requestPath, Object.assign({}, options, { headers }));
      if (!response.ok) {
        const text = await response.text();
        appendConsoleLog(`${method} ${requestPath} failed`, text);
        throw new Error(text);
      }
      const payload = response.headers.get('content-type')?.includes('application/json')
        ? response.json()
        : response.text();
      const data = await payload;
      logApiCommands(method, requestPath, data);
      return data;
    }
    async function fetchBlob(path) {
      const requestPath = apiPath(path);
      const response = await fetch(requestPath, { headers: { 'X-MKEXP2-Token': token() } });
      if (!response.ok) {
        const text = await response.text();
        appendConsoleLog(`GET ${requestPath} failed`, text);
        throw new Error(text);
      }
      return await response.blob();
    }
    async function fetchDownload(path) {
      const requestPath = apiPath(path);
      const response = await fetch(requestPath, { headers: { 'X-MKEXP2-Token': token() } });
      if (!response.ok) {
        const text = await response.text();
        appendConsoleLog(`GET ${requestPath} failed`, text);
        throw new Error(text);
      }
      const disposition = response.headers.get('content-disposition') || '';
      const filenameMatch = disposition.match(/filename="([^"]+)"/);
      return {
        blob: await response.blob(),
        filename: filenameMatch ? filenameMatch[1] : ''
      };
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
    function slugifyName(value) {
      return String(value || 'experiment')
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '') || 'experiment';
    }
    function renderTemplateForDate(template, name) {
      const now = new Date();
      const pad = number => String(number).padStart(2, '0');
      return String(template ?? '%Y.%m.%d-<name>')
        .replaceAll('%Y', String(now.getFullYear()))
        .replaceAll('%m', pad(now.getMonth() + 1))
        .replaceAll('%d', pad(now.getDate()))
        .replaceAll('%H', pad(now.getHours()))
        .replaceAll('%M', pad(now.getMinutes()))
        .replaceAll('%S', pad(now.getSeconds()))
        .replaceAll('<name>', slugifyName(name));
    }
    function activeCreateTemplate() {
      const override = document.getElementById('create-template-override').checked;
      const custom = document.getElementById('create-template').value.trim();
      return override && custom ? custom : (state.config.name_template || '%Y.%m.%d-<name>');
    }
    function updateCreatePreview() {
      const template = activeCreateTemplate();
      const name = document.getElementById('create-name').value || 'experiment';
      const renderedName = slugifyName(name);
      const tokenIndex = template.indexOf('<name>');
      const prefix = document.getElementById('create-name-prefix');
      const suffix = document.getElementById('create-name-suffix');
      if (tokenIndex >= 0) {
        prefix.textContent = renderTemplateForDate(template.slice(0, tokenIndex), '');
        suffix.textContent = renderTemplateForDate(template.slice(tokenIndex + '<name>'.length), '');
      } else {
        prefix.textContent = renderTemplateForDate(template, '');
        suffix.textContent = '';
      }
      document.getElementById('create-preview').textContent = `Will create: ${renderTemplateForDate(template, renderedName)}`;
      document.getElementById('create-template-controls').classList.toggle(
        'hidden',
        !document.getElementById('create-template-override').checked
      );
    }
    function renderGitStatus(status) {
      const repoSummary = document.getElementById('git-repo-summary');
      const grid = document.getElementById('git-status');
      const output = document.getElementById('git-output');
      repoSummary.textContent = `${status.repo || 'experiment repo'}${status.branch ? ` on ${status.branch}` : ''}`;
      grid.innerHTML = '';
      const groups = status.groups || {};
      const list = document.createElement('div');
      list.className = 'git-file-list';
      let total = 0;
      for (const [key, label] of [['added', 'A'], ['modified', 'M'], ['deleted', 'D']]) {
        const files = groups[key] || [];
        total += files.length;
        for (const file of files) {
          const item = document.createElement('div');
          item.className = `git-file ${key}`;
          item.title = `${file.status} ${file.path}`;
          const kind = document.createElement('span');
          kind.className = 'git-file-kind';
          kind.textContent = label;
          const path = document.createElement('span');
          path.className = 'git-file-path';
          path.textContent = file.path;
          item.appendChild(kind);
          item.appendChild(path);
          list.appendChild(item);
        }
      }
      if (!total) {
        const empty = document.createElement('div');
        empty.className = 'csv-summary';
        empty.textContent = 'No added, modified, or deleted files.';
        list.appendChild(empty);
      }
      grid.appendChild(list);
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
    function queueStateClass(state) {
      const raw = String(state || '').toUpperCase();
      if (raw === 'R' || raw === 'RUNNING') return 'queue-state-running';
      if (raw === 'PD' || raw === 'PENDING') return 'queue-state-pending';
      return 'queue-state-other';
    }
    function renderQueue(data) {
      const summary = document.getElementById('queue-summary');
      const output = document.getElementById('queue-output');
      const rows = data.rows || [];
      summary.textContent = `${rows.length} job${rows.length === 1 ? '' : 's'} from ${data.source || 'squeue'}; refreshed ${data.generated_at || 'now'}.`;
      if (!rows.length) {
        output.className = 'csv-empty';
        output.textContent = 'No queued or running Slurm jobs.';
        return;
      }
      output.className = 'queue-table-wrap';
      output.innerHTML = '';
      const table = document.createElement('table');
      table.className = 'queue-table';
      const thead = document.createElement('thead');
      const headRow = document.createElement('tr');
      for (const label of ['Job ID', 'Partition', 'Name', 'User', 'State', 'Time', 'Nodes', 'Node list / reason', 'Action']) {
        const th = document.createElement('th');
        th.textContent = label;
        headRow.appendChild(th);
      }
      thead.appendChild(headRow);
      table.appendChild(thead);
      const tbody = document.createElement('tbody');
      for (const row of rows) {
        const tr = document.createElement('tr');
        for (const key of ['job_id', 'partition', 'name', 'user', 'state', 'time', 'nodes', 'nodelist']) {
          const td = document.createElement('td');
          td.textContent = row[key] || '';
          if (key === 'state') td.className = `queue-state ${queueStateClass(row[key])}`;
          tr.appendChild(td);
        }
        const action = document.createElement('td');
        if (row.user === data.server_user) {
          const button = document.createElement('button');
          button.className = 'queue-cancel';
          button.textContent = 'x';
          button.setAttribute('aria-label', `Cancel Slurm job ${row.job_id}`);
          button.title = `Cancel Slurm job ${row.job_id}`;
          button.onclick = () => cancelQueueJob(row.job_id, button).catch(err => out(String(err)));
          action.appendChild(button);
        } else {
          action.className = 'csv-summary';
          action.textContent = '';
        }
        tr.appendChild(action);
        tbody.appendChild(tr);
      }
      table.appendChild(tbody);
      output.appendChild(table);
    }
    async function loadQueue() {
      const output = document.getElementById('queue-output');
      output.className = 'csv-empty';
      output.textContent = 'Loading Slurm queue...';
      const data = await api('/api/status/squeue');
      renderQueue(data);
      return data;
    }
    async function openQueueDialog() {
      document.getElementById('queue-modal').classList.remove('hidden');
      await loadQueue().catch(err => {
        const output = document.getElementById('queue-output');
        output.className = 'csv-empty status-bad';
        output.textContent = String(err);
      });
    }
    function closeQueueDialog() {
      document.getElementById('queue-modal').classList.add('hidden');
    }
    async function cancelQueueJob(jobId, button = null) {
      if (!confirm(`Cancel Slurm job ${jobId}?`)) return;
      await withBusyButton(button, '', async () => {
        await api('/api/status/squeue/cancel', {
          method: 'POST',
          body: JSON.stringify({ job_id: jobId })
        });
        await loadQueue();
        await refreshStatus().catch(err => out(String(err)));
      });
    }
    function openSettingsDialog() {
      state.consoleOpen = true;
      document.getElementById('settings-modal').classList.remove('hidden');
      loadSpackCacheInfo().catch(err => out(String(err)));
      renderConsoleLog();
    }
    function closeSettingsDialog() {
      state.consoleOpen = false;
      document.getElementById('settings-modal').classList.add('hidden');
    }
    function clearConsoleLog() {
      state.consoleEntries = [];
      renderConsoleLog();
    }
    function renderSpackCacheInfo() {
      const summary = document.getElementById('spack-cache-summary');
      if (!summary) return;
      const info = state.spackCache;
      if (!info) {
        summary.textContent = 'Not loaded.';
        return;
      }
      summary.textContent = info.exists
        ? `${info.entry_count || 0} R library paths, ${formatBytes(info.size || 0)}, modified ${info.modified_at || 'unknown time'}`
        : `No cache file at ${info.path || 'the mkexp2 plots cache'}.`;
    }
    async function loadSpackCacheInfo() {
      state.spackCache = await api('/api/plot/spack-r-libs');
      renderSpackCacheInfo();
      return state.spackCache;
    }
    async function refreshSpackCache() {
      const button = document.getElementById('spack-cache-refresh');
      await withBusyButton(button, 'Resolving...', async () => {
        const action = await api('/api/plot/spack-r-libs/resolve', {
          method: 'POST',
          body: JSON.stringify({ force: true })
        });
        const completed = await watchAction(action.id);
        if (completed?.status === 'completed' && completed.result?.cache) {
          state.spackCache = completed.result.cache;
        } else {
          await loadSpackCacheInfo();
        }
        renderSpackCacheInfo();
      });
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
      await withBusyButton(button, 'Pushing...', async () => {
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
        }
      });
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
    function renderDescriptionWorkspace() {
      const summary = document.getElementById('description-summary');
      const rendered = document.getElementById('description-rendered');
      const editorNode = document.getElementById('description-editor');
      const actions = document.getElementById('description-actions');
      const editButton = document.getElementById('description-edit');
      if (!state.selected) {
        state.descriptionEditing = false;
        summary.textContent = 'No experiment selected.';
        rendered.className = 'csv-empty';
        rendered.textContent = 'Select an experiment first.';
        editorNode.classList.add('hidden');
        actions.classList.add('hidden');
        editButton.disabled = true;
        return;
      }
      editButton.disabled = state.descriptionFor !== state.selected || state.shared;
      if (state.descriptionFor !== state.selected || !state.description) {
        state.descriptionEditing = false;
        summary.textContent = 'No description loaded.';
        rendered.className = 'csv-empty';
        rendered.textContent = 'Loading description...';
        editorNode.classList.add('hidden');
        actions.classList.add('hidden');
        return;
      }
      const content = state.description.content || '';
      const suffix = state.description.truncated ? ' (truncated)' : '';
      summary.textContent = state.description.exists
        ? `description.md, ${state.description.size || 0} bytes, modified ${state.description.modified_at || 'unknown'}${suffix}`
        : 'description.md does not exist yet.';
      if (state.descriptionEditing && !state.shared) {
        rendered.classList.add('hidden');
        editorNode.classList.remove('hidden');
        actions.classList.remove('hidden');
        editButton.disabled = true;
        return;
      }
      editorNode.classList.add('hidden');
      actions.classList.add('hidden');
      rendered.classList.remove('hidden');
      if (content.trim()) {
        renderMarkdown(content, rendered);
      } else {
        rendered.className = 'csv-empty';
        rendered.textContent = 'No description yet.';
      }
    }
    async function loadDescription() {
      if (!state.selected) return;
      const experimentId = state.selected;
      state.descriptionEditing = false;
      renderDescriptionWorkspace();
      const data = await api(`/api/experiments/${encodeURIComponent(experimentId)}/description`);
      if (state.selected !== experimentId) return;
      state.description = data;
      state.descriptionFor = experimentId;
      renderDescriptionWorkspace();
    }
    function editDescription() {
      if (!state.selected || state.shared || state.descriptionFor !== state.selected) return;
      const editorNode = document.getElementById('description-editor');
      editorNode.value = state.description?.content || '';
      state.descriptionEditing = true;
      renderDescriptionWorkspace();
      editorNode.focus();
    }
    function cancelDescriptionEdit() {
      state.descriptionEditing = false;
      renderDescriptionWorkspace();
    }
    async function saveDescription() {
      if (!state.selected || state.shared) return;
      const experimentId = state.selected;
      const description = document.getElementById('description-editor').value;
      await withBusyButton('description-save', 'Saving...', async () => {
        const result = await api(`/api/experiments/${encodeURIComponent(experimentId)}/description`, {
          method: 'PUT',
          body: JSON.stringify({ description })
        });
        if (state.selected !== experimentId) return;
        state.description = result;
        state.descriptionFor = experimentId;
        state.descriptionEditing = false;
        renderDescriptionWorkspace();
      });
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
        up.onclick = () => withBusyButton(up, 'Loading...', () => loadLogs(parentLogDir(dir))).catch(err => out(String(err)));
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
        button.onclick = () => withBusyButton(button, 'Loading...', () => (
          entry.type === 'dir' ? loadLogs(entry.path) : loadLogFile(entry.path)
        )).catch(err => out(String(err)));
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
    async function setView(viewId) {
      state.activeView = viewId;
      document.querySelectorAll('.view-tab').forEach(button => {
        button.classList.toggle('active', button.dataset.view === viewId);
      });
      document.querySelectorAll('.view-panel').forEach(panel => {
        panel.classList.toggle('active', panel.id === viewId);
      });
      if (viewId === 'results-view') {
        await activateCsvView(viewId);
      }
      if (viewId === 'install-log-view') {
        await ensureInstallLogLoaded();
      }
      if (viewId === 'logs-view') {
        await ensureLogsLoaded();
      }
      if (viewId === 'plots-view') {
        await Promise.all([
          loadPlotBackendStatus(),
          state.plotCatalog ? Promise.resolve(state.plotCatalog) : loadPlotCatalog(),
          state.plotSourcesFor === state.selected ? Promise.resolve(state.plotSources) : loadPlotSources(false),
          loadPlotInfo()
        ]);
        renderPlotPanel();
      }
    }
    function treeNode() {
      return { folders: new Map(), experiments: [], count: 0, latest: 0 };
    }
    function experimentCreationKey(exp) {
      const epoch = Number(exp.created_at_epoch);
      if (Number.isFinite(epoch)) return epoch * 1000;
      const parsed = Date.parse(exp.created_at || exp.modified_at || '');
      return Number.isFinite(parsed) ? parsed : 0;
    }
    function compareExperimentsByCreatedDesc(left, right) {
      return experimentCreationKey(right) - experimentCreationKey(left)
        || String(left.label || left.id).localeCompare(String(right.label || right.id));
    }
    function experimentTree(experiments) {
      const root = treeNode();
      const sorted = Array.from(experiments).sort((left, right) => left.id.localeCompare(right.id));
      for (const exp of sorted) {
        const parts = exp.id.split('/').filter(Boolean);
        if (!parts.length) continue;
        const created = experimentCreationKey(exp);
        let node = root;
        node.count += 1;
        node.latest = Math.max(node.latest, created);
        for (let index = 0; index < parts.length - 1; index += 1) {
          const part = parts[index];
          if (!node.folders.has(part)) node.folders.set(part, treeNode());
          node = node.folders.get(part);
          node.count += 1;
          node.latest = Math.max(node.latest, created);
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
      const folders = Array.from(node.folders.entries()).sort((left, right) => {
        return (right[1].latest || 0) - (left[1].latest || 0) || left[0].localeCompare(right[0]);
      });
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
      const experiments = Array.from(node.experiments).sort(compareExperimentsByCreatedDesc);
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
      button.onclick = () => withBusyButton(button, 'Loading...', () => selectExperiment(exp.id)).catch(err => out(String(err)));
      const pin = document.createElement('button');
      pin.className = 'pin-button' + (pinned ? ' active' : '');
      pin.type = 'button';
      pin.textContent = pinned ? '★' : '☆';
      pin.title = pinned ? 'Unpin experiment' : 'Pin experiment';
      pin.setAttribute('aria-label', `${pinned ? 'Unpin' : 'Pin'} ${exp.id}`);
      pin.onclick = () => withBusyButton(pin, '', () => togglePinnedExperiment(exp.id)).catch(err => out(String(err)));
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
    function renderArchivedExperimentTree(container, node, prefix = '') {
      const folders = Array.from(node.folders.entries()).sort((left, right) => left[0].localeCompare(right[0]));
      for (const [name, child] of folders) {
        const id = prefix ? `${prefix}/${name}` : name;
        const details = document.createElement('details');
        details.className = 'experiment-folder archive-folder';
        details.open = state.archivedOpenDirs.has(id);
        details.addEventListener('toggle', () => {
          if (details.open) state.archivedOpenDirs.add(id);
          else state.archivedOpenDirs.delete(id);
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
        renderArchivedExperimentTree(children, child, id);
        details.appendChild(summary);
        details.appendChild(children);
        container.appendChild(details);
      }
      const experiments = Array.from(node.experiments).sort((left, right) => left.label.localeCompare(right.label));
      for (const exp of experiments) {
        renderArchivedExperimentItem(container, exp);
      }
    }
    function renderArchivedExperimentItem(container, exp) {
      const item = document.createElement('div');
      item.className = 'archive-item';
      const text = document.createElement('div');
      const name = document.createElement('div');
      name.className = 'archive-name';
      name.textContent = exp.label || exp.name || exp.id;
      const path = document.createElement('div');
      path.className = 'archive-path';
      path.textContent = exp.id;
      text.appendChild(name);
      text.appendChild(path);
      const button = document.createElement('button');
      button.textContent = 'Unarchive';
      button.title = `Unarchive ${exp.id}`;
      button.onclick = () => unarchiveExperiment(exp.id, button).catch(err => out(String(err)));
      item.appendChild(text);
      item.appendChild(button);
      container.appendChild(item);
    }
    function renderArchivedExperiments() {
      const list = document.getElementById('archive-list');
      const summary = document.getElementById('archive-summary');
      const archived = state.archivedExperiments || [];
      summary.textContent = `${archived.length} archived experiment${archived.length === 1 ? '' : 's'}.`;
      list.innerHTML = '';
      if (!archived.length) {
        list.className = 'archive-list csv-empty';
        list.textContent = 'No archived experiments.';
        return;
      }
      list.className = 'archive-list';
      renderArchivedExperimentTree(list, experimentTree(archived));
    }
    async function loadArchivedExperiments(options = {}) {
      const query = options.force ? '?refresh=1' : '';
      const data = await api(`/api/experiments/archived${query}`);
      state.archivedExperiments = data.experiments || [];
      renderArchivedExperiments();
      return state.archivedExperiments;
    }
    async function openArchiveDialog() {
      document.getElementById('archive-modal').classList.remove('hidden');
      await loadArchivedExperiments({ force: true }).catch(err => {
        const list = document.getElementById('archive-list');
        list.className = 'archive-list csv-empty status-bad';
        list.textContent = String(err);
      });
    }
    function closeArchiveDialog() {
      document.getElementById('archive-modal').classList.add('hidden');
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
    async function refreshConfig() {
      const data = await api('/api/config');
      state.config = data || state.config;
      document.getElementById('create-template').value = state.config.name_template || '%Y.%m.%d-<name>';
      updateCreatePreview();
      return state.config;
    }
    async function refreshPresets() {
      const data = await api('/api/presets');
      clearTransientOutput();
      state.presets = data.presets || [];
      const select = document.getElementById('create-preset');
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
    async function openCreateDialog() {
      document.getElementById('create-modal').classList.remove('hidden');
      document.getElementById('create-name').focus();
      await Promise.all([
        refreshConfig().catch(err => out(String(err))),
        refreshPresets().catch(err => out(String(err)))
      ]);
      updateCreatePreview();
    }
    function closeCreateDialog() {
      document.getElementById('create-modal').classList.add('hidden');
    }
    function renderSubmitLock(lock) {
      state.submitLock = lock || { locked: false };
      const clearButton = document.getElementById('clear-submit-lock');
      const submitButton = document.getElementById('submit');
      const locked = Boolean(state.submitLock.locked);
      clearButton.disabled = !locked || !state.selected;
      renderSubmitButton();
    }
    function submitLockMessage() {
      if (!state.submitLock?.locked) return '';
      const fields = state.submitLock.fields || {};
      const started = fields.started_at ? ` since ${fields.started_at}` : '';
      const algorithms = fields.algorithms ? ` (${fields.algorithms})` : '';
      return `Submit locked${started}${algorithms}`;
    }
    function renderSubmitButton() {
      const submitButton = document.getElementById('submit');
      if (!submitButton) return;
      const locked = Boolean(state.submitLock?.locked);
      const loadingAlgorithms = Boolean(state.algorithmLoading && state.algorithmLoadingFor === state.selected);
      submitButton.disabled = state.submitBusy || loadingAlgorithms || locked || !state.selected;
      submitButton.classList.toggle('is-busy', state.submitBusy || loadingAlgorithms);
      if (state.submitBusy) {
        submitButton.textContent = 'Submitting...';
        submitButton.title = 'Submitting experiment...';
      } else if (loadingAlgorithms) {
        submitButton.textContent = 'Loading algorithms...';
        submitButton.title = 'Loading submit choices...';
      } else {
        submitButton.textContent = 'Submit Selected';
        submitButton.title = locked ? submitLockMessage() : '';
      }
      const clearButton = document.getElementById('clear-submit-lock');
      if (clearButton) clearButton.disabled = !locked || !state.selected;
      const renameButton = document.getElementById('rename-experiment');
      if (renameButton) {
        renameButton.disabled = locked || !state.selected;
        renameButton.title = locked ? 'Cannot rename while submit is locked.' : '';
      }
      const archiveButton = document.getElementById('archive-experiment');
      if (archiveButton) {
        archiveButton.disabled = locked || !state.selected;
        archiveButton.title = locked ? 'Cannot archive while submit is locked.' : '';
      }
      const deleteButton = document.getElementById('delete-experiment');
      if (deleteButton) {
        deleteButton.disabled = locked || !state.selected;
        deleteButton.title = locked ? 'Cannot delete while submit is locked.' : '';
      }
      const dangerSummary = document.getElementById('danger-summary');
      if (dangerSummary) {
        if (locked) {
          dangerSummary.textContent = `${submitLockMessage()}.`;
        } else {
          dangerSummary.textContent = 'Manual recovery, rename, archive, and deletion actions.';
        }
      }
    }
    function renderAlgorithmLoading(experimentId) {
      state.algorithms = [];
      state.algorithmLoading = true;
      state.algorithmLoadingFor = experimentId || '';
      const list = document.getElementById('algorithm-list');
      list.innerHTML = '';
      const row = document.createElement('div');
      row.className = 'chip algorithm-loading';
      const spinner = document.createElement('span');
      spinner.className = 'loading-spinner';
      const text = document.createElement('span');
      text.textContent = 'Loading algorithms...';
      row.appendChild(spinner);
      row.appendChild(text);
      list.appendChild(row);
      renderSubmitButton();
    }
    function renderAlgorithmChoices(names) {
      state.algorithms = Array.from(names || []).sort();
      state.algorithmLoading = false;
      state.algorithmLoadingFor = '';
      const list = document.getElementById('algorithm-list');
      list.innerHTML = '';
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
      if (!state.algorithms.length) {
        const empty = document.createElement('div');
        empty.className = 'csv-empty';
        empty.textContent = 'No algorithms found.';
        list.appendChild(empty);
      }
      renderSubmitButton();
    }
    function clearAlgorithmChoices() {
      state.algorithms = [];
      state.algorithmLoading = false;
      state.algorithmLoadingFor = '';
      state.algorithmLoadSeq += 1;
      document.getElementById('algorithm-list').innerHTML = '';
      renderSubmitButton();
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
      await withBusyButton('clear-submit-lock', 'Unlocking...', async () => {
        const result = await api(`/api/experiments/${encodeURIComponent(state.selected)}/submit-lock`, { method: 'DELETE' });
        renderSubmitLock(result.submit_lock);
      });
    }
    function clearSelectedExperiment() {
      state.selected = null;
      clearAlgorithmChoices();
      state.results = [];
      state.resultsFor = null;
      state.stats = null;
      state.statsFor = null;
      state.selectedResults = [];
      state.compareColumnModes = {};
      state.installLog = null;
      state.installLogFor = null;
      state.description = null;
      state.descriptionFor = null;
      state.descriptionEditing = false;
      state.logsDir = '';
      state.logsListing = null;
      state.logsFor = null;
      state.selectedLog = '';
      state.logContent = null;
      state.submitLock = null;
      state.plotSources = null;
      state.plotSourcesFor = null;
      state.plotSourcesInitializedFor = null;
      state.selectedPlotSources = new Set();
      state.externalPlotSources = [];
      state.plotArtifacts = null;
      state.plotArtifactsFor = null;
      state.selectedPlotArtifact = '';
      state.plotLabelTouched = false;
      clearPlotPdfUrl();
      setView('experiment-view').catch(err => out(String(err)));
      document.getElementById('selected-title').textContent = 'Experiment';
      document.getElementById('selected-path').textContent = '';
      setEditorValue('');
      renderResultsWorkspace();
      renderStatsWorkspace();
      renderInstallLogWorkspace();
      renderDescriptionWorkspace();
      renderLogsWorkspace();
      renderSubmitLock({ locked: false });
      renderProgress(null);
      document.getElementById('probe-summary').textContent = 'No probe loaded.';
      document.getElementById('probe-output').innerHTML = '<div class="probe-placeholder">Run Probe to inspect enabled algorithms, branch settings, CLI arguments, and resolved properties.</div>';
      renderExperimentsList();
    }
    async function archiveExperiment() {
      if (!state.selected) return;
      if (state.submitLock?.locked) {
        alert('Cannot archive while submit is locked.');
        renderSubmitButton();
        return;
      }
      const id = state.selected;
      if (!confirm(`Archive experiment "${id}"? It will be renamed to "${id}.archived" and hidden from the sidebar.`)) return;
      const button = document.getElementById('archive-experiment');
      await withBusyButton(button, 'Archiving...', async () => {
        await api(`/api/experiments/${encodeURIComponent(id)}/archive`, { method: 'POST' });
        clearSelectedExperiment();
        await Promise.all([
          refreshExperiments({ force: true }),
          loadArchivedExperiments({ force: true }).catch(err => out(String(err)))
        ]);
      });
    }
    async function renameExperiment() {
      if (!state.selected) return;
      if (state.submitLock?.locked) {
        alert('Cannot rename while submit is locked.');
        renderSubmitButton();
        return;
      }
      const id = state.selected;
      const newId = prompt('New experiment path:', id);
      if (newId === null) return;
      const trimmed = newId.trim().replace(/^\/+|\/+$/g, '');
      if (!trimmed || trimmed === id) return;
      const button = document.getElementById('rename-experiment');
      await withBusyButton(button, 'Renaming...', async () => {
        const result = await api(`/api/experiments/${encodeURIComponent(id)}/rename`, {
          method: 'POST',
          body: JSON.stringify({ new_id: trimmed })
        });
        await refreshExperiments({ force: true });
        await selectExperiment(result.new_id);
      });
    }
    function closeShareDialog() {
      document.getElementById('share-modal').classList.add('hidden');
    }
    async function shareExperiment() {
      if (!state.selected || state.shared) return;
      await withBusyButton('share-experiment', '', async () => {
        const result = await api(`/api/experiments/${encodeURIComponent(state.selected)}/share`, { method: 'POST' });
        document.getElementById('share-modal').classList.remove('hidden');
        document.getElementById('share-summary').textContent = `Shared ${result.share?.experiment_id || state.selected}.`;
        document.getElementById('share-ssh').value = result.ssh_tunnel || '';
        document.getElementById('share-link').value = result.share_url || '';
      });
    }
    async function downloadExperiment() {
      if (!state.selected) return;
      await withBusyButton('download-experiment', '', async () => {
        const result = await fetchDownload(`/api/experiments/${encodeURIComponent(state.selected)}/download`);
        const url = URL.createObjectURL(result.blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = result.filename || `${slugifyName(state.selected)}.zip`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      });
    }
    async function deleteExperiment() {
      if (!state.selected) return;
      if (state.submitLock?.locked) {
        alert('Cannot delete while submit is locked.');
        renderSubmitButton();
        return;
      }
      const id = state.selected;
      const typed = prompt(`Type the full experiment name to delete it:\n${id}`);
      if (typed !== id) return;
      if (!confirm(`Delete experiment "${id}" and all files in its directory?`)) return;
      const button = document.getElementById('delete-experiment');
      await withBusyButton(button, 'Deleting...', async () => {
        await api(`/api/experiments/${encodeURIComponent(id)}`, { method: 'DELETE' });
        clearSelectedExperiment();
        await refreshExperiments({ force: true });
      });
    }
    async function unarchiveExperiment(id, button) {
      await withBusyButton(button, 'Unarchiving...', async () => {
        await api(`/api/experiments/${encodeURIComponent(id)}/unarchive`, { method: 'POST' });
        await Promise.all([
          refreshExperiments({ force: true }),
          loadArchivedExperiments({ force: true })
        ]);
      });
    }
    function startProgressPolling() {
      if (state.progressTimer) return;
      state.progressTimer = setInterval(() => {
        if (state.selected) loadProgress({ quiet: true }).catch(err => out(String(err)));
      }, 15000);
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
      const selectionId = ++state.selectionSeq;
      state.selected = id;
      state.algorithmLoadSeq += 1;
      state.results = [];
      state.resultsFor = null;
      state.stats = null;
      state.statsFor = null;
      state.selectedResults = [];
      state.compareColumnModes = {};
      state.installLog = null;
      state.installLogFor = null;
      state.description = null;
      state.descriptionFor = null;
      state.descriptionEditing = false;
      state.logsDir = '';
      state.logsListing = null;
      state.logsFor = null;
      state.selectedLog = '';
      state.logContent = null;
      state.submitLock = null;
      state.plotSources = null;
      state.plotSourcesFor = null;
      state.plotSourcesInitializedFor = null;
      state.selectedPlotSources = new Set();
      state.externalPlotSources = [];
      state.plotArtifacts = null;
      state.plotArtifactsFor = null;
      state.selectedPlotArtifact = '';
      state.plotLabelTouched = false;
      clearPlotPdfUrl();
      setView('experiment-view').catch(err => out(String(err)));
      renderResultsWorkspace();
      renderStatsWorkspace();
      renderInstallLogWorkspace();
      renderDescriptionWorkspace();
      renderLogsWorkspace();
      renderSubmitLock({ locked: false });
      renderProgress(null);
      renderAlgorithmLoading(id);
      document.getElementById('probe-summary').textContent = 'No probe loaded.';
      document.getElementById('probe-output').innerHTML = '<div class="probe-placeholder">Run Probe to inspect enabled algorithms, branch settings, CLI arguments, and resolved properties.</div>';
      openExperimentAncestors(id);
      renderExperimentsList();
      const data = await api(`/api/experiments/${encodeURIComponent(id)}/experiment`);
      if (state.selected !== id || state.selectionSeq !== selectionId) return;
      clearTransientOutput();
      document.getElementById('selected-title').textContent = id;
      document.getElementById('selected-path').textContent = data.path;
      setEditorValue(data.experiment);
      renderSubmitLock(data.submit_lock);
      loadDescription().catch(err => out(String(err)));
      await loadAlgorithms(id);
    }
    async function selectSharedExperiment(shareId) {
      state.shared = true;
      state.shareId = shareId;
      document.querySelector('.app').classList.add('share-mode');
      editor.readOnly = true;
      const data = await api(`/api/share/${encodeURIComponent(shareId)}/experiment`);
      const id = data.id;
      state.selected = id;
      state.selectionSeq += 1;
      state.algorithmLoadSeq += 1;
      clearAlgorithmChoices();
      setView('experiment-view').catch(err => out(String(err)));
      document.getElementById('selected-title').textContent = id;
      document.getElementById('selected-path').textContent = data.path;
      setEditorValue(data.experiment);
      renderSubmitLock(data.submit_lock);
      renderProgress(null);
      loadDescription().catch(err => out(String(err)));
    }
    async function persistExperiment() {
      if (!state.selected) return;
      if (state.shared) throw new Error('Shared experiments cannot be edited.');
      const experiment = document.getElementById('experiment-editor').value;
      return await api(`/api/experiments/${encodeURIComponent(state.selected)}/experiment`, {
        method: 'PUT',
        body: JSON.stringify({ experiment })
      });
    }
    async function createExperiment() {
      const name = document.getElementById('create-name').value || 'experiment';
      const preset = document.getElementById('create-preset').value;
      const nameTemplate = activeCreateTemplate();
      const button = document.getElementById('create-submit');
      await withBusyButton(button, 'Creating...', async () => {
        const data = await api('/api/experiments', {
          method: 'POST',
          body: JSON.stringify({ name, preset, name_template: nameTemplate })
        });
        closeCreateDialog();
        await refreshExperiments({ force: true });
        await selectExperiment(data.id);
      });
    }
    async function checkExperiment() {
      if (!state.selected) return;
      const experimentId = state.selected;
      const button = document.getElementById('check');
      await withBusyButton(button, 'Checking...', async () => {
        out('Saving and checking...');
        const saved = await persistExperiment();
        if (state.selected !== experimentId) return;
        const result = await api(`/api/experiments/${encodeURIComponent(experimentId)}/check`, { method: 'POST' });
        if (state.selected !== experimentId) return;
        renderCheckResult(result, saved);
        try {
          await loadAlgorithms(experimentId);
        } catch (err) {
          out(`Algorithm refresh failed after check: ${String(err)}`);
        }
      });
    }
    async function probeExperiment() {
      if (!state.selected) return;
      const experimentId = state.selected;
      await withBusyButton('probe-run', 'Running...', async () => {
        document.getElementById('probe-summary').textContent = 'Running mkexp2 probe...';
        document.getElementById('probe-output').innerHTML = '<div class="probe-placeholder">Running mkexp2 probe...</div>';
        const listing = await api(`/api/experiments/${encodeURIComponent(experimentId)}/probe`, {
          method: 'POST',
          body: JSON.stringify({})
        });
        if (state.selected !== experimentId) return;
        const results = [];
        for (const item of listing.experiments || []) {
          const detail = await api(`/api/experiments/${encodeURIComponent(experimentId)}/probe`, {
            method: 'POST',
            body: JSON.stringify({ selector: item.name })
          });
          if (state.selected !== experimentId) return;
          results.push(detail);
        }
        renderProbeResult(results, null);
        await loadAlgorithms(experimentId);
      });
    }
    async function loadAlgorithms(experimentId = state.selected) {
      if (!experimentId) return;
      const loadId = ++state.algorithmLoadSeq;
      const isCurrent = () => state.selected === experimentId && state.algorithmLoadSeq === loadId;
      renderAlgorithmLoading(experimentId);
      try {
        const probe = await api(`/api/experiments/${encodeURIComponent(experimentId)}/probe`, {
          method: 'POST',
          body: JSON.stringify({})
        });
        if (!isCurrent()) return;
        const experiments = probe.experiments || [];
        const names = new Set();
        for (const item of experiments) {
          const details = await api(`/api/experiments/${encodeURIComponent(experimentId)}/probe`, {
            method: 'POST',
            body: JSON.stringify({ selector: item.name, flags: ['--algorithms'] })
          });
          if (!isCurrent()) return;
          for (const alg of (details.resolved?.algorithms || [])) names.add(alg.name);
        }
        if (!isCurrent()) return;
        renderAlgorithmChoices(names);
      } catch (err) {
        if (!isCurrent()) return;
        state.algorithmLoading = false;
        state.algorithmLoadingFor = '';
        const list = document.getElementById('algorithm-list');
        list.innerHTML = '<div class="csv-empty status-bad">Algorithm loading failed.</div>';
        renderSubmitButton();
        throw err;
      }
    }
    async function submitExperiment(force = false) {
      if (!state.selected) return;
      if (state.algorithmLoading) {
        out('Wait for algorithm loading to finish before submitting.');
        renderSubmitButton();
        return;
      }
      if (state.submitLock?.locked) {
        renderSubmitButton();
        return;
      }
      const selectedAlgorithms = Array.from(document.querySelectorAll('#algorithm-list input:checked')).map(item => item.value);
      if (state.algorithms.length && selectedAlgorithms.length === 0) {
        out('Select at least one algorithm.');
        return;
      }
      const algorithms = selectedAlgorithms.length === state.algorithms.length ? [] : selectedAlgorithms;
      state.submitBusy = true;
      renderSubmitButton();
      try {
        const action = await api(`/api/experiments/${encodeURIComponent(state.selected)}/submit`, {
          method: 'POST',
          body: JSON.stringify({ algorithms, force })
        });
        const completed = await watchAction(action.id);
        if (!force && completed?.status === 'completed' && completed.result?.blocked === 'check failed') {
          state.submitBusy = false;
          renderSubmitButton();
          if (confirm('mkexp2 check failed. Submit anyway?')) {
            await submitExperiment(true);
          }
          return;
        }
      } finally {
        state.submitBusy = false;
        renderSubmitButton();
      }
      await refreshSubmitLock();
      await loadProgress({ quiet: true }).catch(() => {});
    }
    async function parseExperiment() {
      if (!state.selected) return;
      await withBusyButton('parse-results', 'Parsing...', async () => {
        const action = await api(`/api/experiments/${encodeURIComponent(state.selected)}/parse`, {
          method: 'POST',
          body: JSON.stringify({})
        });
        const completed = await watchAction(action.id);
        if (completed?.status === 'completed' && completed.result?.parsed) {
          await loadResults();
          await loadStats();
        }
      });
    }
    function sourceKey(source) {
      if (source.kind === 'algorithm') return `algorithm:${source.name}`;
      return `csv:${source.experiment_id}:${source.file}:${source.alias || ''}`;
    }
    function selectedPlotSourceObjects() {
      const current = state.plotSources?.current || [];
      const all = [...current, ...state.externalPlotSources];
      return all.filter(source => state.selectedPlotSources.has(sourceKey(source)));
    }
    function plotById(id) {
      return (state.plotCatalog?.plots || []).find(plot => plot.id === id) || null;
    }
    function suggestedPlotLabel() {
      const selectedPlots = Array.from(state.selectedPlotTypes).map(plotById).filter(Boolean);
      const sources = selectedPlotSourceObjects();
      if (!selectedPlots.length || !sources.length) return '';
      const sourceText = sources.map(source => source.alias || source.name || source.file).join(', ');
      if (selectedPlots.length === 1) return `${selectedPlots[0].name} - ${sourceText}`;
      return `Plot set - ${sourceText}`;
    }
    function syncPlotLabelSuggestion() {
      const input = document.getElementById('plot-label');
      if (!input) return;
      if (!state.plotLabelTouched) input.value = suggestedPlotLabel();
    }
    function validatePlotSelection() {
      const plots = Array.from(state.selectedPlotTypes).map(plotById).filter(Boolean);
      const sourceCount = selectedPlotSourceObjects().length;
      if (!plots.length) return 'Select at least one plot type.';
      if (!sourceCount) return 'Select at least one CSV source.';
      for (const plot of plots) {
        if (sourceCount < Number(plot.min_sources || 0)) return `${plot.name} requires at least ${plot.min_sources} source(s).`;
        if (plot.max_sources !== null && plot.max_sources !== undefined && sourceCount > Number(plot.max_sources)) {
          return `${plot.name} accepts at most ${plot.max_sources} source(s).`;
        }
      }
      return '';
    }
    function renderPlotCatalog() {
      const box = document.getElementById('plot-catalog');
      if (!box) return;
      const plots = state.plotCatalog?.plots || [];
      if (!plots.length) {
        box.className = 'csv-empty';
        box.textContent = 'No plot types loaded.';
        return;
      }
      if (!state.plotCatalogInitialized) {
        state.selectedPlotTypes = new Set(plots.filter(plot => plot.default_selected).map(plot => plot.id));
        state.plotCatalogInitialized = true;
      }
      box.className = 'plot-artifact-list';
      box.innerHTML = '';
      for (const plot of plots) {
        const label = document.createElement('label');
        label.className = 'plot-choice' + (plot.expensive ? ' expensive' : '');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = state.selectedPlotTypes.has(plot.id);
        checkbox.onchange = () => {
          if (checkbox.checked) state.selectedPlotTypes.add(plot.id);
          else state.selectedPlotTypes.delete(plot.id);
          syncPlotLabelSuggestion();
          renderPlotPanel();
        };
        const body = document.createElement('div');
        const title = document.createElement('div');
        title.className = 'plot-choice-title';
        title.textContent = plot.name;
        const desc = document.createElement('div');
        desc.className = 'plot-choice-desc';
        const maxText = plot.max_sources === null || plot.max_sources === undefined ? 'any' : plot.max_sources;
        desc.textContent = `${plot.description} Sources: ${plot.min_sources}-${maxText}.${plot.expensive ? ' Expensive.' : ''}`;
        body.appendChild(title);
        body.appendChild(desc);
        label.appendChild(checkbox);
        label.appendChild(body);
        box.appendChild(label);
      }
    }
    function renderPlotSources() {
      const box = document.getElementById('plot-sources');
      if (!box) return;
      const current = state.plotSources?.current || [];
      const sources = [...current, ...state.externalPlotSources];
      if (!sources.length) {
        box.className = 'csv-empty';
        box.textContent = 'No CSV results found. Run Parse Logs first or add a CSV from another experiment.';
        return;
      }
      box.className = 'plot-artifact-list';
      box.innerHTML = '';
      for (const source of sources) {
        const key = sourceKey(source);
        const row = document.createElement('label');
        row.className = 'plot-source-row';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = state.selectedPlotSources.has(key);
        checkbox.onchange = () => {
          if (checkbox.checked) state.selectedPlotSources.add(key);
          else state.selectedPlotSources.delete(key);
          syncPlotLabelSuggestion();
          renderPlotPanel();
        };
        const body = document.createElement('div');
        const title = document.createElement('div');
        title.className = 'plot-source-title';
        title.textContent = source.alias || source.name || source.file;
        const meta = document.createElement('div');
        meta.className = 'plot-source-meta';
        const sourceFile = source.file || `${source.name}.csv`;
        meta.textContent = source.kind === 'algorithm'
          ? `${sourceFile}, ${formatBytes(source.size)}`
          : `${source.experiment_id}/${source.file}, ${formatBytes(source.size)}`;
        body.appendChild(title);
        body.appendChild(meta);
        if (source.kind === 'csv') {
          const alias = document.createElement('input');
          alias.className = 'plot-source-alias';
          alias.value = source.alias || '';
          alias.onchange = () => {
            const wasSelected = state.selectedPlotSources.has(key);
            source.alias = alias.value.trim() || `${source.experiment_id}/${source.name || csvLabel(source.file)}`;
            state.selectedPlotSources.delete(key);
            if (wasSelected) state.selectedPlotSources.add(sourceKey(source));
            syncPlotLabelSuggestion();
            renderPlotPanel();
          };
          body.appendChild(alias);
        }
        row.appendChild(checkbox);
        row.appendChild(body);
        box.appendChild(row);
      }
    }
    function renderPlotArtifacts() {
      const box = document.getElementById('plot-artifacts');
      if (!box) return;
      const artifacts = state.plotArtifacts?.artifacts || [];
      if (!artifacts.length) {
        box.className = 'csv-empty';
        box.textContent = state.plotArtifacts?.legacy?.exists
          ? 'No managed artifacts yet. Legacy plots.pdf is still available below.'
          : 'No managed artifacts yet.';
        return;
      }
      if (!state.selectedPlotArtifact || !artifacts.find(item => item.id === state.selectedPlotArtifact)) {
        state.selectedPlotArtifact = artifacts[0].id;
      }
      box.className = 'plot-artifact-list';
      box.innerHTML = '';
      for (const artifact of artifacts) {
        const button = document.createElement('button');
        button.className = 'plot-artifact-row' + (state.selectedPlotArtifact === artifact.id ? ' active' : '');
        const body = document.createElement('div');
        const title = document.createElement('div');
        title.className = 'plot-artifact-title';
        title.textContent = artifact.label || artifact.plot_name || artifact.id;
        const meta = document.createElement('div');
        meta.className = 'plot-artifact-meta';
        const sources = (artifact.sources || []).map(source => source.alias || source.name || source.file).join(', ');
        meta.textContent = `${artifact.plot_name || artifact.plot_id}; ${sources}; ${formatBytes(artifact.size)}; ${artifact.created_at || artifact.modified_at || ''}`;
        body.appendChild(title);
        body.appendChild(meta);
        const open = document.createElement('span');
        open.textContent = 'Open';
        button.appendChild(body);
        button.appendChild(open);
        button.onclick = () => {
          state.selectedPlotArtifact = artifact.id;
          clearPlotPdfUrl();
          renderPlotPanel();
        };
        box.appendChild(button);
      }
    }
    function renderSelectedPlotArtifact() {
      const file = document.getElementById('plot-file');
      if (!file) return;
      const artifacts = state.plotArtifacts?.artifacts || [];
      const artifact = artifacts.find(item => item.id === state.selectedPlotArtifact);
      if (!artifact) {
        if (state.plotArtifacts?.legacy?.exists) {
          renderLegacyPlotPdf();
          return;
        }
        file.className = 'csv-empty';
        file.textContent = 'Generate a plot artifact to preview it here.';
        return;
      }
      const pdfUrl = `/api/experiments/${encodeURIComponent(state.selected)}/plot-artifacts/${encodeURIComponent(artifact.id)}.pdf`;
      const version = encodeURIComponent(`${artifact.modified_at || ''}-${artifact.size || ''}`);
      if (state.plotPdfUrlFor === artifact.id && state.plotPdfVersion === version && state.plotPdfUrl) {
        file.className = 'plot-preview';
        file.innerHTML = `
          <iframe class="plot-pdf" src="${esc(state.plotPdfUrl)}" title="${esc(artifact.label || artifact.id)}"></iframe>
          <div class="csv-summary"><a href="${esc(state.plotPdfUrl)}" target="_blank" rel="noreferrer">Open ${esc(artifact.label || artifact.id)}</a></div>
        `;
      } else {
        file.className = 'csv-empty';
        file.textContent = 'Loading plot artifact...';
        loadPlotPdf(pdfUrl, version, artifact.id).catch(err => {
          file.className = 'csv-empty status-bad';
          file.textContent = `Could not load plot artifact: ${err.message || err}`;
        });
      }
    }
    function renderLegacyPlotPdf() {
      const file = document.getElementById('plot-file');
      const legacy = state.plotArtifacts?.legacy;
      if (!file || !legacy?.exists) return;
      const pdfUrl = `/api/experiments/${encodeURIComponent(state.selected)}/plots.pdf`;
      const version = encodeURIComponent(`${legacy.modified_at || ''}-${legacy.size || ''}`);
      if (state.plotPdfUrlFor === 'legacy' && state.plotPdfVersion === version && state.plotPdfUrl) {
        file.className = 'plot-preview';
        file.innerHTML = `
          <iframe class="plot-pdf" src="${esc(state.plotPdfUrl)}" title="plots.pdf"></iframe>
          <div class="csv-summary"><a href="${esc(state.plotPdfUrl)}" target="_blank" rel="noreferrer">Open legacy plots.pdf</a></div>
        `;
      } else {
        file.className = 'csv-empty';
        file.textContent = 'Loading legacy plots.pdf...';
        loadPlotPdf(pdfUrl, version, 'legacy').catch(err => {
          file.className = 'csv-empty status-bad';
          file.textContent = `Could not load legacy plots.pdf: ${err.message || err}`;
        });
      }
    }
    function renderPlotPanel(action = null) {
      const summary = document.getElementById('plots-summary');
      if (!summary) return;
      applyPlotBackendStatus();
      renderPlotCatalog();
      renderPlotSources();
      renderPlotArtifacts();
      syncPlotLabelSuggestion();
      const error = validatePlotSelection();
      const button = document.getElementById('plot-results');
      if (button && button.dataset.busy !== '1') {
        button.disabled = Boolean(error) || !state.selected;
        button.title = error || 'Generate selected plot artifacts';
      }
      if (!state.selected) {
        summary.textContent = 'No experiment selected.';
        return;
      }
      const actionOutput = document.getElementById('plot-action-output');
      if (action?.status === 'running') {
        if (actionOutput) actionOutput.innerHTML = '';
      } else if (action) {
        renderActionStatus('plot-action-output', 'Plot generation', action, 'plot-artifacts');
      }
      summary.textContent = action?.status === 'running'
        ? 'Plot generation is running.'
        : action?.status === 'completed'
          ? (actionSucceeded(action, 'plot-artifacts') ? 'Plot generation completed.' : 'Plot generation failed.')
          : (state.plotArtifactsFor === state.selected
              ? `${(state.plotArtifacts?.artifacts || []).length} managed artifact(s).`
              : 'Loading plot artifacts...');
      renderSelectedPlotArtifact();
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
      state.plotArtifacts = await api(`/api/experiments/${encodeURIComponent(state.selected)}/plot-artifacts`);
      state.plotArtifactsFor = state.selected;
      renderPlotPanel();
      return state.plotArtifacts;
    }
    async function loadPlotPdf(pdfUrl, version, owner = state.selectedPlotArtifact || 'legacy') {
      if (!state.selected) return null;
      const selected = state.selected;
      const blob = await fetchBlob(`${pdfUrl}?v=${version}`);
      if (state.selected !== selected) return null;
      clearPlotPdfUrl();
      state.plotPdfUrl = URL.createObjectURL(blob);
      state.plotPdfUrlFor = owner;
      state.plotPdfVersion = version;
      renderPlotPanel();
      return state.plotPdfUrl;
    }
    async function loadPlotCatalog() {
      state.plotCatalog = await api('/api/plots/catalog');
      renderPlotPanel();
      return state.plotCatalog;
    }
    async function loadPlotSources(includeAll = false) {
      if (!state.selected) return null;
      const query = includeAll ? '?all=1' : '';
      const data = await api(`/api/experiments/${encodeURIComponent(state.selected)}/plot-sources${query}`);
      if (includeAll) return data;
      state.plotSources = data;
      state.plotSourcesFor = state.selected;
      if (state.plotSourcesInitializedFor !== state.selected) {
        state.selectedPlotSources = new Set((data.current || []).map(sourceKey));
        state.plotSourcesInitializedFor = state.selected;
      }
      renderPlotPanel();
      return data;
    }
    function addExternalPlotSource(experiment, file) {
      const source = Object.assign({}, file, {
        kind: 'csv',
        alias: file.alias || `${experiment.id}/${csvLabel(file.file)}`
      });
      const key = sourceKey(source);
      if (!state.externalPlotSources.find(item => sourceKey(item) === key)) {
        state.externalPlotSources.push(source);
      }
      state.selectedPlotSources.add(key);
      syncPlotLabelSuggestion();
      renderPlotPanel();
    }
    function renderPlotSourceExperiment(container, experiment) {
      const section = document.createElement('section');
      section.className = 'plot-source-modal-exp';
      const title = document.createElement('div');
      title.className = 'plot-artifact-title';
      title.textContent = experiment.label || experiment.name || experiment.id;
      title.title = experiment.id;
      section.appendChild(title);
      if (experiment.id !== title.textContent) {
        const meta = document.createElement('div');
        meta.className = 'plot-artifact-meta';
        meta.textContent = experiment.id;
        section.appendChild(meta);
      }
      const files = document.createElement('div');
      files.className = 'plot-source-modal-files';
      for (const file of experiment.files || []) {
        const button = document.createElement('button');
        button.className = 'small-button';
        button.textContent = csvLabel(file.file);
        button.title = `${experiment.id}/${file.file}`;
        button.onclick = () => addExternalPlotSource(experiment, file);
        files.appendChild(button);
      }
      section.appendChild(files);
      container.appendChild(section);
    }
    function renderPlotSourceTree(container, node, prefix = '') {
      const folders = Array.from(node.folders.entries()).sort((left, right) => left[0].localeCompare(right[0]));
      for (const [name, child] of folders) {
        const id = prefix ? `${prefix}/${name}` : name;
        const details = document.createElement('details');
        details.className = 'experiment-folder plot-source-folder';
        details.open = state.plotSourceOpenDirs.has(id);
        details.addEventListener('toggle', () => {
          if (details.open) state.plotSourceOpenDirs.add(id);
          else state.plotSourceOpenDirs.delete(id);
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
        renderPlotSourceTree(children, child, id);
        details.appendChild(summary);
        details.appendChild(children);
        container.appendChild(details);
      }
      const experiments = Array.from(node.experiments).sort((left, right) => left.label.localeCompare(right.label));
      for (const experiment of experiments) {
        renderPlotSourceExperiment(container, experiment);
      }
    }
    async function openPlotSourceDialog() {
      if (!state.selected) return;
      const modal = document.getElementById('plot-source-modal');
      const list = document.getElementById('plot-source-modal-list');
      const summary = document.getElementById('plot-source-modal-summary');
      modal.classList.remove('hidden');
      list.className = 'plot-source-modal-list csv-empty';
      list.textContent = 'Loading CSV files...';
      const data = await loadPlotSources(true);
      const experiments = data.experiments || [];
      summary.textContent = `${experiments.length} experiment(s) with CSV results.`;
      list.className = 'plot-source-modal-list';
      list.innerHTML = '';
      if (!experiments.length) {
        list.className = 'plot-source-modal-list csv-empty';
        list.textContent = 'No CSV files found in other experiments.';
        return;
      }
      renderPlotSourceTree(list, experimentTree(experiments));
    }
    function closePlotSourceDialog() {
      document.getElementById('plot-source-modal').classList.add('hidden');
    }
    async function plotExperiment() {
      if (!state.selected) return;
      setView('plots-view').catch(err => out(String(err)));
      applyPlotBackendStatus();
      const error = validatePlotSelection();
      if (error) {
        out(error);
        renderPlotPanel();
        return;
      }
      await withBusyButton('plot-results', 'Generating...', async () => {
        const noDocker = document.getElementById('plot-no-docker')?.checked || false;
        const label = document.getElementById('plot-label')?.value || '';
        const action = await api(`/api/experiments/${encodeURIComponent(state.selected)}/plot-artifacts`, {
          method: 'POST',
          body: JSON.stringify({
            no_docker: noDocker,
            plots: Array.from(state.selectedPlotTypes),
            sources: selectedPlotSourceObjects(),
            label
          })
        });
        renderPlotPanel({ status: 'running', id: action.id });
        const completed = await watchAction(action.id, current => renderPlotPanel(current));
        if (completed?.status === 'completed' && completed.result?.plotted) {
          await new Promise(resolve => setTimeout(resolve, PLOT_RELOAD_DELAY_MS));
          await loadPlotInfo();
        }
      });
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
    document.getElementById('refresh-status').onclick = () => withBusyButton('refresh-status', '', refreshStatus).catch(err => out(String(err)));
    document.getElementById('queue-open').onclick = () => withBusyButton('queue-open', '', openQueueDialog).catch(err => out(String(err)));
    document.getElementById('queue-close').onclick = closeQueueDialog;
    document.getElementById('queue-refresh').onclick = () => withBusyButton('queue-refresh', '', loadQueue).catch(err => out(String(err)));
    document.getElementById('create-open').onclick = () => withBusyButton('create-open', '', openCreateDialog).catch(err => out(String(err)));
    document.getElementById('create-close').onclick = closeCreateDialog;
    document.getElementById('create-cancel').onclick = closeCreateDialog;
    document.getElementById('create-submit').onclick = createExperiment;
    document.getElementById('create-name').oninput = updateCreatePreview;
    document.getElementById('create-template').oninput = updateCreatePreview;
    document.getElementById('create-template-override').onchange = updateCreatePreview;
    document.getElementById('archive-open').onclick = () => withBusyButton('archive-open', '', openArchiveDialog).catch(err => out(String(err)));
    document.getElementById('archive-close').onclick = closeArchiveDialog;
    document.getElementById('archive-refresh').onclick = () => withBusyButton('archive-refresh', '', () => loadArchivedExperiments({ force: true })).catch(err => out(String(err)));
    document.getElementById('git-open').onclick = () => withBusyButton('git-open', '', openGitDialog).catch(err => out(String(err)));
    document.getElementById('git-close').onclick = closeGitDialog;
    document.getElementById('git-refresh').onclick = () => withBusyButton('git-refresh', '', loadGitStatus).catch(err => out(String(err)));
    document.getElementById('git-push').onclick = pushGitChanges;
    document.getElementById('share-experiment').onclick = shareExperiment;
    document.getElementById('download-experiment').onclick = downloadExperiment;
    document.getElementById('share-close').onclick = closeShareDialog;
    document.getElementById('settings-open').onclick = openSettingsDialog;
    document.getElementById('settings-close').onclick = closeSettingsDialog;
    document.getElementById('spack-cache-refresh').onclick = () => refreshSpackCache().catch(err => out(String(err)));
    document.getElementById('console-clear').onclick = clearConsoleLog;
    document.getElementById('check').onclick = checkExperiment;
    document.getElementById('probe-run').onclick = probeExperiment;
    document.getElementById('description-edit').onclick = editDescription;
    document.getElementById('description-cancel').onclick = cancelDescriptionEdit;
    document.getElementById('description-save').onclick = saveDescription;
    document.getElementById('submit').onclick = submitExperiment;
    document.getElementById('clear-submit-lock').onclick = clearSubmitLock;
    document.getElementById('rename-experiment').onclick = renameExperiment;
    document.getElementById('archive-experiment').onclick = archiveExperiment;
    document.getElementById('delete-experiment').onclick = deleteExperiment;
    document.getElementById('refresh-progress').onclick = () => withBusyButton('refresh-progress', '', () => loadProgress()).catch(err => out(String(err)));
    document.getElementById('parse-results').onclick = parseExperiment;
    document.getElementById('plot-results').onclick = plotExperiment;
    document.getElementById('add-plot-source').onclick = () => withBusyButton('add-plot-source', 'Loading...', openPlotSourceDialog).catch(err => out(String(err)));
    document.getElementById('plot-source-close').onclick = closePlotSourceDialog;
    document.getElementById('plot-source-close-footer').onclick = closePlotSourceDialog;
    document.getElementById('plot-label').oninput = () => {
      state.plotLabelTouched = true;
    };
    document.getElementById('plot-no-docker').onchange = () => {
      state.plotNoDockerTouched = true;
    };
    document.getElementById('load-results').onclick = () => withBusyButton('load-results', '', loadResults).catch(err => out(String(err)));
    document.getElementById('load-stats').onclick = () => withBusyButton('load-stats', '', loadStats).catch(err => out(String(err)));
    document.getElementById('load-install-log').onclick = () => withBusyButton('load-install-log', '', loadInstallLog).catch(err => out(String(err)));
    document.getElementById('reload-logs').onclick = () => withBusyButton('reload-logs', '', () => loadLogs(state.logsDir || '')).catch(err => out(String(err)));
    document.querySelectorAll('.view-tab').forEach(button => {
      button.onclick = () => withBusyButton(button, 'Loading...', () => setView(button.dataset.view)).catch(err => out(String(err)));
    });
    initSidebarResize();
    if (initialShareId) {
      selectSharedExperiment(initialShareId).catch(err => out(String(err)));
    } else if (token() || allowEmptyToken) {
      refreshConfig().catch(err => out(String(err)));
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

        def render_html(self, share_id=""):
            html = HTML.replace("__ALLOW_EMPTY_TOKEN__", "true" if app.allow_empty_token else "false")
            html = html.replace("__SHARE_ID__", json.dumps(str(share_id or "")))
            return html

        def require_token(self):
            supplied = self.headers.get("X-MKEXP2-Token", "")
            if app.allow_empty_token and supplied == "":
                return True
            return secrets.compare_digest(supplied, app.token)

        def handle_share_get(self, parsed):
            path = parsed.path
            match = re.match(r"^/api/share/([^/]+)(?:/(.*))?$", path)
            if not match:
                json_response(self, 404, {"error": "not found"})
                return
            share_id = urllib.parse.unquote(match.group(1))
            tail = match.group(2) or ""
            context = app.resolve_share(share_id)
            experiment_id = context["experiment_id"]
            query = urllib.parse.parse_qs(parsed.query)
            if tail in ("", "metadata"):
                json_response(self, 200, app.share_metadata(share_id))
                return
            if tail == "experiment":
                exp_path = context["path"]
                json_response(
                    self,
                    200,
                    {
                        "id": experiment_id,
                        "path": str(exp_path),
                        "experiment": (exp_path / "Experiment").read_text(encoding="utf-8"),
                        "submit_lock": app.submit_lock(experiment_id),
                        "read_only": True,
                    },
                )
                return
            if tail == "results":
                json_response(self, 200, app.results(experiment_id))
                return
            if tail == "progress":
                json_response(self, 200, app.progress(experiment_id))
                return
            if tail == "description":
                json_response(self, 200, app.description(experiment_id))
                return
            if tail == "plots":
                json_response(self, 200, app.plots_info(experiment_id))
                return
            if tail == "plot-sources":
                json_response(self, 200, app.plot_sources(experiment_id, include_all=False))
                return
            if tail == "plot-artifacts":
                json_response(self, 200, app.list_plot_artifacts(experiment_id))
                return
            if tail == "stats":
                json_response(self, 200, app.stats(experiment_id))
                return
            if tail == "install-log":
                json_response(self, 200, app.install_log(experiment_id))
                return
            if tail == "logs":
                limit = int((query.get("limit") or [MAX_LOG_LIST_ENTRIES])[0])
                offset = int((query.get("offset") or [0])[0])
                rel_dir = (query.get("dir") or [""])[0]
                json_response(self, 200, app.list_logs(experiment_id, rel_dir, limit=limit, offset=offset))
                return
            if tail == "log":
                rel_path = (query.get("path") or [""])[0]
                json_response(self, 200, app.log_file(experiment_id, rel_path))
                return
            if tail == "plot/backend":
                json_response(self, 200, app.plot_backend_status())
                return
            if tail == "plots/catalog":
                json_response(self, 200, app.plot_catalog())
                return
            match_pdf = re.match(r"^plot-artifacts/([^/]+)\.pdf$", tail)
            if match_pdf:
                pdf = app.plot_artifact_pdf(experiment_id, urllib.parse.unquote(match_pdf.group(1)))
                data = pdf.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mimetypes.types_map.get(".pdf", "application/pdf"))
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if tail == "plots.pdf":
                pdf = context["path"] / "plots.pdf"
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
            if tail == "download":
                archive = app.experiment_archive(experiment_id)
                archive_path = archive["path"]
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", archive["content_type"])
                    self.send_header("Content-Length", str(archive_path.stat().st_size))
                    self.send_header("Content-Disposition", f"attachment; filename=\"{archive['filename']}\"")
                    self.end_headers()
                    with archive_path.open("rb") as file:
                        shutil.copyfileobj(file, self.wfile)
                finally:
                    archive_path.unlink(missing_ok=True)
                return
            match_action = re.match(r"^actions/([^/]+)$", tail)
            if match_action:
                action = app.actions.get(urllib.parse.unquote(match_action.group(1)))
                if not action:
                    json_response(self, 404, {"error": "action not found"})
                else:
                    json_response(self, 200, action)
                return
            json_response(self, 404, {"error": "not found"})

        def handle_share_post(self, parsed):
            path = parsed.path
            match = re.match(r"^/api/share/([^/]+)/(parse|plot-artifacts|probe)$", path)
            if not match:
                json_response(self, 404, {"error": "not found"})
                return
            share_id = urllib.parse.unquote(match.group(1))
            action = match.group(2)
            experiment_id = app.resolve_share(share_id)["experiment_id"]
            payload = read_json(self)
            if action == "parse":
                json_response(self, 202, app.parse_action(experiment_id))
                return
            if action == "probe":
                json_response(self, 200, app.probe_payload(experiment_id, payload))
                return
            json_response(self, 202, app.create_shared_plot_artifacts_action(experiment_id, payload))

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            try:
                if path == "/":
                    text_response(self, 200, self.render_html(), "text/html; charset=utf-8")
                    return
                match = re.match(r"^/share/([^/]+)$", path)
                if match:
                    share_id = urllib.parse.unquote(match.group(1))
                    app.resolve_share(share_id)
                    text_response(self, 200, self.render_html(share_id), "text/html; charset=utf-8")
                    return
                if path.startswith("/api/share/"):
                    self.handle_share_get(parsed)
                    return
                if path.startswith("/api/") and not self.require_token():
                    json_response(self, 401, {"error": "missing or invalid token"})
                    return
                if path == "/api/status/slurm":
                    json_response(self, 200, app.slurm.get())
                    return
                if path == "/api/status/squeue":
                    json_response(self, 200, app.slurm.queue())
                    return
                if path == "/api/config":
                    json_response(self, 200, app.config())
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
                if path == "/api/plot/spack-r-libs":
                    json_response(self, 200, app.spack_plot_cache_info())
                    return
                if path == "/api/plots/catalog":
                    json_response(self, 200, app.plot_catalog())
                    return
                if path == "/api/experiments":
                    query = urllib.parse.parse_qs(parsed.query)
                    force = (query.get("refresh") or ["0"])[0] in ("1", "true", "yes")
                    json_response(self, 200, {"experiments": app.list_experiments(force=force)})
                    return
                if path == "/api/experiments/archived":
                    query = urllib.parse.parse_qs(parsed.query)
                    force = (query.get("refresh") or ["0"])[0] in ("1", "true", "yes")
                    json_response(self, 200, {"experiments": app.list_archived_experiments(force=force)})
                    return
                match = re.match(r"^/api/experiments/([^/]+)/experiment$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    exp_path = app.active_experiment_path(experiment_id)
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
                match = re.match(r"^/api/experiments/([^/]+)/description$", path)
                if match:
                    json_response(self, 200, app.description(urllib.parse.unquote(match.group(1))))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/plots$", path)
                if match:
                    json_response(self, 200, app.plots_info(urllib.parse.unquote(match.group(1))))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/plot-sources$", path)
                if match:
                    query = urllib.parse.parse_qs(parsed.query)
                    include_all = (query.get("all") or ["0"])[0] in ("1", "true", "yes")
                    json_response(self, 200, app.plot_sources(urllib.parse.unquote(match.group(1)), include_all=include_all))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/plot-artifacts$", path)
                if match:
                    json_response(self, 200, app.list_plot_artifacts(urllib.parse.unquote(match.group(1))))
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
                    exp_path = app.active_experiment_path(urllib.parse.unquote(match.group(1)))
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
                match = re.match(r"^/api/experiments/([^/]+)/download$", path)
                if match:
                    archive = app.experiment_archive(urllib.parse.unquote(match.group(1)))
                    archive_path = archive["path"]
                    try:
                        self.send_response(200)
                        self.send_header("Content-Type", archive["content_type"])
                        self.send_header("Content-Length", str(archive_path.stat().st_size))
                        self.send_header("Content-Disposition", f"attachment; filename=\"{archive['filename']}\"")
                        self.end_headers()
                        with archive_path.open("rb") as file:
                            shutil.copyfileobj(file, self.wfile)
                    finally:
                        archive_path.unlink(missing_ok=True)
                    return
                match = re.match(r"^/api/experiments/([^/]+)/plot-artifacts/([^/]+)\.pdf$", path)
                if match:
                    pdf = app.plot_artifact_pdf(urllib.parse.unquote(match.group(1)), urllib.parse.unquote(match.group(2)))
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
                if path.startswith("/api/share/"):
                    self.handle_share_post(parsed)
                    return
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
                if path == "/api/status/squeue/cancel":
                    json_response(self, 200, app.slurm.cancel_job(payload))
                    return
                if path == "/api/plot/spack-r-libs/resolve":
                    force = bool(payload.get("force", False))
                    json_response(self, 202, app.resolve_spack_plot_cache_action(force=force))
                    return
                if path == "/api/pins":
                    json_response(self, 200, app.write_pins(payload.get("pinned") or []))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/rename$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    json_response(self, 200, app.rename_experiment(experiment_id, payload))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/share$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    json_response(self, 200, app.share_experiment(experiment_id))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/(archive|unarchive)$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    action = match.group(2)
                    if action == "archive":
                        json_response(self, 200, app.archive_experiment(experiment_id))
                    else:
                        json_response(self, 200, app.unarchive_experiment(experiment_id))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/(check|probe|submit|parse|plot)$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    action = match.group(2)
                    if action == "check":
                        json_response(self, 200, app.command(experiment_id, ["check", "--json"], timeout=60))
                        return
                    if action == "probe":
                        json_response(self, 200, app.probe_payload(experiment_id, payload))
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
                match = re.match(r"^/api/experiments/([^/]+)/plot-artifacts$", path)
                if match:
                    json_response(self, 202, app.create_plot_artifacts_action(urllib.parse.unquote(match.group(1)), payload))
                    return
                json_response(self, 404, {"error": "not found"})
            except Exception as exc:
                json_response(self, 400, {"error": str(exc)})

        def do_PUT(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            try:
                if path.startswith("/api/share/"):
                    json_response(self, 404, {"error": "not found"})
                    return
                if path.startswith("/api/") and not self.require_token():
                    json_response(self, 401, {"error": "missing or invalid token"})
                    return
                payload = read_json(self)
                if path == "/api/pins":
                    json_response(self, 200, app.write_pins(payload.get("pinned") or []))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/description$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    json_response(self, 200, app.write_description(experiment_id, payload.get("description", "")))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/experiment$", path)
                if not match:
                    json_response(self, 404, {"error": "not found"})
                    return
                experiment_id = urllib.parse.unquote(match.group(1))
                exp_path = app.active_experiment_path(experiment_id)
                (exp_path / "Experiment").write_text(payload.get("experiment", ""), encoding="utf-8")
                json_response(self, 200, {"saved": True, "id": experiment_id})
            except Exception as exc:
                json_response(self, 400, {"error": str(exc)})

        def do_DELETE(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            try:
                if path.startswith("/api/share/"):
                    json_response(self, 404, {"error": "not found"})
                    return
                if path.startswith("/api/") and not self.require_token():
                    json_response(self, 401, {"error": "missing or invalid token"})
                    return
                match = re.match(r"^/api/experiments/([^/]+)/submit-lock$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    json_response(self, 200, app.clear_submit_lock(experiment_id))
                    return
                match = re.match(r"^/api/experiments/([^/]+)$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    json_response(self, 200, app.delete_experiment(experiment_id))
                    return
                json_response(self, 404, {"error": "not found"})
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
    parser.add_argument("--token", default="")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        raise SystemExit(f"repo does not exist: {repo}")
    if shutil.which("git") is None:
        raise SystemExit("git not found")
    git_probe = run_command(["git", "rev-parse", "--show-toplevel"], cwd=repo, timeout=10)
    if git_probe["returncode"] != 0:
        raise SystemExit(f"repo is not a Git repository: {repo}")

    token = args.token or secrets.token_urlsafe(24)
    app = Mkexp2WebApp(
        repo,
        args.mkexp2,
        args.name_template,
        token,
        allow_empty_token=args.allow_empty_token,
        web_host=args.host,
        web_port=args.port,
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    print(f"mkexp2 web: http://{args.host}:{args.port}", flush=True)
    print(f"session token: {token}", flush=True)
    if args.allow_empty_token:
        print("empty token bypass: enabled", flush=True)
    print(f"ssh tunnel: ssh -L {args.port}:{args.host}:{args.port} <user>@<cluster-login>", flush=True)
    print(f"spack R library cache warmup action: {app.warm_spack_plot_cache()}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
