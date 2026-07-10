"""U9 tests: daily digest pipeline (emulator + GitHub/Claude/Discord mocked)."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import get_settings
from app.digest.formatter import (
    format_digest,
    format_publication_section,
    format_substack,
    format_user_section,
)
from app.digest.pipeline import (
    DigestPipeline,
    PublicationSection,
    RepoSection,
    UserSection,
    _is_low_signal,
)
from app.store.repositories import channel_for_group
from app.substack.client import PostRef, SubstackError
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
        self.substack_pubs: list[dict] = []

    async def enqueue_digest_user(self, payload):
        self.digest_users.append(payload)
        return "task/1"

    async def enqueue_substack_publication(self, payload):
        self.substack_pubs.append(payload)
        return "task/2"


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
    # PushEvent payloads no longer inline commits; the client hydrates the
    # before...head range via the compare API. Mirror that: emit before/head and
    # register the matching compare mock returning the commits.
    shas = [s for s, _ in commits]
    before = "before_" + "_".join(shas)
    head = "head_" + "_".join(shas)
    respx.get(f"{BASE}/repos/{repo}/compare/{before}...{head}").mock(
        return_value=httpx.Response(
            200,
            json={
                "commits": [
                    {"sha": s, "commit": {"message": m, "author": {"name": "Jay"}}}
                    for s, m in commits
                ]
            },
        )
    )
    return {
        "type": "PushEvent",
        "created_at": created_at,
        "repo": {"name": repo},
        "payload": {"before": before, "head": head},
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


def _post(post_id, day, *, title="A post", excerpt="Body"):
    return PostRef(
        slug="ex.substack.com",
        post_id=post_id,
        title=title,
        url=f"https://ex.substack.com/p/{post_id}",
        author="Writer",
        published=datetime(2026, 7, day, 10, 0, tzinfo=timezone.utc),
        excerpt=excerpt,
    )


class FakeSubstackClient:
    """Async-CM stand-in for SubstackClient with preset posts or an error."""

    def __init__(self, posts=None, error=None):
        self._posts = posts or []
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetch_posts_since(self, feed_url, since, *, limit=20):
        if self._error is not None:
            raise self._error
        if since is None:
            return list(self._posts)
        return [p for p in self._posts if p.published > since]


# --- Low-signal commit classifier ---


@pytest.mark.parametrize(
    "message",
    [
        "chore: bump deps",
        "docs: rewrite guide",
        "docs(readme): tweak",
        "fix: null deref",
        "fix!: breaking fix",
        "Fix crash on startup",
        "Update README",
        "Bump version to 2.0",
        "typo",
    ],
)
def test_is_low_signal_true(message) -> None:
    assert _is_low_signal(message) is True


def test_channel_for_group_maps_each_group_to_its_channel() -> None:
    config = {"digest_channel_id": "cohort-chan", "leaders_channel_id": "leaders-chan"}
    assert channel_for_group(config, "cohort") == "cohort-chan"
    assert channel_for_group(config, "leaders") == "leaders-chan"
    # An unset channel resolves to ""; an unknown group falls back to cohort's.
    assert channel_for_group({}, "leaders") == ""
    assert channel_for_group(config, "unknown") == "cohort-chan"


@pytest.mark.parametrize(
    "message",
    [
        "feat: add dashboard",
        "Add export endpoint",
        "refactor: extract service",
        "perf: cache lookups",
        "fixture: seed test data",  # "fix" prefix must not match
        "Improve docs discoverability",  # "docs" mid-sentence must not match
    ],
)
def test_is_low_signal_false(message) -> None:
    assert _is_low_signal(message) is False


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
async def test_low_signal_commits_are_filtered_but_cursor_advances(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await repos.config.update({"digest_channel_id": "chan"})
    await repos.tracked_users.add("jay", added_by="a")
    _mock_user(
        "jay",
        [
            _push_event(
                "o/r",
                [
                    ("s1", "feat: add dashboard"),
                    ("s2", "chore: bump deps"),
                    ("s3", "docs(readme): update setup"),
                    ("s4", "fix: correct off-by-one"),
                    ("s5", "Add export endpoint"),
                ],
            )
        ],
    )
    _mock_repo("o/r")
    rest = FakeRest()
    pipeline = _pipeline(repos, rest)

    posted = await pipeline.process_user("jay")
    assert posted is True
    # Only the two signal commits are reported/recorded; chore/docs/fix dropped.
    assert await repos.processed_commits.has_sha("o/r", "s1") is True
    assert await repos.processed_commits.has_sha("o/r", "s5") is True
    for dropped in ("s2", "s3", "s4"):
        assert await repos.processed_commits.has_sha("o/r", dropped) is False
    # Cursor still advances past the whole batch so noise isn't re-scanned.
    assert await repos.tracked_users.get_cursor("jay") is not None


@respx.mock
@pytest.mark.asyncio
async def test_all_low_signal_commits_yields_no_section(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    _mock_user(
        "jay",
        [_push_event("o/r", [("s1", "chore: tidy"), ("s2", "fix typo in log")])],
    )
    _mock_repo("o/r")
    pipeline = _pipeline(repos, FakeRest())
    async with GitHubClient(get_settings().github_token, repos.repo_cache) as gh:
        section = await pipeline.compute_section(gh, "jay", None)
    assert section is None


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


# --- Substack section compute + formatter (S3a) ---


@respx.mock
@pytest.mark.asyncio
async def test_publication_section_dedups_processed_posts(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await repos.processed_posts.record_posts("ex.substack.com", ["p1"])  # already reported
    pipeline = _pipeline(repos, FakeRest())
    client = FakeSubstackClient(posts=[_post("p1", 2), _post("p2", 3)])
    publication = {"slug": "ex.substack.com", "feed_url": "https://ex.substack.com/feed", "title": "Ex"}

    section = await pipeline.compute_publication_section(client, publication, None)
    assert section is not None
    assert section.new_post_ids == ["p2"]  # p1 filtered
    assert section.title == "Ex"
    # Cursor advances past the whole fetched batch (newest = p2 on Jul 3).
    assert section.new_cursor == datetime(2026, 7, 3, 10, 0, tzinfo=timezone.utc)


@respx.mock
@pytest.mark.asyncio
async def test_publication_section_on_demand_skips_dedup(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await repos.processed_posts.record_posts("ex.substack.com", ["p1"])
    pipeline = _pipeline(repos, FakeRest())
    client = FakeSubstackClient(posts=[_post("p1", 2), _post("p2", 3)])
    publication = {"slug": "ex.substack.com", "feed_url": "https://ex.substack.com/feed"}

    section = await pipeline.compute_publication_section(client, publication, None, dedup=False)
    assert section is not None
    assert section.new_post_ids == ["p1", "p2"]  # no dedup on-demand
    assert section.title == "ex.substack.com"  # falls back to slug when title absent


@respx.mock
@pytest.mark.asyncio
async def test_publication_section_skips_broken_feed(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    pipeline = _pipeline(repos, FakeRest())
    client = FakeSubstackClient(error=SubstackError("boom"))
    publication = {"slug": "ex.substack.com", "feed_url": "https://ex.substack.com/feed"}

    section = await pipeline.compute_publication_section(client, publication, None)
    assert section is None  # best-effort skip, no raise


def test_format_publication_section_renders_posts() -> None:
    section = PublicationSection(
        slug="ex.substack.com",
        title="The Example",
        feed_url="https://ex.substack.com/feed",
        posts=[_post("p1", 2, title="Hello", excerpt="An excerpt")],
    )
    embed = format_publication_section(section)
    assert embed["title"] == "📰 The Example"
    assert embed["description"] == "1 new post"
    assert '"Hello"' == embed["fields"][0]["name"]
    assert "An excerpt" in embed["fields"][0]["value"]


def test_format_substack_empty_reports_no_posts() -> None:
    embeds = format_substack("Last 1 day", [])
    assert "No recent posts" in embeds[0]["description"]


# --- Substack scheduled worker (S3b/S4) ---


def _substack_pipeline(repos, rest, posts=None, error=None, enqueuer=None):
    return DigestPipeline(
        repos, enqueuer or FakeEnqueuer(), get_settings(), rest, FakeSummarizer(),
        substack_factory=lambda: FakeSubstackClient(posts=posts, error=error),
    )


@respx.mock
@pytest.mark.asyncio
async def test_process_publication_posts_and_advances_cursor(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await repos.config.update({"digest_channel_id": "chan"})
    await repos.tracked_publications.add(
        "ex.substack.com", "https://ex.substack.com/feed", title="Ex", added_by="a"
    )
    # Pin the cursor before the sample posts so they count as new.
    await repos.tracked_publications.set_cursor(
        "ex.substack.com", datetime(2026, 7, 1, tzinfo=timezone.utc)
    )
    rest = FakeRest()
    pipeline = _substack_pipeline(repos, rest, posts=[_post("p1", 2), _post("p2", 3)])

    posted = await pipeline.process_publication("ex.substack.com")
    assert posted is True
    assert len(rest.posts) == 1
    assert await repos.processed_posts.has_post("ex.substack.com", "p1") is True
    assert await repos.processed_posts.has_post("ex.substack.com", "p2") is True
    assert await repos.tracked_publications.get_cursor("ex.substack.com") == datetime(
        2026, 7, 3, 10, 0, tzinfo=timezone.utc
    )

    # Re-run posts nothing new (idempotent per publication).
    assert await pipeline.process_publication("ex.substack.com") is False
    assert len(rest.posts) == 1


@respx.mock
@pytest.mark.asyncio
async def test_process_publication_post_failure_leaves_state(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await repos.config.update({"digest_channel_id": "chan"})
    await repos.tracked_publications.add(
        "ex.substack.com", "https://ex.substack.com/feed", added_by="a"
    )
    await repos.tracked_publications.set_cursor(
        "ex.substack.com", datetime(2026, 7, 1, tzinfo=timezone.utc)
    )
    pipeline = _substack_pipeline(repos, FakeRest(fail=True), posts=[_post("p1", 2)])

    with pytest.raises(RuntimeError):
        await pipeline.process_publication("ex.substack.com")
    # Post failed → dedup key not recorded, cursor unchanged (retry-safe).
    assert await repos.processed_posts.has_post("ex.substack.com", "p1") is False
    assert await repos.tracked_publications.get_cursor("ex.substack.com") == datetime(
        2026, 7, 1, tzinfo=timezone.utc
    )


@respx.mock
@pytest.mark.asyncio
async def test_fanout_enqueues_one_task_per_publication(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await repos.config.update({"digest_channel_id": "chan"})
    await repos.tracked_users.add("jay", added_by="a")
    await repos.tracked_publications.add("a.substack.com", "https://a.substack.com/feed", added_by="a")
    await repos.tracked_publications.add("b.substack.com", "https://b.substack.com/feed", added_by="a")
    enqueuer = FakeEnqueuer()
    pipeline = _pipeline(repos, FakeRest(), enqueuer)

    await pipeline.run_fanout()
    assert {p["slug"] for p in enqueuer.substack_pubs} == {"a.substack.com", "b.substack.com"}


@respx.mock
@pytest.mark.asyncio
async def test_on_demand_substack_lists_recent_posts(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await repos.tracked_publications.add(
        "ex.substack.com", "https://ex.substack.com/feed", title="Ex", added_by="a"
    )
    # 30d window reaches back past the July sample dates regardless of run time.
    pipeline = _substack_pipeline(repos, FakeRest(), posts=[_post("p1", 2), _post("p2", 3)])
    embeds = await pipeline.on_demand_substack("30d")
    assert embeds[0]["title"] == "📰 Ex"
    assert "2 posts" in embeds[0]["description"]


@respx.mock
@pytest.mark.asyncio
async def test_on_demand_substack_empty_when_no_publications(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    pipeline = _substack_pipeline(repos, FakeRest(), posts=[])
    embeds = await pipeline.on_demand_substack(None)
    assert "No recent posts" in embeds[0]["description"]


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
async def test_fanout_routes_groups_to_distinct_channels(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await repos.config.update(
        {"digest_channel_id": "cohort-chan", "leaders_channel_id": "leaders-chan"}
    )
    await repos.tracked_users.add("jay", added_by="a", group="cohort")
    await repos.tracked_users.add("karpathy", added_by="a", group="leaders")
    enqueuer = FakeEnqueuer()
    rest = FakeRest()
    pipeline = _pipeline(repos, rest, enqueuer)

    await pipeline.run_fanout()

    # One header per non-empty group, each to its own channel.
    headers = {p["channel"]: p["content"] for p in rest.posts if p["content"]}
    assert set(headers) == {"cohort-chan", "leaders-chan"}
    assert "GitHub Daily Digest" in headers["cohort-chan"]
    assert "AI Industry Leaders" in headers["leaders-chan"]
    # Every user is still enqueued exactly once.
    assert {p["username"] for p in enqueuer.digest_users} == {"jay", "karpathy"}


@respx.mock
@pytest.mark.asyncio
async def test_fanout_skips_group_with_no_channel(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    # Only the cohort channel is configured; the leaders group has none.
    await repos.config.update({"digest_channel_id": "cohort-chan"})
    await repos.tracked_users.add("jay", added_by="a", group="cohort")
    await repos.tracked_users.add("karpathy", added_by="a", group="leaders")
    enqueuer = FakeEnqueuer()
    rest = FakeRest()
    pipeline = _pipeline(repos, rest, enqueuer)

    await pipeline.run_fanout()

    # Leaders has no channel → no header, and its users are not enqueued.
    assert [p["channel"] for p in rest.posts] == ["cohort-chan"]
    assert {p["username"] for p in enqueuer.digest_users} == {"jay"}


@respx.mock
@pytest.mark.asyncio
async def test_process_user_posts_to_its_group_channel(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await repos.config.update(
        {"digest_channel_id": "cohort-chan", "leaders_channel_id": "leaders-chan"}
    )
    await repos.tracked_users.add("karpathy", added_by="a", group="leaders")
    _mock_user("karpathy", [_push_event("o/r", [("s1", "Add feature")])])
    _mock_repo("o/r")
    rest = FakeRest()
    pipeline = _pipeline(repos, rest)

    posted = await pipeline.process_user("karpathy")
    assert posted is True
    assert rest.posts[0]["channel"] == "leaders-chan"  # routed by the user's group


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
    # Stamp the event at "now" so it falls inside the on-demand "today" window
    # regardless of the calendar date the suite runs on.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _mock_user("jay", [_push_event("o/r", [("s1", "Add API endpoint")], created_at=now)])
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


def test_format_user_section_links_each_repo_title() -> None:
    section = UserSection(
        "jay", 3, [RepoSection("acme/api", 3, "desc", "Backend work")], {}, None
    )
    embed = format_user_section(section)
    # Field names render no markdown, so the clickable title leads the value.
    assert embed["fields"][0]["name"] == "acme/api"
    assert embed["fields"][0]["value"].startswith(
        "[**acme/api**](https://github.com/acme/api)"
    )
    assert "Backend work" in embed["fields"][0]["value"]


def test_format_digest_links_each_repo_title() -> None:
    embeds = format_digest("July 2", [_section("jay")])
    value = embeds[0]["fields"][0]["value"]
    assert value.startswith("[**o/r**](https://github.com/o/r)")
    assert "Backend work" in value


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


def test_substack_publication_requires_oidc(client: TestClient) -> None:
    assert client.post("/tasks/substack/publication").status_code == 401


def test_substack_publication_missing_slug_400(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(auth_module, "verify_oidc_token", lambda t, a: {"email": "digest-bot-sa@cohort-bot-test.iam.gserviceaccount.com"})
    resp = client.post("/tasks/substack/publication", json={}, headers={"Authorization": "Bearer x"})
    assert resp.status_code == 400


def test_substack_publication_dispatches_to_worker(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(auth_module, "verify_oidc_token", lambda t, a: {"email": "digest-bot-sa@cohort-bot-test.iam.gserviceaccount.com"})
    called = {}

    async def worker(slug):
        called["slug"] = slug

    interactions_module.set_publication_worker(worker)
    resp = client.post("/tasks/substack/publication", json={"slug": "ex.substack.com"}, headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    assert called.get("slug") == "ex.substack.com"
