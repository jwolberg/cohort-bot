"""Async Substack RSS client + parser (ARCHITECTURE §8 parity with GitHubClient).

Mirrors :class:`~app.github.client.GitHubClient`'s shape: async context manager,
injectable ``httpx.AsyncClient`` (for tests), typed errors, bounded concurrency,
and best-effort per-feed behavior.

Substack publishes well-formed **RSS 2.0**. We (A1, see ``docs/spec.md``):
- parse with ``defusedxml.ElementTree`` — XML-bomb / entity-expansion hardened,
  because feeds are untrusted remote input (especially custom domains);
- normalize ``pubDate`` via stdlib ``email.utils.parsedate_to_datetime``
  (RFC-822 → timezone-aware UTC);
- read the author from ``dc:creator`` (standard Dublin Core namespace);
- clean HTML excerpts with stdlib ``html.parser`` (strip tags, unescape
  entities, collapse whitespace, truncate ~280 chars);
- cap the response byte size *before* parsing.

The dedup key (``post_id``) is the entry ``guid``, falling back to its ``link``;
an entry with neither is skipped.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx
from defusedxml import ElementTree as DefusedET

from app.logging import get_logger

logger = get_logger(__name__)

# Dublin Core namespace used by Substack for <dc:creator>.
_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"

# Excerpt length cap (chars) and response size cap (bytes) before parsing.
EXCERPT_CHARS = 280
MAX_FEED_BYTES = 5_000_000


class SubstackError(Exception):
    """Base class for Substack client errors (unreachable / malformed feed)."""


class NotFoundError(SubstackError):
    """The feed URL returned 404."""


@dataclass
class PostRef:
    slug: str
    post_id: str
    title: str
    url: str
    author: str
    published: datetime
    excerpt: str


class _TextExtractor(HTMLParser):
    """Collect text nodes, dropping tags (entities already decoded by the parser)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def clean_excerpt(raw: str | None, *, limit: int = EXCERPT_CHARS) -> str:
    """Strip HTML tags from a feed description and truncate to ``limit`` chars."""
    if not raw:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(raw)
        text = parser.text()
    except Exception:  # noqa: BLE001 - malformed HTML falls back to the raw string
        text = raw
    text = " ".join(text.split())  # collapse all whitespace runs
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def slug_for(feed_url: str) -> str:
    """Derive a stable slug (the feed host) from a feed or publication URL.

    ``https://pragmaticengineer.substack.com/feed`` → ``pragmaticengineer.substack.com``.
    Custom domains are accepted as-is (host only, ``www.`` stripped).
    """
    host = (urlparse(feed_url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or feed_url.strip().lower()


def _parse_pubdate(value: str | None) -> datetime | None:
    """RFC-822 ``pubDate`` → timezone-aware UTC datetime, or None if unparseable."""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:  # naive → assume UTC
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _text(element: Any, tag: str) -> str:
    child = element.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


class SubstackClient:
    """Async Substack RSS client. Use as an async context manager."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        concurrency: int = 5,
        timeout: float = 30.0,
        max_bytes: int = MAX_FEED_BYTES,
    ) -> None:
        self._external_client = client
        self._client = client
        self._sem = asyncio.Semaphore(concurrency)
        self._timeout = timeout
        self._max_bytes = max_bytes

    async def __aenter__(self) -> "SubstackClient":
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={
                    "Accept": "application/rss+xml, application/xml, text/xml",
                    "User-Agent": "cohort-bot/0.1 (+https://github.com/jwolberg/cohort-bot)",
                },
                timeout=self._timeout,
                follow_redirects=True,
            )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None and self._external_client is None:
            await self._client.aclose()

    async def fetch_posts_since(
        self, feed_url: str, since: datetime | None, *, limit: int = 20
    ) -> list[PostRef]:
        """Return posts strictly newer than ``since`` (or all recent), newest-first.

        Raises :class:`NotFoundError` on 404 and :class:`SubstackError` on any
        other transport/parse failure, so the caller can skip one bad feed
        without failing the whole run (spec AC#8).
        """
        assert self._client is not None, "use SubstackClient as an async context manager"
        slug = slug_for(feed_url)
        try:
            async with self._sem:
                response = await self._client.get(feed_url)
        except httpx.RequestError as exc:
            raise SubstackError(f"request failed for {feed_url}: {exc}") from exc

        if response.status_code == 404:
            raise NotFoundError(f"{feed_url} -> 404")
        if response.is_error:
            raise SubstackError(f"{feed_url} -> {response.status_code}")

        body = response.content
        if len(body) > self._max_bytes:
            raise SubstackError(f"{feed_url} feed exceeds {self._max_bytes} bytes")

        try:
            root = DefusedET.fromstring(body)
        except Exception as exc:  # noqa: BLE001 - any XML error is a bad feed
            raise SubstackError(f"invalid feed XML for {feed_url}: {exc}") from exc

        channel = root.find("channel")
        if channel is None:
            raise SubstackError(f"no <channel> in feed {feed_url}")

        posts: list[PostRef] = []
        for item in channel.findall("item"):
            post = self._parse_item(slug, item)
            if post is None:
                continue
            if since is not None and post.published <= since:
                continue
            posts.append(post)

        posts.sort(key=lambda p: p.published, reverse=True)
        return posts[:limit]

    def _parse_item(self, slug: str, item: Any) -> PostRef | None:
        """Map one RSS <item> to a PostRef; return None to skip a bad entry."""
        published = _parse_pubdate(_text(item, "pubDate"))
        if published is None:
            return None  # can't place it on the timeline / dedup by cursor
        link = _text(item, "link")
        guid = _text(item, "guid")
        post_id = guid or link
        if not post_id:
            return None  # no stable dedup key
        author = ""
        creator = item.find(_DC_CREATOR)
        if creator is not None and creator.text:
            author = creator.text.strip()
        return PostRef(
            slug=slug,
            post_id=post_id,
            title=_text(item, "title") or "(untitled)",
            url=link or post_id,
            author=author,
            published=published,
            excerpt=clean_excerpt(_text(item, "description")),
        )
