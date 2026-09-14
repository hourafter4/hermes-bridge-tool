"""Client for nesquena/hermes-webui's existing authenticated conversation API.

WebUI stream IDs, gateway run IDs, and observer turn IDs are separate namespaces.
This module does not install server routes or take ownership of terminal processes.
"""

import re
from typing import Literal

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from .config import webui_connection_settings
from .transport import api_client
from .policy import require


async def webui_request(method: str, path: str, *, payload: dict | None = None,
                        params: dict | None = None, mutation: bool = False) -> dict:
    require("tasks" if mutation or method != "GET" else "read")
    try:
        url, headers = webui_connection_settings()
    except ValueError as error:
        raise ToolError(str(error)) from None
    uncertain = (" The action may have been accepted. Do not blindly retry; inspect "
                 "hermes_webui_session_status and hermes_webui_session first.") if mutation else ""
    try:
        async with api_client("webui", url, timeout=30) as client:
            require("tasks" if mutation or method != "GET" else "read")
            response = await client.request(method, url + path, headers=headers, json=payload, params=params)
    except httpx.TimeoutException:
        raise ToolError("WebUI request timed out." + uncertain) from None
    except httpx.HTTPError:
        raise ToolError("Cannot reach the WebUI. Call hermes_connection_status then hermes_reconnect(backend='webui'); read the existing stream/session after recovery." + uncertain) from None
    if not 200 <= response.status_code < 300:
        hints = {
            401: "Refresh the WebUI login cookie or configured proxy credentials; the gateway API key is separate.",
            403: "Check authentication, profile access, and whether this session is read-only.",
            404: "The WebUI endpoint or session was not found. Check the WebUI URL and version.",
            409: "The session may already be running, belong to another profile, or require a runtime update. Check session status.",
        }
        hint = hints.get(response.status_code, "Check WebUI or reverse proxy logs.")
        if 300 <= response.status_code < 400:
            hint = "Redirects are not followed. Configure the final WebUI API base URL and valid authentication."
        raise ToolError(f"WebUI returned HTTP {response.status_code}. {hint}" + (uncertain if response.status_code >= 500 else ""))
    try:
        result = response.json()
    except ValueError:
        raise ToolError("WebUI returned invalid JSON; a login page or incorrect URL may be configured." + uncertain) from None
    if not isinstance(result, dict):
        raise ToolError("WebUI returned an unexpected response; expected a JSON object." + uncertain)
    return result


def _identifier(value: str, name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,512}", value) or value in (".", ".."):
        raise ToolError(f"Invalid {name}. Use an exact ID returned by the WebUI tools.")


async def hermes_webui_check() -> dict:
    """Check the separately configured WebUI API without starting Hermes work.

    Use this backend for chats shown in nesquena/hermes-webui, including its live
    tasks. Gateway connectivity (hermes_check) does not verify this backend.
    Authentication usually uses a WebUI login cookie or reverse proxy credentials.
    This checks session access, not permission to execute or steer tasks.
    """
    result = await webui_request("GET", "/api/sessions")
    if not isinstance(result.get("sessions"), list):
        raise ToolError("Response does not match the supported Hermes WebUI session API. Check the WebUI base URL.")
    return {"backend": "webui", "ready": True, "session_count": len(result["sessions"]),
            "active_profile": result.get("active_profile"), "task_id_kind": "stream_id"}


async def hermes_webui_sessions(limit: int = 20, offset: int = 0,
                                source: Literal["webui", "cli"] | None = None,
                                include_archived: bool = False) -> dict:
    """List the WebUI's saved conversations, respecting its active profile/settings.

    Use for browser chats; source=cli shows CLI conversations visible to the WebUI.
    This does not discover live terminal processes. Read-only imported sessions
    cannot be continued. Pagination is a local window over the server's sidebar
    response, which can change between calls; IDs can repeat when it changes.
    """
    if not 1 <= limit <= 200 or not 0 <= offset <= 1_000_000:
        raise ToolError("limit must be 1–200; offset must be 0–1000000.")
    params = {"include_archived": "1" if include_archived else "0"}
    if source is not None:
        if source not in ("webui", "cli"):
            raise ToolError("source must be webui or cli.")
        params["sidebar_source"] = source
    result = await webui_request("GET", "/api/sessions", params=params)
    sessions = result.get("sessions")
    if not isinstance(sessions, list):
        raise ToolError("WebUI returned an invalid session list.")
    return {"backend": "webui", "sessions": sessions[offset:offset + limit],
            "limit": limit, "offset": offset, "has_more": offset + limit < len(sessions),
            "available_count": len(sessions), "active_profile": result.get("active_profile"),
            "pagination_scope": "returned_sidebar_sessions"}


async def hermes_webui_session(session_id: str, message_limit: int = 50,
                               before: int | None = None, include_messages: bool = True) -> dict:
    """Read a WebUI conversation and recent messages; use this to retrieve final output.

    message_limit counts visible transcript rows (tool rows may also be included).
    For earlier messages pass session._messages_offset as before. Set
    include_messages=false for metadata. Inspect read_only/source fields before
    continuing imported sessions. Saved messages and active_stream_id alone do not
    prove completion; use hermes_webui_status with the exact WebUI stream_id.
    """
    _identifier(session_id, "session_id")
    if not 1 <= message_limit <= 200 or (before is not None and not 0 <= before <= 1_000_000):
        raise ToolError("message_limit must be 1–200; before must be 0–1000000.")
    params = {"session_id": session_id, "messages": "1" if include_messages else "0",
              "msg_limit": message_limit, "resolve_model": "0"}
    if before is not None:
        params["msg_before"] = before
    return {**await webui_request("GET", "/api/session", params=params), "backend": "webui"}


async def hermes_webui_session_status(session_id: str) -> dict:
    """Discover the active WebUI stream_id for an existing browser conversation.

    Pass active_stream_id to hermes_webui_status/wait/stop. agent_running=false or
    a missing stream ID means no known live WebUI stream, not proof a task succeeded.
    This does not report independently running CLI processes.
    """
    _identifier(session_id, "session_id")
    return {**await webui_request("GET", "/api/session/status", params={"session_id": session_id}),
            "backend": "webui", "completion": "unknown"}


async def hermes_webui_new_chat(workspace: str | None = None) -> dict:
    """Create an empty WebUI chat, then pass session.session_id to hermes_webui_send.

    workspace is a directory on the server; omit it to use the WebUI default.
    Creation is not idempotent. After an uncertain response inspect sessions before
    retrying. This does not start model work or create a Git worktree.
    """
    payload: dict = {"worktree": False}
    if workspace is not None:
        if not workspace.strip():
            raise ToolError("workspace must not be blank.")
        payload["workspace"] = workspace
    return {**await webui_request("POST", "/api/session/new", payload=payload, mutation=True), "backend": "webui"}


async def hermes_webui_send(session_id: str, instructions: str) -> dict:
    """Start a Hermes turn in an existing WebUI chat, with remote tools/permissions.

    First create a chat with hermes_webui_new_chat or choose an existing writable
    session. Returns stream_id: save it and use hermes_webui_wait/status, then read
    hermes_webui_session for the answer. WebUI stream_id is NOT gateway run_id.
    For an already running browser task use hermes_webui_steer instead. Continuing
    an imported CLI transcript creates WebUI-owned work; it does not type into the
    live terminal. Local files must be sent as contents. No idempotency guarantee:
    do not blindly retry after timeout, or fall back to gateway submission.
    """
    _identifier(session_id, "session_id")
    if not instructions.strip():
        raise ToolError("instructions must not be blank.")
    result = await webui_request("POST", "/api/chat/start", payload={"session_id": session_id, "message": instructions}, mutation=True)
    return {**result, "backend": "webui", "submission_confirmed": bool(result.get("stream_id"))}


async def hermes_webui_status(stream_id: str) -> dict:
    """Read lifecycle evidence for an exact WebUI stream_id, including browser-started work.

    Use hermes_webui_session_status to discover a live stream. terminal=true means
    the journal recorded an end, not necessarily success: inspect status. An absent
    stream, lost worker, or transport closure without a semantic result has unknown
    completion. Journal run_id is a WebUI identifier, not a gateway API run_id.
    Read the session transcript for output; never treat approval waits as consent.
    """
    _identifier(stream_id, "stream_id")
    result = await webui_request("GET", "/api/chat/stream/status", params={"stream_id": stream_id})
    journal = result.get("journal") if isinstance(result.get("journal"), dict) else {}
    # WebUI calls stream_end terminal/completed even when no semantic done event
    # exists. Preserve that evidence but do not promote it to successful task work.
    state = journal.get("terminal_state")
    terminal = journal.get("terminal") is True
    completion = "unknown"
    if terminal and state == "completed" and journal.get("last_event") == "done":
        completion = "completed"
    elif terminal and state in {"errored", "tool_limit_reached", "interrupted-by-user", "interrupted-by-crash"}:
        completion = "failed" if state in {"errored", "tool_limit_reached"} else "interrupted"
    return {**result, "backend": "webui", "terminal": terminal,
            "status": state or ("running" if result.get("active") is True else "unknown"),
            "completion": completion}


async def hermes_webui_wait(stream_id: str, timeout_seconds: float = 20,
                            poll_interval_seconds: float = 2) -> dict:
    """Wait up to 45 seconds for a WebUI stream's journal to record its end.

    Keep WebUI stream_id separate from gateway run_id and observer turn_id. A local
    timeout does not cancel work. terminal=true can mean failure/interruption; use
    completion/status and read hermes_webui_session for output. Missing journal
    evidence remains unknown, even when active=false. Call again to keep waiting.
    """
    from .server import poll_until
    _identifier(stream_id, "stream_id")
    result, timed_out = await poll_until(lambda: hermes_webui_status(stream_id),
                                         lambda item: item["terminal"],
                                         timeout_seconds, poll_interval_seconds)
    return {**result, "wait_timed_out": timed_out}


async def hermes_webui_steer(session_id: str, instructions: str) -> dict:
    """Add guidance to the WebUI-owned task currently running in a conversation.

    Uses session_id, unlike gateway hermes_steer which needs run_id. Check returned
    accepted: HTTP success can still mean steering was rejected. Do not automatically
    stop, queue, resubmit, or switch backends after a rejection. This cannot steer an
    independently running CLI process. Guidance may affect remote tool actions.
    """
    _identifier(session_id, "session_id")
    if not instructions.strip():
        raise ToolError("instructions must not be blank.")
    return {**await webui_request("POST", "/api/chat/steer", payload={"session_id": session_id, "text": instructions}, mutation=True), "backend": "webui"}


async def hermes_webui_stop(stream_id: str) -> dict:
    """Request cancellation of an exact WebUI stream_id (a mutating operation).

    This uses the WebUI's existing GET cancel endpoint; it is NOT a read-only check.
    Cancellation acknowledgement is not proof the worker stopped: check status/wait.
    It cannot undo completed remote actions or stop an independent terminal session.
    """
    _identifier(stream_id, "stream_id")
    return {**await webui_request("GET", "/api/chat/cancel", params={"stream_id": stream_id}, mutation=True), "backend": "webui"}


def register_webui_tools(mcp: FastMCP) -> None:
    reads = (hermes_webui_check, hermes_webui_sessions, hermes_webui_session,
             hermes_webui_session_status, hermes_webui_status, hermes_webui_wait)
    for function in reads:
        mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))(function)
    for function in (hermes_webui_new_chat, hermes_webui_send, hermes_webui_steer, hermes_webui_stop):
        mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=function != hermes_webui_new_chat, idempotentHint=False))(function)
