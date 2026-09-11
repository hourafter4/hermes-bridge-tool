# Contributing

Contributions to Hermes Bridge Tool happen through **GitHub issues**. Report a bug,
request a feature, or ask a question; the maintainer reviews the issue and implements
accepted changes.

**Pull requests are disabled; external code contributions are not accepted.**
Please open an issue before spending time on an implementation. The maintainer
handles implementation through the issue tracker.

## Open an issue

- **Bug reports:** describe what happened, what you expected, and how to reproduce
  it. Include the installed version, operating system, coding client, backend, and
  connection mode.
- **Feature requests:** explain the task you want to accomplish and what the
  current behavior prevents. An example is helpful; a proposed implementation is
  optional.
- **Questions:** open a blank issue and describe where you are stuck.

[Search existing issues](https://github.com/hourafter4/hermes-bridge-tool/issues)
first, then [open an issue](https://github.com/hourafter4/hermes-bridge-tool/issues/new/choose).
Use `hermes-bridge-tool --version` to find the installed CLI version. If you are
unsure which backend is involved, say so; [the backend guide](docs/BACKENDS.md)
explains the choices.

Only share the relevant, sanitized part of an error or log. Remove API keys,
passwords, login cookies, authorization headers, private hostnames, and conversation
content you do not intend to publish. Do not upload `.env` files or private
authentication/configuration files.

The maintainer's [development notes](docs/DEVELOPMENT.md) document the code layout
and local checks. They do not change the issue-only contribution policy.
