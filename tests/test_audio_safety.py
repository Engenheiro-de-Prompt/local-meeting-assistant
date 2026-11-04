import json
import pathlib
import struct
import tempfile
import unittest
import wave
from unittest.mock import patch

from audio_safety import AudioSafety, salvage_pcm_wav


def wav(path, frames=16000):
    with wave.open(str(path), 'wb') as f:
        f.setparams((1, 2, 16000, frames, 'NONE', 'not compressed'))
        f.writeframes(b'\0\0' * frames)


class AudioSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def test_salvage_interrupted_and_preserve(self):
        src = self.root / 'in.wav'
        wav(src, 100)
        data = bytearray(src.read_bytes())
        struct.pack_into('<I', data, 4, 0xffffffff)
        struct.pack_into('<I', data, 40, 0xffffffff)
        src.write_bytes(data + b'x')
        result = salvage_pcm_wav(src, self.root / 'out.wav')
        self.assertEqual(result['frames'], 100)
        self.assertTrue(result['source_unfinalized'])
        self.assertEqual(src.read_bytes(), data + b'x')
        with wave.open(str(self.root / 'out.wav')) as f:
            self.assertEqual(f.readframes(1000), bytes(data[44:]))

    def test_reject_paths_formats_and_existing_destination(self):
        src = self.root / 'in.wav'
        wav(src)
        link = self.root / 'link.wav'
        link.symlink_to(src)
        for source, dest in [(link, self.root / 'out.wav'), (src, src),
                             (src, self.root / '..' / 'out.wav')]:
            with self.assertRaises((ValueError, OSError)):
                salvage_pcm_wav(source, dest)
        data = bytearray(src.read_bytes())
        struct.pack_into('<H', data, 20, 3)
        src.write_bytes(data)
        with self.assertRaises(ValueError):
            salvage_pcm_wav(src, self.root / 'out.wav')

    def test_silence_progress_stall_lag_and_dead(self):
        monitor = AudioSafety(self.root, started=0, sync_interval=10)
        wav(self.root / 'chunk_000000.wav')
        self.assertEqual(monitor.sample(True, now=1)['state'], 'healthy')
        self.assertEqual(monitor.sample(True, now=20)['state'], 'stalled')
        wav(self.root / 'chunk_000000.wav', 32000)
        self.assertEqual(monitor.sample(True, now=40)['state'], 'lagging')
        self.assertEqual(monitor.sample(False, now=41)['state'], 'stopped')

    def test_seal_journal_and_sync_error(self):
        wav(self.root / 'chunk_000000.wav')
        wav(self.root / 'chunk_000001.wav')
        monitor = AudioSafety(self.root, started=0)
        result = monitor.sample(True, now=2)
        self.assertEqual(result['sealed_chunks'], 1)
        entries = [json.loads(x) for x in (self.root / 'audio-journal.jsonl').read_text().splitlines()]
        self.assertEqual(entries[0]['frames'], 16000)
        with patch('audio_safety.os.fsync', side_effect=OSError('disk failure')):
            result = monitor.sample(False, now=20)
        self.assertEqual(result['state'], 'error')
        self.assertIn('disk failure', result['error'])

    def test_chunk_bounds_not_trailing_bytes(self):
        src = self.root / 'in.wav'
        wav(src, 10)
        src.write_bytes(src.read_bytes() + b'not audio')
        self.assertEqual(salvage_pcm_wav(src, self.root / 'out.wav')['frames'], 10)

    def test_bounded_seal_batch_and_no_duplicate_entries(self):
        for i in range(12):
            wav(self.root / f'chunk_{i:06d}.wav', 2)
        monitor = AudioSafety(self.root, started=0)
        self.assertEqual(monitor.sample(False, now=1)['sealed_chunks'], 8)
        self.assertEqual(monitor.sample(False, now=2)['sealed_chunks'], 12)
        monitor.sample(False, now=3)
        entries = (self.root / 'audio-journal.jsonl').read_text().splitlines()
        self.assertEqual(len(entries), 12)
        self.assertEqual(monitor.last_bytes, 48)

    def test_active_sync_rate_bounded(self):
        wav(self.root / 'chunk_000000.wav')
        monitor = AudioSafety(self.root, started=0)
        with patch('audio_safety.os.fsync') as sync:
            monitor.sample(True, now=1)
            monitor.sample(True, now=2)
            monitor.sample(True, now=10)
            self.assertEqual(sync.call_count, 1)
            monitor.sample(True, now=11)
            self.assertEqual(sync.call_count, 2)

    def test_empty_active_startup_is_not_failure(self):
        path = self.root / 'chunk_000000.wav'
        path.touch()
        monitor = AudioSafety(self.root, started=0)
        self.assertEqual(monitor.sample(True, now=1)['state'], 'healthy')
        wav(path)
        self.assertEqual(monitor.sample(True, now=2)['state'], 'healthy')

    def test_journal_failure_visible_and_original_unchanged(self):
        path = self.root / 'chunk_000000.wav'
        wav(path)
        original = path.read_bytes()
        (self.root / 'audio-journal.jsonl').mkdir()
        result = AudioSafety(self.root, started=0).sample(False, now=1)
        self.assertEqual(result['state'], 'error')
        self.assertEqual(result['sealed_chunks'], 0)
        self.assertEqual(path.read_bytes(), original)

    def test_worker_independent_warning_cancels_ai_preserves_cursor(self):
        import live_meeting_app as app
        import time
        manager = app.MeetingManager(self.root)
        manager.session = {'chunks_dir': str(self.root), 'started_monotonic': time.monotonic() - 40,
                           'next_chunk': 7, 'segments': []}
        manager.recorder = type('Recorder', (), {'poll': lambda self: None})()
        with patch('live_meeting_app.AudioSafety.sample', return_value={
                'state': 'stalled', 'warning': 'no PCM growth', 'error': ''}):
            manager._start_audio_safety(manager.session)
            self.assertTrue(manager._inference_cancel.wait(2))
            manager._stop_audio_safety()
        self.assertIsNone(manager._audio_safety_thread)
        self.assertEqual(manager.session['next_chunk'], 7)
        self.assertEqual(manager.session['capture_warning'], 'no PCM growth')
        self.assertFalse(manager.resource_guard.allowed('whisper'))
        self.assertIsNone(manager.recorder.poll())

    def test_truncated_metadata_rejected(self):
        src = self.root / 'bad.wav'
        src.write_bytes(b'RIFF' + struct.pack('<I', 100) + b'WAVEJUNK' + struct.pack('<I', 100) + b'x')
        with self.assertRaises(ValueError):
            salvage_pcm_wav(src, self.root / 'out.wav')
