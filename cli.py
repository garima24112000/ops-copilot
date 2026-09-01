"""CLI runner: takes an alert JSON path, runs it through the ops-copilot LangGraph agent,
prints each node transition, pauses for human approval before any side-effecting tool, and
prints the final postmortem id + token usage.

Usage:
    python cli.py data/sample_alert.json
    python cli.py data/sample_alert.json --approve   # auto-approve, for scripted/eval runs
    python cli.py data/sample_alert.json --reject     # auto-reject, for scripted/eval runs
    python cli.py data/sample_alert.json --user alice  # run under alice's DLS-scoped API key
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from agent.graph import build_graph
from agent.telemetry import setup_telemetry, start_agent_span
from common.es_client import set_current_api_key


def _print_update(update: dict[str, Any]) -> None:
    for node_name, node_output in update.items():
        if node_name == "__interrupt__":
            continue
        print(f"\n== node: {node_name} ==")
        for k, v in (node_output or {}).items():
            text = json.dumps(v, default=str) if not isinstance(v, str) else v
            if len(text) > 300:
                text = text[:300] + "..."
            print(f"  {k}: {text}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("alert_path")
    parser.add_argument("--approve", action="store_true", help="auto-approve the proposed action")
    parser.add_argument("--reject", action="store_true", help="auto-reject the proposed action")
    parser.add_argument("--user", help="run under this demo user's DLS-scoped API key (security/dls.py)")
    args = parser.parse_args()

    if args.user:
        from security.dls import get_or_mint_user_api_key

        set_current_api_key(get_or_mint_user_api_key(args.user))
        print(f"running as user {args.user!r} (document-level security scoped to their department)")

    alert = json.loads(open(args.alert_path).read())
    run_id = str(uuid.uuid4())[:8]
    initial_state = {
        "run_id": run_id,
        "alert": alert,
        "token_usage": {"input_tokens": 0, "output_tokens": 0},
    }
    config: RunnableConfig = {"configurable": {"thread_id": run_id}}

    graph = build_graph()
    setup_telemetry()

    print(f"=== run {run_id}: {alert.get('service')} / {alert.get('severity')} — {alert.get('message')} ===")
    t0 = time.time()
    with start_agent_span("ops-copilot-triage", run_id):
        interrupted_value = None
        for update in graph.stream(initial_state, config, stream_mode="updates"):
            if "__interrupt__" in update:
                interrupted_value = update["__interrupt__"][0].value
                break
            _print_update(update)

        if interrupted_value is not None:
            print("\n== APPROVAL REQUIRED ==")
            print(json.dumps(interrupted_value, indent=2, default=str))
            if args.approve:
                approved = True
            elif args.reject:
                approved = False
            else:
                answer = input("Approve this action? [y/N] ").strip().lower()
                approved = answer == "y"

            for update in graph.stream(Command(resume={"approved": approved}), config, stream_mode="updates"):
                _print_update(update)

    final_state = graph.get_state(config).values
    elapsed = time.time() - t0
    print("\n=== run complete ===")
    print(f"postmortem_id: {final_state.get('postmortem_id')}")
    print(f"token_usage:   {final_state.get('token_usage')}")
    print(f"elapsed_s:     {elapsed:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
