"""End-to-end task-success eval: runs real alerts through the full agent (this is the ONE
eval path allowed to call an LLM, per CLAUDE.md -- retrieval evals never do). Reports the
human-authored and LLM-generated subsets as separate rows, never merged, and marks the human
subset PENDING until every stub in golden_set_human.yaml is filled in.

Metrics per task: task success (did the agent ground itself in the expected runbook?),
tool-selection accuracy (did it call the expected tools?), tokens, latency. Proposed actions
are auto-approved during the eval (no human in the loop for a batch run) -- documented here
since it's a deliberate eval-harness choice, not a change to the agent's default behaviour
(cli.py still prompts interactively by default).

Run: python -m evals.end_to_end.run_eval
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import yaml
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from agent import mcp_client
from agent.graph import build_graph
from common.es_client import get_es_client

ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_PATH = ROOT / "evals" / "results" / "e2e_eval.json"


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _load_tasks(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text())
    return data.get("tasks", []) if data else []


def _is_filled(task: dict[str, Any]) -> bool:
    alert = task.get("alert", {})
    return bool(
        all(alert.get(k) for k in ("service", "severity", "metric", "message"))
        and task.get("expected_runbook_id")
    )


def run_task(task: dict[str, Any]) -> dict[str, Any]:
    run_id = str(uuid.uuid4())[:8]
    mcp_client.reset_call_log()
    graph = build_graph()
    config: RunnableConfig = {"configurable": {"thread_id": run_id}}
    initial_state = {
        "run_id": run_id,
        "alert": task["alert"],
        "token_usage": {"input_tokens": 0, "output_tokens": 0},
    }

    t0 = time.time()
    for update in graph.stream(initial_state, config, stream_mode="updates"):
        if "__interrupt__" in update:
            break
    for _ in graph.stream(Command(resume={"approved": True}), config, stream_mode="updates"):
        pass
    elapsed_s = time.time() - t0

    final_state = graph.get_state(config).values
    actual_runbook_id = final_state.get("runbook_id")
    expected_runbook_id = task["expected_runbook_id"]
    task_success = actual_runbook_id == expected_runbook_id

    expected_tools = task.get("expected_tool_sequence", [])
    actual_tools = mcp_client.get_call_log()
    tool_hits = sum(1 for t in expected_tools if t in actual_tools)
    tool_accuracy = tool_hits / len(expected_tools) if expected_tools else 1.0

    usage = final_state.get("token_usage", {"input_tokens": 0, "output_tokens": 0})
    return {
        "id": task["id"],
        "task_success": task_success,
        "expected_runbook_id": expected_runbook_id,
        "actual_runbook_id": actual_runbook_id,
        "tool_selection_accuracy": tool_accuracy,
        "actual_tools": actual_tools,
        "tokens": usage["input_tokens"] + usage["output_tokens"],
        "latency_s": elapsed_s,
    }


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(int(round(p / 100 * (len(s) - 1))), len(s) - 1)
    return s[idx]


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {}
    return {
        "n_tasks": len(results),
        "task_success_rate": sum(r["task_success"] for r in results) / len(results),
        "tool_selection_accuracy": sum(r["tool_selection_accuracy"] for r in results) / len(results),
        "mean_tokens_per_run": sum(r["tokens"] for r in results) / len(results),
        "p95_latency_s": _percentile([r["latency_s"] for r in results], 95),
    }


def main() -> int:
    generated_tasks = _load_tasks(ROOT / "evals" / "end_to_end" / "golden_set_generated.yaml")
    human_tasks_raw = _load_tasks(ROOT / "evals" / "end_to_end" / "golden_set_human.yaml")
    human_tasks = [t for t in human_tasks_raw if _is_filled(t)]
    human_pending = len(human_tasks) < len(human_tasks_raw)

    print(f"running {len(generated_tasks)} generated tasks...")
    generated_results = [run_task(t) for t in generated_tasks]

    if human_pending:
        print(
            f"human golden set: {len(human_tasks)}/{len(human_tasks_raw)} stubs filled in -- "
            "skipping, subset PENDING"
        )
        human_results: list[dict[str, Any]] = []
    else:
        print(f"running {len(human_tasks)} human-authored tasks...")
        human_results = [run_task(t) for t in human_tasks]

    output: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "git_sha": _git_sha(),
        "subsets": {
            "generated": {
                "status": "complete",
                "summary": _summarize(generated_results),
                "tasks": generated_results,
            },
            "human": {
                "status": "PENDING" if human_pending else "complete",
                "summary": _summarize(human_results) if not human_pending else None,
                "tasks": human_results,
            },
        },
    }
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(output, indent=2))
    print(f"wrote {RESULTS_PATH}")

    es = get_es_client()
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    for subset_name, subset in output["subsets"].items():
        if not subset["summary"]:
            continue
        for metric_name, value in subset["summary"].items():
            if metric_name == "n_tasks":
                continue
            es.index(
                index="ops-agent-evals",
                document={
                    "run_id": output["generated_at"],
                    "eval_type": "e2e",
                    "subset": subset_name,
                    "metric": metric_name,
                    "score": value,
                    "git_sha": output["git_sha"],
                    "@timestamp": now,
                },
            )
    es.indices.refresh(index="ops-agent-evals")

    print("\n| subset    | status   | n  | task success | tool acc | mean tokens | p95 latency (s) |")
    print("|-----------|----------|----|--------------| ---------|-------------|------------------|")
    for name, subset in output["subsets"].items():
        s = subset["summary"] or {}
        status = subset["status"]
        if s:
            print(
                f"| {name:<9} | {status:<8} | {s['n_tasks']:<2} | {s['task_success_rate']:.3f}        |"
                f" {s['tool_selection_accuracy']:.3f}    | {s['mean_tokens_per_run']:.0f}         |"
                f" {s['p95_latency_s']:.2f}             |"
            )
        else:
            print(f"| {name:<9} | {status:<8} | -  | -            | -        | -           | -   |")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
