"""Install published, provenance-verified releases without changing connections."""

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile

import httpx

REPOSITORY = "hourafter4/hermes-bridge-tool"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
MAX_DOWNLOAD = 256 * 1024 * 1024
MAX_EXTRACTED = 1024 * 1024 * 1024
MAX_FILES = 20000


def version_tuple(version: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version)
    if not match:
        raise ValueError(f"Unsupported version {version!r}; expected a stable version such as 0.2.1.")
    return tuple(int(part) for part in match.groups())


@dataclass(frozen=True)
class Release:
    tag: str
    version: str
    assets: list

    def report(self, current_version: str) -> dict:
        return {
            "current_version": current_version,
            "latest_version": self.version,
            "update_available": version_tuple(self.version) > version_tuple(current_version),
            "tag": self.tag,
            "release_url": f"{RELEASES_URL}/tag/{self.tag}",
        }


def get_release(tag: str | None = None) -> Release | None:
    if tag is not None:
        version_tuple(tag)
    endpoint = f"tags/{tag}" if tag else "latest"
    try:
        response = httpx.get(
            f"{API_URL}/{endpoint}", timeout=20,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"},
        )
        if response.status_code == 404 and tag is None:
            return None
        if response.status_code in (403, 429):
            raise ValueError("GitHub release checks are rate limited or unavailable. Try again later.")
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as error:
        raise ValueError("Could not check GitHub releases. Check your internet connection and try again.") from error
    if not isinstance(data, dict) or data.get("draft") is not False or data.get("prerelease") is not False or not data.get("published_at"):
        raise ValueError("GitHub did not return a published stable release; no update was installed.")
    selected_tag = data.get("tag_name")
    if not isinstance(selected_tag, str) or (tag is not None and selected_tag != tag):
        raise ValueError("GitHub returned an unexpected release tag; no update was installed.")
    version_tuple(selected_tag)
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise ValueError("The release asset list is invalid. Try again after the release is repaired.")
    return Release(selected_tag, selected_tag.removeprefix("v"), assets)


def check(current_version: str, tag: str | None = None) -> tuple[dict, Release | None]:
    version_tuple(current_version)
    release = get_release(tag)
    if release:
        return release.report(current_version), release
    return {"current_version": current_version, "latest_version": None,
            "update_available": False, "tag": None, "release_url": RELEASES_URL}, None


def _github_cli() -> str:
    command = shutil.which("gh")
    if command:
        return command
    for candidate in (Path("/opt/homebrew/bin/gh"), Path("/usr/local/bin/gh"), Path.home() / ".local/bin/gh"):
        if candidate.is_file() and candidate.stat().st_mode & 0o111:
            return str(candidate)
    raise ValueError("Install GitHub CLI (gh) from https://cli.github.com/ to verify release provenance, then retry the update.")


def _download(url: str, target: Path) -> None:
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=60) as response:
            response.raise_for_status()
            length = response.headers.get("content-length")
            if length and int(length) > MAX_DOWNLOAD:
                raise ValueError("The release download exceeds the supported size limit.")
            downloaded = 0
            with target.open("xb") as output:
                for chunk in response.iter_bytes():
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD:
                        raise ValueError("The release download exceeds the supported size limit.")
                    output.write(chunk)
    except httpx.HTTPError as error:
        raise ValueError("Could not download the release. Check your internet connection and retry.") from error


def _member_path(name: str, root: str, destination: Path) -> Path:
    path = PurePosixPath(name)
    if (not name or "\\" in name or "\x00" in name or path.is_absolute()
            or any(part in (".", "..") for part in name.rstrip("/").split("/"))
            or not path.parts or path.parts[0] != root):
        raise ValueError("The release archive contains an unsafe path.")
    return destination.joinpath(*path.parts)


def _extract(archive: Path, destination: Path, root: str) -> None:
    """Copy regular files only; never let archive libraries create links."""
    total = 0
    seen = set()

    def validate(name, size):
        nonlocal total
        target = _member_path(name, root, destination)
        if target in seen:
            raise ValueError("The release archive contains duplicate paths.")
        seen.add(target)
        total += size
        if size < 0 or total > MAX_EXTRACTED or len(seen) > MAX_FILES:
            raise ValueError("The extracted release exceeds the supported size limit.")
        return target

    def copy_file(source, target, mode):
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            shutil.copyfileobj(source, output, length=64 * 1024)
        target.chmod(0o755 if mode & 0o111 else 0o644)

    try:
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    target = validate(member.filename, member.file_size)
                    mode = member.external_attr >> 16
                    if stat.S_IFMT(mode) not in (0, stat.S_IFDIR, stat.S_IFREG):
                        raise ValueError("The release archive contains a link or special file.")
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        with bundle.open(member) as source:
                            copy_file(source, target, mode)
        else:
            with tarfile.open(archive, "r:gz") as bundle:
                for member in bundle:
                    target = validate(member.name, member.size)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif member.isfile() and not member.issparse():
                        with bundle.extractfile(member) as source:
                            copy_file(source, target, member.mode)
                    else:
                        raise ValueError("The release archive contains a link or special file.")
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, RuntimeError) as error:
        raise ValueError("The release archive is damaged or unsupported. Download it again.") from error


def install(release: Release, *, no_app: bool = False) -> None:
    if sys.platform not in ("darwin", "linux"):
        raise ValueError("Release updates support macOS and Linux only.")
    app = sys.platform == "darwin" and not no_app
    name = "hermes-bridge-tool-macos-universal.zip" if app else "hermes-bridge-tool-cli.tar.gz"
    expected_url = f"{RELEASES_URL}/download/{release.tag}/{name}"
    assets = [asset for asset in release.assets if isinstance(asset, dict) and asset.get("name") == name]
    if len(assets) != 1 or assets[0].get("browser_download_url") != expected_url:
        raise ValueError(f"Release {release.tag} is missing the official {name} download. Try again after the release is repaired.")
    gh = _github_cli()
    with tempfile.TemporaryDirectory(prefix="hermes-bridge-update-") as temporary:
        directory = Path(temporary)
        archive = directory / name
        print(f"Downloading {release.tag}…", flush=True)
        _download(expected_url, archive)
        print("Verifying GitHub release provenance…", flush=True)
        verified = subprocess.run([gh, "attestation", "verify", str(archive), "--repo", REPOSITORY,
                                   "--signer-workflow", f"{REPOSITORY}/.github/workflows/release.yml",
                                   "--source-ref", f"refs/tags/{release.tag}"],
                                  capture_output=True, text=True, timeout=120)
        if verified.returncode:
            raise ValueError("Release provenance verification failed; nothing was installed. Update GitHub CLI, run 'gh auth login' if required, and retry. If it persists, report this release to its maintainer.")
        root = "Hermes Bridge Tool" if app else "hermes-bridge-tool-cli"
        _extract(archive, directory / "extracted", root)
        installer = directory / "extracted" / root / ("Install.command" if app else "install.sh")
        if not installer.is_file():
            raise ValueError("The verified release is missing its installer; nothing was installed.")
        command = ["/bin/sh", str(installer), "--yes"]
        if not app:
            command.append("--no-app")
        print(f"Installing {release.tag}…", flush=True)
        result = subprocess.run(command, cwd=installer.parent)
        if result.returncode:
            raise ValueError("The release installer failed. Review the output above and retry after fixing the reported problem.")
    print(f"Updated Hermes Bridge Tool to {release.version}. Restart your coding clients to load the update.")


def run_update(args) -> int:
    if args.json and not args.check:
        raise ValueError("--json requires --check.")
    report, release = check(args.current_version, args.tag)
    if args.check:
        if args.json:
            print(json.dumps(report))
        elif release is None:
            print("No published stable release is available yet.")
        elif report["update_available"]:
            print(f"Hermes Bridge Tool {release.version} is available (installed: {args.current_version}).")
        else:
            print(f"Hermes Bridge Tool {args.current_version} is up to date.")
        return 0
    if not report["update_available"]:
        print(f"No newer stable release is available (installed: {args.current_version}).")
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            raise ValueError("Run update in an interactive terminal to confirm, or pass --yes.")
        answer = input(f"Download and install Hermes Bridge Tool {release.version}? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Update cancelled.")
            return 0
    install(release, no_app=args.no_app)
    return 0
