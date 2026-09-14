# macOS companion

For everyday installation, use the [prebuilt macOS release](https://github.com/hourafter4/hermes-bridge-tool/releases/latest).
From a source checkout, run `./install.sh --no-setup`. The installer places the app in `~/Applications/Hermes Bridge Tool.app` and installs
the Python CLI separately. Neither installed component needs this checkout at
runtime.

The menu bar shows only the Hermes Bridge Tool H icon, with no text or status
dot. Hover for connection status, or click the icon to see status and actions. Choose
**Set up connection…** to open the guided SSH pairing flow in Terminal, then
**Connect** to start or reuse the shared tunnel. **Reconnect** reloads saved
settings and restores local access after a disconnect. **Settings…** can also be
edited manually. API readiness and SSH errors appear in the menu.

The status shows **monitor only** or **task control enabled**. Monitoring is the
default, including after upgrading an older installation. **Lock bridge access**
blocks new reads, task submissions, platform messages, and agent reconnection,
then closes the shared tunnel. The menu shows **Locked** and disables Connect
and Reconnect. **Security and unlocking…** explains how to unlock in your own
Terminal with `hermes-bridge-tool security unlock`; review the prompt and type
`ENABLE` yourself. Unlocking is not exposed to agents as an MCP tool.

The companion requires macOS 13+. Source builds need Xcode Command Line Tools:

```sh
./scripts/build-macos.sh
open "dist/Hermes Bridge Tool.app"
```

The build creates the icon set, bundles artwork and the setup launcher, compiles
both Apple Silicon and Intel architectures, signs the app, and runs self-tests. It
does not connect to a server. For public distribution, see
[release instructions](../docs/RELEASING.md).

Both runtimes read `~/.config/hermes-bridge-tool/config.json`; the API key stays in a
separate file with mode `600`. SSH uses your existing keys and known host entry.
The app uses the same CLI connection manager as agents and terminal commands.
**Quit** leaves the shared tunnel running. **Disconnect** closes it for all local
clients, including Codex and Claude Code. Remote agent work continues after
disconnection. Reconnect restores access without restarting Hermes or replaying
messages; expired credentials still need updating.

Disconnect and Quit do not lock access. Locking does not cancel accepted remote
work or revoke credentials; other SSH clients remain outside this policy. A
process with your shell or filesystem access can change local policy, so this
control restricts bridge tools rather than providing an operating-system sandbox.
See [security details](../docs/SECURITY.md) for permission commands and boundaries.

Credentials can optionally use macOS Keychain. When a Gateway key uses Keychain,
update it through the CLI; the app refuses to save a new plaintext copy. The app
preserves the chosen credential storage and security policy when saving settings.

The GUI is native Swift/AppKit; connection management, readiness checks, guided
setup, and the coding harnesses use the installed Python CLI companion. Brand sources live in
[`assets/brand`](../assets/brand/).

## Multiple backends

The icon-only companion uses the shared connection manager for configured Gateway
and WebUI SSH forwards, and checks HTTP readiness through the CLI. Direct HTTPS
connections skip SSH when no backend needs a forward. Configure WebUI/direct URLs
with the CLI; the app Settings window edits Gateway SSH settings and preserves
other fields. Native stdio MCP is managed by the coding client's MCP process,
not the menu bar. An agent can call `hermes_reconnect(backend="native")` to recover
that upstream connection. See [backend setup and recovery](../docs/BACKENDS.md).
