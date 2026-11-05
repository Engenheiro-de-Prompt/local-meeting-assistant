#!/bin/sh
# Explicit user launch only; does not build, start backend or open a browser.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BINARY="$HERE/build/Meeting Resource Monitor.app/Contents/MacOS/MeetingResourceMonitor"
if [ ! -x "$BINARY" ]; then
    printf 'Build first: sh "%s/build.sh"\n' "$HERE" >&2
    exit 1
fi
exec "$BINARY" "$@"
