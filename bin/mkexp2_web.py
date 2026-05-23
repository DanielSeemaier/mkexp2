#!/usr/bin/env python3
import argparse
import csv
import datetime as _dt
import getpass
import html
import io
import json
import mimetypes
import os
import re
import secrets
import shlex
import shutil
import signal
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
SQUEUE_NODE_FORMAT = "%i|%N|%u|%j|%T|%S|%M"
SQUEUE_TABLE_FORMAT = "%i|%P|%j|%u|%T|%M|%D|%R"
WEB_STATE_DIR = ".mkexp2"
WEB_PINS_FILE = "web-pins.json"
WEB_SHARES_FILE = "web-shares.json"
WEB_TAGS_FILE = "web-tags.json"
WEB_COLUMNS_FILE = "web-column-visibility.json"
WEB_SETTINGS_FILE = "web-settings.json"
WEB_WORKSPACES_FILE = "web-workspaces.json"
PLOT_INDEX_FILE = "index.json"
ARCHIVE_SUFFIX = ".archived"
EXPERIMENT_SKIP_DIRS = {".git", ".mkexp2", "jobs", "logs", "plots", "results", "slurm"}
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TAG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}$")
TAG_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
TAG_COLOR_PALETTE = [
    {"name": "Blue", "color": "#2563eb"},
    {"name": "Teal", "color": "#0f766e"},
    {"name": "Green", "color": "#16a34a"},
    {"name": "Amber", "color": "#d97706"},
    {"name": "Red", "color": "#dc2626"},
    {"name": "Purple", "color": "#7c3aed"},
    {"name": "Pink", "color": "#db2777"},
    {"name": "Slate", "color": "#64748b"},
]
DEFAULT_TAGS = [{"name": "Codex", "color": TAG_COLOR_PALETTE[0]["color"]}]
DEFAULT_TAG_NAMES = {tag["name"] for tag in DEFAULT_TAGS}
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


def normalize_tag_name(value):
    name = str(value or "").strip()
    if not name:
        return ""
    if not TAG_NAME_RE.fullmatch(name):
        raise ValueError("invalid tag name")
    return name


def normalize_tag_color(value):
    color = str(value or "").strip()
    if not TAG_COLOR_RE.fullmatch(color):
        raise ValueError("invalid tag color")
    return color.lower()


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


def archive_include_dirs_from_query(query):
    if (query.get("select") or ["0"])[0] in ("1", "true", "yes"):
        return query.get("dir") or []
    return None


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


def display_experiment_function_name(function_name):
    value = str(function_name or "")
    if value.startswith("Experiment") and len(value) > len("Experiment"):
        value = value[len("Experiment") :]
    return value.replace("_", " ") or str(function_name or "Experiment")


def percent(done, total):
    return int(done * 100 / total) if total else 0


def run_command(argv, cwd=None, timeout=60):
    started = time.time()
    try:
        proc = subprocess.run(
            list(argv),
            cwd=str(cwd) if cwd else None,
            text=True,
            stdin=subprocess.DEVNULL,
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


def run_command_with_input(argv, input_text, cwd=None, timeout=60):
    started = time.time()
    try:
        proc = subprocess.run(
            list(argv),
            cwd=str(cwd) if cwd else None,
            text=True,
            input=input_text,
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


def lock_values(lock, key):
    values = []
    for line in str(lock.get("content") or "").splitlines():
        if line.startswith(f"{key}="):
            values.append(line.split("=", 1)[1])
    fields = lock.get("fields") or {}
    if key in fields and fields[key] not in values:
        values.append(fields[key])
    return values


def unique_ordered(values):
    seen = set()
    out = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def slurm_cancel_job_id(job_id):
    text = str(job_id or "").strip()
    return re.sub(r"%[A-Za-z0-9_.+\-]+(?=\])", "", text)


def process_exists(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def process_children():
    result = run_command(["ps", "-axo", "pid=,ppid="], timeout=8)
    children = {}
    if result["returncode"] != 0:
        return children
    for line in result["stdout"].splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)
    return children


def process_command(pid):
    result = run_command(["ps", "-p", str(pid), "-o", "command="], timeout=8)
    if result["returncode"] != 0:
        return ""
    return result["stdout"].strip()


def descendant_pids(root_pid):
    children = process_children()
    out = []
    stack = list(children.get(root_pid, []))
    while stack:
        pid = stack.pop()
        if pid in out:
            continue
        out.append(pid)
        stack.extend(children.get(pid, []))
    return out


def terminate_process_tree(root_pid):
    if root_pid <= 1 or root_pid == os.getpid():
        raise ValueError("refusing to terminate an unsafe process id")
    pids = descendant_pids(root_pid)
    targets = [pid for pid in reversed(pids) if pid != os.getpid()]
    if process_exists(root_pid):
        targets.append(root_pid)
    signaled = []
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
            signaled.append(pid)
        except ProcessLookupError:
            pass
    time.sleep(0.25)
    killed = []
    for pid in targets:
        if not process_exists(pid):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except ProcessLookupError:
            pass
    return {"root_pid": root_pid, "descendants": pids, "signaled": signaled, "killed": killed}


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


def _is_real_slurm_node_field(value):
    value = str(value or "").strip()
    if not value or value in ("(null)", "None", "N/A", "null"):
        return False
    if value.startswith("(") and value.endswith(")"):
        return False
    return True


def parse_squeue_jobs(text):
    jobs = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 7:
            continue
        job_id, nodes, user, name, state, start_time, elapsed = [part.strip() for part in parts[:7]]
        node_names = expand_nodelist(nodes) if _is_real_slurm_node_field(nodes) else []
        jobs.append(
            {
                "job_id": job_id,
                "nodes": nodes,
                "node_names": node_names,
                "user": user,
                "job_name": name,
                "state": state,
                "start_time": start_time,
                "elapsed": elapsed,
            }
        )
    return jobs


def _parse_squeue_delimited_line(line):
    parts = [part.strip() for part in line.split("|")]
    if len(parts) < 8:
        return None
    job_id, partition, name, user, state, elapsed, nodes, nodelist = parts[:8]
    return {
        "job_id": job_id,
        "partition": partition,
        "name": name,
        "user": user,
        "state": state,
        "time": elapsed,
        "time_limit": "",
        "nodes": nodes,
        "nodelist": nodelist,
    }


def _parse_squeue_table_line(line):
    parts = line.split()
    if len(parts) < 8:
        return None
    time_limit = ""
    if len(parts) >= 9 and not parts[6].isdigit():
        job_id, partition, name, user, state, elapsed, time_limit, nodes = parts[:8]
        nodelist = " ".join(parts[8:])
    else:
        job_id, partition, name, user, state, elapsed, nodes = parts[:7]
        nodelist = " ".join(parts[7:])
    return {
        "job_id": job_id,
        "partition": partition,
        "name": name,
        "user": user,
        "state": state,
        "time": elapsed,
        "time_limit": time_limit,
        "nodes": nodes,
        "nodelist": nodelist,
    }


def parse_squeue_table(text):
    rows = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("JOBID"):
            continue
        row = _parse_squeue_delimited_line(line) if "|" in line else _parse_squeue_table_line(line)
        if not row:
            continue
        rows.append(row)
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
            ["squeue", "-h", "-o", SQUEUE_NODE_FORMAT],
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
            if not attached and _is_real_slurm_node_field(job["nodes"]):
                name = job["nodes"]
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
        command = run_command(["squeue", "-h", "-o", SQUEUE_TABLE_FORMAT], timeout=8)
        source = f"squeue -h -o {SQUEUE_TABLE_FORMAT}"
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
        if not re.fullmatch(r"[A-Za-z0-9_.+\-\[\]%,]+", job_id):
            raise ValueError("invalid Slurm job id")
        cancel_id = slurm_cancel_job_id(job_id)

        owner = getpass.getuser()
        squeue = run_command(["squeue", "-h", "-o", SQUEUE_TABLE_FORMAT], timeout=8)
        if squeue["returncode"] != 0:
            raise ValueError("cannot verify Slurm job ownership before scancel")
        rows = parse_squeue_table(squeue["stdout"])
        job = next((row for row in rows if row["job_id"] == job_id), None)
        if not job:
            raise ValueError(f"Slurm job not found: {job_id}")
        if job.get("user") != owner:
            raise ValueError(f"refusing to cancel job {job_id}: owner is {job.get('user')}, server user is {owner}")

        scancel = run_command(["scancel", cancel_id], timeout=30)
        self._cache_until = 0
        if scancel["returncode"] != 0:
            raise ValueError(scancel["stderr"] or scancel["stdout"] or f"scancel failed for job {cancel_id}")
        return {
            "ok": scancel["returncode"] == 0,
            "job": job,
            "cancel_job_id": cancel_id,
            "server_user": owner,
            "verify": squeue,
            "scancel": scancel,
        }

    def cancel_user_jobs(self, payload):
        owner = getpass.getuser()
        if not re.fullmatch(r"[A-Za-z0-9_.@+\-]+", owner):
            raise ValueError("invalid server user")
        confirm_user = str((payload or {}).get("confirm_user") or "").strip()
        if confirm_user != owner:
            raise ValueError("confirmation did not match server user")

        scancel = run_command(["scancel", "-u", owner], timeout=30)
        self._cache_until = 0
        if scancel["returncode"] != 0:
            raise ValueError(scancel["stderr"] or scancel["stdout"] or f"scancel failed for user {owner}")
        return {
            "ok": scancel["returncode"] == 0,
            "server_user": owner,
            "scancel": scancel,
        }

    def cancel_job_ids(self, job_ids):
        owner = getpass.getuser()
        normalized = unique_ordered(job_ids)
        if not normalized:
            return {"ok": True, "server_user": owner, "jobs": [], "missing": [], "scancel": []}
        for job_id in normalized:
            if not re.fullmatch(r"[A-Za-z0-9_.+\-\[\]%,]+", job_id):
                raise ValueError(f"invalid Slurm job id: {job_id}")

        squeue = run_command(["squeue", "-h", "-o", SQUEUE_TABLE_FORMAT], timeout=8)
        if squeue["returncode"] != 0:
            raise ValueError("cannot verify Slurm job ownership before scancel")
        rows = parse_squeue_table(squeue["stdout"])
        rows_by_id = {row["job_id"]: row for row in rows}
        jobs = []
        missing = []
        scancels = []
        for job_id in normalized:
            job = rows_by_id.get(job_id)
            if not job:
                job = next((row for row in rows if str(row.get("job_id") or "").startswith(f"{job_id}_")), None)
            if not job:
                missing.append(job_id)
                continue
            if job.get("user") != owner:
                raise ValueError(f"refusing to cancel job {job_id}: owner is {job.get('user')}, server user is {owner}")
            cancel_id = slurm_cancel_job_id(job_id)
            scancel = run_command(["scancel", cancel_id], timeout=30)
            scancels.append(scancel)
            if scancel["returncode"] != 0:
                raise ValueError(scancel["stderr"] or scancel["stdout"] or f"scancel failed for job {cancel_id}")
            jobs.append(job)
        self._cache_until = 0
        return {
            "ok": True,
            "server_user": owner,
            "jobs": jobs,
            "missing": missing,
            "verify": squeue,
            "scancel": scancels,
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
        self.workspace_registry_path = self.repo / WEB_STATE_DIR / WEB_WORKSPACES_FILE
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
        self._repo_lock = threading.RLock()

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

    def experiment_time_metadata(self, path):
        path = Path(path)
        exp_file = path / "Experiment"
        dir_stat = path.stat()
        file_stat = exp_file.stat() if exp_file.is_file() else dir_stat
        created_at = stat_creation_time(dir_stat)
        return {
            "created_at": iso_from_timestamp(created_at),
            "created_at_epoch": created_at,
            "modified_at": iso_from_timestamp(file_stat.st_mtime),
        }

    def active_experiment_path(self, experiment_id):
        if self.is_archived_experiment_id(experiment_id):
            raise ValueError(f"experiment is archived: {experiment_id}")
        return self.experiment_path(experiment_id)

    def readable_experiment_path(self, experiment_id):
        experiment_id = str(experiment_id or "").strip().strip("/")
        path = self.experiment_path(experiment_id)
        if not path.is_dir() or not (path / "Experiment").is_file():
            raise ValueError(f"experiment not found: {experiment_id}")
        return path

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
            time_metadata = self.experiment_time_metadata(path)
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
                    "created_at": time_metadata["created_at"],
                    "created_at_epoch": time_metadata["created_at_epoch"],
                    "modified_at": time_metadata["modified_at"],
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
            return self.with_tag_summaries(self.with_submit_lock_summaries(self._experiments_cache))
        experiments = self._discover_experiments(archived=False)
        self._experiments_cache = experiments
        self._experiments_cache_at = now
        return self.with_tag_summaries(self.with_submit_lock_summaries(experiments))

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

    def experiment_subdirectories(self):
        directories = {}
        for experiment in self.list_experiments(force=True):
            parts = experiment["id"].split("/")[:-1]
            for index in range(1, len(parts) + 1):
                directory = "/".join(parts[:index])
                item = directories.setdefault(directory, {"id": directory, "count": 0, "latest": 0})
                item["count"] += 1
                item["latest"] = max(item["latest"], float(experiment.get("created_at_epoch") or 0))
        return {
            "directories": sorted(
                directories.values(),
                key=lambda item: (-item["latest"], item["id"].casefold()),
            )
        }

    def invalidate_experiments_cache(self):
        self._experiments_cache = None
        self._experiments_cache_at = 0.0
        self._archived_experiments_cache = None
        self._archived_experiments_cache_at = 0.0

    def workspace_path_from_payload(self, payload):
        if isinstance(payload, dict):
            value = payload.get("path")
        else:
            value = payload
        value = str(value or "").strip()
        if not value:
            raise ValueError("workspace directory is required")
        return Path(os.path.expandvars(os.path.expanduser(value))).resolve()

    def workspace_git_info(self, path):
        path = Path(path).resolve()
        if not path.is_dir():
            return {"git": False, "git_root": "", "error": "directory does not exist"}
        result = run_command(["git", "rev-parse", "--show-toplevel"], cwd=path, timeout=10)
        if result["returncode"] != 0:
            message = result["stderr"].strip() or result["stdout"].strip()
            return {"git": False, "git_root": "", "error": message or "not a Git repository"}
        git_root = Path(result["stdout"].strip()).resolve()
        return {"git": True, "git_root": str(git_root), "error": ""}

    def workspace_entry(self, path):
        path = Path(path).resolve()
        git_info = self.workspace_git_info(path)
        return {
            "path": str(path),
            "name": path.name or str(path),
            "active": path == self.repo,
            "exists": path.exists(),
            "directory": path.is_dir(),
            "git": bool(git_info["git"]),
            "git_root": git_info.get("git_root") or "",
            "valid": path.is_dir() and bool(git_info["git"]),
            "error": git_info.get("error") or "",
        }

    def _workspace_paths_from_payload(self, payload):
        if not isinstance(payload, dict):
            return []
        workspaces = payload.get("workspaces") or []
        if not isinstance(workspaces, list):
            raise ValueError("invalid workspaces JSON: workspaces is not an array")
        paths = []
        seen = set()
        for item in workspaces:
            value = item.get("path") if isinstance(item, dict) else item
            value = str(value or "").strip()
            if not value:
                continue
            path = Path(os.path.expandvars(os.path.expanduser(value))).resolve()
            key = str(path)
            if key in seen:
                continue
            paths.append(path)
            seen.add(key)
        return paths

    def read_workspace_paths(self):
        path = self.workspace_registry_path
        if not path.is_file():
            payload = {}
        else:
            try:
                payload = json.loads(path.read_text(encoding="utf-8") or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid workspaces JSON: {exc}") from exc
        paths = self._workspace_paths_from_payload(payload)
        if str(self.repo) not in {str(item) for item in paths}:
            paths.insert(0, self.repo)
        return paths

    def write_workspace_paths(self, paths):
        normalized = []
        seen = set()
        for item in paths:
            path = Path(item).resolve()
            key = str(path)
            if key in seen:
                continue
            normalized.append(path)
            seen.add(key)
        if str(self.repo) not in seen:
            normalized.insert(0, self.repo)
        payload = {
            "active": str(self.repo),
            "workspaces": [{"path": str(path)} for path in normalized],
        }
        path = self.workspace_registry_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        return normalized

    def list_workspaces(self):
        paths = self.read_workspace_paths()
        return {
            "active": str(self.repo),
            "registry": str(self.workspace_registry_path),
            "workspaces": [self.workspace_entry(path) for path in paths],
        }

    def _switch_workspace_locked(self, path, paths=None):
        path = Path(path).resolve()
        git_info = self.workspace_git_info(path)
        if not path.is_dir():
            raise ValueError(f"workspace directory does not exist: {path}")
        if not git_info["git"]:
            raise ValueError(f"workspace is not a Git repository: {path}")
        previous = self.repo
        paths = list(paths or self.read_workspace_paths())
        if str(path) not in {str(item) for item in paths}:
            paths.append(path)
        self.repo = path
        self.invalidate_experiments_cache()
        self._plot_backend_cache = None
        self._plot_backend_cache_at = 0.0
        self.write_workspace_paths(paths)
        result = self.list_workspaces()
        result.update(
            {
                "switched": True,
                "previous": str(previous),
                "repo": str(self.repo),
                "config": self.config(),
            }
        )
        return result

    def switch_workspace(self, payload):
        path = self.workspace_path_from_payload(payload)
        with self._repo_lock:
            return self._switch_workspace_locked(path)

    def create_workspace(self, payload):
        path = self.workspace_path_from_payload(payload)
        switch_after_create = bool(payload.get("switch", True)) if isinstance(payload, dict) else True
        with self._repo_lock:
            if path.exists() and not path.is_dir():
                raise ValueError(f"workspace path exists and is not a directory: {path}")
            created_directory = not path.exists()
            path.mkdir(parents=True, exist_ok=True)
            git_info = self.workspace_git_info(path)
            initialized_git = False
            if not git_info["git"]:
                init = run_command(["git", "init"], cwd=path, timeout=30)
                if init["returncode"] != 0:
                    message = init["stderr"].strip() or init["stdout"].strip() or "git init failed"
                    raise ValueError(message)
                initialized_git = True
            paths = self.read_workspace_paths()
            if str(path) not in {str(item) for item in paths}:
                paths.append(path)
            if switch_after_create:
                result = self._switch_workspace_locked(path, paths=paths)
            else:
                self.write_workspace_paths(paths)
                result = self.list_workspaces()
            result.update(
                {
                    "added": True,
                    "created": created_directory,
                    "created_directory": created_directory,
                    "initialized_git": initialized_git,
                    "path": str(path),
                    "switched": switch_after_create and str(self.repo) == str(path),
                }
            )
            return result

    def remove_workspace(self, payload):
        path = self.workspace_path_from_payload(payload)
        with self._repo_lock:
            if path == self.repo:
                raise ValueError("cannot remove the active workspace")
            paths = [item for item in self.read_workspace_paths() if item != path]
            self.write_workspace_paths(paths)
            result = self.list_workspaces()
            result.update({"removed": True, "path": str(path)})
            return result

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

    def settings_path(self):
        return self.repo / WEB_STATE_DIR / WEB_SETTINGS_FILE

    def normalize_settings(self, payload):
        payload = payload if isinstance(payload, dict) else {}
        theme = str(payload.get("theme") or "light").strip().lower()
        if theme not in ("light", "dark", "system"):
            theme = "light"
        benchmark_base_path = str(payload.get("benchmark_base_path") or "").strip()
        raw_postprocess = payload.get("postprocess_defaults")
        if not isinstance(raw_postprocess, dict):
            raw_postprocess = {}
        postprocess_defaults = {
            "email_to": str(raw_postprocess.get("email_to") or "").strip(),
            "plots": str(raw_postprocess.get("plots") or "default").strip() or "default",
            "email_subject": str(raw_postprocess.get("email_subject") or "mkexp2 {status}: {experiment_id}").strip(),
            "email_body": str(raw_postprocess.get("email_body") or "").strip(),
        }
        return {
            "theme": theme,
            "benchmark_base_path": benchmark_base_path,
            "postprocess_defaults": postprocess_defaults,
        }

    def read_settings(self):
        path = self.settings_path()
        if not path.is_file():
            payload = {}
        else:
            try:
                payload = json.loads(path.read_text(encoding="utf-8") or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid settings JSON: {exc}") from exc
        settings = self.normalize_settings(payload)
        settings["path"] = str(path)
        return settings

    def write_settings(self, payload):
        settings = self.normalize_settings(payload)
        path = self.settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        settings["path"] = str(path)
        settings["saved"] = True
        return settings

    def columns_path(self):
        return self.repo / WEB_STATE_DIR / WEB_COLUMNS_FILE

    def _normalize_column_visibility_map(self, signatures):
        if not isinstance(signatures, dict):
            return {}
        normalized_signatures = {}
        for signature, columns in signatures.items():
            signature = str(signature or "")
            if not signature or not isinstance(columns, list):
                continue
            normalized_columns = []
            seen = set()
            for column in columns:
                column = str(column)
                if column not in seen:
                    normalized_columns.append(column)
                    seen.add(column)
            normalized_signatures[signature] = normalized_columns
        return normalized_signatures

    def _legacy_column_visibility_map(self, payload):
        raw_experiments = payload.get("experiments") if isinstance(payload, dict) else None
        if not isinstance(raw_experiments, dict):
            return {}
        visibility = {}
        for _, signatures in sorted(raw_experiments.items()):
            for signature, columns in self._normalize_column_visibility_map(signatures).items():
                visibility.setdefault(signature, columns)
        return visibility

    def _normalize_column_visibility_payload(self, payload):
        payload = payload if isinstance(payload, dict) else {}
        visibility = self._normalize_column_visibility_map(payload.get("visibility"))
        for signature, columns in self._legacy_column_visibility_map(payload).items():
            visibility.setdefault(signature, columns)
        return visibility

    def read_column_visibility_state(self):
        path = self.columns_path()
        if not path.is_file():
            payload = {}
        else:
            try:
                payload = json.loads(path.read_text(encoding="utf-8") or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid column visibility JSON: {exc}") from exc
        return {"visibility": self._normalize_column_visibility_payload(payload), "path": str(path)}

    def write_column_visibility_state(self, visibility):
        normalized = self._normalize_column_visibility_payload({"visibility": visibility})
        path = self.columns_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps({"visibility": normalized}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        return {"visibility": normalized, "path": str(path), "saved": True}

    def column_visibility(self, experiment_id):
        experiment_id = str(experiment_id or "").strip().strip("/")
        path = self.readable_experiment_path(experiment_id)
        if not path.is_dir() or not (path / "Experiment").is_file():
            raise ValueError(f"experiment not found: {experiment_id}")
        state = self.read_column_visibility_state()
        return {
            "id": experiment_id,
            "visibility": dict(state.get("visibility") or {}),
            "path": state.get("path", ""),
        }

    def global_column_visibility(self):
        state = self.read_column_visibility_state()
        return {
            "visibility": dict(state.get("visibility") or {}),
            "path": state.get("path", ""),
        }

    def write_global_column_visibility(self, payload):
        visibility = payload.get("visibility") if isinstance(payload, dict) else None
        if not isinstance(visibility, dict):
            raise ValueError("visibility must be an object")
        saved = self.write_column_visibility_state(visibility)
        return {
            "visibility": dict(saved.get("visibility") or {}),
            "path": saved.get("path", ""),
            "saved": True,
        }

    def write_column_visibility(self, experiment_id, payload):
        experiment_id = str(experiment_id or "").strip().strip("/")
        path = self.active_experiment_path(experiment_id)
        if not path.is_dir() or not (path / "Experiment").is_file():
            raise ValueError(f"experiment not found: {experiment_id}")
        visibility = payload.get("visibility") if isinstance(payload, dict) else None
        if not isinstance(visibility, dict):
            raise ValueError("visibility must be an object")
        normalized = self._normalize_column_visibility_payload({"visibility": visibility})
        saved = self.write_column_visibility_state(normalized)
        return {
            "id": experiment_id,
            "visibility": dict(saved.get("visibility") or {}),
            "path": saved.get("path", ""),
            "saved": True,
        }

    def move_column_visibility(self, old_id, new_id):
        return None

    def remove_column_visibility(self, experiment_id):
        return None

    def tags_path(self):
        return self.repo / WEB_STATE_DIR / WEB_TAGS_FILE

    def _normalize_tags_payload(self, payload):
        tag_map = {}
        raw_tags = payload.get("tags") if isinstance(payload, dict) else None
        if isinstance(raw_tags, dict):
            raw_tags = [{"name": name, "color": color} for name, color in raw_tags.items()]
        if not isinstance(raw_tags, list):
            raw_tags = []
        for item in [*DEFAULT_TAGS, *raw_tags]:
            if not isinstance(item, dict):
                continue
            try:
                name = normalize_tag_name(item.get("name"))
                color = normalize_tag_color(item.get("color") or "#64748b")
            except ValueError:
                continue
            if name:
                tag_map[name] = {"name": name, "color": color}

        assignments = {}
        raw_assignments = payload.get("assignments") if isinstance(payload, dict) else None
        if isinstance(raw_assignments, dict):
            for experiment_id, tag_name in raw_assignments.items():
                experiment_id = str(experiment_id or "").strip().strip("/")
                try:
                    self.experiment_path(experiment_id)
                    tag_name = normalize_tag_name(tag_name)
                except ValueError:
                    continue
                if tag_name in tag_map:
                    assignments[experiment_id] = tag_name
        return {
            "tags": sorted(tag_map.values(), key=lambda item: item["name"].casefold()),
            "assignments": assignments,
            "palette": list(TAG_COLOR_PALETTE),
            "default_tags": sorted(DEFAULT_TAG_NAMES),
        }

    def read_tags(self):
        path = self.tags_path()
        if not path.is_file():
            payload = {}
        else:
            try:
                payload = json.loads(path.read_text(encoding="utf-8") or "{}")
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid tags JSON: {exc}") from exc
        state = self._normalize_tags_payload(payload)
        state["path"] = str(path)
        return state

    def write_tags_state(self, tags, assignments):
        state = self._normalize_tags_payload({"tags": tags, "assignments": assignments})
        path = self.tags_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"tags": state["tags"], "assignments": state["assignments"]}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
        state["path"] = str(path)
        state["saved"] = True
        return state

    def upsert_tag(self, payload):
        name = normalize_tag_name((payload or {}).get("name"))
        if not name:
            raise ValueError("tag name is required")
        color = normalize_tag_color((payload or {}).get("color"))
        state = self.read_tags()
        tags = [tag for tag in state["tags"] if tag["name"] != name]
        tags.append({"name": name, "color": color})
        return self.write_tags_state(tags, state["assignments"])

    def delete_tag(self, name):
        name = normalize_tag_name(name)
        if not name:
            raise ValueError("tag name is required")
        if name in DEFAULT_TAG_NAMES:
            raise ValueError(f"default tag cannot be deleted: {name}")
        state = self.read_tags()
        tags = [tag for tag in state["tags"] if tag["name"] != name]
        if len(tags) == len(state["tags"]):
            raise ValueError(f"unknown tag: {name}")
        assignments = {
            experiment_id: tag_name
            for experiment_id, tag_name in state["assignments"].items()
            if tag_name != name
        }
        return self.write_tags_state(tags, assignments)

    def tag_for_experiment(self, experiment_id, tags_state=None):
        tags_state = tags_state or self.read_tags()
        tag_name = (tags_state.get("assignments") or {}).get(experiment_id)
        if not tag_name:
            return None
        by_name = {tag["name"]: tag for tag in tags_state.get("tags") or []}
        return by_name.get(tag_name)

    def with_tag_summaries(self, experiments):
        tags_state = self.read_tags()
        summarized = []
        for experiment in experiments:
            item = dict(experiment)
            tag = self.tag_for_experiment(item["id"], tags_state)
            item["tag"] = tag
            item["tag_name"] = tag["name"] if tag else ""
            summarized.append(item)
        return summarized

    def assign_experiment_tag(self, experiment_id, tag_name, require_active=True):
        experiment_id = str(experiment_id or "").strip().strip("/")
        if require_active:
            known = {experiment["id"] for experiment in self.list_experiments(force=True)}
            if experiment_id not in known:
                raise ValueError(f"experiment not found: {experiment_id}")
            path = self.active_experiment_path(experiment_id)
            if not path.is_dir() or not (path / "Experiment").is_file():
                raise ValueError(f"experiment not found: {experiment_id}")
        else:
            self.experiment_path(experiment_id)

        state = self.read_tags()
        assignments = dict(state["assignments"])
        tag_name = normalize_tag_name(tag_name)
        if tag_name:
            tags_by_name = {tag["name"]: tag for tag in state["tags"]}
            if tag_name not in tags_by_name:
                raise ValueError(f"unknown tag: {tag_name}")
            assignments[experiment_id] = tag_name
        else:
            assignments.pop(experiment_id, None)
        updated = self.write_tags_state(state["tags"], assignments)
        return {"experiment_id": experiment_id, "tag": self.tag_for_experiment(experiment_id, updated), "tags": updated}

    def move_experiment_tag(self, old_id, new_id):
        state = self.read_tags()
        assignments = dict(state["assignments"])
        if old_id in assignments:
            assignments[new_id] = assignments.pop(old_id)
            self.write_tags_state(state["tags"], assignments)

    def remove_experiment_tag(self, experiment_id):
        state = self.read_tags()
        assignments = dict(state["assignments"])
        if experiment_id in assignments:
            assignments.pop(experiment_id, None)
            self.write_tags_state(state["tags"], assignments)

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

    def share_remote_host(self):
        for candidate in (os.environ.get("MKEXP2_WEB_PUBLIC_HOST"), socket.getfqdn(), socket.gethostname(), os.environ.get("HOSTNAME")):
            candidate = str(candidate or "").strip()
            if candidate:
                return candidate
        return "<cluster-login>"

    def share_ssh_tunnel_command(self, background=False):
        remote_host = self.share_remote_host()
        flags = "-fN " if background else ""
        return f"ssh {flags}-L {self.web_port}:127.0.0.1:{self.web_port} <user>@{remote_host}"

    def share_colleague_command_template(self, share_id):
        tunnel = self.share_ssh_tunnel_command(background=True)
        return f"{tunnel} && python3 -m webbrowser {shlex.quote(self.share_public_url(share_id))}"

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
            "colleague_command_template": self.share_colleague_command_template(share_id),
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

    def describe_catalog(self):
        result = run_command([str(self.mkexp2), "describe", "--all", "--json"], cwd=self.repo, timeout=45)
        if result["returncode"] != 0:
            message = result["stderr"].strip() or result["stdout"].strip() or "mkexp2 describe --all --json failed"
            raise ValueError(message)
        try:
            payload = json.loads(result["stdout"] or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid describe JSON: {exc}") from exc
        if not isinstance(payload.get("partitioners"), list) or not isinstance(payload.get("systems"), list):
            raise ValueError("invalid describe JSON: expected partitioners and systems arrays")
        return payload

    def config(self):
        return {
            "repo": str(self.repo),
            "workspace": self.workspace_entry(self.repo),
            "workspace_registry": str(self.workspace_registry_path),
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
        tag_name = str(payload.get("tag") or "").strip()
        path.mkdir(parents=True)
        try:
            if preset:
                init = run_command([str(self.mkexp2), "init", preset], cwd=path, timeout=30)
                if init["returncode"] != 0:
                    message = init["stderr"].strip() or init["stdout"].strip() or f"failed to initialize preset {preset}"
                    raise ValueError(message)
                tag = self.assign_experiment_tag(experiment_id, tag_name, require_active=False)["tag"] if tag_name else None
                self.invalidate_experiments_cache()
                return {"id": experiment_id, "path": str(path), "preset": preset, "init": init, "tag": tag}

            raw = payload.get("experiment")
            if not raw:
                raw = experiment_from_form(name, payload.get("form") or {})
            (path / "Experiment").write_text(raw, encoding="utf-8")
            tag = self.assign_experiment_tag(experiment_id, tag_name, require_active=False)["tag"] if tag_name else None
            self.invalidate_experiments_cache()
            return {"id": experiment_id, "path": str(path), "tag": tag}
        except Exception:
            shutil.rmtree(path, ignore_errors=True)
            raise

    def copy_experiment(self, source_id, payload):
        source_path = self.active_experiment_path(source_id)
        known = {experiment["id"] for experiment in self.list_experiments(force=True)}
        if source_id not in known:
            raise ValueError(f"experiment not found: {source_id}")
        source_file = source_path / "Experiment"
        if not source_file.is_file():
            raise ValueError(f"experiment not found: {source_id}")

        default_name = f"{Path(source_id).name}-copy"
        name = payload.get("name") or default_name
        template = payload.get("name_template") or self.name_template
        target_id = render_name_template(template, name)
        self.validate_visible_experiment_id(target_id)
        target_path = self.experiment_path(target_id)
        if target_path.exists():
            raise ValueError(f"experiment already exists: {target_id}")
        if source_path in target_path.parents:
            raise ValueError("copy target cannot be inside the source experiment directory")

        target_path.mkdir(parents=True)
        try:
            (target_path / "Experiment").write_text(source_file.read_text(encoding="utf-8"), encoding="utf-8")
            self.invalidate_experiments_cache()
            return {
                "copied": True,
                "id": target_id,
                "path": str(target_path),
                "source_id": source_id,
                "source_path": str(source_path),
            }
        except Exception:
            shutil.rmtree(target_path, ignore_errors=True)
            raise

    def command(self, experiment_id, argv, timeout=60):
        return run_command([str(self.mkexp2), *argv], cwd=self.active_experiment_path(experiment_id), timeout=timeout)

    def read_command(self, experiment_id, argv, timeout=60):
        return run_command([str(self.mkexp2), *argv], cwd=self.readable_experiment_path(experiment_id), timeout=timeout)

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
            "--all",
        }
        argv = ["probe"]
        if selector:
            argv.append(str(selector))
        for flag in flags:
            if flag not in allowed_flags:
                raise ValueError(f"unsupported probe flag: {flag}")
            argv.append(flag)
        result = self.read_command(experiment_id, argv, timeout=60)
        if result["returncode"] == 0 and result["stdout"].strip():
            try:
                parsed = json.loads(result["stdout"])
            except json.JSONDecodeError:
                parsed = {"raw": result["stdout"]}
            parsed["_command"] = result
            return parsed
        return result

    def guided_model(self, experiment_id):
        probe = self.read_command(experiment_id, ["probe", "--all"], timeout=90)
        if probe["returncode"] != 0:
            message = probe["stderr"].strip() or probe["stdout"].strip() or "mkexp2 probe --all failed"
            raise ValueError(message)
        try:
            probe_payload = json.loads(probe["stdout"] or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid probe JSON: {exc}") from exc
        describe = self.describe_catalog()
        return {
            "probe": probe_payload,
            "describe": describe,
            "settings": self.read_settings(),
            "_command": probe,
        }

    def render_guided_experiment(self, experiment_id, payload):
        form = payload.get("form") if isinstance(payload, dict) else {}
        path = self.readable_experiment_path(experiment_id)
        rendered = experiment_from_form(Path(experiment_id).name, form if isinstance(form, dict) else {})
        response = {
            "id": experiment_id,
            "path": str(path),
            "experiment_file": str(path / "Experiment"),
            "experiment": rendered,
        }
        response.update(self.experiment_time_metadata(path))
        return response

    def save_guided_experiment(self, experiment_id, payload):
        path = self.active_experiment_path(experiment_id)
        rendered = self.render_guided_experiment(experiment_id, payload)
        experiment_file = path / "Experiment"
        experiment_file.write_text(rendered["experiment"], encoding="utf-8")
        return {
            "id": experiment_id,
            "path": str(path),
            "experiment_file": str(experiment_file),
            "experiment": rendered["experiment"],
            "saved": True,
        }

    def fetch_repo_refs(self, payload):
        repo_url = str((payload or {}).get("repo_url") or "").strip()
        if not repo_url:
            raise ValueError("repo_url is required")
        result = run_command(["git", "ls-remote", "--heads", "--tags", repo_url], cwd=self.repo, timeout=45)
        refs = []
        seen = set()
        if result["returncode"] == 0:
            for line in result["stdout"].splitlines():
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                sha, ref = parts
                if ref.endswith("^{}"):
                    continue
                kind = "ref"
                names = []
                if ref.startswith("refs/heads/"):
                    short = ref.removeprefix("refs/heads/")
                    kind = "branch"
                    names = [f"origin/{short}", short]
                elif ref.startswith("refs/tags/"):
                    short = ref.removeprefix("refs/tags/")
                    kind = "tag"
                    names = [short]
                else:
                    names = [ref]
                for name in names:
                    if name in seen:
                        continue
                    seen.add(name)
                    refs.append({"name": name, "ref": ref, "sha": sha, "kind": kind})
        if result["returncode"] != 0:
            message = result["stderr"].strip() or result["stdout"].strip() or "git ls-remote failed"
            raise ValueError(message)
        refs.sort(key=lambda item: (item["kind"] != "branch", item["name"]))
        return {"repo_url": repo_url, "refs": refs, "command": result}

    def benchmark_sets(self, query=""):
        settings = self.read_settings()
        base_text = settings.get("benchmark_base_path", "")
        if not base_text:
            return {"base_path": "", "sets": []}
        base = Path(os.path.expanduser(base_text)).resolve()
        if not base.exists() or not base.is_dir():
            return {"base_path": str(base), "sets": [], "error": "benchmark base path does not exist"}
        query_text = str(query or "").strip().lower()
        results = []
        max_results = 250
        max_seen = 4000
        seen = 0
        graph_suffixes = {".graph", ".metis", ".parhip"}
        for root, dirs, files in os.walk(base):
            dirs[:] = [name for name in dirs if not name.startswith(".")]
            root_path = Path(root)
            rel_depth = len(root_path.relative_to(base).parts)
            if not query_text and rel_depth > 2:
                dirs[:] = []
            candidates = [(root_path, "directory")]
            candidates.extend((root_path / name, "file") for name in files if Path(name).suffix in graph_suffixes)
            for candidate, kind in candidates:
                seen += 1
                if seen > max_seen:
                    break
                text = str(candidate)
                if query_text and query_text not in text.lower():
                    continue
                results.append({
                    "path": text,
                    "name": candidate.name,
                    "kind": kind,
                    "relative": candidate.relative_to(base).as_posix() if candidate != base else ".",
                })
                if len(results) >= max_results:
                    break
            if seen > max_seen or len(results) >= max_results:
                break
        return {"base_path": str(base), "sets": results, "truncated": seen > max_seen or len(results) >= max_results}

    def graph_directory(self, experiment_id, payload):
        path_text = str((payload or {}).get("path") or "").strip()
        if not path_text:
            raise ValueError("graph directory path is required")
        extension = str((payload or {}).get("extension") or "").strip().lstrip(".")
        experiment_path = self.readable_experiment_path(experiment_id)
        raw_path = Path(os.path.expanduser(path_text))
        directory = raw_path if raw_path.is_absolute() else (experiment_path / raw_path)
        directory = directory.resolve()
        if not directory.is_dir():
            raise ValueError(f"not a graph directory: {path_text}")
        graph_suffixes = {".graph", ".metis", ".parhip"}
        entries = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name):
            if not child.is_file():
                continue
            if extension:
                if child.suffix != f".{extension}":
                    continue
            elif child.suffix not in graph_suffixes:
                continue
            if raw_path.is_absolute():
                display_path = str(child.with_suffix(""))
            else:
                display_path = (Path(path_text) / child.name).with_suffix("").as_posix()
            entries.append({
                "path": display_path,
                "name": child.stem,
                "extension": child.suffix.lstrip("."),
                "absolute_path": str(child),
            })
        return {"directory": str(directory), "entries": entries}

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

    def submit_lock_at_path(self, experiment_path):
        path = Path(experiment_path) / ".mkexp2" / "submit.lock"
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

    def submit_lock_summary_at_path(self, experiment_path):
        lock = self.submit_lock_at_path(experiment_path)
        return {
            "locked": bool(lock.get("locked")),
            "fields": lock.get("fields") or {},
            "modified_at": lock.get("modified_at", ""),
        }

    def with_submit_lock_summaries(self, experiments):
        summarized = []
        for experiment in experiments:
            item = dict(experiment)
            try:
                item["submit_lock"] = self.submit_lock_summary_at_path(item["path"])
            except OSError:
                item["submit_lock"] = {"locked": False, "fields": {}, "modified_at": ""}
            summarized.append(item)
        return summarized

    def submit_lock(self, experiment_id):
        return self.submit_lock_at_path(self.active_experiment_path(experiment_id))

    def clear_submit_lock(self, experiment_id):
        path = self.submit_lock_path(experiment_id)
        existed = path.exists()
        path.unlink(missing_ok=True)
        return {"cleared": existed, "submit_lock": self.submit_lock(experiment_id)}

    def cancel_submit(self, experiment_id, payload):
        confirm_id = str((payload or {}).get("confirm_id") or "").strip()
        if confirm_id != experiment_id:
            raise ValueError("confirmation did not match experiment id")
        lock = self.submit_lock(experiment_id)
        if not lock.get("locked"):
            return {"cancelled": False, "message": "submit is not locked", "submit_lock": lock}
        exp_path = self.active_experiment_path(experiment_id)
        lock_cwd = (lock.get("fields") or {}).get("cwd")
        if lock_cwd and Path(lock_cwd).resolve() != exp_path.resolve():
            raise ValueError("submit lock cwd does not match experiment directory")

        systems = set(value.lower() for value in lock_values(lock, "system"))
        slurm_job_ids = []
        slurm_job_ids.extend(lock_values(lock, "slurm_job_id"))
        for value in lock_values(lock, "slurm_job"):
            if ":" in value:
                slurm_job_ids.append(value.rsplit(":", 1)[1])
            else:
                slurm_job_ids.append(value)
        slurm_job_ids = unique_ordered(slurm_job_ids)

        slurm_result = None
        local_result = None
        if slurm_job_ids:
            slurm_result = self.slurm.cancel_job_ids(slurm_job_ids)

        pid_text = (lock.get("fields") or {}).get("pid", "")
        if not slurm_job_ids and pid_text:
            if re.fullmatch(r"\d+", pid_text):
                pid = int(pid_text)
                command = process_command(pid) if process_exists(pid) else ""
                if command and "submit.sh" not in command and str(exp_path) not in command:
                    raise ValueError(f"refusing to terminate pid {pid}: it does not look like this experiment's submit process")
                local_result = terminate_process_tree(pid)
            else:
                raise ValueError("submit lock contains an invalid pid")

        if not slurm_job_ids and local_result is None:
            raise ValueError("submit lock does not contain cancellable Slurm job ids or a local pid")

        cleared = self.clear_submit_lock(experiment_id)
        self.invalidate_experiments_cache()
        return {
            "cancelled": True,
            "system": sorted(systems),
            "slurm_job_ids": slurm_job_ids,
            "slurm": slurm_result,
            "local": local_result,
            "cleared": cleared.get("cleared", False),
            "submit_lock": cleared["submit_lock"],
        }

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
        self.remove_experiment_tag(experiment_id)
        self.remove_column_visibility(experiment_id)
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
        self.move_experiment_tag(experiment_id, new_id)
        self.move_column_visibility(experiment_id, new_id)
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
        self.move_experiment_tag(experiment_id, archived_id)
        self.move_column_visibility(experiment_id, archived_id)
        return {
            "archived": True,
            "id": experiment_id,
            "archived_id": archived_id,
            "path": str(path),
            "archived_path": str(archived_path),
        }

    def archive_tagged_experiments(self, tag_name):
        tag_name = normalize_tag_name(tag_name)
        if not tag_name:
            raise ValueError("tag name is required")
        tags_state = self.read_tags()
        tags_by_name = {tag["name"]: tag for tag in tags_state["tags"]}
        if tag_name not in tags_by_name:
            raise ValueError(f"unknown tag: {tag_name}")

        assignments = tags_state.get("assignments") or {}
        pinned = set(self.read_pins().get("pinned") or [])
        archived = []
        skipped_locked = []
        skipped_pinned = []
        failed = []
        matching = 0

        for experiment in list(self.list_experiments(force=True)):
            experiment_id = experiment["id"]
            if assignments.get(experiment_id) != tag_name:
                continue
            matching += 1
            if experiment_id in pinned:
                skipped_pinned.append({"id": experiment_id, "reason": "pinned"})
                continue
            lock = experiment.get("submit_lock") or {}
            if lock.get("locked"):
                skipped_locked.append(
                    {
                        "id": experiment_id,
                        "reason": "submit_locked",
                        "submit_lock": lock,
                    }
                )
                continue
            try:
                archived.append(self.archive_experiment(experiment_id))
            except Exception as exc:  # keep bulk maintenance useful despite per-item collisions
                failed.append({"id": experiment_id, "error": str(exc)})

        return {
            "tag": tag_name,
            "matching": matching,
            "archived": archived,
            "skipped_locked": skipped_locked,
            "skipped_pinned": skipped_pinned,
            "failed": failed,
            "archived_count": len(archived),
            "skipped_locked_count": len(skipped_locked),
            "skipped_pinned_count": len(skipped_pinned),
            "failed_count": len(failed),
        }

    def archive_subdirectory_experiments(self, directory):
        directory = str(directory or "").strip().strip("/")
        if not directory:
            raise ValueError("directory is required")
        parts = directory.split("/")
        if (
            any(part in ("", ".", "..") for part in parts)
            or any(part in EXPERIMENT_SKIP_DIRS or part.startswith(".") for part in parts)
            or not all(re.match(r"^[A-Za-z0-9._-]+$", part) for part in parts)
        ):
            raise ValueError("invalid directory")

        pinned = set(self.read_pins().get("pinned") or [])
        archived = []
        skipped_locked = []
        skipped_pinned = []
        failed = []
        matching = 0

        prefix = f"{directory}/"
        for experiment in list(self.list_experiments(force=True)):
            experiment_id = experiment["id"]
            if not experiment_id.startswith(prefix):
                continue
            matching += 1
            if experiment_id in pinned:
                skipped_pinned.append({"id": experiment_id, "reason": "pinned"})
                continue
            lock = experiment.get("submit_lock") or {}
            if lock.get("locked"):
                skipped_locked.append(
                    {
                        "id": experiment_id,
                        "reason": "submit_locked",
                        "submit_lock": lock,
                    }
                )
                continue
            try:
                archived.append(self.archive_experiment(experiment_id))
            except Exception as exc:  # keep bulk maintenance useful despite per-item collisions
                failed.append({"id": experiment_id, "error": str(exc)})

        return {
            "directory": directory,
            "matching": matching,
            "archived": archived,
            "skipped_locked": skipped_locked,
            "skipped_pinned": skipped_pinned,
            "failed": failed,
            "archived_count": len(archived),
            "skipped_locked_count": len(skipped_locked),
            "skipped_pinned_count": len(skipped_pinned),
            "failed_count": len(failed),
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
        self.move_experiment_tag(experiment_id, target_id)
        self.move_column_visibility(experiment_id, target_id)
        return {
            "unarchived": True,
            "id": experiment_id,
            "active_id": target_id,
            "path": str(path),
            "active_path": str(target_path),
        }

    def progress(self, experiment_id):
        fast = self.progress_from_metadata(experiment_id)
        if fast is not None:
            return fast

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

    def progress_from_metadata(self, experiment_id):
        started = time.time()
        experiment_path = self.active_experiment_path(experiment_id)
        jobs_dir = experiment_path / "jobs"
        if not jobs_dir.is_dir():
            return None
        meta_files = sorted(jobs_dir.glob("*.cmds.meta.tsv"))
        if not meta_files:
            return None

        experiment_order = []
        algorithm_order = {}
        counts = {}
        expected_logs = []
        try:
            for meta_file in meta_files:
                with meta_file.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        parts = line.rstrip("\n").split("\t")
                        if len(parts) < 6:
                            continue
                        algorithm = parts[1]
                        function_name = parts[3] or "Experiment"
                        log_file = parts[5]
                        if not algorithm or not log_file:
                            continue
                        if function_name not in counts:
                            counts[function_name] = {}
                            experiment_order.append(function_name)
                            algorithm_order[function_name] = []
                        if algorithm not in counts[function_name]:
                            counts[function_name][algorithm] = {"done": 0, "total": 0}
                            algorithm_order[function_name].append(algorithm)
                        counts[function_name][algorithm]["total"] += 1
                        log_path = Path(log_file)
                        if not log_path.is_absolute():
                            log_path = experiment_path / log_path
                        expected_logs.append((function_name, algorithm, (
                            os.path.abspath(str(log_path)),
                            os.path.realpath(str(log_path)),
                        )))
        except OSError:
            return None

        if not expected_logs:
            return None

        existing_logs = {}
        logs_dir = experiment_path / "logs"
        logs_root = logs_dir.resolve() if logs_dir.is_dir() else None
        if logs_dir.is_dir():
            for dirpath, _, filenames in os.walk(logs_dir):
                for filename in filenames:
                    path = Path(dirpath) / filename
                    try:
                        stat = path.stat()
                        rel_path = path.resolve().relative_to(logs_root).as_posix()
                    except (OSError, ValueError):
                        continue
                    info = {
                        "mtime": stat.st_mtime,
                        "path": rel_path,
                    }
                    existing_logs[os.path.abspath(str(path))] = info
                    existing_logs[os.path.realpath(str(path))] = info

        for function_name, algorithm, log_paths in expected_logs:
            info = next((existing_logs[log_path] for log_path in log_paths if log_path in existing_logs), None)
            if info:
                counts[function_name][algorithm]["done"] += 1
                current_latest = counts[function_name][algorithm].get("latest_log_mtime") or 0
                if info["mtime"] >= current_latest:
                    counts[function_name][algorithm]["latest_log_mtime"] = info["mtime"]
                    counts[function_name][algorithm]["latest_log"] = info["path"]

        experiments = []
        all_done = 0
        all_total = 0
        for function_name in experiment_order:
            algorithms = []
            exp_done = 0
            exp_total = 0
            latest_exp_log = ""
            latest_exp_log_mtime = 0
            for algorithm in algorithm_order.get(function_name, []):
                item = counts[function_name][algorithm]
                done = item["done"]
                total = item["total"]
                exp_done += done
                exp_total += total
                latest_log_mtime = item.get("latest_log_mtime") or 0
                if latest_log_mtime >= latest_exp_log_mtime:
                    latest_exp_log_mtime = latest_log_mtime
                    latest_exp_log = item.get("latest_log", "")
                algorithms.append({
                    "name": algorithm,
                    "done": done,
                    "total": total,
                    "percent": percent(done, total),
                    "complete": total > 0 and done >= total,
                    "latest_log": item.get("latest_log", ""),
                })
            all_done += exp_done
            all_total += exp_total
            experiments.append({
                "name": display_experiment_function_name(function_name),
                "function": function_name,
                "done": exp_done,
                "total": exp_total,
                "percent": percent(exp_done, exp_total),
                "complete": exp_total > 0 and exp_done >= exp_total,
                "latest_log": latest_exp_log,
                "algorithms": algorithms,
            })

        progress_json = {
            "ok": True,
            "done": all_done,
            "total": all_total,
            "percent": percent(all_done, all_total),
            "complete": all_total > 0 and all_done >= all_total,
            "experiments": experiments,
        }
        command = {
            "argv": ["metadata-progress"],
            "cwd": str(experiment_path),
            "returncode": 0,
            "stdout": json.dumps(progress_json, separators=(",", ":")),
            "stderr": "",
            "elapsed_seconds": round(time.time() - started, 3),
            "timed_out": False,
        }
        return {
            "ok": True,
            "progress": command,
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

    def normalize_submit_selections(self, payload):
        algorithms = payload.get("algorithms") or []
        selections = payload.get("selections") or []
        if not isinstance(algorithms, list) or not all(isinstance(item, str) for item in algorithms):
            raise ValueError("algorithms must be an array of strings")
        if selections and algorithms:
            raise ValueError("use either algorithms or selections, not both")
        if not isinstance(selections, list):
            raise ValueError("selections must be an array")

        normalized = []
        for item in selections:
            if not isinstance(item, dict):
                raise ValueError("selection entries must be objects")
            experiment = str(item.get("experiment") or item.get("function") or "").strip()
            if not experiment:
                raise ValueError("selection entries require an experiment")
            if "algorithm" in item:
                item_algorithms = [item.get("algorithm")]
            else:
                item_algorithms = item.get("algorithms") or []
            if not isinstance(item_algorithms, list) or not all(isinstance(algorithm, str) for algorithm in item_algorithms):
                raise ValueError("selection algorithms must be an array of strings")
            for algorithm in item_algorithms:
                name = algorithm.strip()
                if not name:
                    continue
                normalized.append({"experiment": experiment, "algorithm": name})
        return algorithms, normalized

    def write_submit_selection_file(self, experiment_id, selections):
        if not selections:
            return None
        selection_dir = self.active_experiment_path(experiment_id) / WEB_STATE_DIR
        selection_dir.mkdir(parents=True, exist_ok=True)
        path = selection_dir / f"web-submit-selection-{secrets.token_urlsafe(8)}.tsv"
        with path.open("w", encoding="utf-8") as handle:
            for item in selections:
                handle.write(f"{item['experiment']}\t{item['algorithm']}\n")
        return path

    def submit_preview(self, experiment_id, payload):
        algorithms, selections = self.normalize_submit_selections(payload)
        exp_path = self.active_experiment_path(experiment_id)
        generate = self.command(experiment_id, ["generate"], timeout=120)
        invocations = []
        jobs_dir = exp_path / "jobs"
        selected_algorithms = set(algorithms)
        selected_pairs = {(item["experiment"], item["algorithm"]) for item in selections}
        if jobs_dir.is_dir():
            for meta_file in sorted(jobs_dir.glob("*.cmds.meta.tsv")):
                command_file = meta_file.with_name(meta_file.name.replace(".cmds.meta.tsv", ".cmds"))
                if not command_file.is_file():
                    continue
                commands = command_file.read_text(encoding="utf-8").splitlines()
                with meta_file.open("r", encoding="utf-8") as handle:
                    for row in handle:
                        parts = row.rstrip("\n").split("\t")
                        if len(parts) < 6:
                            continue
                        try:
                            index = int(parts[0])
                        except ValueError:
                            continue
                        if index < 0 or index >= len(commands):
                            continue
                        algorithm = parts[1]
                        function_name = parts[3] or "Experiment"
                        if selected_algorithms and algorithm not in selected_algorithms:
                            continue
                        if selected_pairs and (function_name, algorithm) not in selected_pairs:
                            continue
                        invocations.append({
                            "index": index,
                            "algorithm": algorithm,
                            "base": parts[2],
                            "experiment": function_name,
                            "topology": parts[4],
                            "log_file": parts[5],
                            "command": commands[index],
                            "job": command_file.name,
                        })
        return {
            "cwd": str(exp_path),
            "algorithms": algorithms,
            "selections": selections,
            "generate": generate,
            "invocations": invocations,
        }

    def submit_action(self, experiment_id, payload):
        algorithms, selections = self.normalize_submit_selections(payload)
        force = bool(payload.get("force"))

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

            selection_file = self.write_submit_selection_file(experiment_id, selections)
            submit_argv = ["zsh", "./submit.sh", "--install", *algorithms]
            if selection_file is not None:
                submit_argv.extend(["--selection-file", str(selection_file)])
            try:
                submit = run_command(
                    submit_argv,
                    cwd=self.active_experiment_path(experiment_id),
                    timeout=120,
                )
            finally:
                if selection_file is not None:
                    try:
                        selection_file.unlink()
                    except FileNotFoundError:
                        pass
            if submit["returncode"] == 0:
                commit = self.git_commit_submission(experiment_id, algorithms, force, selections)
            else:
                commit = {"committed": False, "message": "submit failed; no commit created"}
            return {
                "submitted": submit["returncode"] == 0,
                "algorithms": algorithms,
                "selections": selections,
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

    def git_commit_submission(self, experiment_id, algorithms, force, selections=None):
        rel = experiment_id
        add = run_command(["git", "add", "-A", "--", rel], cwd=self.repo, timeout=60)
        diff = run_command(["git", "diff", "--cached", "--quiet", "--", rel], cwd=self.repo, timeout=60)
        if diff["returncode"] == 0:
            return {"committed": False, "add": add, "message": "nothing to commit"}
        if selections:
            algo_text = ", ".join(f"{item['experiment']}:{item['algorithm']}" for item in selections)
        else:
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
        path = self.readable_experiment_path(experiment_id)
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

    def description(self, experiment_id):
        path = self.readable_experiment_path(experiment_id)
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

    def experiment_download_options(self, experiment_id):
        path = self.readable_experiment_path(experiment_id)
        directories = []
        root_files = []
        for child in sorted(path.iterdir(), key=lambda item: item.name.lower()):
            if child.is_dir():
                directories.append({"name": child.name})
            elif child.is_file():
                root_files.append(child.name)
        return {
            "id": experiment_id,
            "path": str(path),
            "directories": directories,
            "root_files": root_files,
        }

    def archive_entries(self, path, include_dirs):
        root_files = [child.name for child in sorted(path.iterdir(), key=lambda item: item.name.lower()) if child.is_file()]
        if include_dirs is None:
            return None

        available_dirs = {
            child.name
            for child in path.iterdir()
            if child.is_dir()
        }
        selected = []
        seen = set()
        for item in include_dirs:
            name = str(item or "").strip()
            if not name:
                continue
            if "/" in name or "\\" in name or name in (".", ".."):
                raise ValueError(f"invalid archive directory: {name}")
            if name not in available_dirs:
                raise ValueError(f"archive directory not found: {name}")
            if name not in seen:
                selected.append(name)
                seen.add(name)
        selected.sort(key=str.lower)
        root_files.sort(key=str.lower)
        return root_files + selected

    def experiment_archive(self, experiment_id, include_dirs=None):
        path = self.readable_experiment_path(experiment_id)
        base = download_filename(path.name)
        entries = self.archive_entries(path, include_dirs)
        tar_command = shutil.which("tar")
        zstd_command = shutil.which("zstd")
        if tar_command and zstd_command:
            archive = tempfile.NamedTemporaryFile(prefix=f"{base}-", suffix=".tar.zst", delete=False)
            archive.close()
            archive_path = Path(archive.name)
            tar_targets = [path.name] if entries is None else [f"{path.name}/{name}" for name in entries]
            result = run_command(
                [tar_command, "--zstd", "-cf", str(archive_path), "-C", str(path.parent), *tar_targets],
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
                walk_roots = [path] if entries is None else [path / name for name in entries]
                for walk_root in walk_roots:
                    if walk_root.is_file():
                        rel_path = Path(path.name) / walk_root.relative_to(path)
                        zip_file.write(walk_root, rel_path.as_posix())
                        continue
                    for root, dirs, files in os.walk(walk_root):
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
        path = self.readable_experiment_path(experiment_id)
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
        return self.readable_experiment_path(experiment_id) / "plots"

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

    def plot_artifact_set_label(self, artifact):
        label = str(artifact.get("plot_set_label") or "").strip()
        if label:
            return label
        label = str(artifact.get("label") or artifact.get("plot_name") or artifact.get("id") or "Plot set").strip()
        plot_name = str(artifact.get("plot_name") or "").strip()
        suffix = f" - {plot_name}"
        if plot_name and label.endswith(suffix):
            label = label[: -len(suffix)].strip()
        return label or "Plot set"

    def plot_artifact_set_id(self, artifact):
        set_id = str(artifact.get("plot_set_id") or artifact.get("set_id") or "").strip()
        if re.fullmatch(r"[A-Za-z0-9._-]+", set_id):
            return set_id
        created = str(artifact.get("plot_set_created_at") or artifact.get("created_at") or "").strip()
        if created:
            return f"legacy-{slugify(created)}-{slugify(self.plot_artifact_set_label(artifact))[:40]}"
        return f"legacy-{slugify(self.plot_artifact_set_label(artifact))[:64]}"

    def hydrated_plot_artifact(self, artifact, pdf):
        stat = pdf.stat()
        item = dict(artifact)
        set_label = self.plot_artifact_set_label(item)
        item.update(
            {
                "exists": True,
                "size": stat.st_size,
                "modified_at": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "plot_set_id": self.plot_artifact_set_id(item),
                "plot_set_label": set_label,
                "plot_set_created_at": item.get("plot_set_created_at") or item.get("created_at") or "",
            }
        )
        return item

    def list_plot_artifacts(self, experiment_id):
        index = self.read_plot_artifacts_index(experiment_id)
        directory = self.plot_artifacts_dir(experiment_id)
        artifacts = []
        for artifact in index.get("artifacts", []):
            rel_path = str(artifact.get("path") or "")
            pdf = (self.readable_experiment_path(experiment_id) / rel_path).resolve()
            if not rel_path.startswith("plots/") or not pdf.is_file():
                continue
            artifacts.append(self.hydrated_plot_artifact(artifact, pdf))
        artifacts.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return {"artifacts": artifacts, "legacy": self.plots_info(experiment_id), "index_path": str(directory / PLOT_INDEX_FILE)}

    def plot_artifact_pdf(self, experiment_id, artifact_id):
        if not re.match(r"^[A-Za-z0-9._-]+$", artifact_id or ""):
            raise ValueError("invalid plot artifact id")
        for artifact in self.list_plot_artifacts(experiment_id).get("artifacts", []):
            if artifact.get("id") == artifact_id:
                path = (self.readable_experiment_path(experiment_id) / artifact.get("path", "")).resolve()
                root = self.readable_experiment_path(experiment_id).resolve()
                if root not in path.parents:
                    raise ValueError("plot artifact path escapes experiment")
                return path
        raise ValueError("plot artifact not found")

    def _delete_plot_artifact_file(self, experiment_id, artifact):
        rel_path = str(artifact.get("path") or "")
        root = self.active_experiment_path(experiment_id).resolve()
        pdf = (root / rel_path).resolve()
        if not rel_path.startswith("plots/") or root not in pdf.parents:
            raise ValueError("plot artifact path escapes experiment")
        pdf.unlink(missing_ok=True)

    def delete_plot_artifact(self, experiment_id, artifact_id):
        if not re.fullmatch(r"[A-Za-z0-9._-]+", artifact_id or ""):
            raise ValueError("invalid plot artifact id")
        index = self.read_plot_artifacts_index(experiment_id)
        kept = []
        deleted = []
        for artifact in index.get("artifacts", []):
            if artifact.get("id") == artifact_id:
                self._delete_plot_artifact_file(experiment_id, artifact)
                deleted.append(artifact)
            else:
                kept.append(artifact)
        if not deleted:
            raise ValueError("plot artifact not found")
        index["artifacts"] = kept
        self.write_plot_artifacts_index(experiment_id, index)
        return {"deleted": [item.get("id") for item in deleted], "artifacts": self.list_plot_artifacts(experiment_id)}

    def delete_plot_artifact_set(self, experiment_id, set_id):
        if not re.fullmatch(r"[A-Za-z0-9._-]+", set_id or ""):
            raise ValueError("invalid plot set id")
        index = self.read_plot_artifacts_index(experiment_id)
        kept = []
        deleted = []
        for artifact in index.get("artifacts", []):
            if self.plot_artifact_set_id(artifact) == set_id:
                self._delete_plot_artifact_file(experiment_id, artifact)
                deleted.append(artifact)
            else:
                kept.append(artifact)
        if not deleted:
            raise ValueError("plot set not found")
        index["artifacts"] = kept
        self.write_plot_artifacts_index(experiment_id, index)
        return {"deleted": [item.get("id") for item in deleted], "artifacts": self.list_plot_artifacts(experiment_id)}

    def rename_plot_artifact_set(self, experiment_id, set_id, payload):
        if not re.fullmatch(r"[A-Za-z0-9._-]+", set_id or ""):
            raise ValueError("invalid plot set id")
        label = str((payload or {}).get("label") or "").strip()
        if not label:
            raise ValueError("plot set label must not be empty")
        index = self.read_plot_artifacts_index(experiment_id)
        matched = [artifact for artifact in index.get("artifacts", []) if self.plot_artifact_set_id(artifact) == set_id]
        if not matched:
            raise ValueError("plot set not found")
        multi_plot_set = len(matched) > 1
        for artifact in matched:
            artifact["plot_set_id"] = set_id
            artifact["plot_set_label"] = label
            artifact["plot_set_created_at"] = artifact.get("plot_set_created_at") or artifact.get("created_at") or ""
            artifact["label"] = f"{label} - {artifact.get('plot_name', artifact.get('plot_id', 'Plot'))}" if multi_plot_set else label
        self.write_plot_artifacts_index(experiment_id, index)
        return {"renamed": set_id, "label": label, "artifacts": self.list_plot_artifacts(experiment_id)}

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
            set_created_at = _dt.datetime.now().isoformat(timespec="seconds")
            source_label = ", ".join(src["metadata"].get("alias") or src["metadata"].get("name") or "" for src in resolved_sources)
            set_label = label or f"Plot set - {source_label}"
            set_id = "-".join(
                [
                    _dt.datetime.now().strftime("%Y%m%d-%H%M%S"),
                    slugify(set_label)[:48],
                    secrets.token_urlsafe(4).replace("_", "-"),
                ]
            )
            multi_plot_set = len(plot_ids) > 1
            for plot_id in plot_ids:
                entry = catalog[plot_id]
                created_at = _dt.datetime.now().isoformat(timespec="seconds")
                label_text = f"{set_label} - {entry.get('name', plot_id)}" if multi_plot_set else set_label
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
                        "plot_set_id": set_id,
                        "plot_set_label": set_label,
                        "plot_set_created_at": set_created_at,
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
        return self.readable_experiment_path(experiment_id) / "logs"

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
                if child.name.startswith("."):
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

    @staticmethod
    def parse_result_log_filename(filename):
        match = re.match(
            r"^(?P<graph>.+)___k(?P<k>[^_]+)_seed(?P<seed>[^_]+)_eps(?P<epsilon>[^_]+)_P(?P<topology>[^.]+)\.log$",
            str(filename or ""),
        )
        if not match:
            return None
        topology = match.group("topology")
        parts = topology.split("x")
        return {
            "graph": match.group("graph"),
            "k": match.group("k"),
            "seed": match.group("seed"),
            "epsilon": match.group("epsilon"),
            "topology": topology,
            "num_nodes": parts[0] if len(parts) > 0 else "",
            "num_mpis": parts[1] if len(parts) > 1 else "",
            "num_threads": parts[2] if len(parts) > 2 else "",
        }

    @staticmethod
    def values_match(expected, actual):
        expected_text = str(expected or "").strip()
        actual_text = str(actual or "").strip()
        if not expected_text:
            return True
        if expected_text == actual_text:
            return True
        try:
            expected_number = float(expected_text)
            actual_number = float(actual_text)
        except ValueError:
            return False
        return abs(expected_number - actual_number) <= 1e-9 * max(1.0, abs(expected_number), abs(actual_number))

    def resolve_result_log(self, experiment_id, payload):
        algorithm = str((payload or {}).get("algorithm") or "").strip("/")
        if not algorithm:
            raise ValueError("missing algorithm")
        if any(part in ("", ".", "..") for part in algorithm.split("/")):
            raise ValueError("invalid algorithm")
        algorithm_dir = self.log_path(experiment_id, algorithm)
        if not algorithm_dir.is_dir():
            return {"path": "", "candidates": [], "ambiguous": False}

        expected_filename = str((payload or {}).get("filename") or "").strip()
        if expected_filename and ("/" in expected_filename or expected_filename in (".", "..")):
            raise ValueError("invalid log filename")

        criteria = {
            "graph": (payload or {}).get("graph"),
            "k": (payload or {}).get("k"),
            "seed": (payload or {}).get("seed"),
            "epsilon": (payload or {}).get("epsilon"),
            "num_nodes": (payload or {}).get("num_nodes"),
            "num_mpis": (payload or {}).get("num_mpis"),
            "num_threads": (payload or {}).get("num_threads"),
        }
        experiment_label = str((payload or {}).get("experiment_label") or "").strip()
        logs_root = self.logs_root(experiment_id).resolve()
        candidates = []

        for root, dirs, files in os.walk(algorithm_dir):
            dirs[:] = [name for name in dirs if not name.startswith(".")]
            for filename in files:
                if not filename.endswith(".log"):
                    continue
                if expected_filename and filename != expected_filename:
                    parsed = self.parse_result_log_filename(filename)
                    if not parsed:
                        continue
                else:
                    parsed = self.parse_result_log_filename(filename)
                    if not parsed:
                        continue
                if not all(self.values_match(criteria[key], parsed[key]) for key in criteria):
                    continue
                path = Path(root) / filename
                if experiment_label and path.parent.name != experiment_label:
                    continue
                candidates.append(path.resolve().relative_to(logs_root).as_posix())
                if len(candidates) >= 50:
                    break
            if len(candidates) >= 50:
                break

        candidates.sort()
        return {
            "path": candidates[0] if candidates else "",
            "candidates": candidates,
            "ambiguous": len(candidates) > 1,
        }

    def resolve_parser_script_path(self, experiment_path, parser_spec):
        parser_spec = str(parser_spec or "").strip()
        if not parser_spec:
            return None
        exp_path = Path(experiment_path).resolve()
        candidate = Path(parser_spec)
        if candidate.is_absolute():
            resolved = candidate.resolve()
            return resolved if resolved.is_file() else None
        if "/" in parser_spec:
            resolved = (exp_path / parser_spec).resolve()
            return resolved if resolved.is_file() else None

        filename = parser_spec if parser_spec.endswith(".awk") else f"{parser_spec}.awk"
        candidates = [
            self.mkexp2_root() / "plugins" / "parsers" / filename,
            self.mkexp2_root() / "plugins" / "parsers" / f".{filename}",
            exp_path / "plugins" / "parsers" / filename,
            exp_path / "parsers" / filename,
            exp_path / filename,
        ]
        for item in candidates:
            resolved = item.resolve()
            if resolved.is_file():
                return resolved
        return None

    def parser_for_log_algorithm(self, experiment_id, algorithm, experiment_label=""):
        exp_path = self.readable_experiment_path(experiment_id)
        probe = self.read_command(experiment_id, ["probe", "--all", "--algorithms"], timeout=60)
        candidates = []
        if probe["returncode"] == 0 and probe["stdout"].strip():
            try:
                payload = json.loads(probe["stdout"])
            except json.JSONDecodeError:
                payload = {}
            for experiment in payload.get("experiments") or []:
                exp_name = str((experiment.get("experiment") or {}).get("name") or "")
                function_name = str((experiment.get("experiment") or {}).get("function") or "")
                for item in ((experiment.get("resolved") or {}).get("algorithms") or []):
                    if item.get("name") != algorithm:
                        continue
                    parser = item.get("parser") or {}
                    spec = parser.get("spec") or algorithm
                    parser_path = parser.get("resolved_path") or ""
                    candidates.append({
                        "algorithm": algorithm,
                        "experiment": exp_name,
                        "function": function_name,
                        "spec": spec,
                        "resolved_path": parser_path,
                        "found": bool(parser.get("found")),
                    })

        chosen = None
        if candidates:
            lowered_label = str(experiment_label or "").lower()
            for item in candidates:
                if lowered_label and lowered_label in {str(item.get("experiment") or "").lower(), str(item.get("function") or "").lower()}:
                    chosen = item
                    break
            if chosen is None:
                chosen = next((item for item in candidates if item.get("found")), None) or candidates[0]
        else:
            chosen = {"algorithm": algorithm, "experiment": "", "function": "", "spec": algorithm, "resolved_path": "", "found": False}

        parser_path = Path(chosen.get("resolved_path") or "") if chosen.get("resolved_path") else None
        if not parser_path or not parser_path.is_file():
            parser_path = self.resolve_parser_script_path(exp_path, chosen.get("spec") or algorithm)
        if not parser_path:
            raise ValueError(f"no parser script for {algorithm} (spec='{chosen.get('spec') or algorithm}')")
        chosen["resolved_path"] = str(parser_path)
        chosen["found"] = True
        return chosen

    def parse_log_file(self, experiment_id, rel_path):
        rel_text = str(rel_path or "").strip("/")
        parts = rel_text.split("/") if rel_text else []
        if len(parts) < 2 or not rel_text.endswith(".log"):
            raise ValueError("select a run log under logs/<algorithm>/.../*.log")
        algorithm = parts[0]
        experiment_label = parts[1] if len(parts) > 2 else ""
        path = self.log_path(experiment_id, rel_text)
        if not path.is_file():
            raise ValueError("log path is not a file")
        parser = self.parser_for_log_algorithm(experiment_id, algorithm, experiment_label=experiment_label)
        parser_file = Path(parser["resolved_path"]).resolve()
        awk = shutil.which("awk") or "awk"
        awk_args = [awk]
        lib_file = parser_file.parent / ".csv.awk"
        if lib_file.is_file():
            awk_args.extend(["-f", str(lib_file)])
        awk_args.extend(["-f", str(parser_file)])
        marker = path.name[:-4] if path.name.endswith(".log") else path.name
        content = strip_ansi(path.read_text(encoding="utf-8", errors="replace"))
        stream = f"__BEGIN_FILE__ {marker}\n{content}\n__END_FILE__\n"
        command = run_command_with_input(awk_args, stream, cwd=self.readable_experiment_path(experiment_id), timeout=30)
        csv_text = command["stdout"] if command["returncode"] == 0 else ""
        rows = []
        if csv_text.strip():
            rows = list(csv.reader(io.StringIO(csv_text)))
        return {
            "path": rel_text,
            "algorithm": algorithm,
            "parser": parser,
            "command": command,
            "parsed": command["returncode"] == 0,
            "csv": csv_text,
            "headers": rows[0] if rows else [],
            "rows": rows[1:] if len(rows) > 1 else [],
        }


def experiment_function_name(name):
    function = "Experiment" + re.sub(r"[^A-Za-z0-9]+", "", str(name or "").title())
    return function if function != "Experiment" else "ExperimentWeb"


def zsh_words(values):
    return " ".join(shlex.quote(str(value)) for value in values if str(value).strip())


def normalize_form_list(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").replace(",", " ").split() if item.strip()]


def normalize_form_lines(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def normalize_form_graphs(value):
    rows = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                path = str(item.get("path") or item.get("value") or "").strip()
                if not path:
                    continue
                command = str(item.get("command") or item.get("kind") or "Graph").strip()
                if command not in {"Graph", "Graphs"}:
                    command = "Graph"
                extension = str(item.get("extension") or item.get("ext") or "").strip().lstrip(".")
                rows.append({"command": command, "path": path, "extension": extension})
            else:
                path = str(item).strip()
                if path:
                    rows.append({"command": "", "path": path, "extension": ""})
        return rows
    for line in str(value or "").splitlines():
        path = line.strip()
        if path:
            rows.append({"command": "", "path": path, "extension": ""})
    return rows


def sanitize_function_name(value, fallback):
    raw = str(value or "").strip()
    if not raw:
        return fallback
    cleaned = re.sub(r"[^A-Za-z0-9_]", "", raw)
    if not cleaned.startswith("Experiment"):
        cleaned = "Experiment" + cleaned
    if not re.match(r"^Experiment[A-Za-z0-9_]*$", cleaned):
        return fallback
    return cleaned or fallback


def form_properties(properties):
    rows = []
    for prop in properties or []:
        key = str(prop.get("key", "")).strip()
        value = str(prop.get("value", "")).strip()
        if key and value:
            rows.append((key, value))
    return rows


def experiment_from_form(name, form):
    form = form if isinstance(form, dict) else {}
    system = str(form.get("system") or "slurm").strip() or "slurm"
    global_properties = form_properties(form.get("properties") or form.get("global_properties") or [])
    algorithms = form.get("algorithm_definitions") or form.get("algorithms") or []
    experiments = form.get("experiments") or []

    if not experiments:
        experiments = [{
            "function": experiment_function_name(name),
            "algorithms": normalize_form_list(form.get("algorithms") or ["Mock"]),
            "graphs": normalize_form_lines(form.get("graphs") or ["graphs"]),
            "ks": normalize_form_list(form.get("ks") or ["2"]),
            "seeds": normalize_form_list(form.get("seeds") or ["1"]),
            "epsilons": normalize_form_list(form.get("epsilons") or ["0.03"]),
            "topologies": normalize_form_list(form.get("threads") or form.get("topologies") or ["1x1x1"]),
        }]

    lines = [f"System {shlex.quote(system)}"]
    for key, value in global_properties:
        lines.append(f"Property {shlex.quote(key)} {shlex.quote(value)}")

    algorithm_rows = []
    seen_algorithms = set()
    for algorithm in algorithms:
        if not isinstance(algorithm, dict):
            continue
        alg_name = str(algorithm.get("name") or "").strip()
        base = str(algorithm.get("base") or "").strip()
        if not alg_name or not base or alg_name in seen_algorithms:
            continue
        seen_algorithms.add(alg_name)
        args = str(algorithm.get("args") or "").strip()
        definition = f"DefineAlgorithm {shlex.quote(alg_name)} {shlex.quote(base)}"
        if args:
            definition += f" {args}"
        algorithm_rows.append(definition)
        for key, value in form_properties(algorithm.get("properties") or []):
            algorithm_rows.append(f"AlgorithmProperty {shlex.quote(alg_name)} {shlex.quote(key)} {shlex.quote(value)}")
    if algorithm_rows:
        lines.extend(["", *algorithm_rows])

    fallback_function = experiment_function_name(name)
    for index, experiment in enumerate(experiments):
        if not isinstance(experiment, dict):
            continue
        function = sanitize_function_name(experiment.get("function"), fallback_function if index == 0 else f"{fallback_function}{index + 1}")
        selected_algorithms = normalize_form_list(experiment.get("algorithms"))
        graphs = normalize_form_graphs(experiment.get("graphs"))
        ks = normalize_form_list(experiment.get("ks") or ["2"])
        seeds = normalize_form_list(experiment.get("seeds") or ["1"])
        epsilons = normalize_form_list(experiment.get("epsilons") or ["0.03"])
        topologies = normalize_form_list(experiment.get("topologies") or experiment.get("threads") or ["1x1x1"])
        lines.extend(["", f"{function}() {{"])
        if selected_algorithms:
            lines.append("  Algorithms " + zsh_words(selected_algorithms))
        for graph in graphs:
            graph_path = Path(os.path.expanduser(graph["path"]))
            graph_command = graph["command"] or ("Graphs" if graph_path.is_dir() else "Graph")
            graph_line = f"  {graph_command} " + shlex.quote(graph["path"])
            if graph_command == "Graphs" and graph.get("extension"):
                graph_line += " " + shlex.quote(graph["extension"])
            lines.append(graph_line)
        if ks:
            lines.append("  Ks " + zsh_words(ks))
        if seeds:
            lines.append("  Seeds " + zsh_words(seeds))
        if epsilons:
            lines.append("  Epsilons " + zsh_words(epsilons))
        if topologies:
            lines.append("  Threads " + zsh_words(topologies))
        timelimit = str(experiment.get("timelimit") or "").strip()
        if timelimit:
            lines.append("  Timelimit " + shlex.quote(timelimit))
        timelimit_per_instance = str(experiment.get("timelimit_per_instance") or "").strip()
        if timelimit_per_instance:
            lines.append("  TimelimitPerInstance " + shlex.quote(timelimit_per_instance))
        lines.append("}")
    return "\n".join(lines).strip() + "\n"


WEB_ASSET_DIR = Path(__file__).with_name("mkexp2_web_assets")
WEB_APP_JS_FILES = (
    "js/01_bootstrap.js",
    "js/02_settings_dialogs.js",
    "js/03_editor.js",
    "js/04_results_logs.js",
    "js/05_experiments_submit.js",
    "js/06_plots.js",
    "js/07_slurm_init.js",
)


def load_web_asset(name):
    return (WEB_ASSET_DIR / name).read_text(encoding="utf-8")


def load_app_js():
    return "\n".join(load_web_asset(name).rstrip() for name in WEB_APP_JS_FILES)


def load_html():
    template = load_web_asset("index.html")
    return (
        template
        .replace("__MKEXP2_STYLES__", load_web_asset("styles.css").rstrip())
        .replace("__MKEXP2_APP_JS__", load_app_js())
    )


HTML = load_html()



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
            if tail == "describe":
                json_response(self, 200, app.describe_catalog())
                return
            if tail == "experiment":
                exp_path = context["path"]
                payload = {
                    "id": experiment_id,
                    "path": str(exp_path),
                    "experiment_file": str(exp_path / "Experiment"),
                    "experiment": (exp_path / "Experiment").read_text(encoding="utf-8"),
                    "submit_lock": app.submit_lock(experiment_id),
                    "read_only": True,
                }
                payload.update(app.experiment_time_metadata(exp_path))
                json_response(
                    self,
                    200,
                    payload,
                )
                return
            if tail == "results":
                json_response(self, 200, app.results(experiment_id))
                return
            if tail == "columns":
                json_response(self, 200, app.column_visibility(experiment_id))
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
            if tail == "download-options":
                json_response(self, 200, app.experiment_download_options(experiment_id))
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
                archive = app.experiment_archive(experiment_id, include_dirs=archive_include_dirs_from_query(query))
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
            match = re.match(r"^/api/share/([^/]+)/(parse|plot-artifacts|probe|log-parse|log-resolve)$", path)
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
            if action == "log-parse":
                json_response(self, 200, app.parse_log_file(experiment_id, payload.get("path") or ""))
                return
            if action == "log-resolve":
                json_response(self, 200, app.resolve_result_log(experiment_id, payload))
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
                if path == "/api/workspaces":
                    json_response(self, 200, app.list_workspaces())
                    return
                if path == "/api/settings":
                    json_response(self, 200, app.read_settings())
                    return
                if path == "/api/benchmark-sets":
                    query = urllib.parse.parse_qs(parsed.query)
                    json_response(self, 200, app.benchmark_sets((query.get("query") or [""])[0]))
                    return
                if path == "/api/presets":
                    json_response(self, 200, {"presets": app.list_presets()})
                    return
                if path == "/api/describe":
                    json_response(self, 200, app.describe_catalog())
                    return
                if path == "/api/git/status":
                    json_response(self, 200, app.git_status())
                    return
                if path == "/api/pins":
                    json_response(self, 200, app.read_pins())
                    return
                if path == "/api/tags":
                    json_response(self, 200, app.read_tags())
                    return
                if path == "/api/columns":
                    json_response(self, 200, app.global_column_visibility())
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
                if path == "/api/experiments/subdirectories":
                    json_response(self, 200, app.experiment_subdirectories())
                    return
                if path == "/api/experiments/archived":
                    query = urllib.parse.parse_qs(parsed.query)
                    force = (query.get("refresh") or ["0"])[0] in ("1", "true", "yes")
                    json_response(self, 200, {"experiments": app.list_archived_experiments(force=force)})
                    return
                match = re.match(r"^/api/experiments/([^/]+)/experiment$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    exp_path = app.readable_experiment_path(experiment_id)
                    archived = app.is_archived_experiment_id(experiment_id)
                    payload = {
                        "id": experiment_id,
                        "path": str(exp_path),
                        "experiment_file": str(exp_path / "Experiment"),
                        "experiment": (exp_path / "Experiment").read_text(encoding="utf-8"),
                        "submit_lock": {"locked": False, "fields": {}} if archived else app.submit_lock(experiment_id),
                        "tag": app.tag_for_experiment(experiment_id),
                        "archived": archived,
                    }
                    payload.update(app.experiment_time_metadata(exp_path))
                    json_response(
                        self,
                        200,
                        payload,
                    )
                    return
                match = re.match(r"^/api/experiments/([^/]+)/guided$", path)
                if match:
                    json_response(self, 200, app.guided_model(urllib.parse.unquote(match.group(1))))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/results$", path)
                if match:
                    json_response(self, 200, app.results(urllib.parse.unquote(match.group(1))))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/columns$", path)
                if match:
                    json_response(self, 200, app.column_visibility(urllib.parse.unquote(match.group(1))))
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
                match = re.match(r"^/api/experiments/([^/]+)/download-options$", path)
                if match:
                    json_response(self, 200, app.experiment_download_options(urllib.parse.unquote(match.group(1))))
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
                    exp_path = app.readable_experiment_path(urllib.parse.unquote(match.group(1)))
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
                    query = urllib.parse.parse_qs(parsed.query)
                    archive = app.experiment_archive(
                        urllib.parse.unquote(match.group(1)),
                        include_dirs=archive_include_dirs_from_query(query),
                    )
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
                if path == "/api/workspaces":
                    json_response(self, 201, app.create_workspace(payload))
                    return
                if path == "/api/workspaces/switch":
                    json_response(self, 200, app.switch_workspace(payload))
                    return
                if path == "/api/experiments":
                    json_response(self, 201, app.create_experiment(payload))
                    return
                if path == "/api/git/push":
                    json_response(self, 200, app.git_commit_push(payload.get("message", "")))
                    return
                if path == "/api/repo-refs":
                    json_response(self, 200, app.fetch_repo_refs(payload))
                    return
                if path == "/api/status/squeue/cancel":
                    json_response(self, 200, app.slurm.cancel_job(payload))
                    return
                if path == "/api/status/squeue/cancel-all":
                    json_response(self, 200, app.slurm.cancel_user_jobs(payload))
                    return
                if path == "/api/plot/spack-r-libs/resolve":
                    force = bool(payload.get("force", False))
                    json_response(self, 202, app.resolve_spack_plot_cache_action(force=force))
                    return
                if path == "/api/pins":
                    json_response(self, 200, app.write_pins(payload.get("pinned") or []))
                    return
                if path == "/api/tags":
                    json_response(self, 200, app.upsert_tag(payload))
                    return
                if path == "/api/experiments/archive-subdirectory":
                    json_response(self, 200, app.archive_subdirectory_experiments(payload.get("directory") or ""))
                    return
                match = re.match(r"^/api/tags/([^/]+)/archive-experiments$", path)
                if match:
                    tag_name = urllib.parse.unquote(match.group(1))
                    json_response(self, 200, app.archive_tagged_experiments(tag_name))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/rename$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    json_response(self, 200, app.rename_experiment(experiment_id, payload))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/copy$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    json_response(self, 201, app.copy_experiment(experiment_id, payload))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/share$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    json_response(self, 200, app.share_experiment(experiment_id))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/cancel-submit$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    json_response(self, 200, app.cancel_submit(experiment_id, payload))
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
                match = re.match(r"^/api/experiments/([^/]+)/(check|probe|submit|submit-preview|parse|log-parse|log-resolve|plot)$", path)
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
                    if action == "submit-preview":
                        json_response(self, 200, app.submit_preview(experiment_id, payload))
                        return
                    if action == "parse":
                        json_response(self, 202, app.parse_action(experiment_id))
                        return
                    if action == "log-parse":
                        json_response(self, 200, app.parse_log_file(experiment_id, payload.get("path") or ""))
                        return
                    if action == "log-resolve":
                        json_response(self, 200, app.resolve_result_log(experiment_id, payload))
                        return
                    if action == "plot":
                        json_response(self, 202, app.plot_action(experiment_id, payload))
                        return
                match = re.match(r"^/api/experiments/([^/]+)/guided/render$", path)
                if match:
                    json_response(self, 200, app.render_guided_experiment(urllib.parse.unquote(match.group(1)), payload))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/graph-directory$", path)
                if match:
                    json_response(self, 200, app.graph_directory(urllib.parse.unquote(match.group(1)), payload))
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
                if path == "/api/settings":
                    json_response(self, 200, app.write_settings(payload))
                    return
                if path == "/api/columns":
                    json_response(self, 200, app.write_global_column_visibility(payload))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/tag$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    json_response(self, 200, app.assign_experiment_tag(experiment_id, payload.get("tag") or ""))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/description$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    json_response(self, 200, app.write_description(experiment_id, payload.get("description", "")))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/guided$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    json_response(self, 200, app.save_guided_experiment(experiment_id, payload))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/columns$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    json_response(self, 200, app.write_column_visibility(experiment_id, payload))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/plot-artifact-sets/([^/]+)$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    set_id = urllib.parse.unquote(match.group(2))
                    json_response(self, 200, app.rename_plot_artifact_set(experiment_id, set_id, payload))
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
                if path == "/api/workspaces":
                    query = urllib.parse.parse_qs(parsed.query)
                    workspace_path = (query.get("path") or [""])[0]
                    json_response(self, 200, app.remove_workspace(workspace_path))
                    return
                match = re.match(r"^/api/tags/([^/]+)$", path)
                if match:
                    tag_name = urllib.parse.unquote(match.group(1))
                    json_response(self, 200, app.delete_tag(tag_name))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/plot-artifacts/([^/]+)$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    artifact_id = urllib.parse.unquote(match.group(2))
                    json_response(self, 200, app.delete_plot_artifact(experiment_id, artifact_id))
                    return
                match = re.match(r"^/api/experiments/([^/]+)/plot-artifact-sets/([^/]+)$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    set_id = urllib.parse.unquote(match.group(2))
                    json_response(self, 200, app.delete_plot_artifact_set(experiment_id, set_id))
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
