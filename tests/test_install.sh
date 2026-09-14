#!/bin/sh
# Installer checks use a temporary tool home, never the user's installation.
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
test_directory=$(mktemp -d)
trap 'rm -rf "$test_directory"' EXIT HUP INT TERM
mkdir -p "$test_directory/bin" "$test_directory/tools"
export HERMES_INSTALL_TEST_DIR="$test_directory"
cat > "$test_directory/bin/uv" <<'EOF'
#!/bin/sh
set -eu
case "$*" in
    'export '*)
        for arg in "$@"; do output=$arg; done
        printf 'httpx==0.28.1 --hash=sha256:test\n' > "$output" ;;
    'build '*)
        for arg in "$@"; do output=$arg; done
        printf 'wheel\n' > "$output/hermes_bridge_tool-0.1.0-py3-none-any.whl" ;;
    'venv --python 3.12 '*)
        for arg in "$@"; do output=$arg; done
        mkdir -p "$output/bin"
        cp "$HERMES_INSTALL_TEST_DIR/tools/hermes-bridge-tool" "$output/bin/"
        printf '%s\n' "$*" >> "$HERMES_INSTALL_TEST_DIR/calls" ;;
    'pip install '*)
        case "$*" in *'--require-hashes --only-binary :all:'*) ;; *) exit 3 ;; esac
        printf '%s\n' "$*" >> "$HERMES_INSTALL_TEST_DIR/calls" ;;
    'pip check '*) ;;
    *) exit 2 ;;
esac
EOF
cat > "$test_directory/tools/hermes-bridge-tool" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$HERMES_INSTALL_TEST_DIR/bridge-calls"
EOF
chmod +x "$test_directory/bin/uv" "$test_directory/tools/hermes-bridge-tool"
PATH="$test_directory/bin:$PATH"
export PATH
export XDG_DATA_HOME="$test_directory/data" UV_TOOL_BIN_DIR="$test_directory/installed-bin"

"$repo_dir/install.sh" --dry-run --setup --no-app > "$test_directory/preview"
[ ! -e "$test_directory/calls" ]
grep -q 'Would run: hermes-bridge-tool setup' "$test_directory/preview"

"$repo_dir/install.sh" --no-app --no-setup > "$test_directory/output"
grep -Fq -- "--require-hashes --only-binary :all:" "$test_directory/calls"
[ -x "$test_directory/installed-bin/hermes-bridge-tool" ]
[ ! -e "$test_directory/bridge-calls" ]

"$repo_dir/install.sh" --no-app --yes > "$test_directory/output"
[ ! -e "$test_directory/bridge-calls" ]

"$repo_dir/install.sh" --no-app --setup > "$test_directory/output"
[ "$(cat "$test_directory/bridge-calls")" = setup ]

"$repo_dir/install.sh" --no-app --observe-sessions > "$test_directory/output"
[ "$(tail -n 1 "$test_directory/bridge-calls")" = 'setup --observe-sessions' ]
"$repo_dir/install.sh" --no-app --observe-sessions --dry-run > "$test_directory/preview"
grep -q 'Would run: hermes-bridge-tool setup --observe-sessions' "$test_directory/preview"
if "$repo_dir/install.sh" --observe-sessions --no-setup > "$test_directory/output" 2>&1; then
    printf 'Contradictory observer/setup options unexpectedly accepted\n' >&2
    exit 1
fi

if "$repo_dir/install.sh" --invalid > "$test_directory/output" 2>&1; then
    printf 'Unknown option unexpectedly accepted\n' >&2
    exit 1
fi

# A noninteractive invocation must not bootstrap uv without --yes.
mkdir -p "$test_directory/empty-home" "$test_directory/minimal-bin"
for executable in dirname uname; do
    ln -s "$(command -v "$executable")" "$test_directory/minimal-bin/$executable"
done
if HOME="$test_directory/empty-home" PATH="$test_directory/minimal-bin" \
    /bin/sh "$repo_dir/install.sh" --no-app --no-setup </dev/null > "$test_directory/output" 2>&1; then
    printf 'Missing uv unexpectedly accepted without consent\n' >&2
    exit 1
fi
grep -q 'Install uv' "$test_directory/output"

# Even authorized bootstrap refuses a replaced script before executing it.
for executable in mktemp rm shasum sha256sum; do
    if command -v "$executable" >/dev/null 2>&1; then
        ln -s "$(command -v "$executable")" "$test_directory/minimal-bin/$executable"
    fi
done
cat > "$test_directory/minimal-bin/curl" <<'EOF'
#!/bin/sh
for argument in "$@"; do output=$argument; done
printf '#!/bin/sh\nprintf compromised > "%s/bootstrap-executed"\n' "$HERMES_INSTALL_TEST_DIR" > "$output"
EOF
chmod +x "$test_directory/minimal-bin/curl"
if HOME="$test_directory/empty-home" PATH="$test_directory/minimal-bin" \
    /bin/sh "$repo_dir/install.sh" --no-app --no-setup --yes > "$test_directory/output" 2>&1; then
    printf 'Replaced bootstrap unexpectedly accepted\n' >&2
    exit 1
fi
grep -q 'bootstrap checksum did not match' "$test_directory/output"
[ ! -e "$test_directory/bootstrap-executed" ]

# Never replace an arbitrary app symlink, even if installation was requested.
mkdir -p "$test_directory/app-home/Applications"
ln -s "$test_directory/unrelated-app" "$test_directory/app-home/Applications/Hermes Bridge Tool.app"
cat > "$test_directory/bin/uname" <<'EOF'
#!/bin/sh
printf 'Darwin\n'
EOF
cat > "$test_directory/bin/xcrun" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod +x "$test_directory/bin/uname" "$test_directory/bin/xcrun"
if HOME="$test_directory/app-home" "$repo_dir/install.sh" --no-setup > "$test_directory/output" 2>&1; then
    printf 'App symlink unexpectedly replaced\n' >&2
    exit 1
fi
grep -q 'Refusing to replace symlink' "$test_directory/output"
[ -L "$test_directory/app-home/Applications/Hermes Bridge Tool.app" ]
printf 'Installer checks passed (dry-run, isolated install, setup flags, bootstrap consent, app protection, unknown options).\n'
