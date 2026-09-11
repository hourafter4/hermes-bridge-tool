# Build and publish releases

Releases provide installation without a checkout or Xcode. The GitHub workflow
builds a draft from a version tag; publish it after reviewing the checks and assets.

## Assets

| Download | Contents and use |
| --- | --- |
| `hermes-bridge-tool-macos-universal.zip` | Apple Silicon + Intel app, CLI wheel, `Install.command`, instructions, internal checksums |
| `hermes-bridge-tool-cli.tar.gz` | CLI wheel, `install.sh`, instructions, internal checksums; Linux/macOS |
| `hermes_bridge_tool-VERSION-py3-none-any.whl` | Python package for users who already have uv/pip |
| `hermes_bridge_tool-VERSION.tar.gz` | Python source distribution |
| `SHA256SUMS` | SHA-256 hashes of all downloadable archives and the wheel |

The bundled installer installs `uv` only with consent, uses Python 3.12, and
installs into the user's account. It preserves credentials and settings and does
not automatically reconfigure or restart the remote Gateway. Both bundles need
internet access to obtain Python/dependencies when they are not cached.

The macOS app targets macOS 13 and later. Both architectures are compiled and
combined with `lipo`; the build checks the resulting slices and executes its
self-test on the build host. Intel execution still needs an Intel machine for
runtime verification. The app is ad hoc signed, not Developer ID signed or
notarized. Apple signing/notarization requires the maintainer's Developer ID
credentials; the workflow does not claim or attempt it.

## Prepare a version

Keep the version consistent in `pyproject.toml`,
`src/hermes_bridge_tool/__init__.py`, both plugin manifests, and
`macos/Info.plist`. Increment `CFBundleVersion` for a new app build. Run `uv lock`
after changing package metadata and update `CHANGELOG.md` and
`docs/RELEASE-NOTES.md`, including versioned wheel and documentation links.

Do not commit credentials, local settings, or build artifacts.

## Verify and build locally

On a Mac with Xcode Command Line Tools and uv:

```sh
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
sh tests/test_install.sh
sh tests/test_release_install.sh
./scripts/package-release.sh
```

Inspect the archives under `dist/`. The package script produces both installer
bundles and the Python distributions. The release workflow additionally creates
the outer `SHA256SUMS` download.

Try the installer with `--dry-run` from an extracted bundle before installation.
The installer checks its bundled payload checksums, but checksums are not a
substitute for trusting the release's source. A source build remains available
through `./install.sh`.

## Create a draft on GitHub

Push the reviewed commit, then a new tag matching the package version:

```sh
git push origin main
git tag v0.1.0
git push origin v0.1.0
```

Tag pushes run `.github/workflows/release.yml`. It validates every package version,
runs the Python and installer tests, builds the universal app and bundles, and
creates a draft release with notes and downloads. The separate Checks workflow
also tests macOS and Linux.

A failed release run can be rerun from Actions. Manual workflow dispatch must
select the version tag, not `main`. Repeating a run may update an existing draft;
it refuses to overwrite published release assets. Do not move a published tag.

## Publish the reviewed draft

After the workflows pass, inspect the draft and compare downloaded assets with
`SHA256SUMS`, then publish:

```sh
gh release view v0.1.0
gh release edit v0.1.0 --draft=false --latest
```

The README's latest-download links then point to this release. Each release is
self-contained; no PyPI publishing or plugin marketplace is required.

## Repository presentation

Use `assets/brand/social-card.png` as the GitHub social preview. Suggested topics:
`hermes-agent`, `mcp`, `codex`, `claude-code`, `macos`, `ssh`.
Logo sources and regeneration instructions are in [BRAND.md](BRAND.md).
