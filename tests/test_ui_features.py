import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")


class DashboardFeatureTests(unittest.TestCase):
    def test_ai_resource_controls_are_available_and_sent_for_live_and_uploaded_audio(self):
        for element_id in ("transcriptionMode", "liveAnalysisEnabled", "finalAnalysisEnabled"):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', HTML)
        self.assertIn('value="after_meeting"', HTML)
        self.assertIn('value="off"', HTML)
        self.assertIn("transcription_mode:$('transcriptionMode').value", HTML)
        self.assertIn("live_analysis_enabled:$('liveAnalysisEnabled').checked", HTML)
        self.assertIn("final_analysis_enabled:$('finalAnalysisEnabled').checked", HTML)
        self.assertIn("transcription_mode:$('transcriptionMode').value", HTML.split("async function importAudio", 1)[1])

    def test_analysis_panel_precedes_the_secondary_configuration_grid(self):
        self.assertLess(HTML.index('id="analysisPanel"'), HTML.index('id="configPanel"'))

    def test_manual_workspace_is_first_and_separate_from_model_box_context(self):
        self.assertIn('id="notesPanel"', HTML)
        self.assertLess(HTML.index('id="notesPanel"'), HTML.index('id="controlPanel"'))
        self.assertIn('id="resetLayout"', HTML)
        self.assertIn('src="/manual-notes.js"', HTML)
        self.assertIn('src="/dashboard-layout.js"', HTML)
        self.assertIn('manual_notes:globalThis.MeetingNotes?.forStart()', HTML)
        box_config = HTML.split('function getBoxConfig()', 1)[1].split('\n', 1)[0]
        self.assertNotIn('manual', box_config)

    def test_dashboard_defaults_to_dark_color_scheme(self):
        self.assertRegex(HTML, r"color-scheme\s*:\s*dark")
        self.assertIn("--bg:#08090a", HTML)

    def test_outputs_and_original_recorder_actions_are_present(self):
        for element_id in ("downloadTranscript", "downloadAnalysis", "copyTranscript", "openTxt", "openFolder", "playAudio", "audioPlayer", "transcribeOther", "transcriptView"):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', HTML)

    def test_record_only_mode_exposes_its_explanatory_transcript_file(self):
        paint = HTML.split("function paint(s){", 1)[1].split("\n", 1)[0]
        self.assertIn("hasTranscriptDocument=!!s.transcript_file&&(hasTranscript||(s.transcription_mode==='off'&&!busy&&hasSession))", paint)
        self.assertIn("$('downloadTranscript').disabled=!hasTranscriptDocument", paint)
        self.assertIn("$('openTxt').disabled=!hasTranscriptDocument", paint)
        self.assertIn("$('copyTranscript').disabled=!s.transcript", paint)

    def test_final_analysis_workflow_is_configurable_and_exports_combined_txt(self):
        self.assertGreater(HTML.index('id="finalAnalysisPanel"'), HTML.index('id="transcriptPanel"'))
        for element_id in ("summaryPrompt", "tasksPrompt", "finalBoxes", "addFinalBox", "finalTasksTable", "downloadCombined"):
            with self.subTest(element_id=element_id):
                self.assertIn(f'id="{element_id}"', HTML)
        self.assertIn("final_analysis_config:getFinalConfig()", HTML)
        self.assertIn("renderFinalAnalysis(s)", HTML)

    def test_live_box_defaults_use_full_context_and_upgrade_legacy_defaults(self):
        defaults = HTML.split("function defaultBoxes(){", 1)[1].split("]}", 1)[0]
        self.assertIn("transcrição completa", defaults)
        self.assertNotIn("trecho novo", defaults)
        self.assertIn("function migrateDefaultBoxPrompts(saved)", HTML)
        self.assertIn("boxes=migrateDefaultBoxPrompts(saved)", HTML)
        self.assertIn("resumo cumulativo", HTML)
        self.assertIn("transcrição bruta recente", HTML)
        self.assertIn("transcrição completa é preservada", HTML)
        self.assertNotIn("não corta a conversa", HTML)

    def test_live_box_editor_uses_one_shared_context_and_posts_live_changes(self):
        self.assertIn('id="liveBoxStatus"', HTML)
        self.assertEqual(HTML.count('id="meetingContext"'), 1)
        self.assertIn("compartilhado por todas as caixas ao vivo", HTML)
        self.assertIn("/api/live-boxes", HTML)
        self.assertIn("notes:$('meetingContext').value", HTML)
        self.assertNotIn("Observações para o LLM", HTML)
        self.assertIn("scheduleLiveBoxSave()", HTML)

    def test_live_start_keeps_error_visible_and_final_analysis_is_a_busy_state(self):
        self.assertIn("operationError=e.message", HTML)
        self.assertIn("'final_analysis'", HTML)

    def test_installed_gemma4_e4b_is_the_new_default(self):
        self.assertIn('value="11435" selected', HTML)
        self.assertIn("gemma4:e4b", HTML)
        self.assertIn("gemma-3-4b:latest", HTML)  # keep the previous model selectable


if __name__ == "__main__":
    unittest.main()
