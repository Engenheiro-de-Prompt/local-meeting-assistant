"""Headless native monitor integration tests; never contact the meeting service.
Build once with native-monitor/build.sh. No AppKit application is launched.
"""
import json
import os
import pathlib
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
BINARY = ROOT / 'native-monitor/build/Meeting Resource Monitor.app/Contents/MacOS/MeetingResourceMonitor'


@unittest.skipUnless(BINARY.is_file(), 'Build native-monitor/build.sh first (macOS Swift required)')
class NativeMonitorTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.payload = {'state': 'recording', 'processing_progress': {'recorder_alive': True},
                        'resource_guard': {'level': 4, 'name': 'recording_only', 'reason': 'memory pressure',
                                           'effective_analysis_interval_s': 600,
                                           'effective_whisper_interval_s': 120},
                        'resource_usage': {'ram_used_peak_percent': 78.5}}
        self.code = 200
        self.redirect = False
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass
            def do_GET(self):
                owner.requests.append(('GET', self.path))
                self.send_response(302 if owner.redirect else owner.code)
                if owner.redirect:
                    self.send_header('Location', '/redirected')
                self.end_headers()
                self.wfile.write(json.dumps(owner.payload).encode())
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                owner.requests.append(('POST', self.path, self.headers.get('Origin'), json.loads(body)))
                self.send_response(owner.code)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def run_monitor(self, *args):
        return subprocess.run([str(BINARY), '--self-test', '--base-url', self.url, *args],
                              capture_output=True, text=True, timeout=12)

    def test_status_rendering_and_read_only_default(self):
        result = self.run_monitor()
        self.assertEqual(result.returncode, 0, result.stderr)
        for value in ('recording', 'memory pressure', '600', '120', '78.5', 'peak', 'recorder_alive'):
            self.assertIn(value, result.stdout)
        self.assertEqual(self.requests, [('GET', '/api/status')])

    def test_missing_telemetry_is_unknown_not_zero(self):
        self.payload = {'state': 'idle', 'resource_usage': {}}
        result = self.run_monitor()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('unavailable', result.stdout)
        self.assertNotIn('0%', result.stdout)

    def test_latest_guard_fields_null_intervals_and_private_content(self):
        self.payload = {'state': 'recording', 'transcription_interval_s': 73,
                        'transcript': 'PRIVATE_TRANSCRIPT', 'manual_notes': {'text': 'PRIVATE_NOTE'},
                        'resource_guard': {'level': 3, 'level_name': 'transcription_deferred',
                                           'reason': 'pressure', 'capture_alive': True,
                                           'effective_analysis_interval_s': None,
                                           'effective_transcription_interval_s': 146,
                                           'telemetry': {'memory_pressure': 'warning'},
                                           'llm_release_state': 'unverified', 'deferred_chunks': 12}}
        result = self.run_monitor()
        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in ('transcription_deferred', 'Capture alive (backend)',
                         'effective_analysis_interval_s: unavailable',
                         'effective_transcription_interval_s: 146', 'unverified',
                         'telemetry.memory_pressure: warning', 'transcription_interval_s: 73 s'):
            self.assertIn(expected, result.stdout)
        self.assertNotIn('PRIVATE_', result.stdout)
        self.assertNotIn('Capture health: unavailable', result.stdout)

    def test_missing_state_is_invalid(self):
        self.payload = {'resource_guard': {'level': 0}}
        result = self.run_monitor()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('state', result.stderr)

    def test_exact_recording_only_contract_and_status_readback(self):
        result = self.run_monitor('--recording-only')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.requests, [('POST', '/api/resource-guard', 'http://127.0.0.1:18766',
                                          {'recording_only': True}), ('GET', '/api/status')])

    def test_http_error_is_not_healthy(self):
        self.code = 503
        result = self.run_monitor()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('503', result.stderr)

    def test_malformed_schema_is_not_healthy(self):
        self.payload = ['not a status object']
        result = self.run_monitor()
        self.assertNotEqual(result.returncode, 0)

    def test_redirect_is_not_followed(self):
        self.redirect = True
        result = self.run_monitor()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.requests, [('GET', '/api/status')])

    def test_remote_url_rejected_before_network(self):
        result = subprocess.run([str(BINARY), '--self-test', '--base-url', 'https://example.com'],
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('127.0.0.1', result.stderr)

    def test_self_test_refuses_live_mutation(self):
        result = subprocess.run([str(BINARY), '--self-test', '--recording-only'],
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('fixture', result.stderr)


if __name__ == '__main__':
    unittest.main()
