# Hermes Bridge

Give Codex, Claude Code, and other MCP clients a tool for delegating work to your
remote [Hermes agent](https://hermes-agent.nousresearch.com/).

The optional native macOS menu bar app manages the SSH connection. The Python MCP
server exposes four tools: check connectivity, send instructions, retrieve results,
and request cancellation. Hermes does the work on your server with its configured
tools and permissions.

```text
Codex / Claude Code → local MCP process → SSH tunnel → remote Hermes API
                                           ↑
                                  macOS menu bar app
```

This is an independent companion project, not an official Nous Research product.
It was extracted from Monemetrics; the server and agent remain separate installs.

## Start here

Requires Python 3.10+, [uv](https://docs.astral.sh/uv/getting-started/installation/),
SSH access, and a Hermes gateway with the Runs API enabled. The menu bar app needs
macOS 13+ and Xcode Command Line Tools to build. The CLI/MCP package also works
without the app.

From this checkout:

```sh
uv sync --locked
uv run hermes-bridge configure --host hetzner
```

Use your SSH alias in place of `hetzner`. Enter the existing Hermes API key at the
hidden prompt. [Server and credential setup](docs/SETUP.md) covers enabling the
gateway API for the first time.

On macOS, build and open the companion:

```sh
./scripts/build-macos.sh
open "dist/Hermes Bridge.app"
```

Open **Settings** from its menu, verify the connection details, then choose
**Connect**. It keeps the tunnel open while the app runs. For a terminal-only
connection, run `uv run hermes-bridge tunnel` instead.

Verify the API in another terminal:

```sh
uv run hermes-bridge doctor
```

Then [load the Codex or Claude Code plugin](docs/PLUGINS.md), or register the MCP
server directly using the commands there. Do not register both for the same
harness: they expose the same tools.

## Use it

Ask your coding agent:

> Check the Hermes connection, then ask Hermes to reply “Bridge connected” without
> using tools. Wait for its result and show me the reply.

| Tool | Behavior |
| --- | --- |
| `hermes_check()` | Verify authentication and API capabilities without agent work. |
| `hermes_send(instructions, session_id?, request_id?)` | Submit work and receive a run ID immediately. |
| `hermes_status(run_id)` | Read the run state and final response. |
| `hermes_stop(run_id)` | Request interruption at a safe point. |

Each harness runs its own MCP process. They share connection settings and the
menu bar app's tunnel. The bridge sends instructions as text; have the harness
read local files before sending their contents. Hermes cannot read your laptop's
paths. Omit `session_id` for a new conversation, or reuse an existing Hermes
session ID to continue its transcript.

Save the returned run ID and final output. If submission is uncertain, reuse the
returned `request_id` with identical inputs promptly; a fresh ID can repeat the
work. Hermes's deduplication and completed-run retention are time-limited.
Quitting the app disconnects the tunnel; it does not cancel accepted work.
Cancellation does not undo earlier actions. Resolve pending server-side approvals
in Hermes itself.

## Settings

Both the app and MCP server read `~/.config/hermes-bridge/config.json`:

```json
{
  "ssh_host": "hetzner",
  "local_port": 18642,
  "remote_port": 8642,
  "api_key_file": "~/.config/hermes-bridge/api-key"
}
```

The key is stored separately with file mode `600`, outside the repository. SSH
uses your existing keys, agent, host-key verification, and `~/.ssh/config`.
The app never accepts an unknown server key on your behalf. Establish SSH access
once in a terminal before using **Connect**.

`HERMES_BRIDGE_CONFIG` selects another configuration file. The Python MCP server
also accepts `HERMES_API_URL`, `HERMES_API_KEY_FILE`, and `HERMES_API_KEY` overrides.
URL overrides must be loopback origins; the app uses the ports in the JSON file.
Prefer shared file settings so GUI clients and the app see the same connection.

## Develop

```sh
uv sync --locked
uv run python -m unittest discover -s tests -v
uv build
./scripts/build-macos.sh
```

Tests use a local mock Hermes API and real MCP stdio sessions. Building the macOS
app does not connect to a server. The app is a local development build, not a
notarized public release. Nothing in this repository is published automatically.

The bridge requires the APIs described in the [Hermes API reference](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/).
Use `doctor` against your installed gateway to check compatibility.

MIT licensed; see [LICENSE](LICENSE).
