#!/bin/sh
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
app_dir="$repo_dir/dist/Hermes Bridge.app"
resources_dir="$app_dir/Contents/Resources"
mkdir -p "$app_dir/Contents/MacOS" "$resources_dir"
cp "$repo_dir/macos/Info.plist" "$app_dir/Contents/Info.plist"
cp "$repo_dir/assets/brand/menu-bar-template.png" "$resources_dir/"
cp "$repo_dir/assets/brand/menu-bar-template@2x.png" "$resources_dir/"
cp "$repo_dir/assets/brand/app-icon.png" "$resources_dir/"
cp "$repo_dir/scripts/setup-terminal.command" "$resources_dir/"
chmod +x "$resources_dir/setup-terminal.command"
icon_work=$(mktemp -d "${TMPDIR:-/tmp}/hermes-bridge-icon.XXXXXX")
trap 'rm -rf "$icon_work"' EXIT HUP INT TERM
iconset="$icon_work/HermesBridge.iconset"
mkdir -p "$iconset"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$repo_dir/assets/brand/app-icon.png" \
    --out "$iconset/icon_${size}x${size}.png" >/dev/null
  retina_size=$((size * 2))
  sips -z "$retina_size" "$retina_size" "$repo_dir/assets/brand/app-icon.png" \
    --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$resources_dir/HermesBridge.icns"
xcrun swiftc -swift-version 5 -O -target "$(uname -m)-apple-macosx13.0" \
  -framework AppKit "$repo_dir/macos/main.swift" -o "$app_dir/Contents/MacOS/HermesBridge"
codesign --force --sign - "$app_dir"
"$app_dir/Contents/MacOS/HermesBridge" --self-test
printf 'Built %s\n' "$app_dir"
