import json
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
import wave
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import live_meeting_app as app


def make_manager(root, session_id):
    chunks = root / ".chunks"
    chunks.mkdir(exist_ok=True)
    manager = app.MeetingManager(root, ffmpeg=shutil.which("ffmpeg"))
    manager.session = {"session_id": session_id, "state": "processing", "folder": str(root),
        "chunks_dir": str(chunks), "audio_file": str(root / "final.wav"),
        "transcript_file": str(root / "transcript.txt"), "session_json": str(root / "session.json"),
        "transcript_json": str(root / "transcript.json"), "started_at": "test",
        "started_monotonic": time.monotonic(), "elapsed_s": 0, "segments": [], "next_chunk": 0,
        "cards": app.default_boxes(), "analyses": {}, "capture_mode": "fixture",
        "whisper_model": "small", "whisper_device": "cuda", "llm_model": "gemma-3-4b:latest",
        "ollama_port": 11434, "analysis_interval_s": 180, "last_analysis_elapsed": 0,
        "chunk_seconds": 45, "error": ""}
    return manager, chunks


def write_chunk(path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * 1600)


class ProcessingLoopTests(unittest.TestCase):
    def test_live_analysis_off_never_calls_ollama(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), "test-live-analysis-off")
            manager.session.update({"live_analysis_enabled": False,
                                    "segments": [{"start": 0, "end": 1, "text": "Keep this transcript."}]})
            with patch.object(app, "_ollama_call") as ollama:
                manager._maybe_analyze(force=True)
            ollama.assert_not_called()
            self.assertEqual(manager.session["analyses"], {})
            self.assertEqual(manager.session["segments"][0]["text"], "Keep this transcript.")

    def test_after_meeting_mode_defers_whisper_until_capture_stops_then_skips_disabled_final_llm(self):
        with tempfile.TemporaryDirectory() as td:
            manager, chunks = make_manager(pathlib.Path(td), "test-delayed-transcription")
            manager.session.update({"transcription_mode": "after_meeting", "live_analysis_enabled": False,
                                    "final_analysis_enabled": False})
            write_chunk(chunks / "chunk_000000.wav")

            class Recorder:
                alive = True
                def poll(self):
                    return None if self.alive else 0

            recorder = Recorder()
            manager.recorder = recorder
            manager.stop_requested = False
            calls = []
            manager._load_whisper = lambda *_args: object()
            manager._transcribe_chunk = lambda *_args: (calls.append("transcribed") or
                ([{"start": 0, "end": 1, "text": "Delayed transcript."}], "pt"))
            manager._finalize_audio = lambda: None
            worker = threading.Thread(target=manager._processing_loop, args=(manager.session,), daemon=True)
            worker.start()
            try:
                time.sleep(0.15)
                self.assertEqual(calls, [], "Whisper must stay unloaded while the meeting is being recorded")
            finally:
                recorder.alive = False
                with manager.lock:
                    manager.stop_requested = True
                    manager.session["state"] = "processing"
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(calls, ["transcribed"])
            self.assertEqual(manager.session["segments"][0]["text"], "Delayed transcript.")
            self.assertEqual(manager.session["state"], "completed")
            self.assertEqual(manager.session["final_analysis_state"], "skipped")

    def test_delayed_transcription_runs_only_final_llm_after_all_transcript_is_ready(self):
        with tempfile.TemporaryDirectory() as td:
            manager, chunks = make_manager(pathlib.Path(td), "test-delayed-final-analysis")
            manager.session.update({"transcription_mode": "after_meeting", "live_analysis_enabled": False,
                                    "final_analysis_enabled": True})
            manager.stop_requested = True
            write_chunk(chunks / "chunk_000000.wav")
            manager._load_whisper = lambda *_args: object()
            manager._transcribe_chunk = lambda *_args: ([{"start": 0, "end": 1, "text": "Full deferred transcript."}], "pt")
            manager._finalize_audio = lambda: None
            final_inputs = []
            result = {"summary": "Processed after transcription.", "tasks": [], "results": {}, "updated_at": "test"}
            with (patch.object(app, "_ollama_call") as live_llm,
                  patch.object(app, "_ollama_final_call", side_effect=lambda _port, _model, transcript, *_args: final_inputs.append(transcript) or result)):
                manager._processing_loop(manager.session)
            live_llm.assert_not_called()
            self.assertEqual(final_inputs, ["Full deferred transcript."])
            self.assertEqual(manager.session["final_analysis_state"], "completed")
            self.assertEqual(manager.session["final_analysis"]["summary"], "Processed after transcription.")

    def test_final_analysis_off_skips_ollama_but_completes_the_session(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), "test-final-analysis-off")
            manager.session.update({"final_analysis_enabled": False,
                                    "segments": [{"start": 0, "end": 1, "text": "Saved transcript."}],
                                    "final_analysis_state": "running", "state": "final_analysis"})
            with patch.object(app, "_ollama_final_call") as ollama:
                manager._run_final_analysis(manager.session)
            ollama.assert_not_called()
            self.assertEqual(manager.session["final_analysis_state"], "skipped")
            self.assertEqual(manager.session["state"], "completed")

    def test_recording_only_skips_whisper_and_llm_but_keeps_final_audio(self):
        with tempfile.TemporaryDirectory() as td:
            manager, chunks = make_manager(pathlib.Path(td), "test-record-only")
            manager.session.update({"transcription_mode": "off", "live_analysis_enabled": False,
                                    "final_analysis_enabled": False})
            manager.stop_requested = True
            write_chunk(chunks / "chunk_000000.wav")
            with (patch.object(manager, "_load_whisper", side_effect=AssertionError("Whisper should be skipped")),
                  patch.object(app, "_ollama_call") as live_llm,
                  patch.object(app, "_ollama_final_call") as final_llm):
                manager._processing_loop(manager.session)
            live_llm.assert_not_called()
            final_llm.assert_not_called()
            self.assertTrue(pathlib.Path(manager.session["audio_file"]).is_file())
            self.assertEqual(manager.session["state"], "completed")
            self.assertEqual(manager.session["segments"], [])
            self.assertEqual(manager.session["final_analysis_state"], "skipped")

    def test_updated_live_cards_are_used_on_the_next_request_and_removed_results_disappear(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), "test-live-card-update")
            manager.session["segments"].append({"start": 0, "end": 4, "text": "The next deadline is Friday."})
            calls = []
            def fake_ollama(_port, _model, _transcript, boxes, _summary, _context_compaction=None):
                calls.append([dict(box) for box in boxes])
                return "Updated context.", {box["id"]: "Updated result." for box in boxes}
            manager.session["cards"] = [dict(box) for box in app.default_boxes()]
            with patch.object(app, "_ollama_call", side_effect=fake_ollama):
                manager._maybe_analyze(force=True)
                current = [{"id": "custom_now", "title": "Risks", "prompt": "Find unresolved risks.",
                            "notes": "Pay special attention to the Friday deadline.", "enabled": True}]
                manager.update_live_boxes(current)
                self.assertTrue(manager.session["force_analysis"])
                manager._maybe_analyze(force=False)
            self.assertEqual(calls[1][0]["id"], "custom_now")
            self.assertEqual(calls[1][0]["prompt"], "Find unresolved risks.")
            self.assertEqual(calls[1][0]["notes"], "Pay special attention to the Friday deadline.")
            self.assertEqual(set(manager.session["analyses"]), {"custom_now"})
            self.assertEqual(manager.session["cards"], app.normalize_boxes(current))

    def test_live_card_edit_during_llm_call_discards_stale_result_and_requeues_latest_prompts(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), "test-live-card-race")
            manager.session["segments"].append({"start": 0, "end": 4, "text": "A decision is pending."})
            entered, release = threading.Event(), threading.Event()
            calls = []
            def fake_ollama(_port, _model, _transcript, boxes, _summary, _context_compaction=None):
                calls.append([dict(box) for box in boxes])
                if len(calls) == 1:
                    entered.set()
                    self.assertTrue(release.wait(3))
                return "Summary.", {box["id"]: "Result." for box in boxes}
            with patch.object(app, "_ollama_call", side_effect=fake_ollama):
                worker = threading.Thread(target=manager._maybe_analyze, kwargs={"force": True})
                worker.start()
                self.assertTrue(entered.wait(2))
                latest = [{"id": "live", "title": "Latest", "prompt": "Use current details.",
                           "notes": "Updated while the old request was running.", "enabled": True}]
                manager.update_live_boxes(latest)
                release.set()
                worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertTrue(manager.session["force_analysis"])
                manager._maybe_analyze(force=False)
            self.assertEqual(calls[1][0]["id"], "live")
            self.assertEqual(calls[1][0]["notes"], "Updated while the old request was running.")
            self.assertEqual(set(manager.session["analyses"]), {"live"})

    def test_box_edits_during_audio_finalization_are_analyzed_before_final_analysis(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            manager, chunks = make_manager(root, "test-finalize-live-card-race")
            write_chunk(chunks / "chunk_000000.wav")
            manager.stop_requested = True
            manager._load_whisper = lambda *_args: object()
            manager._transcribe_chunk = lambda *_args: ([{"start": 0, "end": 1, "text": "A deadline is pending."}], "pt")
            finalize_started, allow_finalize = threading.Event(), threading.Event()
            def block_finalize():
                finalize_started.set()
                self.assertTrue(allow_finalize.wait(3))
                pathlib.Path(manager.session["audio_file"]).write_bytes(b"final audio")
            manager._finalize_audio = block_finalize
            calls = []
            def fake_live(_port, _model, _transcript, boxes, _summary, _state=None):
                calls.append([dict(box) for box in boxes])
                return "Summary", {box["id"]: "Result" for box in boxes}
            with (patch.object(app, "_ollama_call", side_effect=fake_live),
                  patch.object(app, "_ollama_final_call", return_value={"summary": "Final", "tasks": [], "results": {}, "updated_at": "test"})):
                worker = threading.Thread(target=manager._processing_loop, daemon=True)
                worker.start()
                self.assertTrue(finalize_started.wait(3))
                latest = [{"id": "final_risk", "title": "Risk", "prompt": "Identify remaining risk.",
                           "notes": "Check the pending deadline.", "enabled": True}]
                manager.update_live_boxes(latest)
                allow_finalize.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertGreaterEqual(len(calls), 2)
            self.assertEqual(calls[-1][0]["id"], "final_risk")
            self.assertEqual(calls[-1][0]["notes"], "Check the pending deadline.")
            self.assertEqual(manager.session["state"], "completed")

    def test_analysis_runs_when_interval_is_due_and_not_twice_for_same_text(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), "test-analysis-cadence")
            manager.session["segments"].append({"start": 0, "end": 4, "text": "A decisão foi registrada."})
            manager.session["started_monotonic"] = time.monotonic() - 178
            calls = []
            def fake_ollama(_port, _model, _transcript, boxes, _summary, _context_compaction=None):
                calls.append([b["id"] for b in boxes])
                return "Compact session summary.", {b["id"]: "valid result" for b in boxes}
            with patch.object(app, "_ollama_call", side_effect=fake_ollama):
                manager._maybe_analyze(force=False)
                self.assertEqual(calls, [])
                manager.session["started_monotonic"] = time.monotonic() - 181
                manager._maybe_analyze(force=False)
                manager._maybe_analyze(force=False)
            self.assertEqual(len(calls), 1)
            self.assertEqual(manager._last_analyzed_segment_count, 1)

    def test_analysis_uses_full_transcript_without_timestamps_and_persists_summary(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), "test-summary")
            manager.session["segments"] = [
                {"start": 0, "end": 12, "text": "Old topic about staffing."},
                {"start": 13, "end": 24, "text": "New topic about a delivery date."},
            ]
            manager.session["analysis_summary"] = "Existing context about the project."
            manager._last_analyzed_segment_count = 1
            seen = []
            def fake_ollama(port, model, transcript, boxes, previous_summary, _context_compaction=None):
                seen.append((transcript, previous_summary))
                return "Updated compact project context.", {b["id"]: "Grounded result." for b in boxes}
            with patch.object(app, "_ollama_call", side_effect=fake_ollama):
                manager._maybe_analyze(force=True)
            self.assertEqual(len(seen), 1)
            self.assertIn("Old topic", seen[0][0])
            self.assertIn("New topic", seen[0][0])
            self.assertNotIn("[", seen[0][0])
            self.assertEqual(seen[0][1], "")
            self.assertEqual(manager.session["analysis_summary"], "Updated compact project context.")
            persisted = json.loads(pathlib.Path(manager.session["session_json"]).read_text())
            self.assertEqual(persisted["analysis_summary"], "Updated compact project context.")
            self.assertEqual(manager._last_analyzed_segment_count, 2)

    def test_each_analysis_batch_receives_full_transcript(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), "test-full-context-batches")
            manager.session["segments"] = [
                {"start": 1, "end": 5, "text": "Early decision remains important."},
                {"start": 80, "end": 90, "text": "Recent status confirms it is still open."},
            ]
            manager.session["cards"] = [{"id": f"card_{i}", "title": f"Card {i}", "prompt": "Analyze the complete meeting.", "enabled": True} for i in range(4)]
            manager._last_analyzed_segment_count = 1
            seen = []
            def fake_ollama(_port, _model, transcript, boxes, previous_summary, _context_compaction=None):
                seen.append((transcript, [box["id"] for box in boxes], previous_summary))
                return "Cumulative summary.", {box["id"]: "Grounded." for box in boxes}
            with patch.object(app, "_ollama_call", side_effect=fake_ollama):
                manager._maybe_analyze(force=True)
            self.assertEqual(len(seen), 2)
            for transcript, _ids, previous_summary in seen:
                self.assertIn("Early decision", transcript)
                self.assertIn("Recent status", transcript)
                self.assertNotIn("[", transcript)
                self.assertEqual(previous_summary, "")
            self.assertEqual([ident for _, ids, _ in seen for ident in ids], [f"card_{i}" for i in range(4)])

    def test_oversized_live_context_keeps_transcript_and_surfaces_error_without_partial_analysis(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), "test-context-overflow")
            complete_text = "Synthetic early context. " + "x" * 2000
            manager.session["segments"] = [{"start": 0, "end": 30, "text": complete_text}]
            manager._last_analyzed_segment_count = 0
            with patch.object(app, "_ollama_call", side_effect=app.LiveAnalysisContextTooLong("transcrição integral acima do limite medido")):
                manager._maybe_analyze(force=True)
            self.assertEqual(manager.session["segments"][0]["text"], complete_text)
            self.assertEqual(manager.session["analyses"], {})
            self.assertEqual(manager._last_analyzed_segment_count, 0)
            self.assertIn("não executada", manager._last_analysis_error.casefold())
            self.assertIn("preservada", manager._last_analysis_error.casefold())

    def test_analysis_runs_on_configured_one_minute_interval(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), "test-one-minute-cadence")
            manager.session["analysis_interval_s"] = 60
            manager.session["segments"].append({"start": 0, "end": 4, "text": "A decisão foi registrada."})
            calls = []
            def fake_ollama(_port, _model, _transcript, boxes, _summary, _context_compaction=None):
                calls.append([b["id"] for b in boxes])
                return "Compact session summary.", {b["id"]: "valid result" for b in boxes}
            with patch.object(app, "_ollama_call", side_effect=fake_ollama):
                manager.session["started_monotonic"] = time.monotonic() - 59
                manager._maybe_analyze(force=False)
                self.assertEqual(calls, [])
                manager.session["started_monotonic"] = time.monotonic() - 61
                manager._maybe_analyze(force=False)
            self.assertEqual(calls, [["questions", "actions", "open_points"]])
            self.assertEqual(manager._last_analyzed_segment_count, 1)

    def test_analysis_retries_once_after_a_local_structured_output_value_error(self):
        with tempfile.TemporaryDirectory() as td:
            manager, _ = make_manager(pathlib.Path(td), "test-analysis-retry")
            manager.session["segments"].append({"start": 0, "end": 4, "text": "A decisão foi registrada."})
            outputs = [ValueError("O modelo não respondeu todas as caixas de análise."),
                       ("Updated session summary.", {b["id"]: "Verified local result." for b in app.default_boxes()})]
            with patch.object(app, "_ollama_call", side_effect=outputs) as call:
                manager._maybe_analyze(force=True)
            self.assertEqual(call.call_count, 2)
            self.assertEqual(set(manager.session["analyses"]), {"questions", "actions", "open_points"})
            self.assertEqual(manager._last_analysis_error, "")

    def test_stop_drains_all_chunks_then_runs_one_final_analysis(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            manager, chunks = make_manager(root, "test-stop")
            for i in range(2):
                write_chunk(chunks / f"chunk_{i:06d}.wav")
            manager.stop_requested = True
            manager._load_whisper = lambda *_args: object()
            manager._transcribe_chunk = lambda _model, path, index, _language: ([{"start": 0, "end": 1, "text": f"segment{index}"}], "pt")
            calls = []
            def fake_ollama(_port, _model, _transcript, boxes, _summary, _context_compaction=None):
                calls.append([b["id"] for b in boxes])
                return "Summary for the processed audio.", {b["id"]: "valid local test result" for b in boxes}
            with (patch.object(app, "_ollama_call", side_effect=fake_ollama),
                  patch.object(app, "_ollama_final_call", return_value={"summary": "Final test summary", "tasks": [], "results": {}, "updated_at": "test"})):
                manager._processing_loop()
            self.assertEqual(manager.session["state"], "completed")
            self.assertEqual(manager.session["next_chunk"], 2)
            self.assertEqual(len(manager.session["segments"]), 2)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0], ["questions", "actions", "open_points"])
            self.assertTrue(pathlib.Path(manager.session["audio_file"]).is_file())
            self.assertEqual(json.loads((root / "analysis.json").read_text()).keys(), {"questions", "actions", "open_points"})

    def test_synthetic_live_capture_transcribes_analyzes_saves_and_runs_final_analysis(self):
        with tempfile.TemporaryDirectory() as td:
            manager = app.MeetingManager(pathlib.Path(td), ffmpeg=shutil.which("ffmpeg"))
            manager.devices = lambda: {"microphones": [{"id": "default"}], "monitors": [{"id": "@DEFAULT_MONITOR@"}]}
            manager._load_whisper = lambda *_args: object()
            manager._transcribe_chunk = lambda _model, _path, index, _language: ([{"start": 0, "end": 1, "text": f"synthetic segment {index}"}], "pt")
            manager._start_resource_monitor = lambda *_args: None
            def synthetic_ffmpeg(binary, _mode, _mic, _pc, _mic_volume, _pc_volume, output, chunk_seconds):
                return [binary, "-y", "-nostats", "-loglevel", "error", "-re", "-f", "lavfi", "-i",
                        "sine=frequency=440:sample_rate=16000", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                        "-f", "segment", "-segment_time", str(chunk_seconds), "-reset_timestamps", "1",
                        "-segment_format", "wav", output]
            request = {"capture_mode": "mic_only", "mic_device": "default", "pc_device": "@DEFAULT_MONITOR@",
                       "whisper_model": "small", "whisper_device": "cpu", "ollama_port": 11435,
                       "llm_model": "gemma4:e4b", "analysis_interval_s": 60, "boxes": app.default_boxes(),
                       "final_analysis_config": app.default_final_config()}
            live_calls = []
            def live_result(_port, _model, _text, boxes, _summary, _context_compaction=None):
                live_calls.append(True)
                return "Synthetic rolling summary", {box["id"]: "Synthetic live analysis" for box in boxes}
            def final_result(_port, _model, transcript, _config, _context_compaction=None):
                return {"summary": "Synthetic final summary", "tasks": [], "results": {},
                        "updated_at": "test", "source_chars": len(transcript)}
            with (patch.object(app, "DEFAULT_CHUNK_SECONDS", 2),
                  patch.object(app, "find_whisper_snapshot", return_value=pathlib.Path(td)),
                  patch.object(app, "_local_ollama_models", return_value=["gemma4:e4b"]),
                  patch.object(app, "build_ffmpeg_command", side_effect=synthetic_ffmpeg),
                  patch.object(app, "_ollama_call", side_effect=live_result),
                  patch.object(app, "_ollama_final_call", side_effect=final_result)):
                manager.start(request)
                manager.session["analysis_interval_s"] = 1
                deadline = time.monotonic() + 25
                while not manager.session["analyses"] and time.monotonic() < deadline:
                    time.sleep(0.2)
                self.assertTrue(manager.session["analyses"], "live LLM analysis should happen while recording")
                self.assertTrue(live_calls)
                manager.stop()
                manager.worker_thread.join(timeout=40)
                self.assertFalse(manager.worker_thread.is_alive())
                self.assertEqual(manager.session["state"], "completed")
                self.assertEqual(manager.session["final_analysis_state"], "completed")
                self.assertTrue(pathlib.Path(manager.session["audio_file"]).is_file())
                self.assertTrue(pathlib.Path(manager.session["transcript_file"]).is_file())
                self.assertEqual(manager.session["final_analysis"]["summary"], "Synthetic final summary")
                self.assertTrue(manager.session["analyses"])
            manager.shutdown()

    def test_stop_runs_final_analysis_only_after_audio_and_full_transcript_are_saved(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            manager, chunks = make_manager(root, "test-final-analysis-order")
            write_chunk(chunks / "chunk_000000.wav")
            manager.stop_requested = True
            manager._load_whisper = lambda *_args: object()
            manager._transcribe_chunk = lambda *_args: ([{"start": 0, "end": 1, "text": "Complete meeting fact."}], "pt")
            manager._finalize_audio = lambda: pathlib.Path(manager.session["audio_file"]).write_bytes(b"final audio")
            observed = {}
            def fake_final(_port, _model, transcript, config, _context_compaction=None):
                observed["transcript"] = transcript
                observed["state"] = manager.session["state"]
                observed["transcript_saved"] = pathlib.Path(manager.session["transcript_file"]).is_file()
                observed["config"] = config
                return {"summary": "Final summary", "tasks": [], "results": {}, "updated_at": "test"}
            with (patch.object(app, "_ollama_call", return_value=("Rolling summary", {b["id"]: "Live result" for b in app.default_boxes()})),
                  patch.object(app, "_ollama_final_call", side_effect=fake_final)):
                manager._processing_loop()
            self.assertEqual(observed["state"], "final_analysis")
            self.assertTrue(observed["transcript_saved"])
            self.assertEqual(observed["transcript"], "Complete meeting fact.")
            self.assertNotIn("00:", observed["transcript"])
            self.assertEqual(manager.session["state"], "completed")
            self.assertEqual(manager.session["final_analysis"]["summary"], "Final summary")
            saved = json.loads(pathlib.Path(manager.session["session_json"]).read_text())
            self.assertEqual(saved["final_analysis"]["summary"], "Final summary")

    def test_stop_does_not_transcribe_the_final_segment_until_ffmpeg_exits(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            manager, chunks = make_manager(root, "test-stop-race")
            write_chunk(chunks / "chunk_000000.wav")
            class FakeProcess:
                done = False
                def poll(self):
                    return 0 if self.done else None
            proc = FakeProcess()
            manager.recorder = proc
            manager.stop_requested = True
            manager._load_whisper = lambda *_args: object()
            transcribed = threading.Event()
            manager._transcribe_chunk = lambda *_args: (transcribed.set() or ([{"start": 0, "end": 1, "text": "final"}], "pt"))
            with (patch.object(app, "_ollama_call", return_value=("Final summary.", {b["id"]: "ok" for b in app.default_boxes()})),
                  patch.object(app, "_ollama_final_call", return_value={"summary": "Final", "tasks": [], "results": {}, "updated_at": "test"})):
                worker = threading.Thread(target=manager._processing_loop, daemon=True)
                worker.start()
                self.assertFalse(transcribed.wait(0.2), "must wait for FFmpeg to close the active WAV segment")
                proc.done = True
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertTrue(transcribed.is_set())
            self.assertEqual(manager.session["state"], "completed")

    def test_uploaded_audio_is_segmented_transcribed_analyzed_and_saved_locally(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / "input.audio"
            write_chunk(source)
            manager = app.MeetingManager(root / "outputs", ffmpeg=shutil.which("ffmpeg"))
            request = {"whisper_model": "small", "whisper_device": "cuda", "ollama_port": 11435,
                       "llm_model": "gemma4:e4b", "analysis_interval_s": 180, "boxes": app.default_boxes()}
            with patch.object(app, "find_whisper_snapshot", return_value=root / "local-whisper-model"), \
                 patch.object(app, "_local_ollama_models", return_value=["gemma4:e4b"]):
                token = manager.prepare_file_upload(request)["token"]
                manager._load_whisper = lambda *_args: object()
                manager._transcribe_chunk = lambda _model, _path, index, _language: ([{"start": 0, "end": 0.1, "text": f"local file segment {index}"}], "pt")
                with (patch.object(app, "_ollama_call", side_effect=lambda _p, _m, _t, boxes, _s, _state=None: ("Local test summary", {b["id"]: "Grounded local result" for b in boxes})),
                      patch.object(app, "_ollama_final_call", return_value={"summary": "Uploaded final", "tasks": [], "results": {}, "updated_at": "test"})):
                    result = manager.start_uploaded_file(token, source, "sample.wav")
                    self.assertEqual(result["state"], "processing")
                    manager.worker_thread.join(timeout=10)
                self.assertFalse(manager.worker_thread.is_alive())
                self.assertEqual(manager.session["state"], "completed")
                self.assertEqual(manager.session["capture_mode"], "file_upload")
                self.assertTrue(pathlib.Path(manager.session["audio_file"]).is_file())
                self.assertTrue(pathlib.Path(manager.session["transcript_file"]).is_file())
                self.assertEqual(set(manager.session["analyses"]), {"questions", "actions", "open_points"})
                self.assertEqual(manager.session["ollama_port"], 11435)
                self.assertEqual(manager.session["llm_model"], "gemma4:e4b")
                self.assertGreaterEqual(manager.session["resource_usage"]["sample_count"], 2)
                self.assertIn("ram_available_min_mib", manager.session["resource_usage"])
                saved = json.loads(pathlib.Path(manager.session["session_json"]).read_text())
                self.assertEqual(saved["resource_usage"]["sample_count"], manager.session["resource_usage"]["sample_count"])


if __name__ == "__main__":
    unittest.main()
