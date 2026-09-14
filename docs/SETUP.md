# Setup

For installation without a source checkout or Xcode, use a
[release download](https://github.com/hourafter4/hermes-bridge-tool/releases/latest).
The macOS bundle installs the CLI and app together; the CLI bundle works on Linux
and macOS. Then choose a backend below.


For an existing WebUI or direct HTTPS API, start with [backend setup](BACKENDS.md).
Those configuration commands do not change the server. The pairing instructions
below enable and restart the **Gateway API** specifically.


## Guided setup

From a clone of this repository:

```sh
./install.sh --setup
```

The installer copies the CLI into an isolated per-user environment. On macOS it
also builds and installs `~/Applications/Hermes Bridge Tool.app` when Swift is
available. It needs no `sudo` on your laptop. If your shell cannot find the new
CLI, use the absolute path printed by the installer, or add `~/.local/bin`
to `PATH` and open a new terminal. Runtime dependencies come from the tested
lockfile and every downloaded wheel hash is checked before replacing the CLI.

The wizard asks for:

1. **SSH host** — an existing alias such as `hermes-server`, or `user@hostname`.
2. **Hermes Linux user** — usually `hermes`; enter `-` if your SSH login already owns Hermes.
3. **Coding client** — Codex, Claude Code, both, or none.

It shows what will change before applying anything. The existing Hermes user
must have Python 3 and a configured Hermes gateway. When switching users, a root
login uses `runuser`; other logins need passwordless `sudo` for the Hermes user.
SSH keys and the known host entry must already work noninteractively.

`hermes-server` is an example alias, not a hosting provider or required hostname.
Use any working entry from `~/.ssh/config`, or `user@hostname`. The server can be
a VPS, a machine on your local network, or another SSH-accessible Linux host.
Existing saved connection names are preserved when updating the tool.

Pairing sends the bundled setup helper over SSH. The helper keeps unrelated
`.env` settings, preserves the API key where present, saves a private backup,
sets the API listener to loopback, restarts the standard gateway, and verifies
Runs API support. Only then does the wizard save the key locally and register
the chosen coding client. API keys never appear in setup output or command
arguments.

Open the app and choose **Connect**, or start the shared connection from the CLI:

```sh
hermes-bridge-tool connect
hermes-bridge-tool doctor
```

The managed tunnel continues in the background; this terminal does not need to
stay open. The app and coding clients reuse the same connection.

Expect `"ready": true` for task submission, status, and stopping. The additional
`"session_tools_ready"` and `"steering_ready"` fields report support for browsing
conversations and steering runs. These optional features need a compatible Hermes
gateway; base task tools can still work without them. Doctor checks API access
and features, not the remote model provider or permission to submit tasks.
Monitoring is the default. Read recent sessions first, or explicitly enable
control in your own terminal before trying a small task.

To use existing CLI or web UI conversations, connect to the gateway serving the
profile where those sessions are saved. Start by asking your coding client to
list Hermes sessions without a source filter. Source labels come from Hermes;
the list can include `cli`, `hermes_browser`, and `api_server`. See
[sessions and monitoring](SESSIONS.md) for the available tools and examples.

## Choose access permissions

Every configuration without an explicit policy starts in monitor mode, including
saved configurations from older releases. Reads and reconnection are allowed;
creating chats, submitting, steering, and stopping tasks are denied before any
upstream request. Inspect and change policy locally:

```sh
hermes-bridge-tool security status
hermes-bridge-tool security mode control
```

Enabling control requires an interactive terminal and typing `ENABLE`. Sending
native platform messages also requires `hermes-bridge-tool security messages on`
and its own confirmation. Neither setting enables remote approval responses:
resolve approvals directly in Hermes. Return to monitoring with `security mode
monitor`; this also clears the messaging grant.

The macOS menu shows the effective mode. Choose **Lock bridge access**, or run
`hermes-bridge-tool security lock`, to block future upstream reads, writes, and
reconnects and close the shared tunnel. Unlock locally using `security unlock`
and type `ENABLE`. Locking does not revoke credentials, stop remote work already
accepted, or prevent processes outside this bridge from using your credentials.
An in-flight request may already have reached Hermes. See [Security](SECURITY.md).

## Restrict everyday SSH access

Use an administrator alias for setup and a separate restricted runtime identity
for ordinary connections. After configuring your Gateway/WebUI ports:

```sh
hermes-bridge-tool harden-ssh --admin-host my-admin-alias --hermes-user hermes
hermes-bridge-tool reconnect
```

This is an explicit server administration operation. The existing administrator
login must be root or have passwordless sudo; its credentials and normal login
remain unchanged. The helper creates a non-root forwarding account (default
`hermes-bridge`) whose key can reach only the configured server-loopback API
ports. It restricts shell sessions, other forwarding, and SSH startup hooks.
Private runtime keys are stored under the bridge configuration directory.
`ssh_user` and `ssh_identity_file` override the alias's normal login and key for
runtime use, so a root alias no longer means everyday root access.

To configure native Hermes MCP as well, append its fixed absolute command last:

```sh
hermes-bridge-tool harden-ssh --admin-host my-admin-alias --hermes-user hermes \
  --native-command /home/hermes/.local/bin/hermes mcp serve
hermes-bridge-tool reconnect
```

Native MCP uses a different key and a forced command under the Hermes account;
it cannot request a different SSH command or forwarding. If native MCP was
already configured, supply `--native-command` so its older unrestricted command
is replaced too. Otherwise that option is unnecessary. Restart all coding
clients to close previously running native connections.

The helper validates SSH daemon settings and reloads the existing systemd SSH
service. Custom SSH configurations or service managers may require manual
administration; inspect a failed setup before retrying. See
[the security model](SECURITY.md#separate-setup-access-from-runtime-ssh) for the
restrictions and remaining Hermes account privileges.

## Optional macOS Keychain migration

Move existing Gateway and WebUI credentials to macOS Keychain explicitly:

```sh
hermes-bridge-tool credentials migrate --to keychain --remove-files
```

The migration reads back each new item before switching the configuration.
`--remove-files` removes the old plaintext credential files only after successful
migration; omit it to retain those files. Later `configure` and `configure-webui`
commands save credentials to the selected store. Keychain failure is reported
without silently falling back to plaintext. Linux users retain private mode-600
files, or select file storage with `credentials migrate --to file`.

Environment overrides and external backups are independent of this migration.
Keychain is storage protection; a running authorized bridge still receives the
credential in memory. The bridge does not provide per-session access control or
general secret redaction. Reading a conversation shares it with the coding client
and potentially its model provider. Choose the connected profile accordingly.

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
hermes-bridge-tool setup --host hermes-server --remote-user hermes --client both --yes
```

Use `--remote-user -` to keep the SSH login user. `--remote-home /path/to/profile`
selects a custom Hermes profile. `--remote-port` selects the server port;
`--local-port` is retained as a legacy local-URL marker, not a TCP listener. Repeating setup reuses the server key. A restart failure leaves the updated
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

The automatic restart targets `hermes gateway restart`. Some installations return
success from that command without restarting anything when the user systemd bus
or linger is unavailable. Setup verifies the API for up to 30 seconds afterward;
a successful command exit alone does not establish readiness.

If your gateway uses a watchdog or another service manager, pass its existing
restart command. For example, for a server with this watchdog installed:

```sh
hermes-bridge-tool setup --host hermes-server --remote-user hermes --client both \
  --observe-sessions \
  --restart-command '/home/hermes/.hermes/scripts/gateway-watchdog.sh --restart'
```

The command runs **on the server as the selected Hermes user**, with the selected
`HERMES_HOME`. Quote paths or arguments containing spaces inside the command.
Arguments are parsed without a shell: pipes, redirection, `~`, and environment
variable expansion are not supported. Use an existing executable script if your
restart needs those features. Custom restart commands have up to 120 seconds;
the default command has 40 seconds. Setup does not enable linger or change your
service manager.

For containers managed outside the selected user's environment, configure and
restart the API through the existing deployment instead. Hermes's active
environment needs:

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
hermes-bridge-tool configure --host hermes-server
hermes-bridge-tool register both
```

Use the hidden key prompt or the app's Connection Settings. See the
[official Hermes API documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/)
for server compatibility and configuration details.

## Local settings

Both companions read `~/.config/hermes-bridge-tool/config.json`:

```json
{
  "ssh_host": "hermes-server",
  "local_port": 18642,
  "remote_port": 8642,
  "api_key_file": "~/.config/hermes-bridge-tool/api-key"
}
```

File credentials are stored separately with mode `600`; explicit macOS migration
can select Keychain instead. `HERMES_BRIDGE_TOOL_CONFIG` changes
the settings path. The MCP server also supports `HERMES_API_URL`,
`HERMES_API_KEY_FILE`, and `HERMES_API_KEY`; URL overrides accept HTTPS endpoints, including reverse-proxy path prefixes.
Loopback HTTP URLs must match the configured managed SSH endpoint; requests
travel over a private Unix socket, never an arbitrary local TCP listener. WebUI and native MCP have separate settings described in
[backend setup](BACKENDS.md). Prefer shared file settings for GUI clients.

Run `hermes-bridge-tool reconnect` or choose **Reconnect** after changing ports.
Quitting the app leaves the shared tunnel running. **Disconnect** explicitly
closes the tunnel for all local clients; remote agent runs continue.

## Reconnecting

If an agent still has the bridge's MCP tools, it can call
`hermes_connection_status()` and `hermes_reconnect(backend="all")`. It should then
read the status of any existing task using its saved ID, rather than submit the
instructions again. A backend error does not remove the local tool definitions.

From a terminal, or when the MCP process itself is unavailable:

```sh
hermes-bridge-tool connection-status
hermes-bridge-tool reconnect
```

Reconnect the coding harness's MCP connection if needed, or restart the client.
CLI recovery reloads saved settings and restores access to configured HTTP
backends. Native upstream recovery uses the MCP `hermes_reconnect` tool in the
coding client that owns that connection. Recovery does not rerun
pairing, restart Hermes, refresh credentials, or replay messages. Expired cookies,
invalid keys, and a stopped remote API remain actionable errors. See
[backend recovery](BACKENDS.md#recover-access-without-resubmitting-work) for agent
examples, native MCP cursor handling, and the shared tunnel's lifetime.

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
installed package and app. Run `hermes-bridge-tool register both` (or your chosen
client) to update older registrations to the stable CLI launcher. **For 0.2.0,
quit the older app and restart every MCP process:** already-running code keeps its older permissions and transport until
restarted. Configurations without an explicit policy now default to monitoring.
Run `hermes-bridge-tool reconnect` to replace the previous TCP tunnel with private
Unix sockets. The compatibility `tunnel` command now aliases `connect`. Connection
settings and the key stay outside the installation. Restart Codex or Claude Code
to load updated MCP tools. The app, CLI, and MCP recovery tools use the shared
connection manager; session tools appear in your coding client.

To add the observer to an existing pairing, refresh the local installation first,
then run `hermes-bridge-tool setup --observe-sessions` and reopen remote CLI processes.

Run `hermes-bridge-tool disconnect` to close the managed tunnel, then remove the
CLI launcher at the installed path and its private runtimes under
`~/.local/share/hermes-bridge-tool` after closing every coding client. Respect any
custom `XDG_DATA_HOME` or bin path used during installation. Remove the app from
`~/Applications` and its MCP registration using your client's CLI. For an older
uv-tool installation, `uv tool uninstall hermes-bridge-tool` removes its old runtime;
do not use that command as an upgrade route for the new installer.
Keep `~/.config/hermes-bridge-tool` if you plan to reinstall. The server configuration
is separate; disable its API explicitly if you no longer need it.

## Troubleshooting

- **SSH failed:** verify the alias, loaded key, host key, and remote username in a terminal.
- **No configured Hermes home:** run under the agent's Linux user or specify `--remote-home`.
- **Restart failed or API readiness timed out:** inspect the existing gateway/service manager. A successful `hermes gateway restart` exit can occur without a restart when the user service bus or linger is unavailable. Use `--restart-command` for an existing watchdog or custom service. The timeout reports connection refusal, timeout, or the last HTTP error without exposing gateway output. Saved settings and its backup remain on the server.
- **Missing capabilities:** check the installed Hermes version; upgrade the agent explicitly before retrying.
- **Session tools unavailable:** check `session_tools_ready` in doctor and `features.session_resources` in `hermes_check`; an older gateway may support only the base Runs tools.
- **Expected chat missing:** check the connected Hermes profile and list sessions without a source filter. Laptop CLI sessions and a separate web UI's private history are outside this gateway's database.
- **Chat updates but completion is unknown:** use a run ID with `hermes_wait`, or install the observer and track a new turn with `hermes_wait_turn`. Saved messages alone cannot establish whether a live CLI process has finished its turn.
- **No observed turns:** confirm observer installation and reopen the remote CLI. Turns that ran before the plugin loaded are not recovered retrospectively.
- **Connection dropped:** use `hermes_reconnect` or `hermes-bridge-tool reconnect`, then read the existing task's status. Do not resubmit a prompt just because a connection failed.
- **Manual TCP tunnel or arbitrary localhost URL no longer works:** SSH HTTP access requires the bridge's private Unix sockets. Use the configured SSH backend and `reconnect`; it will never send credentials to a foreign TCP listener. Direct authenticated HTTPS remains supported.
- **Policy denies task control or messages:** enable only the intended permission from your own interactive terminal. Reconnection does not grant permission, unlock the bridge, or enable approvals.
- **Unauthorized:** refresh the correct backend's credentials using `configure` for Gateway or `configure-webui` for WebUI. Reconnection alone does not renew keys or cookies.
- **Tools missing:** run `hermes-bridge-tool register` for your client and reconnect its MCP server or restart the client. An unavailable MCP process cannot run its own reconnect tool.

For the original Monemetrics bridge, replace the old script registration with the
installed tool. Reuse its key by setting `api_key_file` to
`~/.config/hermes-mcp/api-key`, or pair again.
