# Setup

## Guided setup

From a clone of this repository:

```sh
./install.sh --setup
```

The installer copies the CLI into an isolated per-user environment. On macOS it
also builds and installs `~/Applications/Hermes Bridge Tool.app` when Swift is
available. It needs no `sudo` on your laptop. If your shell cannot find the new
CLI, use the absolute path printed by the installer or run `uv tool update-shell`
and open a new terminal.

The wizard asks for:

1. **SSH host** — an existing alias such as `hetzner`, or `user@hostname`.
2. **Hermes Linux user** — usually `hermes`; enter `-` if your SSH login already owns Hermes.
3. **Coding client** — Codex, Claude Code, both, or none.

It shows what will change before applying anything. The existing Hermes user
must have Python 3 and a configured Hermes gateway. When switching users, a root
login uses `runuser`; other logins need passwordless `sudo` for the Hermes user.
SSH keys and the known host entry must already work noninteractively.

Pairing sends the bundled setup helper over SSH. The helper keeps unrelated
`.env` settings, preserves the API key where present, saves a private backup,
sets the API listener to loopback, restarts the standard gateway, and verifies
Runs API support. Only then does the wizard save the key locally and register
the chosen coding client. API keys never appear in setup output or command
arguments.

Open the app and choose **Connect**. For a CLI-only session, keep
`hermes-bridge-tool tunnel` running in a terminal. Then check:

```sh
hermes-bridge-tool doctor
```

Expect `"ready": true` for task submission, status, and stopping. The additional
`"session_tools_ready"` and `"steering_ready"` fields report support for browsing
conversations and steering runs. These optional features need a compatible Hermes
gateway; base task tools can still work without them. Doctor checks API access
and features, not the remote model provider. Try a small agent task after
restarting your coding client.

To use existing CLI or web UI conversations, connect to the gateway serving the
profile where those sessions are saved. Start by asking your coding client to
list Hermes sessions without a source filter. Source labels come from Hermes;
the list can include `cli`, `hermes_browser`, and `api_server`. See
[sessions and monitoring](SESSIONS.md) for the available tools and examples.

## Optional CLI and web UI turn observer

To track lifecycle events for remote CLI and web UI turns, pair with:

```sh
hermes-bridge-tool setup --observe-sessions
```

For a fresh install, `./install.sh --observe-sessions` combines installation and
pairing. This uses the same connection prompts and flags as ordinary setup. It copies the
bundled observer plugin into the remote Hermes installation, enables it through
the Hermes CLI, restarts the gateway, and verifies the authenticated observer
endpoint before saving the pairing locally. Reopen any existing remote CLI processes
to load the plugin there too. Ordinary setup keeps the base tools usable without
installing the observer.

The plugin uses the existing gateway's authenticated API, with no additional
daemon or port. It stores private per-turn metadata in the active profile and
does not copy prompts or message history. Observation begins after the plugin
loads; crashes, missing finish hooks, or plugin-disabled safe mode can leave
completion unknown. See [observed turns](SESSIONS.md#observe-cli-and-web-ui-turns)
for the tool workflow and limits.

## Repeatable command-line setup

To use explicit connection settings without interactive questions:

```sh
hermes-bridge-tool setup --host hetzner --remote-user hermes --client both --yes
```

Use `--remote-user -` to keep the SSH login user. `--remote-home /path/to/profile`
selects a custom Hermes profile, while `--remote-port` and `--local-port` select
ports. Repeating setup reuses the server key. A restart failure leaves the updated
server configuration in place; resolve the service issue and retry.

Registration can be done separately:

```sh
hermes-bridge-tool register codex
hermes-bridge-tool register claude
```

A different existing Claude entry named `hermes-bridge-tool` is left for you to resolve; the
command explains how to replace it. Identical registrations can be repeated.

Installer switches: `--no-app`, `--no-setup`, `--yes`, and `--dry-run`. `--yes`
accepts bootstrapping uv and skips installer prompts; server setup has its own
explicit `--yes` switch.

## Custom services and manual setup

The automatic restart targets `hermes gateway restart`. For a container or
custom service manager, configure the API using its existing deployment and
restart it there. Hermes's active environment needs:

```dotenv
API_SERVER_ENABLED=true
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8642
API_SERVER_KEY=YOUR_PRIVATE_KEY
```

Keep the environment file private. On a normal Linux installation, the standalone
helper `src/hermes_bridge_tool/server_setup.py` can also be copied to the server and
run **as the Hermes user**. Use `--check` to preview, or omit `--restart` to update
only the file. Its `--json` mode is for machine pairing and returns a credential;
use the ordinary human output in your terminal.

After configuring a custom service, enter the same API key locally:

```sh
hermes-bridge-tool configure --host hetzner
hermes-bridge-tool register both
```

Use the hidden key prompt or the app's Connection Settings. See the
[official Hermes API documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/)
for server compatibility and configuration details.

## Local settings

Both companions read `~/.config/hermes-bridge-tool/config.json`:

```json
{
  "ssh_host": "hetzner",
  "local_port": 18642,
  "remote_port": 8642,
  "api_key_file": "~/.config/hermes-bridge-tool/api-key"
}
```

The API key is stored separately with mode `600`. `HERMES_BRIDGE_TOOL_CONFIG` changes
the settings path. The MCP server also supports `HERMES_API_URL`,
`HERMES_API_KEY_FILE`, and `HERMES_API_KEY`; URL overrides must be loopback
origins. Prefer the shared file settings for GUI clients.

Disconnect and reconnect after changing ports. Quitting the app closes only the
SSH tunnel it owns. It does not stop remote agent runs.

## Moving from the provisional name

The unreleased project was previously named **Hermes Bridge**. Install this
checkout and pair again with `./install.sh --observe-sessions` to create the new
`hermes-bridge-tool` installation, settings, and MCP registration. Existing
credentials and settings are not moved automatically.

After checking the new registration, remove the old `hermes` MCP entry manually
with your coding client's CLI (`codex mcp remove hermes` or
`claude mcp remove hermes --scope user`) if it belongs to the previous version of
this tool. Quit the old app and remove its local installation when no longer
needed. The rename does not automatically remove an old server plugin or change
an unrelated Hermes Bridge API service.

## Updating and removing

After pulling changes, run `./install.sh --no-setup` again. It refreshes the
installed package and app. Quit and reopen an older running app. Connection
settings and the key stay outside the installation. Restart Codex or Claude Code
to load updated MCP tools. The menu bar app remains the connection manager;
session tools appear in your coding client.

To add the observer to an existing pairing, refresh the local installation first,
then run `hermes-bridge-tool setup --observe-sessions` and reopen remote CLI processes.

Remove the CLI with `uv tool uninstall hermes-bridge-tool`, remove the app from
`~/Applications`, and remove its MCP registration using your client's CLI.
Keep `~/.config/hermes-bridge-tool` if you plan to reinstall. The server configuration
is separate; disable its API explicitly if you no longer need it.

## Troubleshooting

- **SSH failed:** verify the alias, loaded key, host key, and remote username in a terminal.
- **No configured Hermes home:** run under the agent's Linux user or specify `--remote-home`.
- **Restart failed:** inspect the existing gateway/service manager. Saved settings and its backup remain on the server.
- **Missing capabilities:** check the installed Hermes version; upgrade the agent explicitly before retrying.
- **Session tools unavailable:** check `session_tools_ready` in doctor and `features.session_resources` in `hermes_check`; an older gateway may support only the base Runs tools.
- **Expected chat missing:** check the connected Hermes profile and list sessions without a source filter. Laptop CLI sessions and a separate web UI's private history are outside this gateway's database.
- **Chat updates but completion is unknown:** use a run ID with `hermes_wait`, or install the observer and track a new turn with `hermes_wait_turn`. Saved messages alone cannot establish whether a live CLI process has finished its turn.
- **No observed turns:** confirm observer installation and reopen the remote CLI. Turns that ran before the plugin loaded are not recovered retrospectively.
- **Local port occupied:** close a manually opened tunnel before choosing Connect.
- **Unauthorized:** ensure the local key matches the active profile. Rerun pairing to retrieve it.
- **Tools missing:** run `hermes-bridge-tool register` for your client and restart it.

For the original Monemetrics bridge, replace the old script registration with the
installed tool. Reuse its key by setting `api_key_file` to
`~/.config/hermes-mcp/api-key`, or pair again.
