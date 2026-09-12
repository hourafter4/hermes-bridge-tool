"""Exercise the persistent native adapter against a real stdio MCP child."""

import asyncio
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp.server.fastmcp.exceptions import ToolError

from hermes_bridge_tool.native import NativeProxy, native_command, register_native_tools
from hermes_bridge_tool.cli import main
from mcp.server.fastmcp import FastMCP


class NativeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        fixture = Path(self.directory.name) / "server.py"
        fixture.write_text('''import asyncio
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("native-fixture")
count = 0
@mcp.tool()
def events_poll(after_cursor: int = 0) -> dict:
    global count
    count += 1
    return {"next_cursor": after_cursor + count}
@mcp.tool()
async def events_wait() -> dict:
    await asyncio.sleep(2)
    return {"event": None}
mcp.run()
''')
        self.env = patch.dict(os.environ, {
            "HERMES_NATIVE_MCP_COMMAND": json.dumps([sys.executable, str(fixture)]),
            "HERMES_BRIDGE_TOOL_CONFIG": str(Path(self.directory.name) / "config.json"),
        })
        self.env.start()
        self.proxy = NativeProxy()

    async def asyncTearDown(self):
        await self.proxy.close()
        self.env.stop()
        self.directory.cleanup()

    async def test_discovery_and_repeated_calls_keep_native_session(self):
        listed = await self.proxy.request()
        self.assertEqual(listed["tools"][0]["name"], "events_poll")
        first = await self.proxy.request("events_poll", {"after_cursor": 5})
        second = await self.proxy.request("events_poll", {"after_cursor": 5})
        self.assertEqual(first["connection_id"], second["connection_id"])
        self.assertEqual(first["connection_id"], listed["connection_id"])
        self.assertEqual(json.loads(first["content"][0]["text"])["next_cursor"], 6)
        self.assertEqual(json.loads(second["content"][0]["text"])["next_cursor"], 7)
        restarted = await self.proxy.reconnect()
        self.assertNotEqual(restarted["connection_id"], first["connection_id"])

    async def test_invalid_command_and_child_failure_do_not_expose_argv(self):
        with patch.dict(os.environ, {"HERMES_NATIVE_MCP_COMMAND": "not json private-token"}):
            with self.assertRaises(ToolError) as error:
                native_command()
            self.assertNotIn("private-token", str(error.exception))
        with patch.dict(os.environ, {"HERMES_NATIVE_MCP_COMMAND": '["/missing/private-token"]'}):
            with self.assertRaises(ToolError) as error:
                await self.proxy.request()
            self.assertNotIn("private-token", str(error.exception))

    async def test_optional_backend_not_started_when_unconfigured(self):
        with patch.dict(os.environ):
            os.environ.pop("HERMES_NATIVE_MCP_COMMAND", None)
            self.assertIsNone(native_command())
            with self.assertRaisesRegex(ToolError, "not configured"):
                await self.proxy.request()
            self.assertIsNone(self.proxy.task)

    async def test_native_tool_errors_preserve_error_flag(self):
        result = await self.proxy.request("missing_tool")
        self.assertTrue(result["isError"])

    async def test_busy_request_not_queued_and_cancellation_closes_connection(self):
        await self.proxy.request()
        waiting = asyncio.create_task(self.proxy.request("events_wait"))
        await asyncio.sleep(0.05)
        with self.assertRaisesRegex(ToolError, "not submitted"):
            await self.proxy.request("messages_send", {"target": "must-not-send"})
        connection_id = self.proxy.connection_id
        with self.assertRaisesRegex(ToolError, "no connection was reset"):
            await self.proxy.reconnect()
        self.assertEqual(self.proxy.connection_id, connection_id)
        self.assertFalse(waiting.done())
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        self.assertIsNone(self.proxy.task)
        self.assertFalse(self.proxy.busy)
        self.assertTrue(self.proxy.queue.empty())

    async def test_configure_native_stores_argv_without_running_and_preserves_settings(self):
        path = Path(os.environ["HERMES_BRIDGE_TOOL_CONFIG"])
        path.write_text('{"webui_url":"https://webui.example", "custom":"preserved"}')
        with redirect_stdout(StringIO()):
            self.assertEqual(main(["configure-native", "--", "/not-executed", "literal argument"]), 0)
        saved = json.loads(path.read_text())
        self.assertEqual(saved["native_mcp_command"], ["/not-executed", "literal argument"])
        self.assertEqual(saved["custom"], "preserved")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    async def test_wrappers_reject_writes_through_read_interface(self):
        mcp = FastMCP("test")
        register_native_tools(mcp)
        tools = {tool.name: tool for tool in await mcp.list_tools()}
        self.assertTrue(tools["hermes_native_read"].annotations.readOnlyHint)
        self.assertTrue(tools["hermes_native_write"].annotations.destructiveHint)
        with self.assertRaisesRegex(ToolError, "Not an allowed"):
            await mcp.call_tool("hermes_native_read", {"tool_name": "messages_send"})
        with self.assertRaisesRegex(ToolError, "0–30000"):
            await mcp.call_tool("hermes_native_read", {"tool_name": "events_wait", "arguments": {"timeout_ms": 31000}})


if __name__ == "__main__":
    unittest.main()
