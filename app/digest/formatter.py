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
    from app.digest.pipeline import (
        PublicationSection,
        TrackedRepoSection,
        UserSection,
    )

# Discord embed limits (kept conservative).
MAX_FIELDS_PER_EMBED = 25
MAX_EMBED_VALUE_CHARS = 1024
MAX_EMBED_TITLE_CHARS = 250  # Discord field-name limit is 256
MAX_EMBED_TOTAL_CHARS = 5500


def _repo_link(repo: str) -> str:
    """Clickable repo title. Discord renders masked links in embed descriptions
    and field values, but not in field names — so the link leads the value."""
    return f"[**{repo}**](https://github.com/{repo})"


def _repo_lines(section: "UserSection") -> str:
    parts = []
    for repo in section.repos:
        parts.append(
            f"{_repo_link(repo.repo)} — {repo.count} commit{'s' if repo.count != 1 else ''}\n{repo.summary}"
        )
    value = "\n\n".join(parts)
    return value[:MAX_EMBED_VALUE_CHARS]


def format_user_section(section: "UserSection") -> dict[str, Any]:
    """One embed for a single user's daily activity."""
    fields = [
        responses.field(
            "​",  # zero-width space: the linked repo title in the value is the sole heading
            f"{_repo_link(repo.repo)}\n• {repo.count} commit{'s' if repo.count != 1 else ''}\n{repo.summary}"[:MAX_EMBED_VALUE_CHARS],
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


# --- Tracked repos (repo-centric daily scan) ---


def format_repo_section(section: "TrackedRepoSection") -> dict[str, Any]:
    """One 📦 embed for a single tracked repo's new commits (scheduled per-repo).

    Mirrors :func:`format_user_section`'s per-repo block: an AI summary followed
    by the latest commit subjects, under a linked repo title.
    """
    n = section.count
    lines = "\n".join(f"• {m}" for m in section.latest_messages)
    value = (section.summary + (f"\n{lines}" if lines else ""))[:MAX_EMBED_VALUE_CHARS]
    fields = [responses.field("​", value)] if value else []
    return responses.embed(
        f"📦 {section.repo}",
        description=f"{n} new commit{'s' if n != 1 else ''}",
        url=f"https://github.com/{section.repo}",
        fields=fields,
    )


def format_member_section(
    login: str, sections: list["TrackedRepoSection"]
) -> dict[str, Any]:
    """One 🧑‍💻 embed for a cohort member's private-repo activity (GitHub App).

    Groups all of the member's tracked repos into a single *attributed* embed —
    a linked repo title, commit count, and AI summary per repo (mirrors
    :func:`format_user_section`'s per-repo block). SPEC-GHAPP §4.4.
    """
    total = sum(s.count for s in sections)
    n = len(sections)
    fields = [
        responses.field(
            "​",  # zero-width space: the linked repo title in the value is the heading
            (
                f"{_repo_link(s.repo)}\n"
                f"• {s.count} commit{'s' if s.count != 1 else ''}\n{s.summary}"
            )[:MAX_EMBED_VALUE_CHARS],
        )
        for s in sections
    ]
    return responses.embed(
        f"🧑‍💻 {login}",
        description=(
            f"{total} commit{'s' if total != 1 else ''} across "
            f"{n} repo{'s' if n != 1 else ''}"
        ),
        url=f"https://github.com/{login}",
        fields=fields,
    )


# --- Substack (native excerpt, no LLM summarization) ---


def _post_field(post: Any) -> dict[str, Any]:
    """One embed field per post: title as name, excerpt + link as value."""
    name = f'"{post.title}"'[:MAX_EMBED_TITLE_CHARS]
    parts = [p for p in (post.excerpt, post.url) if p]
    value = "\n".join(parts)[:MAX_EMBED_VALUE_CHARS] or post.url
    return responses.field(name, value)


def format_publication_section(section: "PublicationSection") -> dict[str, Any]:
    """One 📰 embed for a single publication's new posts (scheduled per-pub message)."""
    n = len(section.posts)
    fields = [_post_field(p) for p in section.posts[:MAX_FIELDS_PER_EMBED]]
    return responses.embed(
        f"📰 {section.title}",
        description=f"{n} new post{'s' if n != 1 else ''}",
        url=section.feed_url or None,
        fields=fields,
    )


def format_substack(date_label: str, sections: list["PublicationSection"]) -> list[dict[str, Any]]:
    """On-demand /substack: one embed per publication, or an empty-state embed."""
    if not sections:
        return [
            responses.embed(
                "📰 Substack", description=f"{date_label}\nNo recent posts to report."
            )
        ]
    embeds = [format_publication_section(s) for s in sections]
    # Stamp the window label on the first embed's description for context.
    total = sum(len(s.posts) for s in sections)
    embeds[0]["description"] = (
        f"{date_label} · {total} post{'s' if total != 1 else ''} across "
        f"{len(sections)} publication{'s' if len(sections) != 1 else ''}"
    )
    return embeds[:10]  # Discord allows up to 10 embeds per message
