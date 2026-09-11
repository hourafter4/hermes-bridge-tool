import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

from hermes_bridge import server_setup


class ServerSetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.env = self.home / ".env"
        self.env.write_text("# Existing provider\nPROVIDER_KEY=provider-secret\n")
        self.command = patch.object(server_setup, "hermes_command", return_value=["/test/hermes"])
        self.command.start()
        self.addCleanup(self.command.stop)

    def run_setup(self, *args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = server_setup.main(["--home", str(self.home), *args])
        return code, stream.getvalue()

    def test_pairing_is_private_idempotent_and_preserves_provider_settings(self):
        code, output = self.run_setup("--json")
        result = json.loads(output)
        self.assertEqual(code, 0)
        self.assertTrue(result["changed"])
        self.assertFalse(result["ready"])
        self.assertGreaterEqual(len(result["api_key"]), 32)
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)
        backup = Path(result["backup"])
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
        self.assertEqual(backup.read_text(), "# Existing provider\nPROVIDER_KEY=provider-secret\n")
        self.assertIn(backup.read_text(), self.env.read_text())
        before = self.env.read_bytes()
        code, output = self.run_setup("--json")
        second = json.loads(output)
        self.assertEqual(code, 0)
        self.assertFalse(second["changed"])
        self.assertEqual(second["api_key"], result["api_key"])
        self.assertEqual(self.env.read_bytes(), before)
        self.assertEqual(len(list(self.home.glob(".env.hermes-bridge-backup-*"))), 1)

    def test_preserves_quoted_exported_key_and_comments_deduplicates_settings(self):
        self.env.write_text("export API_SERVER_KEY='existing-key' # keep key\n"
                            'API_SERVER_KEY="existing-key" # duplicate note\n'
                            "API_SERVER_PORT=1234\nAPI_SERVER_PORT=4567\n# My note\n")
        code, output = self.run_setup("--json", "--port", "9864")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["api_key"], "existing-key")
        result = self.env.read_text()
        for name in server_setup.NAMES:
            self.assertEqual(result.count(name + "="), 1)
        self.assertIn("API_SERVER_PORT=9864", result)
        self.assertIn("# keep key", result)
        self.assertIn("# duplicate note", result)
        self.assertIn("# My note", result)

    def test_blank_key_generates_new_key_without_shell_evaluation(self):
        self.env.write_text('API_SERVER_KEY=""\nOTHER=$(touch /never-execute)\n')
        code, output = self.run_setup("--json")
        self.assertEqual(code, 0)
        self.assertGreater(len(json.loads(output)["api_key"]), 30)
        self.assertIn("OTHER=$(touch /never-execute)", self.env.read_text())

    def test_malformed_conflicting_and_dynamic_keys_fail_without_changes_or_secrets(self):
        for content in ('API_SERVER_KEY="sensitive-secret\n',
                        'API_SERVER_KEY sensitive-secret\n',
                        'API_SERVER_KEY=sensitive-secret\nAPI_SERVER_KEY=different-secret\n',
                        'API_SERVER_KEY=${sensitive-secret}\n'):
            with self.subTest(content=content):
                self.env.write_text(content)
                code, output = self.run_setup("--json")
                self.assertEqual(code, 1)
                self.assertNotIn("sensitive-secret", output)
                self.assertNotIn("different-secret", output)
                self.assertNotIn("api_key", json.loads(output))
                self.assertEqual(self.env.read_text(), content)
                self.assertFalse(list(self.home.glob(".env.hermes-bridge-backup-*")))

    def test_check_has_no_mutations_restart_or_credential_output(self):
        original = self.env.read_bytes()
        with patch.object(server_setup.subprocess, "run") as run:
            code, output = self.run_setup("--check", "--restart", "--json")
        self.assertEqual(code, 0)
        result = json.loads(output)
        self.assertTrue(result["would_change"])
        self.assertFalse(result["changed"])
        self.assertNotIn("api_key", result)
        self.assertEqual(self.env.read_bytes(), original)
        self.assertEqual(list(self.home.iterdir()), [self.env])
        run.assert_not_called()

    def test_human_output_never_contains_key(self):
        self.env.write_text("API_SERVER_KEY=private-secret-key\n")
        code, output = self.run_setup()
        self.assertEqual(code, 0)
        self.assertNotIn("private-secret-key", output)
        self.assertNotIn("provider-secret", output)

    def test_existing_short_key_is_preserved_without_rotation(self):
        self.env.write_text("API_SERVER_KEY=x\n")
        code, output = self.run_setup("--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["api_key"], "x")

    def test_invalid_encoding_is_reported_without_raw_environment_bytes(self):
        self.env.write_bytes(b"PROVIDER_KEY=private-secret\xff\n")
        code, output = self.run_setup("--json")
        self.assertEqual(code, 1)
        self.assertNotIn("private-secret", output)
        self.assertIn("permissions", json.loads(output)["error"])

    def test_permission_repair_and_unrelated_line_endings_are_preserved(self):
        self.env.write_bytes(b"# Provider\r\nPROVIDER_KEY=private-secret\r\n")
        code, output = self.run_setup("--json")
        self.assertEqual(code, 0)
        self.assertTrue(self.env.read_bytes().startswith(b"# Provider\r\nPROVIDER_KEY=private-secret\r\n"))
        self.env.chmod(0o644)
        code, output = self.run_setup("--json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output)["changed"])
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)

    def test_no_default_home_is_created_for_wrong_user(self):
        self.env.unlink()
        with patch.object(Path, "home", return_value=self.home), patch.dict(os.environ, {}, clear=True):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = server_setup.main(["--json"])
        self.assertEqual(code, 1)
        self.assertIn("service user", json.loads(stream.getvalue())["error"])
        self.assertFalse((self.home / ".hermes").exists())

    def test_symlink_env_is_not_replaced(self):
        target = self.home / "private.env"
        self.env.rename(target)
        self.env.symlink_to(target)
        code, output = self.run_setup("--json")
        self.assertEqual(code, 1)
        self.assertIn("symlink", json.loads(output)["error"])
        self.assertTrue(self.env.is_symlink())

    def test_restart_and_capability_validation_receive_private_key(self):
        with patch.object(server_setup.subprocess, "run", return_value=Mock(returncode=0)) as run, \
                patch.object(server_setup, "wait_ready") as ready:
            code, output = self.run_setup("--restart", "--json")
        result = json.loads(output)
        self.assertEqual(code, 0)
        self.assertTrue(result["ready"])
        self.assertTrue(result["restarted"])
        self.assertEqual(run.call_args.args[0], ["/test/hermes", "gateway", "restart"])
        self.assertEqual(run.call_args.kwargs["env"]["HERMES_HOME"], str(self.home.resolve()))
        self.assertEqual(run.call_args.kwargs["stdout"], subprocess.DEVNULL)
        ready.assert_called_once_with(8642, result["api_key"])

    def test_restart_failure_keeps_saved_credential_and_suppresses_output(self):
        self.env.write_text("API_SERVER_KEY=existing-secret\n")
        for failure in (Mock(returncode=1), subprocess.TimeoutExpired("hermes", 40, output="existing-secret")):
            with self.subTest(failure=failure):
                kwargs = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with patch.object(server_setup.subprocess, "run", **kwargs):
                    code, output = self.run_setup("--restart", "--json")
                self.assertEqual(code, 1)
                self.assertNotIn("existing-secret", output)
                self.assertNotIn("api_key", json.loads(output))
                self.assertIn("API_SERVER_KEY=existing-secret", self.env.read_text())

    def test_restart_restores_user_service_bus_for_non_login_sessions(self):
        original_is_dir = Path.is_dir
        def is_dir(path):
            return str(path) == "/run/user/4321" or original_is_dir(path)
        with patch.object(Path, "is_dir", is_dir), patch.object(os, "getuid", return_value=4321), \
                patch.dict(os.environ, {}, clear=True), patch.object(server_setup, "wait_ready"), \
                patch.object(server_setup.subprocess, "run", return_value=Mock(returncode=0)) as run:
            code, _ = self.run_setup("--restart", "--json")
        self.assertEqual(code, 0)
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["XDG_RUNTIME_DIR"], "/run/user/4321")
        self.assertEqual(environment["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/4321/bus")

    def test_capabilities_enforce_runs_features_and_never_follow_redirects(self):
        opener = Mock()
        response = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        for features, succeeds in ((dict(run_submission=True, run_status=True, run_stop=True), True),
                                   (dict(run_status=True), False), ([], False)):
            response.read.return_value = json.dumps({"features": features}).encode()
            with patch.object(server_setup.urllib.request, "build_opener", return_value=opener) as build:
                if succeeds:
                    server_setup.wait_ready(8642, "private-secret")
                else:
                    with self.assertRaisesRegex(ValueError, "Runs features"):
                        server_setup.wait_ready(8642, "private-secret")
            self.assertEqual(build.call_args.args[0].proxies, {})
            self.assertIsInstance(build.call_args.args[1], server_setup.NoRedirect)
        opener.open.side_effect = urllib.error.HTTPError("url", 302, "private-secret", {}, None)
        with patch.object(server_setup.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "HTTP 302") as raised:
                server_setup.wait_ready(8642, "private-secret")
        self.assertNotIn("private-secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
