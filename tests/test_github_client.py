"""U5 tests: GitHub client mapping, caching, rate-limit + error paths."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.github.client import (
    GitHubClient,
    NotFoundError,
    RateLimitError,
)

pytestmark = pytest.mark.asyncio

BASE = "https://api.github.com"


class FakeRepoCache:
    def __init__(self, cached=None):
        self._cached = cached
        self.put_calls: list = []

    async def get(self, repo):
        return self._cached

    async def put(self, repo, data):
        self.put_calls.append((repo, data))


@respx.mock
async def test_fetch_repo_maps_fields_and_degrades_gracefully() -> None:
    respx.get(f"{BASE}/repos/octocat/hello-world").mock(
        return_value=httpx.Response(
            200,
            json={
                "description": None,  # missing optional degrades to ""
                "language": "Python",
                "stargazers_count": 100,
                "forks_count": 12,
                "default_branch": "main",
                "pushed_at": "2026-07-01T10:00:00Z",
            },
            headers={"ETag": 'W/"etag1"', "X-RateLimit-Remaining": "4999"},
        )
    )
    cache = FakeRepoCache()
    async with GitHubClient("tok", cache) as gh:
        info = await gh.fetch_repo("octocat/hello-world")
    assert info.description == ""
    assert info.language == "Python"
    assert info.stars == 100
    assert info.forks == 12
    assert info.default_branch == "main"
    assert cache.put_calls and cache.put_calls[0][1]["etag"] == 'W/"etag1"'


@respx.mock
async def test_fetch_repo_304_uses_cache_no_reparse() -> None:
    cached = {
        "repo": "octocat/hello-world",
        "description": "cached desc",
        "language": "Ruby",
        "stars": 7,
        "forks": 1,
        "default_branch": "trunk",
        "etag": 'W/"etag1"',
    }
    route = respx.get(f"{BASE}/repos/octocat/hello-world").mock(
        return_value=httpx.Response(304)
    )
    cache = FakeRepoCache(cached)
    async with GitHubClient("tok", cache) as gh:
        info = await gh.fetch_repo("octocat/hello-world")
    assert route.calls.last.request.headers["if-none-match"] == 'W/"etag1"'
    assert info.from_cache is True
    assert info.stars == 7
    assert info.default_branch == "trunk"
    assert cache.put_calls == []  # nothing re-written on a cache hit


@respx.mock
async def test_fetch_branches_returns_commit_author_updated() -> None:
    respx.get(f"{BASE}/repos/o/r/branches").mock(
        return_value=httpx.Response(
            200, json=[{"name": "main", "commit": {"sha": "abc"}}]
        )
    )
    respx.get(f"{BASE}/repos/o/r/commits/abc").mock(
        return_value=httpx.Response(
            200,
            json={"commit": {"author": {"name": "Jay", "date": "2026-07-01T09:00:00Z"}}},
        )
    )
    async with GitHubClient("tok") as gh:
        branches = await gh.fetch_branches("o/r", default_branch="main")
    assert len(branches) == 1
    assert branches[0].name == "main"
    assert branches[0].author == "Jay"
    assert branches[0].updated_at == datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)


@respx.mock
async def test_commits_since_boundary_is_inclusive_of_same_second() -> None:
    # Events newest-first; cursor equals the middle event's second. That event
    # is re-included (not dropped) so a distinct same-second push isn't lost;
    # SHA dedup upstream filters ones already reported. An event strictly before
    # the cursor stops pagination.
    cursor = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    events = [
        {
            "type": "PushEvent",
            "created_at": "2026-07-01T13:00:00Z",  # after cursor -> included
            "repo": {"name": "o/r"},
            "payload": {"commits": [{"sha": "new1", "message": "m", "author": {"name": "Jay"}}]},
        },
        {
            "type": "PushEvent",
            "created_at": "2026-07-01T12:00:00Z",  # == cursor -> included (inclusive)
            "repo": {"name": "o/r"},
            "payload": {"commits": [{"sha": "same1", "message": "m", "author": {"name": "Jay"}}]},
        },
        {
            "type": "PushEvent",
            "created_at": "2026-07-01T11:00:00Z",  # before cursor -> stops paging
            "repo": {"name": "o/r"},
            "payload": {"commits": [{"sha": "old1", "message": "m", "author": {"name": "Jay"}}]},
        },
    ]
    respx.get(f"{BASE}/users/jay/events/public").mock(
        return_value=httpx.Response(200, json=events)
    )
    async with GitHubClient("tok") as gh:
        commits = await gh.fetch_user_commits_since("jay", cursor)
    assert [c.sha for c in commits] == ["new1", "same1"]


@respx.mock
async def test_user_with_no_commits_returns_empty() -> None:
    respx.get(f"{BASE}/users/ghost/events/public").mock(
        return_value=httpx.Response(200, json=[])
    )
    async with GitHubClient("tok") as gh:
        commits = await gh.fetch_user_commits_since("ghost", None)
    assert commits == []


@respx.mock
async def test_404_raises_not_found() -> None:
    respx.get(f"{BASE}/repos/no/repo").mock(return_value=httpx.Response(404))
    async with GitHubClient("tok") as gh:
        with pytest.raises(NotFoundError):
            await gh.fetch_repo("no/repo")


@respx.mock
async def test_rate_limit_exhausted_raises_after_retries() -> None:
    respx.get(f"{BASE}/repos/o/r").mock(
        return_value=httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"X-RateLimit-Remaining": "0"},
        )
    )
    async with GitHubClient("tok", max_retries=2, retry_base_delay=0.0) as gh:
        with pytest.raises(RateLimitError):
            await gh.fetch_repo("o/r")
