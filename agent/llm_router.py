"""Provider abstraction over Google AI Studio (Gemini), Groq, and local Ollama behind one
chat-with-tools interface.

- Disk cache keyed on a hash of (provider, model, messages, tools) so repeated dev runs and
  CI cost nothing after the first hit.
- Rate-limit-aware failover: providers are tried in order; a 429 (or provider SDK rate-limit
  exception) marks that provider "cooling down" (using the Retry-After header when present,
  else a default backoff) and the router immediately tries the next provider.
- Records per-call input/output token counts on every ChatResult.
- record/replay mode via LLM_ROUTER_MODE: "live" (default) calls providers and caches;
  "record" calls providers, caches, AND writes a frozen fixture; "replay" never calls a
  provider, only reads fixtures/ -- this is what CI uses (zero live API calls).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from common.config import env

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".router_cache"
FIXTURES_DIR = ROOT / "fixtures"

Message = dict[str, Any]
ToolSpec = dict[str, Any]


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatResult:
    content: str
    tool_calls: list[ToolCall]
    input_tokens: int
    output_tokens: int
    provider: str
    model: str
    cached: bool = False


class RateLimitError(Exception):
    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class ProviderError(Exception):
    pass


class Provider(Protocol):
    name: str
    default_model: str

    def chat(self, model: str, messages: list[Message], tools: list[ToolSpec] | None) -> ChatResult: ...


def _cache_key(provider: str, model: str, messages: list[Message], tools: list[ToolSpec] | None) -> str:
    payload = json.dumps(
        {"provider": provider, "model": model, "messages": messages, "tools": tools},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _cache_path(key: str, cache_dir: Path) -> Path:
    return cache_dir / f"{key}.json"


def _read_cache(key: str, cache_dir: Path) -> ChatResult | None:
    path = _cache_path(key, cache_dir)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return ChatResult(
        content=data["content"],
        tool_calls=[ToolCall(**tc) for tc in data["tool_calls"]],
        input_tokens=data["input_tokens"],
        output_tokens=data["output_tokens"],
        provider=data["provider"],
        model=data["model"],
        cached=True,
    )


def _write_cache(key: str, result: ChatResult, cache_dir: Path) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(key, cache_dir)
    path.write_text(
        json.dumps(
            {
                "content": result.content,
                "tool_calls": [{"name": tc.name, "arguments": tc.arguments} for tc in result.tool_calls],
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "provider": result.provider,
                "model": result.model,
            },
            indent=2,
        )
    )


@dataclass
class _Cooldown:
    until: dict[str, float] = field(default_factory=dict)

    def is_cool(self, provider_name: str) -> bool:
        return time.time() < self.until.get(provider_name, 0)

    def mark(self, provider_name: str, retry_after_s: float | None) -> None:
        self.until[provider_name] = time.time() + (retry_after_s or 30.0)


class LLMRouter:
    """Tries providers in order, with a disk cache and rate-limit failover."""

    def __init__(
        self,
        providers: list[Provider],
        mode: str | None = None,
        cache_dir: Path | None = None,
        fixtures_dir: Path | None = None,
    ) -> None:
        if not providers:
            raise ValueError("LLMRouter needs at least one provider")
        self.providers = providers
        self.mode = mode or env("LLM_ROUTER_MODE", "live") or "live"
        self.cache_dir = cache_dir or CACHE_DIR
        self.fixtures_dir = fixtures_dir or FIXTURES_DIR
        self.cooldown = _Cooldown()

    def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
    ) -> ChatResult:
        last_error: Exception | None = None
        for provider in self.providers:
            provider_model = model or provider.default_model
            key = _cache_key(provider.name, provider_model, messages, tools)

            cached = _read_cache(key, self.fixtures_dir) or _read_cache(key, self.cache_dir)
            if cached is not None:
                return cached

            if self.mode == "replay":
                last_error = ProviderError(
                    f"replay mode: no fixture for provider={provider.name} model={provider_model} key={key}"
                )
                continue

            if self.cooldown.is_cool(provider.name):
                continue

            try:
                result = provider.chat(provider_model, messages, tools)
            except RateLimitError as exc:
                self.cooldown.mark(provider.name, exc.retry_after_s)
                last_error = exc
                continue
            except Exception as exc:  # noqa: BLE001 - any provider failure should fail over
                last_error = exc
                continue

            _write_cache(key, result, self.cache_dir)
            if self.mode == "record":
                _write_cache(key, result, self.fixtures_dir)
            return result

        raise ProviderError(f"all providers exhausted or rate-limited: {last_error}")
