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

import time
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
from app.substack.client import PostRef, SubstackClient, SubstackError
from app.summarizer.claude import ClaudeSummarizer
from app.tasks.queue import EnqueueError, TaskEnqueuer

logger = get_logger(__name__)

# Cloud Monitoring alerts on the absence of this log over a ~26h window
# (deploy/setup.sh creates the log-based metric + alert policy, U12).
HEARTBEAT_EVENT = "digest_heartbeat"


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


@dataclass
class PublicationSection:
    slug: str
    title: str
    feed_url: str
    posts: list[PostRef]
    new_post_ids: list[str] = dataclass_field(default_factory=list)
    new_cursor: datetime | None = None


def _start_of_day(day: str) -> datetime:
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=1) if day == "yesterday" else midnight


# /substack window option → days (default 1d per A2).
_WINDOW_DAYS = {"1d": 1, "7d": 7, "30d": 30}


def _window_since(window: str | None) -> tuple[datetime, str]:
    """Map a ``/substack`` window option to a since-datetime and a label."""
    days = _WINDOW_DAYS.get(window or "1d", 1)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return since, f"Last {days} day{'s' if days != 1 else ''}"


# Conventional-commit types treated as low-signal for the digest: chores,
# documentation updates, and bug fixes. Commits carrying one of these types
# (e.g. "fix: ...", "docs(readme): ...") are excluded so the digest highlights
# feature/substantive work. Extend this set to tune what counts as noise.
_LOW_SIGNAL_TYPES = {"chore", "docs", "doc", "fix", "bugfix", "hotfix"}

# Fallback for non-conventional subjects (e.g. "Fix crash", "Update README",
# "Bump deps"). Matched only at the START of the subject so feature commits that
# merely mention these words in passing are kept.
_LOW_SIGNAL_PREFIXES = (
    "fix ", "fixed ", "fixes ", "bugfix", "hotfix",
    "docs ", "doc ", "update docs", "update readme", "readme",
    "chore", "bump ", "typo",
)


def _commit_type(subject: str) -> str | None:
    """Return the conventional-commit type of a subject line, or None.

    "docs(readme)!: x" → "docs"; "Add feature" (no type prefix) → None.
    """
    if ":" not in subject:
        return None
    head = subject.split(":", 1)[0].strip().lower()
    if not head or " " in head:  # not a bare "type:" / "type(scope):" prefix
        return None
    head = head.split("(", 1)[0].rstrip("!")  # drop scope + breaking-change "!"
    return head or None


def _is_low_signal(message: str) -> bool:
    """True if a commit is a chore, docs update, or bug fix (excluded from digest)."""
    subject = message.splitlines()[0].strip() if message.strip() else ""
    if not subject:
        return False
    ctype = _commit_type(subject)
    if ctype is not None:
        return ctype in _LOW_SIGNAL_TYPES
    return subject.lower().startswith(_LOW_SIGNAL_PREFIXES)


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
        substack_factory: Callable[[], SubstackClient] | None = None,
    ) -> None:
        self._repos = repos
        self._enqueuer = enqueuer
        self._settings = settings
        self._rest = rest
        self._summarizer = summarizer
        self._gh_factory = gh_factory or self._default_gh
        self._substack_factory = substack_factory or self._default_substack

    def _default_gh(self) -> GitHubClient:
        return GitHubClient(self._settings.github_token, self._repos.repo_cache)

    def _default_substack(self) -> SubstackClient:
        return SubstackClient()

    # --- shared section computation ---

    async def compute_section(
        self, gh: GitHubClient, username: str, since: datetime | None, *, dedup: bool = True
    ) -> UserSection | None:
        commits = await gh.fetch_user_commits_since(username, since)
        if not commits:
            return None

        # Advance the cursor past EVERYTHING fetched (including filtered commits)
        # so low-signal commits aren't re-scanned on the next run, then keep only
        # signal commits (drop chores, docs updates, and bug fixes) for reporting.
        new_cursor = max(c.timestamp for c in commits)
        commits = [c for c in commits if not _is_low_signal(c.message)]
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
        return UserSection(
            username=username, total=total, repos=repo_sections,
            new_shas=new_shas, new_cursor=new_cursor,
        )

    async def compute_publication_section(
        self,
        client: SubstackClient,
        publication: dict[str, Any],
        since: datetime | None,
        *,
        dedup: bool = True,
    ) -> PublicationSection | None:
        """Compute one publication's new posts (mirrors :meth:`compute_section`).

        Best-effort: an unreachable / malformed feed is skipped with a warning and
        returns None rather than failing the run/command (spec AC#8). With
        ``dedup`` (scheduled path), posts already in ``processed_posts`` are
        dropped; the cursor still advances past everything fetched. With
        ``dedup=False`` (on-demand ``/substack``), all posts in the window are
        returned and no cursor/dedup state is touched.
        """
        slug = publication["slug"]
        feed_url = publication["feed_url"]
        try:
            posts = await client.fetch_posts_since(feed_url, since)
        except SubstackError as exc:
            logger.warning(
                "substack_feed_skipped", extra={"slug": slug, "error": str(exc)}
            )
            return None
        if not posts:
            return None

        new_cursor = max(p.published for p in posts)
        if dedup:
            fresh = [
                p for p in posts
                if not await self._repos.processed_posts.has_post(slug, p.post_id)
            ]
        else:
            fresh = posts
        if not fresh:
            return None
        return PublicationSection(
            slug=slug,
            title=publication.get("title") or slug,
            feed_url=feed_url,
            posts=fresh,
            new_post_ids=[p.post_id for p in fresh],
            new_cursor=new_cursor,
        )

    # --- scheduled fan-out ---

    async def run_fanout(self) -> int:
        """Post the digest header and enqueue one task per enabled user.

        Emits the SLO heartbeat only after the header successfully posts to the
        channel — a failure to post (or missing channel) leaves no heartbeat, so
        the Cloud Monitoring alert fires (ARCHITECTURE §11).
        """
        started = time.monotonic()
        config = await self._repos.config.get()
        channel = config.get("digest_channel_id", "")
        users = await self._repos.tracked_users.list_enabled()
        publications = await self._repos.tracked_publications.list_enabled()

        posted = False
        if channel and users:
            today = datetime.now(timezone.utc).strftime("%B %-d")
            await self._rest.post_channel_message(
                channel, content=f"📊 **GitHub Daily Digest** — {today} · {len(users)} tracked"
            )
            posted = True

        # Best-effort per user: a single enqueue failure must not 500 the job
        # (Cloud Scheduler would retry it and re-post the header). Failed users
        # are logged and simply miss this run.
        enqueued = 0
        for user in users:
            try:
                await self._enqueuer.enqueue_digest_user({"username": user["username"]})
                enqueued += 1
            except EnqueueError:
                logger.warning("digest_user_enqueue_failed", extra={"username": user["username"]})

        # Fan out one Substack task per enabled publication (same best-effort
        # rule; a publication with no new posts simply posts nothing — A3).
        pubs_enqueued = 0
        for pub in publications:
            try:
                await self._enqueuer.enqueue_substack_publication({"slug": pub["slug"]})
                pubs_enqueued += 1
            except EnqueueError:
                logger.warning("substack_pub_enqueue_failed", extra={"slug": pub["slug"]})

        duration_ms = round((time.monotonic() - started) * 1000)
        if posted:
            log_event(
                logger, HEARTBEAT_EVENT,
                users=len(users), enqueued=enqueued,
                publications=len(publications), pubs_enqueued=pubs_enqueued,
                channel=channel, duration_ms=duration_ms,
            )
        else:
            logger.warning(
                "digest_not_posted",
                extra={"users": len(users), "channel": channel, "reason": "no channel or no users"},
            )
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

    async def process_publication(self, slug: str) -> bool:
        """Compute and post one publication's new posts; advance cursor on success.

        Mirrors :meth:`process_user`: post FIRST, then record dedup keys and
        advance the cursor — a Cloud Tasks retry after a failed post recomputes
        and reposts (spec AC#5/#6). Returns False (no post, no cursor change)
        when the publication is gone/disabled or has no new posts.
        """
        config = await self._repos.config.get()
        channel = config.get("digest_channel_id", "")
        publication = await self._repos.tracked_publications.get(slug)
        if publication is None or not publication.get("enabled", False):
            return False
        since = publication.get("last_cursor")

        async with self._substack_factory() as client:
            section = await self.compute_publication_section(client, publication, since)
        if section is None:
            return False

        embed = formatter.format_publication_section(section)
        # Post FIRST — cursor/dedup are only advanced after a successful post.
        await self._rest.post_channel_message(channel, embeds=[embed])

        await self._repos.processed_posts.record_posts(slug, section.new_post_ids)
        if section.new_cursor is not None:
            await self._repos.tracked_publications.set_cursor(slug, section.new_cursor)
        log_event(logger, "substack_publication_posted", slug=slug, posts=len(section.posts))
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

    async def on_demand_substack(self, window: str | None) -> list[dict[str, Any]]:
        """Recent posts across enabled publications in a window (no dedup, /substack)."""
        since, label = _window_since(window)
        publications = await self._repos.tracked_publications.list_enabled()
        sections: list[PublicationSection] = []
        async with self._substack_factory() as client:
            for pub in publications:
                section = await self.compute_publication_section(
                    client, pub, since, dedup=False
                )
                if section is not None:
                    sections.append(section)
        return formatter.format_substack(label, sections)


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

    async def _publication(slug: str) -> None:
        await _pipeline().process_publication(slug)

    async def _on_demand(day: str) -> list[dict[str, Any]]:
        return await _pipeline().on_demand(day or "today")

    async def _on_demand_substack(window: str | None) -> list[dict[str, Any]]:
        return await _pipeline().on_demand_substack(window)

    interactions_module.set_digest_handlers(_run, _user)
    interactions_module.set_publication_worker(_publication)
    handlers_module.set_digest_provider(_on_demand)
    handlers_module.set_substack_provider(_on_demand_substack)
