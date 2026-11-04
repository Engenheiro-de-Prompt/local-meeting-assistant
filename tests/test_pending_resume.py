"""Explicit checkpoint recovery only, using temporary silent fixtures."""
import http.client
import json
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import live_meeting_app as app
from test_processing import make_manager, write_chunk


class PendingResumeTests(unittest.TestCase):
    def checkpoint(self, root):
        folder = root / 'pending-meeting'
        folder.mkdir()
        manager, chunks = make_manager(folder, folder.name)
        manager.session.update(next_chunk=1, transcription_mode='live',
                               live_analysis_enabled=False, final_analysis_enabled=False,
                               segments=[{'start': 0, 'end': 45, 'text': 'Already saved'}])
        for index in range(3):
            write_chunk(chunks / f'chunk_{index:06d}.wav')
        manager._persist()
        return folder, chunks

    def test_uploaded_file_resume_accepts_its_canonical_transcript_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            folder, _ = self.checkpoint(root)
            metadata = folder / 'session.json'
            saved = json.loads(metadata.read_text())
            output = root / (folder.name + '_transcricao.txt')
            saved.update(capture_mode='file_upload', audio_file=str(root / (folder.name + '.wav')),
                         transcript_file=str(output))
            metadata.write_text(json.dumps(saved))
            manager = app.MeetingManager(root)
            try:
                with (patch.object(manager, '_load_whisper', return_value=object()),
                      patch.object(manager, '_transcribe_chunk', side_effect=lambda _m, _p, i, _l:
                                   ([{'start': i * 45, 'end': (i + 1) * 45, 'text': f'Uploaded {i}'}], 'pt')),
                      patch.object(manager, '_start_resource_monitor')):
                    manager.resume_pending(folder.name)
                    assert manager.worker_thread is not None
                    manager.worker_thread.join(5)
                    self.assertFalse(manager.worker_thread.is_alive())
                restored = json.loads(metadata.read_text())
                self.assertEqual(restored['next_chunk'], 3)
                self.assertEqual(restored['state'], 'completed')
                self.assertEqual(restored['segments'][0], saved['segments'][0])
                self.assertIn('Uploaded 2', output.read_text())
            finally:
                manager.shutdown()

    def test_checkpoint_preserves_chunk_interval_for_explicit_resume(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            folder, _ = self.checkpoint(root)
            metadata = folder / 'session.json'
            manager, _ = make_manager(folder, folder.name)
            manager.session.update(chunk_seconds=7, next_chunk=1,
                                   segments=[{'start': 0, 'end': 7, 'text': 'Saved seven seconds'}],
                                   live_analysis_enabled=False, final_analysis_enabled=False)
            manager._persist()
            self.assertEqual(json.loads(metadata.read_text()).get('chunk_seconds'), 7)
            resumed = app.MeetingManager(root)
            calls = []
            with (patch.object(resumed, '_processing_loop', side_effect=lambda s: calls.append(s['chunk_seconds'])),
                  patch.object(resumed, '_start_resource_monitor')):
                resumed.resume_pending(folder.name)
                resumed.worker_thread.join(2)
            resumed.shutdown()
            self.assertEqual(calls, [7])

    def test_resume_rejects_unsafe_outputs_gaps_and_invalid_settings_without_writes(self):
        for case in ('raw-output', 'foreign-output', 'foreign-transcript', 'gap', 'truncated', 'bad-port', 'bad-checkpoint'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as td:
                root = pathlib.Path(td)
                folder, chunks = self.checkpoint(root)
                metadata = folder / 'session.json'
                data = json.loads(metadata.read_text())
                if case == 'raw-output':
                    data['transcript_file'] = str(chunks / 'chunk_000000.wav')
                elif case == 'foreign-output':
                    data['audio_file'] = str(root / 'another-meeting.wav')
                elif case == 'foreign-transcript':
                    data['transcript_file'] = str(root / 'another-meeting_transcricao.txt')
                elif case == 'gap':
                    (chunks / 'chunk_000001.wav').unlink()
                elif case == 'truncated':
                    path = chunks / 'chunk_000002.wav'
                    path.write_bytes(path.read_bytes()[:-2])
                elif case == 'bad-port':
                    data['ollama_port'] = 9999
                elif case == 'bad-checkpoint':
                    data['next_chunk'] = 4
                metadata.write_text(json.dumps(data))
                before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                manager = app.MeetingManager(root)
                with (patch.object(manager, '_processing_loop'),
                      patch.object(manager, '_start_resource_monitor')):
                    with self.assertRaises(ValueError):
                        manager.resume_pending(folder.name)
                    if manager.worker_thread:
                        manager.worker_thread.join(2)
                manager.shutdown()
                self.assertEqual({str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}, before)
                self.assertIsNone(manager.session)

    def test_resume_bounds_wav_reads_and_preserves_originals(self):
        import wave

        for case in ('valid', 'truncated', 'truncated-header', 'sentinel-data', 'sentinel-riff'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as td:
                root = pathlib.Path(td)
                folder, chunks = self.checkpoint(root)
                path = chunks / 'chunk_000000.wav'
                # Exceed the validation read budget with a real, silent PCM file.
                with wave.open(str(path), 'wb') as wav:
                    wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                    wav.writeframes(b'\x00\x00' * 100000)
                raw = bytearray(path.read_bytes())
                if case == 'truncated':
                    del raw[-2:]
                elif case == 'truncated-header':
                    del raw[24:]
                    raw[4:8] = (len(raw) - 8).to_bytes(4, 'little')
                elif case == 'sentinel-data':
                    raw[40:44] = b'\xff' * 4
                elif case == 'sentinel-riff':
                    raw[4:8] = b'\xff' * 4
                path.write_bytes(raw)
                before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                manager = app.MeetingManager(root)
                read_sizes = []
                original_readframes = wave.Wave_read.readframes

                def bounded_readframes(wav, count):
                    read_sizes.append(count)
                    self.assertLessEqual(count, 32768, 'Recovery must never allocate from untrusted frame counts')
                    return original_readframes(wav, count)

                try:
                    with (patch.object(wave.Wave_read, 'readframes', bounded_readframes),
                          patch.object(manager, '_processing_loop') as processing,
                          patch.object(manager, '_start_resource_monitor') as monitor):
                        if case == 'valid':
                            manager.resume_pending(folder.name)
                            assert manager.worker_thread is not None
                            manager.worker_thread.join(2)
                            self.assertGreaterEqual(len(read_sizes), 4)
                            self.assertEqual(processing.call_count, 1)
                        else:
                            with self.assertRaises(ValueError):
                                manager.resume_pending(folder.name)
                            processing.assert_not_called()
                            monitor.assert_not_called()
                            self.assertIsNone(manager.session)
                            self.assertEqual(read_sizes, [], 'Invalid sizes must be rejected before PCM reads')
                finally:
                    manager.shutdown()
                after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
                if case == 'valid':
                    for name, content in before.items():
                        if name.endswith('.wav'):
                            self.assertEqual(after[name], content)
                else:
                    self.assertEqual(after, before)

    def test_restart_does_not_autorun_but_explicit_http_resume_uses_checkpoint_without_duplicates(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            folder, chunks = self.checkpoint(root)
            originals = {p.name: p.read_bytes() for p in chunks.glob('*.wav')}
            manager = app.MeetingManager(root)
            self.assertEqual(manager.status()['state'], 'idle', 'startup must never replay pending capture')
            self.assertIsNone(manager.worker_thread)
            server = app.LoopbackServer(('127.0.0.1', 0), app.make_handler(manager), manager)
            serving = threading.Thread(target=server.serve_forever, daemon=True)
            serving.start()
            calls = []
            try:
                with (patch.object(manager, '_load_whisper', return_value=object()),
                      patch.object(manager, '_transcribe_chunk', side_effect=lambda _m, _p, i, _l:
                                   (calls.append(i) or [{'start': i * 45, 'end': (i + 1) * 45,
                                                        'text': f'Resumed {i}'}], 'pt')),
                      patch.object(app, '_ollama_call') as llm):
                    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    origin = f'http://127.0.0.1:{server.server_port}'
                    conn.request('POST', '/api/resume', body=json.dumps({'session_id': folder.name}),
                                 headers={'Content-Type': 'application/json', 'Origin': origin})
                    response = conn.getresponse()
                    body = response.read()
                    conn.close()
                    self.assertEqual(response.status, 200, body)
                    manager.worker_thread.join(5)
                    self.assertFalse(manager.worker_thread.is_alive())
                    llm.assert_not_called()
                self.assertEqual(calls, [1, 2])
                saved = json.loads((folder / 'session.json').read_text())
                self.assertEqual(saved['state'], 'completed')
                self.assertEqual(saved['next_chunk'], 3)
                self.assertEqual([s['text'] for s in saved['segments']], ['Already saved', 'Resumed 1', 'Resumed 2'])
                self.assertEqual(saved['segments'][-1]['end'], 135)
                self.assertEqual({p.name: p.read_bytes() for p in chunks.glob('*.wav')}, originals)
                self.assertEqual(app.MeetingManager(root).status()['state'], 'completed')
            finally:
                manager.shutdown()
                server.shutdown()
                server.server_close()
                serving.join(2)


if __name__ == '__main__':
    unittest.main()
