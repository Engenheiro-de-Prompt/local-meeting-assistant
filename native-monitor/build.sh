#!/bin/sh
# Build only: no install, launch, backend call, signing identity or autostart.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP="$HERE/build/Meeting Resource Monitor.app"
mkdir -p "$APP/Contents/MacOS" "$HERE/build/module-cache"
/usr/bin/nice -n 10 /usr/bin/swiftc -swift-version 5 -Osize \
    -target "$(uname -m)-apple-macosx12.0" \
    -module-cache-path "$HERE/build/module-cache" \
    -framework AppKit -framework Foundation "$HERE/Monitor.swift" \
    -o "$APP/Contents/MacOS/MeetingResourceMonitor"
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"
printf 'Built: %s\nNot launched or installed.\n' "$APP"
