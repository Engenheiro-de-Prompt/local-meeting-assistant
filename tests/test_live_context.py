import json
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import live_meeting_app as app


class FakeResponse:
    status = 200

    def __init__(self, content):
        self.content = content

    def read(self, _limit):
        return self.content


class FakeConnection:
    def __init__(self, _host, _port, timeout):
        self.timeout = timeout
        self.body = None

    def request(self, _method, _path, body, headers):
        self.body = json.loads(body.decode("utf-8"))
        self.headers = headers

    def getresponse(self):
        user_payload = json.loads(self.body["messages"][1]["content"])
        results = [{"id": box["id"], "text": "Grounded synthetic result."} for box in user_payload["caixas"]]
        content = json.dumps({"summary": "Synthetic cumulative summary.", "results": results}, ensure_ascii=False)
        data = {"message": {"content": content}, "prompt_eval_count": getattr(self, "prompt_eval_count", 100)}
        return FakeResponse(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def close(self):
        pass


class LiveContextRequestTests(unittest.TestCase):
    def test_compaction_keeps_older_summary_before_exact_recent_raw_tail(self):
        transcript = "old decision. " + ("older context. " * 5000) + "LATEST correction and deadline."
        calls = []

        def summarize(_port, _model, _system, user, schema, **_options):
            self.assertNotIn("maxLength", schema["properties"]["notes"])
            payload = json.loads(user)
            calls.append(payload)
            return {"notes": "Decision: old decision. Correction/deadline must be preserved."}

        state = {}
        with patch.object(app, "_ollama_json_call", side_effect=summarize), patch.object(app, "CONTEXT_COMPACTION_TRIGGER_TOKENS", 100):
            context, mode = app._build_analysis_context(11435, "gemma4:e4b", transcript, force=True, compaction_state=state)

        self.assertGreater(len(calls), 1)
        self.assertEqual(len(calls), calls[0]["novos_trechos_a_processar"])
        self.assertEqual(mode, "resumo cumulativo + transcrição bruta recente")
        self.assertLess(context.index("RESUMO CUMULATIVO"), context.index("TRANSCRIÇÃO BRUTA RECENTE"))
        self.assertIn("Decision: old decision", context)
        self.assertTrue(context.endswith("LATEST correction and deadline."))
        self.assertEqual("".join(call["trecho_novo"] for call in calls), transcript[:state["covered_chars"]])
        self.assertTrue(context.endswith(transcript[state["covered_chars"]:]))

    def test_incremental_compaction_only_summarizes_newly_matured_text(self):
        transcript = "Decision: retain option A.\n" + ("Older detail. " * 2000) + "\nRecent status."
        state = {}
        calls = []

        def summarize(_port, _model, _system, user, _schema, **_options):
            calls.append(json.loads(user))
            return {"notes": "Cumulative facts including decision A."}

        with patch.object(app, "_ollama_json_call", side_effect=summarize), patch.object(app, "CONTEXT_COMPACTION_TRIGGER_TOKENS", 100):
            first, _ = app._build_analysis_context(11435, "gemma4:e4b", transcript, force=True, compaction_state=state)
            first_call_count = len(calls)
            extended = transcript + ("new detail. " * 3000) + "\nNewly matured update."
            second, _ = app._build_analysis_context(11435, "gemma4:e4b", extended, compaction_state=state)

        self.assertGreater(first_call_count, 0)
        self.assertEqual(len(calls), first_call_count + 1)
        self.assertEqual(calls[-1]["notas_anteriores"], "Cumulative facts including decision A.")
        self.assertTrue(first.startswith("RESUMO CUMULATIVO"))
        self.assertIn("Newly matured update.", second)


    def test_live_request_uses_compacted_context_when_transcript_crosses_budget(self):
        transcript = "Old agreement. " + ("Meeting details. " * 12000) + "Recent correction."
        boxes = [{"id": "questions", "title": "Questions", "prompt": "Use all context."}]
        connection = FakeConnection("127.0.0.1", 11435, 120)
        with patch.object(app, "_build_analysis_context", return_value=("RESUMO CUMULATIVO\\nold decisions\\nTRANSCRIÇÃO BRUTA RECENTE\\nRecent correction.", "resumo cumulativo + transcrição bruta recente")) as compact, \
             patch.object(app.http.client, "HTTPConnection", return_value=connection):
            app._ollama_call(11435, "gemma4:e4b", transcript, boxes)
        compact.assert_called_once()
        payload = json.loads(connection.body["messages"][1]["content"])
        self.assertEqual(payload["modo_de_contexto"], "resumo cumulativo + transcrição bruta recente")
        self.assertEqual(payload["contexto_reuniao"], "RESUMO CUMULATIVO\\nold decisions\\nTRANSCRIÇÃO BRUTA RECENTE\\nRecent correction.")

    def test_request_sends_full_transcript_without_previous_summary_or_truncation(self):
        transcript = "Fact from the beginning. " + ("Neutral synthetic discussion. " * 1300) + "Latest status update."
        boxes = [{"id": "questions", "title": "Questions", "prompt": "Use the whole meeting."}]
        connection = FakeConnection("127.0.0.1", 11435, 120)
        with patch.object(app.http.client, "HTTPConnection", return_value=connection):
            summary, results = app._ollama_call(11435, "gemma4:e4b", transcript, boxes, "stale summary must not override transcript")
        user_payload = json.loads(connection.body["messages"][1]["content"])
        self.assertEqual(user_payload["contexto_reuniao"], transcript)
        self.assertNotIn("resumo_anterior", user_payload)
        self.assertEqual(user_payload["modo_de_contexto"], "transcrição integral sem compactação")
        self.assertGreater(connection.body["options"]["num_ctx"], 4096)
        self.assertEqual(summary, "Synthetic cumulative summary.")
        self.assertEqual(results, {"questions": "Grounded synthetic result."})

    def test_full_context_beyond_tested_window_fails_explicitly_without_http(self):
        transcript = "x" * 1_000_000
        boxes = [{"id": "questions", "title": "Questions", "prompt": "Use the whole meeting."}]
        with patch.object(app, "_build_analysis_context", return_value=("x" * 1_000_000, "resumo cumulativo + transcrição bruta recente")), \
             patch.object(app.http.client, "HTTPConnection") as connection:
            with self.assertRaises(app.LiveAnalysisContextTooLong):
                app._ollama_call(11435, "gemma4:e4b", transcript, boxes)
        connection.assert_not_called()

    def test_full_131k_character_context_fits_within_measured_window_and_output_reserve(self):
        transcript = "Decision at meeting start. " + ("Synthetic neutral discussion and review. " * 4000)
        transcript = transcript[:131_072].ljust(131_072, "x")
        boxes = [{"id": box["id"], "titulo": box["title"], "prompt": box["prompt"]} for box in app.default_boxes()]
        user = json.dumps({"transcricao_completa": transcript, "caixas": boxes}, ensure_ascii=False)
        context = app._live_analysis_num_ctx(user, "Local system prompt with full-history rules.", 1700)
        self.assertGreater(context, 32768)
        self.assertLessEqual(context, app.LIVE_ANALYSIS_MAX_CONTEXT_TOKENS)

    def test_model_rejects_results_when_prompt_metrics_show_context_overflow(self):
        transcript = "A synthetic full meeting transcript."
        boxes = [{"id": "questions", "title": "Questions", "prompt": "Use the whole meeting."}]
        connection = FakeConnection("127.0.0.1", 11435, 120)
        connection.prompt_eval_count = 100_000
        with patch.object(app.http.client, "HTTPConnection", return_value=connection):
            with self.assertRaises(app.LiveAnalysisContextTooLong):
                app._ollama_call(11435, "gemma4:e4b", transcript, boxes)


if __name__ == "__main__":
    unittest.main()
