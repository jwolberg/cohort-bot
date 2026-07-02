"""U9 tests: daily digest pipeline (emulator + GitHub/Claude/Discord mocked)."""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import get_settings
from app.digest.formatter import format_digest
from app.digest.pipeline import DigestPipeline, RepoSection, UserSection
from app.discord import interactions as interactions_module
from app.github.client import GitHubClient
from app.main import create_app
from app.store.repositories import get_repositories
from app.tasks import auth as auth_module

BASE = "https://api.github.com"


class FakeSummarizer:
    async def summarize(self, *, repo_description, commit_messages, commit_count):
        return f"Summary of {commit_count} commits."


class FakeEnqueuer:
    def __init__(self):
        self.digest_users: list[dict] = []

    async def enqueue_digest_user(self, payload):
        self.digest_users.append(payload)
        return "task/1"


class FakeRest:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.posts: list[dict] = []

    async def post_channel_message(self, channel_id, *, embeds=None, content=None):
        if self.fail:
            raise RuntimeError("discord unavailable")
        self.posts.append({"channel": channel_id, "embeds": embeds, "content": content})
        return {"id": "msg1"}


def _push_event(repo, commits, created_at="2026-07-02T10:00:00Z"):
    return {
        "type": "PushEvent",
        "created_at": created_at,
        "repo": {"name": repo},
        "payload": {"commits": [{"sha": s, "message": m, "author": {"name": "Jay"}} for s, m in commits]},
    }


def _mock_user(username, events):
    respx.get(f"{BASE}/users/{username}/events/public").mock(
        return_value=httpx.Response(200, json=events)
    )


def _mock_repo(repo, description="A repo"):
    respx.get(f"{BASE}/repos/{repo}").mock(
        return_value=httpx.Response(200, json={
            "description": description, "language": "Python",
            "stargazers_count": 1, "forks_count": 0, "default_branch": "main",
        })
    )


def _pipeline(repos, rest, enqueuer=None):
    return DigestPipeline(
        repos, enqueuer or FakeEnqueuer(), get_settings(), rest, FakeSummarizer()
    )


# --- Idempotency / cursor (execution note: write first) ---


@respx.mock
@pytest.mark.asyncio
async def test_cursor_advances_only_after_successful_post(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await repos.config.update({"digest_channel_id": "chan"})
    await repos.tracked_users.add("jay", added_by="admin")
    _mock_user("jay", [_push_event("o/r", [("s1", "Add feature")])])
    _mock_repo("o/r")

    # 1) Post fails → cursor unchanged, SHA not recorded.
    rest = FakeRest(fail=True)
    pipeline = _pipeline(repos, rest)
    with pytest.raises(RuntimeError):
        await pipeline.process_user("jay")
    assert await repos.tracked_users.get_cursor("jay") is None
    assert await repos.processed_commits.has_sha("o/r", "s1") is False

    # 2) Post succeeds → SHA recorded, cursor advanced.
    rest.fail = False
    posted = await pipeline.process_user("jay")
    assert posted is True
    assert len(rest.posts) == 1
    assert await repos.processed_commits.has_sha("o/r", "s1") is True
    assert await repos.tracked_users.get_cursor("jay") is not None

    # 3) Re-run same day posts nothing new (idempotent).
    posted_again = await pipeline.process_user("jay")
    assert posted_again is False
    assert len(rest.posts) == 1


@respx.mock
@pytest.mark.asyncio
async def test_per_user_filters_already_processed_shas(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await repos.processed_commits.record_shas("o/r", ["s1"])  # already reported
    _mock_user("jay", [_push_event("o/r", [("s1", "old"), ("s2", "new")])])
    _mock_repo("o/r")
    pipeline = _pipeline(repos, FakeRest())

    async with GitHubClient(get_settings().github_token, repos.repo_cache) as gh:
        section = await pipeline.compute_section(gh, "jay", None)
    assert section is not None
    assert section.total == 1
    assert section.new_shas["o/r"] == ["s2"]  # s1 filtered out


@respx.mock
@pytest.mark.asyncio
async def test_user_with_no_new_commits_is_omitted(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    _mock_user("ghost", [])
    pipeline = _pipeline(repos, FakeRest())
    async with GitHubClient(get_settings().github_token, repos.repo_cache) as gh:
        section = await pipeline.compute_section(gh, "ghost", None)
    assert section is None


@respx.mock
@pytest.mark.asyncio
async def test_isolated_failure_raises_for_retry(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await repos.config.update({"digest_channel_id": "chan"})
    _mock_user("jay", [_push_event("o/r", [("s1", "m")])])
    _mock_repo("o/r")
    pipeline = _pipeline(repos, FakeRest(fail=True))
    # Cloud Tasks retries on the raised error; other users are unaffected.
    with pytest.raises(RuntimeError):
        await pipeline.process_user("jay")


# --- Fan-out ---


@respx.mock
@pytest.mark.asyncio
async def test_fanout_enqueues_one_task_per_enabled_user(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await repos.config.update({"digest_channel_id": "chan"})
    await repos.tracked_users.add("jay", added_by="a")
    await repos.tracked_users.add("sarah", added_by="a")
    enqueuer = FakeEnqueuer()
    rest = FakeRest()
    pipeline = _pipeline(repos, rest, enqueuer)

    count = await pipeline.run_fanout()
    assert count == 2
    assert {p["username"] for p in enqueuer.digest_users} == {"jay", "sarah"}
    assert len(rest.posts) == 1  # header posted once
    assert "GitHub Daily Digest" in rest.posts[0]["content"]


@respx.mock
@pytest.mark.asyncio
async def test_fanout_tolerates_enqueue_failure_no_header_dup(firestore_client) -> None:
    from app.tasks.queue import EnqueueError

    repos = get_repositories(firestore_client)
    await repos.config.update({"digest_channel_id": "chan"})
    await repos.tracked_users.add("jay", added_by="a")
    await repos.tracked_users.add("sarah", added_by="a")

    class FlakyEnqueuer:
        def __init__(self):
            self.ok: list[dict] = []

        async def enqueue_digest_user(self, payload):
            if payload["username"] == "jay":
                raise EnqueueError("transient")
            self.ok.append(payload)

    rest = FakeRest()
    pipeline = DigestPipeline(repos, FlakyEnqueuer(), get_settings(), rest, FakeSummarizer())
    # A single enqueue failure must not raise (which would 500 → Scheduler retry
    # → duplicate header). Header posts exactly once; the healthy user enqueues.
    await pipeline.run_fanout()
    assert len(rest.posts) == 1


# --- On-demand ---


@respx.mock
@pytest.mark.asyncio
async def test_on_demand_builds_batched_embeds(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await repos.tracked_users.add("jay", added_by="a")
    _mock_user("jay", [_push_event("o/r", [("s1", "Add API endpoint")])])
    _mock_repo("o/r")
    pipeline = _pipeline(repos, FakeRest())
    embeds = await pipeline.on_demand("today")
    assert embeds[0]["title"] == "GitHub Daily Digest"
    assert "Today" in embeds[0]["description"]
    assert any("jay" in f["name"] for f in embeds[0]["fields"])


# --- Formatter: PRD AE + pagination ---


def _section(name, count=3, summary="Backend work"):
    return UserSection(name, count, [RepoSection("o/r", count, "desc", summary)], {}, None)


def test_format_digest_renders_eight_users_with_counts_and_summaries() -> None:
    sections = [_section(f"dev{i}", count=i + 1) for i in range(8)]
    embeds = format_digest("July 2", sections)
    assert embeds[0]["description"].startswith("July 2")
    assert "8 developers active" in embeds[0]["description"]
    all_values = " ".join(f["value"] for e in embeds for f in e["fields"])
    assert "Backend work" in all_values
    assert "1 commit" in all_values  # dev0 has 1


def test_format_digest_paginates_beyond_field_limit() -> None:
    sections = [_section(f"dev{i}") for i in range(40)]  # > 25 fields
    embeds = format_digest("July 2", sections)
    assert len(embeds) >= 2  # paginated, not truncated
    total_fields = sum(len(e["fields"]) for e in embeds)
    assert total_fields == 40  # every user represented


def test_format_digest_empty_reports_no_activity() -> None:
    embeds = format_digest("Today", [])
    assert "No activity" in embeds[0]["description"]


# --- Digest task endpoints (OIDC) ---


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_digest_run_requires_oidc(client: TestClient) -> None:
    assert client.post("/tasks/digest/run").status_code == 401


def test_digest_user_missing_username_400(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(auth_module, "verify_oidc_token", lambda t, a: {"email": "digest-bot-sa@cohort-bot-test.iam.gserviceaccount.com"})
    resp = client.post("/tasks/digest/user", json={}, headers={"Authorization": "Bearer x"})
    assert resp.status_code == 400


def test_digest_run_valid_dispatches_to_runner(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(auth_module, "verify_oidc_token", lambda t, a: {"email": "digest-bot-sa@cohort-bot-test.iam.gserviceaccount.com"})
    called = {}

    async def runner():
        called["ran"] = True

    async def worker(u):
        called["user"] = u

    interactions_module.set_digest_handlers(runner, worker)
    resp = client.post("/tasks/digest/run", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert called.get("ran") is True
