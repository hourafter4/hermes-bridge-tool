"""Access controls deny at the transport boundary before remote side effects."""

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from hermes_bridge_tool import policy, native, recovery
from hermes_bridge_tool.cli import main


class PolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"
        self.env = patch.dict(os.environ, {"HERMES_BRIDGE_TOOL_CONFIG": str(self.path)})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_missing_policy_defaults_existing_and_new_installs_to_monitor(self):
        for data in (None, {"ssh_host": "existing-server"}):
            if data is not None:
                self.path.write_text(json.dumps(data))
            self.assertEqual(policy.status(), {"mode": "monitor", "locked": False,
                                              "tasks": False, "messages": False, "approvals": False})
            policy.require("read")
            policy.require("reconnect")
            for action in ("tasks", "messages", "approvals"):
                with self.assertRaises(ToolError):
                    policy.require(action)

    def test_invalid_security_config_fails_closed(self):
        for text in ('not json', '[]', '{"security": null}', '{"security":{"locked":"false"}}',
                     '{"security":{"mode":"admin"}}', '{"security":{"messages":1}}'):
            self.path.write_text(text)
            self.assertTrue(policy.status()["locked"])
            for action in ("read", "tasks", "messages", "reconnect"):
                with self.assertRaises(ToolError):
                    policy.require(action)

    def test_separate_grants_reload_without_restarting_mcp(self):
        self.path.write_text('{"custom":"preserved"}')
        policy.update(mode="control")
        policy.require("tasks")
        with self.assertRaises(ToolError):
            policy.require("messages")
        policy.update(messages=True)
        policy.require("messages")
        with self.assertRaisesRegex(ToolError, "disabled"):
            policy.require("approvals")
        policy.update(mode="monitor")
        policy.update(mode="control")
        self.assertFalse(policy.status()["messages"])
        self.assertEqual(json.loads(self.path.read_text())["custom"], "preserved")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        policy.update(locked=True)
        with self.assertRaisesRegex(ToolError, "locked"):
            policy.require("read")
        policy.update(locked=False)
        policy.require("tasks")

    def test_cli_rejects_noninteractive_increases_without_modification(self):
        for args in (["mode", "control"], ["messages", "on"], ["unlock"]):
            with patch("sys.stdin.isatty", return_value=False), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(["security", *args]), 1)
            self.assertFalse(self.path.exists())

    def test_cli_requires_exact_confirmation_and_allows_lowering_access(self):
        for response, expected in (("y", 1), ("ENABLE", 0)):
            output = StringIO()
            with redirect_stdout(output), redirect_stderr(StringIO()), patch("sys.stdin.isatty", return_value=True), \
                    patch.object(output, "isatty", return_value=True), patch("builtins.input", return_value=response):
                self.assertEqual(main(["security", "mode", "control"]), expected)
        self.assertTrue(policy.status()["tasks"])
        with redirect_stdout(StringIO()), patch("sys.stdin.isatty", return_value=False):
            self.assertEqual(main(["security", "mode", "monitor"]), 0)
        self.assertFalse(policy.status()["tasks"])

    async def test_denied_native_mutations_never_reach_proxy(self):
        mcp = FastMCP("native-policy")
        native.register_native_tools(mcp)
        with patch.object(native.native_proxy, "request", new_callable=AsyncMock) as request:
            for tool in ("messages_send", "permissions_respond"):
                with self.assertRaises(ToolError):
                    await mcp.call_tool("hermes_native_write", {"tool_name": tool, "arguments": {}})
            policy.update(mode="control", messages=True)
            with self.assertRaisesRegex(ToolError, "disabled"):
                await mcp.call_tool("hermes_native_write", {"tool_name": "permissions_respond", "arguments": {"decision": "allow_always"}})
            request.assert_not_awaited()
            await mcp.call_tool("hermes_native_write", {"tool_name": "messages_send", "arguments": {"text": "authorized"}})
            request.assert_awaited_once_with("messages_send", {"text": "authorized"})

    async def test_native_proxy_itself_enforces_policy_before_child_start(self):
        proxy = native.NativeProxy()
        with patch.object(native, "native_command") as command:
            for tool in ("messages_send", "permissions_respond"):
                with self.assertRaises(ToolError):
                    await proxy.request(tool, {})
            command.assert_not_called()
            self.assertIsNone(proxy.task)

    async def test_lock_denies_recovery_and_native_reads_without_upstream_calls(self):
        policy.update(locked=True)
        mcp = FastMCP("locked-policy")
        native.register_native_tools(mcp)
        recovery.register_recovery_tools(mcp)
        with patch.object(native.native_proxy, "request", new_callable=AsyncMock) as request, \
                patch.object(recovery.connection, "connect", new_callable=AsyncMock) as connect, \
                patch.object(recovery.connection, "connection_status", new_callable=AsyncMock) as health:
            for name, args in (("hermes_native_tools", {}), ("hermes_native_read", {"tool_name": "conversations_list"}),
                               ("hermes_reconnect", {"backend": "all"})):
                with self.assertRaisesRegex(ToolError, "locked"):
                    await mcp.call_tool(name, args)
            await mcp.call_tool("hermes_connection_status", {})
            request.assert_not_awaited()
            connect.assert_not_awaited()
            health.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
