# Development and maintenance

These notes support maintenance and local inspection. Public contributions follow
the [issue-only contribution policy](../CONTRIBUTING.md); external pull requests
are not accepted.

Hermes Bridge Tool has two small runtimes: Python for MCP tools and installation,
and Swift/AppKit for the macOS menu bar companion. They share configuration and
private file or macOS Keychain credential storage; they do not share a process.

The app, CLI, and MCP tools use a shared managed SSH connection. Its lifetime is
independent of any one client: quitting the app or stopping MCP leaves the tunnel
running, while explicit disconnect affects every local client. Tool discovery
must remain available when a backend is down so agents can call the local
connection diagnostics and recovery tools.

## Development loop

```sh
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
sh tests/test_install.sh
sh tests/test_release_install.sh
sh tests/test_dependency_hashes.sh
uv build
```

On macOS, `./scripts/build-macos.sh` builds the app and runs its self-tests.
Use `uv run --locked hermes-bridge-tool ...` to test checkout changes; a previously installed
CLI is a separate copy. Run `./install.sh --no-setup` to refresh that copy while
preserving existing connection settings.

Tests use mock HTTP APIs, temporary config directories, mocked SSH/service calls,
and real MCP stdio sessions. Installer checks use isolated temporary homes and
mock installation commands. The dependency-hash test additionally builds a real
wheel and exercises uv in a temporary runtime, rejecting altered hashes and
checking that failed upgrades preserve the previous CLI. They do not require real agent credentials or submit
work to a server. Verify compatibility with a new Hermes or WebUI version using a
small, read-only smoke check against an installation you control before claiming
live compatibility.

## Code layout

Python modules below live in `src/hermes_bridge_tool/`.

| Component | Responsibility |
| --- | --- |
| `server.py` | MCP registration, Gateway API tools, and observer tools |
| `webui.py` | Existing WebUI conversation and task endpoints |
| `native.py` | Hermes's existing native messaging MCP interface |
| `setup.py` | Local pairing, connection setup, and coding-client registration |
| `server_setup.py` | Standalone standard-library helper sent over SSH |
| `observer_plugin.py` | Optional server plugin for CLI turn lifecycle observations |
| `config.py` | Shared settings and private local storage |
| `connection.py` | Shared owned SSH transport, HTTP connection checks, and recovery |
| `transport.py` | Private Unix-socket HTTP transport and endpoint identity checks |
| `policy.py` | Fresh local access checks before upstream requests |
| `credentials.py`, `credential_migration.py` | Credential storage and verified migration |
| `runtime_ssh.py`, `runtime_setup.py` | Restricted runtime SSH provisioning |
| `recovery.py` | MCP diagnostics and recovery with backend selection |
| `cli.py` | Command parsing and CLI entry points |
| `macos/main.swift` | Native companion, tunnels, status, and settings |
| `scripts/install-cli.sh` | Hash-enforced runtime installation and atomic launcher replacement |
| `scripts/install-release.command` | Installation from prebuilt release packages |
| `assets/brand/` | Editable identity assets and committed exports |

Keep Python and Swift configuration contracts aligned. Preserve existing keys and
unrelated server settings. Never log credentials, automatically approve agent
actions, or infer task cancellation from a broken connection. Keep WebUI stream
IDs, Gateway run IDs, and observer turn IDs distinct; see [Backends](BACKENDS.md).
Reconnection must reload settings without replaying work, renewing credentials,
approving requests, or restarting server services. Native writes cannot be reset
while busy; new native connections require fresh cursors and approval observations.

Treat missing security policy as monitor-only and malformed policy as locked.
Enforce permissions before transport access, including reads and recovery while
locked. Remote approval responses stay disabled. Private managed SSH sockets
must be the only authenticated HTTP transport for loopback configurations; never
fall back to an unrelated TCP listener. Direct HTTPS retains TLS verification.
Policy is not a sandbox against another process running as the same user; avoid
claims of per-session isolation or automatic secret redaction. Restart old MCP
processes when testing upgrades so tests exercise the new runtime.

Keep changes focused and test behavior that can break a connection or lose state.
Record the relevant verification with the change. Follow the [brand guide](BRAND.md)
for visual changes and [release instructions](RELEASING.md) for packaging.
