# Native menu-bar resource monitor (macOS)

A Swift/AppKit accessory app, with Foundation `URLSession` networking. No browser,
Electron, Python runtime, model loading, audio capture, local resource sampler,
background installation, login item or duplicated resource controller. The backend
must already be running. Quitting the monitor does not stop a meeting.

## Build / run (explicit only)

From the repository root, using the installed Apple command-line tools:

```sh
sh native-monitor/build.sh
# Only when you want the menu-bar UI:
sh native-monitor/run.sh
```

Build output: `native-monitor/build/Meeting Resource Monitor.app`. Build does not
launch the app, open a dashboard, install software, restart a service or configure
autostart. It invokes one compiler at lowered scheduling priority. The artifact is
local and unsigned; it is not a notarized distribution. Build products and compiler
cache stay in `native-monitor/build/` and are excluded by the local `.gitignore`.

The app has no Dock icon or initial window. Its **LMA** menu shows:

- Session/capture state, capture/progress metadata and elapsed seconds. Session
  `recording` alone is **not** proof of working capture. Backend capture-health
  fields are displayed when present.
- All bounded `resource_guard` metadata, including integer/name level, reason,
  requested/effective interval fields (`_s` means seconds), deferred chunks,
  capture health, telemetry staleness and inference-release warnings. JSON `null`
  is shown as unavailable, never silently converted to zero.
- Backend `resource_usage`, preserving keys and units: `_mib` is MiB, `_percent`
  is percent. `peak`/`min` keys describe history, **not** current pressure. Load
  per CPU is not CPU utilization. Unknown metrics remain unavailable.
- Last successful receipt time. Transport, HTTP, schema and response-size errors
  mark the retained prior sample **STALE / unavailable** and disable mutation.

`resource_guard` is deliberately decoded as a dictionary so the evolving backend
can add level names, nested current telemetry and effective intervals without a
monitor rebuild. No protection is inferred when the backend omits this field.
The menu is text-only and displays no transcript, notes or analysis content.
Long menu rows are shortened visually; hover for their bounded full text.

## Controls and safety

**Recording only (pause AI)** explicitly sends:

```http
POST /api/resource-guard
Origin: http://127.0.0.1:18766
Content-Type: application/json

{"recording_only":true}
```

It then GETs `/api/status` to display the backend's actual effective state. A
successful POST is not presented as proof that inference was released or capture
is healthy. The action is unavailable without fresh, reported guard metadata.
There is intentionally no “resume AI”, start/stop recording or automatic recovery
control in this client. Backend policy remains authoritative.

**Open dashboard…** is the only code path that opens a browser, and requires a
user menu click. No browser is opened at launch, build, test or error time.

The default endpoint is exactly `http://127.0.0.1:18766`. `--base-url` accepts only
plain HTTP on literal `127.0.0.1` with an explicit port, no credentials, path, query
or fragment. It exists for an isolated fixture or another explicitly selected
local backend. Even on an alternate port, the mutation Origin remains the exact
production Origin above. HTTP redirects are rejected, cookies/credential storage
are disabled, and the ephemeral session bypasses caches and proxies.

## Polling budget / limits

One request at a time, with a one-shot timer scheduled **after completion**. Healthy
polling waits 5 seconds; failures back off to 10, 20, 40, then 60 seconds. No busy
loop, subprocess sampler or inference. Requests have a 4-second timeout and
6-second total resource deadline; responses are streamed with an 8 MiB cap. A
large status fails visibly instead of consuming unbounded memory. This endpoint
also contains transcript/analysis data; the monitor discards those after decoding
and does not log them. Polling still makes the backend serialize its full status.
A future lightweight telemetry endpoint would reduce that cost further.

This is a monitor, **not a watchdog or guarantee against call slowdowns**. It
cannot verify third-party CPU load, actual memory pressure, audio gaps or Ollama
release independently. No live UI energy/RSS budget or meeting-under-load margin
has been measured; measure those outside a meeting with separate authorization.
The headless tests validate protocol/rendering, not AppKit visual behavior.

## Headless smoke tests (safe during a meeting)

Build first, then:

```sh
python3 -m unittest discover -s tests -p test_native_monitor.py -v
```

The tests start a temporary HTTP fixture on an OS-assigned loopback port, run the
compiled executable's `--self-test` path and shut the fixture down. That path does
**not** initialize `NSApplication`, show UI, open a browser or contact production.
It validates actual URLSession GET/POST traffic, exact Origin/body, status
read-back, rendering, unknown telemetry, HTTP/schema errors, redirect refusal and
remote URL rejection. Tests skip explicitly if the binary has not been built.

Manual **read-only** terminal probe (contacts the real backend; no UI):

```sh
sh native-monitor/run.sh --self-test
```

For your own isolated fixture:

```sh
sh native-monitor/run.sh --self-test --base-url http://127.0.0.1:FIXTURE_PORT
# Explicit fixture-only mutation plus GET read-back:
sh native-monitor/run.sh --self-test --base-url http://127.0.0.1:FIXTURE_PORT --recording-only
```

`--recording-only` in headless mode refuses port 18766 to prevent accidental live
meeting mutation. Ordinary UI launch never mutates anything automatically.

## Verification on this host

The Intel x86_64 `.app` was built with installed Swift 6.0.3, and the isolated
headless HTTP test suite passed. `plutil -lint` accepted the bundle plist. No UI
was launched, browser opened, production API called, service restarted, recording
changed or stress test performed. The first cold module-cache build exceeded a
180-second tool deadline; retrying with a longer deadline built successfully.
