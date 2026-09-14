"""Release selection, provenance boundaries, archive safety, and CLI contract."""

import contextlib
import io
import json
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

import httpx

from hermes_bridge_tool import updates
from hermes_bridge_tool.cli import main


def release_data(tag="v0.3.0"):
    return {"tag_name": tag, "draft": False, "prerelease": False,
            "published_at": "2026-09-01T00:00:00Z", "assets": []}


def api_response(data, status=200):
    return httpx.Response(status, json=data, request=httpx.Request("GET", updates.API_URL))


class ReleaseTests(unittest.TestCase):
    def test_numeric_version_order_and_no_downgrade(self):
        for current, latest, available in (("0.9.0", "0.10.0", True), ("1.0.0", "0.9.9", False), ("v1.0.0", "1.0.0", False)):
            self.assertEqual(updates.Release("v" + latest, latest, []).report(current)["update_available"], available)

    def test_nonstable_or_unsafe_versions_are_rejected(self):
        for version in ("1.2.3rc1", "1.2.3-beta", "1.2", "01.2.3", "../1.2.3", "1.2.3\n"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                updates.version_tuple(version)

    def test_pinned_release_uses_exact_tag_endpoint(self):
        with patch.object(updates.httpx, "get", return_value=api_response(release_data())) as get:
            report, release = updates.check("0.2.1", "v0.3.0")
        self.assertTrue(report["update_available"])
        self.assertEqual(release.tag, "v0.3.0")
        self.assertEqual(get.call_args.args[0], updates.API_URL + "/tags/v0.3.0")

    def test_rejects_unpublished_prerelease_and_mismatched_tag(self):
        for change in ({"draft": True}, {"prerelease": True}, {"published_at": None}, {"tag_name": "v0.4.0"}, {"tag_name": "v0.3.0-rc1"}):
            with self.subTest(change=change), patch.object(updates.httpx, "get", return_value=api_response({**release_data(), **change})), self.assertRaises(ValueError):
                updates.get_release("v0.3.0")

    def test_no_published_release_is_a_successful_empty_check(self):
        with patch.object(updates.httpx, "get", return_value=api_response({}, 404)):
            report, release = updates.check("0.2.1")
        self.assertIsNone(release)
        self.assertIsNone(report["latest_version"])
        self.assertFalse(report["update_available"])

    def test_rate_limit_and_network_failures_are_actionable(self):
        with patch.object(updates.httpx, "get", return_value=api_response({}, 403)), self.assertRaisesRegex(ValueError, "Try again later"):
            updates.get_release()
        with patch.object(updates.httpx, "get", side_effect=httpx.ConnectError("offline")), self.assertRaisesRegex(ValueError, "internet connection"):
            updates.get_release()


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.destination = self.directory / "extracted"

    def test_zip_preserves_executable_bits_without_special_permissions(self):
        archive = self.directory / "release.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            entry = zipfile.ZipInfo("release/bin/program")
            entry.external_attr = (stat.S_IFREG | 0o4755) << 16
            bundle.writestr(entry, b"program")
        updates._extract(archive, self.destination, "release")
        target = self.destination / "release/bin/program"
        self.assertEqual(target.read_bytes(), b"program")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)

    def test_zip_rejects_traversal_absolute_and_foreign_roots(self):
        for name in ("release/../../escaped", "/release/file", "other/file", "release\\file", "release/./file"):
            with self.subTest(name=name):
                archive = self.directory / "release.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    bundle.writestr(name, b"unsafe")
                with self.assertRaisesRegex(ValueError, "unsafe path"):
                    updates._extract(archive, self.destination, "release")
        self.assertFalse((self.directory / "escaped").exists())

    def test_zip_symlinks_are_rejected(self):
        archive = self.directory / "release.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            entry = zipfile.ZipInfo("release/link")
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(entry, "../../target")
        with self.assertRaisesRegex(ValueError, "link or special file"):
            updates._extract(archive, self.destination, "release")

    def test_tar_links_and_special_files_are_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE):
            with self.subTest(kind=kind):
                archive = self.directory / "release.tar.gz"
                with tarfile.open(archive, "w:gz") as bundle:
                    entry = tarfile.TarInfo("release/link")
                    entry.type = kind
                    entry.linkname = "../../target"
                    bundle.addfile(entry)
                with self.assertRaisesRegex(ValueError, "link or special file"):
                    updates._extract(archive, self.destination, "release")

    def test_tar_regular_files_are_extracted(self):
        archive = self.directory / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            entry = tarfile.TarInfo("release/install.sh")
            entry.size = 2
            bundle.addfile(entry, io.BytesIO(b"ok"))
        updates._extract(archive, self.destination, "release")
        self.assertEqual((self.destination / "release/install.sh").read_bytes(), b"ok")

    def test_extraction_size_and_count_are_bounded(self):
        archive = self.directory / "release.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("release/file", b"large")
        for limit in ("MAX_EXTRACTED", "MAX_FILES"):
            with self.subTest(limit=limit), patch.object(updates, limit, 0), self.assertRaisesRegex(ValueError, "size limit"):
                updates._extract(archive, self.destination, "release")

    def test_download_size_is_bounded_even_without_content_length(self):
        response = Mock(headers={})
        response.iter_bytes.return_value = [b"abcd", b"efgh"]
        stream = Mock()
        stream.__enter__ = Mock(return_value=response)
        stream.__exit__ = Mock(return_value=False)
        with patch.object(updates.httpx, "stream", return_value=stream), patch.object(updates, "MAX_DOWNLOAD", 5), self.assertRaisesRegex(ValueError, "size limit"):
            updates._download("https://example.invalid", self.directory / "download")


class InstallTests(unittest.TestCase):
    def release(self, app=False):
        name = "hermes-bridge-tool-macos-universal.zip" if app else "hermes-bridge-tool-cli.tar.gz"
        return updates.Release("v0.3.0", "0.3.0", [{"name": name, "browser_download_url": f"{updates.RELEASES_URL}/download/v0.3.0/{name}"}])

    def test_verification_failure_prevents_extraction_or_installation(self):
        with patch.object(updates.sys, "platform", "linux"), patch.object(updates, "_github_cli", return_value="gh"), patch.object(updates, "_download"), patch.object(updates, "_extract") as extract, patch.object(updates.subprocess, "run", return_value=Mock(returncode=1)) as run, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "provenance verification failed"):
                updates.install(self.release())
        extract.assert_not_called()
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--repo") + 1], updates.REPOSITORY)
        self.assertEqual(command[command.index("--source-ref") + 1], "refs/tags/v0.3.0")
        self.assertEqual(command[command.index("--signer-workflow") + 1], updates.REPOSITORY + "/.github/workflows/release.yml")

    def test_missing_gh_prevents_download(self):
        with patch.object(updates.sys, "platform", "linux"), patch.object(updates, "_github_cli", side_effect=ValueError("Install GitHub CLI")), patch.object(updates, "_download") as download, self.assertRaisesRegex(ValueError, "GitHub CLI"):
            updates.install(self.release())
        download.assert_not_called()

    def test_asset_url_must_match_official_repository_and_selected_tag(self):
        release = self.release()
        release.assets[0]["browser_download_url"] = "https://example.invalid/download"
        with patch.object(updates.sys, "platform", "linux"), patch.object(updates, "_download") as download, self.assertRaisesRegex(ValueError, "official"):
            updates.install(release)
        download.assert_not_called()

    def test_install_verifies_then_extracts_then_runs_installer_without_relaunch(self):
        for platform, no_app, app in (("darwin", False, True), ("darwin", True, False), ("linux", False, False)):
            events = []

            def extract(archive, destination, root):
                events.append("extract")
                installer = destination / root / ("Install.command" if app else "install.sh")
                installer.parent.mkdir(parents=True)
                installer.write_text("exit 0\n")

            def run(command, **kwargs):
                events.append("verify" if command[0] == "gh" else "install")
                return Mock(returncode=0)

            with self.subTest(platform=platform, no_app=no_app), patch.object(updates.sys, "platform", platform), patch.object(updates, "_github_cli", return_value="gh"), patch.object(updates, "_download", side_effect=lambda *args: events.append("download")), patch.object(updates, "_extract", side_effect=extract), patch.object(updates.subprocess, "run", side_effect=run) as process, contextlib.redirect_stdout(io.StringIO()):
                updates.install(self.release(app), no_app=no_app)
            self.assertEqual(events, ["download", "verify", "extract", "install"])
            command = process.call_args.args[0]
            self.assertEqual(command[0], "/bin/sh")
            self.assertIn("--yes", command)
            self.assertEqual("--no-app" in command, not app)


class UpdateCLITests(unittest.TestCase):
    def invoke(self, *arguments):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = main(["update", *arguments])
        return result, output.getvalue(), errors.getvalue()

    def test_json_check_never_loads_connection_settings_or_installs(self):
        with patch.object(updates, "get_release", return_value=updates.Release("v0.3.0", "0.3.0", [])), patch.object(updates, "install") as install, patch("hermes_bridge_tool.cli.load_settings", side_effect=AssertionError("must not load settings")):
            code, output, errors = self.invoke("--check", "--json", "--current-version", "0.2.0")
        self.assertEqual(code, 0)
        self.assertEqual(errors, "")
        report = json.loads(output)
        self.assertEqual(set(report), {"current_version", "latest_version", "update_available", "tag", "release_url"})
        self.assertEqual(report["current_version"], "0.2.0")
        install.assert_not_called()

    def test_json_check_failure_is_nonzero_with_actionable_error(self):
        with patch.object(updates, "get_release", side_effect=ValueError("Check your internet connection")):
            code, output, errors = self.invoke("--check", "--json")
        self.assertEqual(code, 1)
        self.assertIn("internet", json.loads(output)["error"])
        self.assertEqual(errors, "")

    def test_cancelled_update_does_not_install(self):
        with patch.object(updates, "get_release", return_value=updates.Release("v0.3.0", "0.3.0", [])), patch.object(updates.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="no"), patch.object(updates, "install") as install:
            code, output, errors = self.invoke("--current-version", "0.2.1")
        self.assertEqual(code, 0)
        self.assertIn("cancelled", output)
        install.assert_not_called()

    def test_unattended_update_requires_confirmation_flag(self):
        with patch.object(updates, "get_release", return_value=updates.Release("v0.3.0", "0.3.0", [])), patch.object(updates.sys.stdin, "isatty", return_value=False), patch.object(updates, "install") as install:
            code, output, errors = self.invoke("--current-version", "0.2.1")
        self.assertEqual(code, 1)
        self.assertIn("--yes", errors)
        install.assert_not_called()

    def test_yes_installs_the_pinned_release_only(self):
        release = updates.Release("v0.3.0", "0.3.0", [])
        with patch.object(updates, "get_release", return_value=release) as get, patch.object(updates, "install") as install:
            code, output, errors = self.invoke("--yes", "--tag", "v0.3.0", "--current-version", "0.2.1", "--no-app")
        self.assertEqual(code, 0)
        get.assert_called_once_with("v0.3.0")
        install.assert_called_once_with(release, no_app=True)

    def test_equal_or_older_release_never_installs(self):
        with patch.object(updates, "get_release", return_value=updates.Release("v0.2.0", "0.2.0", [])), patch.object(updates, "install") as install:
            code, output, errors = self.invoke("--yes", "--current-version", "0.2.1")
        self.assertEqual(code, 0)
        install.assert_not_called()


if __name__ == "__main__":
    unittest.main()
