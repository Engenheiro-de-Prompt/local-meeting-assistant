# Architecture and runtime flow

## Components

- `live_meeting_app.py`: loopback-only HTTP server, session manager, FFmpeg capture/import, incremental Whisper worker, Ollama request/validation, private persistence, and resource samples.
- `web/index.html`: vanilla HTML/CSS/JS dashboard; additional assets are served locally through an exact allowlist, without a CDN.
- `web/manual-notes.js`: manual notes/checklist, per-document draft caches with reload recovery, bounded autosave and explicit revision conflicts. Content stays outside the model context.
- `web/dashboard-layout.js` and `.css`: move/resize controls, responsive groups and validated browser-local layout preferences.
- `start.sh`: offline Hugging Face runtime and environment-based local Python/CUDA path setup.
- `tests/`: unit and HTTP integration tests using temporary directories and synthetic or mocked data.

## Data path

1. The browser sends validated settings to the server bound at `127.0.0.1:18766`.
2. FFmpeg captures short WAV segments locally; closed segments are transcribed sequentially with the cached Whisper model.
3. At the selected interval, the full transcript is sent while it fits the configured working budget. Above 24,576 estimated input tokens, older text is folded into a persisted cumulative semantic ledger in local Ollama calls; the request then carries that ledger first and the exact, not-yet-summarized transcript suffix after it. The same context builder is used by live cards and final summary/tasks/custom cards.
4. Structured output is validated against enabled analysis-card IDs before being persisted. A malformed structured response receives one bounded retry; analysis failures do not discard captured audio or transcript.
5. Completed local sessions are restored from their metadata after server restart. Resource sampling records RAM availability, GPU-memory and utilization peaks, and normalized system load every five seconds during new sessions.

## Context compaction

- The authoritative transcript and audio remain complete and unchanged on disk; compaction changes only the LLM request context.
- The ledger tracks decisions and rationale, rejected proposals, corrections, tasks/owners/deadlines/status, dependencies, risks, numbers, and unresolved/resolved points. Newly matured text is added incrementally in 24,000-character blocks; the last roughly 24,000 characters not yet incorporated (including any unfinished block) remain verbatim after the ledger.
- The ledger and character offset are persisted with session metadata so routine live intervals do not re-summarize the whole meeting. Final analysis can flush the remaining older partial block into the ledger.
- A semantic summary is inherently lossy: prompts and structured validation reduce omission risk but cannot guarantee that no relevant nuance is lost. If compaction output is invalid or the final assembled context exceeds the model budget, the analysis is rejected; the original transcript is preserved and no partial analysis is committed.
- Validate quality with synthetic early-decision/later-correction/resolved-task cases and inspect latency/token metrics. A synthetic evaluation is not proof of perfect retention on every real meeting.

## Boundaries and limitations

- No internet endpoint is called by runtime features; Ollama URLs are hard-coded to loopback and host/origin are validated.
- The server is designed for one local user/session at a time, not as a multi-user service. Do not expose its port to a network.
- On analysis error, UI status reports a sanitized category; request bodies and meeting text are not written to application logs.
- Resource samples are periodic and can miss short spikes. Historical sessions created before resource tracking have no retroactive hardware peak evidence.
- Current live capture is Linux/PulseAudio oriented. A packaged cross-platform release needs platform-specific capture adapters and installer testing.
