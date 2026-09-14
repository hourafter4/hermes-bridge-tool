"""Small command-line interface; importing it never starts an agent or tunnel."""

import argparse
import asyncio
from dataclasses import asdict, replace
from getpass import getpass
import json
from pathlib import Path
import subprocess
import sys

from mcp.server.fastmcp.exceptions import ToolError

from . import __version__
from . import policy
from .config import config_path, load_settings, private_write, save_settings, webui_auth_headers, gateway_configured, webui_configured
from .config import read_credential, write_credential


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Use a remote Hermes agent from Codex, Claude Code, or any MCP client.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    update = commands.add_parser("update", help="Check for and install a verified stable GitHub release.")
    update.add_argument("--check", action="store_true", help="Only check for an update; do not install.")
    update.add_argument("--json", action="store_true", help="Print machine-readable check results (requires --check).")
    update.add_argument("--yes", action="store_true", help="Confirm downloading and installing the selected release.")
    update.add_argument("--tag", help="Check or install this exact published stable release tag.")
    update.add_argument("--current-version", default=__version__, help="Installed app version (defaults to the CLI version).")
    update.add_argument("--no-app", action="store_true", help="Update only the CLI, including on macOS.")
    commands.add_parser("mcp", help="Serve MCP over stdio (normally launched by your coding harness).")
    doctor = commands.add_parser("doctor", help="Check authenticated API connectivity; no agent work.")
    doctor.add_argument("--backend", choices=["gateway", "webui", "all"], default="gateway")
    commands.add_parser("tunnel", help="Compatibility alias for the private managed connection (connect).")
    for command, help_text in (
        ("connect", "Start or reuse the shared background SSH connection; check HTTP backends."),
        ("reconnect", "Repair the shared local connection; never restart Hermes or replay work."),
        ("disconnect", "Close the shared SSH tunnel for all local clients; remote work continues."),
        ("connection-status", "Check HTTP backends and shared tunnel without connecting."),
    ):
        commands.add_parser(command, help=help_text)
    commands.add_parser("config", help="Print configuration and file location, without revealing the key.")
    credentials = commands.add_parser("credentials", help="Migrate saved credentials to Keychain or explicit file storage.")
    credential_commands = credentials.add_subparsers(dest="credential_command", required=True)
    migration = credential_commands.add_parser("migrate", help="Verify destination storage before switching settings.")
    migration.add_argument("--to", choices=["keychain", "file"], required=True)
    migration.add_argument("--remove-files", action="store_true", help="Remove old plaintext files after verified Keychain migration.")
    runtime = commands.add_parser("harden-ssh", help="Provision restricted non-root runtime keys through an existing admin SSH login.")
    runtime.add_argument("--admin-host", required=True, help="Existing administrator SSH alias; used only for provisioning.")
    runtime.add_argument("--runtime-user", default="hermes-bridge")
    runtime.add_argument("--hermes-user", default="hermes")
    runtime.add_argument("--native-command", nargs=argparse.REMAINDER, help="Absolute remote Hermes executable and MCP arguments; must be last.")
    security = commands.add_parser("security", help="Inspect or change local access policy; defaults to monitoring only.")
    security_commands = security.add_subparsers(dest="security_command", required=True)
    security_commands.add_parser("status", help="Print effective permissions without contacting Hermes.")
    mode = security_commands.add_parser("mode", help="Monitor permits reads; control also permits task mutations.")
    mode.add_argument("mode", choices=["monitor", "control"])
    messages = security_commands.add_parser("messages", help="Separately enable platform message delivery in control mode.")
    messages.add_argument("messages", choices=["on", "off"])
    security_commands.add_parser("lock", help="Block future upstream access and close the shared tunnel; remote work continues.")
    security_commands.add_parser("unlock", help="Restore the previous policy after local interactive confirmation.")
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
        if args.command == "update":
            from .updates import run_update
            return run_update(args)
        if args.command == "harden-ssh":
            from .runtime_setup import harden
            result = harden(args.admin_host, runtime_user=args.runtime_user, hermes_user=args.hermes_user,
                            native_command=args.native_command)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "credentials":
            from .credential_migration import migrate
            print(json.dumps(migrate(args.to, remove_files=args.remove_files), indent=2))
            return 0
        if args.command == "security":
            increases_access = args.security_command == "unlock" or (
                args.security_command == "mode" and args.mode == "control"
            ) or (args.security_command == "messages" and args.messages == "on")
            if increases_access:
                if not sys.stdin.isatty() or not sys.stdout.isatty():
                    raise ValueError("Enable access in your own interactive terminal. Noninteractive permission changes are refused; no settings were changed.")
                print("This grants access to every coding client using this configuration. Approval responses remain disabled.")
                print("Processes with shell access as your user can also change this policy; it is not an operating-system sandbox.")
                if input("Type ENABLE to authorize this local policy change: ").strip() != "ENABLE":
                    raise ValueError("Permission change cancelled; no settings were changed.")
            if args.security_command == "status":
                result = policy.status()
            elif args.security_command == "mode":
                result = policy.update(mode=args.mode)
            elif args.security_command == "messages":
                result = policy.update(messages=args.messages == "on")
            else:
                result = policy.update(locked=args.security_command == "lock")
                if args.security_command == "lock":
                    from . import connection
                    async def close_tunnel():
                        return await asyncio.wait_for(connection.disconnect(), timeout=10)
                    try:
                        asyncio.run(close_tunnel())
                        result["shared_tunnel_closed"] = True
                    except (ValueError, OSError, asyncio.TimeoutError):
                        result["shared_tunnel_closed"] = False
                    result["hint"] = "New bridge requests are blocked. Previously accepted remote work continues. This does not revoke credentials or other SSH clients."
            print(json.dumps(result, indent=2))
            return 0
        if args.command in ("connect", "reconnect", "disconnect", "connection-status", "tunnel"):
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
                write_credential(settings, "gateway", key + "\n")
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
                    headers = webui_auth_headers(content=read_credential(settings, "webui"))
                else:
                    if any(ord(char) < 32 or ord(char) > 126 for char in cookie):
                        raise ValueError("Cookie header must contain printable ASCII without newlines.")
                    headers = {"Cookie": cookie}
            previous_auth = read_credential(settings, "webui") if target.exists() or settings.webui_credential_store == "keychain" else None
            write_credential(settings, "webui", json.dumps(headers, indent=2) + "\n")
            try:
                save_settings(settings)
            except (ValueError, OSError):
                if previous_auth is None:
                    from .credentials import delete_secret
                    from .config import credential_account
                    delete_secret(credential_account("webui"), backend=settings.webui_credential_store, file_path=target)
                else:
                    write_credential(settings, "webui", previous_auth)
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
        elif args.command == "doctor":
            from .server import hermes_check
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
    except (ValueError, OSError, ToolError) as error:
        if args.command == "update" and args.json:
            print(json.dumps({"error": str(error)}))
            return 1
        print(f"hermes-bridge-tool: {error}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print("hermes-bridge-tool: The command timed out. Check its status before retrying.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
