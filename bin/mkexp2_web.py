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
SLURM_CACHE_SECONDS = 15
EXPERIMENT_SKIP_DIRS = {".git", ".mkexp2", "jobs", "logs", "results", "slurm"}
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
    def __init__(self, repo, mkexp2, name_template, token):
        self.repo = Path(repo).resolve()
        self.mkexp2 = Path(mkexp2).resolve()
        self.name_template = name_template
        self.token = token
        self.actions = ActionStore()
        self.slurm = SlurmStatus()

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

    def list_experiments(self):
        experiments = []
        for root, dirnames, filenames in os.walk(self.repo):
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in EXPERIMENT_SKIP_DIRS and not name.startswith(".")
            )
            if "Experiment" not in filenames:
                continue
            exp_file = Path(root) / "Experiment"
            path = exp_file.parent.resolve()
            rel = path.relative_to(self.repo).as_posix()
            parts = rel.split("/")
            if any(part in EXPERIMENT_SKIP_DIRS or part.startswith(".") for part in parts):
                continue
            self.experiment_path(rel)
            stat = exp_file.stat()
            experiments.append(
                {
                    "id": rel,
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
                return {"id": experiment_id, "path": str(path), "preset": preset, "init": init}

            raw = payload.get("experiment")
            if not raw:
                raw = experiment_from_form(name, payload.get("form") or {})
            (path / "Experiment").write_text(raw, encoding="utf-8")
            return {"id": experiment_id, "path": str(path)}
        except Exception:
            shutil.rmtree(path, ignore_errors=True)
            raise

    def command(self, experiment_id, argv, timeout=60):
        return run_command([str(self.mkexp2), *argv], cwd=self.experiment_path(experiment_id), timeout=timeout)

    def submit_action(self, experiment_id, payload):
        algorithms = payload.get("algorithms") or []
        force = bool(payload.get("force"))
        if not isinstance(algorithms, list) or not all(isinstance(item, str) for item in algorithms):
            raise ValueError("algorithms must be an array of strings")

        def action():
            check = self.command(experiment_id, ["check"], timeout=60)
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
                ["zsh", "./submit.sh", *algorithms],
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
      margin-bottom: 16px;
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
    .experiment-row {
      width: 100%;
      text-align: left;
      height: auto;
      min-height: 38px;
      padding: 8px 10px;
    }
    .experiment-row.active {
      border-color: var(--accent);
      background: #e8f5f3;
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
    .check-json pre {
      max-height: 180px;
      overflow: auto;
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
      margin: 8px 0 0;
      padding: 8px;
      border-radius: 6px;
      background: #101820;
      color: #e7eef2;
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
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
    .csv-summary {
      color: var(--muted);
      font-size: 12px;
    }
    .compare-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 12px;
      align-items: start;
    }
    .compare-pane {
      display: grid;
      gap: 8px;
      min-width: 0;
    }
    .compare-pane-title {
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .csv-empty {
      color: var(--muted);
      padding: 14px;
      border: 1px dashed var(--border);
      border-radius: 6px;
      background: #fbfcfd;
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
      .chips { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">
        <h1>mkexp2</h1>
        <button id="refresh">Refresh</button>
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
    <main class="main">
      <div class="view-tabs">
        <button class="view-tab active" data-view="experiment-view">Experiment</button>
        <button class="view-tab" data-view="results-view">Results</button>
        <button class="view-tab" data-view="compare-view">Compare</button>
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
                <button id="save">Save</button>
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
                <button class="primary" id="submit">Submit Selected</button>
              </div>
            </section>
            <section class="panel">
              <div class="panel-header">
                <div class="panel-title">Output</div>
              </div>
              <div class="panel-body">
                <div id="output" class="output"></div>
              </div>
            </section>
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
              <button id="load-results">Load CSVs</button>
            </div>
          </div>
          <div class="panel-body">
            <div class="csv-tools">
              <div id="result-file-tabs" class="result-file-tabs"></div>
              <div class="column-actions">
                <button id="columns-all">All columns</button>
                <button id="columns-none">No columns</button>
              </div>
              <div id="column-selector" class="column-selector"></div>
            </div>
            <div id="results" class="csv-empty">Select an experiment, then load CSV results.</div>
          </div>
        </section>
      </section>
      <section id="compare-view" class="view-panel">
        <section class="panel">
          <div class="panel-header">
            <div>
              <div class="panel-title">Compare CSVs</div>
              <div id="compare-summary" class="csv-summary">No comparison loaded.</div>
            </div>
            <button id="load-compare-results">Load CSVs</button>
          </div>
          <div class="panel-body">
            <div class="csv-tools">
              <div class="csv-selectors">
                <select id="compare-left"></select>
                <select id="compare-right"></select>
              </div>
              <div class="column-actions">
                <button id="compare-columns-all">All columns</button>
                <button id="compare-columns-none">No columns</button>
              </div>
              <div id="compare-column-selector" class="column-selector"></div>
            </div>
            <div class="compare-grid">
              <div class="compare-pane">
                <div id="compare-left-title" class="compare-pane-title">Left CSV</div>
                <div id="compare-left-table" class="csv-empty">Select two CSV files to compare.</div>
              </div>
              <div class="compare-pane">
                <div id="compare-right-title" class="compare-pane-title">Right CSV</div>
                <div id="compare-right-table" class="csv-empty">Select two CSV files to compare.</div>
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
            <button id="plot-results">Generate Plots</button>
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
      algorithms: [],
      presets: [],
      openDirs: new Set(),
      results: [],
      resultsFor: null,
      activeResult: '',
      compareLeft: '',
      compareRight: '',
      compareColumnModes: {},
      activeView: 'experiment-view'
    };
    const tokenInput = document.getElementById('token');
    const editor = document.getElementById('experiment-editor');
    const editorHighlight = document.getElementById('experiment-highlight');
    tokenInput.value = localStorage.getItem('mkexp2-token') || '';
    tokenInput.addEventListener('change', () => {
      localStorage.setItem('mkexp2-token', tokenInput.value);
      out('');
      if (token()) {
        refreshPresets().catch(err => out(String(err)));
        refreshExperiments().catch(err => out(String(err)));
        refreshStatus().catch(err => out(String(err)));
      }
    });

    function token() { return tokenInput.value; }
    function out(value) {
      const box = document.getElementById('output');
      box.className = 'output';
      box.textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
    }
    function clearTransientOutput() {
      const box = document.getElementById('output');
      if (/session token|missing or invalid token/i.test(box.textContent)) {
        box.textContent = '';
      }
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
      const box = document.getElementById('output');
      box.className = 'output rendered-output';
      box.innerHTML = '';

      const ok = Number(result.returncode) === 0;
      const combined = `${result.stdout || ''}\n${result.stderr || ''}`;
      const cleanOutput = stripAnsi(combined);
      const card = document.createElement('div');
      card.className = `check-card ${ok ? 'ok' : 'fail'}`;

      const title = document.createElement('h3');
      title.className = ok ? 'status-ok' : 'status-bad';
      title.textContent = ok ? 'Check passed' : 'Check failed';
      card.appendChild(title);

      const message = document.createElement('div');
      message.className = 'check-message';
      message.textContent = ok
        ? 'Saved the Experiment file and mkexp2 check completed successfully.'
        : 'Saved the Experiment file, but mkexp2 check reported problems.';
      card.appendChild(message);

      const facts = document.createElement('div');
      facts.className = 'check-facts';
      appendCheckFact(facts, 'Return code', String(result.returncode));
      appendCheckFact(facts, 'Errors', checkCount(cleanOutput, 'errors') ?? (ok ? '0' : 'unknown'));
      appendCheckFact(facts, 'Warnings', checkCount(cleanOutput, 'warnings') ?? '0');
      appendCheckFact(facts, 'Elapsed', `${result.elapsed_seconds ?? '?'}s`);
      if (saveResult?.path) appendCheckFact(facts, 'Saved', saveResult.path);
      card.appendChild(facts);

      const importantLines = cleanOutput
        .split(/\r?\n/)
        .map(line => line.trim())
        .filter(line => /\[(fail|warn)\]/i.test(line));
      if (importantLines.length) {
        const lines = document.createElement('div');
        lines.className = 'check-lines';
        for (const line of importantLines) {
          const item = document.createElement('div');
          item.className = 'check-line ' + (/\[fail\]/i.test(line) ? 'fail' : 'warn');
          item.textContent = line;
          lines.appendChild(item);
        }
        card.appendChild(lines);
      }

      const details = document.createElement('details');
      details.className = 'check-json';
      const summary = document.createElement('summary');
      summary.textContent = 'Command JSON';
      const pre = document.createElement('pre');
      pre.textContent = JSON.stringify(result, null, 2);
      details.appendChild(summary);
      details.appendChild(pre);
      card.appendChild(details);
      box.appendChild(card);
    }
    async function api(path, options = {}) {
      const headers = Object.assign({ 'X-MKEXP2-Token': token() }, options.headers || {});
      if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
      const response = await fetch(path, Object.assign({}, options, { headers }));
      if (!response.ok) throw new Error(await response.text());
      return response.headers.get('content-type')?.includes('application/json')
        ? response.json()
        : response.text();
    }
    function esc(value) {
      return String(value ?? '').replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[char]));
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
    function compareCellClass(value, peerValue, mode) {
      if (!mode) return '';
      const current = numericCsvValue(value);
      const peer = numericCsvValue(peerValue);
      if (current === null || peer === null) return '';
      if (current === peer) return 'compare-equal';
      const isGood = mode === 1 ? current < peer : current > peer;
      return isGood ? 'compare-good' : 'compare-bad';
    }
    function cycleCompareColumn(header) {
      const current = state.compareColumnModes[header] || 0;
      const next = (current + 1) % 3;
      if (next === 0) {
        delete state.compareColumnModes[header];
      } else {
        state.compareColumnModes[header] = next;
      }
      setTimeout(renderCompareWorkspace, 0);
    }
    function syncCompareScroll(leftBox, rightBox) {
      let syncing = false;
      const sync = (source, target) => {
        if (syncing) return;
        syncing = true;
        target.scrollTop = source.scrollTop;
        target.scrollLeft = source.scrollLeft;
        requestAnimationFrame(() => {
          syncing = false;
        });
      };
      leftBox.onscroll = () => sync(leftBox, rightBox);
      rightBox.onscroll = () => sync(rightBox, leftBox);
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
      const peerHeaderIndex = new Map();
      if (options.peer) {
        options.peer.headers.forEach((header, index) => {
          if (!peerHeaderIndex.has(header)) peerHeaderIndex.set(header, index);
        });
      }
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
          if (options.compare && options.peer) {
            const peerIndex = peerHeaderIndex.has(header) ? peerHeaderIndex.get(header) : -1;
            const peerRow = options.peer.rows[rowIndex] || [];
            const peerValue = peerIndex >= 0 ? (peerRow[peerIndex] ?? '') : '';
            const cellClass = compareCellClass(value, peerValue, state.compareColumnModes[header] || 0);
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
      for (const file of state.results) {
        const button = document.createElement('button');
        button.className = 'csv-file-tab' + (file.name === state.activeResult ? ' active' : '');
        button.textContent = csvLabel(file.name);
        button.title = csvLabel(file.name);
        button.onclick = () => {
          state.activeResult = file.name;
          renderResultsWorkspace();
        };
        tabs.appendChild(button);
      }
    }
    function renderResultsWorkspace() {
      const summary = document.getElementById('results-summary');
      const box = document.getElementById('results');
      renderResultFileTabs();
      if (!state.selected) {
        summary.textContent = 'No experiment selected.';
        box.className = 'csv-empty';
        box.textContent = 'Select an experiment first.';
        const selector = document.getElementById('column-selector');
        selector.className = 'column-selector';
        selector.innerHTML = '';
        return;
      }
      if (!state.results.length) {
        summary.textContent = 'No CSV files loaded.';
        box.className = 'csv-empty';
        box.textContent = 'No CSV files loaded.';
        const selector = document.getElementById('column-selector');
        selector.className = 'column-selector';
        selector.innerHTML = '';
        return;
      }
      if (!findResult(state.activeResult)) state.activeResult = state.results[0].name;
      const file = findResult(state.activeResult);
      summary.textContent = `${state.results.length} CSV file(s), ${file.rows.length} row(s) in ${csvLabel(file.name)}`;
      renderColumnSelector(document.getElementById('column-selector'), file.headers, renderResultsWorkspace);
      document.getElementById('columns-all').onclick = () => setAllColumns(file.headers, true, renderResultsWorkspace);
      document.getElementById('columns-none').onclick = () => setAllColumns(file.headers, false, renderResultsWorkspace);
      renderCsvTable(file, box, file.headers);
    }
    function renderCompareSelectors() {
      const left = document.getElementById('compare-left');
      const right = document.getElementById('compare-right');
      left.innerHTML = '';
      right.innerHTML = '';
      for (const select of [left, right]) {
        for (const file of state.results) {
          const option = document.createElement('option');
          option.value = file.name;
          option.textContent = csvLabel(file.name);
          select.appendChild(option);
        }
      }
      if (!findResult(state.compareLeft)) state.compareLeft = state.results[0]?.name || '';
      if (!findResult(state.compareRight)) state.compareRight = state.results[1]?.name || state.results[0]?.name || '';
      left.value = state.compareLeft;
      right.value = state.compareRight;
      left.onchange = () => {
        state.compareLeft = left.value;
        renderCompareWorkspace();
      };
      right.onchange = () => {
        state.compareRight = right.value;
        renderCompareWorkspace();
      };
    }
    function renderCompareWorkspace() {
      const summary = document.getElementById('compare-summary');
      const leftBox = document.getElementById('compare-left-table');
      const rightBox = document.getElementById('compare-right-table');
      const compareAllButton = document.getElementById('compare-columns-all');
      const compareNoneButton = document.getElementById('compare-columns-none');
      compareAllButton.disabled = true;
      compareNoneButton.disabled = true;
      if (!state.selected) {
        summary.textContent = 'No experiment selected.';
        document.getElementById('compare-left').innerHTML = '';
        document.getElementById('compare-right').innerHTML = '';
        leftBox.className = 'csv-empty';
        rightBox.className = 'csv-empty';
        leftBox.textContent = 'Select an experiment first.';
        rightBox.textContent = 'Select an experiment first.';
        return;
      }
      if (!state.results.length) {
        summary.textContent = 'No CSV files loaded.';
        document.getElementById('compare-left').innerHTML = '';
        document.getElementById('compare-right').innerHTML = '';
        leftBox.className = 'csv-empty';
        rightBox.className = 'csv-empty';
        leftBox.textContent = 'Load CSV files first.';
        rightBox.textContent = 'Load CSV files first.';
        const selector = document.getElementById('compare-column-selector');
        selector.className = 'column-selector';
        selector.innerHTML = '';
        return;
      }
      renderCompareSelectors();
      const leftFile = findResult(state.compareLeft);
      const rightFile = findResult(state.compareRight);
      const headers = headersForFiles([leftFile, rightFile]);
      summary.textContent = `${csvLabel(leftFile?.name)} vs ${csvLabel(rightFile?.name)}`;
      document.getElementById('compare-left-title').textContent = leftFile ? csvLabel(leftFile.name) : 'Left CSV';
      document.getElementById('compare-right-title').textContent = rightFile ? csvLabel(rightFile.name) : 'Right CSV';
      if (leftFile && rightFile && leftFile.rows.length !== rightFile.rows.length) {
        const message = `Cannot compare: row counts differ (${csvLabel(leftFile.name)} has ${leftFile.rows.length}, ${csvLabel(rightFile.name)} has ${rightFile.rows.length}).`;
        summary.textContent = message;
        const selector = document.getElementById('compare-column-selector');
        selector.className = 'csv-empty status-bad';
        selector.textContent = 'Row-wise comparison is disabled until both CSV files have the same number of rows.';
        leftBox.onscroll = null;
        rightBox.onscroll = null;
        leftBox.className = 'csv-empty status-bad';
        rightBox.className = 'csv-empty status-bad';
        leftBox.textContent = message;
        rightBox.textContent = message;
        return;
      }
      renderColumnSelector(document.getElementById('compare-column-selector'), headers, renderCompareWorkspace);
      compareAllButton.disabled = false;
      compareNoneButton.disabled = false;
      compareAllButton.onclick = () => setAllColumns(headers, true, renderCompareWorkspace);
      compareNoneButton.onclick = () => setAllColumns(headers, false, renderCompareWorkspace);
      renderCsvTable(leftFile, leftBox, headers, { compare: true, peer: rightFile });
      renderCsvTable(rightFile, rightBox, headers, { compare: true, peer: leftFile });
      syncCompareScroll(leftBox, rightBox);
    }
    async function ensureResultsLoaded() {
      if (!state.selected) return;
      if (state.resultsFor !== state.selected) await loadResults();
    }
    async function activateCsvView(viewId) {
      await ensureResultsLoaded();
      if (viewId === 'results-view') renderResultsWorkspace();
      if (viewId === 'compare-view') renderCompareWorkspace();
    }
    function setView(viewId) {
      state.activeView = viewId;
      document.querySelectorAll('.view-tab').forEach(button => {
        button.classList.toggle('active', button.dataset.view === viewId);
      });
      document.querySelectorAll('.view-panel').forEach(panel => {
        panel.classList.toggle('active', panel.id === viewId);
      });
      if (viewId === 'results-view' || viewId === 'compare-view') {
        activateCsvView(viewId).catch(err => out(String(err)));
      }
      if (viewId === 'plots-view') {
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
        const button = document.createElement('button');
        button.className = 'experiment-row' + (state.selected === exp.id ? ' active' : '');
        button.textContent = exp.label;
        button.title = exp.id;
        button.onclick = () => selectExperiment(exp.id);
        container.appendChild(button);
      }
    }
    async function refreshExperiments() {
      const data = await api('/api/experiments');
      clearTransientOutput();
      state.experiments = data.experiments;
      const list = document.getElementById('experiments');
      list.innerHTML = '';
      renderExperimentTree(list, experimentTree(state.experiments));
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
    async function selectExperiment(id) {
      state.selected = id;
      state.results = [];
      state.resultsFor = null;
      state.activeResult = '';
      state.compareLeft = '';
      state.compareRight = '';
      state.compareColumnModes = {};
      renderResultsWorkspace();
      renderCompareWorkspace();
      openExperimentAncestors(id);
      await refreshExperiments();
      const data = await api(`/api/experiments/${encodeURIComponent(id)}/experiment`);
      clearTransientOutput();
      document.getElementById('selected-title').textContent = id;
      document.getElementById('selected-path').textContent = data.path;
      setEditorValue(data.experiment);
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
    async function saveExperiment() {
      if (!state.selected) return;
      const data = await persistExperiment();
      try {
        await loadAlgorithms();
        out(Object.assign({}, data, { algorithms: state.algorithms }));
      } catch (err) {
        out(Object.assign({}, data, { algorithm_refresh_error: String(err) }));
      }
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
          const box = document.getElementById('output');
          const note = document.createElement('div');
          note.className = 'check-line warn';
          note.textContent = `Algorithm refresh failed after check: ${String(err)}`;
          box.querySelector('.check-card')?.appendChild(note);
        }
      } finally {
        button.disabled = false;
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
        }
      } finally {
        setActionButtons(['parse-results'], false);
      }
    }
    function renderPlotPanel(action = null) {
      const summary = document.getElementById('plots-summary');
      const file = document.getElementById('plot-file');
      if (!summary || !file) return;
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
          : 'Generate plots for the selected experiment.';
      const pdfUrl = `/api/experiments/${encodeURIComponent(state.selected)}/plots.pdf`;
      file.className = 'csv-empty';
      file.innerHTML = `plots.pdf will be available at <a href="${esc(pdfUrl)}" target="_blank" rel="noreferrer">plots.pdf</a> after generation.`;
    }
    async function plotExperiment() {
      if (!state.selected) return;
      setView('plots-view');
      setActionButtons(['plot-results'], true);
      try {
        const action = await api(`/api/experiments/${encodeURIComponent(state.selected)}/plot`, {
          method: 'POST',
          body: JSON.stringify({})
        });
        renderPlotPanel({ status: 'running', id: action.id });
        await watchAction(action.id, current => renderPlotPanel(current));
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
      state.activeResult = state.results[0]?.name || '';
      state.compareLeft = state.results[0]?.name || '';
      state.compareRight = state.results[1]?.name || state.results[0]?.name || '';
      state.compareColumnModes = {};
      renderResultsWorkspace();
      renderCompareWorkspace();
    }
    function cpuCount(value) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : NaN;
    }
    function formatCpuCount(value) {
      const text = String(value ?? '').trim();
      return text ? `${text} CPU` : 'n/a';
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
      refreshExperiments().catch(err => out(String(err)));
    };
    document.getElementById('refresh-status').onclick = refreshStatus;
    document.getElementById('create').onclick = createExperiment;
    document.getElementById('save').onclick = saveExperiment;
    document.getElementById('check').onclick = checkExperiment;
    document.getElementById('submit').onclick = submitExperiment;
    document.getElementById('parse-results').onclick = parseExperiment;
    document.getElementById('plot-results').onclick = plotExperiment;
    document.getElementById('load-results').onclick = loadResults;
    document.getElementById('load-compare-results').onclick = loadResults;
    document.querySelectorAll('.view-tab').forEach(button => {
      button.onclick = () => setView(button.dataset.view);
    });
    if (token()) {
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
            return secrets.compare_digest(supplied, app.token)

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            try:
                if path == "/":
                    text_response(self, 200, HTML, "text/html; charset=utf-8")
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
                if path == "/api/experiments":
                    json_response(self, 200, {"experiments": app.list_experiments()})
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
                        },
                    )
                    return
                match = re.match(r"^/api/experiments/([^/]+)/results$", path)
                if match:
                    json_response(self, 200, app.results(urllib.parse.unquote(match.group(1))))
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
                match = re.match(r"^/api/experiments/([^/]+)/(check|probe|submit|parse|plot)$", path)
                if match:
                    experiment_id = urllib.parse.unquote(match.group(1))
                    action = match.group(2)
                    if action == "check":
                        json_response(self, 200, app.command(experiment_id, ["check"], timeout=60))
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

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mkexp2", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--name-template", default="%Y.%m.%d-<name>")
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
    app = Mkexp2WebApp(repo, args.mkexp2, args.name_template, token)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    print(f"mkexp2 web: http://{args.host}:{args.port}", flush=True)
    print(f"session token: {token}", flush=True)
    print(f"ssh tunnel: ssh -L {args.port}:{args.host}:{args.port} <user>@<cluster-login>", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
