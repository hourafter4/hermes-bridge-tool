# Codex and Claude Code

The repository is both a Python project and a harness plugin. The two manifests
launch the same MCP server through `scripts/hermes-mcp`. The optional macOS menu
bar app manages the SSH connection; each harness starts its own MCP process.

First configure the connection using the [main setup instructions](../README.md).
Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and run
`uv sync` once in this checkout so initial dependency installation is complete
before a harness starts the server. Keep the SSH tunnel connected through the
menu bar app or `uv run hermes-bridge tunnel`.

## Codex: use the checkout now

Run from this repository root:

```sh
codex mcp add hermes -- "$PWD/scripts/hermes-mcp"
```

Restart Codex or start a new session, then ask it to call `hermes_check`.
The registration uses an absolute checkout path. Register it again if you move
this repository. This makes the tools available across your projects.

The repository also includes a native Codex plugin manifest at
`.codex-plugin/plugin.json`. Codex's native `codex plugin add` command installs
from a marketplace; this repository has not registered or published one. A
marketplace publisher can include this entire repository as the plugin package.
Use either a native plugin installation or the direct MCP registration above to
avoid exposing duplicate tools.

## Claude Code: load the local plugin

Run from this repository root:

```sh
claude --plugin-dir "$PWD"
```

This loads the native plugin for that session. For persistent tool registration
across projects, run:

```sh
claude mcp add --transport stdio --scope user hermes -- "$PWD/scripts/hermes-mcp"
```

Use one of these methods per session. A future marketplace package can use the
included `.claude-plugin/plugin.json` without changing the server code.

## Other MCP clients

Set the transport to stdio and the command to the absolute path to
`scripts/hermes-mcp`. For example, replace the path below with your checkout:

```json
{
  "mcpServers": {
    "hermes": {
      "command": "/absolute/path/to/hermes-bridge/scripts/hermes-mcp"
    }
  }
}
```

On macOS and Linux the launcher locates `uv` on PATH or in common installation
locations. It runs `uv run --directory <plugin-root> hermes-bridge mcp`, so neither
the client's current directory nor a previously activated Python environment
matters. Windows users can register `uv run --directory <checkout>
hermes-bridge mcp` directly instead of the POSIX launcher.

## Credentials and connection ownership

All clients read the same private configuration and API key files under
`~/.config/hermes-bridge/`. Credentials stay outside the plugin manifests and
repository. The MCP server does not open an SSH tunnel automatically: connect
using the menu bar app or CLI first. Exiting a harness stops its MCP process;
Hermes continues its remote runs until completion or a cancellation request.

## Packaging details

Both manifests embed their MCP configuration to avoid one client's automatic
`.mcp.json` discovery loading the other client's settings.

Codex uses a relative `cwd` of `.` and launches `/bin/sh ./scripts/hermes-mcp`.
Codex resolves the working directory against the installed plugin root. Its
native MCP configuration does not require plugin-root variable substitution.
See the [official Codex MCP configuration parser](https://github.com/openai/codex/blob/main/codex-rs/codex-mcp/src/plugin_config.rs).

Claude Code substitutes `${CLAUDE_PLUGIN_ROOT}` in the launcher argument, as
described in its [plugin reference](https://code.claude.com/docs/en/plugins-reference#environment-variables).

Package the complete repository, including `pyproject.toml`, `uv.lock`, `src/`,
`scripts/`, and both hidden manifest directories. Exclude `.venv`, credentials,
local build output, and `.git`. The launcher resolves its own directory, so a
plugin cache does not need the original checkout path.

## Smoke test

With the tunnel connected, ask your harness:

> Call hermes_check. Then ask Hermes to reply “Bridge connected” without using
> tools, wait for its result, and show me the reply.

The first call checks the API. Submitting the second call creates an actual
Hermes run and uses the model configured on that server. Inspect the MCP status
in your harness if no Hermes tools appear.
