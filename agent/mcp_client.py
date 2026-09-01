"""In-process MCP client for the agent: calls the same FastMCP server object (mcp_server.server)
through the real MCP protocol via fastmcp's in-memory transport (no subprocess), so the agent
exercises the identical tool-call path an external MCP client (Inspector, Claude Desktop) would."""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Client

from agent.telemetry import start_tool_span
from mcp_server.server import mcp

_client = Client(mcp)

# Simple call log for the e2e eval's tool-selection-accuracy metric (evals/end_to_end/run_eval.py).
# Eval runs are sequential, not concurrent, so a module-level list (reset between tasks) is
# enough -- no need for the contextvar machinery set_current_api_key uses for cross-user reuse.
_call_log: list[str] = []


def reset_call_log() -> None:
    _call_log.clear()


def get_call_log() -> list[str]:
    return list(_call_log)


def call_tool(name: str, **kwargs: Any) -> Any:
    async def _call() -> Any:
        async with _client:
            result = await _client.call_tool(name, kwargs)
            return result.data

    _call_log.append(name)
    with start_tool_span(name, kwargs):
        return asyncio.run(_call())
