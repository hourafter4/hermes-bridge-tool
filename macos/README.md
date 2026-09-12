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

The companion requires macOS 13+. Source builds need Xcode Command Line Tools:

```sh
./scripts/build-macos.sh
open "dist/Hermes Bridge Tool.app"
```

The build creates the icon set, bundles artwork and the setup launcher, compiles
the current Mac architecture, applies ad hoc signing, and runs self-tests. It
does not connect to a server. For public distribution, see
[release instructions](../docs/RELEASING.md).

Both runtimes read `~/.config/hermes-bridge-tool/config.json`; the API key stays in a
separate file with mode `600`. SSH uses your existing keys and known host entry.
The app uses the same CLI connection manager as agents and terminal commands.
**Quit** leaves the shared tunnel running. **Disconnect** closes it for all local
clients, including Codex and Claude Code. Remote agent work continues after
disconnection. Reconnect restores access without restarting Hermes or replaying
messages; expired credentials still need updating.

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
