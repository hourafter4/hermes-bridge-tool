"""Local integration of separately authorized administrator provisioning."""

from dataclasses import replace
import json

from .config import config_path, load_settings, save_settings, private_write
from .runtime_ssh import generate_runtime_key, provision_runtime_ssh


def harden(admin_host, *, runtime_user="hermes-bridge", hermes_user="hermes", native_command=None):
    settings = load_settings()
    data = json.loads(config_path().read_text()) if config_path().exists() else {}
    if data.get("native_mcp_command") and not native_command:
        raise ValueError("Supply --native-command with the absolute remote Hermes MCP command to replace the existing native connection as well.")
    directory = config_path().parent / "runtime-keys"
    forwarding = directory / "forward-ed25519"
    native = directory / "native-ed25519"
    public = generate_runtime_key(forwarding)
    native_public = generate_runtime_key(native) if native_command else None
    ports = [settings.remote_port]
    if settings.webui_ssh:
        ports.append(settings.webui_remote_port)
    result = provision_runtime_ssh(admin_host, public, runtime_user=runtime_user, hermes_user=hermes_user,
                                   native_public_key=native_public, native_command=native_command, ports=ports)
    # Administrative credentials are never copied or replaced. Runtime flags
    # override the alias's user and select separate narrowly authorized keys.
    save_settings(replace(settings, ssh_host=admin_host, ssh_user=runtime_user,
                          ssh_identity_file=str(forwarding)))
    if native_command:
        data = json.loads(config_path().read_text())
        data["native_mcp_command"] = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ForwardAgent=no", "-o", "ForwardX11=no", "-o", "PermitLocalCommand=no",
            "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "IdentitiesOnly=yes",
            "-i", str(native), "-l", hermes_user, admin_host, "hermes-bridge-tool-native"]
        private_write(config_path(), json.dumps(data, indent=2) + "\n")
    return {**result, "hint": "Runtime keys are configured. Run reconnect, then restart coding clients to discard older native SSH sessions. Existing administrator access was preserved."}
