# Contributing

Hermes Bridge Tool has two small runtimes: Python for the MCP bridge and installation
wizard, and Swift/AppKit for the macOS menu bar companion. They share a JSON
configuration file and a private key file; they do not share a process.

## Development loop

```sh
uv sync --locked
uv run python -m unittest discover -s tests -v
sh tests/test_install.sh
uv build
```

On macOS, `./scripts/build-macos.sh` builds the app and runs its self-tests.
Use `uv run hermes-bridge-tool ...` to test checkout changes; a previously installed
CLI is a separate copy. Run `./install.sh --no-setup` to refresh that copy.

The tests use a mock Hermes API, temporary config directories, mocked SSH/service
calls, and real MCP stdio sessions. They do not connect to a real agent or require
credentials. Run a small live smoke test against your own gateway before claiming
compatibility with a new Hermes version.

## Where changes belong

- `server.py`: MCP tool definitions and API requests.
- `setup.py`: local pairing and harness registration.
- `server_setup.py`: standalone stdlib helper sent over SSH.
- `config.py`: shared settings and private local storage.
- `macos/main.swift`: native companion, tunnel ownership, status, and settings.
- `assets/brand`: editable identity assets and committed exports.

Keep the Python and Swift configuration contracts aligned. Preserve existing
keys and unrelated server settings. Never log credentials, automatically approve
agent actions, or infer task cancellation from a broken connection.

Keep changes focused, add tests for behavior that can break a connection or lose
state, and describe what you verified in the pull request. Follow
[the brand guide](docs/BRAND.md) for visual changes.
