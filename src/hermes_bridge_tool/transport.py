"""Authenticated HTTP goes through TLS or a private SSH-forwarded Unix socket.

Never send secrets to a public local TCP port. Checking its PID or checking an
SSH master first would leave a race in which another user could bind that port.
The private directory is the boundary; same-user arbitrary code is trusted.
"""

from contextlib import asynccontextmanager
import json
import os
import stat
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp.exceptions import ToolError


class TransportUnavailable(ToolError):
    pass


def socket_for(control, backend):
    if backend not in ("gateway", "webui"):
        raise ValueError("Unknown HTTP backend.")
    return control.with_suffix(f".{backend}.sock")


def expected_metadata(settings):
    return {"host": settings.ssh_host, "forwards": settings.ssh_forwards(),
            "user": settings.ssh_user, "identity_file": settings.ssh_identity_file,
            "transport": "private-unix-v1"}


@asynccontextmanager
async def api_client(backend, url, *, timeout=30):
    from .connection import effective_settings, paths, master_running
    from .config import endpoint_settings
    from .policy import require
    require("read")
    endpoint_settings(url)
    settings = effective_settings()
    uses_ssh = urlsplit(url).scheme == "http" and (settings.gateway_uses_ssh() if backend == "gateway" else settings.webui_ssh)
    options = {"timeout": timeout, "trust_env": False, "follow_redirects": False}
    if uses_ssh:
        control, metadata, _ = paths()
        target = socket_for(control, backend)
        try:
            if not await master_running(control):
                raise TransportUnavailable("The private SSH connection is down. Call hermes_reconnect; no credentials were sent.")
            if json.loads(metadata.read_text()) != expected_metadata(settings):
                raise TransportUnavailable("SSH settings changed or an older tunnel is running. Call hermes_reconnect before using credentials.")
            info = target.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise TransportUnavailable("The private SSH socket is invalid. No credentials were sent.")
        except (OSError, ValueError):
            raise TransportUnavailable("The private SSH socket is unavailable. Call hermes_reconnect; no credentials were sent.") from None
        options["transport"] = httpx.AsyncHTTPTransport(uds=str(target), trust_env=False)
    elif urlsplit(url).scheme != "https":
        raise TransportUnavailable("Unmanaged loopback HTTP is disabled: another local process could steal credentials. Configure managed SSH or authenticated HTTPS.")
    async with httpx.AsyncClient(**options) as client:
        require("read")
        yield client
