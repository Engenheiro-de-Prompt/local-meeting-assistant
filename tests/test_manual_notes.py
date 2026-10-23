import io
import json
from types import SimpleNamespace
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import live_meeting_app as app


NOTES = {"text": "Privado: não enviar à IA.", "items": [
    {"id": "manual_1-test", "text": "Conferir prazo", "checked": False}]}


class NormalizeManualNotesTests(unittest.TestCase):
    def test_defaults_limits_and_independent_copy(self):
        self.assertEqual(app.normalize_manual_notes({}), {"text": "", "items": []})
        self.assertEqual(app.normalize_manual_notes({"text": "nota"}), {"text": "nota", "items": []})
        original = {"text": "x" * 20000, "items": [
            {"id": str(i) + "a" * 77, "text": "y" * 500, "checked": bool(i % 2)} for i in range(100)]}
        result = app.normalize_manual_notes(original)
        self.assertEqual(result, original)
        result["items"][0]["text"] = "alterado"
        self.assertNotEqual(original["items"][0]["text"], "alterado")

    def test_rejects_malformed_or_unbounded_notes(self):
        bad = [None, [], "text", True, {"text": 1}, {"text": "x" * 20001},
               {"items": {}}, {"items": [None]}, {"unexpected": 1},
               {"items": [{"id": "a", "text": "x", "checked": 1}]},
               {"items": [{"id": "a", "text": "x"}]},
               {"items": [{"id": "a", "text": 1, "checked": False}]},
               {"items": [{"id": "a", "text": "x" * 501, "checked": False}]},
               {"items": [{"id": "a", "text": "x", "checked": False, "extra": 1}]},
               {"items": NOTES["items"] * 2}, {"items": NOTES["items"] * 101}]
        for unsafe in ("", "x" * 81, "../a", "a b", "a/b", "á", "a\n", 1, None):
            bad.append({"items": [{"id": unsafe, "text": "x", "checked": False}]})
        for value in bad:
            with self.subTest(value=str(value)[:100]), self.assertRaises(ValueError):
                app.normalize_manual_notes(value)


def install_session(root, state="completed"):
    manager = app.MeetingManager(root, ffmpeg="fixture-ffmpeg")
    folder = root / "fixture-session"
    folder.mkdir(exist_ok=True)
    audio, transcript = root / "fixture.wav", root / "fixture.txt"
    audio.write_bytes(b"fixture-not-real-audio")
    transcript.write_text("transcript sentinel", encoding="utf-8")
    for name in ("transcript.json", "analysis.json", "final_analysis.json"):
        (folder / name).write_text("artifact sentinel", encoding="utf-8")
    manager.session = {
        "session_id": folder.name, "state": state, "folder": str(folder),
        "audio_file": str(audio), "transcript_file": str(transcript),
        "session_json": str(folder / "session.json"), "started_at": "fixture",
        "started_monotonic": time.monotonic(), "elapsed_s": 0,
        "segments": [{"start": 0, "end": 1, "text": "Spoken transcript only"}],
        "next_chunk": 1, "cards": app.default_boxes(), "analyses": {},
        "whisper_model": "small", "llm_model": "fixture-model", "ollama_port": 11434,
        "analysis_interval_s": 60, "force_analysis": False, "analysis_summary": ""}
    return manager


class ManualNotesPersistenceTests(unittest.TestCase):
    def test_metadata_only_update_restores_notes_and_revision_without_model_triggers(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            manager = install_session(root)
            artifacts = [root / "fixture.txt"] + [root / "fixture-session" / name for name in
                         ("transcript.json", "analysis.json", "final_analysis.json")]
            before = [(p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in artifacts]
            with (patch.object(app, "_ollama_call") as live,
                  patch.object(app, "_ollama_final_call") as final,
                  patch.object(manager, "_persist", side_effect=AssertionError("metadata only"))):
                status = manager.update_manual_notes("fixture-session", 0, NOTES)
            self.assertEqual(status["manual_notes"], NOTES)
            self.assertEqual(status["manual_notes_revision"], 1)
            self.assertFalse(manager.session["force_analysis"])
            live.assert_not_called()
            final.assert_not_called()
            self.assertEqual(before, [(p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino) for p in artifacts])
            metadata = json.loads(pathlib.Path(manager.session["session_json"]).read_text())
            self.assertEqual(metadata["manual_notes"], NOTES)
            self.assertEqual(metadata["manual_notes_revision"], 1)
            restored = app.MeetingManager(root).status()
            self.assertEqual(restored["manual_notes"], NOTES)
            self.assertEqual(restored["manual_notes_revision"], 1)
            status["manual_notes"]["items"].clear()
            self.assertEqual(manager.status()["manual_notes"], NOTES)

    def test_stale_identity_or_revision_and_invalid_payload_never_write(self):
        with tempfile.TemporaryDirectory() as td:
            manager = install_session(pathlib.Path(td))
            manager.update_manual_notes("fixture-session", 0, NOTES)
            path = pathlib.Path(manager.session["session_json"])
            before = path.read_bytes()
            for session_id, revision in (("old-session", 1), ("fixture-session", 0)):
                with self.subTest(session_id=session_id, revision=revision), self.assertRaises(RuntimeError):
                    manager.update_manual_notes(session_id, revision, {})
            for session_id, revision, notes in ((None, 1, {}), ("", 1, {}), (1, 1, {}),
                    ("fixture-session", True, {}), ("fixture-session", 1.0, {}),
                    ("fixture-session", -1, {}), ("fixture-session", "1", {}),
                    ("fixture-session", 1, {"text": None})):
                with self.subTest(revision=revision, notes=notes), self.assertRaises(ValueError):
                    manager.update_manual_notes(session_id, revision, notes)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(manager.status()["manual_notes_revision"], 1)

    def test_all_session_phases_accept_manual_edits_and_idle_refuses(self):
        for state in ("recording", "processing", "final_analysis", "completed", "error"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as td:
                manager = install_session(pathlib.Path(td), state)
                status = manager.update_manual_notes("fixture-session", 0, NOTES)
                self.assertEqual(status["state"], state)
                self.assertEqual(status["manual_notes_revision"], 1)
        with tempfile.TemporaryDirectory() as td:
            manager = app.MeetingManager(pathlib.Path(td))
            self.assertEqual(manager.status()["manual_notes"], {"text": "", "items": []})
            self.assertEqual(manager.status()["manual_notes_revision"], 0)
            with self.assertRaises(RuntimeError):
                manager.update_manual_notes("fixture-session", 0, {})

    def test_legacy_restore_defaults_and_corrupt_notes_are_not_trusted(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            manager = install_session(root)
            path = pathlib.Path(manager.session["session_json"])
            path.write_text(json.dumps(manager._persistable_session()))
            restored = app.MeetingManager(root).status()
            self.assertEqual(restored["manual_notes"], {"text": "", "items": []})
            self.assertEqual(restored["manual_notes_revision"], 0)
            for fields in ({"manual_notes": {"text": 1}}, {"manual_notes_revision": True},
                           {"manual_notes_revision": -1}, {"manual_notes_revision": 1.5}):
                with self.subTest(fields=fields):
                    path.write_text(json.dumps({**manager._persistable_session(), **fields}))
                    self.assertEqual(app.MeetingManager(root).status()["state"], "idle")

    def test_failed_atomic_write_does_not_advance_in_memory_revision(self):
        with tempfile.TemporaryDirectory() as td:
            manager = install_session(pathlib.Path(td))
            with patch.object(app, "atomic_write_json", side_effect=OSError("fixture disk failure")):
                with self.assertRaises(OSError):
                    manager.update_manual_notes("fixture-session", 0, NOTES)
            self.assertEqual(manager.status()["manual_notes_revision"], 0)
            self.assertEqual(manager.status()["manual_notes"], {"text": "", "items": []})


class InitialManualNotesTests(unittest.TestCase):
    def test_invalid_start_and_upload_notes_are_rejected_before_external_checks_or_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td) / "not-created"
            manager = app.MeetingManager(root, ffmpeg="fixture-ffmpeg")
            for method in (manager.start, manager.prepare_file_upload):
                with self.subTest(method=method.__name__), \
                     patch.object(manager, "devices", side_effect=AssertionError("no device queries")), \
                     patch.object(manager, "_file_settings", side_effect=AssertionError("no model queries")), \
                     patch.object(app.subprocess, "Popen", side_effect=AssertionError("no recording")):
                    with self.assertRaises(ValueError):
                        method({"manual_notes": {"text": 123}})
                self.assertFalse(root.exists())
                self.assertIsNone(manager.session)
                self.assertIsNone(manager._upload_config)

    def test_start_persists_initial_notes_or_empty_defaults_at_revision_zero(self):
        for initial in (NOTES, None):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as td:
                manager = app.MeetingManager(pathlib.Path(td), ffmpeg="fixture-ffmpeg")
                request = {"transcription_mode": "off", "live_analysis_enabled": False,
                           "final_analysis_enabled": False}
                if initial is not None:
                    request["manual_notes"] = initial
                devices = {"microphones": [{"id": "default"}], "monitors": [{"id": "@DEFAULT_MONITOR@"}]}
                with (patch.object(manager, "devices", return_value=devices),
                      patch.object(manager, "_start_resource_monitor"),
                      patch.object(app.threading.Thread, "start"),
                      patch.object(app.time, "sleep"),
                      patch.object(app.subprocess, "Popen") as popen,
                      patch.object(app, "_local_ollama_models", side_effect=AssertionError("no LLM"))):
                    popen.return_value.poll.return_value = None
                    status = manager.start(request)
                expected = initial if initial is not None else {"text": "", "items": []}
                self.assertEqual(status["manual_notes"], expected)
                self.assertEqual(status["manual_notes_revision"], 0)
                metadata = json.loads(pathlib.Path(manager.session["session_json"]).read_text())
                self.assertEqual(metadata["manual_notes"], expected)
                self.assertEqual(metadata["manual_notes_revision"], 0)

    def test_upload_token_configuration_inherits_independent_initial_notes(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            manager = app.MeetingManager(root, ffmpeg="fixture-ffmpeg")
            initial = json.loads(json.dumps(NOTES))
            config = manager.prepare_file_upload({"manual_notes": initial, "transcription_mode": "off",
                "live_analysis_enabled": False, "final_analysis_enabled": False})
            initial["text"] = "changed draft"
            initial["items"].clear()
            source = root / "uploaded.wav"
            source.write_bytes(b"fixture-not-real-audio")
            def decode(command, **_kwargs):
                pathlib.Path(command[-1].replace("%06d", "000000")).write_bytes(b"x" * 64)
                return app.subprocess.CompletedProcess(command, 0)
            with (patch.object(app.subprocess, "run", side_effect=decode),
                  patch.object(app.threading.Thread, "start"),
                  patch.object(manager, "_start_resource_monitor")):
                status = manager.start_uploaded_file(config["token"], source, "uploaded.wav")
            self.assertEqual(status["manual_notes"], NOTES)
            self.assertEqual(status["manual_notes_revision"], 0)
            metadata = json.loads(pathlib.Path(manager.session["session_json"]).read_text())
            self.assertEqual(metadata["manual_notes"], NOTES)
            self.assertEqual(metadata["manual_notes_revision"], 0)


def http_request(manager, route, payload, origin="http://127.0.0.1:8765"):
    """Exercise the real HTTP parser and handler entirely in memory (no sockets)."""
    body = json.dumps(payload).encode("utf-8")
    raw = (f"POST {route} HTTP/1.0\r\nHost: 127.0.0.1:8765\r\n"
           f"Origin: {origin}\r\nContent-Type: application/json\r\n"
           f"Content-Length: {len(body)}\r\n\r\n").encode() + body
    handler_class = app.make_handler(manager)
    handler = handler_class.__new__(handler_class)
    handler.server = SimpleNamespace(server_port=8765)
    handler.client_address = ("127.0.0.1", 12345)
    handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
    handler.handle_one_request()
    head, response = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
    return int(head.split(b" ")[1]), response


class ManualNotesHTTPTests(unittest.TestCase):
    def test_route_returns_status_and_maps_invalid_or_stale_requests(self):
        with tempfile.TemporaryDirectory() as td:
            manager = install_session(pathlib.Path(td))
            payload = {"session_id": "fixture-session", "expected_revision": 0, "notes": NOTES}
            code, body = http_request(manager, "/api/manual-notes", payload)
            self.assertEqual(code, 200, body)
            self.assertEqual(json.loads(body)["manual_notes"], NOTES)
            self.assertEqual(json.loads(body)["manual_notes_revision"], 1)
            self.assertEqual(http_request(manager, "/api/manual-notes", payload)[0], 409)
            self.assertEqual(http_request(manager, "/api/manual-notes", {**payload,
                "session_id": "replaced-session", "expected_revision": 1})[0], 409)
            invalid = [{}, {**payload, "extra": 1}, {**payload, "expected_revision": True},
                       {**payload, "notes": None}, {**payload, "session_id": None}]
            for value in invalid:
                with self.subTest(value=value):
                    self.assertEqual(http_request(manager, "/api/manual-notes", value)[0], 400)
            self.assertEqual(manager.status()["manual_notes_revision"], 1)

    def test_route_requires_same_origin_and_initial_routes_validate_notes(self):
        with tempfile.TemporaryDirectory() as td:
            manager = install_session(pathlib.Path(td))
            payload = {"session_id": "fixture-session", "expected_revision": 0, "notes": NOTES}
            self.assertEqual(http_request(manager, "/api/manual-notes", payload, "http://evil.example")[0], 403)
            self.assertEqual(manager.status()["manual_notes_revision"], 0)
            for route in ("/api/start", "/api/upload-config"):
                with self.subTest(route=route):
                    self.assertEqual(http_request(manager, route, {"manual_notes": []})[0], 400)

    def test_manual_content_never_enters_live_or_final_model_inputs(self):
        with tempfile.TemporaryDirectory() as td:
            manager = install_session(pathlib.Path(td))
            manager.update_manual_notes("fixture-session", 0, NOTES)
            seen = []
            def live(*args):
                seen.append(args)
                return "summary", {card["id"]: "result" for card in args[3]}
            def final(*args):
                seen.append(args)
                return {"summary": "summary", "tasks": [], "results": {}}
            with (patch.object(app, "_ollama_call", side_effect=live),
                  patch.object(app, "_ollama_final_call", side_effect=final)):
                manager._maybe_analyze(force=True)
                manager._run_final_analysis(manager.session)
            self.assertGreaterEqual(len(seen), 2)
            for inputs in seen:
                self.assertEqual(inputs[2], "Spoken transcript only")
                self.assertNotIn(NOTES["text"], json.dumps(inputs, ensure_ascii=False))
                self.assertNotIn(NOTES["items"][0]["text"], json.dumps(inputs, ensure_ascii=False))
            self.assertEqual(manager.status()["manual_notes"], NOTES)
            self.assertEqual(manager.status()["manual_notes_revision"], 1)


if __name__ == "__main__":
    unittest.main()
