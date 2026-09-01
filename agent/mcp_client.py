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


def call_tool(name: str, **kwargs: Any) -> Any:
    async def _call() -> Any:
        async with _client:
            result = await _client.call_tool(name, kwargs)
            return result.data

    with start_tool_span(name, kwargs):
        return asyncio.run(_call())
