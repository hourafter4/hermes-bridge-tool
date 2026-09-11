#!/bin/sh
# All installs/downloads/app operations are mocked in an isolated temporary home.
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
test_directory=$(mktemp -d)
trap 'rm -rf "$test_directory"' EXIT HUP INT TERM
export HERMES_RELEASE_TEST_DIR="$test_directory"
mkdir -p "$test_directory/bin" "$test_directory/tools" "$test_directory/home" "$test_directory/Release With Spaces"
release="$test_directory/Release With Spaces"
cp "$repo_dir/scripts/install-release.command" "$release/Install.command"
printf 'test wheel\n' > "$release/hermes_bridge_tool-0.1.0-py3-none-any.whl"
printf 'install instructions\n' > "$release/README-install.txt"
mkdir -p "$release/Hermes Bridge Tool.app/Contents/MacOS"
printf 'local.hermes-bridge-tool.menubar\n' > "$release/Hermes Bridge Tool.app/Contents/Info.plist"
printf 'bundled app\n' > "$release/Hermes Bridge Tool.app/Contents/MacOS/HermesBridgeTool"
seal() {
    (cd "$release" && find . -type f ! -name SHA256SUMS | sort | while IFS= read -r file; do
        file=${file#./}
        if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$file"; else sha256sum "$file"; fi
    done) > "$release/SHA256SUMS"
}
seal
cat > "$test_directory/bin/uv" <<'EOF'
#!/bin/sh
case "$*" in
    'tool dir --bin') printf '%s/tools\n' "$HERMES_RELEASE_TEST_DIR" ;;
    'tool install --python 3.12 --reinstall '*) printf '%s\n' "$*" >> "$HERMES_RELEASE_TEST_DIR/uv-calls" ;;
    *) exit 2 ;;
esac
EOF
cat > "$test_directory/tools/hermes-bridge-tool" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$HERMES_RELEASE_TEST_DIR/bridge-calls"
EOF
cat > "$test_directory/bin/uname" <<'EOF'
#!/bin/sh
printf '%s\n' "${HERMES_RELEASE_TEST_PLATFORM:-Linux}"
EOF
cat > "$test_directory/bin/plutil" <<'EOF'
#!/bin/sh
for arg in "$@"; do final_arg=$arg; done
cat "$final_arg"
EOF
cat > "$test_directory/bin/ditto" <<'EOF'
#!/bin/sh
[ "${HERMES_RELEASE_TEST_FAIL_COPY:-0}" != 1 ] || exit 1
/bin/cp -R "$1" "$2"
EOF
cat > "$test_directory/bin/mv" <<'EOF'
#!/bin/sh
if [ "${HERMES_RELEASE_TEST_FAIL_MOVE:-0}" = 1 ]; then
    case "$1" in */.hermes-bridge-tool.*/'Hermes Bridge Tool.app') exit 1 ;; esac
fi
/bin/mv "$@"
EOF
cat > "$test_directory/bin/curl" <<'EOF'
#!/bin/sh
printf 'Unexpected download\n' >&2
touch "$HERMES_RELEASE_TEST_DIR/download-called"
exit 1
EOF
chmod +x "$test_directory/bin/"* "$test_directory/tools/hermes-bridge-tool"
PATH="$test_directory/bin:$PATH"
export PATH
run_install() { HOME="$test_directory/home" /bin/sh "$release/Install.command" "$@"; }

run_install --dry-run > "$test_directory/output"
[ ! -e "$test_directory/uv-calls" ]
grep -q 'python 3.12' "$test_directory/output"
run_install --yes > "$test_directory/output"
grep -Fq "tool install --python 3.12 --reinstall $release/hermes_bridge_tool-0.1.0-py3-none-any.whl" "$test_directory/uv-calls"
[ ! -e "$test_directory/bridge-calls" ]
[ ! -e "$test_directory/home/Applications" ]

# Manifest corruption and ambiguous wheels fail before any installation.
cp "$test_directory/uv-calls" "$test_directory/previous-calls"
printf 'tampered\n' >> "$release/hermes_bridge_tool-0.1.0-py3-none-any.whl"
if run_install --yes > "$test_directory/output" 2>&1; then exit 1; fi
grep -q 'checksum verification failed' "$test_directory/output"
cmp "$test_directory/uv-calls" "$test_directory/previous-calls"
seal
cp "$release/hermes_bridge_tool-0.1.0-py3-none-any.whl" "$release/second.whl"
if run_install --yes > "$test_directory/output" 2>&1; then exit 1; fi
grep -q 'exactly one' "$test_directory/output"
rm "$release/second.whl"
cp "$release/SHA256SUMS" "$test_directory/valid-manifest"
printf '%064d  ../outside\n' 0 >> "$release/SHA256SUMS"
if run_install --yes > "$test_directory/output" 2>&1; then exit 1; fi
grep -q 'outside the release' "$test_directory/output"
cp "$test_directory/valid-manifest" "$release/SHA256SUMS"

# macOS installs the already built bundle, preserves config, and never calls Xcode.
export HERMES_RELEASE_TEST_PLATFORM=Darwin
mkdir -p "$test_directory/home/.config/hermes-bridge-tool"
printf 'keep private config\n' > "$test_directory/home/.config/hermes-bridge-tool/config.json"
run_install --yes > "$test_directory/output"
target="$test_directory/home/Applications/Hermes Bridge Tool.app"
cmp "$release/Hermes Bridge Tool.app/Contents/MacOS/HermesBridgeTool" "$target/Contents/MacOS/HermesBridgeTool"
grep -q 'keep private config' "$test_directory/home/.config/hermes-bridge-tool/config.json"
printf 'previous app\n' > "$target/Contents/MacOS/HermesBridgeTool"
if HERMES_RELEASE_TEST_FAIL_COPY=1 run_install --yes > "$test_directory/output" 2>&1; then exit 1; fi
grep -q 'previous app' "$target/Contents/MacOS/HermesBridgeTool"
if HERMES_RELEASE_TEST_FAIL_MOVE=1 run_install --yes > "$test_directory/output" 2>&1; then exit 1; fi
grep -q 'previous app' "$target/Contents/MacOS/HermesBridgeTool"
printf 'unrelated.bundle.id\n' > "$target/Contents/Info.plist"
if run_install --yes > "$test_directory/output" 2>&1; then exit 1; fi
grep -q 'Another app occupies' "$test_directory/output"
rm -rf "$target"
ln -s "$test_directory/unrelated-app" "$target"
if run_install --yes > "$test_directory/output" 2>&1; then exit 1; fi
grep -q 'Refusing to replace symlink' "$test_directory/output"
[ -L "$target" ]
run_install --no-app --yes > "$test_directory/output"

# A Linux source-free bundle has no app and may name the same installer install.sh.
export HERMES_RELEASE_TEST_PLATFORM=Linux
rm -rf "$release/Hermes Bridge Tool.app"
cp "$release/Install.command" "$release/install.sh"
seal
HOME="$test_directory/home" /bin/sh "$release/install.sh" --yes > "$test_directory/output"
export HERMES_RELEASE_TEST_PLATFORM=Darwin
HOME="$test_directory/home" /bin/sh "$release/install.sh" --yes > "$test_directory/output"
grep -q 'Installed CLI:' "$test_directory/output"
[ -L "$target" ]  # CLI archive never touches the app, even on macOS.
export HERMES_RELEASE_TEST_PLATFORM=Linux

# Missing uv cannot cause a noninteractive download without explicit --yes.
mkdir -p "$test_directory/minimal-bin" "$test_directory/empty-home"
for executable in dirname uname shasum sha256sum; do
    if command -v "$executable" >/dev/null 2>&1; then
        ln -s "$(command -v "$executable")" "$test_directory/minimal-bin/$executable"
    fi
done
if HOME="$test_directory/empty-home" PATH="$test_directory/minimal-bin" \
    /bin/sh "$release/Install.command" --no-app </dev/null > "$test_directory/output" 2>&1; then exit 1; fi
grep -q 'Install uv and rerun' "$test_directory/output"
[ ! -e "$test_directory/download-called" ]
[ ! -e "$test_directory/bridge-calls" ]
printf 'Release installer checks passed (checksums, spaces, Python runtime, Linux, app identity, rollback, preserved config, no pairing/downloads).\n'
