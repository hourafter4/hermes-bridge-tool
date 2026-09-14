# Choose the interface that owns the work

Hermes Bridge Tool wraps existing interfaces. It does not install another API
server. Its 29 MCP tools include routing and connection recovery, Gateway tools,
WebUI tools, an optional native MCP client, and the optional CLI observer.

Agents should call **`hermes_backends` first**. It reports which interfaces are
configured and explains their purpose without connecting or executing work.
Then check the intended backend. Configuration is not proof of connectivity.
The same routing rules are supplied in MCP server instructions and individual
tool descriptions, so agents do not need this checkout to discover them.

**Check the reported security policy before planning writes.** Default monitor
mode permits reads and recovery, including for existing configurations with no
explicit policy. Task mutations require the operator to run `hermes-bridge-tool
security mode control` in their own terminal and type `ENABLE`. Platform message
delivery additionally requires `security messages on`. Do not enable your own
permissions or route around a denial. Remote approval responses are permanently
disabled. A security lock blocks future upstream access and reconnects; it can
only be lifted locally. See [Security](SECURITY.md).

| User's intention | Interface and tools | Identifier |
| --- | --- | --- |
| Diagnose a connection or restore access after a disconnect | `hermes_connection_status`, `hermes_reconnect` | Backend name: `all`, `gateway`, `webui`, or `native` |
| Work in a chat shown in nesquena/hermes-webui | `hermes_webui_check`, `hermes_webui_sessions`, `hermes_webui_session` | WebUI `session_id` |
| Start or continue browser chat work | `hermes_webui_new_chat`, `hermes_webui_send` | Save returned `stream_id` |
| Monitor/control a browser-started task | `hermes_webui_session_status`, `hermes_webui_status`, `hermes_webui_wait`, `hermes_webui_steer`, `hermes_webui_stop` | WebUI `stream_id`; steering uses `session_id` |
| Delegate a task through Hermes Gateway | `hermes_check`, `hermes_send`, `hermes_status`, `hermes_wait`, `hermes_steer`, `hermes_stop` | Gateway `run_id`; retain submission `request_id` |
| Browse Gateway profile history | `hermes_sessions`, `hermes_session`, `hermes_messages`, `hermes_new_chat`, `hermes_watch_session` | Gateway `session_id` |
| Read connected platform conversations/events | `hermes_native_tools`, then `hermes_native_read` | Native `session_key` / event cursor |
| Deliver an authorized message to Telegram/Discord/Slack | `hermes_native_write` with the discovered native tool schema | Platform target |
| Track an independently started CLI turn with the observer loaded | `hermes_turns`, `hermes_wait_turn` | Observer `turn_id` plus session ID |

**Do not silently switch backends after an error.** They can share persisted
history while owning different running agents. Submitting through another API
can start duplicate work. WebUI stream IDs, Gateway run IDs, native session keys,
and observer turn IDs are not interchangeable.

Continuing an eligible saved CLI transcript starts a new WebUI or Gateway turn.
It does not inject keyboard input into the original terminal process. Imported
WebUI sessions may be read-only; respect their metadata and server rejection.

## WebUI: browser conversations and live tasks

This adapter targets [nesquena/hermes-webui](https://github.com/nesquena/hermes-webui),
whose default execution runs inside its WebUI server. Other products called a
Hermes dashboard may expose different APIs. Check the returned API shape before
using this backend; Gateway readiness does not establish WebUI readiness.

For an existing HTTPS deployment (skip `./install.sh` if already installed from
a release):

```sh
./install.sh --no-setup
hermes-bridge-tool configure-webui --url https://your-webui.example
hermes-bridge-tool register both
hermes-bridge-tool doctor --backend webui
```

The command privately prompts for the **Cookie header value** from an authenticated
WebUI browser request (browser developer tools → Network → request headers).
Use the complete cookie value, without the `Cookie:` header name. Cookies can
expire; rerun the command to refresh them. A Gateway bearer key does not replace
the WebUI's authentication. The client does not automate browser login.

For deployments requiring proxy authentication or multiple headers, prepare a
private JSON file containing the exact headers your deployment accepts:

```json
{
  "Cookie": "actual-cookie-name=actual-session-value",
  "CF-Access-Client-Id": "your-configured-service-token-id",
  "CF-Access-Client-Secret": "your-configured-service-token-secret"
}
```

Include only the headers your deployment requires. Cloudflare service tokens are
an example, not a built-in requirement; they must already be authorized by your
proxy. Import the file without putting credentials in shell arguments:

```sh
hermes-bridge-tool configure-webui --url https://your-webui.example \
  --auth-file /private/path/webui-headers.json
```

The tool validates and saves headers in the selected credential store: private
files with mode `600`, or macOS Keychain after explicit migration. It does
not follow redirects or disable TLS verification. Reverse-proxy path prefixes
are supported, e.g. `https://your-host.example/hermes`.

If the WebUI is only available inside the server:

```sh
hermes-bridge-tool configure-webui --ssh --host hermes-server --remote-port 8787
hermes-bridge-tool connect
```

This reaches remote loopback port `8787` through a private local Unix socket
and uses the same authentication prompt. The old `18787` local-port setting is
retained for configuration compatibility; no TCP listener is opened there. An intentionally unauthenticated loopback server can use an auth file
containing `{}`. No server configuration changes or restarts occur. Run
`hermes-bridge-tool reconnect` to load the extra forward. Direct HTTPS
needs no tunnel for that backend; another configured backend may still use SSH.

Example agent workflow, after the operator has enabled control mode:

```text
hermes_backends()
hermes_webui_check()
hermes_webui_sessions()
hermes_webui_session(session_id="…")
hermes_webui_send(session_id="…", instructions="Review today's service logs.")
hermes_webui_wait(stream_id="<returned stream_id>")
hermes_webui_session(session_id="…")
```

For a new chat, call `hermes_webui_new_chat()` and pass its `session.session_id`
to `hermes_webui_send`. To locate a browser-started live task, use
`hermes_webui_session_status(session_id="…")` and keep its `active_stream_id`.

Waits last at most 45 seconds per call. `terminal=true` means the WebUI journal
recorded an end; it does **not** by itself prove successful work. WebUI can record
transport closure as `completed` without a semantic result, so the wrapper keeps
`completion: "unknown"` in that case. Read the transcript to assess the result.
An inactive/missing stream with no journal evidence also remains unknown.

Steering uses the session ID and can return `accepted=false` even with HTTP 200.
Do not automatically stop or submit a replacement task after rejection.
Cancellation uses the upstream **mutating GET** endpoint and is marked as a write
tool. Keep monitoring after cancellation acknowledgement. Submission and steering
have no idempotency guarantee; after an uncertain response inspect the session
before deciding to send again.

## Gateway: API-managed agent tasks

Keep the existing pairing workflow when you want the Gateway Runs API:

```sh
hermes-bridge-tool setup --host hermes-server --remote-user hermes --client both
```

Unlike configuring an existing WebUI URL, pairing enables the Gateway API and
restarts its service. Custom watchdogs need the existing restart command; see
[setup instructions](SETUP.md#custom-services-and-manual-setup).

If this API is already exposed through authenticated HTTPS:

```sh
hermes-bridge-tool configure --url https://your-gateway.example
hermes-bridge-tool doctor --backend gateway
```

Enter its existing bearer key at the private prompt. `--keep-key` preserves a
previously saved key without prompting. `configure --url '' --keep-key` restores
the normal SSH connection. Merely exposing the WebUI does not expose Gateway's
separate `/v1/runs` endpoints.

See [Gateway sessions and runs](SESSIONS.md) for idempotency, message pagination,
run monitoring, and observer details.

## Native MCP: platform conversations and messaging

Hermes already includes `hermes mcp serve`. This tool can keep one persistent
stdio connection to it and wrap its installed tool schemas. Native MCP does not
require the Gateway HTTP API or WebUI, but it must run in the correct Hermes user
and profile environment.

When Hermes runs locally:

```sh
hermes-bridge-tool configure-native -- hermes mcp serve
```

For a remote server, provision a separate forced-command key using your existing
administrator alias. The native command must be the last option:

```sh
hermes-bridge-tool harden-ssh --admin-host my-admin-alias --hermes-user hermes \
  --native-command /home/hermes/.local/bin/hermes mcp serve
hermes-bridge-tool reconnect
```

This explicitly changes server SSH authorization, creates a non-root account for
HTTP forwarding, and gives native MCP a different restricted key under the
Hermes account. It preserves administrative login credentials. Adapt the alias,
absolute executable path, and user; see [runtime SSH setup](SETUP.md#restrict-everyday-ssh-access).
Restart coding clients to close their older native SSH processes. Advanced
`configure-native` commands remain argv arrays; do not interpolate chat contents
or put secrets into them. A custom unrestricted command does not gain the
provisioner's restrictions automatically.

If the bridge's tools are already available, reconnect its native client after
changing this command. Otherwise restart your coding client to load the tools:

```text
hermes_reconnect(backend="native")
hermes_native_tools()
hermes_native_read(tool_name="channels_list", arguments={})
hermes_native_read(tool_name="conversations_list", arguments={"limit": 20})
```

Discovery returns exact upstream descriptions and JSON argument schemas. The read
wrapper permits `conversations_list`, `conversation_get`, `messages_read`,
`attachments_fetch`, `events_poll`, `events_wait`, `channels_list`, and
`permissions_list_open`. The write wrapper permits only `messages_send`, with both control mode and the
separate messaging grant. `permissions_respond` is denied even if the upstream
server advertises it. Availability depends on the installed Hermes version.

**`messages_send` delivers to a platform recipient; it does not prompt Hermes to
execute a task.** Require the user's authorization to message that recipient.
Resolve approval requests directly in Hermes; the bridge cannot answer them. Read `isError` and returned
content instead of assuming that a completed MCP call succeeded.

Calls share a persistent connection because native event cursors and observed
approvals have process-local state. `connection_id` changes after reconnection;
old cursors and approval observations must then be rediscovered. Only one native
call runs at a time; a concurrent call reports busy without submitting. Event
waits are capped at 30 seconds. Native tools expose upstream content blocks, and
attachment paths can refer to the server rather than your laptop.

## CLI observer: optional lifecycle evidence

The observer remains useful for CLI processes started independently of either
HTTP API. Install it only when that monitoring is needed. Existing CLI processes
must restart to load its hooks. Missing, crashed, or pruned observations remain
unknown; the observer cannot control terminal input. It uses the existing
Gateway listener, adding no daemon or public port.

## Connection checks and configuration

```sh
hermes-bridge-tool connection-status
hermes-bridge-tool doctor --backend all
```

`connection-status` checks the owned SSH transport and probes configured HTTP
backends without changing the connection. `doctor` checks configured HTTP
backends independently and reports combined readiness.
Unconfigured HTTP backends are skipped. It does not execute model work, prove a
provider is healthy, or test native MCP; use `hermes_native_tools` for that.
The menu bar app and CLI use the same connection manager for SSH forwards. Its
Settings window edits Gateway SSH settings; configure WebUI/direct endpoints in
the CLI. The menu bar remains icon-only.

## Recover access without resubmitting work

The bridge advertises its tools even when a remote backend is unavailable. If an
API call fails but MCP tools remain available, agents should inspect
`hermes_connection_status()`, then reconnect the backend that owns the task:

```text
hermes_connection_status()
hermes_reconnect(backend="webui")
hermes_webui_status(stream_id="<the saved stream ID>")
```

The status tool also reports the current MCP process's native connection metadata,
without starting a native child process to check it.

Use `backend="gateway"` followed by `hermes_status(run_id="…")` for a Gateway
task, or `backend="all"` when recovering all configured connections. Reconnection
reloads saved configuration, restores the managed SSH transport where needed,
and checks access. Direct HTTPS backends are checked without opening a tunnel.
It does not restart server services, refresh expired credentials, approve requests,
or replay prompts. Read the recovery report: an authentication failure needs the
correct backend's credentials; an unavailable remote service needs attention on
the server. Neither means a previously submitted task stopped.

For native MCP, `hermes_reconnect(backend="native")` replaces the local upstream
client in the calling coding client's MCP process and checks discovery. Other
clients' native connections are unaffected. It refuses to reset a busy native write. Once the
call settles, inspect the outcome before deciding whether to retry a message.
A changed `connection_id` invalidates old native event cursors and approval
observations; call `hermes_native_tools` and rediscover them.

If the coding harness cannot start the bridge MCP process at all, its recovery
tools cannot run. Use the installed CLI from a terminal or the agent's shell:

```sh
hermes-bridge-tool reconnect
```

Then reconnect the bridge in the coding harness: use Claude Code's `/mcp` menu,
or reconnect the MCP server in Codex where available and otherwise restart the
client. Use the executable's absolute installed path if it is not on `PATH`.
Recovering a backend does not replace a missing MCP registration or installation.

`hermes-bridge-tool connect` starts or reuses a shared managed SSH connection;
`reconnect` refreshes it. The CLI connection commands manage and check HTTP
backends; they do not reset native children owned by running coding clients.
The legacy `tunnel` command is now an alias for `connect`. HTTP requests in SSH
mode use private Unix sockets owned by this bridge. They never fall back to
loopback TCP listeners or manually opened tunnels. Other local TCP processes
cannot receive bridge credentials by occupying the former forwarding ports.
The tunnel stays alive when the menu bar app quits or an
MCP process stops. **`hermes-bridge-tool disconnect` or the app's Disconnect action
closes the shared tunnel for all local clients.** This affects access, never the
remote agent's accepted work. Reconnecting a shared tunnel may briefly interrupt
other clients' reads; continue with saved task IDs after it returns.

Shared settings are in `~/.config/hermes-bridge-tool/config.json`:

| Setting | Meaning |
| --- | --- |
| `gateway_url` | Existing HTTPS Gateway base URL; empty uses a private SSH Unix socket |
| `api_key_file` | Gateway file credential path; unused when Keychain is selected |
| `webui_url` | Existing WebUI base URL; empty disables WebUI unless `webui_ssh` is true |
| `webui_auth_file` | WebUI file credential path; unused when Keychain is selected |
| `webui_ssh` | Include WebUI in the managed SSH tunnel |
| `webui_local_port`, `webui_remote_port` | Legacy local URL marker / actual remote port; defaults 18787 / 8787 |
| `ssh_user`, `ssh_identity_file` | Restricted runtime login/key, overriding the SSH alias defaults |
| `gateway_credential_store`, `webui_credential_store` | `file` or macOS `keychain` |
| `security` | Local monitor/control, message-delivery, and lock policy |
| `native_mcp_command` | Optional executable-and-arguments array for native stdio MCP |

Environment overrides: `HERMES_API_URL`, `HERMES_API_KEY_FILE`, `HERMES_API_KEY`,
`HERMES_WEBUI_URL`, `HERMES_WEBUI_AUTH_FILE`, and `HERMES_NATIVE_MCP_COMMAND`
(JSON argv). Prefer shared settings for GUI clients. Remote HTTP URLs must use
HTTPS. Loopback HTTP URLs are accepted only when they map to the configured
managed SSH endpoint; arbitrary local HTTP listeners are refused. Authentication for each backend is
separate and existing settings are preserved when adding another backend.

Sources: [Hermes Gateway API](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/),
[Hermes native MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp/#running-hermes-as-an-mcp-server),
[WebUI API architecture](https://github.com/nesquena/hermes-webui/blob/main/ARCHITECTURE.md).
