#!/bin/sh
# Install a checkout without keeping a runtime dependency on its location.
set -eu

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install_app=1
setup_mode=ask
assume_yes=0
dry_run=0
observe_sessions=0

usage() {
    cat <<'EOF'
Usage: ./install.sh [--no-app] [--setup | --no-setup] [--observe-sessions] [--yes] [--dry-run]

Installs the Hermes Bridge Tool CLI for your user, plus the macOS menu bar app
when Apple's command line developer tools are available. No sudo required.

  --no-app    Install only the CLI (also the default on Linux).
  --setup     Pair and configure the Gateway API over SSH after installation.
  --observe-sessions  Set up the connection with the optional session observer.
  --no-setup  Install locally; keep existing settings or configure an existing API.
  --yes       Allow installing uv if missing; skip interactive offers.
  --dry-run   Show what would happen without downloads or changes.
  --help      Show this help.

If uv is missing, installation asks before downloading its official installer
from https://astral.sh/uv/0.10.8/install.sh. --yes accepts that download in advance.
EOF
}

fail() { printf 'hermes-bridge-tool installer: %s\n' "$*" >&2; exit 1; }
confirm() {
    [ -t 0 ] || return 1
    printf '%s [y/N] ' "$1"
    IFS= read -r answer || return 1
    case "$answer" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

for argument in "$@"; do
    case "$argument" in
        --no-app) install_app=0 ;;
        --setup) setup_mode=yes ;;
        --observe-sessions) observe_sessions=1 ;;
        --no-setup) setup_mode=no ;;
        --yes) assume_yes=1 ;;
        --dry-run) dry_run=1 ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; fail "Unknown option: $argument" ;;
    esac
done
if [ "$observe_sessions" = 1 ]; then
    [ "$setup_mode" != no ] || fail '--observe-sessions cannot be combined with --no-setup.'
    setup_mode=yes
fi

[ -f "$repo_dir/pyproject.toml" ] || fail 'Run install.sh from a full project checkout.'
platform=$(uname -s)
case "$platform" in Darwin|Linux) ;; *) fail 'This installer supports macOS and Linux.' ;; esac

if [ "$dry_run" = 1 ]; then
    printf 'Would build and install the CLI with Python 3.12 and hash-verified locked dependencies: %s\n' "$repo_dir"
    printf 'Would offer the official uv installer if uv is missing.\n'
    if [ "$platform" = Darwin ] && [ "$install_app" = 1 ]; then
        printf 'Would build the menu bar app and install it to %s/Applications/Hermes Bridge Tool.app\n' "$HOME"
    fi
    if [ "$setup_mode" = yes ]; then
        if [ "$observe_sessions" = 1 ]; then
            printf 'Would run: hermes-bridge-tool setup --observe-sessions\n'
        else
            printf 'Would run: hermes-bridge-tool setup\n'
        fi
    fi
    exit 0
fi

if command -v uv >/dev/null 2>&1; then
    uv_command=$(command -v uv)
elif [ -x "$HOME/.local/bin/uv" ]; then
    uv_command="$HOME/.local/bin/uv"
else
    if [ "$assume_yes" != 1 ] && ! confirm 'Install uv using its official astral.sh installer?'; then
        fail 'Install uv (https://docs.astral.sh/uv/getting-started/installation/) and rerun, or pass --yes.'
    fi
    command -v curl >/dev/null 2>&1 || fail 'Install curl first, or install uv manually.'
    uv_installer=$(mktemp)
    trap 'rm -f "$uv_installer"' EXIT HUP INT TERM
    curl --proto '=https' --tlsv1.2 -fsSL https://astral.sh/uv/0.10.8/install.sh -o "$uv_installer"
    # Pin the bootstrap script as well as its versioned HTTPS URL.
    if command -v shasum >/dev/null 2>&1; then
        uv_digest=$(shasum -a 256 "$uv_installer")
    else
        uv_digest=$(sha256sum "$uv_installer")
    fi
    [ "${uv_digest%% *}" = eae5e1dae89cd0b74d357f549ccd6faa94b2ad6c1d89d78972a625655a4556ae ] || fail 'The uv bootstrap checksum did not match; nothing was executed.'
    UV_UNMANAGED_INSTALL="$HOME/.local/bin" sh "$uv_installer"
    rm -f "$uv_installer"
    trap - EXIT HUP INT TERM
    uv_command="$HOME/.local/bin/uv"
fi

install_work=$(mktemp -d)
trap 'rm -rf "$install_work"' EXIT HUP INT TERM
"$uv_command" export --project "$repo_dir" --locked --no-dev --no-emit-project --no-annotate --no-header \
    --format requirements-txt --output-file "$install_work/runtime-requirements.txt" >/dev/null
"$uv_command" build "$repo_dir" --wheel --out-dir "$install_work"
set -- "$install_work"/hermes_bridge_tool-*.whl
[ "$#" = 1 ] && [ -f "$1" ] || fail 'Expected exactly one freshly built CLI wheel.'
bridge_command=$(sh "$repo_dir/scripts/install-cli.sh" "$uv_command" "$1" "$install_work/runtime-requirements.txt")
rm -rf "$install_work"
trap - EXIT HUP INT TERM
tool_bin=${bridge_command%/*}
[ -x "$bridge_command" ] || fail "uv did not create $bridge_command. Check its installation output."
printf '\nInstalled CLI: %s\n' "$bridge_command"
case ":$PATH:" in
    *":$tool_bin:"*) ;;
    *) printf 'Add %s to PATH, and reopen your shell\n' "$tool_bin" ;;
esac

if [ "$platform" = Darwin ] && [ "$install_app" = 1 ]; then
    if ! xcrun --find swiftc >/dev/null 2>&1; then
        printf '\nCLI installed. To add the menu bar app, run xcode-select --install, then rerun ./install.sh.\n'
    else
        app_target="$HOME/Applications/Hermes Bridge Tool.app"
        [ ! -L "$app_target" ] || fail "Refusing to replace symlink: $app_target"
        if [ -e "$app_target" ]; then
            existing_id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app_target/Contents/Info.plist" 2>/dev/null || true)
            expected_id=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$repo_dir/macos/Info.plist")
            [ "$existing_id" = "$expected_id" ] || fail "Another app occupies $app_target; move it before installing."
        fi
        "$repo_dir/scripts/build-macos.sh"
        mkdir -p "$HOME/Applications"
        # Only the matching bundle above may be replaced; keep the old app if copying fails.
        app_stage=$(mktemp -d "$HOME/Applications/.hermes-bridge-tool.XXXXXX")
        trap 'rm -rf "$app_stage"' EXIT HUP INT TERM
        ditto "$repo_dir/dist/Hermes Bridge Tool.app" "$app_stage/Hermes Bridge Tool.app"
        if [ -e "$app_target" ]; then
            mv "$app_target" "$app_stage/previous.app"
        fi
        if ! mv "$app_stage/Hermes Bridge Tool.app" "$app_target"; then
            [ ! -d "$app_stage/previous.app" ] || mv "$app_stage/previous.app" "$app_target"
            fail 'Could not install the app; the previous app was restored.'
        fi
        rm -rf "$app_stage"
        trap - EXIT HUP INT TERM
        printf '\nInstalled app: %s\nOpen it from Applications to connect. Quit and reopen any older running copy.\n' "$app_target"
    fi
fi

if [ "$setup_mode" = yes ]; then
    if [ "$observe_sessions" = 1 ]; then
        exec "$bridge_command" setup --observe-sessions
    fi
    exec "$bridge_command" setup
elif [ "$setup_mode" = ask ] && [ "$assume_yes" != 1 ] && confirm 'Pair and configure the Gateway API over SSH now?'; then
    exec "$bridge_command" setup
fi
printf '\nExisting settings are preserved. After upgrading, update client registrations:\n'
printf '"%s" register both  # or codex / claude\n' "$bridge_command"
printf 'Then restart your coding clients to load the updated installation.\n'
printf 'Existing WebUI: "%s" configure-webui --url https://your-webui-host\n' "$bridge_command"
printf 'Gateway SSH pairing: "%s" setup\n' "$bridge_command"
printf 'See docs/BACKENDS.md for HTTPS, SSH, native MCP, and CLI observation.\n'
