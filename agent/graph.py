"""The LangGraph state machine: triage -> ground -> diagnose -> approve -> act -> record.
interrupt() inside approve() pauses before any side-effecting tool call."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from agent import nodes
from agent.state import AgentState


def build_graph() -> CompiledStateGraph[AgentState, Any, Any, Any]:
    graph = StateGraph(AgentState)
    graph.add_node("triage", nodes.triage)
    graph.add_node("ground", nodes.ground)
    graph.add_node("diagnose", nodes.diagnose)
    graph.add_node("approve", nodes.approve)
    graph.add_node("act", nodes.act)
    graph.add_node("record", nodes.record)

    graph.set_entry_point("triage")
    graph.add_edge("triage", "ground")
    graph.add_edge("ground", "diagnose")
    graph.add_edge("diagnose", "approve")
    graph.add_edge("approve", "act")
    graph.add_edge("act", "record")
    graph.add_edge("record", END)

    return graph.compile(checkpointer=MemorySaver())
