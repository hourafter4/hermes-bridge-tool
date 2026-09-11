"""Exercise the bridge over real MCP stdio against a local mock Hermes API.

Run: uv run python -m unittest discover -s tests -v
"""

import json
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from uuid import UUID

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


API_KEY = "test-hermes-secret-never-reflect"


def result_text(result):
    return "\n".join(block.text for block in result.content if block.type == "text")


class BridgeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requests = []
        self.failure_status = None
        test = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else None
                test.requests.append(
                    (self.command, self.path, dict(self.headers), body)
                )
                status = test.failure_status or 200
                if test.failure_status:
                    response = {"error": "credential leaked by upstream: " + API_KEY}
                elif self.path == "/v1/capabilities":
                    response = {"features": {"run_submission": True, "run_status": True, "run_stop": True}}
                elif self.path == "/v1/runs":
                    status = 202
                    response = {"run_id": "run-123", "session_id": "session-123", "status": "started"}
                elif self.path == "/v1/runs/run-123":
                    response = {"run_id": "run-123", "status": "completed", "output": "Server updated"}
                elif self.path == "/v1/runs/run-123/stop":
                    response = {"status": "stopping"}
                else:
                    status = 404
                    response = {"error": "Unexpected route"}
                encoded = json.dumps(response).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.api_url = f"http://127.0.0.1:{self.server.server_port}"
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tempdir.cleanup()

    @asynccontextmanager
    async def session(self, **overrides):
        env = dict(os.environ)
        env.update(
            HERMES_BRIDGE_CONFIG=str(Path(self.tempdir.name) / "config.json"),
            HERMES_API_URL=self.api_url,
            HERMES_API_KEY=API_KEY,
            HERMES_API_KEY_FILE=str(Path(self.tempdir.name) / "missing-key"),
        )
        for key, value in overrides.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        params = StdioServerParameters(
            command=sys.executable, args=["-u", "-m", "hermes_bridge", "mcp"], env=env
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=10)
            ) as client:
                await client.initialize()
                yield client

    async def test_discovery_and_complete_run_lifecycle(self):
        async with self.session() as client:
            listed = await client.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            self.assertEqual(
                set(tools), {"hermes_check", "hermes_send", "hermes_status", "hermes_stop"}
            )
            for name in ("hermes_check", "hermes_status"):
                self.assertTrue(tools[name].annotations.readOnlyHint)
            for name in ("hermes_send", "hermes_stop"):
                self.assertFalse(tools[name].annotations.readOnlyHint)

            check = await client.call_tool("hermes_check", {})
            self.assertFalse(check.isError, result_text(check))
            self.assertIn("run_submission", result_text(check))
            sent = await client.call_tool(
                "hermes_send",
                {"instructions": "Check the deployment", "session_id": "session-existing", "request_id": "request-123"},
            )
            self.assertFalse(sent.isError, result_text(sent))
            self.assertIn("run-123", result_text(sent))
            status = await client.call_tool("hermes_status", {"run_id": "run-123"})
            self.assertFalse(status.isError, result_text(status))
            self.assertIn("Server updated", result_text(status))
            stopped = await client.call_tool("hermes_stop", {"run_id": "run-123"})
            self.assertFalse(stopped.isError, result_text(stopped))
            self.assertIn("stopping", result_text(stopped))

        self.assertEqual(
            [(method, path) for method, path, _, _ in self.requests],
            [("GET", "/v1/capabilities"), ("POST", "/v1/runs"), ("GET", "/v1/runs/run-123"), ("POST", "/v1/runs/run-123/stop")],
        )
        for _, _, headers, _ in self.requests:
            self.assertEqual(headers.get("Authorization"), f"Bearer {API_KEY}")
        _, _, headers, body = self.requests[1]
        self.assertEqual(headers.get("Idempotency-Key"), "request-123")
        self.assertEqual(body, {"input": "Check the deployment", "session_id": "session-existing"})

    async def test_new_run_generates_reusable_request_id(self):
        async with self.session() as client:
            result = await client.call_tool("hermes_send", {"instructions": "Report status"})
            self.assertFalse(result.isError, result_text(result))
        _, _, headers, body = self.requests[0]
        request_id = headers.get("Idempotency-Key")
        self.assertIsNotNone(request_id)
        UUID(request_id)
        self.assertIn(request_id, result_text(result))
        self.assertEqual(body, {"input": "Report status"})

    async def test_reads_api_key_from_file(self):
        key_file = Path(self.tempdir.name) / "api-key"
        key_file.write_text(API_KEY + "\n")
        key_file.chmod(0o600)
        async with self.session(HERMES_API_KEY=None, HERMES_API_KEY_FILE=str(key_file)) as client:
            result = await client.call_tool("hermes_check", {})
            self.assertFalse(result.isError, result_text(result))
        self.assertEqual(self.requests[0][2].get("Authorization"), f"Bearer {API_KEY}")

    async def test_missing_key_allows_discovery_but_errors_on_execution(self):
        async with self.session(HERMES_API_KEY=None) as client:
            self.assertEqual(len((await client.list_tools()).tools), 4)
            result = await client.call_tool("hermes_check", {})
            self.assertTrue(result.isError)
        self.assertEqual(self.requests, [])

    async def test_authentication_error_does_not_reflect_upstream_secret(self):
        self.failure_status = 401
        async with self.session() as client:
            result = await client.call_tool("hermes_check", {})
            self.assertTrue(result.isError)
            self.assertNotIn(API_KEY, result_text(result))
            self.assertNotIn("credential leaked by upstream", result_text(result))

    async def test_connection_failure_is_a_tool_error(self):
        # Reserve an unused local port without listening so connection fails promptly.
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{reserved.getsockname()[1]}"
            async with self.session(HERMES_API_URL=url) as client:
                result = await client.call_tool("hermes_check", {})
                self.assertTrue(result.isError)
                self.assertNotIn(API_KEY, result_text(result))
        self.assertEqual(self.requests, [])

    async def test_run_ids_cannot_change_api_route(self):
        async with self.session() as client:
            for tool in ("hermes_status", "hermes_stop"):
                with self.subTest(tool=tool):
                    result = await client.call_tool(tool, {"run_id": "../../v1/capabilities"})
                    self.assertTrue(result.isError)
        self.assertEqual(self.requests, [])

    async def test_cli_doctor_uses_shared_config_and_reports_auth_failure(self):
        key_file = Path(self.tempdir.name) / "api-key"
        key_file.write_text(API_KEY)
        config_file = Path(self.tempdir.name) / "config.json"
        config_file.write_text(json.dumps({"local_port": self.server.server_port, "api_key_file": str(key_file)}))
        env = {key: value for key, value in os.environ.items() if not key.startswith("HERMES_")}
        env["HERMES_BRIDGE_CONFIG"] = str(config_file)
        command = [sys.executable, "-m", "hermes_bridge", "doctor"]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ready"])
        self.failure_status = 401
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn("401", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(API_KEY, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
