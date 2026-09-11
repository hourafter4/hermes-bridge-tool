"""Configuration shared by the CLI, MCP server, and macOS companion."""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    ssh_host: str = "hetzner"
    local_port: int = 18642
    remote_port: int = 8642
    api_key_file: str = "~/.config/hermes-bridge-tool/api-key"

    def validate(self):
        if not isinstance(self.ssh_host, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]{0,254}", self.ssh_host):
            raise ValueError("SSH host must be an SSH alias or user@hostname, without spaces or options.")
        for port in (self.local_port, self.remote_port):
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("Ports must be integers between 1 and 65535.")
        if not isinstance(self.api_key_file, str) or not self.api_key_file.strip():
            raise ValueError("api_key_file must be a nonempty file path.")
        return self

    def ssh_command(self) -> list[str]:
        self.validate()
        return [
            "ssh", "-N", "-T", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
            "-o", "StrictHostKeyChecking=yes", "-o", "ControlMaster=no",
            "-o", "ControlPath=none", "-o", "ControlPersist=no", "-o", "ForkAfterAuthentication=no",
            "-L", f"127.0.0.1:{self.local_port}:127.0.0.1:{self.remote_port}", self.ssh_host,
        ]


def config_path() -> Path:
    return Path(os.environ.get("HERMES_BRIDGE_TOOL_CONFIG", "~/.config/hermes-bridge-tool/config.json")).expanduser()


def load_settings() -> Settings:
    path = config_path()
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError
        # Ignore other companion metadata while keeping the shared fields typed.
        settings = Settings(**{key: data[key] for key in asdict(Settings()) if key in data})
    except (OSError, ValueError, TypeError):
        raise ValueError(f"Cannot read bridge configuration at {path}. Expected a JSON object.") from None
    return settings.validate()


def private_write(path: Path, content: str):
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            name = handle.name
            os.chmod(name, 0o600)
            handle.write(content)
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def save_settings(settings: Settings):
    settings.validate()
    private_write(config_path(), json.dumps(asdict(settings), indent=2) + "\n")


def connection_settings() -> tuple[str, str]:
    settings = load_settings()
    url = os.environ.get("HERMES_API_URL", f"http://127.0.0.1:{settings.local_port}").rstrip("/")
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and not any((parsed.username, parsed.password, parsed.path, parsed.query, parsed.fragment))
        )
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("HERMES_API_URL must be a loopback HTTP(S) origin. Use the SSH tunnel.")
    key = os.environ.get("HERMES_API_KEY")
    if key is None:
        path = Path(os.environ.get("HERMES_API_KEY_FILE", settings.api_key_file)).expanduser()
        try:
            key = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise ValueError("Cannot read the Hermes API key. Open app Settings or run hermes-bridge-tool configure.") from None
    key = key.strip()
    if not key or any(not 33 <= ord(char) <= 126 for char in key):
        raise ValueError("The Hermes API key must be a nonempty token without whitespace.")
    return url, key
