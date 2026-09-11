import argparse
import contextlib
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from hermes_bridge_tool.config import Settings, load_settings
from hermes_bridge_tool.setup import pair_server, pairing_command, register_clients, run_setup


class SetupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.config = self.directory / "config.json"
        self.key = self.directory / "api-key"
        self.settings = Settings(api_key_file=str(self.key))
        environment = patch.dict(os.environ, {"HERMES_BRIDGE_TOOL_CONFIG": str(self.config)})
        environment.start()
        self.addCleanup(environment.stop)

    def completed(self, data, code=0):
        return subprocess.CompletedProcess([], code, json.dumps(data), "")

    def test_pairing_saves_private_key_without_returning_it(self):
        data = {"ok": True, "ready": True, "api_key": "private-test-key", "changed": True}
        with patch("hermes_bridge_tool.setup.subprocess.run", return_value=self.completed(data)) as run:
            result = pair_server(self.settings)
        self.assertEqual(self.key.read_text(), "private-test-key\n")
        self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o600)
        self.assertEqual(load_settings(), self.settings)
        self.assertNotIn("api_key", result)
        self.assertNotIn("private-test-key", str(run.call_args))
        self.assertIn("--restart", run.call_args.args[0][-1])
        self.assertIn("configure", run.call_args.kwargs["input"])

    def test_failed_pairing_leaves_local_files_untouched(self):
        self.key.write_text("original")
        self.config.write_text('{"ssh_host":"original"}')
        failures = [
            self.completed({"ok": False, "error": "Gateway restart failed"}, 1),
            self.completed({"ok": True, "ready": False, "api_key": "key"}),
            self.completed({"ok": True, "ready": True, "api_key": "bad\nkey"}),
            subprocess.CompletedProcess([], 1, "accidentally reflected secret", "private-test-key"),
        ]
        for result in failures:
            with self.subTest(result=result.returncode), patch("hermes_bridge_tool.setup.subprocess.run", return_value=result), self.assertRaises(ValueError) as error:
                pair_server(self.settings)
            self.assertNotIn("private-test-key", str(error.exception))
            self.assertNotIn("accidentally reflected secret", str(error.exception))
            self.assertEqual(self.key.read_text(), "original")
            self.assertEqual(self.config.read_text(), '{"ssh_host":"original"}')

    def test_timeout_preserves_local_state_and_reports_uncertain_remote_result(self):
        with patch("hermes_bridge_tool.setup.subprocess.run", side_effect=subprocess.TimeoutExpired("ssh", 120, output="secret")), self.assertRaisesRegex(ValueError, "may have changed") as error:
            pair_server(self.settings)
        self.assertNotIn("secret", str(error.exception))
        self.assertFalse(self.key.exists())

    def test_observer_pairing_requires_readiness_before_saving_local_key(self):
        data = {"ok": True, "ready": True, "observer_ready": False, "api_key": "private-test-key"}
        with patch("hermes_bridge_tool.setup.subprocess.run", return_value=self.completed(data)) as run:
            with self.assertRaisesRegex(ValueError, "observer is not ready"):
                pair_server(self.settings, observe_sessions=True)
        self.assertFalse(self.key.exists())
        self.assertFalse(self.config.exists())
        self.assertIn("--observe-sessions", run.call_args.args[0][-1])
        self.assertTrue(run.call_args.kwargs["input"].startswith("OBSERVER_SOURCE = "))

    def test_config_write_failure_restores_previous_key(self):
        self.key.write_text("original\n")
        result = self.completed({"ok": True, "ready": True, "api_key": "new-test-key"})
        with patch("hermes_bridge_tool.setup.subprocess.run", return_value=result), patch("hermes_bridge_tool.setup.save_settings", side_effect=PermissionError("read only")), self.assertRaises(PermissionError):
            pair_server(self.settings)
        self.assertEqual(self.key.read_text(), "original\n")
        self.assertFalse(self.config.exists())

    def test_config_write_failure_removes_new_unpaired_key(self):
        result = self.completed({"ok": True, "ready": True, "api_key": "new-test-key"})
        with patch("hermes_bridge_tool.setup.subprocess.run", return_value=result), patch("hermes_bridge_tool.setup.save_settings", side_effect=PermissionError("read only")), self.assertRaises(PermissionError):
            pair_server(self.settings)
        self.assertFalse(self.key.exists())

    def test_remote_arguments_cannot_become_shell_commands(self):
        custom_home = "/srv/profile with spaces/'; touch /tmp/not-executed; '"
        command = pairing_command(self.settings, "hermes", custom_home)
        remote = shlex.split(command[-1])
        self.assertEqual(remote[-1], custom_home)
        self.assertIn("runuser", remote[2])
        self.assertIn("StrictHostKeyChecking=yes", command)
        for user in ("-root", "root; command", "root\n", "$(id)"):
            with self.subTest(user=user), self.assertRaises(ValueError):
                pairing_command(self.settings, user, None)

    def test_register_preflights_all_clients_and_uses_argument_arrays(self):
        with patch("hermes_bridge_tool.setup.find_command", side_effect=lambda name: "/bin/codex" if name == "codex" else None), patch("hermes_bridge_tool.setup.subprocess.run") as run, self.assertRaises(ValueError):
            register_clients("both")
        run.assert_not_called()
        with patch("hermes_bridge_tool.setup.find_command", side_effect=lambda name: "/bin/" + name), patch("hermes_bridge_tool.setup.bridge_command", return_value=["/path with spaces/hermes-bridge-tool", "mcp"]), patch("hermes_bridge_tool.setup.subprocess.run", return_value=self.completed({})) as run:
            self.assertEqual(register_clients("both"), ["codex", "claude"])
        self.assertEqual(run.call_args_list[0].args[0][-2:], ["/path with spaces/hermes-bridge-tool", "mcp"])
        self.assertIn("--scope", run.call_args_list[1].args[0])

    def test_claude_repeat_registration_is_idempotent_without_removing_entry(self):
        duplicate = subprocess.CompletedProcess([], 1, "", "MCP server hermes-bridge-tool already exists in user config")
        existing = subprocess.CompletedProcess([], 0, "hermes-bridge-tool:\n  Scope: User config (available in all your projects)\n  Command: /bin/hermes-bridge-tool\n  Args: mcp\n", "")
        with patch("hermes_bridge_tool.setup.find_command", return_value="/bin/claude"), patch("hermes_bridge_tool.setup.bridge_command", return_value=["/bin/hermes-bridge-tool", "mcp"]), patch("hermes_bridge_tool.setup.subprocess.run", side_effect=[duplicate, existing]) as run:
            self.assertEqual(register_clients("claude"), ["claude"])
        self.assertEqual(run.call_args_list[-1].args[0][1:], ["mcp", "get", "hermes-bridge-tool"])

    def test_noninteractive_setup_preflights_without_remote_changes(self):
        args = argparse.Namespace(yes=True, host="server", remote_user="hermes", remote_home=None, client="none", local_port=0, remote_port=None)
        with patch("hermes_bridge_tool.setup.pair_server") as pair, self.assertRaises(ValueError):
            run_setup(args)
        pair.assert_not_called()
        args.local_port = None
        args.yes = False
        with patch("sys.stdin.isatty", return_value=False), self.assertRaises(ValueError):
            run_setup(args)

    def test_wizard_completion_does_not_print_pairing_key(self):
        args = argparse.Namespace(yes=True, host="server", remote_user="-", remote_home=None, client="none", local_port=None, remote_port=None)
        output = io.StringIO()
        with patch("hermes_bridge_tool.setup.pair_server", return_value={"ready": True}) as pair, patch("hermes_bridge_tool.setup.register_clients", return_value=[]), patch("sys.platform", "linux"), contextlib.redirect_stdout(output):
            self.assertEqual(run_setup(args), 0)
        self.assertIsNone(pair.call_args.args[1])
        self.assertIn("Pairing complete", output.getvalue())
        args.observe_sessions = True
        with patch("hermes_bridge_tool.setup.pair_server", return_value={"ready": True, "observer_ready": True}) as pair, patch("hermes_bridge_tool.setup.register_clients", return_value=[]), patch("sys.platform", "linux"), contextlib.redirect_stdout(output):
            self.assertEqual(run_setup(args), 0)
        self.assertTrue(pair.call_args.kwargs["observe_sessions"])
        self.assertIn("observer plugin", output.getvalue())

    def test_pairing_runs_bundled_helper_through_stdin(self):
        """Exercise the process boundary without a real SSH server or Hermes agent."""
        remote = self.directory / "remote profile"
        remote.mkdir()
        (remote / ".env").write_text("MODEL=preserved\nAPI_SERVER_KEY=existing-pairing-key\n")
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                result = {"object": "list", "data": []} if self.path.startswith("/hermes-bridge-tool/") else {"features": {"run_submission": True, "run_status": True, "run_stop": True}}
                body = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        api = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=api.serve_forever, daemon=True)
        thread.start()
        fake_bin = self.directory / "bin"
        fake_bin.mkdir()
        # Fake SSH executes exactly the serialized remote command in a child.
        (fake_bin / "ssh").write_text("#!/bin/sh\nfor argument do remote_command=$argument; done\nexec sh -c \"$remote_command\"\n")
        (fake_bin / "hermes").write_text("#!/bin/sh\ncase \"$*\" in 'gateway restart'|'plugins enable hermes-bridge-tool --no-allow-tool-override') exit 0 ;; *) exit 1 ;; esac\n")
        for path in fake_bin.iterdir():
            path.chmod(0o755)
        settings = Settings(remote_port=api.server_port, api_key_file=str(self.key))
        try:
            with patch.dict(os.environ, {"PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]}):
                result = pair_server(settings, remote_user=None, remote_home=str(remote))
            self.assertTrue(result["ready"])
            self.assertEqual(self.key.read_text(), "existing-pairing-key\n")
            self.assertEqual(requests, [("/v1/capabilities", "Bearer existing-pairing-key")])
            self.assertIn("MODEL=preserved", (remote / ".env").read_text())
            self.assertIn("API_SERVER_HOST=127.0.0.1", (remote / ".env").read_text())
            requests.clear()
            with patch.dict(os.environ, {"PATH": str(fake_bin) + os.pathsep + os.environ["PATH"]}):
                result = pair_server(settings, remote_user=None, remote_home=str(remote), observe_sessions=True)
            self.assertTrue(result["observer_ready"])
            self.assertEqual(requests, [
                ("/v1/capabilities", "Bearer existing-pairing-key"),
                ("/hermes-bridge-tool/v1/turns?session_id=hermes-bridge-tool-readiness&limit=1", "Bearer existing-pairing-key"),
            ])
            self.assertEqual((remote / "plugins/hermes-bridge-tool/__init__.py").read_text(),
                             (Path(__file__).parents[1] / "src/hermes_bridge_tool/observer_plugin.py").read_text())
        finally:
            api.shutdown()
            api.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
