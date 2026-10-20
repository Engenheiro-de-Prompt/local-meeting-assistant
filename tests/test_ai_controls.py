import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import live_meeting_app as app


class ProcessingOptionsTests(unittest.TestCase):
    def test_legacy_request_preserves_current_feature_defaults(self):
        self.assertEqual(app.normalize_processing_options({}), {
            "transcription_mode": "live",
            "live_analysis_enabled": True,
            "final_analysis_enabled": True,
        })

    def test_user_can_select_delayed_transcription_and_disable_each_llm_phase(self):
        self.assertEqual(app.normalize_processing_options({
            "transcription_mode": "after_meeting",
            "live_analysis_enabled": False,
            "final_analysis_enabled": True,
        }), {
            "transcription_mode": "after_meeting",
            "live_analysis_enabled": False,
            "final_analysis_enabled": True,
        })

    def test_ai_phase_requirements_match_the_selected_transcription_mode(self):
        self.assertTrue(app.processing_needs_llm({"transcription_mode": "live", "live_analysis_enabled": True, "final_analysis_enabled": False}))
        self.assertTrue(app.processing_needs_llm({"transcription_mode": "after_meeting", "live_analysis_enabled": False, "final_analysis_enabled": True}))
        self.assertFalse(app.processing_needs_llm({"transcription_mode": "after_meeting", "live_analysis_enabled": True, "final_analysis_enabled": False}))
        self.assertFalse(app.processing_needs_llm({"transcription_mode": "off", "live_analysis_enabled": True, "final_analysis_enabled": True}))

    def test_record_only_configuration_does_not_require_whisper_or_ollama_models(self):
        with tempfile.TemporaryDirectory() as td:
            manager = app.MeetingManager(pathlib.Path(td), ffmpeg="/usr/bin/ffmpeg")
            request = {
                "transcription_mode": "off",
                "live_analysis_enabled": False,
                "final_analysis_enabled": False,
            }
            with (patch.object(app, "find_whisper_snapshot", side_effect=AssertionError("Whisper lookup should be skipped")),
                  patch.object(app, "_local_ollama_models", side_effect=AssertionError("Ollama lookup should be skipped"))):
                settings = manager._file_settings(request)
            self.assertEqual(settings["transcription_mode"], "off")
            self.assertFalse(settings["live_analysis_enabled"])
            self.assertFalse(settings["final_analysis_enabled"])

    def test_invalid_modes_and_non_boolean_switches_are_rejected(self):
        for options in ({"transcription_mode": "sometimes"}, {"live_analysis_enabled": "false"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                app.normalize_processing_options(options)


if __name__ == "__main__":
    unittest.main()
