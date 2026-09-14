"""Real local listeners must never receive bridge credentials outside SSH."""

import asyncio
from functools import partial
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from mcp.server.fastmcp.exceptions import ToolError

from hermes_bridge_tool import connection, policy, server, transport, webui
from hermes_bridge_tool.config import load_settings


class TransportSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="hbt-security-", dir="/tmp")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.received = []
        self.listeners = []
        self.control_paths = []
        self.env = patch.dict(os.environ, {"HERMES_BRIDGE_TOOL_CONFIG": str(self.root / "config.json")})
        self.env.start()
        self.addCleanup(self.env.stop)
        for name in ("HERMES_API_URL", "HERMES_API_KEY", "HERMES_API_KEY_FILE", "HERMES_WEBUI_URL", "HERMES_WEBUI_AUTH_FILE"):
            os.environ.pop(name, None)
        self.gateway_listener = await self.listen_tcp()
        self.webui_listener = await self.listen_tcp()
        gateway_port = self.gateway_listener.sockets[0].getsockname()[1]
        webui_port = self.webui_listener.sockets[0].getsockname()[1]
        (self.root / "key").write_text("dummy-gateway-secret")
        (self.root / "cookie.json").write_text('{"Cookie":"dummy-webui-secret"}')
        self.config = {"local_port": gateway_port, "webui_local_port": webui_port,
                       "webui_ssh": True, "webui_url": f"http://127.0.0.1:{webui_port}",
                       "api_key_file": str(self.root / "key"), "webui_auth_file": str(self.root / "cookie.json"),
                       "security": {"mode": "control"}}
        self.save_config()
        self.control, self.metadata, self.lock = connection.paths()
        self.control_paths = [self.control, self.metadata, self.lock,
                              transport.socket_for(self.control, "gateway"), transport.socket_for(self.control, "webui")]

    def save_config(self):
        (self.root / "config.json").write_text(json.dumps(self.config))

    async def asyncTearDown(self):
        for listener in self.listeners:
            listener.close()
            await listener.wait_closed()
        for path in self.control_paths:
            path.unlink(missing_ok=True)

    async def handle(self, reader, writer, *, source="tcp"):
        # Record accepted connections, even if credentials arrive in a later packet.
        record = {"headers": b"", "source": source}
        self.received.append(record)
        try:
            record["headers"] = await reader.readuntil(b"\r\n\r\n")
            body = json.dumps({"features": {"run_submission": True, "run_status": True, "run_stop": True}, "sessions": []}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    async def listen_tcp(self):
        listener = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        self.listeners.append(listener)
        return listener

    async def trusted_socket(self, backend):
        target = transport.socket_for(self.control, backend)
        listener = await asyncio.start_unix_server(partial(self.handle, source="unix"), path=str(target))
        self.listeners.append(listener)
        self.metadata.write_text(json.dumps(transport.expected_metadata(load_settings())))
        return target

    async def test_foreign_tcp_listener_receives_zero_connections_during_requests_and_health(self):
        with patch.object(connection, "master_running", AsyncMock(return_value=False)):
            for request in (lambda: server.api_request("GET", "/v1/capabilities"),
                            lambda: webui.webui_request("GET", "/api/sessions")):
                with self.assertRaisesRegex(ToolError, "no credentials were sent"):
                    await request()
            report = await connection.connection_status()
        self.assertFalse(report["ready"])
        self.assertEqual(report["gateway"]["problem"], "transport")
        self.assertEqual(report["webui"]["problem"], "transport")
        self.assertEqual(self.received, [])

    async def test_unmanaged_loopback_http_is_rejected_before_any_connection(self):
        self.config["local_port"] = 18642 if self.config["local_port"] != 18642 else 18643
        self.config["gateway_url"] = f"http://127.0.0.1:{self.gateway_listener.sockets[0].getsockname()[1]}"
        self.config["webui_ssh"] = False
        self.save_config()
        for request in (lambda: server.api_request("GET", "/v1/capabilities"),
                        lambda: webui.webui_request("GET", "/api/sessions")):
            with self.assertRaisesRegex(ToolError, "Unmanaged loopback HTTP"):
                await request()
        self.assertEqual(self.received, [])

    async def test_valid_private_sockets_receive_credentials_and_bypass_tcp_listeners(self):
        await self.trusted_socket("gateway")
        await self.trusted_socket("webui")
        with patch.object(connection, "master_running", AsyncMock(return_value=True)):
            gateway = await server.api_request("GET", "/v1/capabilities")
            ui = await webui.webui_request("GET", "/api/sessions")
        self.assertTrue(gateway["features"]["run_submission"])
        self.assertEqual(ui["sessions"], [])
        self.assertEqual(len(self.received), 2)
        self.assertTrue(all(record["source"] == "unix" for record in self.received))
        self.assertIn(b"Authorization: Bearer dummy-gateway-secret", self.received[0]["headers"])
        self.assertIn(b"Cookie: dummy-webui-secret", self.received[1]["headers"])

    async def test_regular_file_and_symlink_are_not_accepted_as_private_socket(self):
        real = self.root / "real.sock"
        self.listeners.append(await asyncio.start_unix_server(self.handle, path=str(real)))
        self.metadata.write_text(json.dumps(transport.expected_metadata(load_settings())))
        for backend in ("gateway", "webui"):
            target = transport.socket_for(self.control, backend)
            for kind in ("file", "symlink"):
                if kind == "file":
                    target.write_text("not a socket")
                else:
                    target.symlink_to(real)
                with patch.object(connection, "master_running", AsyncMock(return_value=True)):
                    with self.assertRaisesRegex(ToolError, "socket is invalid"):
                        if backend == "gateway":
                            await server.api_request("GET", "/v1/capabilities")
                        else:
                            await webui.webui_request("GET", "/api/sessions")
                target.unlink()
        self.assertEqual(self.received, [])

    async def test_changed_metadata_denies_even_valid_socket(self):
        await self.trusted_socket("gateway")
        metadata = json.loads(self.metadata.read_text())
        metadata["host"] = "other-server"
        self.metadata.write_text(json.dumps(metadata))
        with patch.object(connection, "master_running", AsyncMock(return_value=True)):
            with self.assertRaisesRegex(ToolError, "settings changed"):
                await server.api_request("GET", "/v1/capabilities")
        self.assertEqual(self.received, [])

    async def test_policy_revocation_during_transport_check_prevents_task_dispatch(self):
        await self.trusted_socket("gateway")
        await self.trusted_socket("webui")

        async def revoke(_):
            policy.update(mode="monitor")
            return True

        with patch.object(connection, "master_running", side_effect=revoke):
            for request in (lambda: server.api_request("POST", "/v1/runs", payload={"prompt": "never send"}),
                            lambda: webui.webui_request("GET", "/api/chat/cancel", mutation=True)):
                policy.update(mode="control")
                with self.assertRaisesRegex(ToolError, "denies tasks"):
                    await request()
        self.assertEqual(self.received, [])

    async def test_lock_during_transport_check_prevents_even_reads(self):
        await self.trusted_socket("gateway")

        async def revoke(_):
            policy.update(locked=True)
            return True

        with patch.object(connection, "master_running", side_effect=revoke):
            with self.assertRaisesRegex(ToolError, "locked"):
                await server.api_request("GET", "/v1/capabilities")
        self.assertEqual(self.received, [])


if __name__ == "__main__":
    unittest.main()
