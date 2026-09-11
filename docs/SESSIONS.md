# Sessions and monitoring

Hermes Bridge exposes conversations saved in the connected Hermes gateway's
active profile. That can include Hermes CLI, Hermes web UI, and API sessions
sharing the same database. It can read those conversations and start new turns
using their transcripts. An optional server plugin observes CLI and web UI turn
lifecycle events. The 13 tools are available in Codex and Claude Code; the menu
bar companion manages the SSH connection.

## Find and continue a conversation

Ask your coding client:

> List my recent Hermes chats, find the CLI conversation about backups, and show
> me its latest messages. Then ask Hermes in that chat to summarize the next step.

The tool sequence is:

1. `hermes_check()` to verify connectivity and capabilities.
2. `hermes_sessions()` to discover saved sessions and their actual source labels.
3. `hermes_session(session_id="…")` for metadata, or
   `hermes_messages(session_id="…")` for the latest 50 messages.
4. `hermes_send(instructions="Summarize the next step.", session_id="…")` to
   start a turn in that conversation.
5. `hermes_wait(run_id="…")` to check for completion and retrieve output.

Use the **returned** `session_id` from the message page for the next turn. Hermes
can resolve an older session to its resumed or compacted descendant. A session ID
identifies a conversation; a run ID identifies one execution. They are not
interchangeable.

`hermes_sessions` accepts `source`, `limit` (1–200), `offset`, and
`include_children`. Omit `source` first: labels such as `cli`, `hermes_browser`,
and `api_server` depend on how the conversation was created. Use an exact
returned label to filter. Follow `has_more` by increasing the offset; pinned
sessions can reappear on successive pages.

`hermes_messages` accepts `limit` (1–500), `offset`, and `order`. Its default
`order="latest"` selects the newest window; `order="oldest"` starts at the
beginning. Messages within each window are chronological and can include tool
calls and results. Increase the offset to read further pages.

## Start a new chat

For a new conversation with its first message:

```text
hermes_send(instructions="Check disk usage and report the largest directories.")
```

Omitting `session_id` creates a fresh conversation when Hermes runs the task.
Keep the returned `run_id` and `request_id`. Read `hermes_status` or `hermes_wait`
for the conversation's `session_id`; some gateways omit it from submission.

To create an empty, named chat first:

```text
hermes_new_chat(title="Server maintenance")
hermes_send(instructions="Check disk usage.", session_id="<returned session.id>")
```

Creating the empty chat does not execute an agent task. Chat creation is not
idempotent: if its response is lost, inspect the session list before creating
another. Task submission supports safe retries with the same `request_id` and
identical inputs.

## Wait for completion

Use `hermes_status(run_id="…")` for one status request or
`hermes_wait(run_id="…")` to poll. Waits default to 20 seconds and accept
`timeout_seconds` from 0 to 45. Zero requests one snapshot. The polling interval
defaults to 2 seconds and can be set from 0.2 to 5 seconds.

The wait result includes the gateway status and:

| Field | Meaning |
| --- | --- |
| `terminal: true` | Status is `completed`, `failed`, or `cancelled`. |
| `terminal: false` | Completion has not been established. |
| `wait_timed_out: true` | The local wait ended; remote work may still be running. |

Call `hermes_wait` again to continue monitoring. Approval and other attention
states return immediately with `terminal: false`; resolve approvals in Hermes.
`stopping` is still active. After `hermes_stop`, keep checking until the run
reaches a terminal state. Losing the connection or ending a local wait does not
cancel remote work.

Known web UI/API run IDs also work when that gateway registered them in its Runs
API. Hermes does not expose a public session-to-active-run lookup, so the bridge
cannot recover an arbitrary live CLI run from a session ID. Save run IDs when
available, and save final output promptly because completed run records expire.
The lookup limitation follows from the reviewed
[upstream Runs routes](https://github.com/NousResearch/hermes-agent/blob/0b8daf30aae1d0b129ede9b857cac2158eb50324/gateway/platforms/api_server_runs.py#L91).
The optional observer below tracks separate turn IDs when no run ID is available.

## Observe CLI and web UI turns

Install the bundled observer through the normal pairing flow:

```sh
hermes-bridge setup --observe-sessions
```

For a fresh installation, `./install.sh --observe-sessions` installs locally
and runs this pairing flow in one step. Setup enables the plugin, restarts the
gateway, and checks its authenticated observer endpoint. Reopen existing remote CLI
processes so they load the plugin; already-running processes cannot gain hooks
retroactively. Then discover a conversation with `hermes_sessions` and ask for
its observed turns:

```text
hermes_turns(session_id="…", limit=10)
hermes_wait_turn(session_id="…", turn_id="<observed turn ID>")
```

Track the exact `turn_id` returned by `hermes_turns`. This identifies a lifecycle
observation, not an API `run_id`. `hermes_wait_turn` defaults to a 20-second wait
and a 2-second polling interval, with the same 45-second maximum as `hermes_wait`.
Call again when the local wait ends before a finish event arrives.

Observed terminal states are `completed`, `failed`, `interrupted`, and
`incomplete` (the turn ended without completing its task). A
`started` record proves that the start hook ran; it does not prove the process
is still alive. A process crash, missing finish hook, or plugin-disabled safe
mode can leave completion unknown. The observer cannot recover events from
before it loaded. Read the session's messages separately for the actual reply.

The plugin observes Hermes's
[`pre_llm_call` and `on_session_end` hooks](https://github.com/NousResearch/hermes-agent/blob/0b8daf30aae1d0b129ede9b857cac2158eb50324/website/docs/developer-guide/plugins/index.md#L945)
and serves
`/hermes-bridge/v1/turns` through the existing gateway, using the same API key.
It adds no daemon or port. It stores only sanitized per-turn metadata in a
private SQLite database at `<Hermes home>/plugin-data/hermes-bridge-observer/turns.sqlite3`,
with directory mode `700` and file mode `600`. It retains the latest 2,000 turn
observations, including unfinished ones. Pruned records return `unknown`. It
does not store prompts or conversation history. It uses the upstream
[plugin API route extension](https://github.com/NousResearch/hermes-agent/blob/0b8daf30aae1d0b129ede9b857cac2158eb50324/website/docs/developer-guide/plugins/index.md#L1298).

## Watch an existing CLI or web UI chat

Use this when you have a session ID and want to see persisted conversation
updates:

```text
hermes_watch_session(session_id="…")
hermes_watch_session(session_id="…", cursor="<returned cursor>")
```

The first call returns a baseline immediately. Subsequent calls wait for that
session's latest 50-message window to change, using the same timeout and polling
options as `hermes_wait`. Reuse each returned cursor for the next watch. The
result contains `changed`, `cursor`, `wait_timed_out`, and the full message
window under `messages`; it is not a message delta. For a busy conversation,
page through `hermes_messages` to retrieve more history.

The watch result always reports `completion: "unknown"`. An assistant reply,
quiet interval, or session `ended_at`/`end_reason` cannot prove that a particular
turn finished. Watching observes saved messages, not live token streaming or
terminal input. Use a known run ID with `hermes_wait` for authoritative run state.
With the observer installed, use `hermes_turns` and `hermes_wait_turn` to monitor
hook events instead of inferring completion from the message window.
The upstream web UI's
[session activity flag uses a recency heuristic](https://github.com/NousResearch/hermes-agent/blob/0b8daf30aae1d0b129ede9b857cac2158eb50324/hermes_cli/web_routers/sessions.py#L124),
which the bridge does not treat as completion evidence.

## Guide a running task

When `hermes_check` reports `features.run_steer`, send additional guidance to a
known active run:

```text
hermes_steer(run_id="…", instructions="Focus on today's logs first.")
```

Hermes delivers guidance at the next tool boundary. Acceptance is not completion;
continue monitoring the same run ID. Steering is not idempotent, so inspect
progress after an uncertain response before deciding whether to send it again.
For a new turn after the current task, use `hermes_send` with the session ID.
See the reviewed
[upstream steering handler](https://github.com/NousResearch/hermes-agent/blob/0b8daf30aae1d0b129ede9b857cac2158eb50324/gateway/platforms/api_server_runs.py#L823)
for its active-agent requirement.

## Scope and compatibility

- Session tools require the gateway's `features.session_resources`; steering
  requires `features.run_steer`. `hermes-bridge doctor` reports
  `session_tools_ready` and `steering_ready` separately from base Runs readiness.
- Only the connected remote profile's persisted sessions are visible. CLI chats
  on your laptop, another profile, and a third-party web UI's separate history
  are outside that database.
- Continuing a saved CLI chat starts a new gateway turn using its transcript.
  It does not type into or attach to the running terminal process. The bridge
  needs a tracked API run or an observer finish event to report turn completion.
- The observer is optional. `hermes_turns` and `hermes_wait_turn` require its
  server routes; the other 11 tools use the gateway's native APIs.
- Session contents are conversation data. Send local file contents explicitly
  when the remote task needs them; laptop paths are not accessible to Hermes.

For endpoint behavior and server configuration, see the
[official Hermes API documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/).

After updating this checkout, run `./install.sh --no-setup` and restart your
coding clients to load the new tools. Upgrade the remote Hermes installation
separately if its API lacks the needed capabilities.
