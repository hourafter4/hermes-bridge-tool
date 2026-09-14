"""Recovery tools are registered locally and remain usable when Hermes is offline."""

import asyncio
from typing import Literal

from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import connection, policy
from .native import native_command, native_proxy


def native_status():
    try:
        configured = native_command() is not None
    except ToolError:
        return {"configured": True, "ready": False, "problem": "configuration"}
    connected = native_proxy.task is not None and not native_proxy.task.done()
    return {"configured": configured, "ready": connected, "busy": native_proxy.busy,
            "connection_id": native_proxy.connection_id if connected else None,
            "skipped": not configured,
            "hint": "Local child state only; hermes_native_tools verifies the upstream connection."}


async def http_status():
    if policy.status()["locked"]:
        return {"ready": False, "problem": "security_locked", "security": policy.status(),
                "hint": "Locked: no remote health probes were made. The operator must unlock locally."}
    try:
        return await asyncio.wait_for(connection.connection_status(), timeout=10)
    except (ValueError, OSError, asyncio.TimeoutError):
        return {"ready": False, "problem": "local_connection",
                "hint": "Check private configuration, SSH availability and the local connection manager."}


def register_recovery_tools(mcp):
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
    async def hermes_connection_status() -> dict:
        """Diagnose local tunnel and authenticated Gateway/WebUI connectivity, even if Hermes is down.

        Makes read-only API probes; never opens a tunnel or starts native MCP.
        Native state is local metadata, not a remote health check. For transport
        failure call hermes_reconnect for the affected backend, then read the
        existing run/stream ID. Authentication failures need refreshed credentials.
        If this local MCP server itself is unavailable, use the shell command
        hermes-bridge-tool reconnect and reconnect the coding client's MCP server.
        """
        return {**await http_status(), "native": native_status(), "security": policy.status()}

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
    async def hermes_reconnect(backend: Literal["all", "gateway", "webui", "native"] = "all") -> dict:
        """Restore a configured local connection without restarting Hermes or replaying tasks.

        Choose the backend owning the failed task. Gateway/WebUI share an owned
        SSH tunnel with the menu bar app and other coding clients; repairing it
        can briefly interrupt both. Healthy tunnels and unrelated listeners are
        preserved. Direct HTTPS is checked only. Does not log in, change keys,
        approve actions, or send messages. Read known task IDs after recovery.
        Native resets only this MCP client's idle child and discovers its tools;
        a busy call is rejected. Discard old native event cursors and rediscover
        approvals when connection_id changes. 'all' skips unconfigured backends.
        If tools themselves are absent, run hermes-bridge-tool reconnect in the
        shell, then reconnect the coding harness's MCP connection.
        """
        policy.require("reconnect")
        async def recover():
            reports = {}
            if backend != "native":
                report = await http_status()
                names = ("gateway", "webui") if backend == "all" else (backend,)
                selected = [report.get(name, {}) for name in names]
                if any(item.get("uses_ssh") and (item.get("problem") == "transport" or report.get("settings_match") is False) for item in selected):
                    try:
                        report = await connection.connect(restart=True)
                    except (ValueError, OSError, asyncio.TimeoutError):
                        report = {"ready": False, "problem": "local_connection",
                                  "hint": "Connection recovery failed. Check SSH keys, verified host, configuration, and concurrent connection operations."}
                reports["http"] = report
                for name in names:
                    reports[name] = report.get(name, {"configured": False, "ready": False,
                                                    "problem": report.get("problem", "not_configured")})
            if backend in ("all", "native"):
                state = native_status()
                if state["configured"] and state.get("problem") != "configuration":
                    try:
                        result = await asyncio.wait_for(native_proxy.reconnect(), timeout=15)
                        state = {"configured": True, "ready": True, "connection_id": result["connection_id"],
                                 "tools_count": len(result.get("tools", [])),
                                 "hint": "Native cursors and observed approvals were reset. Discover schemas with hermes_native_tools before calling native tools."}
                    except (ToolError, OSError, asyncio.TimeoutError):
                        state = {"configured": True, "ready": False,
                                 "problem": "busy" if native_proxy.busy else "native_connection",
                                 "hint": "Wait for any in-flight native call; otherwise check the configured native command. Never replay writes after disconnect."}
                reports["native"] = state
            names = ("gateway", "webui", "native") if backend == "all" else (backend,)
            active = [reports[name] for name in names if backend != "all" or not reports[name].get("skipped")]
            return {"ready": bool(active) and all(item.get("ready") is True for item in active),
                    **reports, "next": "Read the existing run/stream status. Recovery never resubmits instructions."}
        try:
            return await asyncio.wait_for(recover(), timeout=45)
        except asyncio.TimeoutError:
            return {"ready": False, "problem": "timeout", "hint": "Recovery timed out. Check connection status before retrying; remote work may still be running."}
