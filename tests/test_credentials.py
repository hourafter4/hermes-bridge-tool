import ctypes
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from hermes_bridge_tool import credentials


class CredentialsTests(unittest.TestCase):
    def test_file_round_trip_is_private_and_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "credential"
            credentials.store_secret("webui", '{"Cookie":"session=fake"}', backend="file", file_path=path)
            self.assertEqual(credentials.load_secret("webui", backend="file", file_path=path), '{"Cookie":"session=fake"}')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            credentials.store_secret("webui", "replacement", backend="file", file_path=path)
            self.assertEqual(path.read_text(), "replacement")
            credentials.delete_secret("webui", backend="file", file_path=path)
            credentials.delete_secret("webui", backend="file", file_path=path)
            self.assertFalse(path.exists())

    def test_keychain_failure_never_writes_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credential"
            with patch.object(credentials, "_Keychain", side_effect=credentials.KeychainUnavailable("locked")):
                with self.assertRaises(credentials.KeychainUnavailable):
                    credentials.store_secret("gateway", "test-secret", file_path=path)
            self.assertFalse(path.exists())

    def test_keychain_uses_framework_wrapper_not_subprocess(self):
        with patch.object(credentials, "_Keychain") as backend, patch("subprocess.run") as process:
            credentials.store_secret("gateway", "synthetic-secret")
            backend.return_value.operate.assert_called_once_with("store", "gateway", "synthetic-secret")
            backend.return_value.operate.return_value = "synthetic-secret"
            self.assertEqual(credentials.load_secret("gateway"), "synthetic-secret")
            credentials.delete_secret("gateway")
            backend.return_value.operate.assert_called_with("delete", "gateway")
            process.assert_not_called()

    def test_non_mac_keychain_and_invalid_inputs(self):
        with patch.object(credentials.sys, "platform", "linux"):
            with self.assertRaises(credentials.KeychainUnavailable):
                credentials._Keychain()
        for options in ({"backend": "automatic"}, {"backend": "file"}):
            with self.assertRaises(ValueError):
                credentials.store_secret("gateway", "synthetic-secret", **options)
        with self.assertRaises(ValueError):
            credentials.store_secret("bad\naccount", "synthetic-secret")

    @unittest.skipUnless(sys.platform == "darwin", "CoreFoundation is macOS only")
    def test_real_corefoundation_marshalling_without_accessing_keychain(self):
        backend = credentials._Keychain()
        backend.cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        backend.cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        observed = []

        def update(query, updates):
            data = backend.cf.CFDictionaryGetValue(updates, backend.symbol("kSecValueData"))
            observed.append(ctypes.string_at(backend.cf.CFDataGetBytePtr(data), backend.cf.CFDataGetLength(data)))
            return -25300

        def copy(query, output):
            raw = b"synthetic-retrieved"
            output._obj.value = backend.cf.CFDataCreate(None, ctypes.c_char_p(raw), len(raw))
            return 0

        # Replace every Security API operation; only CoreFoundation memory helpers
        # run for real. This cannot read, write, or prompt for any Keychain item.
        backend.sec.SecItemUpdate = update
        backend.sec.SecItemAdd = lambda query, output: 0
        backend.sec.SecItemCopyMatching = copy
        backend.sec.SecItemDelete = lambda query: -25300
        backend.operate("store", "test-account", "synthetic-value")
        self.assertEqual(observed, [b"synthetic-value"])
        self.assertEqual(backend.operate("load", "test-account"), "synthetic-retrieved")
        backend.operate("delete", "test-account")

    def test_missing_file_error_does_not_expose_path_or_secret(self):
        with self.assertRaisesRegex(ValueError, "Cannot read the configured credential file"):
            credentials.load_secret("gateway", backend="file", file_path="/nonexistent/synthetic-secret")


if __name__ == "__main__":
    unittest.main()
