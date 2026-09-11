#!/bin/sh
# Opened by the menu bar companion; credentials never appear in this command.
set -eu
bridge_cli=$(command -v hermes-bridge || true)
if [ -z "$bridge_cli" ]; then
    for candidate in "$HOME/.local/bin/hermes-bridge" /opt/homebrew/bin/hermes-bridge /usr/local/bin/hermes-bridge; do
        if [ -x "$candidate" ]; then
            bridge_cli=$candidate
            break
        fi
    done
fi
if [ -z "$bridge_cli" ]; then
    echo 'Install the Hermes Bridge command-line companion first: run ./install.sh from its repository.' >&2
    printf 'Press Enter to close. '
    read -r bridge_reply
    exit 1
fi
"$bridge_cli" setup || {
    printf '\nSetup did not finish. Press Enter to close. '
    read -r bridge_reply
    exit 1
}
