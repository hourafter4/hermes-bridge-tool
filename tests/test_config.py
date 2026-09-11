import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from hermes_bridge_tool.config import Settings, connection_settings, endpoint_settings, load_settings, private_write, save_settings, webui_connection_settings


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        env = patch.dict(os.environ, {"HERMES_BRIDGE_TOOL_CONFIG": str(self.path / "config.json")})
        env.start()
        self.addCleanup(env.stop)

    def test_shared_settings_drive_api_and_ssh(self):
        key_file = self.path / "api-key"
        private_write(key_file, "local-test-key\n")
        settings = Settings(ssh_host="user@server", local_port=19642, remote_port=9642, api_key_file=str(key_file))
        save_settings(settings)
        with patch.dict(os.environ, {}, clear=True):
            os.environ["HERMES_BRIDGE_TOOL_CONFIG"] = str(self.path / "config.json")
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

    def test_url_override_rejects_insecure_remote_or_ambiguous_targets(self):
        for url in ("http://example.com", "http://localhost.evil", "http://user:pass@localhost", "https://example.com/?token=secret", "http://localhost:invalid", "https://example.com/#fragment", "https://example.com\n", "https://example.com:0", "https://example.com\\evil"):
            with self.subTest(url=url), patch.dict(os.environ, {"HERMES_API_URL": url, "HERMES_API_KEY": "secret"}), self.assertRaises(ValueError):
                connection_settings()

    def test_https_and_reverse_proxy_prefix_are_supported(self):
        for url in ("https://hermes.example.com/prefix", "http://127.0.0.1:18642/prefix", "https://[::1]:18642"):
            with self.subTest(url=url), patch.dict(os.environ, {"HERMES_API_URL": url + "/", "HERMES_API_KEY": "secret"}):
                self.assertEqual(connection_settings(), (url, "secret"))
        self.assertEqual(endpoint_settings("https://example.com"), "https://example.com")

    def test_direct_gateway_uses_saved_url_and_needs_no_forward(self):
        key = self.path / "key"
        key.write_text("saved-key")
        settings = Settings(gateway_url="https://gateway.example.com/hermes", api_key_file=str(key))
        save_settings(settings)
        self.assertEqual(connection_settings(), (settings.gateway_url, "saved-key"))
        self.assertEqual(settings.ssh_forwards(), [])
        with self.assertRaisesRegex(ValueError, "No SSH forwards"):
            settings.ssh_command()

    def test_webui_private_headers_and_separate_ssh_forward(self):
        auth = self.path / "webui-auth.json"
        auth.write_text(json.dumps({"Cookie": "session=test-secret", "CF-Access-Client-Secret": "proxy-secret"}))
        key = self.path / "key"
        key.write_text("gateway-key")
        settings = Settings(webui_url="http://127.0.0.1:18787", webui_ssh=True, webui_auth_file=str(auth), api_key_file=str(key))
        save_settings(settings)
        url, headers = webui_connection_settings()
        self.assertEqual(url, settings.webui_url)
        self.assertEqual(headers["Cookie"], "session=test-secret")
        self.assertNotIn("test-secret", (self.path / "config.json").read_text())
        self.assertIn("127.0.0.1:18787:127.0.0.1:8787", settings.ssh_command())
        self.assertIn("127.0.0.1:18642:127.0.0.1:8642", settings.ssh_command())
        with self.assertRaisesRegex(ValueError, "different local ports"):
            Settings(webui_ssh=True, webui_local_port=18642).validate()
        with self.assertRaisesRegex(ValueError, "loopback URL"):
            Settings(webui_ssh=True, webui_url="https://example.com").validate()

    def test_webui_only_ssh_skips_unconfigured_gateway_forward(self):
        settings = Settings(webui_ssh=True, api_key_file=str(self.path / "missing-key"))
        self.assertEqual(settings.ssh_forwards(), ["127.0.0.1:18787:127.0.0.1:8787"])

    def test_webui_missing_or_invalid_auth_fails_without_echoing_secrets(self):
        with self.assertRaisesRegex(ValueError, "WebUI is not configured"):
            webui_connection_settings()
        auth = self.path / "auth.json"
        save_settings(Settings(webui_url="https://webui.example.com", webui_auth_file=str(auth)))
        with self.assertRaisesRegex(ValueError, "Cannot read WebUI"):
            webui_connection_settings()
        for data in ([], {"Cookie": "secret\nInjected: yes"}, {"Host": "secret"}, {"Cookie": 123}, {"Cookie": "secret", "cookie": "duplicate"}):
            auth.write_text(json.dumps(data))
            with self.subTest(data=data), self.assertRaises(ValueError) as error:
                webui_connection_settings()
            self.assertNotIn("secret", str(error.exception))
        auth.write_text("{}")
        self.assertEqual(webui_connection_settings(), ("https://webui.example.com", {}))

    def test_save_preserves_companion_and_future_backend_metadata(self):
        path = self.path / "config.json"
        path.write_text(json.dumps({"appearance": {"show_text": False}, "native_mcp_command": ["hermes", "mcp", "serve"]}))
        save_settings(Settings(ssh_host="server"))
        data = json.loads(path.read_text())
        self.assertEqual(data["appearance"], {"show_text": False})
        self.assertEqual(data["native_mcp_command"], ["hermes", "mcp", "serve"])
        self.assertEqual(load_settings().ssh_host, "server")

    def test_configuration_does_not_contain_secret(self):
        save_settings(Settings())
        data = json.loads((self.path / "config.json").read_text())
        self.assertNotIn("api_key", data)
        self.assertEqual(data["api_key_file"], "~/.config/hermes-bridge-tool/api-key")


if __name__ == "__main__":
    unittest.main()
