#!/bin/sh
# Exercise real uv hash enforcement in isolation, including upgrade rollback.
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
test_directory=$(mktemp -d "${TMPDIR:-/tmp}/hermes hash test.XXXXXXXX")
trap 'rm -rf "$test_directory"' EXIT HUP INT TERM
export XDG_DATA_HOME="$test_directory/data" UV_TOOL_BIN_DIR="$test_directory/bin"
uv_command=$(command -v uv)
uv export --project "$repo_dir" --locked --no-dev --no-emit-project --no-header --no-annotate \
    --format requirements-txt --output-file "$test_directory/requirements.txt" >/dev/null
uv build "$repo_dir" --wheel --out-dir "$test_directory" > "$test_directory/build.log" 2>&1
set -- "$test_directory"/hermes_bridge_tool-*.whl
wheel=$1
mkdir -p "$UV_TOOL_BIN_DIR"
printf '#!/bin/sh\nprintf "previous CLI\\n"\n' > "$UV_TOOL_BIN_DIR/hermes-bridge-tool"
chmod +x "$UV_TOOL_BIN_DIR/hermes-bridge-tool"
sed 's/sha256:[0-9a-f]*/sha256:0000000000000000000000000000000000000000000000000000000000000000/g' \
    "$test_directory/requirements.txt" > "$test_directory/bad-requirements.txt"
if sh "$repo_dir/scripts/install-cli.sh" "$uv_command" "$wheel" "$test_directory/bad-requirements.txt" \
    > "$test_directory/output" 2>&1; then
    printf 'Installer accepted deliberately incorrect dependency hashes.\n' >&2
    exit 1
fi
grep -qi 'hash mismatch' "$test_directory/output" || { cat "$test_directory/output" >&2; exit 1; }
[ "$("$UV_TOOL_BIN_DIR/hermes-bridge-tool")" = 'previous CLI' ]
sh "$repo_dir/scripts/install-cli.sh" "$uv_command" "$wheel" "$test_directory/requirements.txt" \
    > "$test_directory/output" 2>&1 || { cat "$test_directory/output" >&2; exit 1; }
[ -L "$UV_TOOL_BIN_DIR/hermes-bridge-tool" ]
"$UV_TOOL_BIN_DIR/hermes-bridge-tool" --help >/dev/null
printf 'Real dependency checks passed: rejected wrong hashes, retained previous CLI, installed valid locked wheels.\n'
