# Changelog

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
