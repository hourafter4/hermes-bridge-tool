"""Explicit operator migration; verify new storage before changing configuration."""

from dataclasses import replace
from pathlib import Path

from .config import load_settings, save_settings, read_credential, write_credential, webui_auth_headers


def migrate(target, *, remove_files=False):
    if target not in ("file", "keychain") or (remove_files and target != "keychain"):
        raise ValueError("Choose file or keychain storage; --remove-files applies only when moving to Keychain.")
    settings = load_settings()
    changed = []
    obsolete = []
    for backend in ("gateway", "webui"):
        old_store = getattr(settings, f"{backend}_credential_store")
        path = Path(settings.api_key_file if backend == "gateway" else settings.webui_auth_file).expanduser()
        if old_store == "file" and not path.exists():
            continue
        if path.is_symlink():
            raise ValueError("Credential migration refuses symbolic links.")
        content = read_credential(settings, backend)
        if backend == "webui":
            webui_auth_headers(content=content)
        elif not content.strip() or any(not 33 <= ord(char) <= 126 for char in content.strip()):
            raise ValueError("Invalid Gateway credential; migration stopped.")
        updated = replace(settings, **{f"{backend}_credential_store": target})
        write_credential(updated, backend, content)
        if read_credential(updated, backend) != content:
            raise ValueError("New credential storage could not be verified; existing settings are preserved.")
        settings = updated
        changed.append(backend)
        if old_store == "file" and target == "keychain" and remove_files:
            obsolete.append(path)
    save_settings(settings)
    for path in obsolete:
        path.unlink()
    return {"store": target, "migrated": changed, "removed_plaintext_files": len(obsolete),
            "hint": "Environment credential overrides and external backups are independent of this migration."}
