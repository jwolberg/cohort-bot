"""Claude-backed commit summarizer.

Turns a repo's commit messages + description into one concise paragraph for the
daily digest. Uses the Anthropic SDK with ``claude-haiku-4-5`` by default
(cost-efficient; overridable by config for richer digests). Prompt size is
bounded so a busy repo can't blow up the request, and any API failure degrades
to a safe fallback string so one bad summary never sinks the whole digest.
"""

from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic

from app.config import Settings
from app.logging import get_logger

logger = get_logger(__name__)

# Keep the prompt bounded regardless of how busy a repo is.
MAX_COMMITS_IN_PROMPT = 50
MAX_MESSAGE_CHARS = 300

SYSTEM_PROMPT = (
    "You summarize a developer's recent commits in one repository into a single "
    "concise paragraph (1-3 sentences) describing the engineering work performed. "
    "Be specific and factual; do not use bullet points or a preamble."
)


class ClaudeSummarizer:
    """Summarize a repo's commits into a short paragraph."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        client: AsyncAnthropic | None = None,
        max_tokens: int = 300,
    ) -> None:
        self._client = client or AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    @classmethod
    def from_settings(cls, settings: Settings, *, client: AsyncAnthropic | None = None) -> "ClaudeSummarizer":
        return cls(settings.anthropic_api_key, settings.summarizer_model, client=client)

    def _build_prompt(
        self, repo_description: str, commit_messages: list[str], commit_count: int
    ) -> str:
        lines = []
        for message in commit_messages[:MAX_COMMITS_IN_PROMPT]:
            first_line = message.strip().splitlines()[0] if message.strip() else ""
            if first_line:
                lines.append(f"- {first_line[:MAX_MESSAGE_CHARS]}")
        overflow = len(commit_messages) - MAX_COMMITS_IN_PROMPT
        if overflow > 0:
            lines.append(f"- (+{overflow} more commits)")

        description = repo_description or "(no description)"
        return (
            f"Repository description: {description}\n"
            f"Total commits today: {commit_count}\n"
            f"Commit messages:\n" + "\n".join(lines)
        )

    @staticmethod
    def _fallback(commit_count: int) -> str:
        return f"{commit_count} commit{'s' if commit_count != 1 else ''}."

    async def summarize(
        self,
        *,
        repo_description: str,
        commit_messages: list[str],
        commit_count: int,
    ) -> str:
        """Return a one-paragraph summary, or a safe fallback on failure.

        Callers must skip empty commit sets — summarizing nothing is a bug, so
        this raises rather than calling the model.
        """
        if commit_count <= 0 or not commit_messages:
            raise ValueError("summarize() called with no commits")

        prompt = self._build_prompt(repo_description, commit_messages, commit_count)
        try:
            response: Any = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text = next(
                (block.text for block in response.content if block.type == "text"), ""
            ).strip()
            return text or self._fallback(commit_count)
        except Exception:  # noqa: BLE001 - a summary failure must not fail the digest
            logger.warning("summarizer_failed", extra={"model": self._model})
            return self._fallback(commit_count)
