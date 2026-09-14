"""Provision narrowly scoped runtime SSH keys through an existing admin login.

The remote helper is this stdlib-only module, sent over stdin. Nothing here runs
on import; provisioning is an explicit administrative action.
"""

import base64
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


MARKER = "Managed by Hermes Bridge Tool runtime SSH"
STATE = Path("/etc/hermes-bridge-tool")
DROPIN = Path("/etc/ssh/sshd_config.d/90-hermes-bridge-tool-runtime.conf")
NATIVE_DROPIN = Path("/etc/ssh/sshd_config.d/91-hermes-bridge-tool-native.conf")


def validate_user(user):
    if not isinstance(user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user) or user == "root":
        raise ValueError("Runtime SSH requires a non-root Linux username.")
    return user


def public_key(value):
    if not isinstance(value, str) or "\n" in value.strip() or "\r" in value:
        raise ValueError("Supply a single Ed25519 public key.")
    fields = value.strip().split()
    if len(fields) < 2 or fields[0] != "ssh-ed25519":
        raise ValueError("Runtime SSH requires an Ed25519 public key.")
    try:
        raw = base64.b64decode(fields[1], validate=True)
        if raw[:19] != b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" or len(raw) != 51:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Invalid Ed25519 public key.") from None
    return " ".join(fields[:2])


def validate_ports(ports):
    if not isinstance(ports, (tuple, list)) or not ports or len(ports) > 2:
        raise ValueError("Specify the Gateway and optional WebUI forwarding ports.")
    if any(type(port) is not int or not 1 <= port <= 65535 for port in ports):
        raise ValueError("Invalid forwarding port.")
    return sorted(set(ports))


def forwarding_key_line(key, ports=(8642, 8787)):
    allowed = ",".join(f'permitopen="127.0.0.1:{port}"' for port in validate_ports(ports))
    return f'restrict,port-forwarding,{allowed},command="/bin/false" {public_key(key)} hermes-bridge-tool:forward\n'


def validate_native_command(command):
    if (not isinstance(command, list) or not command or not isinstance(command[0], str)
            or not command[0].startswith("/") or any(not isinstance(arg, str) or not arg
            or any(ord(c) < 32 or ord(c) == 127 for c in arg) for arg in command)):
        raise ValueError("Native MCP command must be an argv list with an absolute executable and no control characters.")
    return shlex.join(command)


def native_key_line(key, command):
    forced = validate_native_command(command).replace("\\", "\\\\").replace('"', '\\"')
    return f'restrict,command="{forced}" {public_key(key)} hermes-bridge-tool:native\n'


def sshd_configuration(user, ports=(8642, 8787)):
    validate_user(user)
    destinations = " ".join(f"127.0.0.1:{port}" for port in validate_ports(ports))
    return f"""# {MARKER}
Match User {user}
    AuthorizedKeysFile /etc/hermes-bridge-tool/authorized_keys/%u
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding local
    PermitOpen {destinations}
    PermitListen none
    AllowStreamLocalForwarding no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
    PermitTunnel no
    PermitUserRC no
    MaxSessions 0
    ForceCommand /bin/false
Match all
"""


def native_sshd_configuration(user, command):
    validate_user(user)
    validate_native_command(command)
    return f"""# {MARKER}
Match User {user}
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding no
    AllowStreamLocalForwarding no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
    PermitTunnel no
    PermitUserRC no
    ForceCommand {shlex.join(command)}
Match all
"""


def merge_managed_key(existing, line, label="native"):
    """Replace only our tagged key, preserving every unrelated line byte-for-byte."""
    marker = "hermes-bridge-tool:" + label
    key_blob = line.split(" ssh-ed25519 ", 1)[1].split()[0]
    kept = []
    for current in existing.splitlines(keepends=True):
        if current.rstrip().endswith(" " + marker):
            continue
        if key_blob in current.split():
            raise ValueError("The runtime key already has an unrelated authorization; use a fresh dedicated key.")
        kept.append(current)
    text = "".join(kept)
    return text + ("\n" if text and not text.endswith("\n") else "") + line


def _safe_path(path):
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Runtime SSH setup refuses symlinked managed paths.")


def _write(path, text, mode=0o600, uid=0, gid=0):
    _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
            temporary = handle.name
            os.fchmod(handle.fileno(), mode)
            os.fchown(handle.fileno(), uid, gid)
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _run(command, **options):
    try:
        return subprocess.run(command, check=True, capture_output=True, text=True, timeout=30, **options).stdout
    except (OSError, subprocess.SubprocessError):
        raise ValueError("Runtime SSH administrative command failed; inspect the server's SSH configuration and service logs.") from None


def _effective_config(sshd, user, ports):
    raw = _run([sshd, "-T", "-C", f"user={user},host=localhost,addr=127.0.0.1"])
    settings = dict(line.split(" ", 1) for line in raw.splitlines() if " " in line)
    expected = {
        "allowtcpforwarding": "local", "allowstreamlocalforwarding": "no",
        "permitlisten": "none", "allowagentforwarding": "no", "x11forwarding": "no",
        "permittty": "no", "permittunnel": "no", "permituserrc": "no",
        "maxsessions": "0", "forcecommand": "/bin/false", "authenticationmethods": "publickey",
        "passwordauthentication": "no", "kbdinteractiveauthentication": "no",
        "authorizedkeysfile": "/etc/hermes-bridge-tool/authorized_keys/%u",
    }
    if any(settings.get(key) != value for key, value in expected.items()):
        raise ValueError("The SSH daemon does not apply the required runtime restrictions. Check sshd_config Include and Match rules.")
    if set(settings.get("permitopen", "").split()) != {f"127.0.0.1:{port}" for port in ports}:
        raise ValueError("The SSH daemon forwarding destinations do not match the runtime policy.")


def _native_login_preflight(account, sshd):
    """Return whether an unusable Linux account-lock marker needs repair.

    No password hash is logged or returned to callers. Accounts with a usable
    password (even under a lock prefix), custom auth sources, or other keys need
    manual setup so provisioning cannot repurpose unrelated access.
    """
    if account.pw_uid == 0:
        raise ValueError("Native MCP cannot run as root.")
    shadow = _run(["getent", "shadow", account.pw_name]).strip().split(":")
    if len(shadow) < 2 or shadow[0] != account.pw_name or shadow[1] not in {"!", "!!", "!*", "*"}:
        raise ValueError("Native SSH account has a password or custom login state. Configure restricted SSH manually; its password was not changed.")
    unlock = shadow[1].startswith("!")
    raw = _run([sshd, "-T", "-C", f"user={account.pw_name},host=localhost,addr=127.0.0.1"])
    settings = dict(line.split(" ", 1) for line in raw.splitlines() if " " in line)
    for name in ("authorizedkeyscommand", "trustedusercakeys", "authorizedprincipalscommand"):
        if settings.get(name, "none") != "none":
            raise ValueError("Native SSH has another authorization provider. Configure its restrictions manually.")
    if settings.get("authorizedkeysfile") != ".ssh/authorized_keys .ssh/authorized_keys2":
        raise ValueError("Native SSH uses custom authorized key paths. Configure its restrictions manually.")
    for filename in ("authorized_keys", "authorized_keys2"):
        path = Path(account.pw_dir) / ".ssh" / filename
        _safe_path(path)
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip() and not line.lstrip().startswith("#") and not line.rstrip().endswith(" hermes-bridge-tool:native"):
                    raise ValueError("Native SSH has unrelated authorized keys. Configure its restrictions manually; existing access was preserved.")
    return unlock


def _effective_native_config(sshd, user, command):
    raw = _run([sshd, "-T", "-C", f"user={user},host=localhost,addr=127.0.0.1"])
    settings = dict(line.split(" ", 1) for line in raw.splitlines() if " " in line)
    expected = {"authenticationmethods": "publickey", "passwordauthentication": "no",
                "kbdinteractiveauthentication": "no", "allowtcpforwarding": "no",
                "allowstreamlocalforwarding": "no", "allowagentforwarding": "no",
                "x11forwarding": "no", "permittty": "no", "permittunnel": "no",
                "permituserrc": "no", "forcecommand": shlex.join(command)}
    if any(settings.get(key) != value for key, value in expected.items()):
        raise ValueError("Effective native SSH restrictions do not match the fixed MCP command. Configure SSH manually; the account was not unlocked.")


def provision_native_login(account, command, sshd):
    unlock = _native_login_preflight(account, sshd)
    _safe_path(NATIVE_DROPIN)
    if NATIVE_DROPIN.exists() and (NATIVE_DROPIN.stat().st_uid != 0 or NATIVE_DROPIN.stat().st_mode & 0o022
            or not NATIVE_DROPIN.read_text().startswith("# " + MARKER + "\n")):
        raise ValueError("An unrelated or unsafe native SSH drop-in exists; it was not changed.")
    previous = NATIVE_DROPIN.read_text() if NATIVE_DROPIN.exists() else None
    try:
        _write(NATIVE_DROPIN, native_sshd_configuration(account.pw_name, command), mode=0o644)
        _run([sshd, "-t"])
        _effective_native_config(sshd, account.pw_name, command)
        _reload_ssh()
    except Exception:
        if previous is None:
            NATIVE_DROPIN.unlink(missing_ok=True)
        else:
            _write(NATIVE_DROPIN, previous, mode=0o644)
        raise
    if unlock:
        _run(["usermod", "--password", "*", account.pw_name])


def _reload_ssh():
    for service in ("ssh", "sshd"):
        check = subprocess.run(["systemctl", "is-active", "--quiet", service], capture_output=True, timeout=10)
        if check.returncode == 0:
            _run(["systemctl", "reload", service])
            return
    raise ValueError("No active systemd SSH service found. Configure runtime SSH manually for this service manager.")


def _allow_managed_public_key_login(account):
    if (account.pw_uid == 0 or account.pw_gecos != MARKER
            or account.pw_shell not in ("/usr/sbin/nologin", "/sbin/nologin")):
        raise ValueError("Refusing to change an unrelated account's login state.")
    # On Linux, useradd's default '!' disables the whole account when UsePAM=no.
    # '*' is an unusable password hash while still permitting public-key login.
    # Call only after the effective publickey-only SSH policy has been reloaded.
    _run(["usermod", "--password", "*", account.pw_name])


def _write_native_authorization(path, line, account):
    # Drop privileges before touching a user-controlled home directory. A racing
    # symlink can then never turn provisioning into a write with root privileges.
    writer = '''import json, os, pathlib, sys, tempfile
path_text, line = json.load(sys.stdin)
path = pathlib.Path(path_text)
if any(part.is_symlink() for part in (path, *path.parents)):
    raise SystemExit("Symlinked native SSH authorization path")
path.parent.mkdir(mode=0o700, exist_ok=True)
old = path.read_text() if path.exists() else ""
blob = line.split(" ssh-ed25519 ", 1)[1].split()[0]
kept = []
for current in old.splitlines(keepends=True):
    if current.rstrip().endswith(" hermes-bridge-tool:native"):
        continue
    if blob in current.split():
        raise SystemExit("Runtime key has an unrelated authorization")
    kept.append(current)
text = "".join(kept)
text += ("\\n" if text and not text.endswith("\\n") else "") + line
name = None
try:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        name = handle.name
        os.fchmod(handle.fileno(), 0o600)
        handle.write(text)
    os.replace(name, path)
finally:
    if name and os.path.exists(name):
        os.unlink(name)
'''
    _run([sys.executable, "-c", writer], input=json.dumps([str(path), line]),
         user=account.pw_uid, group=account.pw_gid, extra_groups=[])


def provision_on_server(request):
    """Linux root-only helper; no runtime sudo is installed or required."""
    import pwd
    if os.geteuid() != 0:
        raise ValueError("Runtime SSH provisioning requires an administrator with root or passwordless sudo.")
    user = validate_user(request.get("runtime_user", "hermes-bridge"))
    hermes_user = validate_user(request.get("hermes_user", "hermes"))
    if user == hermes_user:
        raise ValueError("Forwarding and Hermes execution must use separate Linux accounts.")
    ports = validate_ports(request.get("ports", [8642, 8787]))
    forward_line = forwarding_key_line(request["forwarding_public_key"], ports)
    native_key = request.get("native_public_key")
    native_line = native_key_line(native_key, request.get("native_command")) if native_key else None
    if native_key and public_key(native_key) == public_key(request["forwarding_public_key"]):
        raise ValueError("Forwarding and native MCP must use different dedicated keys.")
    native_path = None
    if native_line:
        try:
            hermes = pwd.getpwnam(hermes_user)
        except KeyError:
            raise ValueError("The Hermes execution account does not exist.") from None
        if hermes.pw_uid == 0:
            raise ValueError("Native MCP cannot execute as root.")
        native_path = Path(hermes.pw_dir) / ".ssh/authorized_keys"
        _safe_path(native_path)
        merge_managed_key(native_path.read_text() if native_path.exists() else "", native_line)
    _safe_path(STATE)
    _safe_path(DROPIN)
    for managed in (STATE, STATE / "authorized_keys", DROPIN, DROPIN.parent):
        if managed.exists() and (managed.stat().st_uid != 0 or managed.stat().st_mode & 0o022):
            raise ValueError("Runtime SSH managed paths must be root-owned and not writable by other users.")
    forward_path = STATE / "authorized_keys" / user
    _safe_path(forward_path)
    forward_text = merge_managed_key(forward_path.read_text() if forward_path.exists() else "", forward_line, "forward")
    if DROPIN.exists() and not DROPIN.read_text().startswith("# " + MARKER + "\n"):
        raise ValueError("An unrelated runtime SSH drop-in exists; it was not changed.")
    try:
        account = pwd.getpwnam(user)
    except KeyError:
        account = None
    if account is not None and (account.pw_uid == 0 or account.pw_gecos != MARKER
                                or account.pw_shell not in ("/usr/sbin/nologin", "/sbin/nologin")):
        raise ValueError("The forwarding username belongs to an unrelated account; choose a new username.")
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    _run([sshd, "-t"])
    if account is None:
        nologin = shutil.which("nologin") or "/usr/sbin/nologin"
        _run(["useradd", "--system", "--create-home", "--shell", nologin, "--comment", MARKER, user])
        account = pwd.getpwnam(user)
    old_config = DROPIN.read_text() if DROPIN.exists() else None
    try:
        _write(DROPIN, sshd_configuration(user, ports), mode=0o644)
        _run([sshd, "-t"])
        _effective_config(sshd, user, ports)
        _reload_ssh()
    except Exception:
        if old_config is None:
            DROPIN.unlink(missing_ok=True)
        else:
            _write(DROPIN, old_config, mode=0o644)
        # No runtime key is added until restrictions have passed validation and reload.
        raise
    _allow_managed_public_key_login(account)
    _write(forward_path, forward_text)
    # sshd reads this root-owned path before dropping privileges. Permit traversal
    # for StrictModes implementations that inspect it using the target account.
    os.chmod(STATE, 0o755)
    os.chmod(STATE / "authorized_keys", 0o755)
    os.chmod(STATE / "authorized_keys" / user, 0o644)
    if native_path:
        provision_native_login(hermes, request["native_command"], sshd)
        _write_native_authorization(native_path, native_line, hermes)
    return {"ok": True, "runtime_user": user, "hermes_user": hermes_user,
            "ports": ports, "native_configured": native_line is not None}


def generate_runtime_key(path) -> str:
    """Return a public key, generating a private Ed25519 identity once (mode 600)."""
    path = Path(path).expanduser()
    _safe_path(path)
    _safe_path(Path(str(path) + ".pub"))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        _run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "hermes-bridge-tool runtime", "-f", str(path)])
    os.chmod(path, 0o600)
    return public_key(_run(["ssh-keygen", "-y", "-P", "", "-f", str(path)]))


def provision_runtime_ssh(admin_host, forwarding_public_key, *, runtime_user="hermes-bridge",
                          hermes_user="hermes", native_public_key=None, native_command=None,
                          ports=(8642, 8787)):
    if not isinstance(admin_host, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]{0,254}", admin_host):
        raise ValueError("Admin host must be an existing SSH alias or user@host.")
    validate_user(runtime_user)
    validate_user(hermes_user)
    forwarding_key_line(forwarding_public_key, ports)
    if native_public_key:
        native_key_line(native_public_key, native_command)
    request = dict(forwarding_public_key=forwarding_public_key, runtime_user=runtime_user,
                   hermes_user=hermes_user, native_public_key=native_public_key,
                   native_command=native_command, ports=list(ports))
    source = Path(__file__).read_text()
    program = source + "\ntry:\n    print(json.dumps(provision_on_server(" + repr(request) + ")))\nexcept Exception as error:\n    print(json.dumps({'ok': False, 'error': str(error)}))\n    raise SystemExit(1)\n"
    remote = "if [ \"$(id -u)\" = 0 ]; then exec python3 -; else exec sudo -n python3 -; fi"
    command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", "ForwardAgent=no", "-o", "ConnectTimeout=10", admin_host, remote]
    try:
        completed = subprocess.run(command, input=program, text=True, capture_output=True, timeout=120)
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        raise ValueError("Runtime SSH setup did not return a result. Check admin SSH access; inspect server state before retrying.") from None
    if completed.returncode or not isinstance(result, dict) or result.get("ok") is not True:
        error = result.get("error", "Check the remote SSH configuration.") if isinstance(result, dict) else "Invalid helper response."
        raise ValueError("Runtime SSH setup failed: " + str(error))
    return result
