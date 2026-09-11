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
from .config import config_path, load_settings, private_write, save_settings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Use a remote Hermes agent from Codex, Claude Code, or any MCP client.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("mcp", help="Serve MCP over stdio (normally launched by your coding harness).")
    commands.add_parser("doctor", help="Check authenticated API connectivity and required features; no agent work.")
    commands.add_parser("tunnel", help="Keep the SSH tunnel running in this terminal.")
    commands.add_parser("config", help="Print configuration and file location, without revealing the key.")
    configure = commands.add_parser("configure", help="Save connection settings and privately enter the existing server API key.")
    configure.add_argument("--host", help="An existing SSH alias or user@hostname.")
    configure.add_argument("--local-port", type=int)
    configure.add_argument("--remote-port", type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "mcp":
            from .server import mcp
            mcp.run(transport="stdio")
            return 0
        settings = load_settings()
        if args.command == "config":
            print(json.dumps({"config_file": str(config_path()), **asdict(settings)}, indent=2))
        elif args.command == "configure":
            changes = {field: getattr(args, flag) for field, flag in (("ssh_host", "host"), ("local_port", "local_port"), ("remote_port", "remote_port")) if getattr(args, flag) is not None}
            settings = replace(settings, **changes).validate()
            if not sys.stdin.isatty():
                raise ValueError("Run configure interactively to enter the API key privately, or use app Settings.")
            key = getpass("Hermes API key (Enter keeps the existing key): ").strip()
            if key:
                if any(not 33 <= ord(char) <= 126 for char in key):
                    raise ValueError("API key must be a token without whitespace.")
                private_write(Path(settings.api_key_file), key + "\n")
            save_settings(settings)
            print(f"Saved {config_path()}. Start the tunnel or connect through the menu bar app.")
        elif args.command == "tunnel":
            print(f"Forwarding 127.0.0.1:{settings.local_port} through {settings.ssh_host}. Ctrl-C disconnects.", file=sys.stderr)
            return subprocess.run(settings.ssh_command()).returncode
        elif args.command == "doctor":
            from .server import hermes_check
            from mcp.server.fastmcp.exceptions import ToolError
            try:
                result = asyncio.run(hermes_check())
            except ToolError as error:
                print(f"hermes-bridge: {error}", file=sys.stderr)
                return 1
            features = result.get("features", {})
            required = ("run_submission", "run_status", "run_stop")
            ready = isinstance(features, dict) and all(features.get(name) is True for name in required)
            print(json.dumps({"ready": ready, "capabilities": result}, indent=2))
            return 0 if ready else 1
        return 0
    except (ValueError, OSError) as error:
        print(f"hermes-bridge: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
