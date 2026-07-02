"""Async GitHub REST client with ETag caching + rate-limit backoff.

Design decisions (ARCHITECTURE §8; U5 deferred question resolved):
- **Per-user commits** are read from the public **Events API**
  (``/users/{u}/events/public``): one request per user, newest-first, so we can
  stop early once we pass the cursor. PushEvent payloads carry the SHAs/messages
  we need. (Enumerating every repo would be far more expensive.)
- **Repo metadata** uses conditional requests: the stored ETag is sent as
  ``If-None-Match``; a ``304`` reuses the cache and does not count against the
  rate limit.
- **Rate limits:** honor ``X-RateLimit-Remaining`` / ``Retry-After`` and back off
  with jitter on 403/429; surface a typed :class:`RateLimitError` rather than
  crashing. Bounded concurrency via a semaphore.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.logging import get_logger

logger = get_logger(__name__)

API_BASE = "https://api.github.com"


class GitHubError(Exception):
    """Base class for GitHub client errors."""


class NotFoundError(GitHubError):
    """A requested resource (repo/user) does not exist."""


class RateLimitError(GitHubError):
    """Rate limit exhausted and retries were exhausted."""


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass
class CommitRef:
    repo: str
    sha: str
    message: str
    author: str
    timestamp: datetime
    url: str


@dataclass
class RepoInfo:
    repo: str
    description: str
    language: str
    stars: int
    forks: int
    default_branch: str
    updated_at: datetime | None
    from_cache: bool = False

    @classmethod
    def from_api(cls, repo: str, data: dict[str, Any]) -> "RepoInfo":
        return cls(
            repo=repo,
            description=data.get("description") or "",
            language=data.get("language") or "",
            stars=data.get("stargazers_count", 0),
            forks=data.get("forks_count", 0),
            default_branch=data.get("default_branch", "main"),
            updated_at=_parse_ts(data.get("pushed_at") or data.get("updated_at")),
        )

    @classmethod
    def from_cache(cls, cached: dict[str, Any]) -> "RepoInfo":
        return cls(
            repo=cached["repo"],
            description=cached.get("description", ""),
            language=cached.get("language", ""),
            stars=cached.get("stars", 0),
            forks=cached.get("forks", 0),
            default_branch=cached.get("default_branch", "main"),
            updated_at=cached.get("updated_at"),
            from_cache=True,
        )

    def cache_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "language": self.language,
            "stars": self.stars,
            "forks": self.forks,
            "default_branch": self.default_branch,
            "updated_at": self.updated_at,
        }


@dataclass
class BranchInfo:
    name: str
    sha: str
    author: str = ""
    updated_at: datetime | None = None
    ahead_by: int | None = None
    behind_by: int | None = None


class GitHubClient:
    """Async GitHub client. Use as an async context manager."""

    def __init__(
        self,
        token: str,
        repo_cache: Any | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        concurrency: int = 5,
        max_retries: int = 3,
        retry_base_delay: float = 0.5,
    ) -> None:
        self._token = token
        self._repo_cache = repo_cache
        self._external_client = client
        self._client = client
        self._sem = asyncio.Semaphore(concurrency)
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay

    async def __aenter__(self) -> "GitHubClient":
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=API_BASE,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                timeout=30.0,
            )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None and self._external_client is None:
            await self._client.aclose()

    # --- low-level request with backoff ---

    async def _request(
        self,
        method: str,
        path: str,
        *,
        etag: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        assert self._client is not None, "use GitHubClient as an async context manager"
        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag

        attempt = 0
        while True:
            async with self._sem:
                response = await self._client.request(
                    method, path, headers=headers, params=params
                )

            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining is not None:
                logger.debug("github_request", extra={"path": path, "remaining": remaining})

            if response.status_code == 304:
                return response
            if response.status_code == 404:
                raise NotFoundError(f"{method} {path} -> 404")
            if response.status_code in (403, 429) and self._is_rate_limited(response):
                if attempt >= self._max_retries:
                    logger.warning("github_rate_limited", extra={"path": path})
                    raise RateLimitError(f"rate limited on {path}")
                await asyncio.sleep(self._backoff(response, attempt))
                attempt += 1
                continue
            if response.is_error:
                raise GitHubError(f"{method} {path} -> {response.status_code}")
            return response

    @staticmethod
    def _is_rate_limited(response: httpx.Response) -> bool:
        if response.headers.get("X-RateLimit-Remaining") == "0":
            return True
        if "Retry-After" in response.headers:
            return True
        body = response.text.lower()
        return "rate limit" in body or "secondary rate" in body

    def _backoff(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            base = float(retry_after)
        else:
            base = self._retry_base_delay * (2 ** attempt)
        # Cap and add jitter so retries don't stampede.
        return min(base, 60.0) + random.uniform(0, self._retry_base_delay)

    # --- high-level API ---

    async def fetch_user_commits_since(
        self, username: str, since: datetime | None, *, max_pages: int = 3
    ) -> list[CommitRef]:
        """Return the user's push commits strictly after ``since`` (newest-first).

        Uses the public Events API. Stops paging as soon as an event at/older
        than ``since`` is seen (events are returned newest-first).
        """
        commits: list[CommitRef] = []
        for page in range(1, max_pages + 1):
            resp = await self._request(
                "GET",
                f"/users/{username}/events/public",
                params={"per_page": 100, "page": page},
            )
            events = resp.json()
            if not events:
                break
            reached_cursor = False
            for event in events:
                created = _parse_ts(event.get("created_at"))
                # Inclusive lower bound: events sharing the cursor's (1-second)
                # timestamp are re-included rather than dropped — the
                # processed_commits SHA dedup filters ones already reported, so
                # a distinct later push in the same second is never lost.
                if since is not None and created is not None and created < since:
                    reached_cursor = True
                    break
                if event.get("type") != "PushEvent":
                    continue
                repo_name = event.get("repo", {}).get("name", "")
                for commit in event.get("payload", {}).get("commits", []):
                    if not commit.get("sha"):
                        continue
                    commits.append(
                        CommitRef(
                            repo=repo_name,
                            sha=commit["sha"],
                            message=commit.get("message", ""),
                            author=commit.get("author", {}).get("name", username),
                            timestamp=created or datetime.now(timezone.utc),
                            url=f"https://github.com/{repo_name}/commit/{commit['sha']}",
                        )
                    )
            if reached_cursor or len(events) < 100:
                break
        return commits

    async def fetch_repo(self, repo: str) -> RepoInfo:
        """Fetch repo metadata using a conditional (ETag) request."""
        cached: dict[str, Any] | None = None
        etag: str | None = None
        if self._repo_cache is not None:
            cached = await self._repo_cache.get(repo)
            etag = cached.get("etag") if cached else None

        resp = await self._request("GET", f"/repos/{repo}", etag=etag)
        if resp.status_code == 304 and cached:
            return RepoInfo.from_cache(cached)

        info = RepoInfo.from_api(repo, resp.json())
        if self._repo_cache is not None:
            await self._repo_cache.put(
                repo, {**info.cache_dict(), "etag": resp.headers.get("ETag", "")}
            )
        return info

    async def fetch_recent_commits(self, repo: str, *, limit: int = 5) -> list[CommitRef]:
        resp = await self._request(
            "GET", f"/repos/{repo}/commits", params={"per_page": limit}
        )
        result: list[CommitRef] = []
        for item in resp.json():
            commit = item.get("commit", {})
            author = commit.get("author", {})
            result.append(
                CommitRef(
                    repo=repo,
                    sha=item.get("sha", ""),
                    message=commit.get("message", ""),
                    author=author.get("name", ""),
                    timestamp=_parse_ts(author.get("date")) or datetime.now(timezone.utc),
                    url=item.get("html_url", ""),
                )
            )
        return result

    async def fetch_contributors(self, repo: str, *, limit: int = 5) -> list[str]:
        resp = await self._request(
            "GET", f"/repos/{repo}/contributors", params={"per_page": limit}
        )
        return [c.get("login", "") for c in resp.json() if c.get("login")]

    async def fetch_branches(
        self, repo: str, *, enrich_limit: int = 10, default_branch: str | None = None
    ) -> list[BranchInfo]:
        """List branches, enriching the first ``enrich_limit`` with commit
        date/author and (best-effort) ahead/behind vs the default branch."""
        resp = await self._request(
            "GET", f"/repos/{repo}/branches", params={"per_page": 100}
        )
        branches = [
            BranchInfo(name=b.get("name", ""), sha=b.get("commit", {}).get("sha", ""))
            for b in resp.json()
        ]
        for branch in branches[:enrich_limit]:
            await self._enrich_branch(repo, branch, default_branch)
        return branches

    async def _enrich_branch(
        self, repo: str, branch: BranchInfo, default_branch: str | None
    ) -> None:
        try:
            commit_resp = await self._request("GET", f"/repos/{repo}/commits/{branch.sha}")
            commit = commit_resp.json().get("commit", {})
            author = commit.get("author", {})
            branch.author = author.get("name", "")
            branch.updated_at = _parse_ts(author.get("date"))
        except GitHubError:
            pass  # enrichment is best-effort
        if default_branch and branch.name != default_branch:
            try:
                cmp_resp = await self._request(
                    "GET", f"/repos/{repo}/compare/{default_branch}...{branch.name}"
                )
                data = cmp_resp.json()
                branch.ahead_by = data.get("ahead_by")
                branch.behind_by = data.get("behind_by")
            except GitHubError:
                pass  # ahead/behind is "when available"
