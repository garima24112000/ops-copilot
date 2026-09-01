"""LangGraph state for the ops-copilot incident-triage agent."""

from __future__ import annotations

from typing import Any, TypedDict


class Alert(TypedDict):
    service: str
    severity: str
    metric: str
    message: str


class TokenUsage(TypedDict):
    input_tokens: int
    output_tokens: int


class ProposedAction(TypedDict):
    tool: str  # "create_ticket" | "restart_service"
    args: dict[str, Any]
    rationale: str


class AgentState(TypedDict, total=False):
    run_id: str
    alert: Alert

    # triage
    triaged_service: str
    triaged_severity: str

    # ground
    runbook_id: str | None
    runbook_title: str | None
    runbook_snippet: str | None
    runbook_source_url: str | None

    # diagnose
    similar_incidents: list[dict[str, Any]]
    service_health: dict[str, Any]
    hypothesis: str
    diagnosis_confirmed: bool

    # approve / act
    proposed_action: ProposedAction | None
    approved: bool | None
    action_result: dict[str, Any] | None

    # record
    postmortem_id: str | None

    token_usage: TokenUsage
