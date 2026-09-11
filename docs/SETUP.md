# Connect your Hermes server

Hermes Bridge runs locally. Your remote Hermes gateway needs its authenticated
API enabled. These instructions assume the gateway belongs to the `hermes` user
and uses the default `~/.hermes` profile; use your actual user/profile if different.

## 1. Establish SSH access

Use an existing SSH alias, such as `hetzner`, or `user@hostname`:

```sh
ssh hetzner true
```

Complete any initial host-key verification in your terminal. The menu bar app
uses noninteractive SSH and your existing SSH agent; it cannot ask for an SSH
password or key passphrase. Load your key into your SSH agent if necessary.

## 2. Enable the API

On the server, inspect the installed Hermes version and gateway:

```sh
ssh hetzner
su - hermes
hermes --version
hermes gateway status
```

If an API key already exists, reuse it. Otherwise generate one on your Mac into a
private file, without printing it:

```sh
python3 - <<'PY'
import os
import secrets
from pathlib import Path
directory = Path.home() / '.config/hermes-bridge'
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
fd = os.open(directory / 'api-key', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as handle:
    handle.write(secrets.token_urlsafe(32) + '\n')
PY
pbcopy < ~/.config/hermes-bridge/api-key
```

In a private editor on the server, update the active profile's `~/.hermes/.env`:

```dotenv
API_SERVER_ENABLED=true
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8642
API_SERVER_KEY=PASTE_YOUR_KEY_HERE
```

Replace the placeholder with the key. Keep one definition of each setting and
preserve the rest of the file. Run `chmod 600 ~/.hermes/.env`, then restart the
existing gateway through its service manager. For a standard Hermes-managed
gateway, use `hermes gateway restart`. For a container or custom systemd service,
restart that existing service. Do not start a second gateway.

Clear the Mac clipboard after saving: `pbcopy < /dev/null`. Keep the API bound to
loopback; the SSH tunnel provides access. See the [official API configuration](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/).

## 3. Configure the local companion

From the Hermes Bridge repository, run:

```sh
uv sync --locked
uv run hermes-bridge configure --host hetzner
```

Enter the server's existing API key privately, or press Enter to keep the key
already saved in step 2. You can also enter these settings in the menu bar app.

Choose **Connect** in the app, or run `uv run hermes-bridge tunnel` in a terminal.
Then run `uv run hermes-bridge doctor` from another terminal. It should report
`"ready": true`. This verifies authentication and Runs API support, not that the
remote model provider is funded or configured correctly.

If the API returns `404` for `/v1/capabilities`, or the required run features are
missing, check your installed Hermes version before submitting work.

## 4. Add your coding harness

Follow [the plugin/registration guide](PLUGINS.md). Restart or reload the harness,
then call `hermes_check` before a small first task.

## Migrating the original Monemetrics bridge

The code now lives entirely in this repository. Register `scripts/hermes-mcp` from
here to replace any registration pointing to `deploy/hermes/mcp_bridge.py`.
The old key file can be reused: set `api_key_file` in the shared JSON configuration
to `~/.config/hermes-mcp/api-key`, or enter the same key through **Settings**.
Stop any manually opened tunnel before using **Connect** on the same local port.

## Troubleshooting

- SSH exits immediately: verify the alias, SSH agent, and host key in a terminal;
  check whether another process already uses the local port.
- API unavailable: check the remote gateway and its loopback listener.
- `401` or `403`: the key must match the active gateway profile.
- Missing tools: check the launcher path and uv installation, then reload the harness.
- Pending approval: resolve it in Hermes. The bridge does not approve remote actions.
- Missing completed run: check Hermes's session history before resubmitting work.

Keep credentials out of chat, shell command arguments, and version control.
