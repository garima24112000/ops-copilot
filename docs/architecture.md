# Architecture

Ops Copilot has three planes that share almost no code but the same Elasticsearch cluster:
a **control plane** (LangGraph, deciding what to do next), a **data plane** (Elasticsearch,
holding runbooks/incidents/logs/postmortems), and a **telemetry plane** (OpenTelemetry ->
APM Server, watching the control plane work). The pitch is that Elasticsearch is both the
agent's brain/memory *and* its observability backend — you debug the agent using the same
stack the agent runs on.

```mermaid
flowchart TB
    subgraph control["Control plane — LangGraph (agent/)"]
        direction LR
        triage[triage] --> ground[ground] --> diagnose[diagnose] --> approve["approve\n(interrupt)"] --> act[act] --> record[record]
    end

    subgraph data["Data plane — Elasticsearch"]
        direction LR
        runbooks[(ops-runbooks\nBM25 + ELSER + dense)]
        incidents[(ops-incidents)]
        logs[(ops-logs-*\nLoghub, ES|QL)]
        postmortems[(ops-postmortems)]
        evals[(ops-agent-evals)]
    end

    subgraph telemetry["Telemetry plane — OTel -> APM Server -> Kibana"]
        direction LR
        invoke_span["invoke_agent span"] --> chat_span["chat spans\n(tokens, provider, model)"]
        invoke_span --> tool_span["execute_tool spans"]
    end

    ground -- "search_runbooks\n(MCP)" --> runbooks
    diagnose -- "find_similar_incidents\n(MCP)" --> incidents
    diagnose -- "query_service_health\n(MCP, ES|QL)" --> logs
    act -- "create_ticket / restart_service\n(MCP, mock API — after approval only)" --> mock[["mock ITSM /\nops API"]]
    record -- writes postmortem --> postmortems

    control -. "every LLM call + tool call" .-> telemetry
    telemetry -. "traces indexed as\napm-*/traces-*" .-> data
```

## Control plane: the loop

`agent/graph.py` compiles a `StateGraph` (LangGraph) with six nodes and a `MemorySaver`
checkpointer:

1. **triage** — one LLM call (`agent/llm_router.py`) normalizes the raw alert's `service` and
   `severity` into a consistent shape.
2. **ground** — calls the MCP `search_runbooks` tool: hybrid BM25 + ELSER retrieval via
   Elasticsearch's `rrf` retriever, returns the top-3 runbooks as *truncated* snippets (this is
   the main lever on the token budget — full documents never enter the prompt).
3. **diagnose** — calls `find_similar_incidents` and `query_service_health` (an ES|QL
   aggregation over `ops-logs-*`), then one more LLM call to state a hypothesis and whether
   telemetry confirms or refutes the candidate runbook.
4. **approve** — proposes `restart_service` (confirmed-critical) or `create_ticket` (everything
   else), then calls LangGraph's `interrupt()` and pauses. This is the human-in-the-loop gate:
   nothing with a side effect happens without it.
5. **act** — if approved, calls the proposed tool (mock ITSM/ops API); if not, records that it
   was skipped.
6. **record** — writes a postmortem back into `ops-postmortems`, closing the loop: the next
   `find_similar_incidents` call can retrieve *this* run's outcome.

Every LLM call in this path goes through `agent/llm_router.py` (disk cache + rate-limit
failover across Gemini/Groq/Ollama) — never a provider SDK directly. Every tool call goes
through `agent/mcp_client.py`, an in-process MCP client hitting the *same* FastMCP server
(`mcp_server/server.py`) an external MCP client (Inspector, Claude Desktop) could also connect
to — the tools are not LangChain-specific glue.

## Data plane: three retrieval representations, one memory loop

`ops-runbooks` and `ops-incidents` carry the same text in three parallel fields — `body` (BM25),
`body_semantic` (ELSER `semantic_text`), `body_dense` (precomputed `bge-small-en-v1.5` vectors)
— specifically so `evals/retrieval/run_ablation.py` can compare all four retrieval strategies
(BM25-only, dense-only, ELSER-only, hybrid RRF) against the same corpus. `ops-logs-*` is a data
stream of real Loghub log lines, queried via ES|QL rather than a bespoke aggregation DSL.
`ops-postmortems` is the write side of the memory loop described above. `ops-agent-evals` holds
every eval score (retrieval ablation, CI ablation, end-to-end) indexed with its git SHA, so the
numbers in the README are traceable to a specific commit, not just a file on disk.

## Telemetry plane: the same stack, watching itself

`agent/telemetry.py` wraps the control plane in OpenTelemetry GenAI semantic-convention spans:
one `invoke_agent` root span per `cli.py` run, one `chat` child span per LLM call (carrying
`gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` / `gen_ai.provider.name`), one
`execute_tool` child span per MCP tool call. These export via OTLP/HTTP to the same APM Server
that's part of `docker-compose.yml`, and land in the same Elasticsearch cluster the agent reads
from — so a Kibana dashboard built against APM data is querying the identical stack the agent
itself queries for retrieval. See `dashboards/` (session 12) for the exported saved objects.

## Why three planes, not one graph

Keeping the planes separate — the graph doesn't know about spans, the spans don't know about
retrieval strategy, the retrieval mappings don't know about the agent — is what makes each one
independently testable: `evals/retrieval/run_ablation.py` never imports `agent/`, the CI
retrieval eval never touches an LLM, and a trace in APM is legible without needing to read
`agent/nodes.py` first. That separation is also what let this project degrade gracefully when
one plane hit a real problem (session 5's ELSER throughput issue, documented in full in
`PROGRESS.md`) without the other two planes' code needing to change at all.
