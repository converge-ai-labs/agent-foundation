#!/bin/sh
# Install the opt-in macOS Console URL launcher around a caller-selected native binary.
set -eu

launcher_replace=false
if [ "${1:-}" = --replace ]; then
    launcher_replace=true
    shift
fi

if [ "$(uname -s)" != Darwin ]; then
    echo "The Console link launcher currently supports macOS. Use connect-service --pairing-id on other systems." >&2
    exit 1
fi
if [ "$#" -ne 1 ] || [ ! -x "$1" ]; then
    echo "Usage: sh scripts/install-a13n-envd-launcher.sh [--replace] /absolute/path/to/a13n-envd" >&2
    exit 1
fi
case "$1" in /*) ;; *) echo "Pass an absolute executable path" >&2; exit 1 ;; esac
launcher_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
launcher_parent="$HOME/Applications"
launcher_destination="$launcher_parent/A13N Envd.app"
mkdir -p "$launcher_parent"
if [ -e "$launcher_destination" ] && [ "$launcher_replace" != true ]; then
    echo "Already installed at $launcher_destination. Use --replace to back it up and install the new app." >&2
    exit 1
fi
launcher_staging=$(mktemp -d "$launcher_parent/.a13n-envd-launcher.XXXXXX")
trap 'rm -rf "$launcher_staging"' EXIT HUP INT TERM
launcher_app="$launcher_staging/A13N Envd.app"
mkdir -p "$launcher_app/Contents/MacOS" "$launcher_app/Contents/Resources"
/usr/bin/xcrun swiftc -swift-version 5 -parse-as-library -O \
    "$launcher_root/../crates/a13n-envd/macos/Connection.swift" \
    "$launcher_root/../crates/a13n-envd/macos/App.swift" \
    -o "$launcher_app/Contents/MacOS/A13N Envd" -framework AppKit
cp -R "$launcher_root/../crates/a13n-envd/macos/zh-Hans.lproj" "$launcher_app/Contents/Resources/"
cp "$1" "$launcher_app/Contents/Resources/a13n-envd"
chmod 755 "$launcher_app/Contents/Resources/a13n-envd"
cat > "$launcher_app/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>ai.converge.a13n.envd.launcher</string>
<key>CFBundleExecutable</key><string>A13N Envd</string>
<key>CFBundleName</key><string>A13N Envd</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleDevelopmentRegion</key><string>en</string>
<key>CFBundleLocalizations</key><array><string>en</string><string>zh-Hans</string></array>
<key>CFBundleVersion</key><string>1</string>
<key>CFBundleShortVersionString</key><string>0.1</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>LSMultipleInstancesProhibited</key><true/>
<key>NSHighResolutionCapable</key><true/>
<key>CFBundleURLTypes</key><array><dict>
<key>CFBundleURLName</key><string>A13N Console connection</string>
<key>CFBundleURLSchemes</key><array><string>a13n-envd</string></array>
</dict></array>
</dict></plist>
PLIST
/usr/bin/codesign --force --deep --sign - "$launcher_app"
if [ -e "$launcher_destination" ]; then
    launcher_backup=$(mktemp -d "$launcher_parent/.a13n-envd-previous.XXXXXX")
    mv "$launcher_destination" "$launcher_backup/A13N Envd.app"
    echo "Previous app saved in $launcher_backup"
fi
mv "$launcher_app" "$launcher_destination"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$launcher_destination"
echo "Installed $launcher_destination"
echo "In Console choose Environments → Instances → Connect my computer → Open A13N Envd."
echo "Envd runs inside the app. Closing its window keeps the connection; quitting disconnects. No login service was installed."
