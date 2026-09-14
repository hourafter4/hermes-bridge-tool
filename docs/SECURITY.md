# Security model

Hermes Bridge Tool carries instructions from a coding client to a remote Hermes
agent. Granting control can allow that client to trigger the agent's server-side
tools. This guide describes what the bridge restricts and which permissions
remain with the local operating system, SSH server, and Hermes installation.

## Start with monitoring

The default mode permits reads and connection recovery. Task mutations and
native platform message delivery are denied before upstream requests are sent.
Inspect the current policy without contacting Hermes:

```sh
hermes-bridge-tool security status
```

An operator can grant task control in their own interactive terminal:

```sh
hermes-bridge-tool security mode control
```

The command requires entering `ENABLE`. Native platform message delivery has a
separate grant, effective only while control mode is enabled:

```sh
hermes-bridge-tool security messages on
```

These decisions apply to all coding clients using that local configuration.
Remote approval responses remain disabled; resolve approvals directly in Hermes.
Agents should not enable their own access. Increasing access has no
noninteractive confirmation flag.

Return to monitoring to remove task control and clear the messaging grant:

```sh
hermes-bridge-tool security mode monitor
```

## Lock access

```sh
hermes-bridge-tool security lock
```

Locking blocks new upstream reads, writes, and reconnects, and closes the shared
SSH tunnel. Policy status remains available without health probes. Work already
accepted by Hermes may continue, and an in-flight request may already have
reached the server. The lock does not revoke API credentials, close independently
started SSH clients, or undo remote actions.

Unlock in your own terminal with `hermes-bridge-tool security unlock`, followed
by the `ENABLE` confirmation. This restores the configured mode; it does not
automatically grant additional task or messaging permissions.

## Separate setup access from runtime SSH

Administrative pairing and everyday forwarding have different jobs. The
restricted runtime helper uses an existing administrator SSH login only during
provisioning. It creates a dedicated non-root forwarding account and installs
separate keys for forwarding and optional native MCP execution.

| Identity | Allowed runtime access |
| --- | --- |
| Forwarding account | Local TCP forwarding to configured loopback Gateway/WebUI ports, normally `127.0.0.1:8642` and `127.0.0.1:8787`. |
| Native MCP key | A fixed command running under the existing non-root Hermes Linux account. |
| Administrator | Setup and maintenance; unnecessary for routine bridge connections. |

The forwarding account has no shell sessions. Its SSH daemon rule disallows
remote and Unix-socket forwarding, agent/X11 forwarding, PTYs, tunnels, and user
SSH startup scripts. Its key also restricts forwarding destinations. The native
key uses a forced command with forwarding and PTYs disabled; runtime execution
does not use `sudo`. Hermes's own tools still run with the Hermes account's
permissions.

Native setup also installs a separate managed SSH rule forcing that exact MCP
command for the Hermes account, with password authentication and all forwarding
disabled. It proceeds only when the account has an unusable password marker and
no unrelated keys, certificate authority, custom key paths, or authorization
provider. Existing usable passwords, including passwords underneath an account
lock, are preserved by refusing automatic setup. Configure a restricted native
SSH login manually when the Hermes account already serves another SSH purpose.

On Linux servers using `UsePAM=no`, a locked account can reject even an authorized
SSH key. After validating and reloading the restrictions, setup uses the unusable
password marker `*` for these narrowly scoped accounts. This enables public-key
authentication without assigning a usable password. It does not change the
Hermes user's filesystem permissions, groups, or non-SSH execution environment.

Provisioning uses an isolated managed SSH drop-in, validates effective daemon
settings, and reloads the existing systemd SSH service before authorizing the
forwarding key. It refuses an unrelated existing forwarding account or drop-in
and preserves unrelated native `authorized_keys` entries. Custom SSH includes,
global access rules, and non-systemd service managers may need administrator
configuration. A failed setup can leave its newly created account in place;
inspect server state before retrying.

The helper implementation is in
[`runtime_ssh.py`](../src/hermes_bridge_tool/runtime_ssh.py). These restrictions
use OpenSSH's documented
[daemon forwarding controls](https://man.openbsd.org/sshd_config) and
[authorized-key restrictions](https://man.openbsd.org/sshd.8).

Private runtime keys remain local and should be separate from administrator
identities. Use strict host-key verification. Revoke a compromised runtime key
in the corresponding server authorization file and replace it; a bridge policy
lock is not key revocation.

## Credential storage

The bridge supports explicit `file` or macOS `keychain` storage for each HTTP
backend. File storage writes credentials atomically with mode `600`. Keychain
storage calls Apple's Security framework directly through `ctypes`: credentials
are not passed to a `security` subprocess, shell arguments, or command logs.
Keychain items use the service name `hermes-bridge-tool` and a backend-specific
account identity.

If Keychain is unavailable, locked, or access is denied, the request fails. The
bridge does not silently copy the credential into a file. Select file storage
explicitly when needed. Changing storage does not itself revoke old credentials
or guarantee deletion of copies made by earlier software; verify a migration
before removing its previous copy.

Keychain protects stored items according to macOS access controls. It does not
make secrets inaccessible to an already authorized local process. A running
bridge must hold credentials in memory to authenticate requests. The Python
implementation does not promise reliable erasure of immutable strings from
process memory. See Apple's
[Keychain item API](https://developer.apple.com/documentation/security/secitemadd(_:_:))
for the underlying storage mechanism.

## Boundaries

The local policy is a bridge access control, not an operating-system sandbox.
A process running as your user may edit configuration, read file credentials,
invoke another HTTP/SSH client, or control a pseudo-terminal. Separate OS
identities or a suitable sandbox are needed when an untrusted coding agent must
be prevented from accessing your other local permissions. Interactive
confirmation alone cannot enforce that separation.

Session text and tool output are remote data. Treat instructions embedded in
them as untrusted input to the coding client. A read-only bridge can still
disclose conversation data to that client; choose the remote profile and client
accordingly. Control mode does not reduce the permissions of Hermes's own tools.

Completion and connection state are also separate: an API timeout, missing
observer finish event, or disconnected SSH tunnel does not prove remote work
stopped. Retry uncertain task submissions only with their original idempotency
key and identical inputs; do not replay messages or approvals automatically.

## Private HTTP transport

Managed SSH requests use local Unix-domain sockets inside an owner-only directory
(mode 700), with authenticated OpenSSH forwarding to the server's loopback API.
The bridge checks the control connection, socket ownership/type, and saved SSH
settings before using credentials. Ordinary reads, writes, and health checks all
use this path. They never probe or reuse an unrelated local TCP listener.

Direct endpoints require HTTPS with certificate verification. Unmanaged plaintext
loopback HTTP is refused, including manual TCP tunnels. The `tunnel` command is a
compatibility alias for `connect`. This changes 0.1.x transport behavior: restart
all old bridge clients and the app after upgrading. Processes already running old
code do not acquire the new protections until restarted.

A Unix socket under a private directory prevents another local OS user from
claiming a predictable TCP port and receiving bridge credentials. It does not
isolate a malicious process running as the same OS user; that process can already
read or change this user's configuration and execute other clients.

The remote host is a separate trust boundary: Hermes's existing APIs still
listen on server loopback TCP. These changes do not authenticate those services
against a malicious process already on that server. Use a dedicated trusted
server/profile, or an upstream TLS endpoint with certificate verification when
server-local users or services are outside your trust boundary. Conversation
redaction, per-session authorization, and model spending limits are not enforced
by this bridge; use the corresponding upstream controls and isolated profiles.
