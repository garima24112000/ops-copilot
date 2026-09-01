"""OpenTelemetry GenAI semantic-convention instrumentation, exporting OTLP/HTTP to the local
APM Server. Attribute names verified against the current (2026, Development-status)
open-telemetry/semantic-conventions-genai spec, not assumed (CLAUDE.md rule 10):
gen_ai.operation.name, gen_ai.provider.name, gen_ai.request.model, gen_ai.response.model,
gen_ai.usage.input_tokens, gen_ai.usage.output_tokens, gen_ai.agent.name are all current keys.
gen_ai.tool.name is used for execute_tool spans per the broader gen_ai.tool.* namespace in the
same spec family (agent/tool-orchestration conventions are explicitly the least stable part of
the spec -- flagged here so a future drift is easy to find).

invoke_agent is the root span per run; chat is a child span per LLM call; execute_tool is a
child span per MCP tool call.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

from common.config import env

_initialized = False


def setup_telemetry(service_name: str = "ops-copilot-agent") -> None:
    global _initialized
    if _initialized:
        return
    apm_url = (env("APM_SERVER_URL", "http://localhost:8200") or "http://localhost:8200").rstrip("/")
    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{apm_url}/v1/traces")))
    trace.set_tracer_provider(provider)
    _initialized = True


def _tracer() -> trace.Tracer:
    return trace.get_tracer("ops-copilot")


@contextmanager
def start_agent_span(agent_name: str, run_id: str) -> Iterator[Span]:
    with _tracer().start_as_current_span(f"invoke_agent {agent_name}") as span:
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("gen_ai.provider.name", "ops-copilot")
        span.set_attribute("gen_ai.agent.name", agent_name)
        span.set_attribute("ops_copilot.run_id", run_id)
        yield span


@contextmanager
def start_chat_span(provider: str, model: str) -> Iterator[Span]:
    with _tracer().start_as_current_span(f"chat {model}") as span:
        span.set_attribute("gen_ai.operation.name", "chat")
        span.set_attribute("gen_ai.provider.name", provider)
        span.set_attribute("gen_ai.request.model", model)
        try:
            yield span
        except Exception as exc:  # noqa: BLE001
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.set_attribute("error.type", type(exc).__name__)
            raise


def record_chat_result(span: Span, response_model: str, input_tokens: int, output_tokens: int) -> None:
    span.set_attribute("gen_ai.response.model", response_model)
    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)


@contextmanager
def start_tool_span(tool_name: str, args: dict[str, Any] | None = None) -> Iterator[Span]:
    with _tracer().start_as_current_span(f"execute_tool {tool_name}") as span:
        span.set_attribute("gen_ai.operation.name", "execute_tool")
        span.set_attribute("gen_ai.tool.name", tool_name)
        if args:
            span.set_attribute("gen_ai.tool.call.arguments", str(args)[:500])
        try:
            yield span
        except Exception as exc:  # noqa: BLE001
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.set_attribute("error.type", type(exc).__name__)
            raise
