"""Configuration shared by the CLI, MCP server, and macOS companion."""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import tempfile
from hashlib import sha256
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    ssh_host: str = "hermes-server"
    ssh_user: str = ""
    ssh_identity_file: str = ""
    local_port: int = 18642
    remote_port: int = 8642
    api_key_file: str = "~/.config/hermes-bridge-tool/api-key"
    gateway_url: str = ""
    webui_url: str = ""
    webui_auth_file: str = "~/.config/hermes-bridge-tool/webui-auth.json"
    webui_ssh: bool = False
    webui_local_port: int = 18787
    webui_remote_port: int = 8787
    gateway_credential_store: str = "file"
    webui_credential_store: str = "file"

    def validate(self):
        if not isinstance(self.ssh_user, str) or (self.ssh_user and not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", self.ssh_user)):
            raise ValueError("SSH user must be a Linux account name.")
        if not isinstance(self.ssh_identity_file, str) or any(ord(char) < 32 for char in self.ssh_identity_file):
            raise ValueError("SSH identity must be a local file path without control characters.")
        for backend in ("gateway", "webui"):
            if getattr(self, f"{backend}_credential_store") not in ("file", "keychain"):
                raise ValueError("Credential storage must be file or keychain.")
        if not isinstance(self.ssh_host, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]{0,254}", self.ssh_host):
            raise ValueError("SSH host must be an SSH alias or user@hostname, without spaces or options.")
        for port in (self.local_port, self.remote_port, self.webui_local_port, self.webui_remote_port):
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("Ports must be integers between 1 and 65535.")
        for name in ("api_key_file", "webui_auth_file"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a nonempty file path.")
        for name in ("gateway_url", "webui_url"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a URL string.")
            if value:
                endpoint_settings(value, name)
        if type(self.webui_ssh) is not bool:
            raise ValueError("webui_ssh must be a boolean.")
        if self.webui_ssh:
            url = self.webui_url or f"http://127.0.0.1:{self.webui_local_port}"
            if not _matches_forward(url, self.webui_local_port):
                raise ValueError("WebUI SSH requires a loopback URL using webui_local_port.")
            if self.gateway_uses_ssh() and self.local_port == self.webui_local_port:
                raise ValueError("Gateway and WebUI SSH forwards must use different local ports.")
        return self

    def gateway_uses_ssh(self) -> bool:
        return not self.gateway_url or _matches_forward(self.gateway_url, self.local_port)

    def ssh_forwards(self) -> list[str]:
        forwards = []
        if self.gateway_uses_ssh() and (not webui_configured(self) or gateway_configured(self)):
            forwards.append(f"127.0.0.1:{self.local_port}:127.0.0.1:{self.remote_port}")
        if self.webui_ssh:
            forwards.append(f"127.0.0.1:{self.webui_local_port}:127.0.0.1:{self.webui_remote_port}")
        return forwards

    def ssh_command(self) -> list[str]:
        self.validate()
        forwards = self.ssh_forwards()
        if not forwards:
            raise ValueError("No SSH forwards are configured. Direct HTTPS connections do not need a tunnel.")
        return [
            "ssh", "-N", "-T", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
            "-o", "StrictHostKeyChecking=yes", "-o", "ControlMaster=no",
            "-o", "ForwardAgent=no", "-o", "ForwardX11=no", "-o", "PermitLocalCommand=no",
            "-o", "ControlPath=none", "-o", "ControlPersist=no", "-o", "ForkAfterAuthentication=no",
            *(["-l", self.ssh_user] if self.ssh_user else []),
            *(["-i", str(Path(self.ssh_identity_file).expanduser()), "-o", "IdentitiesOnly=yes"] if self.ssh_identity_file else []),
            *(argument for forward in forwards for argument in ("-L", forward)), self.ssh_host,
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
    path = config_path()
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(data, dict):
            raise ValueError
    except (OSError, ValueError):
        raise ValueError(f"Cannot preserve existing configuration at {path}; expected a JSON object.") from None
    data.update(asdict(settings))
    private_write(path, json.dumps(data, indent=2) + "\n")


def endpoint_settings(url: str, label: str = "API URL") -> str:
    """Validate a base URL before loading credentials; allow reverse-proxy path prefixes."""
    try:
        if not isinstance(url, str) or any(char.isspace() or ord(char) < 32 for char in url):
            raise ValueError
        parsed = urlsplit(url)
        valid = (
            bool(parsed.hostname)
            and (parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}))
            and parsed.username is None and parsed.password is None
            and "?" not in url and "#" not in url
            and "\\" not in url
        )
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            valid = False
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError(f"{label} must use HTTPS or loopback HTTP, without URL credentials, query, or fragment.")
    return url.rstrip("/")


def _matches_forward(url: str, port: int) -> bool:
    try:
        parsed = urlsplit(url)
        return parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port == port
    except ValueError:
        return False


def webui_auth_headers(path: Path | None = None, *, content: str | None = None) -> dict[str, str]:
    try:
        data = json.loads(content if content is not None else path.expanduser().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ValueError("Cannot read WebUI authentication headers. Run hermes-bridge-tool configure-webui.") from None
    if not isinstance(data, dict):
        raise ValueError("WebUI authentication file must contain a JSON object of HTTP header names and values.")
    for name, value in data.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                or name.lower() in {"host", "content-length", "transfer-encoding", "connection"}
                or not isinstance(value, str) or any(ord(char) < 32 or ord(char) > 126 for char in value)):
            raise ValueError("WebUI authentication file contains an invalid HTTP header.")
    if len({name.lower() for name in data}) != len(data):
        raise ValueError("WebUI authentication file contains duplicate HTTP header names.")
    return data


def gateway_configured(settings: Settings | None = None) -> bool:
    settings = settings or load_settings()
    if "HERMES_API_KEY" in os.environ:
        return True
    if settings.gateway_credential_store == "keychain":
        return True
    return Path(os.environ.get("HERMES_API_KEY_FILE", settings.api_key_file)).expanduser().is_file()


def webui_configured(settings: Settings | None = None) -> bool:
    settings = settings or load_settings()
    return bool(os.environ.get("HERMES_WEBUI_URL", settings.webui_url) or settings.webui_ssh)


def webui_connection_settings() -> tuple[str, dict[str, str]]:
    settings = load_settings()
    url = os.environ.get("HERMES_WEBUI_URL", settings.webui_url)
    if not url and settings.webui_ssh:
        url = f"http://127.0.0.1:{settings.webui_local_port}"
    if not url:
        raise ValueError("WebUI is not configured. Run hermes-bridge-tool configure-webui --url https://your-webui-host.")
    url = endpoint_settings(url, "WebUI URL")
    path = Path(os.environ.get("HERMES_WEBUI_AUTH_FILE", settings.webui_auth_file))
    if settings.webui_credential_store == "keychain" and "HERMES_WEBUI_AUTH_FILE" not in os.environ:
        return url, webui_auth_headers(content=read_credential(settings, "webui"))
    return url, webui_auth_headers(path)


def connection_settings() -> tuple[str, str]:
    settings = load_settings()
    url = endpoint_settings(os.environ.get("HERMES_API_URL", settings.gateway_url or f"http://127.0.0.1:{settings.local_port}"), "Gateway URL")
    key = os.environ.get("HERMES_API_KEY")
    if key is None:
        path = Path(os.environ.get("HERMES_API_KEY_FILE", settings.api_key_file)).expanduser()
        try:
            key = (read_credential(settings, "gateway") if "HERMES_API_KEY_FILE" not in os.environ
                   else path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            raise ValueError("Cannot read the Hermes API key. Open app Settings or run hermes-bridge-tool configure.") from None
    key = key.strip()
    if not key or any(not 33 <= ord(char) <= 126 for char in key):
        raise ValueError("The Hermes API key must be a nonempty token without whitespace.")
    return url, key


def credential_account(backend):
    return sha256(str(config_path().resolve()).encode()).hexdigest() + ":" + backend


def read_credential(settings, backend):
    from .credentials import load_secret
    path = settings.api_key_file if backend == "gateway" else settings.webui_auth_file
    return load_secret(credential_account(backend), backend=getattr(settings, f"{backend}_credential_store"), file_path=path)


def write_credential(settings, backend, content):
    from .credentials import store_secret
    path = settings.api_key_file if backend == "gateway" else settings.webui_auth_file
    store_secret(credential_account(backend), content, backend=getattr(settings, f"{backend}_credential_store"), file_path=path)
