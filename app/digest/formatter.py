"""Digest embed formatting (mirrors the PRD digest example).

A per-user message shows total commits and, per repository, the commit count and
an AI summary. The on-demand ``/digest`` command batches all users into one or
more embeds, paginating when Discord's limits (25 fields / ~6000 chars per
embed) would otherwise force silent truncation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.discord import responses

if TYPE_CHECKING:
    from app.digest.pipeline import UserSection

# Discord embed limits (kept conservative).
MAX_FIELDS_PER_EMBED = 25
MAX_EMBED_VALUE_CHARS = 1024
MAX_EMBED_TOTAL_CHARS = 5500


def _repo_lines(section: "UserSection") -> str:
    parts = []
    for repo in section.repos:
        parts.append(f"**{repo.repo}** — {repo.count} commit{'s' if repo.count != 1 else ''}\n{repo.summary}")
    value = "\n\n".join(parts)
    return value[:MAX_EMBED_VALUE_CHARS]


def format_user_section(section: "UserSection") -> dict[str, Any]:
    """One embed for a single user's daily activity."""
    fields = [
        responses.field(
            repo.repo,
            f"• {repo.count} commit{'s' if repo.count != 1 else ''}\n{repo.summary}"[:MAX_EMBED_VALUE_CHARS],
        )
        for repo in section.repos
    ]
    return responses.embed(
        section.username,
        description=f"{section.total} commit{'s' if section.total != 1 else ''}",
        url=f"https://github.com/{section.username}",
        fields=fields,
    )


def _user_field(section: "UserSection") -> dict[str, Any]:
    return responses.field(f"{section.username} — {section.total} commits", _repo_lines(section))


def format_digest(date_label: str, sections: list["UserSection"]) -> list[dict[str, Any]]:
    """Batched digest embeds with pagination (for on-demand /digest)."""
    if not sections:
        return [
            responses.embed(
                "GitHub Daily Digest", description=f"{date_label}\nNo activity to report."
            )
        ]

    fields = [_user_field(s) for s in sections]
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for field in fields:
        size = len(field["name"]) + len(field["value"])
        if current and (len(current) >= MAX_FIELDS_PER_EMBED or current_chars + size > MAX_EMBED_TOTAL_CHARS):
            chunks.append(current)
            current, current_chars = [], 0
        current.append(field)
        current_chars += size
    if current:
        chunks.append(current)

    embeds = []
    header = f"{date_label}\n{len(sections)} developer{'s' if len(sections) != 1 else ''} active"
    for i, chunk in enumerate(chunks):
        title = "GitHub Daily Digest" if i == 0 else f"GitHub Daily Digest (cont. {i + 1})"
        description = header if i == 0 else None
        embeds.append(responses.embed(title, description=description, fields=chunk))
    return embeds
