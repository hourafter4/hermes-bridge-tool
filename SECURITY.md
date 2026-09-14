# Security

Hermes Bridge Tool connects coding clients to a remote agent that can execute
tools and interact with connected accounts. Read the
[security model and operating guide](docs/SECURITY.md) before granting control.

Version 0.2.0 defaults to monitoring. Task execution and platform message delivery
require separate local operator decisions. Remote approval responses remain
disabled.

To report a vulnerability, use [private vulnerability reporting](https://github.com/hourafter4/hermes-bridge-tool/security/advisories/new).
Describe the affected version, impact,
and a minimal reproduction using synthetic credentials. Do not include real
API keys, cookies, SSH private keys, or private conversation history in an issue
or attachment. If private reporting is unavailable, open an issue requesting a
private reporting channel without publishing exploit details or credentials.

Security fixes target the latest release. A bridge lock stops new requests
through the bridge; it does not revoke credentials or undo work already
accepted by Hermes.
