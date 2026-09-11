Connect Codex, Claude Code, and other MCP clients to your existing Hermes installation.

### Install on macOS 13 or later

1. Download **hermes-bridge-tool-macos-universal.zip** below and extract it.
2. Open **Install.command** inside the **Hermes Bridge Tool** folder.
3. Open **Hermes Bridge Tool** from your user Applications folder.

The same download supports Apple Silicon and Intel Macs. The installer installs
the CLI and prebuilt app for your user; no Xcode, source checkout, or system
Python is required. It offers to install `uv` when needed and uses it to obtain
Python 3.12 and the CLI dependencies. An internet connection is required.

The app is ad hoc signed and **not Apple-notarized**. If macOS blocks opening it,
use **System Settings → Privacy & Security → Open Anyway** for this download.
See [Apple's instructions](https://support.apple.com/guide/mac-help/mh40616/mac).

### Install the CLI on Linux or macOS

Download **hermes-bridge-tool-cli.tar.gz**, extract it, and run:

```sh
cd hermes-bridge-tool-cli
sh install.sh
```

If you already have `uv`, you can instead download the wheel and run:

```sh
uv tool install --python 3.12 ./hermes_bridge_tool-0.1.0-py3-none-any.whl
```

### Connect Hermes

Use the CLI path printed by the installer if `hermes-bridge-tool` is not yet on
PATH. Pick the interface you actually use:

```sh
# Existing WebUI over HTTPS; privately prompts for its login cookie
hermes-bridge-tool configure-webui --url https://your-webui.example

# Or pair the Gateway API over an existing SSH alias
hermes-bridge-tool setup --host your-ssh-alias --remote-user hermes --client both

# Register an already configured connection
hermes-bridge-tool register both
```

Gateway pairing configures and restarts the remote Gateway; connecting an existing
WebUI does not. SSH backends use the app's Connect button or `hermes-bridge-tool tunnel`.
Restart your coding client, then ask it to call **hermes_backends**.

The 27 tools cover WebUI chats and tasks, Gateway runs, native Hermes messaging
MCP, and optional CLI turn observation. Each family explains when to use it and
which identifiers establish task state. Full instructions are in
[the backend guide](https://github.com/hourafter4/hermes-bridge-tool/blob/v0.1.0/docs/BACKENDS.md).

Existing credentials and connection settings are preserved during updates.
Quit and reopen a running older app after installing. These packages do not
install Hermes on the server. `SHA256SUMS` accompanies the downloads.
