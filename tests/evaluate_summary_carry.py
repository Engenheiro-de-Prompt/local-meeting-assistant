#!/usr/bin/env python3
"""Verify that compact meeting context carries facts between local analysis ticks."""
from __future__ import annotations
import json
import pathlib
import time
import live_meeting_app as app


def main():
    boxes = app.default_boxes()
    start = time.monotonic()
    summary_1, results_1 = app._ollama_call(11434, "gemma-3-4b:latest",
        "A equipe designou Ana como responsável por enviar o relatório até sexta-feira.", boxes, "")
    summary_2, results_2 = app._ollama_call(11434, "gemma-3-4b:latest",
        "O orçamento continua indefinido e será decidido na segunda-feira.", boxes, summary_1)
    normalized = summary_2.casefold()
    checks = {"owner_preserved": "ana" in normalized,
              "first_deadline_preserved": "sexta" in normalized,
              "new_budget_fact_present": "orçamento" in normalized or "orcamento" in normalized,
              "new_decision_date_present": "segunda" in normalized,
              "all_three_boxes_returned_each_tick": len(results_1) == 3 and len(results_2) == 3 and all(results_1.values()) and all(results_2.values())}
    if not all(checks.values()):
        raise SystemExit(json.dumps({"failed": [k for k,v in checks.items() if not v],
                                     "summary_chars": [len(summary_1), len(summary_2)]}))
    report = {"test": "local_incremental_context_carry_forward", "model": "gemma-3-4b:latest",
              "endpoint": "127.0.0.1:11434", "analysis_ticks": 2,
              "summary_chars": [len(summary_1), len(summary_2)], "checks": checks,
              "raw_summary_or_outputs_printed": False, "external_network_used": False,
              "wall_seconds": round(time.monotonic()-start,2)}
    out = pathlib.Path(__file__).resolve().parents[1] / "evidence" / "summary-carry-forward.json"
    app.atomic_write_json(out, report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
