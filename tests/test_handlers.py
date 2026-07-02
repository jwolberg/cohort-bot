"""U7 tests: command handlers (fast path, deferral, slow-path worker)."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx

from app.config import get_settings
from app.discord import responses
from app.discord.handlers import CommandHandler
from app.store.repositories import get_repositories

BASE = "https://api.github.com"


class FakeEnqueuer:
    def __init__(self):
        self.followups: list[dict] = []

    async def enqueue_followup(self, payload):
        self.followups.append(payload)
        return "queues/interaction-followups/tasks/1"


class FakeRest:
    def __init__(self):
        self.edits: list[dict] = []

    async def edit_original_response(self, application_id, interaction_token, *, embeds=None, content=None):
        self.edits.append({"application_id": application_id, "token": interaction_token, "embeds": embeds})


class FakeRepoCache:
    async def get(self, repo):
        return None

    async def put(self, repo, data):
        return None


def _track_interaction(sub, *, username=None, roles=()):
    opts = [{"name": "username", "type": 3, "value": username}] if username else []
    return {
        "type": 2,
        "application_id": "app",
        "token": "tok",
        "data": {"name": "track", "options": [{"name": sub, "type": 1, "options": opts}]},
        "member": {"roles": list(roles), "user": {"id": "u1"}},
    }


def _slash(name, **opts):
    options = [{"name": k, "type": 3, "value": v} for k, v in opts.items()]
    return {"type": 2, "application_id": "app", "token": "tok", "data": {"name": name, "options": options}}


# --- Fast path: /track + /help against the emulator ---


@pytest.fixture
def track_handler(firestore_client):
    repos = get_repositories(firestore_client)
    return CommandHandler(repos, FakeEnqueuer(), get_settings(), FakeRest()), repos


@pytest.mark.asyncio
async def test_track_add_by_admin_persists(track_handler) -> None:
    handler, repos = track_handler
    await repos.config.update({"admin_role_ids": ["admin1"]})
    resp = await handler.dispatch(_track_interaction("add", username="octocat", roles=["admin1"]))
    assert resp["type"] == responses.CHANNEL_MESSAGE
    enabled = await repos.tracked_users.list_enabled()
    assert [u["username"] for u in enabled] == ["octocat"]


@pytest.mark.asyncio
async def test_track_add_by_non_admin_is_denied(track_handler) -> None:
    handler, repos = track_handler
    await repos.config.update({"admin_role_ids": ["admin1"]})
    resp = await handler.dispatch(_track_interaction("add", username="octocat", roles=["someoneelse"]))
    assert resp["data"]["flags"] == responses.EPHEMERAL_FLAG  # ephemeral denial
    assert await repos.tracked_users.list_enabled() == []  # not persisted


@pytest.mark.asyncio
async def test_track_remove_and_list_reflect_store(track_handler) -> None:
    handler, repos = track_handler
    await repos.config.update({"admin_role_ids": ["admin1"]})
    await handler.dispatch(_track_interaction("add", username="a", roles=["admin1"]))
    await handler.dispatch(_track_interaction("add", username="b", roles=["admin1"]))
    await handler.dispatch(_track_interaction("remove", username="a", roles=["admin1"]))
    resp = await handler.dispatch(_track_interaction("list"))
    assert resp["data"]["embeds"][0]["description"] == "b"  # a removed, only b remains


@pytest.mark.asyncio
async def test_help_returns_immediate_reference(track_handler) -> None:
    handler, _ = track_handler
    resp = await handler.dispatch({"type": 2, "application_id": "app", "token": "tok", "data": {"name": "help"}})
    assert resp["type"] == responses.CHANNEL_MESSAGE
    assert "/track add" in resp["data"]["embeds"][0]["description"]


# --- Slow path: dispatch defers + enqueues; worker builds the embed ---


@pytest.fixture
def slow_handler():
    repos = SimpleNamespace(repo_cache=FakeRepoCache(), tracked_users=None, config=None, processed_commits=None)
    enqueuer = FakeEnqueuer()
    rest = FakeRest()
    handler = CommandHandler(repos, enqueuer, get_settings(), rest)
    return handler, enqueuer, rest


@pytest.mark.asyncio
async def test_repo_defers_and_enqueues(slow_handler) -> None:
    handler, enqueuer, _ = slow_handler
    resp = await handler.dispatch(_slash("repo", repo="octocat/hello-world"))
    assert resp["type"] == responses.DEFERRED_CHANNEL_MESSAGE
    assert len(enqueuer.followups) == 1
    assert enqueuer.followups[0]["command"] == "repo"
    assert enqueuer.followups[0]["options"]["repo"] == "octocat/hello-world"


@pytest.mark.asyncio
async def test_malformed_repo_validates_without_github_or_enqueue(slow_handler) -> None:
    handler, enqueuer, _ = slow_handler
    resp = await handler.dispatch(_slash("repo", repo="not-a-repo"))
    assert resp["data"]["flags"] == responses.EPHEMERAL_FLAG
    assert enqueuer.followups == []  # no task enqueued


@respx.mock
@pytest.mark.asyncio
async def test_repo_followup_builds_embed_and_patches(slow_handler) -> None:
    handler, _, rest = slow_handler
    respx.get(f"{BASE}/repos/octocat/hello-world").mock(
        return_value=httpx.Response(200, json={
            "description": "My repo", "language": "Python",
            "stargazers_count": 42, "forks_count": 3, "default_branch": "main",
        })
    )
    respx.get(f"{BASE}/repos/octocat/hello-world/commits").mock(
        return_value=httpx.Response(200, json=[
            {"sha": "a", "commit": {"message": "Add feature", "author": {"name": "Jay", "date": "2026-07-01T00:00:00Z"}}, "html_url": "u"}
        ])
    )
    respx.get(f"{BASE}/repos/octocat/hello-world/contributors").mock(
        return_value=httpx.Response(200, json=[{"login": "jay"}])
    )
    await handler.run_followup({
        "application_id": "app", "interaction_token": "tok",
        "command": "repo", "sub": None, "options": {"repo": "octocat/hello-world"},
    })
    assert len(rest.edits) == 1
    embed = rest.edits[0]["embeds"][0]
    assert embed["title"] == "octocat/hello-world"
    assert embed["description"] == "My repo"
    field_names = {f["name"] for f in embed["fields"]}
    assert {"Stars", "Recent commits", "Contributors"} <= field_names


@respx.mock
@pytest.mark.asyncio
async def test_repo_followup_not_found_renders_friendly(slow_handler) -> None:
    handler, _, rest = slow_handler
    respx.get(f"{BASE}/repos/no/repo").mock(return_value=httpx.Response(404))
    await handler.run_followup({
        "application_id": "app", "interaction_token": "tok",
        "command": "repo", "sub": None, "options": {"repo": "no/repo"},
    })
    assert rest.edits[0]["embeds"][0]["title"] == "Not found"
