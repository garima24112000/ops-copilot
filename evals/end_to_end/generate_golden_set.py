"""Generate the 20-task GENERATED subset of the end-to-end golden set from data/alerts.jsonl
(session 14: the operator's instructions allow Claude Code to generate this subset -- the 10
human-authored tasks in golden_set_human.yaml are explicitly NOT this script's job).

expected_tool_sequence is a deterministic prediction from the graph's fixed topology
(agent/graph.py always calls search_runbooks -> find_similar_incidents -> query_service_health
during ground/diagnose; the actual action tool depends on triage/diagnose LLM output, which
this script cannot know ahead of time -- left empty for critical alerts pending real diagnosis,
since predicting a specific side-effecting tool call before running the agent would be a guess
dressed up as ground truth.

Run: python -m evals.end_to_end.generate_golden_set
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data"
OUT_PATH = ROOT / "evals" / "end_to_end" / "golden_set_generated.yaml"
N_TASKS = 20
SEED = 7


def main() -> int:
    alerts = [json.loads(line) for line in (DATA_DIR / "alerts.jsonl").open() if line.strip()]
    rng = random.Random(SEED)
    sampled = rng.sample(alerts, min(N_TASKS, len(alerts)))

    tasks = []
    for i, a in enumerate(sampled, start=1):
        tasks.append(
            {
                "id": f"gen-{i:02d}",
                "alert": {
                    "service": a["service"],
                    "severity": a["severity"],
                    "metric": a["metric"],
                    "message": a["message"],
                },
                "expected_runbook_id": a["runbook_id"],
                "expected_tool_sequence": [
                    "search_runbooks",
                    "find_similar_incidents",
                    "query_service_health",
                ],
            }
        )

    OUT_PATH.write_text(
        "# Generated end-to-end golden set (session 14) -- reverse-generated alerts from "
        "data/alerts.jsonl,\n"
        "# same source and same LLM-generated-query bias as the retrieval ablation. Reported "
        "as a\n"
        "# SEPARATE subset from golden_set_human.yaml, never merged (CLAUDE.md rule).\n"
        + yaml.safe_dump({"tasks": tasks}, sort_keys=False, allow_unicode=True)
    )
    print(f"wrote {len(tasks)} generated tasks -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
