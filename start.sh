#!/usr/bin/env bash
set -euo pipefail
umask 077
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${MEETING_CONFIG_FILE:-${XDG_CONFIG_HOME:-$HOME/.config}/local-meeting-assistant/env}"
if [[ -r "$CONFIG_FILE" ]]; then
  set -a
  # This is a user-owned local shell config; keep it outside the repository.
  source "$CONFIG_FILE"
  set +a
fi
SOURCE_APP="${MEETING_AUDIO_SOURCE:-$HOME/GravadorReunioes}"
export MEETING_AUDIO_SOURCE="$SOURCE_APP"
export MEETING_RECORDINGS_DIR="${MEETING_RECORDINGS_DIR:-$HOME/Gravações_Reunioes/Ao_Vivo}"
export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export HF_HUB_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1

if [[ -n "${MEETING_PYTHON:-}" ]]; then
  PYTHON="$MEETING_PYTHON"
elif [[ -x "$SOURCE_APP/venv/bin/python" ]]; then
  PYTHON="$SOURCE_APP/venv/bin/python"
else
  PYTHON="$(command -v python3 || true)"
fi
if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
  printf 'Python 3 não encontrado. Defina MEETING_PYTHON ou instale python3.\n' >&2
  exit 1
fi

SITE_PACKAGES="$($PYTHON -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
CUDA_LIBS=""
for library in cudnn cublas cuda_nvrtc; do
  candidate="$SITE_PACKAGES/nvidia/$library/lib"
  if [[ -d "$candidate" ]]; then
    CUDA_LIBS="${CUDA_LIBS:+$CUDA_LIBS:}$candidate"
  fi
done
export LD_LIBRARY_PATH="${CUDA_LIBS}${CUDA_LIBS:+:}${LD_LIBRARY_PATH:-}"
cd "$APP_DIR"
exec "$PYTHON" "$APP_DIR/live_meeting_app.py" "$@"
