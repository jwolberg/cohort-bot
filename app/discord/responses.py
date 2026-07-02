"""Builders for Discord interaction responses and embeds.

Interaction callback types (Discord API):
- 1 PONG
- 4 CHANNEL_MESSAGE_WITH_SOURCE      (immediate reply)
- 5 DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE  ("Bot is thinking…")

Message flags: EPHEMERAL = 1 << 6 = 64.
"""

from __future__ import annotations

from typing import Any

PONG = 1
CHANNEL_MESSAGE = 4
DEFERRED_CHANNEL_MESSAGE = 5

EPHEMERAL_FLAG = 1 << 6  # 64

# A pleasant default embed color (GitHub-ish dark).
DEFAULT_COLOR = 0x2B3137


def pong() -> dict[str, Any]:
    """Answer Discord's PING with a PONG."""
    return {"type": PONG}


def message(
    content: str | None = None,
    *,
    embeds: list[dict[str, Any]] | None = None,
    ephemeral: bool = False,
) -> dict[str, Any]:
    """Immediate message response (type 4)."""
    data: dict[str, Any] = {}
    if content is not None:
        data["content"] = content
    if embeds:
        data["embeds"] = embeds
    if ephemeral:
        data["flags"] = EPHEMERAL_FLAG
    return {"type": CHANNEL_MESSAGE, "data": data}


def deferred(*, ephemeral: bool = False) -> dict[str, Any]:
    """Deferred ACK (type 5); the real content is PATCHed in later."""
    response: dict[str, Any] = {"type": DEFERRED_CHANNEL_MESSAGE}
    if ephemeral:
        response["data"] = {"flags": EPHEMERAL_FLAG}
    return response


def embed(
    title: str,
    *,
    description: str | None = None,
    url: str | None = None,
    fields: list[dict[str, Any]] | None = None,
    color: int = DEFAULT_COLOR,
    footer: str | None = None,
) -> dict[str, Any]:
    """Build a Discord embed object."""
    obj: dict[str, Any] = {"title": title, "color": color}
    if description is not None:
        obj["description"] = description
    if url:
        obj["url"] = url
    if fields:
        obj["fields"] = fields
    if footer:
        obj["footer"] = {"text": footer}
    return obj


def field(name: str, value: str, *, inline: bool = False) -> dict[str, Any]:
    """Build an embed field."""
    return {"name": name, "value": value, "inline": inline}
