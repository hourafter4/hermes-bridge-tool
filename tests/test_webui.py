"""WebUI wire contracts from installed nesquena/hermes-webui api/routes.py.

Exercise HTTP requests, response handling, completion evidence, and MCP metadata
without starting model work or needing an external WebUI.
"""

import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from hermes_bridge_tool import webui


class WebUIContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requests = []
        self.error = None
        self.statuses = []
        self.secret = "private-webui-cookie-do-not-reflect"
        original_client = httpx.AsyncClient

        def handle(request):
            self.requests.append(request)
            path = request.url.path.removeprefix("/hermes")
            if self.error:
                return httpx.Response(self.error, json={"error": self.secret}, headers={"Location": "https://other.invalid/"})
            if path == "/api/sessions":
                body = {"sessions": [{"session_id": "chat-one"}, {"session_id": "chat-two"}], "active_profile": "default"}
            elif path == "/api/session":
                body = {"session": {"session_id": "chat-one", "messages": [{"role": "assistant", "content": "Finished"}], "_messages_offset": 7}}
            elif path == "/api/session/new":
                body = {"session": {"session_id": "chat-new", "messages": []}}
            elif path == "/api/session/status":
                body = {"session_id": "chat-one", "agent_running": True, "active_stream_id": "stream-one"}
            elif path == "/api/chat/start":
                body = {"session_id": "chat-one", "stream_id": "stream-one", "turn_id": "journal-turn", "pending_started_at": 123}
            elif path == "/api/chat/steer":
                body = {"accepted": False, "fallback": "not_running", "stream_id": None}
            elif path == "/api/chat/cancel":
                body = {"ok": True, "cancelled": True, "stream_id": "stream-one"}
            elif path == "/api/chat/stream/status":
                body = self.statuses[0] if self.statuses else {"active": False, "stream_id": "stream-one", "replay_available": False}
                if len(self.statuses) > 1:
                    self.statuses.pop(0)
            else:
                return httpx.Response(404, json={"error": "unexpected route"})
            return httpx.Response(200, json=body)

        def client(**kwargs):
            self.assertFalse(kwargs["trust_env"])
            self.assertFalse(kwargs["follow_redirects"])
            return original_client(transport=httpx.MockTransport(handle), **kwargs)

        self.addCleanup(patch.stopall)
        patch("hermes_bridge_tool.policy.status", return_value={"mode": "control", "locked": False, "tasks": True, "messages": False}).start()
        patch.object(webui, "webui_connection_settings", return_value=("https://example.invalid/hermes", {"Cookie": "hermes_session=" + self.secret})).start()
        patch.object(webui.httpx, "AsyncClient", side_effect=client).start()

    async def test_readiness_does_not_return_conversations(self):
        result = await webui.hermes_webui_check()
        self.assertTrue(result["ready"])
        self.assertEqual(result["task_id_kind"], "stream_id")
        self.assertNotIn("sessions", result)
        self.assertNotIn(self.secret, json.dumps(result))
        self.assertEqual(self.requests[0].headers["Cookie"], "hermes_session=" + self.secret)

    async def test_session_listing_and_history_use_webui_schema(self):
        listing = await webui.hermes_webui_sessions(limit=1, offset=1, source="cli", include_archived=True)
        self.assertEqual(listing["sessions"], [{"session_id": "chat-two"}])
        self.assertFalse(listing["has_more"])
        self.assertEqual(dict(self.requests[-1].url.params), {"sidebar_source": "cli", "include_archived": "1"})
        history = await webui.hermes_webui_session("chat-one", message_limit=10, before=17)
        self.assertEqual(history["session"]["_messages_offset"], 7)
        self.assertEqual(dict(self.requests[-1].url.params), {"session_id": "chat-one", "messages": "1", "msg_limit": "10", "msg_before": "17", "resolve_model": "0"})

    async def test_new_send_discover_steer_cancel_wire_contract(self):
        created = await webui.hermes_webui_new_chat(workspace="/srv/project")
        self.assertEqual(created["session"]["session_id"], "chat-new")
        self.assertEqual(json.loads(self.requests[-1].content), {"workspace": "/srv/project", "worktree": False})
        sent = await webui.hermes_webui_send("chat-one", "Inspect the project")
        self.assertTrue(sent["submission_confirmed"])
        self.assertEqual(sent["stream_id"], "stream-one")
        self.assertEqual(json.loads(self.requests[-1].content), {"session_id": "chat-one", "message": "Inspect the project"})
        status = await webui.hermes_webui_session_status("chat-one")
        self.assertEqual(status["active_stream_id"], "stream-one")
        self.assertEqual(status["completion"], "unknown")
        steer = await webui.hermes_webui_steer("chat-one", "Focus on tests")
        self.assertFalse(steer["accepted"])
        self.assertEqual(json.loads(self.requests[-1].content), {"session_id": "chat-one", "text": "Focus on tests"})
        cancelled = await webui.hermes_webui_stop("stream-one")
        self.assertTrue(cancelled["cancelled"])
        self.assertEqual(self.requests[-1].method, "GET")
        self.assertEqual(self.requests[-1].url.path, "/hermes/api/chat/cancel")
        self.assertEqual(self.requests[-1].url.params["stream_id"], "stream-one")

    async def test_inactive_or_missing_journal_is_not_completion(self):
        result = await webui.hermes_webui_wait("stream-one", timeout_seconds=0)
        self.assertFalse(result["terminal"])
        self.assertEqual(result["completion"], "unknown")
        self.assertTrue(result["wait_timed_out"])
        self.statuses = [{"active": False, "journal": {"terminal": False, "terminal_state": "lost-worker-bookkeeping"}}]
        result = await webui.hermes_webui_status("stream-one")
        self.assertFalse(result["terminal"])
        self.assertEqual(result["completion"], "unknown")

    async def test_polling_uses_journal_outcome(self):
        self.statuses = [
            {"active": True, "journal": {"terminal": False, "last_event": "tool_start"}},
            {"active": False, "journal": {"terminal": True, "terminal_state": "completed", "last_event": "done"}},
        ]
        result = await webui.hermes_webui_wait("stream-one", timeout_seconds=2, poll_interval_seconds=0.2)
        self.assertTrue(result["terminal"])
        self.assertEqual(result["completion"], "completed")
        self.assertFalse(result["wait_timed_out"])
        self.assertEqual(len(self.requests), 2)

    async def test_transport_closure_cannot_prove_semantic_success(self):
        self.statuses = [{"active": False, "journal": {"terminal": True, "terminal_state": "completed", "last_event": "stream_end"}}]
        result = await webui.hermes_webui_status("stream-one")
        self.assertTrue(result["terminal"])
        self.assertEqual(result["completion"], "unknown")
        self.assertEqual(result["status"], "completed")

    async def test_journal_failure_and_interruption_are_preserved(self):
        for state, expected in (("errored", "failed"), ("tool_limit_reached", "failed"), ("interrupted-by-user", "interrupted"), ("interrupted-by-crash", "interrupted")):
            with self.subTest(state=state):
                self.statuses = [{"active": False, "journal": {"terminal": True, "terminal_state": state, "last_event": "stream_end"}}]
                result = await webui.hermes_webui_status("stream-one")
                self.assertTrue(result["terminal"])
                self.assertEqual(result["completion"], expected)

    async def test_http_failures_redacted_redirects_not_followed(self):
        for status in (302, 401, 403, 404, 409, 500):
            with self.subTest(status=status):
                self.error = status
                before = len(self.requests)
                with self.assertRaises(ToolError) as caught:
                    await webui.hermes_webui_send("chat-one", "Do work")
                self.assertNotIn(self.secret, str(caught.exception))
                self.assertIn(str(status), str(caught.exception))
                self.assertEqual(len(self.requests), before + 1)

    async def test_uncertain_submission_is_not_retried(self):
        request = httpx.Request("POST", "https://example.invalid/api/chat/start")
        with patch.object(webui.httpx, "AsyncClient", side_effect=httpx.ReadTimeout(self.secret, request=request)):
            with self.assertRaises(ToolError) as caught:
                await webui.hermes_webui_send("chat-one", "Do work")
        self.assertIn("Do not blindly retry", str(caught.exception))
        self.assertNotIn(self.secret, str(caught.exception))
        self.assertEqual(self.requests, [])

    async def test_wait_network_deadline_is_bounded(self):
        async def slow(*args):
            await asyncio.sleep(5)
        with patch.object(webui, "hermes_webui_status", side_effect=slow):
            with self.assertRaisesRegex(ToolError, "deadline"):
                await webui.hermes_webui_wait("stream-one", timeout_seconds=0.02)

    async def test_validation_prevents_ambiguous_requests(self):
        for identifier in ("../x", "foo?bar", "", "x\ny"):
            with self.assertRaises(ToolError):
                await webui.hermes_webui_status(identifier)
        with self.assertRaises(ToolError):
            await webui.hermes_webui_send("chat-one", " ")
        with self.assertRaises(ToolError):
            await webui.hermes_webui_sessions(limit=0)
        with self.assertRaises(ToolError):
            await webui.hermes_webui_session("chat-one", before=-1)
        self.assertEqual(self.requests, [])

    async def test_tool_discovery_marks_cancel_as_mutating(self):
        server = FastMCP("webui-test")
        webui.register_webui_tools(server)
        tools = {tool.name: tool for tool in await server.list_tools()}
        self.assertEqual(len(tools), 10)
        for name in ("hermes_webui_stop", "hermes_webui_send", "hermes_webui_steer", "hermes_webui_new_chat"):
            self.assertFalse(tools[name].annotations.readOnlyHint)
            self.assertFalse(tools[name].annotations.idempotentHint)
        self.assertTrue(tools["hermes_webui_status"].annotations.readOnlyHint)
        self.assertIn("NOT gateway run_id", tools["hermes_webui_send"].description)


if __name__ == "__main__":
    unittest.main()
