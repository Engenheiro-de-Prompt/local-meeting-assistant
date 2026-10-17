import http.client
import json
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import live_meeting_app as app


class LocalServerSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = app.MeetingManager(pathlib.Path(self.tmp.name), ffmpeg="/usr/bin/ffmpeg")
        self.server = app.LoopbackServer(("127.0.0.1", 0), app.make_handler(self.manager), self.manager)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        result = response.status, dict(response.getheaders()), data
        conn.close()
        return result

    def test_ui_and_json_api_are_served_with_security_headers(self):
        status, headers, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Transcri", body)
        policy = headers.get("Content-Security-Policy", "")
        self.assertIn("connect-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        status, _, body = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(app.strict_json_loads(body.decode())["state"], "idle")

    def test_start_reports_missing_local_whisper_cache_with_an_actionable_error(self):
        headers = {"Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        payload = {"capture_mode": "mic_only", "mic_device": "default", "pc_device": "@DEFAULT_MONITOR@",
                   "whisper_model": "small", "whisper_device": "cuda", "ollama_port": 11435,
                   "llm_model": "gemma4:e4b", "analysis_interval_s": 60, "boxes": app.default_boxes()}
        devices = {"microphones": [{"id": "default"}], "monitors": [{"id": "@DEFAULT_MONITOR@"}]}
        with (patch.object(self.manager, "devices", return_value=devices),
              patch.object(app, "find_whisper_snapshot", side_effect=FileNotFoundError("missing fixture model")),
              patch.object(app, "_local_ollama_models", return_value=["gemma4:e4b"])):
            status, _, body = self.request("POST", "/api/start", headers, json.dumps(payload).encode())
        self.assertEqual(status, 409)
        self.assertIn(b"HF_HOME", body)
        self.assertIsNone(self.manager.session)

    def test_host_header_and_post_origin_are_pinned_to_local_origin(self):
        status, _, _ = self.request("GET", "/api/status", {"Host": "evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/analyze", {"Host": f"127.0.0.1:{self.port}", "Content-Type": "application/json"}, b"{}")
        self.assertEqual(status, 403)

    def test_quit_is_refused_while_recording_then_stops_the_local_server(self):
        self.manager.session = {"state": "recording"}
        headers = {"Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        status, _, _ = self.request("POST", "/api/quit", headers, b"{}")
        self.assertEqual(status, 409)
        self.assertTrue(self.thread.is_alive())
        self.manager.session = None
        status, _, _ = self.request("POST", "/api/quit", headers, b"{}")
        self.assertEqual(status, 202)
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())

    def test_post_requires_same_origin_and_rejects_unknown_route(self):
        headers = {"Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        status, _, _ = self.request("POST", "/api/analyze", headers, b"{}")
        self.assertEqual(status, 409)
        status, _, _ = self.request("POST", "/api/not-a-route", headers, b"{}")
        self.assertEqual(status, 404)

    def _install_finished_session(self):
        root = pathlib.Path(self.tmp.name)
        folder = root / "session-test"
        folder.mkdir()
        audio = root / "session-test.wav"
        audio.write_bytes(b"RIFF0123456789")
        transcript = root / "session-test.txt"
        transcript.write_text("Private local transcript\n", encoding="utf-8")
        analysis = folder / "analysis.json"
        analysis.write_text('{"questions":{"text":"Question"}}', encoding="utf-8")
        self.manager.session = {
            "session_id": "session-test", "state": "completed", "folder": str(folder),
            "audio_file": str(audio), "transcript_file": str(transcript),
            "session_json": str(folder / "session.json"), "transcript_json": str(folder / "transcript.json"),
            "started_at": "test", "started_monotonic": 1.0, "elapsed_s": 1,
            "segments": [{"start": 0, "end": 1, "text": "Private local transcript"}], "next_chunk": 1,
            "cards": app.default_boxes(), "analyses": {"questions": {"text": "Question"}},
            "capture_mode": "fixture", "whisper_model": "small", "whisper_device": "cuda",
            "llm_model": "gemma4:e4b", "ollama_port": 11435, "analysis_interval_s": 180,
        }
        return audio, transcript, analysis

    def test_combined_report_download_places_final_results_before_full_transcript(self):
        self._install_finished_session()
        self.manager.session.update({
            "final_analysis_state": "completed",
            "final_analysis_config": app.default_final_config(),
            "final_analysis": {"summary": "Final summary", "tasks": [], "results": {}, "updated_at": "test"},
        })
        status, headers, body = self.request("GET", "/api/download/report")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        report = body.decode("utf-8")
        self.assertLess(report.index("Final summary"), report.index("TRANSCRIÇÃO COMPLETA"))
        self.assertIn("Private local transcript", report)

    def test_combined_report_download_is_unavailable_until_final_analysis_completes(self):
        self._install_finished_session()
        status, _, body = self.request("GET", "/api/download/report")
        self.assertEqual(status, 409)
        self.assertIn("análises finais", body.decode("utf-8"))

    def test_http_start_stop_endpoint_runs_synthetic_capture_and_finalizes_analysis(self):
        self._install_finished_session()
        self.manager._start_resource_monitor = lambda *_args: None
        self.manager._load_whisper = lambda *_args: object()
        self.manager._transcribe_chunk = lambda _model, _path, index, _language: ([{"start": 0, "end": 0.5, "text": f"synthetic spoken segment {index}"}], "pt")
        def synthetic_ffmpeg(binary, _mode, _mic, _pc, _mic_volume, _pc_volume, output, chunk_seconds):
            return [binary, "-y", "-nostats", "-loglevel", "error", "-re", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=16000", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                    "-f", "segment", "-segment_time", str(chunk_seconds), "-reset_timestamps", "1",
                    "-segment_format", "wav", output]
        def live_result(_port, _model, _text, boxes, _summary, _context_compaction=None):
            return "Synthetic rolling summary", {box["id"]: "Synthetic live result" for box in boxes}
        final_result = {"summary": "Synthetic final summary", "tasks": [], "results": {}, "updated_at": "test"}
        headers = {"Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        payload = {"capture_mode": "mic_only", "mic_device": "default", "pc_device": "@DEFAULT_MONITOR@",
                   "whisper_model": "small", "whisper_device": "cpu", "ollama_port": 11435,
                   "llm_model": "gemma4:e4b", "analysis_interval_s": 60, "boxes": app.default_boxes(),
                   "final_analysis_config": app.default_final_config()}
        with (patch.object(app, "DEFAULT_CHUNK_SECONDS", 1),
              patch.object(self.manager, "devices", return_value={"microphones": [{"id": "default"}], "monitors": [{"id": "@DEFAULT_MONITOR@"}]}),
              patch.object(app, "find_whisper_snapshot", return_value=pathlib.Path(self.tmp.name)),
              patch.object(app, "_local_ollama_models", return_value=["gemma4:e4b"]),
              patch.object(app, "build_ffmpeg_command", side_effect=synthetic_ffmpeg),
              patch.object(app, "_ollama_call", side_effect=live_result),
              patch.object(app, "_ollama_final_call", return_value=final_result)):
            status, _, body = self.request("POST", "/api/start", headers, json.dumps(payload).encode())
            self.assertEqual(status, 200, body.decode("utf-8"))
            start_status = app.strict_json_loads(body.decode())
            self.assertEqual(start_status["state"], "recording")
            self.assertNotEqual(start_status["session_id"], "session-test")
            self.manager.session["analysis_interval_s"] = 1
            deadline = time.monotonic() + 15
            while not self.manager.session["analyses"] and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertTrue(self.manager.session["analyses"], "live analysis should occur before stop")
            status, _, body = self.request("POST", "/api/stop", headers, b"{}")
            self.assertEqual(status, 200, body.decode("utf-8"))
            deadline = time.monotonic() + 20
            while self.manager.status()["state"] != "completed" and time.monotonic() < deadline:
                time.sleep(0.1)
            final_status = self.manager.status()
            self.assertEqual(final_status["state"], "completed")
            self.assertEqual(final_status["final_analysis_state"], "completed")
            self.assertTrue(pathlib.Path(final_status["audio_file"]).is_file())
            self.assertTrue(pathlib.Path(final_status["transcript_file"]).is_file())
            report_status, _, report = self.request("GET", "/api/download/report")
            self.assertEqual(report_status, 200)
            self.assertLess(report.index(b"Synthetic final summary"), report.index(b"TRANSCRI\xc3\x87\xc3\x83O COMPLETA"))
        self.manager.shutdown()

    def test_download_routes_serve_only_current_session_artifacts(self):
        audio, transcript, analysis = self._install_finished_session()
        got, headers, body = self.request("GET", "/api/download/transcript")
        self.assertEqual(got, 200)
        self.assertEqual(body, transcript.read_bytes())
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        got, headers, body = self.request("GET", "/api/download/analysis")
        self.assertEqual(got, 200)
        self.assertEqual(app.strict_json_loads(body.decode()), {"questions": {"text": "Question"}})
        got, headers, body = self.request("GET", "/api/current-audio", {"Range": "bytes=0-3"})
        self.assertEqual(got, 206)
        self.assertEqual(body, audio.read_bytes()[:4])
        self.assertIn("bytes 0-3/", headers.get("Content-Range", ""))

    def test_open_routes_use_only_paths_from_the_current_session(self):
        _, transcript, _ = self._install_finished_session()
        headers = {"Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        with patch.object(app.shutil, "which", return_value="/usr/bin/xdg-open"), patch.object(app.subprocess, "Popen") as popen:
            status, _, _ = self.request("POST", "/api/open/transcript", headers, b"{}")
            self.assertEqual(status, 202)
            self.assertEqual(popen.call_args.args[0], ["/usr/bin/xdg-open", str(transcript)])
            status, _, _ = self.request("POST", "/api/open/folder", headers, b"{}")
            self.assertEqual(status, 202)
            self.assertEqual(popen.call_args.args[0], ["/usr/bin/xdg-open", self.tmp.name])

    def test_import_upload_is_local_tokenized_and_streamed_to_a_temporary_file(self):
        headers = {"Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}", "Content-Type": "application/json"}
        with patch.object(self.manager, "_file_settings", return_value={"whisper_model": "small"}):
            status, _, body = self.request("POST", "/api/upload-config", headers, b"{}")
        self.assertEqual(status, 201)
        token = app.strict_json_loads(body.decode())["token"]
        self.assertGreaterEqual(len(token), 40)
        raw = b"small-local-audio-fixture"
        upload_headers = {"Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}",
                          "Content-Type": "application/octet-stream", "X-Upload-Token": token, "X-Filename": "sample.wav"}
        seen = {}
        def accept_upload(given_token, path, filename):
            seen.update(token=given_token, filename=filename, body=path.read_bytes(), temp_path=path)
            return {"state": "processing", "session_id": "fixture"}
        with patch.object(self.manager, "start_uploaded_file", side_effect=accept_upload):
            status, _, body = self.request("POST", "/api/transcribe-file", upload_headers, raw)
        self.assertEqual(status, 202)
        self.assertEqual(app.strict_json_loads(body.decode())["state"], "processing")
        self.assertEqual(seen["token"], token)
        self.assertEqual(seen["filename"], "sample.wav")
        self.assertEqual(seen["body"], raw)
        self.assertFalse(seen["temp_path"].exists())

    def test_upload_size_limit_is_enforced_before_reading_body(self):
        headers = {"Host": f"127.0.0.1:{self.port}", "Origin": f"http://127.0.0.1:{self.port}",
                   "Content-Type": "application/octet-stream", "Content-Length": str(app.MAX_AUDIO_UPLOAD_BYTES + 1)}
        status, _, body = self.request("POST", "/api/transcribe-file", headers)
        self.assertEqual(status, 400)
        self.assertIn(b"250 MiB", body)


if __name__ == "__main__":
    unittest.main()
