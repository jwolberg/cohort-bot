"""S2 tests: Substack RSS client — parsing, since filtering, best-effort skips."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.substack.client import (
    NotFoundError,
    SubstackClient,
    SubstackError,
    clean_excerpt,
    normalize_feed_url,
    slug_for,
)

# asyncio_mode = "auto" (pyproject) runs the async tests; the two pure-helper
# tests below are sync, so no module-level asyncio mark.

FEED_URL = "https://ex.substack.com/feed"

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>The Example</title>
    <item>
      <title>Second Post</title>
      <link>https://ex.substack.com/p/second</link>
      <guid>https://ex.substack.com/p/second-guid</guid>
      <dc:creator>Jane Doe</dc:creator>
      <pubDate>Wed, 02 Jul 2026 10:00:00 GMT</pubDate>
      <description>&lt;p&gt;A &lt;strong&gt;bold&lt;/strong&gt; take &amp; more.&lt;/p&gt;</description>
    </item>
    <item>
      <title>First Post</title>
      <link>https://ex.substack.com/p/first</link>
      <pubDate>Mon, 30 Jun 2026 08:00:00 GMT</pubDate>
      <description>Older post.</description>
    </item>
  </channel>
</rss>
"""


def _mock_feed(body: str | bytes = RSS, status: int = 200) -> None:
    content = body.encode() if isinstance(body, str) else body
    respx.get(FEED_URL).mock(return_value=httpx.Response(status, content=content))


# --- pure helpers ---


def test_slug_for_strips_scheme_and_www() -> None:
    assert slug_for("https://pragmaticengineer.substack.com/feed") == "pragmaticengineer.substack.com"
    assert slug_for("https://www.custom-domain.com/feed") == "custom-domain.com"


def test_normalize_feed_url() -> None:
    assert normalize_feed_url("pragmaticengineer.substack.com") == "https://pragmaticengineer.substack.com/feed"
    assert normalize_feed_url("https://ex.substack.com/feed") == "https://ex.substack.com/feed"
    assert normalize_feed_url("https://ex.substack.com/p/some-post") == "https://ex.substack.com/feed"
    assert normalize_feed_url("  ") == ""
    assert normalize_feed_url("https://") == ""  # scheme but no host


def test_clean_excerpt_strips_tags_and_truncates() -> None:
    assert clean_excerpt("<p>A <strong>bold</strong> take &amp; more.</p>") == "A bold take & more."
    long = "<p>" + ("x " * 400) + "</p>"
    out = clean_excerpt(long, limit=280)
    assert len(out) <= 281 and out.endswith("…")
    assert clean_excerpt(None) == ""


# --- client ---


@respx.mock
async def test_fetch_maps_all_fields() -> None:
    _mock_feed()
    async with SubstackClient() as sc:
        posts = await sc.fetch_posts_since(FEED_URL, None)
    assert [p.title for p in posts] == ["Second Post", "First Post"]  # newest-first
    second = posts[0]
    assert second.slug == "ex.substack.com"
    assert second.post_id == "https://ex.substack.com/p/second-guid"  # guid preferred
    assert second.url == "https://ex.substack.com/p/second"
    assert second.author == "Jane Doe"
    assert second.published == datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)
    assert second.excerpt == "A bold take & more."


@respx.mock
async def test_guid_falls_back_to_link() -> None:
    _mock_feed()
    async with SubstackClient() as sc:
        posts = await sc.fetch_posts_since(FEED_URL, None)
    first = next(p for p in posts if p.title == "First Post")
    assert first.post_id == "https://ex.substack.com/p/first"  # no <guid> → link


@respx.mock
async def test_since_filters_strictly_newer() -> None:
    _mock_feed()
    since = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    async with SubstackClient() as sc:
        posts = await sc.fetch_posts_since(FEED_URL, since)
    assert [p.title for p in posts] == ["Second Post"]  # First Post (Jun 30) excluded


@respx.mock
async def test_limit_caps_results() -> None:
    _mock_feed()
    async with SubstackClient() as sc:
        posts = await sc.fetch_posts_since(FEED_URL, None, limit=1)
    assert len(posts) == 1
    assert posts[0].title == "Second Post"


@respx.mock
async def test_entry_missing_pubdate_is_skipped() -> None:
    body = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item><title>No date</title><link>https://ex.substack.com/p/x</link></item>
      <item><title>Dated</title><link>https://ex.substack.com/p/y</link>
        <pubDate>Wed, 02 Jul 2026 10:00:00 GMT</pubDate></item>
    </channel></rss>"""
    _mock_feed(body)
    async with SubstackClient() as sc:
        posts = await sc.fetch_posts_since(FEED_URL, None)
    assert [p.title for p in posts] == ["Dated"]


@respx.mock
async def test_fetch_publication_returns_metadata_and_posts() -> None:
    _mock_feed()
    async with SubstackClient() as sc:
        view = await sc.fetch_publication(FEED_URL, limit=5)
    assert view.slug == "ex.substack.com"
    assert view.title == "The Example"  # from <channel><title>
    assert [p.title for p in view.posts] == ["Second Post", "First Post"]  # newest-first


@respx.mock
async def test_fetch_publication_limit_caps_posts() -> None:
    _mock_feed()
    async with SubstackClient() as sc:
        view = await sc.fetch_publication(FEED_URL, limit=1)
    assert [p.title for p in view.posts] == ["Second Post"]


@respx.mock
async def test_fetch_publication_404_raises_not_found() -> None:
    _mock_feed(status=404)
    async with SubstackClient() as sc:
        with pytest.raises(NotFoundError):
            await sc.fetch_publication(FEED_URL)


@respx.mock
async def test_404_raises_not_found() -> None:
    _mock_feed(status=404)
    async with SubstackClient() as sc:
        with pytest.raises(NotFoundError):
            await sc.fetch_posts_since(FEED_URL, None)


@respx.mock
async def test_invalid_xml_raises_substack_error() -> None:
    _mock_feed(b"<rss><channel><item>broken")
    async with SubstackClient() as sc:
        with pytest.raises(SubstackError):
            await sc.fetch_posts_since(FEED_URL, None)


@respx.mock
async def test_timeout_raises_substack_error() -> None:
    respx.get(FEED_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
    async with SubstackClient() as sc:
        with pytest.raises(SubstackError):
            await sc.fetch_posts_since(FEED_URL, None)
