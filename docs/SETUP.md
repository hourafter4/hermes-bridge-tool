# Setup

## Guided setup

From a clone of this repository:

```sh
./install.sh --setup
```

The installer copies the CLI into an isolated per-user environment. On macOS it
also builds and installs `~/Applications/Hermes Bridge.app` when Swift is
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
`hermes-bridge tunnel` running in a terminal. Then check:

```sh
hermes-bridge doctor
```

Expect `"ready": true`. This checks API access and features, not the remote
model provider. Try a small agent task after restarting your coding client.

## Repeatable command-line setup

To use explicit connection settings without interactive questions:

```sh
hermes-bridge setup --host hetzner --remote-user hermes --client both --yes
```

Use `--remote-user -` to keep the SSH login user. `--remote-home /path/to/profile`
selects a custom Hermes profile, while `--remote-port` and `--local-port` select
ports. Repeating setup reuses the server key. A restart failure leaves the updated
server configuration in place; resolve the service issue and retry.

Registration can be done separately:

```sh
hermes-bridge register codex
hermes-bridge register claude
```

A different existing Claude entry named `hermes` is left for you to resolve; the
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
helper `src/hermes_bridge/server_setup.py` can also be copied to the server and
run **as the Hermes user**. Use `--check` to preview, or omit `--restart` to update
only the file. Its `--json` mode is for machine pairing and returns a credential;
use the ordinary human output in your terminal.

After configuring a custom service, enter the same API key locally:

```sh
hermes-bridge configure --host hetzner
hermes-bridge register both
```

Use the hidden key prompt or the app's Connection Settings. See the
[official Hermes API documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/)
for server compatibility and configuration details.

## Local settings

Both companions read `~/.config/hermes-bridge/config.json`:

```json
{
  "ssh_host": "hetzner",
  "local_port": 18642,
  "remote_port": 8642,
  "api_key_file": "~/.config/hermes-bridge/api-key"
}
```

The API key is stored separately with mode `600`. `HERMES_BRIDGE_CONFIG` changes
the settings path. The MCP server also supports `HERMES_API_URL`,
`HERMES_API_KEY_FILE`, and `HERMES_API_KEY`; URL overrides must be loopback
origins. Prefer the shared file settings for GUI clients.

Disconnect and reconnect after changing ports. Quitting the app closes only the
SSH tunnel it owns. It does not stop remote agent runs.

## Updating and removing

After pulling changes, run `./install.sh --no-setup` again. It refreshes the
installed package and app. Quit and reopen an older running app. Connection
settings and the key stay outside the installation.

Remove the CLI with `uv tool uninstall hermes-bridge`, remove the app from
`~/Applications`, and remove its MCP registration using your client's CLI.
Keep `~/.config/hermes-bridge` if you plan to reinstall. The server configuration
is separate; disable its API explicitly if you no longer need it.

## Troubleshooting

- **SSH failed:** verify the alias, loaded key, host key, and remote username in a terminal.
- **No configured Hermes home:** run under the agent's Linux user or specify `--remote-home`.
- **Restart failed:** inspect the existing gateway/service manager. Saved settings and its backup remain on the server.
- **Missing capabilities:** check the installed Hermes version; upgrade the agent explicitly before retrying.
- **Local port occupied:** close a manually opened tunnel before choosing Connect.
- **Unauthorized:** ensure the local key matches the active profile. Rerun pairing to retrieve it.
- **Tools missing:** run `hermes-bridge register` for your client and restart it.

For the original Monemetrics bridge, replace the old script registration with the
installed tool. Reuse its key by setting `api_key_file` to
`~/.config/hermes-mcp/api-key`, or pair again.
