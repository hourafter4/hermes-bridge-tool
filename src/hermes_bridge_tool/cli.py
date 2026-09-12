"""Small command-line interface; importing it never starts an agent or tunnel."""

import argparse
import asyncio
from dataclasses import asdict, replace
from getpass import getpass
import json
from pathlib import Path
import subprocess
import sys

from . import __version__
from .config import config_path, load_settings, private_write, save_settings, webui_auth_headers, gateway_configured, webui_configured


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Use a remote Hermes agent from Codex, Claude Code, or any MCP client.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("mcp", help="Serve MCP over stdio (normally launched by your coding harness).")
    doctor = commands.add_parser("doctor", help="Check authenticated API connectivity; no agent work.")
    doctor.add_argument("--backend", choices=["gateway", "webui", "all"], default="gateway")
    commands.add_parser("tunnel", help="Legacy foreground SSH tunnel; use connect for agent-managed recovery.")
    for command, help_text in (
        ("connect", "Start or reuse the shared background SSH connection; check HTTP backends."),
        ("reconnect", "Repair the shared local connection; never restart Hermes or replay work."),
        ("disconnect", "Close the shared SSH tunnel for all local clients; remote work continues."),
        ("connection-status", "Check HTTP backends and shared tunnel without connecting."),
    ):
        commands.add_parser(command, help=help_text)
    commands.add_parser("config", help="Print configuration and file location, without revealing the key.")
    configure = commands.add_parser("configure", help="Save connection settings and privately enter the existing server API key.")
    configure.add_argument("--host", help="An existing SSH alias or user@hostname.")
    configure.add_argument("--url", help="Existing Gateway HTTPS base URL; an empty string restores SSH loopback.")
    configure.add_argument("--keep-key", action="store_true", help="Keep the saved key without prompting (also usable with HERMES_API_KEY).")
    configure.add_argument("--local-port", type=int)
    configure.add_argument("--remote-port", type=int)
    webui = commands.add_parser("configure-webui", help="Connect an existing WebUI API; makes no changes on the server.")
    webui.add_argument("--url", help="Existing HTTPS base URL, optionally including a reverse-proxy path prefix.")
    webui.add_argument("--auth-file", help="JSON file of request headers; copied to private local storage. Use {} for an unauthenticated local server.")
    webui.add_argument("--ssh", action="store_true", help="Reach the WebUI using a loopback SSH forward instead of public HTTPS.")
    webui.add_argument("--host", help="Existing SSH alias shared with the Gateway tunnel.")
    webui.add_argument("--local-port", type=int)
    webui.add_argument("--remote-port", type=int)
    native = commands.add_parser("configure-native", help="Save the command launching Hermes's existing stdio MCP server; no server changes.")
    native.add_argument("argv", nargs=argparse.REMAINDER, help="Executable and arguments after --, e.g. -- hermes mcp serve")
    setup = commands.add_parser("setup", help="Pair with an existing Hermes server over SSH and connect coding clients.")
    setup.add_argument("--host", help="SSH alias or user@hostname.")
    setup.add_argument("--remote-user", help="Linux user running Hermes; '-' keeps the SSH login user.")
    setup.add_argument("--remote-home", help="Custom Hermes profile directory on the server.")
    setup.add_argument("--restart-command", help="Custom remote gateway restart executable and arguments; runs as the Hermes user without a shell.")
    setup.add_argument("--local-port", type=int)
    setup.add_argument("--remote-port", type=int)
    setup.add_argument("--client", choices=["codex", "claude", "both", "none"])
    setup.add_argument("--yes", action="store_true", help="Apply setup without interactive questions.")
    setup.add_argument("--observe-sessions", action="store_true", help="Install the Hermes observer plugin to track CLI and web UI turn completion.")
    register = commands.add_parser("register", help="Register the installed MCP tool in a coding client.")
    register.add_argument("client", choices=["codex", "claude", "both"])
    args = parser.parse_args(argv)
    try:
        if args.command in ("connect", "reconnect", "disconnect", "connection-status"):
            from . import connection
            async def operation():
                if args.command == "disconnect":
                    return await connection.disconnect()
                if args.command == "connection-status":
                    return await connection.connection_status()
                return await connection.connect(restart=args.command == "reconnect")
            async def bounded():
                return await asyncio.wait_for(operation(), timeout=45)
            try:
                result = asyncio.run(bounded())
            except (ValueError, OSError, asyncio.TimeoutError):
                result = {"ready": False, "action": "connection_error", "hint": "Check private connection settings, SSH availability, and whether another connection operation is in progress. No remote service was restarted."}
                print(json.dumps(result, indent=2))
                return 1
            print(json.dumps(result, indent=2))
            return 0 if result.get("ready") or args.command == "disconnect" else 1
        if args.command == "setup":
            from .setup import run_setup
            return run_setup(args)
        if args.command == "register":
            from .setup import register_clients
            names = register_clients(args.client)
            print(f"Registered {', '.join(names)}. Restart the client to load Hermes tools.")
            return 0
        if args.command == "mcp":
            from .server import mcp
            mcp.run(transport="stdio")
            return 0
        settings = load_settings()
        if args.command == "config":
            print(json.dumps({"config_file": str(config_path()), **asdict(settings)}, indent=2))
        elif args.command == "configure":
            changes = {field: getattr(args, flag) for field, flag in (("ssh_host", "host"), ("local_port", "local_port"), ("remote_port", "remote_port"), ("gateway_url", "url")) if getattr(args, flag) is not None}
            settings = replace(settings, **changes).validate()
            if not args.keep_key and not sys.stdin.isatty():
                raise ValueError("Run configure interactively to enter the API key privately, or pass --keep-key.")
            key = "" if args.keep_key else getpass("Hermes Gateway API key (Enter keeps the existing key): ").strip()
            if key:
                if any(not 33 <= ord(char) <= 126 for char in key):
                    raise ValueError("API key must be a token without whitespace.")
                private_write(Path(settings.api_key_file), key + "\n")
            save_settings(settings)
            print(f"Saved {config_path()}. " + ("Start the tunnel or connect through the menu bar app." if settings.gateway_uses_ssh() else "Gateway will connect directly over HTTPS."))
        elif args.command == "configure-webui":
            local_port = settings.webui_local_port if args.local_port is None else args.local_port
            remote_port = settings.webui_remote_port if args.remote_port is None else args.remote_port
            url = args.url or (f"http://127.0.0.1:{local_port}" if args.ssh else settings.webui_url)
            if not url:
                raise ValueError("Supply --url https://your-webui-host or --ssh to use the existing server over SSH.")
            settings = replace(settings, webui_url=url, webui_ssh=args.ssh,
                               webui_local_port=local_port, webui_remote_port=remote_port,
                               ssh_host=args.host or settings.ssh_host).validate()
            target = Path(settings.webui_auth_file).expanduser()
            if args.auth_file:
                headers = webui_auth_headers(Path(args.auth_file))
            else:
                if not sys.stdin.isatty():
                    raise ValueError("Supply --auth-file with a JSON header map, or run interactively to enter the WebUI Cookie privately.")
                cookie = getpass("WebUI Cookie header (Enter keeps saved authentication): ").strip()
                if not cookie:
                    headers = webui_auth_headers(target)
                else:
                    if any(ord(char) < 32 or ord(char) > 126 for char in cookie):
                        raise ValueError("Cookie header must contain printable ASCII without newlines.")
                    headers = {"Cookie": cookie}
            previous_auth = target.read_text() if target.exists() else None
            private_write(target, json.dumps(headers, indent=2) + "\n")
            try:
                save_settings(settings)
            except (ValueError, OSError):
                if previous_auth is None:
                    target.unlink()
                else:
                    private_write(target, previous_auth)
                raise
            print(f"Saved WebUI connection to {config_path()}. Authentication is stored privately.")
            print("Run hermes-bridge-tool reconnect or choose Reconnect to include the WebUI forward." if args.ssh else "WebUI requests connect directly; no SSH tunnel is needed for this backend.")
            print("Check with hermes-bridge-tool doctor --backend webui; register codex/claude/both if needed.")
        elif args.command == "configure-native":
            command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            if not command or any(not item or "\x00" in item for item in command):
                raise ValueError("Supply executable and arguments after --. Example: configure-native -- hermes mcp serve")
            path = config_path()
            data = json.loads(path.read_text()) if path.exists() else {}
            data["native_mcp_command"] = command
            private_write(path, json.dumps(data, indent=2) + "\n")
            print("Saved native MCP command. Restart the coding client, then call hermes_native_tools to verify it.")
        elif args.command == "tunnel":
            command = settings.ssh_command()
            print(f"Forwarding {', '.join(settings.ssh_forwards())} through {settings.ssh_host}. Ctrl-C disconnects.", file=sys.stderr)
            return subprocess.run(command).returncode
        elif args.command == "doctor":
            from .server import hermes_check
            from mcp.server.fastmcp.exceptions import ToolError
            reports = {}
            for backend in (("gateway", "webui") if args.backend == "all" else (args.backend,)):
                try:
                    configured = webui_configured(settings) if backend == "webui" else gateway_configured(settings)
                    if args.backend == "all" and not configured:
                        reports[backend] = {"configured": False, "skipped": True}
                        continue
                    if backend == "webui":
                        from .webui import hermes_webui_check
                        reports[backend] = asyncio.run(hermes_webui_check())
                    else:
                        result = asyncio.run(hermes_check())
                        features = result.get("features", {})
                        required = ("run_submission", "run_status", "run_stop")
                        reports[backend] = {
                            "ready": isinstance(features, dict) and all(features.get(name) is True for name in required),
                            "session_tools_ready": isinstance(features, dict) and features.get("session_resources") is True,
                            "steering_ready": isinstance(features, dict) and features.get("run_steer") is True,
                            "capabilities": result,
                        }
                except (ToolError, ValueError) as error:
                    reports[backend] = {"ready": False, "error": str(error)}
            active = [report for report in reports.values() if not report.get("skipped")]
            ready = bool(active) and all(report.get("ready") is True for report in active)
            output = {"ready": ready, **reports} if args.backend == "all" else reports[args.backend]
            print(json.dumps(output, indent=2))
            return 0 if ready else 1
        return 0
    except (ValueError, OSError) as error:
        print(f"hermes-bridge-tool: {error}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("hermes-bridge-tool: The command timed out. Check its status before retrying.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
