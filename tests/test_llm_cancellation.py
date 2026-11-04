"""Synthetic loopback transport tests: never contact Ollama or load models."""
import contextlib
import http.server
import json
import pathlib
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import live_meeting_app as app


@contextlib.contextmanager
def slow_server(partial_body=False):
    entered, disconnected = threading.Event(), threading.Event()
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            requests.append(self.path)
            if partial_body:
                # HTTP/1.0 deliberately detaches HTTPConnection.sock in getresponse.
                self.send_response(200)
                self.send_header('Content-Length', '100000')
                self.end_headers()
                self.wfile.write(b'{')
                self.wfile.flush()
            entered.set()
            self.connection.settimeout(3)
            try:
                if self.connection.recv(1) == b'':
                    disconnected.set()
            except ConnectionResetError:
                disconnected.set()
            except socket.timeout:
                pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, entered, disconnected, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


class LLMCancellationTests(unittest.TestCase):
    def exercise_cancellation(self, mode, call_kind, partial_body):
        with tempfile.TemporaryDirectory() as td, slow_server(partial_body) as (port, entered, disconnected, requests):
            manager = app.MeetingManager(pathlib.Path(td), ffmpeg='unused')
            errors = []
            def worker():
                try:
                    with manager._inference('llm'):
                        if call_kind == 'json':
                            app._ollama_json_call(port, 'synthetic', 'system', 'user', {})
                        else:
                            app._ollama_call(port, 'synthetic', 'test transcript', app.default_boxes())
                except Exception as exc:
                    errors.append(exc)

            budget = .25 if mode == 'deadline' else 10
            with patch.object(app, 'LLM_ANALYSIS_DEADLINE_SECONDS', budget), patch.object(app.subprocess, 'run') as run, patch.object(app.os, 'kill') as kill:
                thread = threading.Thread(target=worker, daemon=True)
                thread.start()
                self.assertTrue(entered.wait(2), 'synthetic request never arrived')
                started = time.monotonic()
                if mode == 'guard':
                    manager.set_resource_guard({'recording_only': True})
                elif mode == 'shutdown':
                    manager._shutdown_requested.set()
                elif mode == 'pressure':
                    manager.resource_guard.enabled = True
                    manager.resource_guard.update(dict(cpu_percent=1, ram_available_mib=1,
                                                       memory_pressure='critical', disk_free_mib=9000))
                thread.join(2)
                self.assertFalse(thread.is_alive(), 'HTTP request remained blocked')
                self.assertLess(time.monotonic() - started, 2)
                self.assertTrue(disconnected.wait(1), 'server never observed socket disconnect')
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], app.InferenceDeferred)
                self.assertEqual(manager._llm_release_state, 'server_release_unverified')
                self.assertFalse(manager.resource_guard.allowed('whisper'))
                manager._shutdown_requested.clear()
                manager.set_resource_guard({'recording_only': False})
                with self.assertRaises(app.InferenceDeferred):
                    with manager._inference('llm'):
                        self.fail('unverified server must block all later heavy work')
                self.assertEqual(requests, ['/api/chat'])
                run.assert_not_called()
                kill.assert_not_called()
                self.assertFalse(any(t.name == 'OllamaHTTPCancellation' and t.is_alive()
                                     for t in threading.enumerate()))

    def test_real_socket_guard_and_deadline_interrupt_both_transports(self):
        for mode in ('guard', 'deadline', 'shutdown', 'pressure'):
            for kind in ('json', 'live'):
                for partial in (False, True):
                    with self.subTest(mode=mode, kind=kind, partial_body=partial):
                        self.exercise_cancellation(mode, kind, partial)

    def test_budget_is_shared_across_compaction_and_retries(self):
        with tempfile.TemporaryDirectory() as td:
            manager = app.MeetingManager(pathlib.Path(td), ffmpeg='unused')
            deadlines = []
            clock = [100.0]
            def json_call(*args, **kwargs):
                app._before_llm_request()
                deadlines.append(app._INFERENCE_CONTEXT.deadline)
                clock[0] += 1
                raise ValueError('synthetic invalid response')
            with patch.object(app, 'LLM_ANALYSIS_DEADLINE_SECONDS', 1.5), patch.object(app.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(app, '_ollama_json_call', side_effect=json_call):
                with self.assertRaises((ValueError, app.InferenceDeferred)):
                    with manager._inference('llm'):
                        app._ollama_final_call(1, 'synthetic', 'transcript', app.default_final_config())
            self.assertEqual(len(deadlines), 2)
            self.assertEqual(deadlines[0], deadlines[1])
            self.assertIsNone(getattr(app._INFERENCE_CONTEXT, 'deadline', None))

    def test_compaction_chunks_share_budget_and_stop_before_next_http(self):
        with tempfile.TemporaryDirectory() as td:
            manager = app.MeetingManager(pathlib.Path(td), ffmpeg='unused')
            clock, deadlines = [100.0], []
            def json_call(*args, **kwargs):
                app._before_llm_request()
                deadlines.append(app._INFERENCE_CONTEXT.deadline)
                clock[0] += 1
                return {'notes': 'Synthetic cumulative notes'}
            with patch.object(app, 'LLM_ANALYSIS_DEADLINE_SECONDS', 2.5), patch.object(app.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(app, '_ollama_json_call', side_effect=json_call):
                with self.assertRaises(app.InferenceDeferred):
                    with manager._inference('llm'):
                        app._ollama_final_call(1, 'synthetic', 'x' * 150000, app.default_final_config())
            self.assertEqual(deadlines, [102.5] * 3)
            self.assertEqual(manager._llm_release_state, 'server_release_unverified')
            self.assertFalse(manager.resource_guard.allowed('whisper'))

    def test_live_retry_budget_is_not_reset_by_inference_gate(self):
        from test_processing import make_manager
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), 'shared-live-budget')
            assert manager.session is not None
            manager.session['segments'] = [{'start': 0, 'end': 1, 'text': 'Synthetic meeting'}]
            clock, deadlines = [100.0], []
            def live_call(*args, **kwargs):
                app._before_llm_request()
                deadlines.append(app._INFERENCE_CONTEXT.deadline)
                clock[0] += 2
                raise ValueError('synthetic invalid response')
            with patch.object(app, 'LLM_ANALYSIS_DEADLINE_SECONDS', 1.5), patch.object(app.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(app, '_ollama_call', side_effect=live_call):
                manager._maybe_analyze(force=True)
            # The retry's inference gate sees the original expired budget. It
            # must not grant a fresh allowance or start a second HTTP request.
            self.assertEqual(deadlines, [101.5])
            self.assertEqual(manager.session['analyses'], {})
            self.assertIsNone(getattr(app._INFERENCE_CONTEXT, 'deadline', None))

    def test_both_payloads_explicitly_reserve_gpu_and_limit_cpu_threads(self):
        for kind in ('json', 'live'):
            with self.subTest(kind=kind):
                boxes = app.default_boxes()[:1]
                result = {'notes': 'Synthetic notes'} if kind == 'json' else {
                    'summary': 'Synthetic summary',
                    'results': [{'id': boxes[0]['id'], 'text': 'Synthetic result'}]}
                connection = Mock()
                connection.getresponse.return_value.status = 200
                connection.getresponse.return_value.read.return_value = json.dumps(
                    {'message': {'content': json.dumps(result)}}).encode()
                with patch.object(app.http.client, 'HTTPConnection', return_value=connection):
                    if kind == 'json':
                        app._ollama_json_call(1, 'synthetic', '', '', {})
                    else:
                        app._ollama_call(1, 'synthetic', 'Synthetic transcript', boxes)
                payload = json.loads(connection.request.call_args.kwargs['body'])
                self.assertEqual(payload['options']['num_gpu'], 0)
                self.assertEqual(payload['options']['num_thread'], 2)
                self.assertIs(payload['think'], False)

    def test_preflight_guard_never_opens_connection_or_claims_release(self):
        with tempfile.TemporaryDirectory() as td:
            manager = app.MeetingManager(pathlib.Path(td), ffmpeg='unused')
            manager.set_resource_guard({'recording_only': True})
            with patch.object(app.http.client, 'HTTPConnection') as connection:
                with self.assertRaises(app.InferenceDeferred):
                    with manager._inference('llm'):
                        app._ollama_json_call(1, 'synthetic', '', '', {})
                connection.assert_not_called()
            self.assertEqual(manager._llm_release_state, 'not_requested')


if __name__ == '__main__':
    unittest.main()
