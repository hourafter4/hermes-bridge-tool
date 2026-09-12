"""Optional persistent client of Hermes's existing stdio MCP server."""

import asyncio
from contextlib import suppress
from datetime import timedelta
import json
import os
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from .config import config_path


READ_TOOLS = {
    "conversations_list", "conversation_get", "messages_read", "attachments_fetch",
    "events_poll", "events_wait", "channels_list", "permissions_list_open",
}
WRITE_TOOLS = {"messages_send", "permissions_respond"}


def native_command() -> list[str] | None:
    try:
        raw = os.environ.get("HERMES_NATIVE_MCP_COMMAND")
        value = json.loads(raw) if raw is not None else (
            json.loads(config_path().read_text()).get("native_mcp_command") if config_path().exists() else None
        )
        if value is None:
            return None
        if not isinstance(value, list) or not value or any(
            not isinstance(arg, str) or not arg or "\x00" in arg for arg in value
        ):
            raise ValueError
        return value
    except (ValueError, OSError, TypeError, AttributeError):
        raise ToolError("native_mcp_command must be a nonempty JSON array of executable and arguments; never put credentials in it.") from None


class NativeProxy:
    """One owner task keeps SDK cancellation scopes and event cursors alive."""

    def __init__(self):
        self.task = None
        self.queue = None
        self.connection_id = None
        self.busy = False

    async def request(self, name=None, arguments=None):
        command = native_command()
        if command is None:
            raise ToolError("Native Hermes MCP is not configured. Set native_mcp_command in config.json; see docs/BACKENDS.md. WebUI and Gateway tools work independently.")
        if self.busy:
            raise ToolError("Another native MCP call is in progress. Wait for it before making this call; this request was not submitted.")
        self.busy = True
        if self.task is None or self.task.done():
            self.queue = asyncio.Queue()
            self.connection_id = str(uuid4())
            self.task = asyncio.create_task(self._worker(command))
        future = asyncio.get_running_loop().create_future()
        await self.queue.put((future, name, arguments))
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=55)
        except asyncio.TimeoutError:
            future.cancel()
            await self.close()
            raise ToolError("Native MCP timed out. Remote actions may have executed. Do not blindly retry writes; reconnecting resets native event cursors and observed approvals.") from None
        except asyncio.CancelledError:
            future.cancel()
            await self.close()
            raise
        finally:
            self.busy = False

    async def _worker(self, command):
        current = None
        try:
            with open(os.devnull, "w") as errors:
                async with stdio_client(StdioServerParameters(command=command[0], args=command[1:]), errlog=errors) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=45)) as client:
                        await client.initialize()
                        while True:
                            current, name, arguments = await self.queue.get()
                            if current.cancelled():
                                continue
                            if name is None:
                                result = (await client.list_tools()).model_dump(mode="json", exclude_none=True)
                            else:
                                result = (await client.call_tool(name, arguments)).model_dump(mode="json", exclude_none=True)
                            if not current.done():
                                current.set_result({"backend": "native", "connection_id": self.connection_id, **result})
                            current = None
        except (Exception, asyncio.CancelledError):
            # SDK errors and child stderr may contain private command/config data.
            pass
        finally:
            pending = [current] if current is not None else []
            while not self.queue.empty():
                pending.append(self.queue.get_nowait()[0])
            for future in pending:
                if not future.done():
                    future.set_exception(ToolError("Native MCP disconnected or failed. Check its configured command. An action may have executed; do not automatically retry writes. Event cursors reset on reconnection."))

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    async def reconnect(self):
        """Reset only this client's idle connection, then rediscover schemas."""
        if self.busy:
            raise ToolError("Another native MCP call is in progress. Wait for it; no connection was reset and no request was submitted.")
        self.busy = True
        try:
            await self.close()
        finally:
            self.busy = False
        return await self.request()


native_proxy = NativeProxy()


def register_native_tools(mcp):
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
    async def hermes_native_tools() -> dict:
        """Discover native Hermes MCP tools and their exact argument schemas.

        Use for connected platform conversations (Telegram, Discord, Slack),
        channel delivery and observed approvals, rather than WebUI/Gateway agent
        execution. Native MCP must be configured separately. Tools are fetched
        from the installed Hermes version. Keep connection_id: native event
        cursors and observed approvals are scoped to this persistent connection.
        """
        return await native_proxy.request()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
    async def hermes_native_read(tool_name: str, arguments: dict | None = None) -> dict:
        """Call a native read tool using arguments from hermes_native_tools.

        Allowed: conversations_list, conversation_get, messages_read,
        attachments_fetch, events_poll, events_wait, channels_list,
        permissions_list_open. These read platform conversation data/events;
        events are not proof of an agent task completing. events_wait is capped
        at 30 seconds. Preserve returned cursors only while connection_id matches.
        Native isError is forwarded in the response; inspect it before using data.
        """
        if tool_name not in READ_TOOLS:
            raise ToolError("Not an allowed native read tool; discover schemas with hermes_native_tools.")
        args = dict(arguments or {})
        if tool_name == "events_wait":
            timeout = args.get("timeout_ms", 30000)
            if type(timeout) is not int or not 0 <= timeout <= 30000:
                raise ToolError("Native events_wait timeout_ms must be 0–30000.")
            args["timeout_ms"] = timeout
        return await native_proxy.request(tool_name, args)

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False))
    async def hermes_native_write(tool_name: str, arguments: dict) -> dict:
        """Call native messages_send or permissions_respond with discovered arguments.

        messages_send DELIVERS a message to a connected platform/recipient; it
        does not ask the Hermes agent to perform a task. Send only when the user
        authorized messaging that recipient. For agent work use hermes_webui_send
        or hermes_send. permissions_respond changes a pending approval: relay the
        exact request and obtain explicit user authorization for the decision,
        especially allow-always. Never approve merely to unblock a task. Writes
        have no retry guarantee. Inspect native isError and content for success.
        """
        if tool_name not in WRITE_TOOLS:
            raise ToolError("Only messages_send and permissions_respond are native write tools.")
        return await native_proxy.request(tool_name, arguments)
