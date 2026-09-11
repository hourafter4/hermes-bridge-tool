#!/bin/sh
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
app_dir="$repo_dir/dist/Hermes Bridge.app"
mkdir -p "$app_dir/Contents/MacOS"
cp "$repo_dir/macos/Info.plist" "$app_dir/Contents/Info.plist"
xcrun swiftc -swift-version 5 -O -target "$(uname -m)-apple-macosx13.0" \
  -framework AppKit "$repo_dir/macos/main.swift" -o "$app_dir/Contents/MacOS/HermesBridge"
codesign --force --sign - "$app_dir"
"$app_dir/Contents/MacOS/HermesBridge" --self-test
printf 'Built %s\n' "$app_dir"
