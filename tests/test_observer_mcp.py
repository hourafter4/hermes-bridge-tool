"""Real plugin hooks -> SQLite -> HTTP -> MCP stdio completion integration."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from hermes_bridge import observer_plugin


class Context:
    def __init__(self):
        self.hooks = {}
        self.handlers = {}

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_platform_handler(self, name, factory):
        self.handlers[name] = factory


def result_json(result):
    content = "\n".join(block.text for block in result.content if block.type == "text")
    if result.isError:
        raise AssertionError(content)
    return json.loads(content)


class ObserverMCPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        scope = patch.object(observer_plugin, "_home", return_value=self.home)
        scope.start()
        self.addCleanup(scope.stop)
        self.context = Context()
        observer_plugin.register(self.context)
        self.first_poll = asyncio.Event()

        @web.middleware
        async def notice_poll(request, handler):
            response = await handler(request)
            if request.match_info.get("turn_id") is not None:
                self.first_poll.set()
            return response

        app = web.Application(middlewares=[notice_poll])
        self.key = "isolated-observer-gateway-key"
        adapter = types.SimpleNamespace(_expected_api_key=lambda: self.key)
        self.context.handlers["api_server"](app, adapter)
        self.server = TestServer(app)
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)

    @asynccontextmanager
    async def client(self):
        environment = dict(os.environ)
        environment.update(
            HERMES_API_URL=str(self.server.make_url("/")).rstrip("/"),
            HERMES_API_KEY=self.key,
            HERMES_BRIDGE_CONFIG=str(self.home / "bridge-config.json"),
            HERMES_API_KEY_FILE=str(self.home / "unused-key"),
        )
        params = StdioServerParameters(
            command=sys.executable, args=["-u", "-m", "hermes_bridge", "mcp"], env=environment)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as client:
                await client.initialize()
                yield client

    async def observe_outcome(self, status, *, compaction=False):
        session_id = "cli-original"
        turn_id = "cli-original:task-123:abc456"
        start = self.context.hooks["pre_llm_call"]
        end = self.context.hooks["on_session_end"]
        # Exact fields supplied by Hermes agent/turn_context.py.
        start(session_id=session_id, task_id="task-123", turn_id=turn_id,
              user_message="do not expose private prompt", conversation_history=[],
              is_first_turn=True, model="model", platform="cli", parent_session_id="", sender_id="")
        async with self.client() as client:
            listed = result_json(await client.call_tool("hermes_turns", {"session_id": session_id}))
            self.assertEqual(len(listed["data"]), 1)
            self.assertEqual(listed["data"][0]["turn_id"], turn_id)
            self.assertEqual(listed["data"][0]["status"], "started")
            self.assertNotIn("private prompt", json.dumps(listed))

            async def finish_after_first_poll():
                await asyncio.wait_for(self.first_poll.wait(), 5)
                # A concurrent newer turn must not change which turn MCP monitors.
                await asyncio.to_thread(start, session_id=session_id, turn_id="newer-turn", platform="cli")
                await asyncio.to_thread(
                    end, session_id="cli-compacted" if compaction else session_id,
                    task_id="task-123", turn_id=turn_id,
                    completed=status == "completed", failed=status == "failed",
                    interrupted=status == "interrupted", turn_exit_reason="text_response(1)",
                    model="model", platform="cli")

            completion = asyncio.create_task(finish_after_first_poll())
            try:
                waited = result_json(await client.call_tool("hermes_wait_turn", {
                    "session_id": session_id, "turn_id": turn_id,
                    "timeout_seconds": 5, "poll_interval_seconds": 0.2}))
                await completion
            finally:
                if not completion.done():
                    completion.cancel()
                    await asyncio.gather(completion, return_exceptions=True)
            self.assertEqual(waited["turn_id"], turn_id)
            self.assertEqual(waited["initial_session_id"], session_id)
            self.assertEqual(waited["session_id"], "cli-compacted" if compaction else session_id)
            self.assertEqual(waited["status"], status)
            self.assertTrue(waited["terminal"])
            self.assertFalse(waited["wait_timed_out"])
            self.assertEqual(waited["liveness"], "unknown")
            self.assertEqual(observer_plugin.TurnStore(self.home).get(session_id, "newer-turn")["status"], "started")

    async def test_completed_cli_turn_from_hook_to_mcp(self):
        await self.observe_outcome("completed")

    async def test_compaction_preserves_turn_correlation_across_mcp(self):
        await self.observe_outcome("completed", compaction=True)

    async def test_failed_turn_is_terminal_without_claiming_success(self):
        await self.observe_outcome("failed")

    async def test_missing_turn_remains_unknown_over_mcp(self):
        async with self.client() as client:
            waited = result_json(await client.call_tool("hermes_wait_turn", {
                "session_id": "cli", "turn_id": "not-observed", "timeout_seconds": 0}))
            self.assertEqual(waited["status"], "unknown")
            self.assertFalse(waited["terminal"])
            self.assertTrue(waited["wait_timed_out"])


if __name__ == "__main__":
    unittest.main()
