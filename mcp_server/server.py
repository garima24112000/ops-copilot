"""Standalone FastMCP server exposing five tools over the ops-copilot Elasticsearch indices:
search_runbooks, find_similar_incidents, query_service_health (read-only), and create_ticket,
restart_service (side-effecting -- annotated destructiveHint/readOnlyHint=False so any MCP
client, including this project's own LangGraph agent, treats them as requiring approval).

Standalone on purpose (not LangChain tools): the same server can be pointed at from any MCP
client (Claude Code, MCP Inspector, Claude Desktop) with zero code changes -- see the README
section this session adds on connecting another client.

Run directly for MCP Inspector / stdio clients:  python -m mcp_server.server
Run over HTTP for the LangGraph agent to connect to over the network:
    python -m mcp_server.server --http --port 8765
"""

from __future__ import annotations

import argparse
from typing import Any

from fastmcp import FastMCP

from common.es_client import get_es_client
from mcp_server.tools.actions import create_ticket as _create_ticket
from mcp_server.tools.actions import restart_service as _restart_service
from mcp_server.tools.health import query_service_health as _query_service_health
from mcp_server.tools.incidents import find_similar_incidents as _find_similar_incidents
from mcp_server.tools.runbooks import search_runbooks as _search_runbooks

mcp = FastMCP("ops-copilot")

# A fresh client per call (not a fixed module-level singleton) so a run under --user (see
# security/dls.py, cli.py) -- which sets a contextvar before invoking the graph -- is honoured:
# the same process can serve one user's DLS-scoped requests and then another's.


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def search_runbooks(query: str, service: str | None = None) -> list[dict[str, Any]]:
    """Hybrid BM25 + ELSER (RRF) search over ops-runbooks. Returns the top-3 runbooks as
    truncated snippets: id, title, snippet, service, department, source_url, score."""
    return _search_runbooks(get_es_client(), query=query, service=service)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def find_similar_incidents(summary: str, service: str | None = None) -> list[dict[str, Any]]:
    """Semantic search over ops-incidents for past incidents similar to the given summary.
    Returns the top-3 matches: id, title, summary, resolution, service, severity, score."""
    return _find_similar_incidents(get_es_client(), summary=summary, service=service)


@mcp.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def query_service_health(service: str, window: str = "1h") -> dict[str, Any]:
    """ES|QL aggregation over ops-logs-*: log line counts by level for `service` within the
    trailing `window` (e.g. '15m', '1h', '2d'). Returns total_lines, by_level, error_rate."""
    return _query_service_health(get_es_client(), service=service, window=window)


@mcp.tool(
    annotations={"readOnlyHint": False, "destructiveHint": True, "title": "Create ticket (requires approval)"}
)
def create_ticket(title: str, body: str, severity: str) -> dict[str, Any]:
    """REQUIRES HUMAN APPROVAL. Files a ticket in the (mock) internal ITSM system. Returns
    ticket_id, status, created_at."""
    return _create_ticket(title=title, body=body, severity=severity)


@mcp.tool(
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "title": "Restart service (requires approval)",
    }
)
def restart_service(service: str) -> dict[str, Any]:
    """REQUIRES HUMAN APPROVAL. Restarts a service via the (mock) internal ops API. Returns
    restart_id, status, at."""
    return _restart_service(service=service)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true", help="serve over streamable HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.http:
        mcp.run(transport="http", port=args.port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
