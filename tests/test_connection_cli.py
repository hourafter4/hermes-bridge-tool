"""The app's JSON CLI contract for shared connection management."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from hermes_bridge_tool.cli import main


class ConnectionCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="bridge-connection-cli-")
        self.addCleanup(temporary.cleanup)
        environment = patch.dict(os.environ, {"HERMES_BRIDGE_TOOL_CONFIG": str(Path(temporary.name) / "config.json")}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def invoke(self, command):
        output = io.StringIO()
        errors = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main([command])
        return code, json.loads(output.getvalue()), errors.getvalue()

    def test_connect_ensures_existing_transport_without_forcing_restart(self):
        report = {"ready": True, "action": "reused", "managed_tunnel": True}
        with patch("hermes_bridge_tool.connection.connect", AsyncMock(return_value=report)) as connect:
            code, result, errors = self.invoke("connect")
        self.assertEqual(code, 0)
        self.assertEqual(result, report)
        self.assertEqual(errors, "")
        connect.assert_awaited_once()
        self.assertFalse(connect.call_args.kwargs.get("restart", False))

    def test_reconnect_requests_bounded_transport_repair(self):
        report = {"ready": True, "action": "reconnected"}
        with patch("hermes_bridge_tool.connection.connect", AsyncMock(return_value=report)) as connect:
            code, result, errors = self.invoke("reconnect")
        self.assertEqual(code, 0)
        self.assertEqual(result, report)
        connect.assert_awaited_once_with(restart=True)
        self.assertEqual(errors, "")

    def test_status_observes_without_starting_or_stopping_transport(self):
        report = {"ready": False, "managed_tunnel": False, "gateway": {"problem": "transport"}}
        with patch("hermes_bridge_tool.connection.connection_status", AsyncMock(return_value=report)) as status, \
                patch("hermes_bridge_tool.connection.connect", AsyncMock()) as connect, \
                patch("hermes_bridge_tool.connection.disconnect", AsyncMock()) as disconnect:
            code, result, errors = self.invoke("connection-status")
        self.assertEqual(code, 1)
        self.assertEqual(result, report)
        self.assertEqual(errors, "")
        status.assert_awaited_once_with()
        connect.assert_not_awaited()
        disconnect.assert_not_awaited()

    def test_successful_disconnect_exits_zero_even_though_not_ready(self):
        report = {"ready": False, "action": "disconnected", "managed_tunnel": False}
        with patch("hermes_bridge_tool.connection.disconnect", AsyncMock(return_value=report)) as disconnect:
            code, result, errors = self.invoke("disconnect")
        self.assertEqual(code, 0)
        self.assertEqual(result, report)
        self.assertEqual(errors, "")
        disconnect.assert_awaited_once_with()

    def test_failed_readiness_is_preserved_without_retrying_or_claiming_repair(self):
        for command in ("connect", "reconnect"):
            report = {"ready": False, "action": "no_transport_repair", "gateway": {"problem": "authentication"}}
            with self.subTest(command=command), patch("hermes_bridge_tool.connection.connect", AsyncMock(return_value=report)) as connect:
                code, result, errors = self.invoke(command)
            self.assertEqual(code, 1)
            self.assertEqual(result, report)
            self.assertEqual(errors, "")
            connect.assert_awaited_once()

    def test_failures_remain_json_and_do_not_reflect_sensitive_exceptions(self):
        functions = {"connect": "connect", "reconnect": "connect", "disconnect": "disconnect", "connection-status": "connection_status"}
        secret = "credential-reflected-by-subprocess"
        for command, function in functions.items():
            for failure in (ValueError(secret), OSError(secret), TimeoutError(secret)):
                with self.subTest(command=command, failure=type(failure).__name__), \
                        patch(f"hermes_bridge_tool.connection.{function}", AsyncMock(side_effect=failure)) as operation:
                    code, result, errors = self.invoke(command)
                self.assertEqual(code, 1)
                self.assertIs(result["ready"], False)
                self.assertNotIn(secret, json.dumps(result) + errors)
                operation.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
