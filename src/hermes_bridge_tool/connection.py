"""Shared, user-owned SSH transport for the app, CLI and MCP clients.

Only our private OpenSSH control socket can stop a tunnel. Never kill a PID or
restart a remote service. The local MCP server remains independent of this link.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
from time import monotonic

import httpx

from .config import (config_path, load_settings, connection_settings,
                     webui_connection_settings, gateway_configured, webui_configured,
                     private_write, _matches_forward)


def effective_settings():
    settings = load_settings()
    webui_url = os.environ.get("HERMES_WEBUI_URL", settings.webui_url)
    return replace(
        settings,
        gateway_url=os.environ.get("HERMES_API_URL", settings.gateway_url),
        webui_url=webui_url,
        webui_ssh=settings.webui_ssh and (not webui_url or _matches_forward(webui_url, settings.webui_local_port)),
    ).validate()


def paths():
    # Keep UNIX socket paths below macOS's limit even with a long home/config path.
    directory = Path("/tmp") / f"hermes-bridge-tool-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("The shared SSH control directory must be private and owned by your user.")
    name = sha256(str(config_path().resolve()).encode()).hexdigest()[:20]
    return directory / (name + ".sock"), directory / (name + ".json"), directory / (name + ".lock")


@asynccontextmanager
async def connection_lock(lock_path):
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("The connection lock must be a private file owned by your user.")
        deadline = monotonic() + 5
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if monotonic() >= deadline:
                    raise ValueError("Another connection operation is in progress. Retry after it finishes.") from None
                await asyncio.sleep(0.1)
        yield
    finally:
        os.close(descriptor)


async def run_ssh(arguments, timeout=3):
    process = await asyncio.create_subprocess_exec(
        *arguments, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        return await asyncio.wait_for(process.wait(), timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise


async def master_running(socket_path):
    try:
        info = socket_path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("Unexpected file at the shared SSH control socket; refusing to use it.")
    return await run_ssh(["ssh", "-F", "/dev/null", "-S", str(socket_path), "-O", "check", "localhost"]) == 0


async def _probe(backend):
    try:
        if backend == "gateway":
            url, key = connection_settings()
            headers, route = {"Authorization": f"Bearer {key}"}, "/v1/capabilities"
        else:
            url, headers = webui_connection_settings()
            route = "/api/sessions"
        async with httpx.AsyncClient(timeout=3, follow_redirects=False, trust_env=False) as client:
            response = await client.get(url + route, headers=headers)
        if response.status_code in (401, 403):
            return {"configured": True, "ready": False, "problem": "authentication", "http_status": response.status_code,
                    "hint": "Refresh this backend's saved credentials; reconnecting cannot fix an expired cookie or rejected key."}
        if response.status_code != 200:
            return {"configured": True, "ready": False, "problem": "http", "http_status": response.status_code,
                    "hint": "Check this backend's URL and server; reconnect does not restart remote services."}
        data = response.json()
        ready = isinstance(data, dict) and (
            isinstance(data.get("sessions"), list) if backend == "webui" else
            isinstance(data.get("features"), dict) and all(data["features"].get(key) is True for key in ("run_submission", "run_status", "run_stop"))
        )
        return {"configured": True, "ready": ready, "problem": None if ready else "unsupported_api"}
    except (httpx.HTTPError, asyncio.TimeoutError):
        return {"configured": True, "ready": False, "problem": "transport",
                "hint": "Call hermes_reconnect or run hermes-bridge-tool reconnect, then check the existing task ID. Do not resubmit work."}
    except (ValueError, OSError):
        return {"configured": True, "ready": False, "problem": "configuration",
                "hint": "Check the saved endpoint and credential file. Reconnection does not change configuration or log in."}


async def connection_status():
    settings = effective_settings()
    socket_path, _, _ = paths()
    selected = [name for name, enabled in (("gateway", gateway_configured(settings)), ("webui", webui_configured(settings))) if enabled]
    results = await asyncio.gather(*(_probe(name) for name in selected))
    reports = {name: {"configured": False, "skipped": True} for name in ("gateway", "webui")}
    reports.update(zip(selected, results))
    for name in selected:
        reports[name]["uses_ssh"] = settings.gateway_uses_ssh() if name == "gateway" else settings.webui_ssh
    return {"ready": bool(results) and all(result["ready"] for result in results),
            "managed_tunnel": await master_running(socket_path),
            "ssh_required": bool(settings.ssh_forwards()), **reports}


async def _stop(socket_path):
    if not await master_running(socket_path):
        return
    if await run_ssh(["ssh", "-F", "/dev/null", "-S", str(socket_path), "-O", "exit", "localhost"]) != 0:
        raise ValueError("Could not stop the owned SSH tunnel. No other process was stopped.")
    deadline = monotonic() + 3
    while await master_running(socket_path):
        if monotonic() >= deadline:
            raise ValueError("The owned SSH tunnel is still closing. Retry after it exits.")
        await asyncio.sleep(0.1)


async def port_in_use(port):
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 0.4)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        # A service resetting an accepted connection still owns this port.
        pass
    return True


async def connect(*, restart=False):
    settings = effective_settings()
    socket_path, metadata, lock_path = paths()
    async with connection_lock(lock_path):
        forwards = settings.ssh_forwards()
        desired = {"host": settings.ssh_host, "forwards": forwards}
        running = await master_running(socket_path)
        try:
            previous = json.loads(metadata.read_text())
        except (OSError, ValueError):
            previous = None
        if running and restart and previous == desired and forwards:
            report = await connection_status()
            if report["ready"]:
                return {**report, "action": "already_ready"}
            if not any(report[name].get("problem") == "transport" and report[name].get("uses_ssh") for name in ("gateway", "webui")):
                return {**report, "action": "no_transport_repair", "hint":
                        "The tunnel is running; this failure needs valid credentials, configuration, or a working remote API. Reconnecting cannot repair it. No tunnel was restarted."}
        if running and (restart or previous != desired or not forwards):
            await _stop(socket_path)
            running = False
        action = "reused" if running else "checked_direct"
        if forwards and not running:
            occupied = [forward for forward in forwards if await port_in_use(int(forward.split(":")[1]))]
            if occupied:
                report = await connection_status()
                return {**report, "action": "external_listener", "hint":
                        "A listener outside the shared manager owns a configured port. Healthy connections can be used; to replace it, close the older app or manual tunnel yourself. No process was killed."}
            # Remove only a stale, validated socket in our private directory.
            if socket_path.exists():
                socket_path.unlink()
            command = settings.ssh_command()
            overrides = {"ControlMaster=no": "ControlMaster=yes", "ControlPath=none": f"ControlPath={socket_path}",
                         "ForkAfterAuthentication=no": "ForkAfterAuthentication=yes"}
            command = [overrides.get(argument, argument) for argument in command]
            command[1:1] = ["-o", "ConnectTimeout=10", "-o", "ConnectionAttempts=1"]
            try:
                result = await run_ssh(command, timeout=15)
            except asyncio.TimeoutError:
                return {**await connection_status(), "action": "ssh_timeout", "hint": "SSH did not connect within its deadline. Check network, SSH keys, and the saved alias."}
            if result != 0:
                return {**await connection_status(), "action": "ssh_failed", "hint": "Check the saved SSH alias, verified host key, key/agent access, and local ports. No remote service was restarted."}
            private_write(metadata, json.dumps(desired))
            action = "reconnected" if restart else "connected"
        return {**await connection_status(), "action": action}


async def disconnect():
    socket_path, metadata, lock_path = paths()
    async with connection_lock(lock_path):
        await _stop(socket_path)
        metadata.unlink(missing_ok=True)
    return {"ready": False, "managed_tunnel": False, "action": "disconnected",
            "hint": "Only the shared owned SSH tunnel was stopped. Remote tasks, direct HTTPS endpoints, and other tunnels remain running."}
