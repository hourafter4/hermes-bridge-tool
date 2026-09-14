"""Local operator policy, checked afresh before every upstream request.

This limits the bridge's MCP surface. It is not a sandbox against a process
that can edit this user's files, run its own SSH command, or control a terminal.
"""

import json

from mcp.server.fastmcp.exceptions import ToolError

from .config import config_path, private_write


def status() -> dict:
    """Return effective permissions without exposing connection secrets."""
    try:
        path = config_path()
        data = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError
        security = data.get("security", {})
        if not isinstance(security, dict):
            raise ValueError
        mode = security.get("mode", "monitor")
        locked = security.get("locked", False)
        messages = security.get("messages", False)
        if mode not in ("monitor", "control") or type(locked) is not bool or type(messages) is not bool:
            raise ValueError
        return {"mode": mode, "locked": locked, "tasks": mode == "control" and not locked,
                "messages": mode == "control" and messages and not locked, "approvals": False}
    except (ValueError, OSError, TypeError):
        return {"mode": "monitor", "locked": True, "tasks": False,
                "messages": False, "approvals": False, "problem": "invalid_security_configuration"}


def require(action: str = "read") -> None:
    """Deny before any transport or remote side effect; no policy caching."""
    state = status()
    if state["locked"]:
        raise ToolError("Hermes Bridge Tool is security locked. No upstream request was sent. The operator must unlock it locally; reconnect cannot unlock it.")
    if action == "approvals":
        raise ToolError("Remote approval responses are disabled in Hermes Bridge Tool. Resolve the approval directly in Hermes; this request was not sent.")
    if action in ("tasks", "messages") and not state[action]:
        instruction = "security mode control" if action == "tasks" else "security messages on (control mode also required)"
        raise ToolError(f"Local security policy denies {action}. This request was not sent. The operator can review and enable access in their own terminal with hermes-bridge-tool {instruction}.")
    if action not in ("read", "tasks", "messages", "reconnect", "approvals"):
        raise ToolError("Unknown bridge permission; no upstream request was sent.")


def update(**changes) -> dict:
    """Persist an operator decision; callers enforce interactive confirmation."""
    if set(changes) - {"mode", "locked", "messages"}:
        raise ValueError("Unknown security setting.")
    path = config_path()
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError
        previous = data.get("security", {})
        if not isinstance(previous, dict):
            raise ValueError
        security = {"mode": previous.get("mode", "monitor"),
                    "locked": previous.get("locked", False), "messages": previous.get("messages", False), **changes}
        if security["mode"] not in ("monitor", "control") or type(security["locked"]) is not bool or type(security["messages"]) is not bool:
            raise ValueError
    except (ValueError, OSError, TypeError):
        raise ValueError("Invalid security configuration; repair the local config before changing permissions.") from None
    # Returning to monitor mode also clears the separate messaging grant.
    if changes.get("mode") == "monitor":
        security["messages"] = False
    data["security"] = security
    private_write(path, json.dumps(data, indent=2) + "\n")
    return status()
