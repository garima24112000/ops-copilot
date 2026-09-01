"""The six nodes of the ops-copilot graph: triage -> ground -> diagnose -> approve -> act ->
record. Every LLM call goes through agent.llm_router (CLAUDE.md rule). Retrieved context is
truncated at the source (mcp_server/tools/*) rather than here -- nodes just consume already-
truncated snippets, keeping total context per run under the ~4-5K token budget.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from langgraph.types import interrupt

from agent import mcp_client
from agent.llm_router import ChatResult, LLMRouter
from agent.providers import default_providers
from agent.state import AgentState
from agent.telemetry import record_chat_result, start_chat_span
from common.es_client import get_es_client

_router: LLMRouter | None = None


def get_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter(default_providers())
    return _router


def _chat(prompt: str) -> ChatResult:
    """chat() wrapped in an execute_tool-sibling `chat` OTel span (session 11)."""
    router = get_router()
    # the router may fail over between providers; open the span against the configured
    # first-choice provider/model so it still shows up even if a call fails over downstream
    first = router.providers[0]
    with start_chat_span(first.name, first.default_model) as span:
        result = router.chat([{"role": "user", "content": prompt}])
        record_chat_result(span, result.model, result.input_tokens, result.output_tokens)
        span.set_attribute("gen_ai.provider.name", result.provider)
        return result


def _accumulate_tokens(state: AgentState, input_tokens: int, output_tokens: int) -> dict[str, int]:
    usage = state.get("token_usage", {"input_tokens": 0, "output_tokens": 0})
    return {
        "input_tokens": usage["input_tokens"] + input_tokens,
        "output_tokens": usage["output_tokens"] + output_tokens,
    }


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict[str, Any]:
    m = _JSON_RE.search(text)
    if not m:
        raise ValueError(f"no JSON object found in model output: {text!r}")
    parsed: dict[str, Any] = json.loads(m.group(0))
    return parsed


def triage(state: AgentState) -> dict[str, Any]:
    alert = state["alert"]
    prompt = (
        "You triage monitoring alerts for an on-call rotation. Normalize this alert into "
        "a JSON object with exactly two keys: \"service\" (lowercase-kebab-case) and "
        '"severity" (one of: critical, warning, info).\n\n'
        f"ALERT: service={alert['service']!r} severity={alert['severity']!r} "
        f"metric={alert['metric']!r} message={alert['message']!r}\n\n"
        "Respond with ONLY the JSON object."
    )
    result = _chat(prompt)
    try:
        parsed = _extract_json(result.content)
        service = parsed.get("service", alert["service"])
        severity = parsed.get("severity", alert["severity"])
    except (ValueError, json.JSONDecodeError):
        service, severity = alert["service"], alert["severity"]

    return {
        "triaged_service": service,
        "triaged_severity": severity,
        "token_usage": _accumulate_tokens(state, result.input_tokens, result.output_tokens),
    }


def ground(state: AgentState) -> dict[str, Any]:
    alert = state["alert"]
    hits = mcp_client.call_tool(
        "search_runbooks", query=alert["message"], service=state.get("triaged_service")
    )
    if not hits:
        # service filter too narrow / no match -- retry unfiltered
        hits = mcp_client.call_tool("search_runbooks", query=alert["message"])

    if not hits:
        return {
            "runbook_id": None,
            "runbook_title": None,
            "runbook_snippet": None,
            "runbook_source_url": None,
        }
    top = hits[0]
    return {
        "runbook_id": top["id"],
        "runbook_title": top["title"],
        "runbook_snippet": top["snippet"],
        "runbook_source_url": top["source_url"],
    }


def diagnose(state: AgentState) -> dict[str, Any]:
    alert = state["alert"]
    service = state.get("triaged_service", alert["service"])

    incidents = mcp_client.call_tool(
        "find_similar_incidents", summary=alert["message"], service=service
    )[:2]
    health = mcp_client.call_tool("query_service_health", service=service, window="1h")

    incidents_text = "\n".join(f"- {i['title']}: {i['resolution'][:150]}" for i in incidents) or "none found"
    prompt = (
        "You are diagnosing a live incident. Given the candidate runbook, recent telemetry, "
        "and similar past incidents, state a one-sentence hypothesis for the root cause and "
        "whether telemetry CONFIRMS or REFUTES the runbook's relevance. Respond as JSON with "
        'keys "hypothesis" (string) and "confirmed" (boolean).\n\n'
        f"RUNBOOK: {state.get('runbook_title')}: {(state.get('runbook_snippet') or '')[:400]}\n\n"
        f"TELEMETRY (last 1h for {service}): total_lines={health['total_lines']} "
        f"error_rate={health['error_rate']:.2f} by_level={health['by_level']}\n\n"
        f"SIMILAR PAST INCIDENTS:\n{incidents_text}\n\n"
        "Respond with ONLY the JSON object."
    )
    result = _chat(prompt)
    try:
        parsed = _extract_json(result.content)
        hypothesis = parsed.get("hypothesis", "unable to parse hypothesis")
        confirmed = bool(parsed.get("confirmed", health["error_rate"] > 0.1))
    except (ValueError, json.JSONDecodeError):
        hypothesis = result.content[:300]
        confirmed = health["error_rate"] > 0.1

    return {
        "similar_incidents": incidents,
        "service_health": health,
        "hypothesis": hypothesis,
        "diagnosis_confirmed": confirmed,
        "token_usage": _accumulate_tokens(state, result.input_tokens, result.output_tokens),
    }


def approve(state: AgentState) -> dict[str, Any]:
    alert = state["alert"]
    severity = state.get("triaged_severity", alert["severity"])
    service = state.get("triaged_service", alert["service"])

    if severity == "critical" and state.get("diagnosis_confirmed"):
        action: dict[str, Any] = {
            "tool": "restart_service",
            "args": {"service": service},
            "rationale": f"critical + confirmed diagnosis: {state.get('hypothesis', '')[:200]}",
        }
    else:
        action = {
            "tool": "create_ticket",
            "args": {
                "title": f"[{severity}] {service}: {alert['metric']}",
                "body": (
                    f"{alert['message']}\n\nHypothesis: {state.get('hypothesis', 'n/a')}\n"
                    f"Runbook: {state.get('runbook_title')} ({state.get('runbook_source_url')})"
                ),
                "severity": severity,
            },
            "rationale": f"non-critical or unconfirmed diagnosis: {state.get('hypothesis', '')[:200]}",
        }

    decision = interrupt(
        {
            "proposed_action": action,
            "runbook": state.get("runbook_title"),
            "hypothesis": state.get("hypothesis"),
            "diagnosis_confirmed": state.get("diagnosis_confirmed"),
        }
    )
    approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)
    return {"proposed_action": action, "approved": approved}


def act(state: AgentState) -> dict[str, Any]:
    action = state.get("proposed_action")
    if not state.get("approved") or not action:
        return {"action_result": {"status": "skipped_not_approved"}}
    result = mcp_client.call_tool(action["tool"], **action["args"])
    return {"action_result": result}


def record(state: AgentState) -> dict[str, Any]:
    es = get_es_client()
    alert = state["alert"]
    postmortem_id = f"pm-{state['run_id']}"
    action = state.get("proposed_action")
    action_taken = action["tool"] if action else "none"
    doc = {
        "id": postmortem_id,
        "alert_id": state["run_id"],
        "run_id": state["run_id"],
        "service": state.get("triaged_service", alert["service"]),
        "severity": state.get("triaged_severity", alert["severity"]),
        "runbook_id": state.get("runbook_id"),
        "hypothesis": state.get("hypothesis", ""),
        "diagnosis": json.dumps(state.get("service_health", {})),
        "action_taken": action_taken,
        "approved": bool(state.get("approved")),
        "summary": (
            f"Alert {alert['message']} on {state.get('triaged_service')} "
            f"({state.get('triaged_severity')}). {state.get('hypothesis', '')} "
            f"Action: {action_taken} "
            f"({'approved' if state.get('approved') else 'not approved'})."
        ),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    }
    doc["summary_semantic"] = doc["summary"]
    es.index(index="ops-postmortems", id=postmortem_id, document=doc, refresh="wait_for")
    return {"postmortem_id": postmortem_id}
