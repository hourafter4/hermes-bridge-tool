# Connect your coding harness

## Recommended: installed CLI

The setup wizard detects installed coding clients and offers to register them.
You can also register explicitly:

```sh
hermes-bridge-tool register codex
hermes-bridge-tool register claude
# Or both:
hermes-bridge-tool register both
```

The registration points at the installed CLI's absolute path, so moving your
source checkout does not break the connection. Restart the client to load the
Hermes session and task tools. For SSH backends, connect the tunnel through the menu bar app first. Direct
HTTPS backends do not need it. Ask the agent to call `hermes_backends` to choose
between WebUI, Gateway, native platform MCP, and CLI observation. See
[backend selection](BACKENDS.md) for the routing rules included in tool descriptions.

These commands use the clients' own configuration CLIs. Repeating an identical
registration is supported. If Claude already has a different `hermes-bridge-tool` entry, the
command explains how to remove it before replacing it.

## Native plugins and development checkouts

This repository also contains native plugin manifests for both harnesses. Both
launch the same MCP implementation from the plugin package.

For local Claude Code plugin development:

```sh
uv sync --locked
claude --plugin-dir "$PWD"
```

For a development checkout in Codex:

```sh
codex mcp add hermes-bridge-tool -- "$PWD/scripts/hermes-mcp"
```

This checkout-specific registration needs updating if you move the repository.
The installed CLI route above is intended for everyday use. Choose one route per
harness to avoid exposing duplicate tools.

The native Codex manifest is in `.codex-plugin/plugin.json`; installing it as a
native plugin requires a marketplace. No marketplace is registered or published
automatically. Claude's native manifest is in `.claude-plugin/plugin.json`.

## Other MCP clients

Use stdio transport with the absolute installed `hermes-bridge-tool` executable and
one argument, `mcp`. For example:

```json
{
  "mcpServers": {
    "hermes-bridge-tool": {
      "command": "/absolute/path/to/hermes-bridge-tool",
      "args": ["mcp"]
    }
  }
}
```

Run `command -v hermes-bridge-tool` to find the path. The CLI and menu bar app share
private settings under `~/.config/hermes-bridge-tool`.

## Packaging notes

For native plugin distribution, include the complete repository: `src/`,
`pyproject.toml`, `uv.lock`, `scripts/`, and both hidden manifest directories.
Exclude `.venv`, credentials, `.git`, and build outputs. The launcher finds its
own root and uses uv, including common GUI PATH fallbacks.

Both manifests embed their MCP configuration to avoid cross-client `.mcp.json`
auto-discovery. Codex resolves `cwd: "."` against its installed plugin root;
Claude substitutes `${CLAUDE_PLUGIN_ROOT}` in the launcher path.

See the [Codex MCP guide](https://developers.openai.com/codex/mcp) and
[Claude Code plugin reference](https://code.claude.com/docs/en/plugins-reference).
