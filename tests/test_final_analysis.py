import json
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import live_meeting_app as app


class FinalAnalysisTests(unittest.TestCase):
    def test_default_final_configuration_has_summary_and_task_prompts_without_custom_boxes(self):
        config = app.default_final_config()
        self.assertIn("resumo", config["summary_prompt"].lower())
        self.assertIn("tarefa", config["tasks_prompt"].lower())
        self.assertEqual(config["boxes"], [])

    def test_final_config_validates_custom_boxes_and_prompt_lengths(self):
        config = app.normalize_final_config({
            "summary_prompt": "Resumo fiel, sem inventar.",
            "tasks_prompt": "Extraia tarefas explícitas.",
            "boxes": [{"id": "risks", "title": "Riscos", "prompt": "Aponte riscos", "enabled": True}],
        })
        self.assertEqual(config["boxes"][0]["id"], "risks")
        with self.assertRaises(ValueError):
            app.normalize_final_config({"summary_prompt": "", "tasks_prompt": "ok", "boxes": []})

    def test_final_json_parser_validates_results_and_sorts_urgent_tasks_first(self):
        config = app.normalize_final_config({
            "summary_prompt": "Resuma.", "tasks_prompt": "Liste tarefas.",
            "boxes": [{"id": "risks", "title": "Riscos", "prompt": "Riscos", "enabled": True}],
        })
        answer = {
            "summary": "Resumo completo.",
            "tasks": [
                {"task": "Acompanhar", "description": "Verificar depois", "status": "Não iniciado", "owner": "Não informado", "priority": "Baixa", "urgency": "Baixa"},
                {"task": "Enviar proposta", "description": "Enviar hoje", "status": "Em andamento", "owner": "Ana", "priority": "Máxima", "urgency": "Imediata"},
            ],
            "results": [{"id": "risks", "text": "Prazo curto."}],
        }
        parsed = app.parse_final_analysis_response(json.dumps(answer, ensure_ascii=False), config)
        self.assertEqual(parsed["tasks"][0]["task"], "Enviar proposta")
        self.assertEqual(parsed["results"], {"risks": "Prazo curto."})

    def test_final_transcript_omits_timecodes_but_keeps_all_segment_text(self):
        text = app.transcript_text_without_timestamps([
            {"start": 0, "end": 2, "text": "Primeira decisão."},
            {"start": 45, "end": 48, "text": "Responsável definido."},
        ])
        self.assertEqual(text, "Primeira decisão.\nResponsável definido.")
        self.assertNotIn("00:", text)

    def test_final_summary_tasks_and_custom_boxes_receive_shared_compacted_context(self):
        config = app.normalize_final_config({
            "summary_prompt": "Resumo completo.", "tasks_prompt": "Tarefas explícitas.",
            "boxes": [{"id": "risks", "title": "Riscos", "prompt": "Liste riscos", "enabled": True}],
        })
        captured = {}

        def fake_call(_port, _model, _system, user, _schema, **_options):
            captured["user"] = json.loads(user)
            return {"summary": "Resumo validado.", "tasks": [], "results": [{"id": "risks", "text": "Nenhum risco pendente."}]}

        context = "RESUMO CUMULATIVO\nDecisão antiga\nTRANSCRIÇÃO BRUTA RECENTE\nCorreção atual"
        with patch.object(app, "_build_analysis_context", return_value=(context, "resumo cumulativo + transcrição bruta recente")), \
             patch.object(app, "_ollama_json_call", side_effect=fake_call):
            result = app._ollama_final_call(11435, "gemma4:e4b", "synthetic full transcript", config, {})

        self.assertEqual(captured["user"]["contexto_completo"], context)
        self.assertEqual(captured["user"]["tipo_de_contexto"], "resumo cumulativo + transcrição bruta recente")
        self.assertEqual(result["summary"], "Resumo validado.")
        self.assertEqual(result["results"]["risks"], "Nenhum risco pendente.")

    def test_combined_report_orders_summary_tasks_then_complete_transcript(self):
        session = {
            "session_id": "test-session",
            "segments": [{"start": 0, "end": 1, "text": "Transcrição completa."}],
            "final_analysis": {
                "summary": "Resumo completo.",
                "tasks": [{"task": "Enviar", "description": "Proposta", "status": "Não iniciado", "owner": "Ana", "priority": "Alta", "urgency": "Alta"}],
                "results": {"risks": "Risco de prazo."},
            },
            "final_analysis_config": {"boxes": [{"id": "risks", "title": "Riscos", "enabled": True}]},
        }
        report = app.combined_report_text(session)
        self.assertLess(report.index("Resumo completo."), report.index("Enviar"))
        self.assertLess(report.index("Enviar"), report.index("Transcrição completa."))
        self.assertIn("Risco de prazo.", report)


if __name__ == "__main__":
    unittest.main()
