"""U6 tests: Claude summarizer (SDK mocked)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.summarizer.claude import ClaudeSummarizer

pytestmark = pytest.mark.asyncio


class FakeMessages:
    def __init__(self, *, text: str | None = None, exc: Exception | None = None):
        self._text = text
        self._exc = exc
        self.last_kwargs: dict | None = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._exc is not None:
            raise self._exc
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._text)]
        )


class FakeClient:
    def __init__(self, *, text=None, exc=None):
        self.messages = FakeMessages(text=text, exc=exc)


def _summarizer(text=None, exc=None) -> ClaudeSummarizer:
    return ClaudeSummarizer("key", "claude-haiku-4-5", client=FakeClient(text=text, exc=exc))


async def test_returns_model_text_and_asserts_prompt_shape() -> None:
    summarizer = _summarizer(text="Focused on backend caching and a new API endpoint.")
    result = await summarizer.summarize(
        repo_description="Analytics platform",
        commit_messages=["Add IV surface endpoint", "Refactor GEX cache"],
        commit_count=2,
    )
    assert result == "Focused on backend caching and a new API endpoint."

    kwargs = summarizer._client.messages.last_kwargs
    assert kwargs["model"] == "claude-haiku-4-5"
    prompt = kwargs["messages"][0]["content"]
    assert "Add IV surface endpoint" in prompt
    assert "Refactor GEX cache" in prompt
    assert "Total commits today: 2" in prompt


async def test_api_failure_returns_safe_fallback() -> None:
    summarizer = _summarizer(exc=RuntimeError("anthropic down"))
    result = await summarizer.summarize(
        repo_description="x",
        commit_messages=["a", "b", "c"],
        commit_count=3,
    )
    assert result == "3 commits."  # fallback, no raise


async def test_empty_response_uses_fallback() -> None:
    summarizer = _summarizer(text="   ")
    result = await summarizer.summarize(
        repo_description="x", commit_messages=["a"], commit_count=1
    )
    assert result == "1 commit."


async def test_empty_commits_raises_guard() -> None:
    summarizer = _summarizer(text="unused")
    with pytest.raises(ValueError):
        await summarizer.summarize(
            repo_description="x", commit_messages=[], commit_count=0
        )


async def test_large_commit_list_is_truncated_in_prompt() -> None:
    summarizer = _summarizer(text="ok")
    messages = [f"commit number {i}" for i in range(120)]
    await summarizer.summarize(
        repo_description="big repo", commit_messages=messages, commit_count=120
    )
    prompt = summarizer._client.messages.last_kwargs["messages"][0]["content"]
    assert "commit number 0" in prompt
    assert "commit number 119" not in prompt  # beyond the cap
    assert "+70 more commits" in prompt  # 120 - 50 cap
