"""One bounded, low-priority Whisper chunk per disposable subprocess.

Importing this module never imports a model or starts work. The test command seam
runs only explicitly supplied commands; production always launches this file.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time


class WorkerCancelled(RuntimeError):
    pass


def run_cancellable(command, *, cancelled=lambda: False, process_changed=lambda proc: None, timeout=120):
    if cancelled():
        raise WorkerCancelled('Inference cancelled before launch')
    env = dict(os.environ, OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2', MKL_NUM_THREADS='2',
               VECLIB_MAXIMUM_THREADS='2', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
    # File-backed output avoids a pipe deadlock, and is bounded before reading.
    with tempfile.TemporaryFile() as output:
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                stderr=subprocess.DEVNULL, env=env, close_fds=True, start_new_session=True)
        process_changed(proc)
        start = time.monotonic()
        try:
            while proc.poll() is None:
                if cancelled():
                    raise WorkerCancelled('Inference cancelled; original chunk preserved')
                if time.monotonic() - start > timeout:
                    raise TimeoutError('Whisper chunk exceeded 120-second work budget')
                time.sleep(0.05)
            if cancelled():
                raise WorkerCancelled('Inference cancelled before checkpoint')
            if proc.returncode:
                raise RuntimeError('Whisper subprocess failed; original chunk preserved')
            output.seek(0, 2)
            if output.tell() > 2 * 1024 * 1024:
                raise RuntimeError('Whisper output exceeded bound')
            output.seek(0)
            return output.read()
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1)
            process_changed(None)


def run_whisper(descriptor, path, *, language='pt', cancelled=lambda: False, process_changed=lambda proc: None):
    command = [sys.executable, str(pathlib.Path(__file__).resolve()), descriptor['snapshot'],
               descriptor['device'], str(path), language]
    raw = run_cancellable(command, cancelled=cancelled, process_changed=process_changed)
    data = json.loads(raw.decode('utf-8'))
    rows = data['segments']
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError('Invalid Whisper output')
    return rows, data['language']


def main():
    snapshot, device, path, language = sys.argv[1:]
    try:
        os.nice(10)
    except OSError:
        pass
    source = pathlib.Path(os.environ.get('MEETING_AUDIO_SOURCE', pathlib.Path.home() / 'GravadorReunioes'))
    if source.is_dir():
        sys.path.insert(0, str(source))
        try:
            from transcriber import ensure_cuda_environment
            ensure_cuda_environment()
        except ImportError:
            pass
    from faster_whisper import WhisperModel
    model = WhisperModel(snapshot, device=device, compute_type='float16' if device == 'cuda' else 'int8',
                         cpu_threads=2, num_workers=1, local_files_only=True)
    segments, info = model.transcribe(path, language=language, beam_size=2, vad_filter=True,
                                      vad_parameters={'min_silence_duration_ms': 400})
    rows = [{'start': float(seg.start), 'end': float(seg.end), 'text': seg.text} for seg in segments]
    print(json.dumps({'segments': rows, 'language': info.language}, ensure_ascii=False))


if __name__ == '__main__':
    main()
