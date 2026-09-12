"""Exercise the bridge over real MCP stdio against a local mock Hermes API.

Run: uv run python -m unittest discover -s tests -v
"""

import asyncio
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
from urllib.parse import parse_qs, urlsplit

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


API_KEY = "test-hermes-secret-never-reflect"


def result_text(result):
    return "\n".join(block.text for block in result.content if block.type == "text")


def result_json(result):
    if result.isError:
        raise AssertionError(result_text(result))
    return json.loads(result_text(result))


class BridgeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requests = []
        self.failure_status = None
        self.disconnect_transport = False
        self.features = {"run_submission": True, "run_status": True, "run_stop": True, "session_resources": True, "run_steer": True}
        self.run_responses = []
        self.message_responses = []
        self.turn_responses = []
        test = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                if test.disconnect_transport:
                    # Accept TCP, then close without an HTTP response. A bound,
                    # non-listening socket can hang instead of refusing on macOS.
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else None
                test.requests.append(
                    (self.command, self.path, dict(self.headers), body)
                )
                status = test.failure_status or 200
                path = urlsplit(self.path).path
                query = parse_qs(urlsplit(self.path).query)
                if test.failure_status:
                    response = {"error": "credential leaked by upstream: " + API_KEY}
                elif self.path == "/v1/capabilities":
                    response = {"features": test.features}
                elif self.path == "/v1/runs":
                    status = 202
                    response = {"run_id": "run-123", "status": "started", "replayed": False}
                elif self.path == "/v1/runs/run-123":
                    response = {"run_id": "run-123", "session_id": "session-123", "status": "completed", "output": "Server updated"}
                    if test.run_responses:
                        response = test.run_responses[0]
                        if len(test.run_responses) > 1:
                            test.run_responses.pop(0)
                elif self.path == "/v1/runs/run-123/stop":
                    response = {"status": "stopping"}
                elif self.path == "/v1/runs/run-123/steer":
                    response = {"object": "hermes.run.steer", "run_id": "run-123", "accepted": True}
                elif path == "/hermes-bridge-tool/v1/turns":
                    response = {"object": "list", "data": [{"session_id": "session-existing", "turn_id": "turn-123", "status": "started", "terminal": False}]}
                elif path == "/hermes-bridge-tool/v1/turns/turn-123":
                    response = {"session_id": "session-existing", "turn_id": "turn-123", "status": "unknown", "terminal": False}
                    if test.turn_responses:
                        response = test.turn_responses[0]
                        if len(test.turn_responses) > 1:
                            test.turn_responses.pop(0)
                elif path == "/api/sessions" and self.command == "GET":
                    response = {
                        "object": "list", "data": [],
                        "limit": int(query.get("limit", [20])[0]),
                        "offset": int(query.get("offset", [0])[0]),
                        "has_more": False,
                    }
                elif path == "/api/sessions" and self.command == "POST":
                    status = 201
                    response = {"object": "hermes.session", "session": {"id": "session-new", **body}}
                elif path == "/api/sessions/session-existing":
                    response = {"object": "hermes.session", "session": {"id": "session-existing", "source": "cli"}}
                elif path == "/api/sessions/session-existing/messages":
                    response = {
                        "object": "list", "session_id": "session-resolved",
                        "data": [{"id": 1, "role": "assistant", "content": "Earlier answer"}],
                        "pagination": {"limit": int(query.get("limit", [50])[0]), "offset": int(query.get("offset", [0])[0]), "has_more": False},
                    }
                    if test.message_responses:
                        response = test.message_responses[0]
                        if len(test.message_responses) > 1:
                            test.message_responses.pop(0)
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
            HERMES_BRIDGE_TOOL_CONFIG=str(Path(self.tempdir.name) / "config.json"),
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
            command=sys.executable, args=["-u", "-m", "hermes_bridge_tool", "mcp"], env=env
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
                set(tools), {
                    "hermes_check", "hermes_send", "hermes_status", "hermes_stop",
                    "hermes_sessions", "hermes_session", "hermes_messages", "hermes_new_chat",
                    "hermes_wait", "hermes_watch_session", "hermes_steer",
                    "hermes_turns", "hermes_wait_turn", "hermes_backends",
                    "hermes_webui_check", "hermes_webui_sessions", "hermes_webui_session",
                    "hermes_webui_session_status", "hermes_webui_new_chat", "hermes_webui_send",
                    "hermes_webui_status", "hermes_webui_wait", "hermes_webui_steer", "hermes_webui_stop",
                    "hermes_native_tools", "hermes_native_read", "hermes_native_write",
                    "hermes_connection_status", "hermes_reconnect",
                }
            )
            for name in ("hermes_check", "hermes_status", "hermes_sessions", "hermes_session", "hermes_messages", "hermes_wait", "hermes_watch_session", "hermes_turns", "hermes_wait_turn"):
                self.assertTrue(tools[name].annotations.readOnlyHint)
            for name in ("hermes_send", "hermes_stop", "hermes_new_chat", "hermes_steer"):
                self.assertFalse(tools[name].annotations.readOnlyHint)
            self.assertFalse(tools["hermes_steer"].annotations.idempotentHint)

            backends = result_json(await client.call_tool("hermes_backends", {}))
            self.assertEqual(backends["gateway"]["task_id"], "run_id")
            self.assertEqual(backends["webui"]["task_id"], "stream_id")
            self.assertEqual(backends["observer"]["task_id"], "turn_id")
            self.assertNotIn(API_KEY, json.dumps(backends))
            self.assertEqual(self.requests, [])  # Routing never starts remote work.
            for name in ("hermes_webui_check", "hermes_webui_status", "hermes_native_read"):
                self.assertTrue(tools[name].annotations.readOnlyHint)
            for name in ("hermes_webui_send", "hermes_webui_stop", "hermes_native_write"):
                self.assertFalse(tools[name].annotations.readOnlyHint)
                self.assertTrue(tools[name].annotations.destructiveHint)

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

    async def test_recovery_tools_work_while_api_is_down_without_replaying_work(self):
        self.disconnect_transport = True
        async with self.session() as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            self.assertTrue(tools["hermes_connection_status"].annotations.readOnlyHint)
            self.assertFalse(tools["hermes_reconnect"].annotations.readOnlyHint)
            status = result_json(await client.call_tool("hermes_connection_status", {}))
            self.assertFalse(status["ready"])
            self.assertEqual(status["gateway"]["problem"], "transport")
            failed = result_json(await client.call_tool("hermes_reconnect", {"backend": "gateway"}))
            self.assertFalse(failed["ready"])
            self.assertEqual(self.requests, [])
            self.disconnect_transport = False
            recovered = result_json(await client.call_tool("hermes_reconnect", {"backend": "gateway"}))
            self.assertTrue(recovered["ready"])
            self.assertTrue(all(method == "GET" and path == "/v1/capabilities" for method, path, _, _ in self.requests))
            self.assertNotIn(API_KEY, json.dumps(recovered))

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
            self.assertEqual(len((await client.list_tools()).tools), 29)
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

    async def test_transport_disconnect_is_a_tool_error(self):
        self.disconnect_transport = True
        async with self.session() as client:
            result = await client.call_tool("hermes_check", {})
            self.assertTrue(result.isError)
            self.assertIn("Cannot reach the Gateway API", result_text(result))
            self.assertNotIn(API_KEY, result_text(result))
        self.assertEqual(self.requests, [])

    async def test_run_ids_cannot_change_api_route(self):
        async with self.session() as client:
            for tool in ("hermes_status", "hermes_stop", "hermes_wait", "hermes_steer"):
                with self.subTest(tool=tool):
                    args = {"run_id": "../../v1/capabilities"}
                    if tool == "hermes_steer":
                        args["instructions"] = "Adjust the task"
                    result = await client.call_tool(tool, args)
                    self.assertTrue(result.isError)
        self.assertEqual(self.requests, [])

    async def test_browse_sessions_preserves_pagination_and_resolved_session(self):
        async with self.session() as client:
            sessions = result_json(await client.call_tool("hermes_sessions", {
                "source": "cli", "limit": 7, "offset": 21, "include_children": True,
            }))
            self.assertEqual(sessions, {"object": "list", "data": [], "limit": 7, "offset": 21, "has_more": False})
            metadata = result_json(await client.call_tool("hermes_session", {"session_id": "session-existing"}))
            self.assertEqual(metadata["session"]["source"], "cli")
            messages = result_json(await client.call_tool("hermes_messages", {
                "session_id": "session-existing", "limit": 12, "offset": 24, "order": "oldest",
            }))
            self.assertEqual(messages["session_id"], "session-resolved")
            self.assertEqual(messages["pagination"]["offset"], 24)
            self.assertEqual(messages["data"][0]["content"], "Earlier answer")
            self.message_responses = [{"object": "list", "session_id": "session-existing", "data": [], "pagination": {"has_more": False}}]
            empty = result_json(await client.call_tool("hermes_messages", {"session_id": "session-existing"}))
            self.assertEqual(empty["data"], [])
        self.assertEqual(parse_qs(urlsplit(self.requests[0][1]).query), {
            "source": ["cli"], "limit": ["7"], "offset": ["21"], "include_children": ["true"],
        })
        self.assertEqual(parse_qs(urlsplit(self.requests[2][1]).query), {"limit": ["12"], "offset": ["24"], "order": ["oldest"]})

    async def test_new_chat_creation_then_message_routes_to_that_chat(self):
        async with self.session() as client:
            created = result_json(await client.call_tool("hermes_new_chat", {"title": "Deployment review"}))
            self.assertEqual(len(self.requests), 1, "Creating an empty chat must not start a run")
            self.assertEqual(created["session"]["id"], "session-new")
            sent = result_json(await client.call_tool("hermes_send", {
                "instructions": "Check release readiness", "session_id": created["session"]["id"],
            }))
            self.assertEqual(sent["run_id"], "run-123")
            result_json(await client.call_tool("hermes_new_chat", {}))
        self.assertEqual(self.requests[0][3], {"title": "Deployment review"})
        self.assertEqual(self.requests[1][3], {"input": "Check release readiness", "session_id": "session-new"})
        self.assertEqual(self.requests[2][3], {})

    async def test_wait_polls_until_authoritative_completion(self):
        self.run_responses = [
            {"run_id": "run-123", "status": "queued"},
            {"run_id": "run-123", "status": "running"},
            {"run_id": "run-123", "session_id": "session-123", "status": "completed", "output": "Ready"},
        ]
        async with self.session() as client:
            result = result_json(await client.call_tool("hermes_wait", {
                "run_id": "run-123", "timeout_seconds": 3, "poll_interval_seconds": 0.2,
            }))
        self.assertTrue(result["terminal"])
        self.assertFalse(result["wait_timed_out"])
        self.assertEqual(result["output"], "Ready")
        self.assertEqual(result["session_id"], "session-123")
        self.assertEqual(len(self.requests), 3)

    async def test_wait_distinguishes_failure_cancel_pending_and_attention(self):
        async with self.session() as client:
            for status, terminal in (("failed", True), ("cancelled", True), ("stopping", False), ("running", False), ("waiting_for_approval", False), ("unknown", False)):
                with self.subTest(status=status):
                    self.run_responses = [{"run_id": "run-123", "status": status}]
                    before = len(self.requests)
                    result = result_json(await client.call_tool("hermes_wait", {"run_id": "run-123", "timeout_seconds": 0}))
                    self.assertEqual(result["status"], status)
                    self.assertEqual(result["terminal"], terminal)
                    self.assertEqual(len(self.requests), before + 1)
                    if terminal:
                        self.assertFalse(result["wait_timed_out"])
            for status in ("waiting_for_approval", "unrecognized-state"):
                self.run_responses = [{"run_id": "run-123", "status": status}]
                result = result_json(await client.call_tool("hermes_wait", {"run_id": "run-123", "timeout_seconds": 5}))
                self.assertFalse(result["terminal"])
                self.assertFalse(result["wait_timed_out"])
            self.run_responses = [{"run_id": "run-123", "status": "running"}]
            result = result_json(await client.call_tool("hermes_wait", {"run_id": "run-123", "timeout_seconds": 0.3, "poll_interval_seconds": 0.2}))
            self.assertFalse(result["terminal"])
            self.assertTrue(result["wait_timed_out"])

    async def test_watch_baselines_detects_change_and_never_guesses_completion(self):
        async with self.session() as client:
            baseline = result_json(await client.call_tool("hermes_watch_session", {"session_id": "session-existing"}))
            self.assertRegex(baseline["cursor"], r"^[a-f0-9]{64}$")
            self.assertFalse(baseline["changed"])
            self.assertFalse(baseline["wait_timed_out"])
            self.assertEqual(baseline["completion"], "unknown")
            self.assertEqual(baseline["requested_session_id"], "session-existing")
            self.assertEqual(baseline["messages"]["session_id"], "session-resolved")
            unchanged = result_json(await client.call_tool("hermes_watch_session", {
                "session_id": "session-existing", "cursor": baseline["cursor"], "timeout_seconds": 0,
            }))
            self.assertFalse(unchanged["changed"])
            self.assertTrue(unchanged["wait_timed_out"])
            # Hermes can update content in place; the same message ID must still
            # produce a new cursor when its text changes.
            changed_messages = {**baseline["messages"], "data": [{"id": 1, "role": "assistant", "content": "A revised answer"}]}
            self.message_responses = [baseline["messages"], changed_messages]
            changed = result_json(await client.call_tool("hermes_watch_session", {
                "session_id": "session-existing", "cursor": baseline["cursor"], "timeout_seconds": 3, "poll_interval_seconds": 0.2,
            }))
            self.assertTrue(changed["changed"])
            self.assertFalse(changed["wait_timed_out"])
            self.assertNotEqual(changed["cursor"], baseline["cursor"])
            self.assertEqual(changed["completion"], "unknown", "An assistant message cannot prove an external CLI turn is complete")
        for _, route, _, _ in self.requests:
            self.assertEqual(parse_qs(urlsplit(route).query), {"limit": ["50"], "offset": ["0"], "order": ["latest"]})

    async def test_session_routes_and_poll_parameters_reject_invalid_inputs(self):
        cases = []
        for session_id in ("../other", "x?limit=1", "x#fragment", "x%2Fother", "x\\other", "x\nother", ""):
            for tool in ("hermes_session", "hermes_messages", "hermes_watch_session"):
                cases.append((tool, {"session_id": session_id}))
            cases.append(("hermes_send", {"session_id": session_id, "instructions": "Continue"}))
        for tool, upper in (("hermes_sessions", 200), ("hermes_messages", 500)):
            base = {"session_id": "session-existing"} if tool == "hermes_messages" else {}
            cases.extend((tool, {**base, "limit": limit}) for limit in (0, upper + 1))
            cases.extend((tool, {**base, "offset": offset}) for offset in (-1, 1000001))
        cases.append(("hermes_messages", {"session_id": "session-existing", "order": "backwards"}))
        for tool, base in (("hermes_wait", {"run_id": "run-123"}), ("hermes_watch_session", {"session_id": "session-existing"})):
            cases.extend((tool, {**base, "timeout_seconds": timeout}) for timeout in (-1, 46))
            cases.extend((tool, {**base, "poll_interval_seconds": interval}) for interval in (0, 0.1, 6))
        cases.append(("hermes_watch_session", {"session_id": "session-existing", "cursor": "invalid"}))
        cases.append(("hermes_sessions", {"source": "  "}))
        cases.append(("hermes_new_chat", {"title": "  "}))
        async with self.session() as client:
            for tool, args in cases:
                with self.subTest(tool=tool, args=args):
                    result = await client.call_tool(tool, args)
                    self.assertTrue(result.isError, result_text(result))
        self.assertEqual(self.requests, [])

    async def test_steer_targets_active_run_and_does_not_retry_conflicts(self):
        async with self.session() as client:
            result_json(await client.call_tool("hermes_steer", {"run_id": "run-123", "instructions": "Focus on the database"}))
            self.failure_status = 409
            result = await client.call_tool("hermes_steer", {"run_id": "run-123", "instructions": "Pause that step"})
            self.assertTrue(result.isError)
            self.assertNotIn(API_KEY, result_text(result))
            invalid = await client.call_tool("hermes_steer", {"run_id": "run-123", "instructions": "  "})
            self.assertTrue(invalid.isError)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[0][0:2], ("POST", "/v1/runs/run-123/steer"))
        self.assertEqual(self.requests[0][3], {"input": "Focus on the database"})

    async def test_watch_rejects_malformed_message_pages(self):
        async with self.session() as client:
            self.message_responses = [{"object": "list", "session_id": "session-existing", "data": []}]
            baseline = result_json(await client.call_tool("hermes_watch_session", {"session_id": "session-existing"}))
            self.assertEqual(baseline["messages"]["data"], [])
            self.assertFalse(baseline["changed"])
            self.assertEqual(baseline["completion"], "unknown")
            for malformed in ({"object": "list", "data": "not a list"}, {"object": "list", "data": []}, []):
                self.message_responses = [malformed]
                result = await client.call_tool("hermes_watch_session", {"session_id": "session-existing"})
                self.assertTrue(result.isError)
                self.assertNotIn('"completion"', result_text(result))

    async def test_observer_lists_turns_and_waits_for_exact_cli_turn(self):
        base = {"session_id": "session-existing", "turn_id": "turn-123", "liveness": "unknown"}
        self.turn_responses = [
            {**base, "status": "started", "terminal": False},
            {**base, "status": "completed", "terminal": True},
        ]
        async with self.session() as client:
            turns = result_json(await client.call_tool("hermes_turns", {"session_id": "session-existing", "limit": 3}))
            self.assertEqual(turns["data"][0]["turn_id"], "turn-123")
            result = result_json(await client.call_tool("hermes_wait_turn", {
                "session_id": "session-existing", "turn_id": "turn-123", "timeout_seconds": 3, "poll_interval_seconds": 0.2,
            }))
            self.assertTrue(result["terminal"])
            self.assertEqual(result["status"], "completed")
            self.assertFalse(result["wait_timed_out"])
            for status in ("failed", "interrupted", "incomplete", "unknown", "started"):
                self.turn_responses = [{**base, "status": status, "terminal": True}]
                result = result_json(await client.call_tool("hermes_wait_turn", {
                    "session_id": "session-existing", "turn_id": "turn-123", "timeout_seconds": 0,
                }))
                self.assertEqual(result["terminal"], status in {"failed", "interrupted", "incomplete"})
        self.assertEqual(parse_qs(urlsplit(self.requests[0][1]).query), {"session_id": ["session-existing"], "limit": ["3"]})
        for _, path, headers, _ in self.requests:
            self.assertEqual(headers.get("Authorization"), "Bearer " + API_KEY)
            self.assertNotIn("/v1/runs/", path)

    async def test_observer_missing_mismatched_and_unsafe_turns_never_complete(self):
        async with self.session() as client:
            result = result_json(await client.call_tool("hermes_wait_turn", {
                "session_id": "session-existing", "turn_id": "turn-123", "timeout_seconds": 0,
            }))
            self.assertFalse(result["terminal"])
            self.assertTrue(result["wait_timed_out"])
            for session_id, turn_id in (("another-session", "turn-123"), ("session-existing", "another-turn")):
                self.turn_responses = [{"session_id": session_id, "turn_id": turn_id, "status": "completed", "terminal": True}]
                result = await client.call_tool("hermes_wait_turn", {"session_id": "session-existing", "turn_id": "turn-123"})
                self.assertTrue(result.isError)
                self.assertIn("different session or turn", result_text(result))
            before = len(self.requests)
            for tool, args in (("hermes_turns", {"session_id": "../other"}),
                               ("hermes_turns", {"session_id": "session-existing", "limit": 101}),
                               ("hermes_wait_turn", {"session_id": "session-existing", "turn_id": "../other"})):
                self.assertTrue((await client.call_tool(tool, args)).isError)
            self.assertEqual(len(self.requests), before)
            self.failure_status = 404
            missing = await client.call_tool("hermes_turns", {"session_id": "session-existing"})
            self.assertTrue(missing.isError)
            self.assertIn("setup --observe-sessions", result_text(missing))
            self.assertNotIn(API_KEY, result_text(missing))

    async def test_cli_doctor_uses_shared_config_and_reports_auth_failure(self):
        key_file = Path(self.tempdir.name) / "api-key"
        key_file.write_text(API_KEY)
        config_file = Path(self.tempdir.name) / "config.json"
        config_file.write_text(json.dumps({"local_port": self.server.server_port, "api_key_file": str(key_file)}))
        env = {key: value for key, value in os.environ.items() if not key.startswith("HERMES_")}
        env["HERMES_BRIDGE_TOOL_CONFIG"] = str(config_file)
        command = [sys.executable, "-m", "hermes_bridge_tool", "doctor"]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        readiness = json.loads(result.stdout)
        self.assertTrue(readiness["ready"])
        self.assertTrue(readiness["session_tools_ready"])
        self.assertTrue(readiness["steering_ready"])
        self.features = {"run_submission": True, "run_status": True, "run_stop": True}
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        readiness = json.loads(result.stdout)
        self.assertTrue(readiness["ready"])
        self.assertFalse(readiness["session_tools_ready"])
        self.assertFalse(readiness["steering_ready"])
        self.failure_status = 401
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertIn("401", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(API_KEY, result.stdout + result.stderr)


class PollValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_nonfinite_wait_parameters_fail_before_any_network_call(self):
        # JSON cannot portably represent NaN/infinity; exercise the shared polling
        # boundary directly as well as the finite limits tested over MCP above.
        from hermes_bridge_tool.server import poll_until
        from mcp.server.fastmcp.exceptions import ToolError

        async def unexpected_fetch():
            self.fail("Invalid wait arguments reached the network boundary")

        for value in (float("nan"), float("inf"), float("-inf")):
            for timeout, interval in ((value, 1), (1, value)):
                with self.subTest(timeout=timeout, interval=interval):
                    with self.assertRaises(ToolError):
                        await poll_until(unexpected_fetch, lambda _: False, timeout, interval)

    async def test_slow_first_read_obeys_deadline_without_claiming_cancellation(self):
        from hermes_bridge_tool.server import poll_until
        from mcp.server.fastmcp.exceptions import ToolError

        local_read_cancelled = asyncio.Event()

        async def slow_fetch():
            try:
                await asyncio.sleep(60)
            finally:
                local_read_cancelled.set()

        with self.assertRaisesRegex(ToolError, "Remote work may still be running"):
            await asyncio.wait_for(poll_until(slow_fetch, lambda _: False, 0.05, 0.2), timeout=1)
        self.assertTrue(local_read_cancelled.is_set())

    async def test_slow_followup_read_returns_latest_known_state_at_deadline(self):
        from hermes_bridge_tool.server import poll_until

        reads = 0

        async def fetch():
            nonlocal reads
            reads += 1
            if reads == 1:
                return {"status": "running"}
            await asyncio.sleep(60)

        result, timed_out = await asyncio.wait_for(poll_until(fetch, lambda _: False, 0.3, 0.2), timeout=1)
        self.assertEqual(result, {"status": "running"})
        self.assertTrue(timed_out)
        self.assertEqual(reads, 2)


if __name__ == "__main__":
    unittest.main()
