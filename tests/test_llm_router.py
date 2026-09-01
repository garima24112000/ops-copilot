from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent.llm_router import ChatResult, LLMRouter, Message, ProviderError, RateLimitError, ToolCall, ToolSpec


class FakeProvider:
    """A test double that counts calls and can be told to rate-limit or fail."""

    def __init__(self, name: str, model: str = "fake-model", rate_limited: bool = False) -> None:
        self.name = name
        self.default_model = model
        self.rate_limited = rate_limited
        self.calls = 0

    def chat(self, model: str, messages: list[Message], tools: list[ToolSpec] | None) -> ChatResult:
        self.calls += 1
        if self.rate_limited:
            raise RateLimitError(f"{self.name} is rate limited", retry_after_s=9999)
        return ChatResult(
            content=f"response from {self.name}",
            tool_calls=[ToolCall(name="noop", arguments={})],
            input_tokens=10,
            output_tokens=5,
            provider=self.name,
            model=model,
        )


def _router(tmp_path: Path, providers: list[Any]) -> LLMRouter:
    return LLMRouter(
        providers,
        mode="live",
        cache_dir=tmp_path / "cache",
        fixtures_dir=tmp_path / "fixtures",
    )


def test_second_identical_call_is_a_cache_hit(tmp_path: Path) -> None:
    provider = FakeProvider("only")
    router = _router(tmp_path, [provider])
    messages = [{"role": "user", "content": "hello"}]

    first = router.chat(messages)
    second = router.chat(messages)

    assert provider.calls == 1  # second call served from disk cache, no provider hit
    assert first.cached is False
    assert second.cached is True
    assert first.content == second.content


def test_rate_limited_provider_fails_over_to_next(tmp_path: Path) -> None:
    gemini = FakeProvider("gemini", rate_limited=True)
    groq = FakeProvider("groq")
    router = _router(tmp_path, [gemini, groq])

    result = router.chat([{"role": "user", "content": "hi"}])

    assert result.provider == "groq"
    assert gemini.calls == 1
    assert groq.calls == 1


def test_rate_limited_provider_stays_cooled_down_for_subsequent_distinct_calls(tmp_path: Path) -> None:
    gemini = FakeProvider("gemini", rate_limited=True)
    groq = FakeProvider("groq")
    router = _router(tmp_path, [gemini, groq])

    router.chat([{"role": "user", "content": "hi"}])
    router.chat([{"role": "user", "content": "a different message"}])

    assert gemini.calls == 1  # not retried once cooled down
    assert groq.calls == 2


def test_all_providers_rate_limited_raises(tmp_path: Path) -> None:
    router = _router(
        tmp_path, [FakeProvider("a", rate_limited=True), FakeProvider("b", rate_limited=True)]
    )
    with pytest.raises(ProviderError):
        router.chat([{"role": "user", "content": "hi"}])


def test_replay_mode_never_calls_a_provider_and_uses_fixtures(tmp_path: Path) -> None:
    fixtures_dir = tmp_path / "fixtures"
    live_provider = FakeProvider("groq")
    recorder = _router(tmp_path, [live_provider])
    recorder.mode = "record"
    messages = [{"role": "user", "content": "record me"}]
    recorder.chat(messages)
    assert live_provider.calls == 1

    never_called = FakeProvider("groq")
    replay_router = LLMRouter(
        [never_called], mode="replay", cache_dir=tmp_path / "unused_cache", fixtures_dir=fixtures_dir
    )
    result = replay_router.chat(messages)

    assert never_called.calls == 0
    assert result.cached is True
    assert result.content == "response from groq"


def test_replay_mode_raises_on_missing_fixture(tmp_path: Path) -> None:
    router = LLMRouter(
        [FakeProvider("groq")],
        mode="replay",
        cache_dir=tmp_path / "cache",
        fixtures_dir=tmp_path / "fixtures",
    )
    with pytest.raises(ProviderError):
        router.chat([{"role": "user", "content": "never recorded"}])
