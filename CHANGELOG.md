# Changelog

## Unreleased

- Tint the entire menu bar bridge icon green, yellow, or red for connection status, reducing its width by removing the separate status dot.
- Show the macOS app version and build in Settings.
- Add daily automatic checks for official stable GitHub releases, a Settings toggle, and manual checks in Settings and the menu. Prompt before downloading and installing, then offer an app restart.
- Add `hermes-bridge-tool update` with check-only, JSON output, noninteractive approval, and CLI-only options. Verify release archive provenance with GitHub CLI before extraction and use the existing installer to preserve settings and credentials.
- Upgrades still require coding-client registration and MCP process restarts; the updater does not restart clients or the remote Hermes agent.

## 0.2.1 — refreshed macOS branding

- Unify the app, setup window, README, and project preview artwork around the sage tile and teal bridge.
- Add a dedicated macOS icon with transparent outer spacing and a soft shadow, and a matching rounded tile menu bar template for light and dark appearances.
- Show connection status beside the menu bar icon with an adaptive bridge mark and a separate status dot.
- Preserve existing connection settings, credentials, and security policy.

## 0.2.0 — security hardening

- **Breaking default:** configurations without an explicit security policy now start in monitor mode. Task mutations require an operator-confirmed control grant, and native platform messaging requires a separate grant. Remote approval responses are disabled.
- Check local policy before upstream requests. Add a security lock blocking future reads, writes, and recovery, with lock and mode status in the macOS app. Locking does not revoke credentials or cancel accepted work.
- Send SSH-backed HTTP traffic exclusively through private local Unix sockets. Never authenticate a foreign localhost TCP listener or reuse an unmanaged tunnel. `tunnel` now aliases the managed `connect` command; authenticated direct HTTPS remains supported.
- Add `harden-ssh` to provision a restricted non-root forwarding account and a separate forced-command native MCP key while preserving administrator access.
- Add verified file-to-Keychain credential migration on macOS, optional removal of old plaintext files, and explicit storage selection with no silent plaintext fallback.
- Install exact runtime versions exported from `uv.lock` and enforce artifact hashes before atomically replacing the CLI launcher. Refuse dependency source builds and preserve an existing CLI after installation failure.
- Pin the uv bootstrap version and script hash, plugin lockfile use, build backend, and workflow action revisions. Separate build and publication permissions and attest release downloads with GitHub provenance.
- Support optional Developer ID signing and notarization when the maintainer supplies credentials. This release remains ad hoc signed and is not Apple-notarized.
- **Upgrade:** restart every older app/MCP process, reconnect to replace old TCP transport, and review the new access policy. Existing settings and credentials are preserved; runtime SSH restriction and Keychain migration are explicit operations.

## 0.1.1

- Add `hermes_connection_status` and `hermes_reconnect`, bringing the MCP interface to 29 tools. Agents can diagnose backend failures and restore access before checking an existing task.
- Add `connect`, `reconnect`, `disconnect`, and `connection-status` CLI commands for shared SSH transport and HTTP connection checks.
- Share the managed SSH tunnel between the menu bar app, CLI, and MCP clients. Quitting the app or an MCP process leaves it running; explicit Disconnect closes shared access without stopping remote agent work.
- Add Reconnect to the macOS companion and keep MCP discovery available while remote backends are unavailable.
- Reload saved settings during recovery without restarting server services, renewing credentials, approving requests, or replaying prompts. Native MCP recovery replaces only the calling client's upstream connection and refuses to reset a busy write.
- Document recovery with saved task IDs, native cursor rediscovery, and the difference between an unavailable backend and a stopped coding-client MCP process.
- Upgrade note: quit the old app once before connecting with this version, then reload the coding client's MCP connection to get the new tools. Existing credentials and settings remain in place; no server pairing or observer reinstall is required.

## 0.1.0

- Downloadable universal macOS and CLI installation bundles with checksums; no source checkout or Xcode required.

- Remove logo rendering seams with one continuous SVG silhouette and refreshed PNG assets.
- Use a generic SSH alias in defaults and examples; preserve existing saved connections.
- Make the MCP transport-failure test deterministic on macOS and Linux.
- Wrap the existing WebUI API, Gateway API, native Hermes MCP, and optional CLI observer in one 27-tool MCP client.
- Add backend selection instructions and an agent-visible `hermes_backends` routing tool.
- Browse, continue, monitor, steer, and cancel WebUI-owned chats without adding server routes.
- Support direct HTTPS, reverse-proxy path prefixes, private WebUI cookies/proxy headers, and combined SSH forwarding.
- Preserve native MCP conversation/event state in a persistent subprocess; separate read tools from messaging and approval writes.
- Add `configure-webui`, `configure-native`, and per-backend connection checks.
- Check configured HTTP backends from the icon-only menu bar companion.
- Document identifier ownership, completion uncertainty, authentication, and backend selection.

- Provisional project name: Hermes Bridge Tool, with its own CLI, package, settings, and MCP registration names.

- MCP tools for connection checks, task submission, status, and cancellation.
- Session discovery, metadata, message history, and creation for the connected Hermes profile.
- New and continued chat turns, bounded run waits, and saved-message watching.
- Optional steering of known active API runs, with explicit completion and live CLI limitations.
- Optional server observer plugin and MCP tools for tracking CLI and web UI turn lifecycle events through the existing authenticated gateway.
- Guided SSH pairing with private key transfer and server API configuration.
- Custom gateway restart commands, detection of unsuccessful restart messages, and clearer API readiness errors.
- Per-user local installer and Codex/Claude Code registration.
- Native macOS companion with an icon-only menu bar item, connection settings, and status in the menu and tooltip.
- Editable Hermes Bridge Tool identity, app artwork, and social preview.
- Linux/macOS CI and a draft GitHub release workflow.
