<img src="assets/brand/github-banner.svg" alt="Hermes Bridge Tool — Your agent. Within reach." width="1280">

# Hermes Bridge Tool

**Give your coding agent a direct line to your remote Hermes agent.**

Browse your server's Hermes conversations from Codex or Claude Code, send messages
to new or existing chats, and wait for remote tasks to finish. It wraps the existing WebUI API, Gateway API, and native Hermes MCP server.
Connect over HTTPS or SSH, with an optional icon-only macOS menu bar companion.

[Choose a backend](docs/BACKENDS.md) · [Download](#download-and-install) · [Sessions and monitoring](docs/SESSIONS.md) · [Setup details](docs/SETUP.md) · [Harnesses](docs/PLUGINS.md) · [Brand assets](docs/BRAND.md)

Hermes Bridge Tool is the provisional project name. See the [rename notes](docs/SETUP.md#moving-from-the-provisional-name) if you installed the earlier Hermes Bridge version.

## Download and install

[**Download for macOS — Apple Silicon and Intel**](https://github.com/hourafter4/hermes-bridge-tool/releases/latest/download/hermes-bridge-tool-macos-universal.zip)

Extract the ZIP and open **Install.command**. It installs the prebuilt menu bar
app and CLI into your user account. No Xcode, source checkout, or system Python
is required. The installer offers to install `uv` when missing; internet access
is needed for Python and dependencies.

**Linux / CLI only:** download the [CLI installation bundle](https://github.com/hourafter4/hermes-bridge-tool/releases/latest/download/hermes-bridge-tool-cli.tar.gz),
extract it, and run `sh install.sh` inside its folder.

The app is not Apple-notarized. If macOS blocks it, use **System Settings →
Privacy & Security → Open Anyway**, following [Apple's instructions](https://support.apple.com/guide/mac-help/mh40616/mac).

[All releases, installation notes, and checksums](https://github.com/hourafter4/hermes-bridge-tool/releases)

After installation, choose a connection below. Updates preserve your saved
settings; quit and reopen an older app, and reconnect your coding client's MCP.

## Choose what to connect

| You want to… | Use |
| --- | --- |
| Continue browser chats and monitor browser-started tasks | **WebUI API** (`hermes_webui_*`) |
| Delegate tasks through the Gateway | **Gateway API** (`hermes_send`, `hermes_wait`, etc.) |
| Read platform conversations or deliver authorized messages | **Native Hermes MCP** (`hermes_native_*`) |
| Monitor independently started CLI turns | **Optional observer** (`hermes_turns`, `hermes_wait_turn`) |

Agents call `hermes_backends` first. Tool descriptions explain ownership, IDs,
completion evidence, and when each interface applies. They never need to guess
whether a browser stream ID is a Gateway run ID.

If you already have a WebUI, connect without changing the server. Skip the first
command if you installed a release:

```sh
./install.sh --no-setup
hermes-bridge-tool configure-webui --url https://your-webui.example
hermes-bridge-tool register both
hermes-bridge-tool doctor --backend webui
```

The command privately prompts for the WebUI login cookie. Use `--ssh --host hermes-server`
instead of `--url` for a server-only WebUI. Replace `hermes-server` with your
own SSH alias or `user@hostname`; any SSH-accessible server can be used. Direct
HTTPS needs no SSH tunnel.
See [backend setup and agent workflows](docs/BACKENDS.md) for authentication,
native MCP setup, and monitoring examples. The Gateway pairing route follows.

## Install and pair

Clone this repository, enter its directory, and run:

```sh
./install.sh --observe-sessions
```

The installer installs the CLI for your user and builds the macOS companion when
Apple's Command Line Tools are available. It offers to install `uv` if needed.
The guided setup then asks for your SSH host, the Linux user running Hermes, and
which coding clients to connect. `--observe-sessions` also installs the server
observer for CLI turn completion; use `--setup` for the native API tools alone.

**Pairing handles the server configuration for you:** it enables Hermes's
localhost API, preserves or creates its key, restarts the standard gateway,
checks compatibility, and saves the key privately on your machine over SSH.
You do not need to paste credentials between machines.

Open **Hermes Bridge Tool** from `~/Applications`, choose **Connect**, and restart your
coding client. Ask it:

> Check Hermes, then ask it to reply “Bridge connected” without using tools.
> Wait for its result and show me the reply.

Already installed? Run `hermes-bridge-tool setup` or choose **Set up connection…** in
the menu bar. For a Linux/CLI-only install:

```sh
./install.sh --no-app --observe-sessions
hermes-bridge-tool connect
```

### What you need

- A Linux server with [Hermes installed and configured](https://hermes-agent.nousresearch.com/docs/getting-started/installation/), including a working model provider.
- For SSH connections: key/agent access with the host key already verified.
- For direct HTTPS: an existing endpoint and its accepted authentication.
- macOS 13+ for the menu bar app, or macOS/Linux for the CLI.
- Only for source builds: Xcode Command Line Tools (`xcode-select --install`).

The wizard configures an existing Hermes gateway. It does not install or upgrade
the agent itself. Custom service managers and Docker setups use the
[manual setup path](docs/SETUP.md#custom-services-and-manual-setup).

## What it does

| From your coding agent | Hermes Bridge Tool |
| --- | --- |
| “Find my recent Hermes CLI or web UI chats.” | Lists saved sessions and reads their messages. |
| “Start a new chat about the server logs.” | Creates a conversation and submits instructions. |
| “Continue that conversation.” | Starts a new turn using its saved transcript. |
| “Wait for that task to finish.” | Polls a known run and returns its state and output. |
| “Watch this existing chat for updates.” | Watches saved messages for changes. |
| “Monitor this CLI turn until it finishes.” | Tracks lifecycle events with the optional server observer. |
| “Tell the running task to focus on today's logs.” | Steers a known run when the gateway supports it. |
| “Stop that task.” | Requests interruption and checks the resulting state. |
| “Reconnect to Hermes and check that task again.” | Restores local access and reads the existing task; it does not resubmit instructions. |

The 13 Gateway and observer tools remain available alongside 10 WebUI tools,
three native MCP wrappers, the `hermes_backends` routing guide, and two connection
tools (29 total):

| Purpose | Tools |
| --- | --- |
| Connection and capabilities | `hermes_check` |
| Diagnose or reconnect access | `hermes_connection_status`, `hermes_reconnect` |
| Browse conversations | `hermes_sessions`, `hermes_session`, `hermes_messages` |
| Start or continue a chat | `hermes_new_chat`, `hermes_send` |
| Monitor work | `hermes_status`, `hermes_wait`, `hermes_watch_session` |
| Observe CLI and web UI turns | `hermes_turns`, `hermes_wait_turn` |
| Guide or stop a running task | `hermes_steer`, `hermes_stop` |

Sessions come from the connected server profile, including Hermes CLI and web UI
history saved there. API tasks use a **run ID** for completion monitoring. To
observe CLI and web UI turn lifecycle events on an existing installation, add
the optional server plugin (the install command above already includes it):

```sh
hermes-bridge-tool setup --observe-sessions
```

Reopen existing remote CLI processes so they load the plugin. It records new
turns from lifecycle hooks and serves metadata through the existing gateway;
missing finish events remain unknown. Continuing a CLI session starts a gateway
turn using its transcript. See [sessions and monitoring](docs/SESSIONS.md) for
examples and the limits of live terminal access.

```text
Codex / Claude Code → local MCP client
                     ├─ HTTPS or SSH → existing WebUI API
                     ├─ HTTPS or SSH → existing Gateway API (+ optional observer)
                     └─ local process or SSH → native hermes mcp serve
```

Each harness starts its own MCP process. The app, CLI, and MCP recovery tools use
one shared SSH connection manager; browsing and messaging happen through your coding client's tools. The
installed CLI and app work independently of the source checkout.
Hermes executes instructions with its server-side tools, settings, and permissions.

Read local prompt files before sending their contents: the remote agent cannot
read laptop paths. Save run IDs and final output; Hermes retains completed run
status only temporarily. For Gateway submissions, retry with the same `request_id` and identical inputs.
WebUI and native writes have no such guarantee; inspect state before retrying. Disconnecting the tunnel does not cancel accepted work;
stopping a run does not undo earlier actions. Resolve pending approvals in Hermes.

## Reconnect without repeating work

If a backend disconnects, the agent can call `hermes_connection_status()` and
`hermes_reconnect(backend="all")`, then check the task using its saved ID. The
tool reloads saved settings and restores local access; it does not restart the
server, renew credentials, or send the prompt again.

You can also choose **Reconnect** in the menu bar or run:

```sh
hermes-bridge-tool reconnect
```

If the bridge MCP process itself is unavailable, run that command through the
agent's shell or a terminal, then reconnect the harness's MCP server or restart
the coding client. Quitting the app leaves the managed tunnel running. Explicit
**Disconnect** closes the shared tunnel for all local clients without cancelling
remote agent work. See [connection recovery](docs/BACKENDS.md#recover-access-without-resubmitting-work).

## A small, inspectable project

| Component | Where |
| --- | --- |
| Python CLI, MCP tools, SSH pairing | `src/hermes_bridge_tool/` |
| Native Swift/AppKit companion | `macos/` |
| Codex and Claude Code plugin manifests | `.codex-plugin/`, `.claude-plugin/` |
| Editable logo and app artwork | `assets/brand/` |
| Mock API, installer, and pairing tests | `tests/` |

Settings live in `~/.config/hermes-bridge-tool/config.json`; the key is a separate
private file. No account with Hermes Bridge Tool, public port, or hosted relay is
required. See [setup details](docs/SETUP.md) for profiles, ports, migration, and
troubleshooting.

## Feedback and maintenance

Contributions happen through [GitHub issues](https://github.com/hourafter4/hermes-bridge-tool/issues/new/choose):
report bugs, request features, or ask questions. The maintainer implements accepted
changes; **pull requests are disabled**. See [Contributing](CONTRIBUTING.md)
for what to include in an issue.

[Development notes](docs/DEVELOPMENT.md) document the maintainer's local checks and code layout.
[Release instructions](docs/RELEASING.md) cover pushing your repository, creating
artifacts, and drafting a GitHub release. The Mac app is currently ad hoc signed;
a public notarized release needs an Apple Developer signing identity.

Independent open-source companion; not an official Nous Research, OpenAI, or
Anthropic product. MIT licensed. Original bridge code came from Monemetrics;
see [LICENSE](LICENSE).
