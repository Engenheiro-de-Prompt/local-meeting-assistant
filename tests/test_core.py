import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import live_meeting_app as app


class LiveMeetingCoreTests(unittest.TestCase):
    def test_three_default_boxes_cover_questions_actions_and_open_points(self):
        boxes = app.default_boxes()
        self.assertEqual(len(boxes), 3)
        self.assertEqual([b["id"] for b in boxes], ["questions", "actions", "open_points"])
        self.assertTrue(all(b["enabled"] and b["prompt"].strip() for b in boxes))

    def test_custom_boxes_are_unbounded_by_ui_count_but_each_is_validated(self):
        boxes = [{"id": f"card_{i}", "title": f"Card {i}", "prompt": "Prompt"} for i in range(27)]
        normalized = app.normalize_boxes(boxes)
        self.assertEqual(len(normalized), 27)
        self.assertEqual(app.normalize_boxes([]), [])
        disabled = app.normalize_boxes([{"id": "disabled", "title": "Disabled", "prompt": "Prompt", "enabled": False}])
        self.assertFalse(disabled[0]["enabled"])
        noted = app.normalize_boxes([{"id": "card", "title": "Card", "prompt": "Prompt", "notes": "Keep the caveat visible."}])
        self.assertEqual(noted[0]["notes"], "Keep the caveat visible.")
        self.assertEqual(app.normalize_boxes([{"id": "card", "title": "Card", "prompt": "Prompt"}])[0]["notes"], "")
        with self.assertRaises(ValueError):
            app.normalize_boxes([{"id": "same", "title": "A", "prompt": "P"}, {"id": "same", "title": "B", "prompt": "P"}])
        with self.assertRaises(ValueError):
            app.normalize_boxes([{"id": "card", "title": "Card", "prompt": "Prompt", "notes": "x" * (app.MAX_NOTES_CHARS + 1)}])

    def test_analysis_schema_requires_a_summary_and_exact_box_results(self):
        schema = app.analysis_json_schema(app.default_boxes())
        self.assertEqual(schema["type"], "object")
        self.assertEqual(set(schema["required"]), {"summary", "results"})
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("maxLength", schema["properties"]["summary"])
        item = schema["properties"]["results"]["items"]
        self.assertEqual(schema["properties"]["results"]["minItems"], len(app.default_boxes()))
        self.assertEqual(schema["properties"]["results"]["maxItems"], len(app.default_boxes()))
        self.assertEqual(item["properties"]["id"]["enum"], [b["id"] for b in app.default_boxes()])
        self.assertNotIn("maxLength", item["properties"]["text"])
        self.assertEqual(set(item["required"]), {"id", "text"})
        self.assertFalse(item["additionalProperties"])

    def test_analysis_envelope_requires_and_returns_compact_summary(self):
        boxes = app.default_boxes()
        payload = {"summary": "A equipe ainda compara duas opções de prazo.", "results": [{"id": b["id"], "text": "Resultado baseado no trecho."} for b in boxes]}
        payload_with_echo = {**payload, "resumo_anterior": "Contexto já conhecido."}
        summary, results = app.parse_analysis_envelope(json.dumps(payload_with_echo, ensure_ascii=False), boxes)
        self.assertEqual(summary, payload["summary"])
        self.assertEqual(set(results), {b["id"] for b in boxes})
        alias_only = {"resumo_anterior": "Resumo incremental atualizado.", "results": payload["results"]}
        alias_summary, _ = app.parse_analysis_envelope(json.dumps(alias_only, ensure_ascii=False), boxes)
        self.assertEqual(alias_summary, alias_only["resumo_anterior"])
        for invalid in (
            {"results": payload["results"]},
            {"summary": "", "results": payload["results"]},
            {"summary": "x" * 3001, "results": payload["results"]},
            {"summary": "ok", "results": payload["results"], "extra": True},
        ):
            with self.subTest(invalid=invalid.keys()), self.assertRaises(ValueError):
                app.parse_analysis_envelope(json.dumps(invalid), boxes)

    def test_analysis_splits_boxes_into_sequential_batches_of_three(self):
        boxes = [{"id": f"card_{i}", "title": str(i), "prompt": "P", "enabled": True} for i in range(8)]
        batches = app.analysis_batches(boxes)
        self.assertEqual([len(x) for x in batches], [3, 3, 2])
        self.assertEqual([b["id"] for group in batches for b in group], [b["id"] for b in boxes])

    def test_chunk_segments_get_absolute_timestamps(self):
        converted = app.offset_segments(3, [{"start": 1.5, "end": 3.25, "text": "  Olá equipe  "}], chunk_seconds=45)
        self.assertEqual(converted, [{"start": 136.5, "end": 138.25, "text": "Olá equipe"}])

    def test_analysis_response_requires_each_expected_id_once_and_nonempty_text(self):
        boxes = [{"id": "questions"}, {"id": "actions"}]
        parsed = app.parse_analysis_response('{"results":[{"id":"questions","text":"Q?"},{"id":"actions","text":"A"}]}', boxes)
        self.assertEqual(parsed, {"questions": "Q?", "actions": "A"})
        for bad in (
            "{broken",
            '{"results":[{"id":"questions","text":"Q?"}]}',
            '{"results":[{"id":"questions","text":"Q?"},{"id":"questions","text":"Q2"}]}',
            '{"results":[{"id":"questions","text":"Q?"},{"id":"actions","text":"   "}]}',
            json.dumps({"results": [{"id": "questions", "text": "x" * (app.LIVE_ANALYSIS_RESULT_CHARS + 1)}, {"id": "actions", "text": "A"}]}),
            '{"results":[{"id":"questions","text":"Q?"},{"id":"actions","text":"A"}],"extra":true}',
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                app.parse_analysis_response(bad, boxes)

    def test_only_loopback_ollama_endpoints_are_accepted(self):
        self.assertEqual(app.validate_ollama_base_url("http://127.0.0.1:11434"), ("127.0.0.1", 11434))
        self.assertEqual(app.validate_ollama_base_url("http://127.0.0.1:11435/"), ("127.0.0.1", 11435))
        for value in ("http://example.com:11434", "http://127.0.0.1:0", "http://127.0.0.1:11434evil", "https://127.0.0.1:11434", "http://user@127.0.0.1:11434"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                app.validate_ollama_base_url(value)

    def test_default_llm_points_to_installed_gemma4_e4b_local_endpoint(self):
        self.assertEqual(app.DEFAULT_OLLAMA_PORT, 11435)
        self.assertEqual(app.DEFAULT_LLM_MODEL, "gemma4:e4b")

    def test_local_whisper_snapshot_is_resolved_without_download(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td) / "hub/models--Systran--faster-whisper-small/snapshots/abc"
            root.mkdir(parents=True)
            (root / "model.bin").write_bytes(b"fixture")
            self.assertEqual(app.find_whisper_snapshot("small", pathlib.Path(td)), root)
            with self.assertRaises(FileNotFoundError):
                app.find_whisper_snapshot("medium", pathlib.Path(td))

    def test_ffmpeg_command_uses_local_pulse_capture_and_segmented_wav_without_shell(self):
        cmd = app.build_ffmpeg_command(
            ffmpeg="/usr/bin/ffmpeg", mode="both", mic_device="default", pc_device="@DEFAULT_MONITOR@",
            mic_volume=1.0, pc_volume=1.0, chunks_pattern="/recordings/session/chunk_%06d.wav", chunk_seconds=45,
        )
        self.assertIsInstance(cmd, list)
        self.assertIn("segment", cmd)
        self.assertIn("-segment_time", cmd)
        self.assertNotIn("shell", cmd)
        joined = " ".join(cmd)
        self.assertIn("amix=inputs=2", joined)
        with self.assertRaises(ValueError):
            app.build_ffmpeg_command("ffmpeg", "both", "bad;device", "@DEFAULT_MONITOR@", 1, 1, "x_%06d.wav", 45)

    def test_session_files_are_atomically_written_private_and_transcript_is_cumulative(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            target = root / "session.json"
            app.atomic_write_json(target, {"transcript": "primeiro"})
            app.atomic_write_json(target, {"transcript": "primeiro segundo"})
            self.assertEqual(json.loads(target.read_text())["transcript"], "primeiro segundo")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(sorted(x.name for x in root.iterdir()), ["session.json"])


if __name__ == "__main__":
    unittest.main()
