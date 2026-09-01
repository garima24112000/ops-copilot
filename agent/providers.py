"""Concrete Provider implementations for agent/llm_router.py: Gemini (Google AI Studio),
Groq, and local Ollama. Each exposes the same chat(model, messages, tools) -> ChatResult
interface so LLMRouter can fail over between them transparently."""

from __future__ import annotations

import json
from typing import Any, cast

from agent.llm_router import ChatResult, Message, RateLimitError, ToolCall, ToolSpec
from common.config import env


def _split_system(messages: list[Message]) -> tuple[str | None, list[Message]]:
    system = None
    rest = []
    for m in messages:
        if m["role"] == "system" and system is None:
            system = m["content"]
        else:
            rest.append(m)
    return system, rest


class GeminiProvider:
    name = "gemini"
    default_model = "gemini-flash-latest"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or env("GOOGLE_API_KEY")

    def chat(self, model: str, messages: list[Message], tools: list[ToolSpec] | None) -> ChatResult:
        if not self.api_key:
            raise RuntimeError("GOOGLE_API_KEY not set")
        from google import genai
        from google.genai import types
        from google.genai.errors import ClientError

        client = genai.Client(api_key=self.api_key)
        system, rest = _split_system(messages)
        contents = [
            types.Content(
                role="model" if m["role"] == "assistant" else "user",
                parts=[types.Part(text=m["content"])],
            )
            for m in rest
        ]
        config_kwargs: dict[str, Any] = {}
        if system:
            config_kwargs["system_instruction"] = system
        if tools:
            declarations = [
                types.FunctionDeclaration(
                    name=t["name"], description=t.get("description", ""), parameters=t.get("parameters", {})
                )
                for t in tools
            ]
            config_kwargs["tools"] = [types.Tool(function_declarations=declarations)]

        try:
            resp = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs) if config_kwargs else None,
            )
        except ClientError as exc:
            if getattr(exc, "code", None) == 429:
                raise RateLimitError(str(exc)) from exc
            raise

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        candidate = resp.candidates[0] if resp.candidates else None
        if candidate and candidate.content and candidate.content.parts:
            for part in candidate.content.parts:
                part_text = getattr(part, "text", None)
                if part_text:
                    text_parts.append(part_text)
                fc = getattr(part, "function_call", None)
                if fc:
                    tool_calls.append(ToolCall(name=fc.name, arguments=dict(fc.args or {})))

        usage = resp.usage_metadata
        return ChatResult(
            content="".join(text_parts),
            tool_calls=tool_calls,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            provider=self.name,
            model=model,
        )


class GroqProvider:
    name = "groq"
    default_model = "openai/gpt-oss-20b"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or env("GROQ_API_KEY")

    def chat(self, model: str, messages: list[Message], tools: list[ToolSpec] | None) -> ChatResult:
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY not set")
        from groq import Groq
        from groq import RateLimitError as GroqRateLimitError

        client = Groq(api_key=self.api_key)
        groq_tools = None
        if tools:
            groq_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {}),
                    },
                }
                for t in tools
            ]
        try:
            # messages/tools are plain dicts matching the OpenAI-compatible wire format Groq
            # expects; cast rather than reconstructing the SDK's TypedDict unions here.
            resp = client.chat.completions.create(
                model=model, messages=cast(Any, messages), tools=cast(Any, groq_tools)
            )
        except GroqRateLimitError as exc:
            retry_after = None
            headers = getattr(getattr(exc, "response", None), "headers", None)
            if headers:
                retry_after = headers.get("retry-after")
            raise RateLimitError(str(exc), retry_after_s=float(retry_after) if retry_after else None) from exc

        choice = resp.choices[0]
        tool_calls = [
            ToolCall(name=tc.function.name, arguments=json.loads(tc.function.arguments or "{}"))
            for tc in (choice.message.tool_calls or [])
        ]
        return ChatResult(
            content=choice.message.content or "",
            tool_calls=tool_calls,
            input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            output_tokens=resp.usage.completion_tokens if resp.usage else 0,
            provider=self.name,
            model=model,
        )


class OllamaProvider:
    name = "ollama"
    default_model = "qwen3:4b"

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = base_url or env("OLLAMA_BASE_URL", "http://localhost:11434")

    def chat(self, model: str, messages: list[Message], tools: list[ToolSpec] | None) -> ChatResult:
        import ollama

        client = ollama.Client(host=self.base_url)
        ollama_tools = None
        if tools:
            ollama_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {}),
                    },
                }
                for t in tools
            ]
        resp = client.chat(model=model, messages=messages, tools=ollama_tools)
        message = resp["message"]
        tool_calls = [
            ToolCall(name=tc["function"]["name"], arguments=dict(tc["function"].get("arguments", {})))
            for tc in (message.get("tool_calls") or [])
        ]
        return ChatResult(
            content=message.get("content", "") or "",
            tool_calls=tool_calls,
            input_tokens=resp.get("prompt_eval_count", 0) or 0,
            output_tokens=resp.get("eval_count", 0) or 0,
            provider=self.name,
            model=model,
        )


def default_providers() -> list[Any]:
    """Reasoning provider chain: fast hosted first, local Ollama as the always-available
    last resort (no rate limit, no card, but slower and weaker at tool selection)."""
    return [GeminiProvider(), GroqProvider(), OllamaProvider()]
