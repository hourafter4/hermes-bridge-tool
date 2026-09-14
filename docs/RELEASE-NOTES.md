Connect Codex, Claude Code, and other MCP clients to your existing Hermes installation.

### 0.2.0: monitoring by default, explicit control

This security release changes the default access policy. Existing configurations
without an explicit policy now permit reads and reconnection; task mutations and
platform messaging are denied before reaching Hermes. To grant task control,
run this in your own interactive terminal and type `ENABLE`:

```sh
hermes-bridge-tool security mode control
```

Platform delivery additionally requires `hermes-bridge-tool security messages on`
and its own confirmation. **Remote approval responses are disabled.** Resolve
those requests directly in Hermes. The app shows the effective mode and adds
**Lock bridge access**. The equivalent `security lock` command blocks future
reads, writes, and reconnects; it does not cancel accepted remote work or revoke
credentials. These permissions apply to all clients sharing your configuration.

SSH-backed HTTP requests now use private local Unix sockets. This closes a
credential-exposure issue where another local process occupying a former TCP
forwarding port could receive credentials during an authenticated probe. There
is no fallback to manual TCP tunnels or arbitrary localhost endpoints. Direct
HTTPS connections remain supported.

The release also adds restricted runtime SSH provisioning and verified macOS
Keychain migration. These are explicit operations; installing the app alone does
not change server accounts or move existing credentials. See
[the security model](https://github.com/hourafter4/hermes-bridge-tool/blob/v0.2.0/docs/SECURITY.md).

### Upgrade from 0.1.x

1. Quit the older menu bar app and stop the coding clients' bridge MCP processes. Older running code does not acquire these protections automatically.
2. Install the complete release bundle and run `hermes-bridge-tool register both` (or `codex` / `claude`) to update older registrations to the stable launcher. Restart the coding clients, reopen the app, and run `hermes-bridge-tool reconnect` to replace the old shared TCP tunnel with private sockets.
3. Check `hermes-bridge-tool security status`. Enable control only if you want the agents to create, send, steer, or stop tasks. Reading existing conversations works in monitor mode.
4. Optionally provision restricted runtime SSH and migrate credentials using the commands below.

Existing settings and credentials are preserved. All 29 MCP tools remain
available for discovery; write execution now follows the local policy. If MCP
itself cannot start, run the installed CLI from a terminal, then reconnect the
coding client's MCP server. A reconnect never replays prompts or unlocks policy.

### Verify and install

Download **hermes-bridge-tool-macos-universal.zip** for macOS 13 or later, or
**hermes-bridge-tool-cli.tar.gz** for the CLI on Linux/macOS. Verify the archive's
GitHub provenance before extracting and running its installer:

```sh
gh attestation verify hermes-bridge-tool-macos-universal.zip --repo hourafter4/hermes-bridge-tool
# Or, for the CLI archive:
gh attestation verify hermes-bridge-tool-cli.tar.gz --repo hourafter4/hermes-bridge-tool
```

Inspect the reported workflow and source commit/tag against this release.
`SHA256SUMS` also accompanies the downloads; checksums alone do not identify a
publisher. Provenance establishes the build source, not an absence of software
vulnerabilities.

On macOS, extract the complete ZIP, open **Install.command**, then open the app
from your user Applications folder. The same download supports Apple Silicon
and Intel; no Xcode or source checkout is needed. For the CLI archive:

```sh
cd hermes-bridge-tool-cli
sh install.sh
```

The installer uses Python 3.12 and exact dependencies exported from the tested
lockfile, verifies their hashes, and only then replaces the CLI launcher. It
offers a version-and-checksum-pinned uv installer if necessary. Internet access
may be needed for Python and dependency wheels. Upgrade by rerunning the bundle
installer, not `uv tool upgrade`. A raw wheel installation does not provide the
bundle's locked-dependency guarantees.

**This release is ad hoc signed and not Apple-notarized.** Optional Developer ID
signing/notarization support is available for future builds when credentials are
provided. See `SIGNING-STATUS.txt` in the macOS bundle. If macOS blocks a download
you have verified and trust, follow
[Apple's Open Anyway instructions](https://support.apple.com/guide/mac-help/mh40616/mac).

### Restrict an existing SSH connection

Use your existing administrator alias to provision restricted everyday keys:

```sh
hermes-bridge-tool harden-ssh --admin-host my-admin-alias --hermes-user hermes
hermes-bridge-tool reconnect
```

If native Hermes MCP is configured or desired, append its absolute command last:

```sh
hermes-bridge-tool harden-ssh --admin-host my-admin-alias --hermes-user hermes \
  --native-command /home/hermes/.local/bin/hermes mcp serve
```

This creates a non-root forwarding account and, when requested, a separate
forced-command key for the Hermes account. Administrator login is preserved.
Restart coding clients afterward to discard older native SSH sessions. Custom
SSH/service configurations may require administrator changes; read
[setup details](https://github.com/hourafter4/hermes-bridge-tool/blob/v0.2.0/docs/SETUP.md#restrict-everyday-ssh-access)
before provisioning.

On macOS, explicitly migrate saved HTTP credentials and remove their previous
plaintext files after verification:

```sh
hermes-bridge-tool credentials migrate --to keychain --remove-files
```

Keychain does not protect against every process acting as your user; the bridge
still needs credentials in memory. Environment overrides and external backups
remain separate. Reading sessions shares their contents with the coding client
and potentially its model provider. This release does not add per-session access
control or general secret redaction.

For a fresh connection, choose the existing WebUI, Gateway, native MCP, or CLI
observer using [the backend guide](https://github.com/hourafter4/hermes-bridge-tool/blob/v0.2.0/docs/BACKENDS.md).
These packages connect to Hermes; they do not install Hermes on the server.
