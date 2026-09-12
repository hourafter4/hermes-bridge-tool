"""Shared transport ownership and concurrency, without contacting a real server."""

import asyncio
from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from hermes_bridge_tool import connection
from hermes_bridge_tool.config import Settings, save_settings


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bridge-connection-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.key = self.root / "api-key"
        self.key.write_text("private-test-key")
        self.settings = Settings(ssh_host="test-server", api_key_file=str(self.key))
        self.control = self.root / "control.sock"
        self.metadata = self.root / "control.json"
        self.lock = self.root / "control.lock"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"HERMES_BRIDGE_TOOL_CONFIG": str(self.config)}, clear=True))
        save_settings(self.settings)
        self.stack.enter_context(patch.object(connection, "paths", return_value=(self.control, self.metadata, self.lock)))
        self.master = None
        self.starts = []
        self.stops = 0
        self.run = self.stack.enter_context(patch.object(connection, "run_ssh", side_effect=self.fake_ssh))
        self.port = self.stack.enter_context(patch.object(connection, "port_in_use", AsyncMock(return_value=False)))
        self.probe = self.stack.enter_context(patch.object(connection, "_probe", AsyncMock(return_value={"configured": True, "ready": True, "problem": None})))
        self.addCleanup(self.close_master)

    def close_master(self):
        if self.master is not None:
            self.master.close()
            self.master = None
        self.control.unlink(missing_ok=True)

    async def fake_ssh(self, command, timeout=3):
        if "-O" in command:
            action = command[command.index("-O") + 1]
            if action == "check":
                return 0 if self.master else 1
            if action == "exit":
                self.stops += 1
                self.close_master()
                return 0
            raise AssertionError(action)
        self.starts.append(command)
        # Let competing callers contend for the actual file lock before startup.
        await asyncio.sleep(0.03)
        self.master = socket.socket(socket.AF_UNIX)
        self.master.bind(str(self.control))
        self.master.listen()
        return 0

    async def test_connect_reuses_one_owned_master_and_private_metadata(self):
        first = await connection.connect()
        second = await connection.connect()
        self.assertTrue(first["ready"])
        self.assertTrue(first["managed_tunnel"])
        self.assertTrue(first["settings_match"])
        self.assertEqual(first["action"], "connected")
        self.assertEqual(second["action"], "reused")
        self.assertEqual(len(self.starts), 1)
        self.assertEqual(self.stops, 0)
        self.assertEqual(stat.S_IMODE(self.metadata.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.lock.stat().st_mode), 0o600)
        command = self.starts[0]
        self.assertIn("ControlMaster=yes", command)
        self.assertIn(f"ControlPath={self.control}", command)
        self.assertIn("ForkAfterAuthentication=yes", command)
        self.assertIn("ExitOnForwardFailure=yes", command)
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("BatchMode=yes", command)
        self.assertNotIn("private-test-key", json.dumps(command))
        self.assertNotIn("private-test-key", self.metadata.read_text())

    async def test_explicit_restart_only_stops_owned_control_master(self):
        await connection.connect()
        self.probe.side_effect = [
            {"configured": True, "ready": False, "problem": "transport"},
            {"configured": True, "ready": True, "problem": None},
        ]
        result = await connection.connect(restart=True)
        self.assertEqual(result["action"], "reconnected")
        self.assertEqual(len(self.starts), 2)
        self.assertEqual(self.stops, 1)
        stops = [call.args[0] for call in self.run.call_args_list if "exit" in call.args[0]]
        self.assertEqual(stops, [["ssh", "-F", "/dev/null", "-S", str(self.control), "-O", "exit", "localhost"]])
        await connection.disconnect()
        self.assertFalse(self.metadata.exists())
        self.assertIsNone(self.master)
        self.assertEqual(self.stops, 2)

    async def test_healthy_reconnect_does_not_reset_shared_transport(self):
        await connection.connect()
        result = await connection.connect(restart=True)
        self.assertEqual(result["action"], "already_ready")
        self.assertEqual(len(self.starts), 1)
        self.assertEqual(self.stops, 0)

    async def test_auth_and_remote_api_failures_do_not_restart_tunnel(self):
        await connection.connect()
        for problem in ("authentication", "configuration", "http", "unsupported_api"):
            self.probe.return_value = {"configured": True, "ready": False, "problem": problem}
            result = await connection.connect(restart=True)
            self.assertEqual(result["action"], "no_transport_repair")
            self.assertFalse(result["ready"])
            self.assertTrue(result["managed_tunnel"])
        self.assertEqual(len(self.starts), 1)
        self.assertEqual(self.stops, 0)

    async def test_failed_direct_backend_does_not_reset_other_ssh_backend(self):
        save_settings(replace(self.settings, gateway_url="https://gateway.example.test", webui_ssh=True))
        self.probe.side_effect = lambda name: {"configured": True, "ready": name == "webui", "problem": "transport" if name == "gateway" else None}
        await connection.connect()
        result = await connection.connect(restart=True)
        self.assertEqual(result["action"], "no_transport_repair")
        self.assertFalse(result["gateway"]["uses_ssh"])
        self.assertTrue(result["webui"]["uses_ssh"])
        self.assertEqual(len(self.starts), 1)
        self.assertEqual(self.stops, 0)

    async def test_unresponsive_owned_master_is_not_replaced_blindly(self):
        await connection.connect()
        self.probe.return_value = {"configured": True, "ready": False, "problem": "transport"}
        original = self.fake_ssh
        async def cannot_stop(command, timeout=3):
            if "exit" in command:
                return 1
            return await original(command, timeout)
        self.run.side_effect = cannot_stop
        with self.assertRaisesRegex(ValueError, "Could not stop the owned"):
            await connection.connect(restart=True)
        self.assertEqual(len(self.starts), 1)
        self.assertIsNotNone(self.master)

    async def test_concurrent_connects_share_real_file_lock(self):
        results = await asyncio.gather(*(connection.connect() for _ in range(4)))
        self.assertTrue(all(result["ready"] for result in results))
        self.assertEqual(len(self.starts), 1)
        self.assertEqual(sorted(result["action"] for result in results), ["connected", "reused", "reused", "reused"])

    async def test_changed_forward_replaces_owned_master(self):
        await connection.connect()
        save_settings(replace(self.settings, remote_port=9864))
        self.assertFalse((await connection.connection_status())["settings_match"])
        result = await connection.connect()
        self.assertTrue(result["ready"])
        self.assertTrue(result["settings_match"])
        self.assertEqual(self.stops, 1)
        self.assertIn("127.0.0.1:18642:127.0.0.1:9864", self.starts[-1])

    async def test_external_listener_is_never_stopped_even_for_restart(self):
        self.port.return_value = True
        result = await connection.connect(restart=True)
        self.assertEqual(result["action"], "external_listener")
        self.assertEqual(self.starts, [])
        self.assertEqual(self.stops, 0)
        self.assertTrue(result["ready"])
        self.assertFalse(result["managed_tunnel"])
        await connection.disconnect()
        self.run.assert_not_called()

    async def test_one_occupied_port_blocks_partial_forward_startup(self):
        save_settings(replace(self.settings, webui_url="http://127.0.0.1:18787", webui_ssh=True))
        self.port.side_effect = lambda port: port == 18787
        result = await connection.connect()
        self.assertEqual(result["action"], "external_listener")
        self.assertEqual(self.starts, [])
        self.assertEqual(self.stops, 0)

    async def test_direct_https_has_no_ssh_or_port_operations(self):
        save_settings(replace(self.settings, gateway_url="https://gateway.example.test/hermes"))
        result = await connection.connect(restart=True)
        self.assertEqual(result["action"], "checked_direct")
        self.assertFalse(result["ssh_required"])
        self.run.assert_not_called()
        self.port.assert_not_called()

    async def test_environment_https_overrides_disable_webui_forward(self):
        save_settings(replace(self.settings, webui_url="http://127.0.0.1:18787", webui_ssh=True))
        with patch.dict(os.environ, {"HERMES_API_URL": "https://gateway.example.test", "HERMES_WEBUI_URL": "https://webui.example.test"}):
            result = await connection.connect()
        self.assertFalse(result["ssh_required"])
        self.run.assert_not_called()
        self.port.assert_not_called()

    async def test_unsafe_socket_is_rejected_without_ssh(self):
        self.control.write_text("unexpected")
        with self.assertRaisesRegex(ValueError, "Unexpected file"):
            await connection.connect()
        self.run.assert_not_called()
        self.assertEqual(self.control.read_text(), "unexpected")

    async def test_symlink_socket_is_rejected_without_ssh(self):
        other = self.root / "unrelated"
        other.write_text("preserved")
        self.control.symlink_to(other)
        with self.assertRaisesRegex(ValueError, "Unexpected file"):
            await connection.disconnect()
        self.assertEqual(other.read_text(), "preserved")
        self.run.assert_not_called()

    async def test_world_readable_lock_rejected_before_startup(self):
        self.lock.touch(mode=0o644)
        with self.assertRaisesRegex(ValueError, "private file"):
            await connection.connect()
        self.run.assert_not_called()

    async def test_lock_symlink_does_not_touch_target(self):
        other = self.root / "unrelated"
        other.write_text("preserved")
        self.lock.symlink_to(other)
        with self.assertRaises(OSError):
            await connection.connect()
        self.assertEqual(other.read_text(), "preserved")
        self.run.assert_not_called()

    async def test_failed_start_does_not_claim_or_persist_a_master(self):
        self.run.side_effect = None
        self.run.return_value = 255
        self.probe.return_value = {"configured": True, "ready": False, "problem": "transport"}
        result = await connection.connect()
        self.assertEqual(result["action"], "ssh_failed")
        self.assertFalse(result["ready"])
        self.assertFalse(result["managed_tunnel"])
        self.assertFalse(self.metadata.exists())


class ConnectionProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_authentication_errors_are_redacted_and_redirects_not_followed(self):
        original_client = httpx.AsyncClient
        requests = []
        def response(request):
            requests.append(request)
            return httpx.Response(401, json={"error": "private-key-reflected"})
        def client(**options):
            self.assertFalse(options["follow_redirects"])
            self.assertFalse(options["trust_env"])
            return original_client(transport=httpx.MockTransport(response), **options)
        with patch.object(connection, "connection_settings", return_value=("https://gateway.example.test/prefix", "private-key")), patch.object(connection.httpx, "AsyncClient", side_effect=client):
            result = await connection._probe("gateway")
        self.assertEqual(result["problem"], "authentication")
        self.assertEqual(result["http_status"], 401)
        self.assertNotIn("private-key", json.dumps(result))
        self.assertEqual(str(requests[0].url), "https://gateway.example.test/prefix/v1/capabilities")
        self.assertEqual(requests[0].headers["Authorization"], "Bearer private-key")

    async def test_reset_after_port_accept_still_means_occupied(self):
        writer = unittest.mock.Mock()
        writer.wait_closed = AsyncMock(side_effect=ConnectionResetError("reset"))
        with patch.object(connection.asyncio, "open_connection", AsyncMock(return_value=(None, writer))):
            self.assertTrue(await connection.port_in_use(18642))
        writer.close.assert_called_once()

    async def test_cancelled_ssh_wait_reaps_only_its_own_child(self):
        created = []
        original = asyncio.create_subprocess_exec
        async def create(*arguments, **options):
            self.assertEqual(options["stdout"], asyncio.subprocess.DEVNULL)
            self.assertEqual(options["stderr"], asyncio.subprocess.DEVNULL)
            process = await original(*arguments, **options)
            created.append(process)
            return process
        with patch.object(connection.asyncio, "create_subprocess_exec", side_effect=create):
            task = asyncio.create_task(connection.run_ssh([sys.executable, "-c", "import time; time.sleep(30)"], timeout=5))
            while not created:
                await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(created[0].returncode)


class ConnectionPathsTests(unittest.TestCase):
    def test_control_paths_are_private_short_and_profile_specific(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def isolated_path(value):
                return root if value == "/tmp" else Path(value)
            with patch.object(connection, "Path", side_effect=isolated_path), patch.dict(os.environ, {"HERMES_BRIDGE_TOOL_CONFIG": str(root / "profile-one.json")}):
                first = connection.paths()
                self.assertEqual(stat.S_IMODE(first[0].parent.stat().st_mode), 0o700)
                with patch.dict(os.environ, {"HERMES_BRIDGE_TOOL_CONFIG": str(root / "profile-two.json")}):
                    second = connection.paths()
                self.assertNotEqual(first, second)
                self.assertLess(len(first[0].name), 32)
                first[0].parent.chmod(0o755)
                with self.assertRaisesRegex(ValueError, "private and owned"):
                    connection.paths()

    def test_control_directory_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "target").mkdir()
            (root / f"hermes-bridge-tool-{os.getuid()}").symlink_to(root / "target")
            with patch.object(connection, "Path", side_effect=lambda value: root if value == "/tmp" else Path(value)):
                with self.assertRaisesRegex(ValueError, "private and owned"):
                    connection.paths()


if __name__ == "__main__":
    unittest.main()
