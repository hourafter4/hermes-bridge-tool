# macOS menu bar app

Requires macOS 13+ and Xcode Command Line Tools. Build for the current Mac:

```sh
./scripts/build-macos.sh
open "dist/Hermes Bridge.app"
```

The build produces a local, ad hoc signed app and runs a no-network self-test.
The app is not installed or launched by the build script. Distribution to other
people will require normal Apple signing and notarization.

Click **Hermes ○** in the menu bar, choose **Settings…**, and enter your existing
SSH alias, ports, and Hermes API key. The defaults are SSH host `hetzner`, local
port `18642`, and remote API port `8642`. Leave the key blank to preserve it.
Choose **Connect** to open the tunnel. **Hermes ●** means Hermes reports all three
required run capabilities. Connection and API errors appear in the menu.

The app uses `~/.config/hermes-bridge/config.json` and stores the API key separately
in `~/.config/hermes-bridge/api-key`, both with mode `0600`. The optional config
field `api_key_file` changes the key path. Unknown config fields are preserved.
`HERMES_BRIDGE_CONFIG` changes the config path when launching the binary directly.
The app shares these settings with the MCP bridge; restart a harness's MCP
connection after changing its port or credentials.

SSH must already work noninteractively with your keys and known host entry.
The app never accepts unknown host keys automatically and starts SSH only when
you click **Connect**. **Disconnect** and **Quit** terminate only the SSH process
this app started. A separate existing tunnel on the local port causes an SSH
error; the app does not stop it. API health is checked every ten seconds.

The GUI has no Python dependency. Codex and Claude Code use the separate Python
MCP package while this app manages the shared tunnel.
