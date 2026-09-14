# Build and publish releases

Releases provide installation without a checkout or Xcode. The GitHub workflow
builds a draft from a version tag; publish it after reviewing the checks and assets.

## Assets

| Download | Contents and use |
| --- | --- |
| `hermes-bridge-tool-macos-universal.zip` | Apple Silicon + Intel app, CLI wheel, hashed runtime lock, installer, instructions, internal checksums |
| `hermes-bridge-tool-cli.tar.gz` | CLI wheel, hashed runtime lock, installer, instructions, internal checksums; Linux/macOS |
| `hermes_bridge_tool-VERSION-py3-none-any.whl` | Python package for users who already have uv/pip |
| `hermes_bridge_tool-VERSION.tar.gz` | Python source distribution |
| `SHA256SUMS` | SHA-256 hashes of all downloadable archives and the wheel |

The bundled installer bootstraps uv 0.10.8 only with consent from a versioned
HTTPS URL and verifies the installer script's pinned SHA-256 before executing it.
An already installed uv is reused. The installer uses Python 3.12 and exports
runtime dependencies from the same `uv.lock` tested in CI. It creates a private
runtime under `~/.local/share/hermes-bridge-tool/`, checks every package hash with
`uv pip install --require-hashes --only-binary :all:`, and atomically replaces the
`~/.local/bin/hermes-bridge-tool` launcher only after success. `XDG_DATA_HOME`,
`XDG_BIN_HOME`, and `UV_TOOL_BIN_DIR` are respected. No dependency source builds
are allowed; unsupported platforms fail safely when locked wheels are unavailable.

`uv tool install --constraints` pins versions but does not enforce their hashes;
that is why installers use a private venv. Upgrade with the app's **Check for
Updates…**, `hermes-bridge-tool update`, or by rerunning the source or release
installer, not `uv tool upgrade`. Earlier uv tool environments remain
inert, and old private runtimes remain for already-running clients. After closing
all clients, old runtime directories may be removed; keep the one the launcher
symlink references. Harness plugin launches use `uv run --locked`.

The installer preserves credentials and settings and does
not automatically reconfigure or restart the remote Gateway. Both bundles need
internet access to obtain Python/dependencies when they are not cached.

The macOS app targets macOS 13 and later. Both architectures are compiled and
combined with `lipo`; the build checks the resulting slices and executes its
self-test on the build host. Intel execution still needs an Intel machine for
runtime verification. With no signing credentials the app is ad hoc signed, with
no verified Apple publisher identity or notarization. `SIGNING-STATUS.txt` inside
the macOS archive records the actual build mode. Never describe an ad hoc build
as Apple-notarized.

## Built-in updates

The updater discovers stable releases from `hourafter4/hermes-bridge-tool` on
GitHub; drafts and prereleases are excluded. The macOS app checks at most once a
day by default, offers a manual check, and asks before downloading and installing.
Settings displays the app's version and build and allows automatic checks to be
disabled. The CLI supports `update --check [--json]`, interactive `update`, and
`update --yes`. `--no-app` selects the CLI bundle on macOS; Linux uses it
automatically.

Installation uses the selected release tag throughout, so publishing a newer
release between the check and approval does not change the approved download.
GitHub CLI (`gh`) and network access are required to verify the archive's GitHub
attestation before extraction or execution. A missing verifier or failed
verification stops the update. Release discovery does not forward saved bridge
credentials. The verified bundle then runs the existing installer and preserves
local settings and credentials.

The app prompts for a restart after installation. Coding clients require
`hermes-bridge-tool register both` (or the relevant client) and their own MCP
process restart; the updater does not perform those steps or restart Hermes.
Provenance verification remains separate from Apple signing and notarization.

## Optional Apple signing and notarization

For a local build, set `HERMES_SIGNING_IDENTITY` to a valid **Developer ID
Application** identity in the login Keychain. The build signs with a timestamp
and hardened runtime and verifies the signature. To notarize, also set
`HERMES_NOTARY_PROFILE` to credentials previously stored with `xcrun notarytool
store-credentials`. The build submits the complete app to Apple, requires an
accepted result, staples its ticket, and validates Gatekeeper acceptance.
Notarization cannot run without a signing identity; failures stop packaging.

For GitHub builds, optional secrets are `MACOS_CERTIFICATE_P12` (base64 P12),
`MACOS_CERTIFICATE_PASSWORD`, and `MACOS_SIGNING_IDENTITY`. For notarization,
also supply `APPLE_ID`, `APPLE_TEAM_ID`, and `APPLE_APP_PASSWORD` (an app-specific
password). Credentials enter a temporary Keychain deleted in an always-run cleanup
step. No credentials are required for the default ad hoc release. The maintainer
must obtain and supply valid Apple Developer credentials to enable these paths.

See [Apple's notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow)
for the signing account requirements.

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
sh tests/test_dependency_hashes.sh
./scripts/package-release.sh
```

Inspect the archives under `dist/`. The package script produces both installer
bundles and the Python distributions. The release workflow additionally creates
the outer `SHA256SUMS` download.

Try the installer with `--dry-run` from an extracted bundle before installation.
Before extraction or execution, verify the downloaded archive's GitHub provenance:

```sh
gh attestation verify hermes-bridge-tool-macos-universal.zip --repo hourafter4/hermes-bridge-tool
gh attestation verify hermes-bridge-tool-cli.tar.gz --repo hourafter4/hermes-bridge-tool
```

Inspect the verifier's workflow and source commit/tag against the intended release.
The workflow attests all exact downloadable assets, including `SHA256SUMS`, after
building in a separate job. The build job only has repository read permission;
the publish job has the content, attestation, and OIDC permissions it needs and
never executes downloaded build output. Actions are pinned to verified upstream
commit SHAs; the uv version is fixed. Attestations establish build provenance,
not that source code or dependencies are safe. Users need GitHub CLI and network
access for verification; this is deliberately done **before** running the bundled
installer, which cannot authenticate itself. Checksums alone are not publisher
identity. A source build remains available through `./install.sh`.

See [GitHub artifact attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations)
and [uv hash-checking options](https://docs.astral.sh/uv/reference/cli/#uv-pip-install)
for the verification mechanisms.

## Create a draft on GitHub

Push the reviewed commit, then a new tag matching the package version:

```sh
git push origin main
git tag v0.2.1
git push origin v0.2.1
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
gh release view v0.2.1
gh release edit v0.2.1 --draft=false --latest
```

The README's latest-download links then point to this release. Each release is
self-contained; no PyPI publishing or plugin marketplace is required.

## Repository presentation

Use `assets/brand/social-card.png` as the GitHub social preview. Suggested topics:
`hermes-agent`, `mcp`, `codex`, `claude-code`, `macos`, `ssh`.
Logo sources and regeneration instructions are in [BRAND.md](BRAND.md).
