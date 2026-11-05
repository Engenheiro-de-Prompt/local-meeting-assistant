"""PCM progress and best-effort durability. No capture, model or import side effects.

fsync requests writeback; it is NOT a promise of survival through power loss.
Offline salvage copies only complete available frames, never fills missing time.
"""
import json
import os
from pathlib import Path
import stat
import struct
import time
import wave


_UNKNOWN = 0xffffffff


def _path(value):
    p = Path(value)
    if '..' in p.parts or not p.name or '\x00' in str(p):
        raise ValueError('Unsafe path')
    p = p.absolute()
    for part in [*reversed(p.parents), p]:
        if part.is_symlink():
            # macOS exposes OS-owned /var and /tmp aliases for temporary files.
            # Do not relax checks for user-controlled symlink components.
            if str(part) in {'/var', '/tmp'} and part.lstat().st_uid == 0 and str(part.resolve()) in {'/private/var', '/private/tmp'}:
                continue
            raise ValueError('Symlinks are not accepted')
    return p.resolve(strict=False)


def _open_read(path):
    fd = os.open(_path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError('Expected regular WAV file')
    return os.fdopen(fd, 'rb')


def _pcm(stream):
    """Read at most 128 metadata chunks / 1 MiB metadata; never scan PCM."""
    size = os.fstat(stream.fileno()).st_size
    head = stream.read(12)
    if len(head) != 12 or head[:4] != b'RIFF' or head[8:] != b'WAVE':
        raise ValueError('Expected RIFF WAVE')
    riff = struct.unpack('<I', head[4:8])[0]
    unfinalized = riff == _UNKNOWN or riff + 8 > size
    end = size if riff == _UNKNOWN else min(size, riff + 8)
    fmt = False
    offset = 12
    for _ in range(128):
        if offset + 8 > end or offset > 1024 * 1024:
            break
        stream.seek(offset)
        tag, length = struct.unpack('<4sI', stream.read(8))
        start = offset + 8
        if tag == b'data':
            if not fmt:
                raise ValueError('Missing PCM format')
            available = max(0, end - start)
            actual = min(length, available) & ~1
            return {'offset': start, 'pcm_bytes': actual, 'frames': actual // 2,
                    'duration_s': actual / 32000,
                    'source_unfinalized': unfinalized or length == _UNKNOWN or length > available}
        if length == _UNKNOWN or start + length > end:
            raise ValueError('Truncated metadata chunk')
        if tag == b'fmt ':
            if fmt or length < 16 or length > 1024:
                raise ValueError('Invalid format chunk')
            fields = struct.unpack('<HHIIHH', stream.read(16))
            if fields != (1, 1, 16000, 32000, 2, 16):
                raise ValueError('Only PCM16le mono 16000 Hz is supported')
            fmt = True
        offset = start + length + (length & 1)
    raise ValueError('Missing data chunk or metadata limit exceeded')


def salvage_pcm_wav(source_path, destination_path) -> dict:
    """Explicit offline copy into a NEW WAV. Caller must ensure capture is stopped.

    Reject symlinks, traversal, nonregular sources and unsupported codecs. No
    original is edited and existing destinations are never replaced.
    """
    source, destination = _path(source_path), _path(destination_path)
    created = False
    try:
        with _open_read(source) as src:
            before = os.fstat(src.fileno())
            info = _pcm(src)
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            created = True
            with os.fdopen(fd, 'wb') as out:
                with wave.open(out, 'wb') as target:
                    target.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                    src.seek(info['offset'])
                    remaining = info['pcm_bytes']
                    while remaining:
                        block = src.read(min(65536, remaining))
                        if not block:
                            raise ValueError('Source changed during salvage')
                        target.writeframesraw(block)
                        remaining -= len(block)
                out.flush()
                os.fsync(out.fileno())
            after = os.fstat(src.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError('Source changed during salvage; stop capture first')
        with wave.open(str(destination), 'rb') as check:
            if check.getnframes() != info['frames'] or check.getparams()[:3] != (1, 2, 16000):
                raise ValueError('Output validation failed')
        return {**info, 'source': str(source), 'destination': str(destination)}
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


class AudioSafety:
    """Constant-memory incremental monitor: at most eight sealed chunks per tick.

    2s polling, active writeback at most every 10s, journal only after sealed
    audio fsync. No sample-amplitude analysis, transcript or cursor mutation.
    """
    def __init__(self, chunks_dir, *, started=None, sync_interval=10):
        self.root = _path(chunks_dir)
        self.started = time.monotonic() if started is None else started
        self.sync_interval = max(5, sync_interval)
        self.index = 0
        self.sealed_bytes = 0
        self.last_bytes = 0
        self.last_growth = self.started
        self.last_sync = self.started - self.sync_interval
        self.error = ''

    def _chunk(self, index):
        return self.root / f'chunk_{index:06d}.wav'

    def _seal(self, path, info):
        if info['source_unfinalized']:
            raise ValueError('Sealed chunk has unfinished WAV header; offline salvage required')
        with _open_read(path) as stream:
            os.fsync(stream.fileno())
        journal = _path(self.root / 'audio-journal.jsonl')
        fd = os.open(journal, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(fd, 'ab') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('Journal must be regular')
            entry = {'chunk': path.name, 'frames': info['frames'], 'pcm_bytes': info['pcm_bytes'],
                     'duration_s': info['duration_s'], 'audio_fsync_requested': True}
            stream.write((json.dumps(entry, separators=(',', ':')) + '\n').encode())
            stream.flush()
            os.fsync(stream.fileno())

    def sample(self, capture_alive, *, now=None, expected_running=True):
        now = time.monotonic() if now is None else now
        current_bytes = 0
        try:
            for _ in range(8):
                path = self._chunk(self.index)
                if not path.exists():
                    break
                sealed = self._chunk(self.index + 1).exists() or not capture_alive
                with _open_read(path) as stream:
                    # FFmpeg creates an empty active output before writing its
                    # header. This is not an I/O failure; growth timeout handles it.
                    if not sealed and os.fstat(stream.fileno()).st_size < 44:
                        break
                    info = _pcm(stream)
                if not sealed:
                    current_bytes = info['pcm_bytes']
                    if now - self.last_sync >= self.sync_interval:
                        with _open_read(path) as stream:
                            os.fsync(stream.fileno())
                        self.last_sync = now
                    break
                current_bytes = info['pcm_bytes']
                self._seal(path, info)
                self.sealed_bytes += info['pcm_bytes']
                current_bytes = 0
                self.index += 1
        except (OSError, ValueError) as exc:
            self.error = str(exc)[:240]
        total = self.sealed_bytes + current_bytes
        if total > self.last_bytes:
            self.last_growth = now
        self.last_bytes = max(self.last_bytes, total)
        duration = self.last_bytes / 32000
        elapsed = max(0, now - self.started)
        age = max(0, now - self.last_growth)
        state = ('error' if self.error else 'stopped' if not capture_alive else
                 'stalled' if age >= 15 else 'lagging' if elapsed >= 30 and elapsed - duration > max(15, elapsed * .25)
                 else 'healthy')
        warning = {'error': 'Falha de segurança do áudio: ' + self.error,
                   'stalled': 'Captura sem crescimento PCM; áudio original preservado.',
                   'lagging': 'Duração PCM abaixo do tempo decorrido; áudio original preservado.',
                   'stopped': 'Captura encerrada inesperadamente.' if expected_running else ''}.get(state, '')
        return {'state': state, 'capture_alive': bool(capture_alive), 'pcm_bytes': self.last_bytes,
                'duration_s': round(duration, 3), 'elapsed_s': round(elapsed, 3),
                'lag_s': round(max(0, elapsed - duration), 3), 'last_growth_age_s': round(age, 3),
                'sealed_chunks': self.index, 'active_sync_interval_s': self.sync_interval,
                'error': self.error, 'warning': warning,
                'durability': 'best_effort_fsync_not_power_loss_guarantee'}
