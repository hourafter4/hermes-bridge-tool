import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from hermes_bridge.config import Settings, connection_settings, load_settings, private_write, save_settings


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        env = patch.dict(os.environ, {"HERMES_BRIDGE_CONFIG": str(self.path / "config.json")})
        env.start()
        self.addCleanup(env.stop)

    def test_shared_settings_drive_api_and_ssh(self):
        key_file = self.path / "api-key"
        private_write(key_file, "local-test-key\n")
        settings = Settings(ssh_host="user@server", local_port=19642, remote_port=9642, api_key_file=str(key_file))
        save_settings(settings)
        with patch.dict(os.environ, {}, clear=True):
            os.environ["HERMES_BRIDGE_CONFIG"] = str(self.path / "config.json")
            self.assertEqual(load_settings(), settings)
            self.assertEqual(connection_settings(), ("http://127.0.0.1:19642", "local-test-key"))
        self.assertIn("127.0.0.1:19642:127.0.0.1:9642", settings.ssh_command())
        self.assertEqual(settings.ssh_command()[-1], "user@server")
        self.assertIn("BatchMode=yes", settings.ssh_command())

    def test_private_writes_replace_files_with_restricted_permissions(self):
        path = self.path / "key"
        path.write_text("old")
        path.chmod(0o644)
        private_write(path, "new")
        self.assertEqual(path.read_text(), "new")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_host_cannot_inject_ssh_options(self):
        for host in ("-oProxyCommand=bad", "server;touch /tmp/bad", "server\nother", "", None):
            with self.subTest(host=host), self.assertRaises(ValueError):
                Settings(ssh_host=host).ssh_command()

    def test_rejects_invalid_ports_and_malformed_config(self):
        for port in (0, 65536, True, "8642"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                Settings(local_port=port).validate()
        for content in ("[]", "not json", '{"remote_port": "8642"}'):
            (self.path / "config.json").write_text(content)
            with self.subTest(content=content), self.assertRaises(ValueError):
                load_settings()

    def test_url_override_cannot_send_credentials_off_loopback(self):
        for url in ("https://example.com", "http://localhost.evil", "http://user:pass@localhost", "http://127.0.0.1/v1", "http://localhost:invalid"):
            with self.subTest(url=url), patch.dict(os.environ, {"HERMES_API_URL": url, "HERMES_API_KEY": "secret"}), self.assertRaises(ValueError):
                connection_settings()

    def test_configuration_does_not_contain_secret(self):
        save_settings(Settings())
        data = json.loads((self.path / "config.json").read_text())
        self.assertNotIn("api_key", data)
        self.assertEqual(data["api_key_file"], "~/.config/hermes-bridge/api-key")


if __name__ == "__main__":
    unittest.main()
