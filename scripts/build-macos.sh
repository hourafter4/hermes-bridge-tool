#!/bin/sh
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
app_dir="$repo_dir/dist/Hermes Bridge Tool.app"
resources_dir="$app_dir/Contents/Resources"
mkdir -p "$app_dir/Contents/MacOS" "$resources_dir"
cp "$repo_dir/macos/Info.plist" "$app_dir/Contents/Info.plist"
cp "$repo_dir/assets/brand/menu-bar-template.png" "$resources_dir/"
cp "$repo_dir/assets/brand/menu-bar-template@2x.png" "$resources_dir/"
cp "$repo_dir/assets/brand/app-icon.png" "$resources_dir/"
cp "$repo_dir/scripts/setup-terminal.command" "$resources_dir/"
chmod +x "$resources_dir/setup-terminal.command"
icon_work=$(mktemp -d "${TMPDIR:-/tmp}/hermes-bridge-tool-icon.XXXXXX")
trap 'rm -rf "$icon_work"' EXIT HUP INT TERM
iconset="$icon_work/HermesBridgeTool.iconset"
mkdir -p "$iconset"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$repo_dir/assets/brand/macos-icon.png" \
    --out "$iconset/icon_${size}x${size}.png" >/dev/null
  retina_size=$((size * 2))
  sips -z "$retina_size" "$retina_size" "$repo_dir/assets/brand/macos-icon.png" \
    --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$resources_dir/HermesBridgeTool.icns"
for architecture in arm64 x86_64; do
  xcrun swiftc -swift-version 5 -O -target "$architecture-apple-macosx13.0" \
    -framework AppKit "$repo_dir/macos/main.swift" -o "$icon_work/HermesBridgeTool-$architecture"
done
xcrun lipo -create "$icon_work/HermesBridgeTool-arm64" "$icon_work/HermesBridgeTool-x86_64" \
  -output "$app_dir/Contents/MacOS/HermesBridgeTool"
xcrun lipo "$app_dir/Contents/MacOS/HermesBridgeTool" -verify_arch arm64 x86_64
if [ -n "${HERMES_NOTARY_PROFILE:-}" ] && [ -z "${HERMES_SIGNING_IDENTITY:-}" ]; then
  printf '%s\n' 'Notarization requires HERMES_SIGNING_IDENTITY (Developer ID Application).' >&2
  exit 1
fi
if [ -n "${HERMES_SIGNING_IDENTITY:-}" ]; then
  codesign --force --options runtime --timestamp --sign "$HERMES_SIGNING_IDENTITY" "$app_dir"
else
  codesign --force --sign - "$app_dir"
fi
codesign --verify --strict "$app_dir"
if [ -n "${HERMES_NOTARY_PROFILE:-}" ]; then
  ditto -c -k --keepParent "$app_dir" "$icon_work/notarize.zip"
  xcrun notarytool submit "$icon_work/notarize.zip" --keychain-profile "$HERMES_NOTARY_PROFILE" --wait --timeout 15m
  xcrun stapler staple "$app_dir"
  xcrun stapler validate "$app_dir"
  spctl --assess --type execute "$app_dir"
fi
"$app_dir/Contents/MacOS/HermesBridgeTool" --self-test
printf 'Built %s\n' "$app_dir"
