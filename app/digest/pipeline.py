"""Daily digest pipeline (ARCHITECTURE §6c).

Scheduled path (fan-out, per-user messages):
  /tasks/digest/run  → post header, enqueue one /tasks/digest/user per user
  /tasks/digest/user → compute the user's section, post it, then record SHAs and
                       advance the cursor **only after** a successful post
                       (idempotent → safe to retry / re-run).

On-demand path (/digest today|yesterday): read-only window view, batched embeds,
no dedup and no cursor advance.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.config import Settings, get_settings
from app.digest import formatter
from app.discord.rest import DiscordREST
from app.github.client import GitHubClient, NotFoundError
from app.logging import get_logger, log_event
from app.store.repositories import Repositories, get_repositories
from app.summarizer.claude import ClaudeSummarizer
from app.tasks.queue import TaskEnqueuer

logger = get_logger(__name__)


@dataclass
class RepoSection:
    repo: str
    count: int
    description: str
    summary: str
    latest_messages: list[str] = dataclass_field(default_factory=list)


@dataclass
class UserSection:
    username: str
    total: int
    repos: list[RepoSection]
    new_shas: dict[str, list[str]] = dataclass_field(default_factory=dict)
    new_cursor: datetime | None = None


def _start_of_day(day: str) -> datetime:
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=1) if day == "yesterday" else midnight


class DigestPipeline:
    def __init__(
        self,
        repos: Repositories,
        enqueuer: TaskEnqueuer,
        settings: Settings,
        rest: DiscordREST,
        summarizer: ClaudeSummarizer,
        *,
        gh_factory: Callable[[], GitHubClient] | None = None,
    ) -> None:
        self._repos = repos
        self._enqueuer = enqueuer
        self._settings = settings
        self._rest = rest
        self._summarizer = summarizer
        self._gh_factory = gh_factory or self._default_gh

    def _default_gh(self) -> GitHubClient:
        return GitHubClient(self._settings.github_token, self._repos.repo_cache)

    # --- shared section computation ---

    async def compute_section(
        self, gh: GitHubClient, username: str, since: datetime | None, *, dedup: bool = True
    ) -> UserSection | None:
        commits = await gh.fetch_user_commits_since(username, since)
        if not commits:
            return None

        by_repo: dict[str, list[Any]] = defaultdict(list)
        for commit in commits:
            by_repo[commit.repo].append(commit)

        repo_sections: list[RepoSection] = []
        new_shas: dict[str, list[str]] = {}
        total = 0
        for repo, repo_commits in by_repo.items():
            if dedup:
                fresh = [
                    c for c in repo_commits
                    if not await self._repos.processed_commits.has_sha(c.repo, c.sha)
                ]
            else:
                fresh = repo_commits
            if not fresh:
                continue
            total += len(fresh)
            try:
                info = await gh.fetch_repo(repo)
                description = info.description
            except NotFoundError:
                description = ""
            messages = [c.message for c in fresh]
            summary = await self._summarizer.summarize(
                repo_description=description,
                commit_messages=messages,
                commit_count=len(fresh),
            )
            repo_sections.append(
                RepoSection(
                    repo=repo,
                    count=len(fresh),
                    description=description,
                    summary=summary,
                    latest_messages=[m.splitlines()[0] for m in messages[:3] if m.strip()],
                )
            )
            new_shas[repo] = [c.sha for c in fresh]

        if not repo_sections:
            return None
        new_cursor = max(c.timestamp for c in commits)
        return UserSection(
            username=username, total=total, repos=repo_sections,
            new_shas=new_shas, new_cursor=new_cursor,
        )

    # --- scheduled fan-out ---

    async def run_fanout(self) -> int:
        """Post the digest header and enqueue one task per enabled user."""
        config = await self._repos.config.get()
        channel = config.get("digest_channel_id", "")
        users = await self._repos.tracked_users.list_enabled()
        if channel and users:
            today = datetime.now(timezone.utc).strftime("%B %-d")
            await self._rest.post_channel_message(
                channel, content=f"📊 **GitHub Daily Digest** — {today} · {len(users)} tracked"
            )
        for user in users:
            await self._enqueuer.enqueue_digest_user({"username": user["username"]})
        log_event(logger, "digest_fanout", users=len(users))
        return len(users)

    async def process_user(self, username: str) -> bool:
        """Compute and post one user's section; advance cursor only on success.

        Returns True if a section was posted, False if the user had no new
        activity. Raises (for Cloud Tasks retry) if the post fails.
        """
        config = await self._repos.config.get()
        channel = config.get("digest_channel_id", "")
        since = await self._repos.tracked_users.get_cursor(username)

        async with self._gh_factory() as gh:
            section = await self.compute_section(gh, username, since)
        if section is None:
            return False

        embed = formatter.format_user_section(section)
        # Post FIRST — if this raises, the cursor is not advanced and SHAs are
        # not recorded, so a retry recomputes and reposts (recovery guarantee).
        await self._rest.post_channel_message(channel, embeds=[embed])

        for repo, shas in section.new_shas.items():
            await self._repos.processed_commits.record_shas(repo, shas)
        if section.new_cursor is not None:
            await self._repos.tracked_users.set_cursor(username, section.new_cursor)
        log_event(logger, "digest_user_posted", username=username, commits=section.total)
        return True

    # --- on-demand /digest command ---

    async def on_demand(self, day: str) -> list[dict[str, Any]]:
        since = _start_of_day(day)
        users = await self._repos.tracked_users.list_enabled()
        sections: list[UserSection] = []
        async with self._gh_factory() as gh:
            for user in users:
                section = await self.compute_section(gh, user["username"], since, dedup=False)
                if section is not None:
                    sections.append(section)
        label = "Today" if day == "today" else "Yesterday"
        return formatter.format_digest(label, sections)


def install_digest() -> None:
    """Wire digest endpoints + the /digest on-demand provider (lazy)."""
    from app.discord import handlers as handlers_module
    from app.discord import interactions as interactions_module

    holder: dict[str, DigestPipeline] = {}

    def _pipeline() -> DigestPipeline:
        if "p" not in holder:
            settings = get_settings()
            holder["p"] = DigestPipeline(
                get_repositories(),
                TaskEnqueuer(settings),
                settings,
                DiscordREST.from_settings(settings),
                ClaudeSummarizer.from_settings(settings),
            )
        return holder["p"]

    async def _run() -> None:
        await _pipeline().run_fanout()

    async def _user(username: str) -> None:
        await _pipeline().process_user(username)

    async def _on_demand(day: str) -> list[dict[str, Any]]:
        return await _pipeline().on_demand(day or "today")

    interactions_module.set_digest_handlers(_run, _user)
    handlers_module.set_digest_provider(_on_demand)
