"""create_ticket / restart_service: the two side-effecting tools. Both are flagged
non-read-only in their MCP tool annotations (server.py) and, in the agent graph, are only
ever called after the approve node's human-in-the-loop interrupt() returns approved=True."""

from __future__ import annotations

from typing import Any

from mcp_server import mock_api


def create_ticket(title: str, body: str, severity: str) -> dict[str, Any]:
    return mock_api.create_ticket(title=title, body=body, severity=severity)


def restart_service(service: str) -> dict[str, Any]:
    return mock_api.restart_service(service=service)
