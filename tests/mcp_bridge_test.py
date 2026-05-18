#!/usr/bin/env python3
import importlib.util
import io
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mkexp2_mcp", ROOT / "bin" / "mkexp2_mcp.py")
mkexp2_mcp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mkexp2_mcp)


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, path, timeout=None):
        self.calls.append(("GET", path, None, timeout))
        if path == "/api/presets":
            return {"presets": [{"name": "Default"}]}
        if path.endswith("/stats"):
            return {"ok": True, "stats_json": {"algorithms": []}}
        return {"ok": True, "path": path}

    def post(self, path, payload=None, timeout=None):
        self.calls.append(("POST", path, payload, timeout))
        if path.endswith("/check"):
            return {"returncode": 0, "stdout": '{"ok":true,"experiments":[]}\n', "stderr": ""}
        if path.endswith("/submit"):
            return {"id": "action-1", "status": "running"}
        return {"ok": True, "path": path, "payload": payload}

    def put(self, path, payload=None, timeout=None):
        self.calls.append(("PUT", path, payload, timeout))
        return {"saved": True}

    def delete(self, path, timeout=None):
        self.calls.append(("DELETE", path, None, timeout))
        return {"cleared": True}


class McpBridgeTest(unittest.TestCase):
    def test_lists_codex_experiment_tools(self):
        server = mkexp2_mcp.Mkexp2McpServer(FakeClient())
        response = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = [tool["name"] for tool in response["result"]["tools"]]

        self.assertIn("mkexp2_get_experiment_guide", names)
        self.assertIn("mkexp2_create_experiment", names)
        self.assertIn("mkexp2_submit_experiment", names)
        self.assertIn("mkexp2_get_progress", names)
        self.assertIn("mkexp2_get_stats", names)

    def test_write_experiment_routes_through_web_api_with_encoded_id(self):
        client = FakeClient()
        server = mkexp2_mcp.Mkexp2McpServer(client)

        result = server.call_tool(
            "mkexp2_write_experiment",
            {"experiment_id": "2026/run-a", "experiment": "ExperimentA() { :; }\n"},
        )

        self.assertTrue(result["saved"])
        self.assertEqual(
            client.calls,
            [
                (
                    "PUT",
                    "/api/experiments/2026%2Frun-a/experiment",
                    {"experiment": "ExperimentA() { :; }\n"},
                    None,
                )
            ],
        )

    def test_check_parses_json_stdout(self):
        client = FakeClient()
        server = mkexp2_mcp.Mkexp2McpServer(client)

        result = server.call_tool("mkexp2_check_experiment", {"experiment_id": "exp"})

        self.assertEqual(result["check_json"]["ok"], True)
        self.assertEqual(client.calls[0][0:2], ("POST", "/api/experiments/exp/check"))

    def test_submit_returns_action_for_polling(self):
        client = FakeClient()
        server = mkexp2_mcp.Mkexp2McpServer(client)

        result = server.call_tool(
            "mkexp2_submit_experiment",
            {"experiment_id": "exp", "algorithms": ["Feature"], "force": False},
        )

        self.assertEqual(result["id"], "action-1")
        self.assertEqual(
            client.calls[0],
            (
                "POST",
                "/api/experiments/exp/submit",
                {"algorithms": ["Feature"], "force": False},
                120,
            ),
        )

    def test_mcp_framing_round_trip(self):
        message = {"jsonrpc": "2.0", "id": 7, "method": "ping"}
        buffer = io.BytesIO()

        mkexp2_mcp.write_message(buffer, message)
        buffer.seek(0)

        self.assertEqual(mkexp2_mcp.read_message(buffer), message)

    def test_tool_errors_are_returned_as_mcp_tool_errors(self):
        server = mkexp2_mcp.Mkexp2McpServer(FakeClient())
        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "mkexp2_read_experiment", "arguments": {}},
            }
        )

        self.assertTrue(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertIn("missing required argument", payload["error"])


if __name__ == "__main__":
    unittest.main()
