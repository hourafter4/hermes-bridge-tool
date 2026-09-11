"""Expose the Hermes Runs API as local MCP tools. See docs/SETUP.md."""

import re
from uuid import uuid4

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from .config import connection_settings


mcp = FastMCP(
    "hermes",
    instructions=(
        "Delegate instructions to Hermes on the remote server. Call hermes_check first. "
        "Send actual prompt contents: local files are not accessible to the remote agent. "
        "Store the returned run_id and request_id, then poll hermes_status. "
        "A started run is not a completed task. Reuse an existing session_id only when "
        "continuing that conversation. Relay pending approvals to the user; never "
        "treat submission or a waiting state as approval."
    ),
)


async def api_request(method: str, path: str, *, payload: dict | None = None, request_id: str | None = None) -> dict:
    try:
        url, key = connection_settings()
    except ValueError as error:
        raise ToolError(str(error)) from None
    headers = {"Authorization": f"Bearer {key}"}
    if request_id is not None:
        headers["Idempotency-Key"] = request_id
    try:
        async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
            response = await client.request(method, url + path, headers=headers, json=payload)
    except httpx.TimeoutException:
        raise ToolError("Hermes API timed out. An instruction may still have been accepted; this does not cancel remote work.") from None
    except httpx.HTTPError:
        raise ToolError("Cannot reach the Hermes API. Check the SSH tunnel and Hermes gateway.") from None

    if not 200 <= response.status_code < 300:
        hints = {
            401: "Check that the local API key matches the Hermes gateway key.",
            403: "The Hermes API denied this request.",
            404: "The endpoint or run was not found. Check API capabilities; completed run statuses can expire.",
            409: "The request conflicts with existing state. An idempotency key must use the same inputs on every retry.",
        }
        hint = hints.get(response.status_code, "Check the gateway's logs on the server.")
        # Do not echo error bodies: a proxy or server can reflect credentials in them.
        raise ToolError(f"Hermes API returned HTTP {response.status_code}. {hint}")
    try:
        result = response.json()
    except ValueError:
        raise ToolError("Hermes API returned invalid JSON. Check the forwarded port and gateway version.") from None
    if not isinstance(result, dict):
        raise ToolError("Hermes API returned an unexpected response; expected a JSON object.")
    return result


def validate_run_id(run_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", run_id):
        raise ToolError("Invalid run_id. Supply the ID returned by hermes_send.")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
async def hermes_check() -> dict:
    """Check authenticated API connectivity and supported features without starting agent work.

    Verify features.run_submission and features.run_status before sending instructions.
    """
    return await api_request("GET", "/v1/capabilities")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False))
async def hermes_send(instructions: str, session_id: str | None = None, request_id: str | None = None) -> dict:
    """Submit instructions for Hermes to execute with its remote tools and permissions.

    Returns immediately with a run_id; use hermes_status to retrieve the result.
    Omit session_id for a new conversation, or supply an existing Hermes session ID
    to continue its transcript. Send local file contents explicitly when needed.
    request_id is an optional unique idempotency key. Reuse it with identical inputs
    after an uncertain submission; a new key can execute the instructions again.
    """
    if not instructions.strip():
        raise ToolError("instructions must not be blank.")
    if session_id is not None and not session_id.strip():
        raise ToolError("session_id must not be blank; omit it for a new conversation.")
    if request_id is None:
        request_id = str(uuid4())
    if not re.fullmatch(r"[\x21-\x7e]{1,255}", request_id):
        raise ToolError("request_id must contain 1–255 visible ASCII characters without spaces.")
    payload = {"input": instructions}
    if session_id is not None:
        payload["session_id"] = session_id
    try:
        result = await api_request("POST", "/v1/runs", payload=payload, request_id=request_id)
    except ToolError as error:
        raise ToolError(f"{error} Submission was not confirmed. If retrying, reuse request_id={request_id!r} with identical inputs.") from None
    return {**result, "request_id": request_id}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
async def hermes_status(run_id: str) -> dict:
    """Get a run's state and final output. Poll until completed, failed, or cancelled.

    If waiting for human approval, ask the user to resolve it in Hermes. A connection
    error or timeout does not mean the run stopped. Save final output when received:
    Hermes retains completed run statuses only for a limited period.
    """
    validate_run_id(run_id)
    return await api_request("GET", f"/v1/runs/{run_id}")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True))
async def hermes_stop(run_id: str) -> dict:
    """Request interruption of a Hermes run. This does not undo actions already taken.

    A stopping response is not cancellation confirmation: poll hermes_status until
    the executor exits and the run reaches a terminal state.
    """
    validate_run_id(run_id)
    return await api_request("POST", f"/v1/runs/{run_id}/stop")


if __name__ == "__main__":
    mcp.run(transport="stdio")
