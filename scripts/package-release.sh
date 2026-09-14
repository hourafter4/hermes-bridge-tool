#!/bin/sh
# Build a universal macOS download without requiring a source checkout to install.
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "$(uname -s)" != Darwin ]; then
  printf '%s\n' 'Build the macOS release archive on macOS with Xcode command-line tools.' >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'Install uv before building releases: https://docs.astral.sh/uv/getting-started/installation/' >&2
  exit 1
fi
if [ ! -f "$repo_dir/scripts/install-release.command" ]; then
  printf '%s\n' 'Missing scripts/install-release.command.' >&2
  exit 1
fi
release_work=$(mktemp -d "${TMPDIR:-/tmp}/hermes-bridge-tool-release.XXXXXX")
trap 'rm -rf "$release_work"' EXIT HUP INT TERM
uv lock --project "$repo_dir" --check
uv export --project "$repo_dir" --locked --no-dev --no-emit-project --no-annotate --no-header \
  --format requirements-txt --output-file "$release_work/runtime-requirements.txt" >/dev/null
uv build "$repo_dir" --out-dir "$release_work/python"
"$repo_dir/scripts/build-macos.sh"
set -- "$release_work/python"/hermes_bridge_tool-*.whl
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  printf '%s\n' 'Expected exactly one freshly built Hermes Bridge Tool wheel.' >&2
  exit 1
fi
stage="$release_work/Hermes Bridge Tool"
mkdir -p "$stage"
cp "$1" "$stage/"
cp "$release_work/runtime-requirements.txt" "$repo_dir/scripts/install-cli.sh" "$stage/"
# Replace only generated Python distributions, avoiding stale versions in uploads.
for old_artifact in "$repo_dir/dist"/hermes_bridge_tool-*.whl "$repo_dir/dist"/hermes_bridge_tool-*.tar.gz; do
  if [ -f "$old_artifact" ]; then rm -f "$old_artifact"; fi
done
cp "$release_work/python/"* "$repo_dir/dist/"
ditto --norsrc --noextattr "$repo_dir/dist/Hermes Bridge Tool.app" "$stage/Hermes Bridge Tool.app"
cp "$repo_dir/scripts/install-release.command" "$stage/Install.command"
chmod +x "$stage/Install.command"
cat > "$stage/README-install.txt" <<'TXT'
Hermes Bridge Tool for macOS 13 or newer
Universal app: Apple silicon and Intel

1. Extract the complete ZIP before installing.
2. Open Install.command and follow its Terminal instructions.
3. Open Hermes Bridge Tool from your Applications folder to connect.

The installer uses the included Python wheel and copies the menu bar app to
~/Applications. Python dependencies are pinned to the tested lockfile and their
hashes are checked before the CLI launcher is replaced. Downloads may need internet.
Existing connection settings are preserved. No source checkout is needed.
After upgrading, run hermes-bridge-tool register both (or codex / claude),
then restart the coding clients so they use the updated installation.

Check SIGNING-STATUS.txt for this build's actual Apple signing/notarization status.
macOS may require approval under System Settings > Privacy & Security.
Verify GitHub artifact provenance before executing an installer:
  gh attestation verify hermes-bridge-tool-macos-universal.zip --repo hourafter4/hermes-bridge-tool
Checksums alone cannot identify a publisher.

Usage and connection setup:
https://github.com/hourafter4/hermes-bridge-tool#readme

SHA256SUMS lists the included installer, wheel, documentation, and app files.
TXT
if [ -n "${HERMES_NOTARY_PROFILE:-}" ]; then
  printf '%s\n' 'Developer ID signed and Apple-notarized; ticket stapled and validated.' > "$stage/SIGNING-STATUS.txt"
elif [ -n "${HERMES_SIGNING_IDENTITY:-}" ]; then
  printf '%s\n' 'Developer ID signed, NOT Apple-notarized.' > "$stage/SIGNING-STATUS.txt"
else
  printf '%s\n' 'Ad hoc signed. NO verified Apple publisher identity. NOT Apple-notarized.' > "$stage/SIGNING-STATUS.txt"
fi
cli_stage="$release_work/hermes-bridge-tool-cli"
mkdir -p "$cli_stage"
cp "$1" "$cli_stage/"
cp "$release_work/runtime-requirements.txt" "$repo_dir/scripts/install-cli.sh" "$cli_stage/"
cp "$repo_dir/scripts/install-release.command" "$cli_stage/install.sh"
chmod +x "$cli_stage/install.sh"
cat > "$cli_stage/README-install.txt" <<'TXT'
Hermes Bridge Tool CLI

Extract this archive, open a terminal in this folder, and run:

    sh install.sh

The installer uses the included wheel. It offers to install uv if necessary;
uv manages the Python runtime. Dependencies are pinned to the tested lockfile
and their hashes are verified. Downloads may require an internet connection.
Existing connection settings are preserved. No Git or source checkout is needed.
After upgrading, run hermes-bridge-tool register both (or codex / claude),
then restart the coding clients so they use the updated installation.

Connection setup and usage:
https://github.com/hourafter4/hermes-bridge-tool#readme

Verify provenance before running the installer:
  gh attestation verify hermes-bridge-tool-cli.tar.gz --repo hourafter4/hermes-bridge-tool
Checksums alone cannot identify a publisher.

For the macOS menu bar app, download the macOS universal ZIP instead.
SHA256SUMS lists the installer, wheel, and documentation included here.
TXT
# Hash owned staging files with paths relative to the extracted folder. Python
# handles spaces in app bundle filenames without shell parsing or external tools.
uv run --project "$repo_dir" --locked python - "$stage" "$cli_stage" <<'PY'
from hashlib import sha256
from pathlib import Path
import sys
for argument in sys.argv[1:]:
    stage = Path(argument)
    files = sorted(path for path in stage.rglob("*") if path.is_file())
    manifest = "".join(f"{sha256(path.read_bytes()).hexdigest()}  {path.relative_to(stage).as_posix()}\n" for path in files)
    (stage / "SHA256SUMS").write_text(manifest, encoding="utf-8")
PY
archive="$repo_dir/dist/hermes-bridge-tool-macos-universal.zip"
# Replace only this generated archive; ditto otherwise can update stale entries.
rm -f "$archive"
ditto -c -k --norsrc --noextattr --keepParent "$stage" "$archive"
cli_archive="$repo_dir/dist/hermes-bridge-tool-cli.tar.gz"
COPYFILE_DISABLE=1 tar -czf "$cli_archive" -C "$release_work" hermes-bridge-tool-cli
printf 'Packaged %s\n' "$archive" "$cli_archive"
