#!/bin/sh
# Install the downloaded release beside this file; no checkout or compiler needed.
set -eu

release_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install_app=1
# The CLI archive names this entry point install.sh and contains no app, including
# when that archive is used on macOS. The app archive keeps Install.command.
case "${0##*/}" in install.sh) install_app=0 ;; esac
assume_yes=0
dry_run=0
app_stage=
uv_installer=
app_target=

fail() { printf 'Hermes Bridge Tool installer: %s\n' "$*" >&2; exit 1; }
cleanup() {
    if [ -n "$uv_installer" ]; then rm -f "$uv_installer"; fi
    if [ -n "$app_stage" ] && [ -d "$app_stage" ]; then
        if [ -d "$app_stage/previous.app" ] && [ ! -e "$app_target" ]; then
            if ! mv "$app_stage/previous.app" "$app_target"; then
                printf 'Previous app preserved at %s/previous.app; restore it manually.\n' "$app_stage" >&2
                return
            fi
        fi
        rm -rf "$app_stage"
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' HUP TERM

usage() {
    cat <<'EOF'
Usage: ./Install.command [--no-app] [--yes] [--dry-run]

Install the bundled CLI and macOS app for your user. No Xcode, checkout, or sudo
is needed. uv installs Python 3.12 automatically when necessary. Existing private
settings are preserved; installation does not pair, restart, or change a server.

  --no-app   Install only the CLI (automatic on Linux and for install.sh).
  --yes      Allow downloading uv if missing; skip interactive offers.
  --dry-run  Validate the release and show the plan without installing anything.
  --help     Show this help.

If uv is missing, its official https://astral.sh/uv/install.sh installer is offered.
Noninteractive installation requires --yes to authorize that download.
EOF
}
confirm() {
    [ -t 0 ] || return 1
    printf '%s [y/N] ' "$1"
    IFS= read -r answer || return 1
    case "$answer" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}
for argument in "$@"; do
    case "$argument" in
        --no-app) install_app=0 ;;
        --yes) assume_yes=1 ;;
        --dry-run) dry_run=1 ;;
        --help|-h) usage; exit 0 ;;
        *) fail "Unknown option: $argument" ;;
    esac
done

platform=$(uname -s)
case "$platform" in Darwin|Linux) ;; *) fail 'This release installer supports macOS and Linux.' ;; esac
set -- "$release_dir"/*.whl
[ "$#" = 1 ] && [ -f "$1" ] || fail 'Keep exactly one bundled .whl file beside Install.command.'
wheel=$1
wheel_name=${wheel##*/}
case "$wheel_name" in hermes_bridge_tool-*.whl) ;; *) fail 'The bundled wheel is not Hermes Bridge Tool.' ;; esac
case "$wheel_name" in *[!A-Za-z0-9_.-]*) fail 'Invalid wheel filename.' ;; esac
manifest="$release_dir/SHA256SUMS"
[ -f "$manifest" ] && [ ! -L "$manifest" ] || fail 'The release SHA256SUMS manifest is missing or is a symlink.'

# Validate paths before asking a checksum utility to read them. A manifest must
# refer only to the package, never follow a symlink or read an arbitrary local file.
wheel_entries=0
while IFS= read -r line || [ -n "$line" ]; do
    digest=${line%% *}
    relative=${line#"$digest  "}
    [ "${#digest}" = 64 ] && [ "$relative" != "$line" ] || fail 'Invalid checksum manifest format.'
    case "$digest" in *[!a-fA-F0-9]*) fail 'Invalid checksum digest.' ;; esac
    case "$relative" in
        "$wheel_name") wheel_entries=$((wheel_entries + 1)) ;;
        Install.command|install.sh|README-install.txt|'Hermes Bridge Tool.app/'*) ;;
        *) fail 'Checksum manifest contains a path outside the release.' ;;
    esac
    case "/$relative/" in */../*|*/./*|*\\*) fail 'Unsafe checksum manifest path.' ;; esac
    check_path="$release_dir/$relative"
    [ -f "$check_path" ] || fail 'A file listed in the release manifest is missing.'
    while [ "$check_path" != "$release_dir" ]; do
        [ ! -L "$check_path" ] || fail 'Release files must not be symbolic links.'
        check_path=${check_path%/*}
    done
done < "$manifest"
[ "$wheel_entries" = 1 ] || fail 'The checksum manifest must list the bundled wheel exactly once.'
if command -v shasum >/dev/null 2>&1; then
    (cd "$release_dir" && shasum -a 256 -c SHA256SUMS >/dev/null 2>&1) || fail 'Release checksum verification failed. Download and extract the release again.'
elif command -v sha256sum >/dev/null 2>&1; then
    (cd "$release_dir" && sha256sum -c SHA256SUMS >/dev/null 2>&1) || fail 'Release checksum verification failed. Download and extract the release again.'
else
    fail 'Install shasum or sha256sum to verify the release.'
fi

if [ "$platform" = Darwin ] && [ "$install_app" = 1 ]; then
    app_source="$release_dir/Hermes Bridge Tool.app"
    app_target="$HOME/Applications/Hermes Bridge Tool.app"
    [ -d "$app_source" ] && [ ! -L "$app_source" ] || fail 'The bundled macOS app is missing or is a symlink; use --no-app for CLI only.'
    expected_id=local.hermes-bridge-tool.menubar
    source_id=$(plutil -extract CFBundleIdentifier raw -o - "$app_source/Contents/Info.plist" 2>/dev/null || true)
    [ "$source_id" = "$expected_id" ] || fail 'The bundled app has an unexpected identity.'
    [ ! -L "$app_target" ] || fail "Refusing to replace symlink: $app_target"
    if [ -e "$app_target" ]; then
        existing_id=$(plutil -extract CFBundleIdentifier raw -o - "$app_target/Contents/Info.plist" 2>/dev/null || true)
        [ "$existing_id" = "$expected_id" ] || fail "Another app occupies $app_target; move it before installing."
    fi
fi

if [ "$dry_run" = 1 ]; then
    printf 'Release checksums verified.\nWould install CLI: uv tool install --python 3.12 --reinstall %s\n' "$wheel"
    if [ -n "$app_target" ]; then printf 'Would install bundled app: %s\n' "$app_target"; fi
    printf 'Existing settings are preserved. No server setup runs.\n'
    exit 0
fi

if command -v uv >/dev/null 2>&1; then
    uv_command=$(command -v uv)
elif [ -x "$HOME/.local/bin/uv" ]; then
    uv_command="$HOME/.local/bin/uv"
else
    if [ "$assume_yes" != 1 ] && ! confirm 'Install uv using its official astral.sh installer?'; then
        fail 'Install uv and rerun, or pass --yes to allow its download.'
    fi
    command -v curl >/dev/null 2>&1 || fail 'Install curl first, or install uv manually.'
    uv_installer=$(mktemp)
    curl --proto '=https' --tlsv1.2 -fsSL https://astral.sh/uv/install.sh -o "$uv_installer"
    UV_UNMANAGED_INSTALL="$HOME/.local/bin" sh "$uv_installer"
    rm -f "$uv_installer"
    uv_installer=
    uv_command="$HOME/.local/bin/uv"
fi

"$uv_command" tool install --python 3.12 --reinstall "$wheel"
tool_bin=$("$uv_command" tool dir --bin)
bridge_command="$tool_bin/hermes-bridge-tool"
[ -x "$bridge_command" ] || fail "uv did not create $bridge_command. Check the installation output."
printf '\nInstalled CLI: %s\n' "$bridge_command"
case ":$PATH:" in *":$tool_bin:"*) ;; *) printf 'Add %s to PATH, or run: uv tool update-shell\n' "$tool_bin" ;; esac

if [ -n "$app_target" ]; then
    mkdir -p "$HOME/Applications"
    app_stage=$(mktemp -d "$HOME/Applications/.hermes-bridge-tool.XXXXXX")
    ditto "$app_source" "$app_stage/Hermes Bridge Tool.app"
    if [ -e "$app_target" ]; then mv "$app_target" "$app_stage/previous.app"; fi
    if ! mv "$app_stage/Hermes Bridge Tool.app" "$app_target"; then
        fail 'Could not install the app; restoring the previous app.'
    fi
    rm -rf "$app_stage"
    app_stage=
    printf '\nInstalled app: %s\nQuit any older running copy, then open it from Applications.\n' "$app_target"
fi

printf '\nChoose the connection for your task; these commands run only when you choose:\n'
printf 'WebUI chats: "%s" configure-webui --url https://your-webui-host\n' "$bridge_command"
printf 'Native messaging MCP: "%s" configure-native --help\n' "$bridge_command"
printf 'Gateway SSH pairing: "%s" setup\n' "$bridge_command"
printf 'Connect coding clients: "%s" register --help\n' "$bridge_command"
printf '\nExisting settings are preserved. Restart Codex or Claude Code to load updated tools.\n'
if [ -n "$app_target" ] && [ "$assume_yes" != 1 ] && confirm 'Open Hermes Bridge Tool now?'; then
    open "$app_target"
fi
