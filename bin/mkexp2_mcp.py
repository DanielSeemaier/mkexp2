#!/usr/bin/env python3
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


EXPERIMENT_GUIDE = """mkexp2 experiment workflow for Codex

Connection model:
- This MCP server is a local stdio bridge to the mkexp2 web backend.
- The backend must already be running on the cluster/login node with `mkexp2 web --repo ...`.
- Reach it through SSH port forwarding, for example `ssh -L 8765:127.0.0.1:8765 user@cluster-login`.
- Use the session token printed by `mkexp2 web` when starting this MCP bridge.

Typical workflow:
1. Call `mkexp2_list_presets`, then `mkexp2_create_experiment` with a short name and optional preset.
2. Write or edit the Experiment file with `mkexp2_write_experiment`.
3. Validate with `mkexp2_check_experiment`.
4. Inspect algorithms with `mkexp2_probe_experiment`, usually with `flags: ["--algorithms"]`.
5. Submit with `mkexp2_submit_experiment`; omit `algorithms` to submit all enabled algorithms.
6. Poll `mkexp2_get_action` until the submit action completes.
7. Poll `mkexp2_get_progress` until `progress_json.complete` is true.
8. Run `mkexp2_parse_results`, poll the parse action, then call `mkexp2_get_stats`.

Experiment file basics:
- The file is zsh and must define one or more functions whose names start with `Experiment`.
- Common top-level directives are `System`, `Property`, `SystemProperty`,
  `DefineAlgorithm`, and `AlgorithmProperty`.
- Inside an `Experiment...()` function, use directives such as `Algorithms`,
  `Graphs`, `Ks`, `Seeds`, `Epsilons`, `Threads`, and `Property`.
- `DefineAlgorithm Child Base [extra CLI args...]` creates a named variant.
- `AlgorithmProperty Child repo_ref origin/my-branch` selects a Git branch/ref.
- Algorithm names submitted through `mkexp2_submit_experiment` must exactly match
  resolved names reported by `mkexp2_probe_experiment`.

Minimal example:
```zsh
#!/usr/bin/env zsh

System slurm
Property slurm.partition all
Property slurm.use_array true

DefineAlgorithm Feature KaMinPar
AlgorithmProperty Feature repo_ref origin/my-feature

ExperimentFeatureVsBaseline() {
  Algorithms Feature KaMinPar
  Graphs $HOME/Graphs/
  Ks 32
  Seeds 1
  Epsilons 0.03
  Threads 1x1x16
}
```

Safety constraints:
- The MCP bridge exposes only fixed web API operations; it does not expose a shell.
- Experiment ids are repo-relative ids from `mkexp2_list_experiments`.
"""


class WebApiError(RuntimeError):
    pass


class Mkexp2WebClient:
    def __init__(self, base_url, token, timeout=60):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method, path, payload=None, timeout=None):
        data = None
        headers = {
            "Accept": "application/json",
            "X-MKEXP2-Token": self.token,
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            message = body
            try:
                message = json.loads(body).get("error", body)
            except json.JSONDecodeError:
                pass
            raise WebApiError(f"{method} {path} failed with HTTP {exc.code}: {message}") from exc
        except urllib.error.URLError as exc:
            raise WebApiError(f"{method} {path} failed: {exc.reason}") from exc

        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise WebApiError(f"{method} {path} returned non-JSON response") from exc

    def get(self, path, timeout=None):
        return self.request("GET", path, timeout=timeout)

    def post(self, path, payload=None, timeout=None):
        return self.request("POST", path, payload or {}, timeout=timeout)

    def put(self, path, payload=None, timeout=None):
        return self.request("PUT", path, payload or {}, timeout=timeout)

    def delete(self, path, timeout=None):
        return self.request("DELETE", path, timeout=timeout)


def quote_segment(value):
    return urllib.parse.quote(str(value), safe="")


def parse_stdout_json(command_result):
    if not isinstance(command_result, dict):
        return None
    stdout = str(command_result.get("stdout") or "").strip()
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def input_schema(properties=None, required=None):
    return {
        "type": "object",
        "properties": properties or {},
        "required": required or [],
        "additionalProperties": False,
    }


def string_schema(description):
    return {"type": "string", "description": description}


def array_schema(description):
    return {
        "type": "array",
        "description": description,
        "items": {"type": "string"},
    }


class Mkexp2McpServer:
    def __init__(self, client):
        self.client = client
        self.tools = self._build_tools()

    def _build_tools(self):
        return {
            "mkexp2_get_experiment_guide": {
                "description": "Return concise instructions for creating, checking, submitting, and tracking mkexp2 experiments.",
                "inputSchema": input_schema(),
                "handler": lambda args: {"guide": EXPERIMENT_GUIDE},
            },
            "mkexp2_list_presets": {
                "description": "List available mkexp2 init presets from the remote web backend.",
                "inputSchema": input_schema(),
                "handler": lambda args: self.client.get("/api/presets"),
            },
            "mkexp2_list_experiments": {
                "description": "List experiment directories known to the configured experiment repo.",
                "inputSchema": input_schema(),
                "handler": lambda args: self.client.get("/api/experiments"),
            },
            "mkexp2_create_experiment": {
                "description": "Create a new experiment directory, optionally initialized from a preset.",
                "inputSchema": input_schema(
                    {
                        "name": string_schema("Short human name used by the backend name template."),
                        "preset": string_schema("Optional preset name from mkexp2_list_presets."),
                        "experiment": string_schema("Optional raw Experiment file content."),
                    },
                    ["name"],
                ),
                "handler": self.create_experiment,
            },
            "mkexp2_read_experiment": {
                "description": "Read an experiment's raw Experiment file.",
                "inputSchema": input_schema(
                    {"experiment_id": string_schema("Repo-relative experiment id.")},
                    ["experiment_id"],
                ),
                "handler": self.read_experiment,
            },
            "mkexp2_write_experiment": {
                "description": "Replace an experiment's raw Experiment file.",
                "inputSchema": input_schema(
                    {
                        "experiment_id": string_schema("Repo-relative experiment id."),
                        "experiment": string_schema("Full Experiment file content."),
                    },
                    ["experiment_id", "experiment"],
                ),
                "handler": self.write_experiment,
            },
            "mkexp2_check_experiment": {
                "description": "Run mkexp2 check --json for an experiment and return parsed check JSON when available.",
                "inputSchema": input_schema(
                    {"experiment_id": string_schema("Repo-relative experiment id.")},
                    ["experiment_id"],
                ),
                "handler": self.check_experiment,
            },
            "mkexp2_probe_experiment": {
                "description": "Run mkexp2 probe for an experiment. Use flags such as --algorithms, --jobs, or --calls.",
                "inputSchema": input_schema(
                    {
                        "experiment_id": string_schema("Repo-relative experiment id."),
                        "selector": string_schema("Optional experiment function/display selector."),
                        "flags": array_schema("Allowed flags: --algorithms, --graphs, --topologies, --run-properties, --jobs, --calls."),
                    },
                    ["experiment_id"],
                ),
                "handler": self.probe_experiment,
            },
            "mkexp2_submit_experiment": {
                "description": "Generate and submit an experiment. Omit algorithms to submit all enabled algorithms.",
                "inputSchema": input_schema(
                    {
                        "experiment_id": string_schema("Repo-relative experiment id."),
                        "algorithms": array_schema("Optional exact algorithm names to submit."),
                        "force": {"type": "boolean", "description": "Submit even if mkexp2 check fails."},
                    },
                    ["experiment_id"],
                ),
                "handler": self.submit_experiment,
            },
            "mkexp2_get_action": {
                "description": "Poll an asynchronous submit/parse/plot action by id.",
                "inputSchema": input_schema(
                    {"action_id": string_schema("Action id returned by submit/parse/plot.")},
                    ["action_id"],
                ),
                "handler": self.get_action,
            },
            "mkexp2_get_progress": {
                "description": "Run mkexp2 progress --json for an experiment and return structured completion state.",
                "inputSchema": input_schema(
                    {"experiment_id": string_schema("Repo-relative experiment id.")},
                    ["experiment_id"],
                ),
                "handler": self.get_progress,
            },
            "mkexp2_parse_results": {
                "description": "Start mkexp2 parse for an experiment. Poll the returned action id.",
                "inputSchema": input_schema(
                    {"experiment_id": string_schema("Repo-relative experiment id.")},
                    ["experiment_id"],
                ),
                "handler": self.parse_results,
            },
            "mkexp2_get_stats": {
                "description": "Run mkexp2 stats --json for an experiment and return geometric-mean cut/time summaries.",
                "inputSchema": input_schema(
                    {"experiment_id": string_schema("Repo-relative experiment id.")},
                    ["experiment_id"],
                ),
                "handler": self.get_stats,
            },
            "mkexp2_get_results": {
                "description": "Fetch parsed CSV result files for an experiment.",
                "inputSchema": input_schema(
                    {"experiment_id": string_schema("Repo-relative experiment id.")},
                    ["experiment_id"],
                ),
                "handler": self.get_results,
            },
            "mkexp2_clear_submit_lock": {
                "description": "Clear an experiment submit lock after a failed/crashed submission.",
                "inputSchema": input_schema(
                    {"experiment_id": string_schema("Repo-relative experiment id.")},
                    ["experiment_id"],
                ),
                "handler": self.clear_submit_lock,
            },
        }

    def tool_definitions(self):
        return [
            {
                "name": name,
                "description": spec["description"],
                "inputSchema": spec["inputSchema"],
            }
            for name, spec in self.tools.items()
        ]

    def create_experiment(self, args):
        if args.get("preset") and args.get("experiment"):
            raise ValueError("create with either preset or experiment, not both")
        payload = {"name": args["name"]}
        if args.get("preset"):
            payload["preset"] = args["preset"]
        if args.get("experiment"):
            payload["experiment"] = args["experiment"]
        return self.client.post("/api/experiments", payload)

    def read_experiment(self, args):
        exp = quote_segment(args["experiment_id"])
        return self.client.get(f"/api/experiments/{exp}/experiment")

    def write_experiment(self, args):
        exp = quote_segment(args["experiment_id"])
        return self.client.put(
            f"/api/experiments/{exp}/experiment",
            {"experiment": args["experiment"]},
        )

    def check_experiment(self, args):
        exp = quote_segment(args["experiment_id"])
        result = self.client.post(f"/api/experiments/{exp}/check", {})
        return {**result, "check_json": parse_stdout_json(result)}

    def probe_experiment(self, args):
        exp = quote_segment(args["experiment_id"])
        payload = {}
        if args.get("selector"):
            payload["selector"] = args["selector"]
        if args.get("flags"):
            payload["flags"] = args["flags"]
        return self.client.post(f"/api/experiments/{exp}/probe", payload)

    def submit_experiment(self, args):
        exp = quote_segment(args["experiment_id"])
        payload = {
            "algorithms": args.get("algorithms") or [],
            "force": bool(args.get("force")),
        }
        return self.client.post(f"/api/experiments/{exp}/submit", payload, timeout=120)

    def get_action(self, args):
        action = quote_segment(args["action_id"])
        return self.client.get(f"/api/actions/{action}")

    def get_progress(self, args):
        exp = quote_segment(args["experiment_id"])
        return self.client.get(f"/api/experiments/{exp}/progress")

    def parse_results(self, args):
        exp = quote_segment(args["experiment_id"])
        return self.client.post(f"/api/experiments/{exp}/parse", {}, timeout=120)

    def get_stats(self, args):
        exp = quote_segment(args["experiment_id"])
        return self.client.get(f"/api/experiments/{exp}/stats")

    def get_results(self, args):
        exp = quote_segment(args["experiment_id"])
        return self.client.get(f"/api/experiments/{exp}/results")

    def clear_submit_lock(self, args):
        exp = quote_segment(args["experiment_id"])
        return self.client.delete(f"/api/experiments/{exp}/submit-lock")

    def call_tool(self, name, arguments):
        if name not in self.tools:
            raise ValueError(f"unknown tool: {name}")
        args = arguments or {}
        schema = self.tools[name]["inputSchema"]
        for required in schema.get("required", []):
            if required not in args:
                raise ValueError(f"missing required argument: {required}")
        return self.tools[name]["handler"](args)

    def handle_message(self, message):
        msg_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}

        if method == "initialize":
            protocol = params.get("protocolVersion") or "2024-11-05"
            return rpc_result(
                msg_id,
                {
                    "protocolVersion": protocol,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mkexp2-web-bridge", "version": "0.1.0"},
                },
            )
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return rpc_result(msg_id, {})
        if method == "tools/list":
            return rpc_result(msg_id, {"tools": self.tool_definitions()})
        if method == "tools/call":
            try:
                result = self.call_tool(params.get("name"), params.get("arguments") or {})
                return rpc_result(msg_id, tool_payload(result))
            except Exception as exc:
                return rpc_result(msg_id, tool_payload({"error": str(exc)}, is_error=True))
        return rpc_error(msg_id, -32601, f"method not found: {method}")


def tool_payload(data, is_error=False):
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(data, indent=2, sort_keys=True),
            }
        ],
        "isError": bool(is_error),
    }


def rpc_result(msg_id, result):
    response = {"jsonrpc": "2.0", "result": result}
    if msg_id is not None:
        response["id"] = msg_id
    return response


def rpc_error(msg_id, code, message):
    response = {"jsonrpc": "2.0", "error": {"code": code, "message": message}}
    if msg_id is not None:
        response["id"] = msg_id
    return response


def read_message(input_buffer):
    first = input_buffer.readline()
    if not first:
        return None
    if first.lstrip().startswith(b"{"):
        return json.loads(first.decode("utf-8"))

    headers = {}
    line = first
    while line and line not in (b"\r\n", b"\n"):
        text = line.decode("ascii", errors="replace").strip()
        if ":" in text:
            key, value = text.split(":", 1)
            headers[key.lower()] = value.strip()
        line = input_buffer.readline()

    length = int(headers.get("content-length", "0"))
    if length <= 0:
        raise ValueError("missing Content-Length header")
    body = input_buffer.read(length)
    return json.loads(body.decode("utf-8"))


def write_message(output_buffer, message):
    body = json.dumps(message, separators=(",", ":")).encode("utf-8")
    output_buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    output_buffer.write(body)
    output_buffer.flush()


def serve_stdio(server):
    while True:
        message = read_message(sys.stdin.buffer)
        if message is None:
            break
        response = server.handle_message(message)
        if response is not None and message.get("id") is not None:
            write_message(sys.stdout.buffer, response)


def main():
    parser = argparse.ArgumentParser(description="Serve a stdio MCP bridge to mkexp2 web.")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    client = Mkexp2WebClient(args.url, args.token)
    serve_stdio(Mkexp2McpServer(client))


if __name__ == "__main__":
    main()
