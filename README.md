<img src="assets/brand/github-banner.svg" alt="Hermes Bridge — Your agent. Within reach." width="1280">

# Hermes Bridge

**Give your coding agent a direct line to your remote Hermes agent.**

Send instructions from Codex or Claude Code, check progress, and bring the result
back into your workspace. A small macOS menu bar app keeps the SSH connection
within reach. The same MCP tools work from the CLI on Linux.

[Get started](#install-and-pair) · [Setup details](docs/SETUP.md) · [Harnesses](docs/PLUGINS.md) · [Brand assets](docs/BRAND.md)

## Install and pair

Clone this repository, enter its directory, and run:

```sh
./install.sh --setup
```

The installer installs the CLI for your user and builds the macOS companion when
Apple's Command Line Tools are available. It offers to install `uv` if needed.
The guided setup then asks for your SSH host, the Linux user running Hermes, and
which coding clients to connect.

**Pairing handles the server configuration for you:** it enables Hermes's
localhost API, preserves or creates its key, restarts the standard gateway,
checks compatibility, and saves the key privately on your machine over SSH.
You do not need to paste credentials between machines.

Open **Hermes Bridge** from `~/Applications`, choose **Connect**, and restart your
coding client. Ask it:

> Check Hermes, then ask it to reply “Bridge connected” without using tools.
> Wait for its result and show me the reply.

Already installed? Run `hermes-bridge setup` or choose **Set up connection…** in
the menu bar. For a Linux/CLI-only install:

```sh
./install.sh --no-app --setup
hermes-bridge tunnel
```

### What you need

- A Linux server with [Hermes installed and configured](https://hermes-agent.nousresearch.com/docs/getting-started/installation/), including a working model provider.
- SSH access using your key/agent, with the host key already verified.
- macOS 13+ for the menu bar app, or macOS/Linux for the CLI.
- Xcode Command Line Tools to build the Mac app from source (`xcode-select --install`).

The wizard configures an existing Hermes gateway. It does not install or upgrade
the agent itself. Custom service managers and Docker setups use the
[manual setup path](docs/SETUP.md#custom-services-and-manual-setup).

## What it does

| From your coding agent | Hermes Bridge |
| --- | --- |
| “Ask Hermes to inspect the server logs.” | Submits instructions to the remote agent. |
| “Has that finished?” | Retrieves run state and final output. |
| “Continue that conversation.” | Reuses the Hermes session ID. |
| “Stop that task.” | Requests interruption at the next safe point. |

The tools are `hermes_check`, `hermes_send`, `hermes_status`, and `hermes_stop`.
Long tasks return a run ID immediately, so your coding agent can check back later.

```text
Codex / Claude Code → local MCP process → SSH tunnel → Hermes API → remote agent
                                             ↑
                                     menu bar companion
```

Each harness starts its own MCP process. The menu bar app owns the shared SSH
tunnel. The installed CLI and app work independently of the source checkout.
Hermes executes instructions with its server-side tools, settings, and permissions.

Read local prompt files before sending their contents: the remote agent cannot
read laptop paths. Save run IDs and final output; Hermes retains completed run
status only temporarily. Retry an uncertain submission with the same `request_id`
and identical inputs. Disconnecting the tunnel does not cancel accepted work;
stopping a run does not undo earlier actions. Resolve pending approvals in Hermes.

## A small, inspectable project

| Component | Where |
| --- | --- |
| Python CLI, MCP tools, SSH pairing | `src/hermes_bridge/` |
| Native Swift/AppKit companion | `macos/` |
| Codex and Claude Code plugin manifests | `.codex-plugin/`, `.claude-plugin/` |
| Editable logo and app artwork | `assets/brand/` |
| Mock API, installer, and pairing tests | `tests/` |

Settings live in `~/.config/hermes-bridge/config.json`; the key is a separate
private file. No account with Hermes Bridge, public port, or hosted relay is
required. See [setup details](docs/SETUP.md) for profiles, ports, migration, and
troubleshooting.

## Develop and share

```sh
uv sync --locked
uv run python -m unittest discover -s tests -v
sh tests/test_install.sh
./scripts/build-macos.sh  # macOS
```

[Contributing](CONTRIBUTING.md) explains the development loop.
[Release instructions](docs/RELEASING.md) cover pushing your repository, creating
artifacts, and drafting a GitHub release. The Mac app is currently ad hoc signed;
a public notarized release needs an Apple Developer signing identity.

Independent open-source companion; not an official Nous Research, OpenAI, or
Anthropic product. MIT licensed. Original bridge code came from Monemetrics;
see [LICENSE](LICENSE).
