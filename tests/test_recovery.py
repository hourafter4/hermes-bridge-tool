"""MCP recovery routing stays available without remote agent connectivity."""

from contextlib import ExitStack
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from hermes_bridge_tool import recovery
from hermes_bridge_tool.native import NativeProxy


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mcp = FastMCP("recovery-tests")
        recovery.register_recovery_tools(self.mcp)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.report = {
            "ready": True,
            "managed_tunnel": True,
            "gateway": {"configured": True, "ready": True},
            "webui": {"configured": False, "skipped": True},
        }
        self.status = self.stack.enter_context(patch.object(recovery.connection, "connection_status", AsyncMock(return_value=self.report)))
        self.connect = self.stack.enter_context(patch.object(recovery.connection, "connect", AsyncMock(return_value=self.report)))
        self.command = self.stack.enter_context(patch.object(recovery, "native_command", return_value=None))
        self.proxy = SimpleNamespace(task=None, busy=False, connection_id=None,
                                     reconnect=AsyncMock(return_value={"connection_id": "new-native-id", "tools": []}),
                                     request=AsyncMock())
        self.stack.enter_context(patch.object(recovery, "native_proxy", self.proxy))

    async def call(self, name="hermes_reconnect", **arguments):
        result = await self.mcp.call_tool(name, arguments)
        if isinstance(result, tuple):
            return result[1]
        return json.loads(next(block.text for block in result if block.type == "text"))

    async def test_recovery_discovery_explains_effects_and_marks_mutation(self):
        tools = {tool.name: tool for tool in await self.mcp.list_tools()}
        self.assertTrue(tools["hermes_connection_status"].annotations.readOnlyHint)
        self.assertFalse(tools["hermes_reconnect"].annotations.readOnlyHint)
        self.assertFalse(tools["hermes_reconnect"].annotations.destructiveHint)
        self.assertIn("never", tools["hermes_connection_status"].description)

    async def test_status_probes_without_starting_transport_or_native(self):
        self.command.return_value = ["hermes", "mcp", "serve"]
        self.proxy.task = Mock()
        self.proxy.task.done.return_value = False
        self.proxy.connection_id = "current-id"
        result = await self.call("hermes_connection_status")
        self.assertTrue(result["native"]["ready"])
        self.assertEqual(result["native"]["connection_id"], "current-id")
        self.status.assert_awaited_once()
        self.connect.assert_not_awaited()
        self.proxy.reconnect.assert_not_awaited()
        self.proxy.request.assert_not_awaited()

    async def test_all_skips_unconfigured_backends(self):
        result = await self.call()
        self.assertTrue(result["ready"])
        self.assertTrue(result["webui"]["skipped"])
        self.assertTrue(result["native"]["skipped"])
        self.connect.assert_not_awaited()
        self.proxy.reconnect.assert_not_awaited()

    async def test_all_without_configuration_is_not_ready(self):
        self.report.update(ready=False, gateway={"configured": False, "skipped": True})
        result = await self.call()
        self.assertFalse(result["ready"])
        self.connect.assert_not_awaited()
        self.proxy.reconnect.assert_not_awaited()

    async def test_selected_backend_does_not_repair_unrelated_transport_failure(self):
        self.report.update(ready=False, gateway={"configured": True, "ready": False, "problem": "transport", "uses_ssh": True},
                           webui={"configured": True, "ready": True})
        result = await self.call(backend="webui")
        self.assertTrue(result["ready"])
        self.connect.assert_not_awaited()
        self.proxy.reconnect.assert_not_awaited()

    async def test_transport_failure_calls_shared_repair_once(self):
        self.status.return_value = {**self.report, "ready": False,
                                    "gateway": {"configured": True, "ready": False, "problem": "transport", "uses_ssh": True}}
        result = await self.call(backend="gateway")
        self.assertTrue(result["ready"])
        self.connect.assert_awaited_once_with(restart=True)
        self.proxy.reconnect.assert_not_awaited()
        self.assertIn("never resubmits", result["next"])

    async def test_authentication_failure_does_not_reset_tunnel(self):
        self.report.update(ready=False, gateway={"configured": True, "ready": False, "problem": "authentication"})
        result = await self.call(backend="gateway")
        self.assertFalse(result["ready"])
        self.assertEqual(result["gateway"]["problem"], "authentication")
        self.connect.assert_not_awaited()

    async def test_direct_failure_does_not_reset_healthy_other_ssh_backend(self):
        self.report.update(ready=False,
                           gateway={"configured": True, "ready": False, "problem": "transport", "uses_ssh": False},
                           webui={"configured": True, "ready": True, "uses_ssh": True})
        for backend in ("gateway", "all"):
            result = await self.call(backend=backend)
            self.assertFalse(result["ready"])
        self.connect.assert_not_awaited()

    async def test_native_selection_never_touches_http_transport(self):
        self.command.return_value = ["hermes", "mcp", "serve"]
        result = await self.call(backend="native")
        self.assertTrue(result["ready"])
        self.assertEqual(result["native"]["connection_id"], "new-native-id")
        self.assertIn("reset", result["native"]["hint"])
        self.status.assert_not_awaited()
        self.connect.assert_not_awaited()
        self.proxy.reconnect.assert_awaited_once()

    async def test_busy_native_call_is_not_interrupted(self):
        self.command.return_value = ["hermes", "mcp", "serve"]
        proxy = NativeProxy()
        proxy.busy = True
        with patch.object(recovery, "native_proxy", proxy), patch.object(proxy, "close", AsyncMock()) as close:
            result = await self.call(backend="native")
        self.assertFalse(result["ready"])
        self.assertEqual(result["native"]["problem"], "busy")
        close.assert_not_awaited()
        self.assertTrue(proxy.busy)

    async def test_invalid_native_configuration_is_redacted_and_not_started(self):
        self.command.side_effect = ToolError("private-command-secret")
        result = await self.call(backend="native")
        self.assertFalse(result["ready"])
        self.assertEqual(result["native"]["problem"], "configuration")
        self.assertNotIn("private-command-secret", json.dumps(result))
        self.proxy.reconnect.assert_not_awaited()

    async def test_failed_repair_is_redacted_and_not_retried(self):
        self.report.update(ready=False, gateway={"configured": True, "ready": False, "problem": "transport", "uses_ssh": True})
        self.connect.side_effect = OSError("private-ssh-output")
        result = await self.call(backend="gateway")
        self.assertFalse(result["ready"])
        self.assertNotIn("private-ssh-output", json.dumps(result))
        self.connect.assert_awaited_once()

    async def test_invalid_backend_is_rejected_before_connectivity_work(self):
        with self.assertRaises(ToolError):
            await self.call(backend="arbitrary-host")
        self.status.assert_not_awaited()
        self.connect.assert_not_awaited()
        self.proxy.reconnect.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
