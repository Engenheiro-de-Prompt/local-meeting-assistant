# Local meeting assistant — private audio capture, transcription and local LLM analysis

A desktop-friendly, local-first meeting recorder with incremental transcription and configurable LLM analysis. Audio and transcripts stay on the machine; LLM requests go only to a local Ollama endpoint.

> **Status:** early, Linux-focused project. Review `SECURITY.md` and `docs/ARCHITECTURE.md` before running or packaging it.

## Features

- Start/stop local capture (microphone, system audio, or both).
- Incremental transcription with a cached Faster-Whisper model; the user can transcribe live, defer all transcription until capture stops, or disable transcription for recording-only sessions.
- Independent switches for live and final analysis with a local Ollama model; Gemma 4 E4B is the local setup's default, but any installed model may be selected. The legacy/default setup keeps all features enabled.
- Live and final analysis use the full transcript until a conservative estimated context budget is crossed (checked against Ollama prompt metrics when available); then older text is added to a persisted cumulative summary and the newest, not-yet-summarized transcript remains verbatim after it. The original transcript is always retained in full.
- After stopping, the full transcription is saved first (when enabled); final LLM analysis can then produce a meeting summary, structured/sorted task table, and optional custom analysis cards.
- Final summary/task prompts and custom final-analysis cards are editable in the dashboard; the combined TXT export puts summary, tasks and custom findings before the full transcript.
- Dark dashboard, transcript/analysis downloads, copy/open/playback, and local audio-file import.
- Local-only HTTP service bound to loopback; bounded sequential work and private-per-user output permissions.

## Requirements

- Linux with Python 3.10+, FFmpeg, and PulseAudio/PipeWire-Pulse for live capture.
- Faster-Whisper and a local cached Whisper model when transcription is enabled; Ollama with an installed local model when live or final LLM analysis is enabled.
- NVIDIA CUDA is optional; CPU transcription is supported when the installed CTranslate2 build supports it.

The project never downloads model weights at runtime. The launcher sets Hugging Face offline mode.

## Run

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
# Install FFmpeg and configure Ollama/model separately for your system.
MEETING_PYTHON="$PWD/.venv/bin/python" ./start.sh
```

Open `http://127.0.0.1:18766/`. By default, recordings are written to a directory under the current user's home folder. Set `MEETING_RECORDINGS_DIR` to choose another local directory. `MEETING_AUDIO_SOURCE` is optional and only used to reuse compatible audio-device/CUDA helpers from an existing local recorder. No source recorder is required for basic fallback device selection or CPU model loading.

The launcher can use an existing recorder virtual environment when available; otherwise it uses `python3`. Set `MEETING_PYTHON` to choose explicitly. A private user config file may be placed at `${XDG_CONFIG_HOME:-$HOME/.config}/local-meeting-assistant/env`; this is useful for machine-specific data locations and must remain outside Git. All paths can be overridden with environment variables; do not commit local config files or machine-specific paths.

## Tests

```bash
python -m unittest discover -s tests -v
python -m py_compile live_meeting_app.py tests/*.py
bash -n start.sh
```

Tests use synthetic or temporary audio and mocked model outputs; they do not need a real meeting or an Ollama model. Install FFmpeg to run the audio-processing tests.

Context summaries are semantic compression, not a lossless transcript. The original transcript remains the source of truth; automated prompts/tests reduce but cannot eliminate omission risk. See `docs/ARCHITECTURE.md` for the compaction budget and behavior.

## Privacy and scope

- No cloud LLM, audio upload service, analytics, telemetry, or external relay is used by the app.
- User audio, transcript, analysis JSON, and runtime evidence must never be committed. `.gitignore` excludes the local evidence and meeting-data paths.
- HTTP logs do not include request bodies, prompts, or transcript contents.
- The app is intentionally separate from any existing recorder; `MEETING_AUDIO_SOURCE` is read-only helper reuse.

See `docs/ARCHITECTURE.md`, `CONTRIBUTING.md`, and `SECURITY.md`. An open-source license has not been chosen yet; do not redistribute until the project owner selects one.
