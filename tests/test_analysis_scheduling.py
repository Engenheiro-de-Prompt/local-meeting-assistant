"""Accelerated scheduling tests: declared media time, NOT wall-clock soaks.

WAV inputs are short silent fixtures; mocked Whisper boundaries declare 45 s
per chunk. Scheduling, timestamp offsets, HTTP Ollama calls and persistence
are real. No devices, speakers, real recordings or model weights are used.
"""
import http.server
import json
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import live_meeting_app as app
from test_processing import make_manager, write_chunk


class AnalysisSchedulingTests(unittest.TestCase):
    def test_shutdown_discards_late_whisper_checkpoint(self):
        with tempfile.TemporaryDirectory() as td:
            manager, chunks = make_manager(pathlib.Path(td), 'cancel-whisper')
            session = manager.session
            assert session is not None
            session.update(live_analysis_enabled=False, final_analysis_enabled=False)
            write_chunk(chunks / 'chunk_000000.wav')
            manager._persist()
            checkpoint = pathlib.Path(session['session_json'])
            before = checkpoint.read_bytes()
            entered, release = threading.Event(), threading.Event()

            def blocked(*_args):
                entered.set()
                release.wait(5)
                return [{'start': 0, 'end': 45, 'text': 'Cancelled inference'}], 'pt'

            with (patch.object(manager, '_load_whisper', return_value=object()),
                  patch.object(manager, '_transcribe_chunk', side_effect=blocked)):
                worker = threading.Thread(target=manager._processing_loop, args=(manager.session,), daemon=True)
                manager.worker_thread = worker
                worker.start()
                original_join = worker.join
                try:
                    self.assertTrue(entered.wait(2))
                    # Exercise expiry of the shutdown join without a 15-second test sleep.
                    with patch.object(worker, 'join', side_effect=lambda timeout: original_join(.02)):
                        manager.shutdown()
                    self.assertTrue(worker.is_alive())
                finally:
                    release.set()
                    original_join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(session['next_chunk'], 0)
            self.assertEqual(session['segments'], [])
            self.assertEqual(checkpoint.read_bytes(), before)

    def test_analysis_persistence_error_does_not_kill_worker_or_wedge_drain(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), 'analysis-storage-error')
            manager.session['segments'] = [{'start': 0, 'end': 45, 'text': 'Saved transcript'}]
            done = threading.Event()
            result = ('Summary', {b['id']: 'Result' for b in app.default_boxes()})
            try:
                with (patch.object(app, '_ollama_call', return_value=result),
                      patch.object(manager, '_persist', side_effect=OSError('Synthetic full disk'))):
                    manager._schedule_analysis(manager.session, force=True)
                    waiter = threading.Thread(target=lambda: (manager._wait_for_analysis(), done.set()), daemon=True)
                    waiter.start()
                    self.assertTrue(done.wait(2), 'analysis persistence error must release drain')
                    manager._analysis_thread.join(.1)
                    self.assertTrue(manager._analysis_thread.is_alive(), 'worker must survive an analysis persistence failure')
                    self.assertIn('OSError', manager._last_analysis_error)
            finally:
                manager.shutdown()

    def test_status_exposes_closed_chunk_backlog_separately_from_open_capture(self):
        with tempfile.TemporaryDirectory() as td:
            manager, chunks = make_manager(pathlib.Path(td), 'progress')
            manager.session['next_chunk'] = 1
            manager.session['segments'] = [{'start': 0, 'end': 45, 'text': 'Saved'}]
            for index in range(4):
                write_chunk(chunks / f'chunk_{index:06d}.wav')
            manager.recorder = SimpleNamespace(poll=lambda: None)
            manager._analysis_busy = True
            progress = manager.status().get('processing_progress')
            self.assertIsNotNone(progress, 'status must expose transcription backlog rather than only wall time')
            self.assertEqual(progress['pending_chunk_count'], 2)
            self.assertEqual(progress['captured_chunk_count'], 4)
            self.assertEqual(progress['open_chunk_count'], 1)
            self.assertEqual(progress['next_chunk'], 1)
            self.assertEqual(progress['transcript_coverage_s'], 45)
            self.assertFalse(progress['caught_up'])
            self.assertTrue(progress['analysis_busy'])
            manager.session['next_chunk'] = 3
            self.assertTrue(manager.status()['processing_progress']['caught_up'])
            manager.recorder = None
            self.assertEqual(manager.status()['processing_progress']['pending_chunk_count'], 1)

    def test_stale_transcription_worker_cannot_process_or_error_new_session(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), 'new-session')
            old_session = dict(manager.session, session_id='old-session')
            manager._processing_loop(old_session)
            self.assertEqual(manager.session['state'], 'processing')
            self.assertEqual(manager.session['error'], '')
            self.assertEqual(manager.session['next_chunk'], 0)

    def test_shutdown_discards_late_final_response_and_private_compaction(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), 'cancel-final')
            manager.session.update(state='final_analysis', final_analysis_state='running',
                                   segments=[{'start': 0, 'end': 45, 'text': 'Full transcript'}],
                                   context_compaction={'summary': 'Original ledger'})
            manager._persist()
            before = pathlib.Path(manager.session['session_json']).read_bytes()
            entered, release = threading.Event(), threading.Event()

            def blocked(_port, _model, _text, _config, context):
                context['summary'] = 'Late mutation'
                entered.set()
                release.wait(4)
                return {'summary': 'Late final', 'tasks': [], 'results': {}}

            with patch.object(app, '_ollama_final_call', side_effect=blocked):
                worker = threading.Thread(target=manager._run_final_analysis, args=(manager.session,))
                worker.start()
                try:
                    self.assertTrue(entered.wait(2))
                    manager.shutdown()
                finally:
                    release.set()
                    worker.join(2)
            self.assertEqual(manager.session['context_compaction'], {'summary': 'Original ledger'})
            self.assertEqual(manager.session['state'], 'final_analysis')
            self.assertEqual(pathlib.Path(manager.session['session_json']).read_bytes(), before)

    def test_audio_finalization_retains_original_raw_chunks(self):
        with tempfile.TemporaryDirectory() as td:
            manager, chunks = make_manager(pathlib.Path(td), 'raw-preservation')
            for index in range(3):
                write_chunk(chunks / f'chunk_{index:06d}.wav')
            originals = {p.name: p.read_bytes() for p in chunks.glob('*.wav')}
            manager._finalize_audio()
            self.assertEqual({p.name: p.read_bytes() for p in chunks.glob('*.wav')}, originals)
            self.assertTrue(pathlib.Path(manager.session['audio_file']).is_file())

    def test_changed_model_cannot_commit_stale_analysis_or_mutate_compaction(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), 'stale-model')
            manager.session['segments'] = [{'start': 0, 'end': 45, 'text': 'Original transcript'}]
            original = {'summary': 'Existing ledger', 'covered_chars': 10}
            manager.session['context_compaction'] = dict(original)

            def stale(_port, _model, _text, boxes, _summary, context):
                context['summary'] = 'Uncommitted stale ledger'
                manager.session['llm_model'] = 'changed-local-model'
                return 'Stale summary', {b['id']: 'Stale result' for b in boxes}

            with patch.object(app, '_ollama_call', side_effect=stale):
                manager._maybe_analyze(force=True)
            self.assertEqual(manager.session['context_compaction'], original,
                             'LLM must mutate a private snapshot, not shared session state')
            self.assertEqual(manager.session['analyses'], {})
            self.assertTrue(manager.session['force_analysis'])

    def test_analysis_interval_is_measured_from_completion_even_after_timeout(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), 'analysis-backoff')
            manager.session.update(started_monotonic=0, analysis_interval_s=60,
                                   segments=[{'start': 0, 'end': 45, 'text': 'First'}])
            clock = [61.0]
            calls = []

            def timeout(*_args):
                calls.append(clock[0])
                clock[0] += 600  # finite individual inference, not a meeting duration limit
                raise TimeoutError('Synthetic timeout')

            with (patch.object(app.time, 'monotonic', side_effect=lambda: clock[0]),
                  patch.object(app, '_ollama_call', side_effect=timeout)):
                manager._maybe_analyze()
                manager.session['segments'].append({'start': 45, 'end': 90, 'text': 'Second'})
                manager._maybe_analyze()
                self.assertEqual(calls, [61.0], 'slow errors must not make every next chunk immediately due')
                self.assertEqual(manager.session['last_analysis_elapsed'], 661)
                clock[0] += 61
                manager._maybe_analyze()
                self.assertEqual(calls, [61.0, 722.0])
            self.assertEqual(len(manager.session['segments']), 2)
            self.assertIn('TimeoutError', manager._last_analysis_error)

    def test_shutdown_is_bounded_cancels_coalesced_work_and_discards_late_result(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), 'shutdown-blocked-analysis')
            manager.session['segments'] = [{'start': 0, 'end': 45, 'text': 'Saved before shutdown'}]
            manager._persist()
            before = pathlib.Path(manager.session['session_json']).read_bytes()
            entered, release = threading.Event(), threading.Event()
            calls = []

            def blocked(_port, _model, _text, boxes, *_args):
                calls.append(True)
                entered.set()
                release.wait(5)
                return 'Late summary', {b['id']: 'Late result' for b in boxes}

            with patch.object(app, '_ollama_call', side_effect=blocked):
                manager._schedule_analysis(manager.session, force=True)
                self.assertTrue(entered.wait(2))
                worker = manager._analysis_thread
                try:
                    for _ in range(2000):
                        manager._schedule_analysis(manager.session, force=True)
                    self.assertIs(manager._analysis_thread, worker)
                    self.assertEqual(len(calls), 1)
                    start = time.monotonic()
                    manager.shutdown()
                    self.assertLess(time.monotonic() - start, 2)
                finally:
                    release.set()
                    worker.join(3)
                self.assertFalse(worker.is_alive(), 'analysis daemon must exit after cancelled in-flight call')
                self.assertEqual(calls, [True], 'cancel pending jobs, never drain LLM queue on shutdown')
                self.assertEqual(manager.session['analyses'], {}, 'late response must not commit')
                self.assertEqual(pathlib.Path(manager.session['session_json']).read_bytes(), before)

    def test_accelerated_8145_seconds_persist_while_ollama_http_is_blocked(self):
        declared_chunks, chunk_seconds = 181, 45
        declared_seconds = declared_chunks * chunk_seconds
        self.assertGreater(declared_seconds, 2 * 60 * 60)
        entered, release, transcribed = threading.Event(), threading.Event(), threading.Event()
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                payload = json.loads(body['messages'][1]['content'])
                requests.append(payload)
                entered.set()
                release.wait(15)
                result = {'summary': 'Synthetic summary', 'results': [
                    {'id': box['id'], 'text': 'Synthetic result'} for box in payload['caixas']]}
                raw = json.dumps({'message': {'content': json.dumps(result)}}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            with tempfile.TemporaryDirectory() as td:
                root = pathlib.Path(td)
                manager, chunks = make_manager(root, 'accelerated-8145-seconds')
                manager.session.update(ollama_port=server.server_port,
                                       started_monotonic=time.monotonic() - declared_seconds,
                                       final_analysis_enabled=True)
                manager.stop_requested = True
                for index in range(declared_chunks):
                    write_chunk(chunks / f'chunk_{index:06d}.wav')
                calls = []

                class WhisperBoundary:
                    def transcribe(self, path, **_kwargs):
                        index = int(pathlib.Path(path).stem.split('_')[1])
                        if index == 1:
                            if not entered.wait(3):
                                raise AssertionError('LLM did not start after first chunk')
                        calls.append(index)
                        if index == declared_chunks - 1:
                            transcribed.set()
                        return [SimpleNamespace(start=0, end=chunk_seconds,
                                                text=f'fixture-{index:03d}')], SimpleNamespace(language='pt')

                final_inputs = []
                manager._finalize_audio = lambda: (root / 'final.wav').write_bytes(b'synthetic-final-audio')
                with (patch.object(manager, '_load_whisper', return_value=WhisperBoundary()) as load,
                      patch.object(app, '_ollama_final_call', side_effect=lambda _p, _m, text, *_a:
                                   final_inputs.append(text) or {'summary': 'Final', 'tasks': [], 'results': {}})):
                    worker = threading.Thread(target=manager._processing_loop, args=(manager.session,), daemon=True)
                    worker.start()
                    try:
                        self.assertTrue(entered.wait(3), 'expected real loopback Ollama HTTP request')
                        self.assertTrue(transcribed.wait(5),
                                        'Whisper stalled behind blocked Ollama before >2h declared coverage')
                        # The event is inside the last inference: synchronize with its persisted checkpoint.
                        deadline = time.monotonic() + 3
                        while time.monotonic() < deadline:
                            with manager.lock:
                                if manager.session['next_chunk'] == declared_chunks:
                                    break
                            time.sleep(.005)
                        with manager.lock:
                            saved = json.loads((root / 'session.json').read_text())
                        self.assertFalse(release.is_set())
                        self.assertEqual(saved['next_chunk'], declared_chunks)
                        self.assertEqual(len(saved['segments']), declared_chunks)
                        self.assertEqual(saved['segments'][0]['start'], 0)
                        self.assertEqual(saved['segments'][-1]['end'], declared_seconds)
                        self.assertEqual(calls, list(range(declared_chunks)))
                        self.assertIn('fixture-180', (root / 'transcript.txt').read_text())
                        self.assertEqual(load.call_count, 1, 'live LLM must not evict Whisper')
                        self.assertEqual(len(requests), 1, 'only one LLM call may be in flight')
                        self.assertEqual(final_inputs, [], 'final analysis must wait for full transcript/live drain')
                    finally:
                        release.set()
                        worker.join(10)
                        manager.shutdown()
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(manager.session['state'], 'completed')
                    self.assertEqual(len(final_inputs), 1)
                    self.assertIn('fixture-000', final_inputs[0])
                    self.assertIn('fixture-180', final_inputs[0])
                    self.assertLessEqual(len(requests), 2, 'coalesce accumulated work into latest transcript')
                    self.assertEqual(len(list(chunks.glob('chunk_*.wav'))), declared_chunks)
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            server_thread.join(2)


if __name__ == '__main__':
    unittest.main()
