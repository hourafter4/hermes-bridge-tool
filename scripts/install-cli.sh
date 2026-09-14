#!/bin/sh
# Build an isolated, hash-verified environment before replacing the CLI launcher.
set -eu
umask 077
uv_command=$1
wheel=$2
requirements=$3
install_root="${XDG_DATA_HOME:-$HOME/.local/share}/hermes-bridge-tool"
tool_bin="${UV_TOOL_BIN_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}"
fail() { printf 'Hermes Bridge Tool installer: %s\n' "$*" >&2; exit 1; }
[ ! -L "$install_root" ] || fail 'The private installation directory must not be a symlink.'
mkdir -p "$install_root" "$tool_bin"
chmod 700 "$install_root"
install_env=$(mktemp -d "$install_root/runtime.XXXXXXXX")
launcher_stage=
completed=0
cleanup() {
    [ -z "$launcher_stage" ] || rm -f "$launcher_stage"
    if [ "$completed" = 0 ]; then rm -rf "$install_env"; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' HUP TERM
wheel_name=${wheel##*/}
case "$wheel_name" in hermes_bridge_tool-*.whl) ;; *) fail 'Unexpected wheel name.' ;; esac
case "$wheel_name" in *[!A-Za-z0-9_.-]*) fail 'Invalid wheel name.' ;; esac
cp "$wheel" "$install_env/$wheel_name"
cp "$requirements" "$install_env/runtime-requirements.txt"
if command -v shasum >/dev/null 2>&1; then
    wheel_digest=$(shasum -a 256 "$install_env/$wheel_name")
else
    wheel_digest=$(sha256sum "$install_env/$wheel_name")
fi
wheel_digest=${wheel_digest%% *}
printf './%s --hash=sha256:%s\n' "$wheel_name" "$wheel_digest" > "$install_env/project-requirements.txt"
"$uv_command" venv --python 3.12 "$install_env/venv" >&2
# uv tool install ignores requirement hashes; uv pip's explicit hash mode checks
# every transitive dependency. Wheels only: no unpinned dependency build tools.
(cd "$install_env" && "$uv_command" pip install --python "$install_env/venv/bin/python" \
    --require-hashes --only-binary :all: -r runtime-requirements.txt -r project-requirements.txt) >&2
[ -x "$install_env/venv/bin/hermes-bridge-tool" ] || fail 'The verified environment has no CLI executable.'
"$uv_command" pip check --python "$install_env/venv/bin/python" >&2
target="$tool_bin/hermes-bridge-tool"
[ ! -d "$target" ] || fail 'A directory occupies the CLI launcher path.'
launcher_stage=$(mktemp "$tool_bin/.hermes-bridge-tool.XXXXXXXX")
rm -f "$launcher_stage"
ln -s "$install_env/venv/bin/hermes-bridge-tool" "$launcher_stage"
mv -f "$launcher_stage" "$target"
launcher_stage=
completed=1
# Old environments remain available to already-running MCP clients. Reinstalling
# never deletes an environment that may still be in use.
printf '%s\n' "$target"
