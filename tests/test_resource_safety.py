import pathlib
import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import live_meeting_app as app
from test_processing import make_manager, write_chunk


class SafetyTests(unittest.TestCase):
    def test_low_disk_refuses_capture_before_device_or_process_access(self):
        with tempfile.TemporaryDirectory() as td:
            manager = app.MeetingManager(pathlib.Path(td), ffmpeg='fake')
            with patch.object(app.shutil, 'disk_usage', return_value=Mock(free=1)), patch.object(manager, 'devices') as devices, patch.object(app.subprocess, 'Popen') as popen:
                with self.assertRaises(RuntimeError):
                    manager.start({})
            devices.assert_not_called()
            popen.assert_not_called()

    def test_error_state_recorder_is_still_stoppable(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), 'stop-error')
            manager.session['state'] = 'error'
            class Recorder:
                stdin = stderr = None
                alive = True
                def poll(self):
                    return None if self.alive else 0
                def wait(self, timeout):
                    self.alive = False
            manager.recorder = Recorder()
            with patch.object(manager, '_processing_loop'):
                manager.stop()
                manager.worker_thread.join(2)
            self.assertFalse(manager.status()['resource_guard']['capture_alive'])
            self.assertTrue(manager.stop_requested)

    def test_pause_racing_whisper_return_does_not_commit_cursor(self):
        with tempfile.TemporaryDirectory() as td:
            manager, chunks = make_manager(pathlib.Path(td), 'pause-race')
            manager.stop_requested = True
            write_chunk(chunks / 'chunk_000000.wav')
            def transcribe(*_args):
                manager.set_resource_guard({'recording_only': True})
                return ([{'start': 0, 'end': 1, 'text': 'uncommitted'}], 'pt')
            with patch.object(manager, '_load_whisper', return_value={'snapshot': 'fake'}), patch.object(manager, '_transcribe_chunk', side_effect=transcribe), patch.object(manager, '_finalize_audio'):
                manager._processing_loop(manager.session)
            self.assertEqual(manager.session['next_chunk'], 0)
            self.assertEqual(manager.session['segments'], [])

    def test_stopped_capture_finalizes_without_waiting_for_blocked_llm(self):
        with tempfile.TemporaryDirectory() as td:
            manager, chunks = make_manager(pathlib.Path(td), 'stop-with-llm')
            manager.stop_requested = True
            write_chunk(chunks / 'chunk_000000.wav')
            manager.resource_guard.enabled = True
            manager.resource_guard.update(dict(cpu_percent=10, ram_available_mib=9000,
                                                memory_pressure='normal', disk_free_mib=9000))
            manager._inference_kind = 'llm'
            manager._inference_lock.acquire()
            with patch.object(manager, '_finalize_audio') as finalize, patch.object(manager, '_load_whisper') as load:
                worker = threading.Thread(target=manager._processing_loop, daemon=True)
                worker.start()
                worker.join(1)
                try:
                    self.assertFalse(worker.is_alive())
                    finalize.assert_called_once()
                    load.assert_not_called()
                    self.assertEqual(manager.session['next_chunk'], 0)
                    self.assertTrue(manager.session['resource_deferred'])
                finally:
                    manager._shutdown_requested.set()
                    manager._inference_lock.release()
                    manager._inference_kind = None
                    worker.join(2)

    def test_api_guard_requires_exact_origin_and_readback_reports_applied_state(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), 'api-guard')
            server = app.LoopbackServer(('127.0.0.1', 0), app.make_handler(manager), manager)
            serving = threading.Thread(target=server.serve_forever, daemon=True)
            serving.start()
            try:
                for origin, expected in [('http://evil.example', 403), (f'http://127.0.0.1:{server.server_port}', 200)]:
                    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                    conn.request('POST', '/api/resource-guard', body=json.dumps({'recording_only': True}),
                                 headers={'Origin': origin, 'Content-Type': 'application/json'})
                    response = conn.getresponse()
                    response.read()
                    self.assertEqual(response.status, expected)
                    conn.close()
                conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                conn.request('GET', '/api/status')
                state = json.loads(conn.getresponse().read())
                conn.close()
                self.assertTrue(state['resource_guard']['recording_only'])
                self.assertEqual(state['resource_guard']['level_name'], 'recording_only')
                self.assertTrue(json.loads(pathlib.Path(manager.session['session_json']).read_text())['recording_only'])
            finally:
                server.shutdown()
                server.server_close()
                serving.join(2)

    def test_one_heavy_inference_gate_including_forced_final(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), 'single-gate')
            manager.session['segments'] = [{'start': 0, 'end': 1, 'text': 'saved'}]
            with manager._inference('whisper'):
                with self.assertRaises(app.InferenceDeferred):
                    with manager._inference('llm'):
                        self.fail('must not run concurrently')
                with patch.object(app, '_ollama_final_call') as final:
                    manager._run_final_analysis(manager.session)
                final.assert_not_called()
            self.assertEqual(manager.session['final_analysis_state'], 'deferred')

    def test_real_request_timeout_latches_unverified_without_killing_third_party(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), 'llm-release')
            with patch.object(app.subprocess, 'run') as run, patch.object(app.os, 'kill') as kill:
                with self.assertRaises(TimeoutError):
                    with manager._inference('llm'):
                        app._before_llm_request()
                        raise TimeoutError('synthetic transport timeout')
                self.assertFalse(manager.resource_guard.allowed('whisper'))
                self.assertEqual(manager.status()['resource_guard']['llm_release_state'], 'server_release_unverified')
                manager.set_resource_guard({'recording_only': False})
                self.assertFalse(manager.resource_guard.allowed('llm'))
                run.assert_not_called()
                kill.assert_not_called()

    def test_real_descriptor_backlog_obeys_window_without_models(self):
        with tempfile.TemporaryDirectory() as td:
            manager, chunks = make_manager(pathlib.Path(td), 'bounded-backlog')
            manager.session.update(transcription_interval_s=45, live_analysis_enabled=False, final_analysis_enabled=False)
            manager.stop_requested = True
            for index in range(2):
                write_chunk(chunks / f'chunk_{index:06d}.wav')
            calls = []
            def transcribe(*_args):
                calls.append(True)
                return ([{'start': 0, 'end': 1, 'text': 'one'}], 'pt')
            with patch.object(manager, '_load_whisper', return_value={'snapshot': 'fake'}), patch.object(manager, '_transcribe_chunk', side_effect=transcribe):
                worker = threading.Thread(target=manager._processing_loop, daemon=True)
                worker.start()
                try:
                    deadline = time.monotonic() + 2
                    while not calls and time.monotonic() < deadline:
                        time.sleep(.01)
                    time.sleep(.25)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(manager.session['next_chunk'], 1)
                finally:
                    manager._shutdown_requested.set()
                    worker.join(2)
                self.assertFalse(worker.is_alive())

    def test_worker_can_cancel_real_sleeping_child_without_model(self):
        from inference_worker import run_cancellable, WorkerCancelled
        seen = []
        started = time.monotonic()
        with self.assertRaises(WorkerCancelled):
            run_cancellable([sys.executable, '-c', 'import time; time.sleep(30)'],
                            cancelled=lambda: time.monotonic() - started > 0.1,
                            process_changed=lambda proc: seen.append(proc))
        self.assertLess(time.monotonic() - started, 3)
        self.assertIsNotNone(seen[0].poll())
        self.assertIsNone(seen[-1])

    def test_transcription_interval_validation_is_strict(self):
        for value in (True, 9, 3601, 15.5, '45', float('nan')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.normalize_processing_options({'transcription_interval_s': value})
        self.assertEqual(app.normalize_transcription_interval({}), 45)

    def test_analysis_interval_rejects_coercion_and_nonfinite_values(self):
        with tempfile.TemporaryDirectory() as td:
            manager = app.MeetingManager(pathlib.Path(td), ffmpeg='fake')
            for value in (True, '180', 180.5, float('nan'), 59, 601):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    manager._file_settings({'transcription_mode': 'off', 'analysis_interval_s': value})

    def test_final_disk_reserve_preserves_chunks_without_concat(self):
        with tempfile.TemporaryDirectory() as td:
            manager, chunks = make_manager(pathlib.Path(td), 'disk-reserve')
            write_chunk(chunks / 'chunk_000000.wav')
            manager.resource_guard.update(dict(cpu_percent=1, ram_available_mib=9000, memory_pressure='normal', disk_free_mib=1))
            with patch.object(app.subprocess, 'run') as run, self.assertRaises(app.AudioCaptureError):
                manager._finalize_audio()
            run.assert_not_called()
            self.assertTrue((chunks / 'chunk_000000.wav').is_file())

    def test_manual_guard_blocks_forced_live_and_final_analysis(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), 'guard')
            manager.session['segments'] = [{'start': 0, 'end': 1, 'text': 'saved'}]
            manager.set_resource_guard({'recording_only': True})
            with patch.object(app, '_ollama_call') as live, patch.object(app, '_ollama_final_call') as final:
                manager._maybe_analyze(force=True)
                manager._run_final_analysis(manager.session)
                with self.assertRaises(RuntimeError):
                    manager.analyze_now()
            live.assert_not_called()
            final.assert_not_called()
            state = manager.status()['resource_guard']
            self.assertEqual(state['level'], 4)
            self.assertEqual(manager.session['final_analysis_state'], 'deferred')

    def test_pause_preserves_cursor_and_backlog_after_stop(self):
        with tempfile.TemporaryDirectory() as td:
            manager, chunks = make_manager(pathlib.Path(td), 'guard')
            write_chunk(chunks / 'chunk_000000.wav')
            manager.stop_requested = True
            manager.set_resource_guard({'recording_only': True})
            with patch.object(manager, '_load_whisper') as load, patch.object(manager, '_finalize_audio') as finalize:
                manager._processing_loop(manager.session)
            load.assert_not_called()
            finalize.assert_called_once()
            self.assertEqual(manager.session['next_chunk'], 0)
            self.assertEqual(manager.status()['resource_guard']['deferred_chunks'], 1)
            self.assertTrue(manager.session['resource_deferred'])

    def test_alive_recorder_blocks_new_start_even_when_session_error(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), 'guard')
            manager.session['state'] = 'error'
            manager.recorder = Mock()
            manager.recorder.poll.return_value = None
            with patch.object(manager, 'devices') as devices, self.assertRaises(RuntimeError):
                manager.start({})
            devices.assert_not_called()

    def test_inference_error_retains_supervised_parable_capture(self):
        with tempfile.TemporaryDirectory() as td:
            manager, chunks = make_manager(pathlib.Path(td), 'guard')
            manager.session['state'] = 'recording'
            for index in range(2):
                write_chunk(chunks / f'chunk_{index:06d}.wav')
            manager.recorder = Mock()
            manager.recorder.poll.return_value = None
            with patch.object(manager, '_load_whisper', side_effect=RuntimeError('failure')), patch.object(manager, '_persist'), patch.object(manager, '_stop_resource_monitor') as stop_monitor:
                manager._processing_loop(manager.session)
            self.assertEqual(manager.session['state'], 'recording')
            self.assertIn('processing_error', manager.session)
            stop_monitor.assert_not_called()
            self.assertEqual(manager.status()['resource_guard']['capture_alive'], True)
            manager.recorder.poll.return_value = 0


if __name__ == '__main__':
    unittest.main()
