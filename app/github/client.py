"""Async GitHub REST client with ETag caching + rate-limit backoff.

Design decisions (ARCHITECTURE §8; U5 deferred question resolved):
- **Per-user commits** are discovered from the public **Events API**
  (``/users/{u}/events/public``): one request per user, newest-first, so we can
  stop early once we pass the cursor. PushEvent payloads no longer inline the
  commit list — they carry only the ``before``/``head`` SHAs — so each push's
  range is hydrated via the compare API. (Enumerating every repo would be far
  more expensive.)
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
from typing import Any, Awaitable, Callable

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
        token: str | None = None,
        repo_cache: Any | None = None,
        *,
        token_provider: Callable[[], Awaitable[str]] | None = None,
        client: httpx.AsyncClient | None = None,
        concurrency: int = 5,
        max_retries: int = 3,
        retry_base_delay: float = 0.5,
    ) -> None:
        # Authenticate with either a static ``token`` (PATs, the public/private
        # digest paths) or an async ``token_provider`` that returns a token at
        # connect time — e.g. a GitHub App installation token (see
        # ``app.github.app_auth.GitHubAppAuth.token_provider``). The provider is
        # resolved once in ``__aenter__``; a per-run client outlives one token.
        if token is None and token_provider is None:
            raise ValueError("GitHubClient requires a token or a token_provider")
        self._token = token
        self._token_provider = token_provider
        self._repo_cache = repo_cache
        self._external_client = client
        self._client = client
        self._sem = asyncio.Semaphore(concurrency)
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay

    async def __aenter__(self) -> "GitHubClient":
        if self._client is None:
            token = self._token
            if token is None and self._token_provider is not None:
                token = await self._token_provider()
            self._client = httpx.AsyncClient(
                base_url=API_BASE,
                headers={
                    "Authorization": f"Bearer {token}",
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

        Uses the public Events API to find pushes, then hydrates each push's
        commits from its ``before...head`` range (see :meth:`_commits_from_push`).
        Stops paging as soon as an event at/older than ``since`` is seen (events
        are returned newest-first).
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
                payload = event.get("payload", {})
                commits.extend(
                    await self._commits_from_push(
                        repo_name,
                        payload.get("before"),
                        payload.get("head"),
                        when=created or datetime.now(timezone.utc),
                        username=username,
                    )
                )
            if reached_cursor or len(events) < 100:
                break
        return commits

    async def _commits_from_push(
        self,
        repo: str,
        before: str | None,
        head: str | None,
        *,
        when: datetime,
        username: str,
    ) -> list[CommitRef]:
        """Resolve the commits introduced by a single push.

        The public Events API no longer inlines a PushEvent's commit list — the
        payload carries only the ``before``/``head`` SHAs — so hydrate the range
        via the compare API. A new-branch push (``before`` all-zeros) has no base
        to compare against, so fall back to the single head commit. Best-effort:
        a range we can't resolve (deleted/renamed repo, etc.) is skipped rather
        than failing the whole digest. ``when`` (the push time) is used as each
        commit's timestamp so window/cursor semantics match the event stream.
        """
        if not repo or not head:
            return []
        try:
            if not before or set(before) == {"0"}:
                resp = await self._request("GET", f"/repos/{repo}/commits/{head}")
                raw = [resp.json()]
            else:
                resp = await self._request(
                    "GET", f"/repos/{repo}/compare/{before}...{head}"
                )
                raw = resp.json().get("commits") or []
        except GitHubError:
            return []
        out: list[CommitRef] = []
        for entry in raw:
            sha = entry.get("sha")
            if not sha:
                continue
            commit = entry.get("commit") or {}
            author = commit.get("author") or {}
            out.append(
                CommitRef(
                    repo=repo,
                    sha=sha,
                    message=commit.get("message", ""),
                    author=author.get("name") or username,
                    timestamp=when,
                    url=entry.get("html_url")
                    or f"https://github.com/{repo}/commit/{sha}",
                )
            )
        return out

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

    async def fetch_commits_since(
        self, repo: str, since: datetime | None, *, max_pages: int = 3
    ) -> list[CommitRef]:
        """Return a repo's commits after ``since`` (newest-first, default branch).

        Repo-centric counterpart to :meth:`fetch_user_commits_since`: it reads a
        repository directly (``/repos/{repo}/commits``) rather than via a user's
        public Events feed, so it covers repos that never surface in that feed —
        notably **private** repos read with a scoped token. ``since`` is an ISO-
        8601 lower bound the API applies to each commit's *committer* date, so the
        stored ``timestamp`` (and thus the next cursor) uses committer date too,
        keeping the cursor and the ``since`` filter consistent. Boundary commits
        sharing the cursor's second may re-appear; upstream SHA dedup drops ones
        already reported. Paging stops on a short page or at ``max_pages``.
        """
        base_params: dict[str, Any] = {"per_page": 100}
        if since is not None:
            base_params["since"] = (
                since.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            )
        commits: list[CommitRef] = []
        for page in range(1, max_pages + 1):
            resp = await self._request(
                "GET", f"/repos/{repo}/commits", params={**base_params, "page": page}
            )
            batch = resp.json()
            if not batch:
                break
            for item in batch:
                sha = item.get("sha")
                if not sha:
                    continue
                commit = item.get("commit") or {}
                author = commit.get("author") or {}
                committer = commit.get("committer") or {}
                timestamp = (
                    _parse_ts(committer.get("date"))
                    or _parse_ts(author.get("date"))
                    or datetime.now(timezone.utc)
                )
                commits.append(
                    CommitRef(
                        repo=repo,
                        sha=sha,
                        message=commit.get("message", ""),
                        author=author.get("name", ""),
                        timestamp=timestamp,
                        url=item.get("html_url", "")
                        or f"https://github.com/{repo}/commit/{sha}",
                    )
                )
            if len(batch) < 100:
                break
        return commits

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
