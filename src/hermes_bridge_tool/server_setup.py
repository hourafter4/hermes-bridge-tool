"""Standalone stdlib helper: run on the server as the Hermes service user.

Only --json returns a credential, for pairing over an authenticated SSH channel.
Configuration follows https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/
"""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


INSTALL_URL = "https://hermes-agent.nousresearch.com/docs/getting-started/installation/"
NAMES = ("API_SERVER_ENABLED", "API_SERVER_HOST", "API_SERVER_PORT", "API_SERVER_KEY")
ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?(" + "|".join(NAMES) + r")\b(.*)$")
KEY = re.compile(r"[A-Za-z0-9._~+/=-]{1,512}\Z")
OBSERVER_MARKER = "# Managed by Hermes Bridge Tool; session observer plugin.\n"
OBSERVER_MANIFEST = OBSERVER_MARKER + """name: hermes-bridge-tool
version: 0.1.0
description: Observe Hermes turns for Hermes Bridge Tool
provides_hooks:
  - pre_llm_call
  - on_session_end
"""


def observer_files(home):
    """Validate ownership boundaries before altering either the plugin or API config."""
    plugin = home / "plugins" / "hermes-bridge-tool"
    manifest, module = plugin / "plugin.yaml", plugin / "__init__.py"
    for path in (home / "plugins", plugin, manifest, module):
        if path.is_symlink():
            raise ValueError("Session observer plugin paths must not be symlinks. Move the link before setup.")
    for path in (home / "plugins", plugin):
        if path.exists() and not path.is_dir():
            raise ValueError("Session observer plugin directory is occupied by a file. Move it before setup.")
    for path in (manifest, module):
        if path.exists() and not path.is_file():
            raise ValueError("Session observer plugin files must be regular files. Move the conflicting path before setup.")
    if plugin.exists() and (not plugin.is_dir() or not manifest.is_file()
                            or not manifest.read_text().startswith(OBSERVER_MARKER)):
        raise ValueError("An unrelated hermes-bridge-tool plugin already exists. Move it before installing the session observer.")
    source = globals().get("OBSERVER_SOURCE")
    if source is None:
        sibling = Path(__file__).with_name("observer_plugin.py")
        if not sibling.is_file():
            raise ValueError("Observer source is missing. Run hermes-bridge-tool setup --observe-sessions from a complete installation.")
        source = sibling.read_text()
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Observer source is empty. Reinstall Hermes Bridge Tool before enabling session observation.")
    try:
        compile(source, "hermes-bridge-tool observer", "exec")
    except SyntaxError:
        raise ValueError("Observer source is invalid. Reinstall Hermes Bridge Tool before enabling session observation.") from None
    changed = not (manifest.is_file() and manifest.read_text() == OBSERVER_MANIFEST
                   and module.is_file() and module.read_text() == source
                   and stat.S_IMODE(plugin.stat().st_mode) == 0o700
                   and all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in (manifest, module)))
    return plugin, source, changed


def install_observer(plugin, source):
    plugin.parent.mkdir(mode=0o700, exist_ok=True)
    plugin.mkdir(mode=0o700, exist_ok=True)
    plugin.chmod(0o700)
    private_replace(plugin / "__init__.py", source)
    private_replace(plugin / "plugin.yaml", OBSERVER_MANIFEST)


def parse_value(value):
    """Parse one managed dotenv scalar without executing or expanding it."""
    value = value.strip()
    if value.startswith(("'", '"')):
        quote = value[0]
        match = re.fullmatch(r"(['\"])((?:\\.|[^\\])*?)\1\s*(#.*)?", value)
        if not match or quote in re.sub(r"\\.", "", match[2]):
            raise ValueError("Malformed quoted API setting in .env; fix it before pairing.")
        result = re.sub(r"\\(['\"\\])", r"\1", match[2])
        return result, (" " + match[3]) if match[3] else ""
    parts = re.split(r"\s+#", value, maxsplit=1)
    return parts[0].strip(), (" #" + parts[1]) if len(parts) == 2 else ""


def prepare_env(original, port):
    lines, entries, keys = original.splitlines(keepends=True), {}, []
    for index, line in enumerate(lines):
        match = ASSIGNMENT.match(line)
        if not match:
            continue
        if not match[2].lstrip().startswith("="):
            raise ValueError("Malformed API setting in .env; fix it before pairing.")
        value, comment = parse_value(match[2].lstrip()[1:])
        entries[index] = (match[1], comment)
        if match[1] == "API_SERVER_KEY":
            keys.append(value)
    if len(set(keys)) > 1:
        raise ValueError("Conflicting API_SERVER_KEY entries in .env; keep the active key once before pairing.")
    key = keys[-1] if keys else ""
    if key and not KEY.fullmatch(key):
        raise ValueError("Existing API key must use URL-safe or base64 characters (up to 512); update it explicitly before pairing.")
    key = key or secrets.token_urlsafe(32)
    desired = dict(zip(NAMES, ("true", "127.0.0.1", str(port), key)))
    output, seen = [], set()
    for index, line in enumerate(lines):
        if index not in entries:
            output.append(line)
            continue
        name, comment = entries[index]
        if name not in seen:
            output.append(f"{name}={desired[name]}{comment}\n")
            seen.add(name)
        elif comment:
            output.append(comment.lstrip() + "\n")
    if output and not output[-1].endswith("\n"):
        output[-1] += "\n"
    output.extend(f"{name}={desired[name]}\n" for name in NAMES if name not in seen)
    return "".join(output), key


def hermes_command(home):
    # Official installer links the command into ~/.local/bin (or /usr/local/bin).
    found = shutil.which("hermes")
    if found:
        return [found]
    for candidate in (Path.home() / ".local/bin/hermes", Path("/usr/local/bin/hermes")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return [str(candidate)]
    # Official source installations use venv/bin/python plus the repo's hermes script.
    for repo in (home / "hermes-agent", Path("/usr/local/lib/hermes-agent")):
        launcher = repo / "venv/bin/hermes"
        if launcher.is_file() and os.access(launcher, os.X_OK):
            return [str(launcher)]
        python, script = repo / "venv/bin/python", repo / "hermes"
        if python.is_file() and os.access(python, os.X_OK) and script.is_file():
            return [str(python), str(script)]
    raise ValueError("Hermes executable not found. Install/configure Hermes first: " + INSTALL_URL)


def private_replace(path, content):
    descriptor, temporary = tempfile.mkstemp(prefix=".hermes-bridge-tool-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def wait_ready(port, key, seconds=10):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/capabilities",
                                     headers={"Authorization": "Bearer " + key})
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with opener.open(request, timeout=min(2, max(0.1, deadline - time.monotonic()))) as response:
                body = json.loads(response.read(1_000_001))
            features = body.get("features", {}) if isinstance(body, dict) else {}
            if not isinstance(features, dict) or not all(features.get(name) is True for name in ("run_submission", "run_status", "run_stop")):
                raise ValueError("Hermes API lacks required Runs features. Upgrade Hermes explicitly, then pair again.")
            return
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise ValueError("Hermes API lacks /v1/capabilities. Upgrade Hermes explicitly, then pair again.") from None
            if error.code < 500:
                raise ValueError(f"Hermes API returned HTTP {error.code}. Check gateway config, credentials and port.") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            pass
        except (json.JSONDecodeError, UnicodeError):
            raise ValueError("Hermes API returned invalid capabilities JSON. Check the gateway port/version.") from None
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    raise ValueError("Hermes API did not become ready within 10 seconds. Check gateway logs; custom services need their own restart command.")


def wait_observer_ready(port, key, seconds=10):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/hermes-bridge-tool/v1/turns?session_id=hermes-bridge-tool-readiness&limit=1",
        headers={"Authorization": "Bearer " + key},
    )
    hint = "Session observer is unavailable. Update Hermes to a version with plugin hook/API route support and inspect gateway plugin errors."
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with opener.open(request, timeout=min(2, max(0.1, deadline - time.monotonic()))) as response:
                body = json.loads(response.read(1_000_001))
            if not isinstance(body, dict) or body.get("object") != "list" or not isinstance(body.get("data"), list):
                raise ValueError(hint)
            return
        except urllib.error.HTTPError as error:
            if error.code < 500:
                raise ValueError(hint + f" HTTP {error.code}.") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            pass
        except (json.JSONDecodeError, UnicodeError):
            raise ValueError(hint) from None
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    raise ValueError(hint)


def configure(args, result):
    home = Path(args.home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser().resolve()
    result.update(hermes_home=str(home), port=args.port)
    if not 1 <= args.port <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    env_file = home / ".env"
    if not home.is_dir() or not any((home / name).is_file() for name in (".env", "config.yaml")):
        raise ValueError("No configured Hermes home found. Run as the Hermes service user or specify --home. Setup: " + INSTALL_URL)
    if env_file.is_symlink():
        raise ValueError("Hermes .env is a symlink; configure its target explicitly before pairing.")
    command = hermes_command(home)
    original = ""
    if env_file.exists():
        with env_file.open(encoding="utf-8", newline="") as handle:
            original = handle.read()
    updated, key = prepare_env(original, args.port)
    needs_write = updated != original or (env_file.exists() and stat.S_IMODE(env_file.stat().st_mode) != 0o600)
    observe_sessions = getattr(args, "observe_sessions", False)
    if observe_sessions:
        result.update(observer_requested=True, observer_ready=False)
        plugin, observer_source, observer_changed = observer_files(home)
    if args.check:
        result.update(would_change=needs_write, checked=True)
        if observe_sessions:
            result["observer_would_change"] = observer_changed
        return
    if needs_write:
        if env_file.exists():
            descriptor, backup = tempfile.mkstemp(prefix=".env.hermes-bridge-tool-backup-", dir=home)
            os.close(descriptor)
            private_replace(Path(backup), original)
            result["backup"] = backup
        private_replace(env_file, updated)
        result["changed"] = True
    if args.restart or observe_sessions:
        environment = dict(os.environ, HERMES_HOME=str(home))
        runtime = Path("/run/user") / str(os.getuid())
        if runtime.is_dir():
            environment.setdefault("XDG_RUNTIME_DIR", str(runtime))
            environment.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=" + str(runtime / "bus"))
    if observe_sessions:
        if observer_changed:
            install_observer(plugin, observer_source)
        result["observer_changed"] = observer_changed
        try:
            process = subprocess.run(command + ["plugins", "enable", "hermes-bridge-tool", "--no-allow-tool-override"], env=environment,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, timeout=30, check=False)
        except subprocess.TimeoutExpired:
            raise ValueError("Session observer enable timed out. Check Hermes plugin support and plugin errors before retrying.") from None
        if process.returncode:
            raise ValueError("Could not enable the session observer. Update Hermes to a version with plugin hook/API route support and inspect plugin errors.")
        result["observer_enabled"] = True
    if args.restart:
        try:
            # Never forward gateway output: it can contain provider credentials.
            process = subprocess.run(command + ["gateway", "restart"], env=environment,
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, timeout=40, check=False)
        except subprocess.TimeoutExpired:
            raise ValueError("Gateway restart timed out. Check gateway logs; restart custom services through their service manager, then retry.") from None
        if process.returncode:
            raise ValueError(f"Gateway restart failed (exit {process.returncode}). Check gateway logs; custom services need their own restart command.")
        result["restarted"] = True
        wait_ready(args.port, key)
        if observe_sessions:
            wait_observer_ready(args.port, key)
            result["observer_ready"] = True
        result["ready"] = True
    if args.json:
        result["api_key"] = key


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", help="Hermes data directory (default: HERMES_HOME or ~/.hermes)")
    parser.add_argument("--port", type=int, default=8642)
    parser.add_argument("--restart", action="store_true", help="Restart the Hermes gateway and verify readiness")
    parser.add_argument("--observe-sessions", action="store_true", help="Install and enable the Hermes Bridge Tool session observer plugin")
    parser.add_argument("--check", action="store_true", help="Validate and show planned changes without writing or restarting")
    parser.add_argument("--json", action="store_true", help="Machine pairing output; includes the API key on successful setup")
    args = parser.parse_args(argv)
    result = dict(ok=False, changed=False, restarted=False, ready=False)
    try:
        configure(args, result)
        result["ok"] = True
    except (OSError, UnicodeError):
        result["error"] = "Cannot access Hermes configuration or executable. Check paths, ownership and permissions as the service user."
    except ValueError as error:
        result["error"] = str(error)
    if args.json:
        print(json.dumps(result))
    elif result["ok"]:
        print("Hermes configuration check passed." if args.check else "Hermes API configured on 127.0.0.1:" + str(args.port) + ".")
        if "backup" in result:
            print("Private configuration backup: " + result["backup"])
        if not args.check:
            print("Gateway ready." if result["ready"] else "Restart your Hermes gateway to apply changes.")
    else:
        print("Setup failed: " + result["error"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
