import base64
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from hermes_bridge_tool import runtime_ssh as runtime


KEY = "ssh-ed25519 " + base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"a" * 32).decode()
OTHER_KEY = "ssh-ed25519 " + base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"b" * 32).decode()


class RuntimeSSHTests(unittest.TestCase):
    def test_forwarding_authorization_and_daemon_restrictions(self):
        line = runtime.forwarding_key_line(KEY)
        self.assertIn('restrict,port-forwarding,permitopen="127.0.0.1:8642",permitopen="127.0.0.1:8787"', line)
        self.assertIn('command="/bin/false"', line)
        config = runtime.sshd_configuration("hermes-bridge")
        for restriction in ("AllowTcpForwarding local", "PermitListen none", "AllowStreamLocalForwarding no",
                            "AllowAgentForwarding no", "X11Forwarding no", "PermitTTY no", "MaxSessions 0"):
            self.assertIn(restriction, config)
        self.assertTrue(config.endswith("Match all\n"))

    def test_native_is_forced_and_has_no_forwarding(self):
        line = runtime.native_key_line(KEY, ["/opt/hermes/bin/hermes", "mcp", "serve"])
        self.assertIn('restrict,command="/opt/hermes/bin/hermes mcp serve"', line)
        self.assertNotIn("port-forwarding", line)
        with self.assertRaises(ValueError):
            runtime.native_key_line(KEY, ["hermes", "mcp", "serve"])

    def test_key_merge_preserves_unrelated_authorizations_and_is_idempotent(self):
        existing = "# personal key\n" + OTHER_KEY + " personal\n"
        line = runtime.native_key_line(KEY, ["/opt/hermes/bin/hermes", "mcp", "serve"])
        merged = runtime.merge_managed_key(existing, line)
        self.assertTrue(merged.startswith(existing))
        self.assertEqual(runtime.merge_managed_key(merged, line), merged)
        with self.assertRaises(ValueError):
            runtime.merge_managed_key(KEY + " unrelated\n", line)

    def test_invalid_user_key_and_destinations_rejected(self):
        for user in ("root", "-root", "user\nMatch all"):
            with self.assertRaises(ValueError):
                runtime.sshd_configuration(user)
        for key in (KEY + "\n" + OTHER_KEY, "ssh-ed25519 AAAA", 'command="sh" ' + KEY):
            with self.assertRaises(ValueError):
                runtime.forwarding_key_line(key)
        for ports in ([True], [0], [65536], []):
            with self.assertRaises(ValueError):
                runtime.forwarding_key_line(KEY, ports)

    def test_generation_uses_dedicated_identity_and_reuses_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "runtime_key"
            first = runtime.generate_runtime_key(path)
            self.assertEqual(runtime.generate_runtime_key(path), first)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertTrue(first.startswith("ssh-ed25519 "))

    def test_symlink_identity_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "key"
            path.symlink_to(Path(directory) / "missing")
            with self.assertRaisesRegex(ValueError, "symlink"):
                runtime.generate_runtime_key(path)

    def test_admin_transport_uses_stdin_and_no_runtime_sudo_key(self):
        with patch.object(runtime.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout='{"ok":true}\n')) as run:
            self.assertTrue(runtime.provision_runtime_ssh("admin-server", KEY)["ok"])
        args, kwargs = run.call_args
        self.assertNotIn(KEY, " ".join(args[0]))
        self.assertIn(KEY, kwargs["input"])
        self.assertIn("ForwardAgent=no", args[0])
        self.assertIn("StrictHostKeyChecking=yes", args[0])
        self.assertNotIn("sudo", runtime.native_key_line(KEY, ["/opt/hermes/bin/hermes", "mcp", "serve"]))

    def test_ineffective_daemon_policy_rejected(self):
        with patch.object(runtime, "_run", return_value="allowtcpforwarding yes\n"):
            with self.assertRaisesRegex(ValueError, "required runtime restrictions"):
                runtime._effective_config("sshd", "hermes-bridge", [8642, 8787])

    def test_only_managed_account_gets_unusable_password_for_public_key_login(self):
        managed = SimpleNamespace(pw_uid=996, pw_gecos=runtime.MARKER,
                                  pw_shell="/usr/sbin/nologin", pw_name="hermes-bridge")
        with patch.object(runtime, "_run") as run:
            runtime._allow_managed_public_key_login(managed)
            run.assert_called_once_with(["usermod", "--password", "*", "hermes-bridge"])
            run.reset_mock()
            for change in ({"pw_uid": 0}, {"pw_gecos": "Another account"}, {"pw_shell": "/bin/bash"}):
                invalid = SimpleNamespace(**{**vars(managed), **change})
                with self.assertRaisesRegex(ValueError, "unrelated account"):
                    runtime._allow_managed_public_key_login(invalid)
            run.assert_not_called()

    def test_native_authorization_writer_drops_admin_privileges(self):
        account = SimpleNamespace(pw_uid=1234, pw_gid=1234)
        with patch.object(runtime, "_run") as run:
            runtime._write_native_authorization(Path("/home/hermes/.ssh/authorized_keys"),
                                                runtime.native_key_line(KEY, ["/opt/hermes/bin/hermes", "mcp", "serve"]), account)
        self.assertEqual(run.call_args.kwargs["user"], 1234)
        self.assertEqual(run.call_args.kwargs["group"], 1234)
        self.assertEqual(run.call_args.kwargs["extra_groups"], [])
        self.assertIn(KEY, run.call_args.kwargs["input"])

    def test_native_preflight_preserves_passwords_and_unrelated_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            account = SimpleNamespace(pw_uid=1011, pw_name="hermes", pw_dir=str(Path(directory).resolve()))
            config = "authorizedkeysfile .ssh/authorized_keys .ssh/authorized_keys2\nauthorizedkeyscommand none\ntrustedusercakeys none\nauthorizedprincipalscommand none\n"
            for password in ("$synthetic-hash", "!$synthetic-hash", ""):
                with patch.object(runtime, "_run", return_value=f"hermes:{password}:1:0:99999:7:::\n"):
                    with self.assertRaisesRegex(ValueError, "password or custom login"):
                        runtime._native_login_preflight(account, "sshd")
            for password, expected in (("!", True), ("*", False)):
                with patch.object(runtime, "_run", side_effect=[f"hermes:{password}:1:0:99999:7:::\n", config]):
                    self.assertEqual(runtime._native_login_preflight(account, "sshd"), expected)
            path = Path(directory) / ".ssh/authorized_keys"
            path.parent.mkdir()
            path.write_text(OTHER_KEY + " existing-access\n")
            with patch.object(runtime, "_run", side_effect=["hermes:!:1:0:99999:7:::\n", config]):
                with self.assertRaisesRegex(ValueError, "unrelated authorized keys"):
                    runtime._native_login_preflight(account, "sshd")

    def test_native_preflight_refuses_additional_auth_providers(self):
        account = SimpleNamespace(pw_uid=1011, pw_name="hermes", pw_dir="/home/hermes")
        with patch.object(runtime, "_run", side_effect=["hermes:!:1:0:99999:7:::\n", "trustedusercakeys /etc/ssh/company-ca\n"]):
            with self.assertRaisesRegex(ValueError, "authorization provider"):
                runtime._native_login_preflight(account, "sshd")

    def test_native_effective_command_must_match_exactly(self):
        command = ["/home/hermes/.local/bin/hermes", "mcp", "serve"]
        lines = runtime.native_sshd_configuration("hermes", command).splitlines()
        effective = "\n".join(line.strip().split(" ", 1)[0].lower() + " " + line.strip().split(" ", 1)[1]
                              for line in lines if line.startswith("    "))
        with patch.object(runtime, "_run", return_value=effective):
            runtime._effective_native_config("sshd", "hermes", command)
        for weak in (effective.replace("forcecommand /home/hermes/.local/bin/hermes mcp serve", "forcecommand none"),
                     effective.replace("passwordauthentication no", "passwordauthentication yes")):
            with patch.object(runtime, "_run", return_value=weak):
                with self.assertRaisesRegex(ValueError, "not match"):
                    runtime._effective_native_config("sshd", "hermes", command)

    def test_unrelated_account_is_never_modified(self):
        import pwd
        account = SimpleNamespace(pw_uid=1000, pw_gecos="Existing person", pw_shell="/bin/bash")
        with tempfile.TemporaryDirectory() as directory, patch.object(runtime.os, "geteuid", return_value=0), \
                patch.object(pwd, "getpwnam", return_value=account), \
                patch.object(runtime, "STATE", Path(directory).resolve() / "state"), \
                patch.object(runtime, "DROPIN", Path(directory).resolve() / "sshd" / "dropin"), \
                patch.object(runtime, "_run") as run:
            with self.assertRaisesRegex(ValueError, "unrelated account"):
                runtime.provision_on_server({"forwarding_public_key": KEY})
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
