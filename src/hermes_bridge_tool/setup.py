"""Pair an existing Hermes gateway over SSH and register local coding clients."""

from dataclasses import replace
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

from .config import Settings, config_path, load_settings, private_write, save_settings


def find_command(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for directory in (Path.home() / ".local/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def pairing_command(settings: Settings, remote_user: str | None, remote_home: str | None, observe_sessions: bool = False) -> list[str]:
    settings.validate()
    command = ["python3", "-", "--json", "--restart", "--port", str(settings.remote_port)]
    if remote_home:
        command += ["--home", remote_home]
    if observe_sessions:
        command += ["--observe-sessions"]
    if remote_user:
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", remote_user):
            raise ValueError("Remote user must be a Linux username; use '-' to keep the SSH login user.")
        # Arguments stay separate, including profile paths containing spaces.
        wrapper = ('bridge_user=$1; shift; '
                   'if [ "$(id -un)" = "$bridge_user" ]; then exec "$@"; '
                   'elif [ "$(id -u)" = 0 ]; then exec runuser -u "$bridge_user" -- "$@"; '
                   'else exec sudo -n -H -u "$bridge_user" "$@"; fi')
        command = ["sh", "-c", wrapper, "hermes-bridge-tool", remote_user, *command]
    return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", settings.ssh_host, shlex.join(command)]


def pair_server(settings: Settings, remote_user: str | None = "hermes", remote_home: str | None = None, observe_sessions: bool = False) -> dict:
    command = pairing_command(settings, remote_user, remote_home, observe_sessions)
    helper = Path(__file__).with_name("server_setup.py").read_text()
    if observe_sessions:
        source = Path(__file__).with_name("observer_plugin.py").read_text()
        helper = "OBSERVER_SOURCE = " + repr(source) + "\n" + helper
    try:
        result = subprocess.run(command, input=helper, text=True, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise ValueError("Server setup timed out. It may have changed the API settings; rerun setup to reuse the same key.") from None
    try:
        # SSH login banners can precede the helper's single JSON result.
        data = json.loads(result.stdout.strip().splitlines()[-1])
        if not isinstance(data, dict):
            raise ValueError
    except (ValueError, IndexError):
        hint = "Check SSH access, Python 3, and the Hermes Linux user. Switching users requires root/runuser or passwordless sudo."
        if "host key" in result.stderr.lower():
            hint = "Verify the server's SSH host key in your terminal first, then rerun setup."
        elif "permission denied" in result.stderr.lower():
            hint = "SSH authentication failed. Load your SSH key into the agent and verify the host alias."
        raise ValueError(f"Could not pair with the server. {hint}") from None
    if result.returncode != 0 or data.get("ok") is not True:
        # The bundled helper returns sanitized errors, never gateway output.
        raise ValueError("Server setup failed: " + str(data.get("error", "Check the existing Hermes gateway and its service manager.")))
    key = data.get("api_key")
    if not isinstance(key, str) or not key or any(not 33 <= ord(char) <= 126 for char in key):
        raise ValueError("The server did not return a valid API key. Local settings were not changed.")
    if data.get("ready") is not True:
        raise ValueError("The server API is not ready. Check its gateway and supported Runs API before pairing.")
    if observe_sessions and data.get("observer_ready") is not True:
        raise ValueError("The session observer is not ready. Check Hermes plugin support and gateway plugin errors before pairing.")
    key_path = Path(settings.api_key_file).expanduser()
    previous_key = key_path.read_text() if key_path.exists() else None
    private_write(key_path, key + "\n")
    try:
        save_settings(settings)
    except (OSError, ValueError):
        # Keep an existing pairing usable when the config cannot be committed.
        if previous_key is None:
            key_path.unlink()
        else:
            private_write(key_path, previous_key)
        raise
    return {field: value for field, value in data.items() if field != "api_key"}


def bridge_command() -> list[str]:
    # Prefer this installation over an unrelated command earlier on PATH.
    executable = Path(sys.executable).parent / "hermes-bridge-tool"
    if executable.is_file():
        return [str(executable), "mcp"]
    return [sys.executable, "-m", "hermes_bridge_tool", "mcp"]


def register_clients(client: str) -> list[str]:
    names = ("codex", "claude") if client == "both" else (() if client == "none" else (client,))
    commands = []
    for name in names:
        executable = find_command(name)
        if not executable:
            raise ValueError(f"{name} is not installed. Install it first, or use --client none and register later.")
        options = ["--transport", "stdio", "--scope", "user"] if name == "claude" else []
        commands.append((name, [executable, "mcp", "add", *options, "hermes-bridge-tool", "--", *bridge_command()]))
    done = []
    for name, command in commands:
        result = subprocess.run(command, text=True, capture_output=True, timeout=30)
        if result.returncode and name == "claude" and "already exists in user config" in result.stderr:
            existing = subprocess.run([command[0], "mcp", "get", "hermes-bridge-tool"], text=True, capture_output=True, timeout=30)
            fields = dict(line.strip().split(": ", 1) for line in existing.stdout.splitlines() if ": " in line)
            expected = bridge_command()
            if (existing.returncode == 0 and fields.get("Scope", "").startswith("User config")
                    and fields.get("Command") == expected[0]
                    and fields.get("Args", "").strip() == " ".join(expected[1:])):
                done.append(name)
                continue
            raise ValueError("Claude already has a different 'hermes-bridge-tool' entry. To replace it, run claude mcp remove hermes-bridge-tool --scope user, then hermes-bridge-tool register claude.")
        if result.returncode:
            completed = f" Already registered: {', '.join(done)}." if done else ""
            raise ValueError(f"Could not register {name}. Inspect its MCP configuration and run hermes-bridge-tool register {client} again.{completed}")
        done.append(name)
    return done


def run_setup(args) -> int:
    settings = load_settings()
    observe_sessions = getattr(args, "observe_sessions", False)
    interactive = sys.stdin.isatty() and not args.yes
    if not interactive and not args.yes:
        raise ValueError("Run setup in a terminal, or pass --yes with explicit connection options.")

    def ask(label, default):
        return (input(f"{label} [{default}]: ").strip() or default) if interactive else default

    host = args.host or ask("SSH host or alias", settings.ssh_host)
    remote_user = args.remote_user or ask("Linux user running Hermes ('-' for SSH login user)", "hermes")
    installed = [name for name in ("codex", "claude") if find_command(name)]
    default_client = "both" if len(installed) == 2 else (installed[0] if installed else "none")
    client = args.client or ask("Connect coding client: codex / claude / both / none", default_client)
    if client not in {"codex", "claude", "both", "none"}:
        raise ValueError("Choose codex, claude, both, or none.")
    settings = replace(settings, ssh_host=host,
                       local_port=settings.local_port if args.local_port is None else args.local_port,
                       remote_port=settings.remote_port if args.remote_port is None else args.remote_port).validate()
    # Check local prerequisites before changing anything on the server.
    for name in (("codex", "claude") if client == "both" else (() if client == "none" else (client,))):
        if not find_command(name):
            raise ValueError(f"{name} is not installed. Choose --client none to pair first.")
    pairing_command(settings, None if remote_user == "-" else remote_user, args.remote_home, observe_sessions)
    print(f"\nPairing {host} with Hermes Bridge Tool.")
    print("This enables the localhost API, restarts the existing gateway, and saves its key privately on this machine.")
    if observe_sessions:
        print("It also installs and enables the Hermes Bridge Tool session observer plugin. Restart CLI sessions to load it there.")
    if interactive and input("Continue? [Y/n]: ").strip().lower() not in {"", "y", "yes"}:
        print("Setup cancelled; no changes made.")
        return 0
    print("1/3  Configure and check the server API…", flush=True)
    pair_server(settings, None if remote_user == "-" else remote_user, args.remote_home, observe_sessions=observe_sessions)
    print(f"2/3  Paired. Settings saved to {config_path()}.")
    names = register_clients(client)
    print("3/3  " + (f"Registered {', '.join(names)}. Restart the client to load Hermes tools." if names else "Pairing complete. Register a client later with hermes-bridge-tool register."))
    app = Path.home() / "Applications/Hermes Bridge Tool.app"
    if sys.platform == "darwin" and app.is_dir():
        subprocess.run(["open", str(app)], check=False)
        print("Choose Connect from the Hermes Bridge Tool menu bar icon, then run hermes-bridge-tool doctor.")
    else:
        print("Run hermes-bridge-tool tunnel, then hermes-bridge-tool doctor in another terminal.")
    return 0
