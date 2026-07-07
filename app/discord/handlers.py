"""Slash-command handlers: fast path + deferred slow path.

Fast commands (``/track *``, ``/help``) touch only Firestore and answer within
Discord's 3s deadline. Slow commands (``/repo``, ``/branches``, ``/user``,
``/digest``) return a deferred ACK, enqueue a Cloud Task, and the follow-up
worker calls GitHub/Claude and PATCHes the webhook (ARCHITECTURE §6a/§6b).

``install_handlers`` wires the default handler into the interactions router; the
handler is built lazily so importing/creating the app never requires live GCP.
"""

from __future__ import annotations

import re
from typing import Any

from app.config import Settings, get_settings
from app.digest.formatter import MAX_FIELDS_PER_EMBED, _post_field
from app.discord import interactions as interactions_module
from app.discord import responses
from app.discord.rest import DiscordREST
from app.github.client import GitHubClient, NotFoundError
from app.logging import get_logger
from app.store.repositories import Repositories, get_repositories
from app.substack.client import (
    NotFoundError as SubstackNotFound,
    SubstackClient,
    SubstackError,
    normalize_feed_url,
)
from app.tasks.queue import TaskEnqueuer

logger = get_logger(__name__)

SLOW_COMMANDS = {"repo", "branches", "user", "digest", "substack", "publication"}

# The /digest and /substack on-demand providers are registered by the digest
# pipeline (U9 / S5).
_digest_provider = None
_substack_provider = None


def set_digest_provider(provider) -> None:
    """Register the coroutine that builds on-demand /digest embeds (U9)."""
    global _digest_provider
    _digest_provider = provider


def set_substack_provider(provider) -> None:
    """Register the coroutine that builds on-demand /substack embeds (S5)."""
    global _substack_provider
    _substack_provider = provider
_REPO_RE = re.compile(r"^[^/\s]+/[^/\s]+$")

SUB_COMMAND_OPTION_TYPE = 1

HELP_TEXT = (
    "**Tracking**\n"
    "`/track add <user>` — track a GitHub user (admin)\n"
    "`/track remove <user>` — stop tracking (admin)\n"
    "`/track list` — list tracked users\n\n"
    "**Activity**\n"
    "`/repo owner/repo` — inspect a repository\n"
    "`/branches owner/repo` — list branches\n"
    "`/user <user>` — recent activity for a user\n"
    "`/digest today|yesterday` — post the digest\n"
    "`/substack [1d|7d|30d]` — recent posts from tracked publications\n"
    "`/publication <url>` — inspect a Substack publication\n"
)


def _parse(interaction: dict[str, Any]) -> tuple[str, str | None, dict[str, Any]]:
    """Return (command, subcommand, options-by-name)."""
    data = interaction.get("data", {})
    name = data.get("name", "")
    options = data.get("options", [])
    if options and options[0].get("type") == SUB_COMMAND_OPTION_TYPE:
        sub = options[0]["name"]
        opt_list = options[0].get("options", [])
    else:
        sub = None
        opt_list = options
    opts = {o["name"]: o.get("value") for o in opt_list}
    return name, sub, opts


class CommandHandler:
    def __init__(
        self,
        repos: Repositories,
        enqueuer: TaskEnqueuer,
        settings: Settings,
        rest: DiscordREST,
        *,
        gh_factory=None,
        substack_factory=None,
    ) -> None:
        self._repos = repos
        self._enqueuer = enqueuer
        self._settings = settings
        self._rest = rest
        self._gh_factory = gh_factory or self._default_gh
        self._substack_factory = substack_factory or self._default_substack

    def _default_gh(self) -> GitHubClient:
        return GitHubClient(self._settings.github_token, self._repos.repo_cache)

    def _default_substack(self) -> SubstackClient:
        return SubstackClient()

    # --- dispatch (fast path + defer) ---

    async def dispatch(self, interaction: dict[str, Any]) -> dict[str, Any]:
        name, sub, opts = _parse(interaction)

        if name == "help":
            return responses.message(embeds=[responses.embed("Commands", description=HELP_TEXT)])

        if name == "track":
            return await self._handle_track(interaction, sub, opts)

        if name in SLOW_COMMANDS:
            # Validate the owner/repo argument before deferring — no point
            # enqueuing a task (or calling GitHub) for a malformed input.
            if name in ("repo", "branches"):
                repo = opts.get("repo", "")
                if not _REPO_RE.match(repo or ""):
                    return responses.message(
                        embeds=[responses.embed("Invalid repository", description="Use `owner/repo`.")],
                        ephemeral=True,
                    )
            if name == "publication":
                # Normalize the pasted URL/host to its /feed URL up front; a
                # hostless input never reaches the worker (mirrors the repo guard).
                feed_url = normalize_feed_url(opts.get("publication", "") or "")
                if not feed_url:
                    return responses.message(
                        embeds=[responses.embed(
                            "Invalid publication",
                            description="Provide a Substack URL or host, e.g. `pragmaticengineer.substack.com`.",
                        )],
                        ephemeral=True,
                    )
                opts["publication"] = feed_url
            await self._enqueuer.enqueue_followup(
                {
                    "application_id": interaction.get("application_id", self._settings.discord_app_id),
                    "interaction_token": interaction["token"],
                    "command": name,
                    "sub": sub,
                    "options": opts,
                }
            )
            return responses.deferred()

        return responses.message("Unknown command.", ephemeral=True)

    async def _handle_track(
        self, interaction: dict[str, Any], sub: str | None, opts: dict[str, Any]
    ) -> dict[str, Any]:
        if sub == "list":
            users = await self._repos.tracked_users.list_enabled()
            names = ", ".join(sorted(u["username"] for u in users)) or "(none)"
            return responses.message(
                embeds=[responses.embed("Tracked users", description=names)]
            )

        if sub in ("add", "remove"):
            if not await self._is_admin(interaction):
                return responses.message(
                    embeds=[responses.embed("Permission denied", description="Admin role required.")],
                    ephemeral=True,
                )
            username = opts["username"]
            if sub == "add":
                await self._repos.tracked_users.add(username, added_by=self._actor_id(interaction))
                verb = "Now tracking"
            else:
                await self._repos.tracked_users.remove(username)
                verb = "Stopped tracking"
            return responses.message(
                embeds=[responses.embed(verb, description=f"`{username}`")]
            )

        return responses.message("Unknown /track subcommand.", ephemeral=True)

    async def _is_admin(self, interaction: dict[str, Any]) -> bool:
        config = await self._repos.config.get()
        admin_roles = set(config.get("admin_role_ids", []))
        if not admin_roles:
            return False  # admins are configured via the admin panel (U10)
        member = interaction.get("member") or {}
        roles = set(member.get("roles", []))
        return bool(roles & admin_roles)

    @staticmethod
    def _actor_id(interaction: dict[str, Any]) -> str:
        member = interaction.get("member") or {}
        user = member.get("user") or interaction.get("user") or {}
        return user.get("id", "unknown")

    # --- follow-up worker (slow path) ---

    async def run_followup(self, payload: dict[str, Any]) -> None:
        command = payload["command"]
        opts = payload.get("options", {})
        sub = payload.get("sub")
        application_id = payload["application_id"]
        token = payload["interaction_token"]

        # /digest and /substack are served by the digest pipeline (U9 / S5),
        # which return ready embeds; the others build a single embed from GitHub.
        if command == "digest":
            if _digest_provider is None:
                embeds = [responses.embed("Unavailable", description="Digest is not available yet.")]
            else:
                embeds = await _digest_provider(sub or "today")
            await self._rest.edit_original_response(application_id, token, embeds=embeds)
            return

        if command == "substack":
            if _substack_provider is None:
                embeds = [responses.embed("Unavailable", description="Substack is not available yet.")]
            else:
                embeds = await _substack_provider(opts.get("window"))
            await self._rest.edit_original_response(application_id, token, embeds=embeds)
            return

        # /publication inspects one Substack feed live (parity with /repo), so it
        # opens a SubstackClient rather than the GitHub client below.
        if command == "publication":
            async with self._substack_factory() as sc:
                embed = await self._publication_embed(sc, opts["publication"])
            await self._rest.edit_original_response(application_id, token, embeds=[embed])
            return

        async with self._gh_factory() as gh:
            if command == "repo":
                embed = await self._repo_embed(gh, opts["repo"])
            elif command == "branches":
                embed = await self._branches_embed(gh, opts["repo"])
            elif command == "user":
                embed = await self._user_embed(gh, opts["username"])
            else:
                embed = responses.embed("Unavailable", description=f"`/{command}` is not available.")

        await self._rest.edit_original_response(application_id, token, embeds=[embed])

    async def _repo_embed(self, gh: GitHubClient, repo: str) -> dict[str, Any]:
        try:
            info = await gh.fetch_repo(repo)
        except NotFoundError:
            return responses.embed("Not found", description=f"Repository `{repo}` was not found.")
        commits = await gh.fetch_recent_commits(repo, limit=5)
        contributors = await gh.fetch_contributors(repo, limit=5)

        recent = "\n".join(f"• {c.message.splitlines()[0][:80]}" for c in commits) or "—"
        fields = [
            responses.field("Language", info.language or "—", inline=True),
            responses.field("Stars", str(info.stars), inline=True),
            responses.field("Forks", str(info.forks), inline=True),
            responses.field("Default branch", info.default_branch, inline=True),
            responses.field("Contributors", ", ".join(contributors) or "—"),
            responses.field("Recent commits", recent),
        ]
        return responses.embed(
            repo,
            description=info.description or None,
            url=f"https://github.com/{repo}",
            fields=fields,
        )

    async def _publication_embed(self, sc: SubstackClient, feed_url: str) -> dict[str, Any]:
        try:
            view = await sc.fetch_publication(feed_url, limit=5)
        except SubstackNotFound:
            return responses.embed("Not found", description=f"No Substack feed at `{feed_url}`.")
        except SubstackError:
            return responses.embed("Unreachable", description=f"Could not read the feed at `{feed_url}`.")
        if not view.posts:
            return responses.embed(
                f"📰 {view.title}",
                description="No recent posts.",
                url=view.link or None,
            )
        fields = [_post_field(p) for p in view.posts[:MAX_FIELDS_PER_EMBED]]
        return responses.embed(
            f"📰 {view.title}",
            description=view.description or None,
            url=view.link or None,
            fields=fields,
        )

    async def _branches_embed(self, gh: GitHubClient, repo: str) -> dict[str, Any]:
        try:
            branches = await gh.fetch_branches(repo)
        except NotFoundError:
            return responses.embed("Not found", description=f"Repository `{repo}` was not found.")
        lines = []
        for b in branches[:15]:
            extra = ""
            if b.ahead_by is not None or b.behind_by is not None:
                extra = f" (+{b.ahead_by or 0}/-{b.behind_by or 0})"
            author = f" — {b.author}" if b.author else ""
            lines.append(f"• `{b.name}`{extra}{author}")
        return responses.embed(
            f"Branches — {repo}", description="\n".join(lines) or "No branches."
        )

    async def _user_embed(self, gh: GitHubClient, username: str) -> dict[str, Any]:
        try:
            commits = await gh.fetch_user_commits_since(username, None)
        except NotFoundError:
            return responses.embed("Not found", description=f"User `{username}` was not found.")
        if not commits:
            return responses.embed(
                username, description="No recent public activity.",
                url=f"https://github.com/{username}",
            )
        by_repo: dict[str, int] = {}
        for c in commits:
            by_repo[c.repo] = by_repo.get(c.repo, 0) + 1
        lines = [f"• `{repo}` — {n} commit{'s' if n != 1 else ''}" for repo, n in sorted(
            by_repo.items(), key=lambda kv: kv[1], reverse=True
        )]
        return responses.embed(
            username,
            description=f"{len(commits)} recent commits\n" + "\n".join(lines[:10]),
            url=f"https://github.com/{username}",
        )


def install_handlers() -> None:
    """Wire the default command handler into the interactions router (lazy)."""
    holder: dict[str, CommandHandler] = {}

    def _handler() -> CommandHandler:
        if "h" not in holder:
            settings = get_settings()
            holder["h"] = CommandHandler(
                get_repositories(),
                TaskEnqueuer(settings),
                settings,
                DiscordREST.from_settings(settings),
            )
        return holder["h"]

    async def _dispatch(interaction: dict[str, Any]) -> dict[str, Any]:
        return await _handler().dispatch(interaction)

    async def _followup(payload: dict[str, Any]) -> None:
        await _handler().run_followup(payload)

    interactions_module.set_command_dispatcher(_dispatch)
    interactions_module.set_followup_handler(_followup)
