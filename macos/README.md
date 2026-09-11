# macOS companion

For everyday installation, run `./install.sh --setup` from the repository root.
The installer places the app in `~/Applications/Hermes Bridge.app` and installs
the Python CLI separately. Neither installed component needs this checkout at
runtime.

The menu bar shows the Hermes Bridge H mark with a state indicator. Choose
**Set up connection…** to open the guided SSH pairing flow in Terminal, then
**Connect** to keep the tunnel running. **Settings…** can also be
edited manually. API readiness and SSH errors appear in the menu.

The companion requires macOS 13+. Source builds need Xcode Command Line Tools:

```sh
./scripts/build-macos.sh
open "dist/Hermes Bridge.app"
```

The build creates the icon set, bundles artwork and the setup launcher, compiles
the current Mac architecture, applies ad hoc signing, and runs self-tests. It
does not connect to a server. For public distribution, see
[release instructions](../docs/RELEASING.md).

Both runtimes read `~/.config/hermes-bridge/config.json`; the API key stays in a
separate file with mode `600`. SSH uses your existing keys and known host entry.
The app starts its tunnel only on **Connect**, and closes its owned process on
**Disconnect** or **Quit**. Remote agent work continues after disconnection.

The GUI itself has no Python dependency. Its guided setup command and the coding
harnesses use the installed Python companion. Brand sources live in
[`assets/brand`](../assets/brand/).
