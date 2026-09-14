"""Migration verifies destination storage before committing settings or deletion."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hermes_bridge_tool import credential_migration, credentials
from hermes_bridge_tool.config import load_settings, read_credential


class CredentialMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config_path = self.root / "config.json"
        self.key = self.root / "key"
        self.cookie = self.root / "cookie.json"
        self.key.write_text("dummy-gateway-key\n")
        self.cookie.write_text('{"Cookie":"dummy-cookie"}\n')
        self.original = {"api_key_file": str(self.key), "webui_auth_file": str(self.cookie),
                         "custom": "preserved", "security": {"mode": "monitor", "locked": True}}
        self.config_path.write_text(json.dumps(self.original))
        self.env = patch.dict(os.environ, {"HERMES_BRIDGE_TOOL_CONFIG": str(self.config_path)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.keychain = {}
        self.operations = []
        self.fail_action = None
        self.fail_backend = None
        self.corrupt_read = False

        def operate(action, account, secret=None):
            self.operations.append((action, account))
            if action == self.fail_action and account.endswith(":" + self.fail_backend):
                raise credentials.KeychainUnavailable("Simulated Keychain failure")
            if action == "store":
                self.keychain[account] = secret
            elif action == "load":
                return "corrupt" if self.corrupt_read else self.keychain[account]
            else:
                raise AssertionError("Unexpected Keychain operation")

        self.mock_keychain = patch.object(credentials, "_Keychain")
        self.mock_keychain.start().return_value.operate.side_effect = operate
        self.addCleanup(self.mock_keychain.stop)

    def assert_original_configuration_and_files(self):
        self.assertEqual(json.loads(self.config_path.read_text()), self.original)
        self.assertEqual(self.key.read_text(), "dummy-gateway-key\n")
        self.assertEqual(self.cookie.read_text(), '{"Cookie":"dummy-cookie"}\n')

    def test_verified_keychain_roundtrip_preserves_policy_and_removes_files_only_when_requested(self):
        report = credential_migration.migrate("keychain", remove_files=True)
        self.assertEqual(report["migrated"], ["gateway", "webui"])
        self.assertEqual(report["removed_plaintext_files"], 2)
        self.assertFalse(self.key.exists())
        self.assertFalse(self.cookie.exists())
        settings = load_settings()
        self.assertEqual(settings.gateway_credential_store, "keychain")
        self.assertEqual(settings.webui_credential_store, "keychain")
        self.assertEqual(read_credential(settings, "gateway"), "dummy-gateway-key\n")
        self.assertEqual(read_credential(settings, "webui"), '{"Cookie":"dummy-cookie"}\n')
        report = credential_migration.migrate("file")
        self.assertEqual(report["removed_plaintext_files"], 0)
        settings = load_settings()
        self.assertEqual(settings.gateway_credential_store, "file")
        self.assertEqual(settings.webui_credential_store, "file")
        for path in (self.config_path, self.key, self.cookie):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        data = json.loads(self.config_path.read_text())
        self.assertEqual(data["security"], self.original["security"])
        self.assertEqual(data["custom"], "preserved")

    def test_keychain_migration_keeps_original_files_by_default(self):
        report = credential_migration.migrate("keychain")
        self.assertEqual(report["removed_plaintext_files"], 0)
        self.assertTrue(self.key.exists())
        self.assertTrue(self.cookie.exists())

    def test_verification_mismatch_preserves_current_settings_and_plaintext_files(self):
        self.corrupt_read = True
        with self.assertRaisesRegex(ValueError, "verified"):
            credential_migration.migrate("keychain", remove_files=True)
        self.assert_original_configuration_and_files()

    def test_second_backend_failure_does_not_commit_first_backend_or_delete_files(self):
        self.fail_action = "store"
        self.fail_backend = "webui"
        with self.assertRaisesRegex(ValueError, "Keychain failure"):
            credential_migration.migrate("keychain", remove_files=True)
        self.assert_original_configuration_and_files()

    def test_configuration_commit_failure_keeps_both_source_files(self):
        with patch.object(credential_migration, "save_settings", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError):
                credential_migration.migrate("keychain", remove_files=True)
        self.assert_original_configuration_and_files()

    def test_migration_refuses_symlinks_without_writing_keychain(self):
        real_key = self.root / "real-key"
        self.key.rename(real_key)
        self.key.symlink_to(real_key)
        with self.assertRaisesRegex(ValueError, "symbolic links"):
            credential_migration.migrate("keychain", remove_files=True)
        self.assertEqual(self.operations, [])
        self.assertEqual(real_key.read_text(), "dummy-gateway-key\n")
        self.assertEqual(json.loads(self.config_path.read_text()), self.original)

    def test_invalid_remove_option_and_empty_configuration_do_not_touch_keychain(self):
        with self.assertRaises(ValueError):
            credential_migration.migrate("file", remove_files=True)
        self.key.unlink()
        self.cookie.unlink()
        result = credential_migration.migrate("keychain")
        self.assertEqual(result["migrated"], [])
        self.assertEqual(self.operations, [])


if __name__ == "__main__":
    unittest.main()
