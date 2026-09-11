# Publish your copy

The repository is initialized on `main` with local commits. No remote is assumed.
Create an empty repository on your Git host, then connect and push it:

```sh
git remote add origin <your-repository-URL>
git push -u origin main
```

If `origin` already exists, inspect it with `git remote -v` before changing it.
No credentials, local settings, or build artifacts should be committed.

## Repository presentation

Use **Hermes Bridge** as the display name and a description such as:

> Bring your remote Hermes agent into Codex and Claude Code. MCP tools, SSH pairing, and a macOS menu bar companion.

Upload `assets/brand/social-card.png` as the GitHub social preview. Suggested
topics: `hermes-agent`, `mcp`, `codex`, `claude-code`, `macos`, `ssh`.
The README already references the checked-in banner. Identity sources are in
`assets/brand` and documented in [BRAND.md](BRAND.md).

## Build a release

```sh
uv sync --locked
uv run python -m unittest discover -s tests -v
sh tests/test_install.sh
uv build
./scripts/build-macos.sh
```

Keep the version consistent in `pyproject.toml`, `src/hermes_bridge/__init__.py`,
both plugin manifests, and `macos/Info.plist`. Refresh `uv.lock` when changing
package metadata. Update `CHANGELOG.md`.

After pushing to GitHub, a version tag matching the package version triggers the
release workflow. For version 0.1.0:

```sh
git tag v0.1.0
git push origin v0.1.0
```

The workflow runs checks and creates a **draft** release with the wheel, source
archive, Apple Silicon app archive, and SHA-256 checksums. Review it before
publishing. The app archive still needs the CLI companion installed. The source
installer builds for the architecture of the local Mac; the hosted release job
currently builds Apple Silicon only.

The app is ad hoc signed for local development. Developer ID signing and Apple
notarization are separate steps requiring your own Apple Developer credentials.
Do not describe the existing build as notarized. The GitHub workflow does not
publish to PyPI or install a plugin marketplace.
