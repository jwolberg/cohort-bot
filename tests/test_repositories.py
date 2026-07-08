"""U2 integration tests — run against the Firestore emulator (not mocks)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.store.repositories import get_repositories

pytestmark = pytest.mark.asyncio


@pytest.fixture
def repos(firestore_client):
    return get_repositories(firestore_client)


async def test_add_user_appears_in_list_enabled(repos) -> None:
    await repos.tracked_users.add("octocat", added_by="admin#1")
    enabled = await repos.tracked_users.list_enabled()
    assert [u["username"] for u in enabled] == ["octocat"]


async def test_duplicate_add_is_idempotent(repos) -> None:
    await repos.tracked_users.add("octocat", added_by="admin#1")
    await repos.tracked_users.add("octocat", added_by="admin#2")
    enabled = await repos.tracked_users.list_enabled()
    assert len(enabled) == 1
    # Original adder is preserved (re-add does not overwrite created metadata).
    assert enabled[0]["added_by"] == "admin#1"


async def test_add_user_stores_group_and_defaults_to_cohort(repos) -> None:
    await repos.tracked_users.add("octocat", added_by="admin#1")  # no group → cohort
    await repos.tracked_users.add("karpathy", added_by="admin#1", group="leaders")
    enabled = {u["username"]: u["group"] for u in await repos.tracked_users.list_enabled()}
    assert enabled == {"octocat": "cohort", "karpathy": "leaders"}


async def test_re_add_updates_group_preserving_metadata(repos) -> None:
    await repos.tracked_users.add("octocat", added_by="admin#1", group="cohort")
    await repos.tracked_users.add("octocat", added_by="admin#2", group="leaders")
    enabled = await repos.tracked_users.list_enabled()
    assert len(enabled) == 1
    assert enabled[0]["group"] == "leaders"  # re-add moved the group
    assert enabled[0]["added_by"] == "admin#1"  # original metadata preserved


async def test_remove_excludes_from_list_enabled(repos) -> None:
    await repos.tracked_users.add("octocat", added_by="admin#1")
    await repos.tracked_users.add("hubot", added_by="admin#1")
    await repos.tracked_users.remove("octocat")
    enabled = await repos.tracked_users.list_enabled()
    assert [u["username"] for u in enabled] == ["hubot"]


async def test_record_and_check_shas(repos) -> None:
    repo = "octocat/hello-world"
    await repos.processed_commits.record_shas(repo, ["abc123", "def456"])
    assert await repos.processed_commits.has_sha(repo, "abc123") is True
    assert await repos.processed_commits.has_sha(repo, "def456") is True
    # Unknown SHA is not deduped.
    assert await repos.processed_commits.has_sha(repo, "zzz999") is False
    # SHA in a different repo does not collide (dedup key is repo + sha).
    assert await repos.processed_commits.has_sha("other/repo", "abc123") is False


async def test_record_shas_empty_is_noop(repos) -> None:
    await repos.processed_commits.record_shas("octocat/hello-world", [])
    assert await repos.processed_commits.has_sha("octocat/hello-world", "x") is False


async def test_cursor_round_trips_per_user(repos) -> None:
    await repos.tracked_users.add("octocat", added_by="admin#1")
    cursor = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)
    await repos.tracked_users.set_cursor("octocat", cursor)
    read_back = await repos.tracked_users.get_cursor("octocat")
    assert read_back == cursor


async def test_repo_cache_preserves_etag_and_fetched_at(repos) -> None:
    await repos.repo_cache.put(
        "octocat/hello-world",
        {
            "description": "My first repository",
            "language": "Ruby",
            "stars": 42,
            "forks": 7,
            "default_branch": "main",
            "etag": 'W/"abc123etag"',
        },
    )
    cached = await repos.repo_cache.get("octocat/hello-world")
    assert cached is not None
    assert cached["etag"] == 'W/"abc123etag"'
    assert cached["stars"] == 42
    assert cached["fetched_at"] is not None  # server-stamped


async def test_list_enabled_empty_returns_empty_not_error(repos) -> None:
    assert await repos.tracked_users.list_enabled() == []


# --- Substack: tracked_publications + processed_posts (S1) ---


async def test_add_publication_appears_in_list_enabled(repos) -> None:
    await repos.tracked_publications.add(
        "pragmaticengineer.substack.com",
        "https://pragmaticengineer.substack.com/feed",
        title="The Pragmatic Engineer",
        added_by="admin-panel",
    )
    enabled = await repos.tracked_publications.list_enabled()
    assert [p["slug"] for p in enabled] == ["pragmaticengineer.substack.com"]
    assert enabled[0]["title"] == "The Pragmatic Engineer"


async def test_publication_add_initializes_cursor_to_add_time(repos) -> None:
    # A5: cursor is set on add so the back catalog is never dumped.
    await repos.tracked_publications.add(
        "x.substack.com", "https://x.substack.com/feed", added_by="admin"
    )
    cursor = await repos.tracked_publications.get_cursor("x.substack.com")
    assert isinstance(cursor, datetime)


async def test_publication_reAdd_is_idempotent_preserving_cursor(repos) -> None:
    await repos.tracked_publications.add(
        "x.substack.com", "https://x.substack.com/feed", added_by="admin#1"
    )
    original = await repos.tracked_publications.get_cursor("x.substack.com")
    await repos.tracked_publications.add(
        "x.substack.com", "https://x.substack.com/feed", added_by="admin#2"
    )
    enabled = await repos.tracked_publications.list_enabled()
    assert len(enabled) == 1
    # created_at/last_cursor are preserved across a re-add.
    assert await repos.tracked_publications.get_cursor("x.substack.com") == original


async def test_remove_publication_excludes_from_list_enabled(repos) -> None:
    await repos.tracked_publications.add("a.substack.com", "https://a.substack.com/feed", added_by="a")
    await repos.tracked_publications.add("b.substack.com", "https://b.substack.com/feed", added_by="a")
    await repos.tracked_publications.remove("a.substack.com")
    enabled = await repos.tracked_publications.list_enabled()
    assert [p["slug"] for p in enabled] == ["b.substack.com"]


async def test_publication_cursor_round_trips(repos) -> None:
    await repos.tracked_publications.add("x.substack.com", "https://x.substack.com/feed", added_by="a")
    cursor = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)
    await repos.tracked_publications.set_cursor("x.substack.com", cursor)
    assert await repos.tracked_publications.get_cursor("x.substack.com") == cursor


async def test_record_and_check_posts(repos) -> None:
    slug = "x.substack.com"
    # post ids are often URLs/guids containing "/".
    p1 = "https://x.substack.com/p/first-post"
    p2 = "https://x.substack.com/p/second-post"
    await repos.processed_posts.record_posts(slug, [p1, p2])
    assert await repos.processed_posts.has_post(slug, p1) is True
    assert await repos.processed_posts.has_post(slug, p2) is True
    assert await repos.processed_posts.has_post(slug, "https://x.substack.com/p/unseen") is False
    # Same post id under a different publication does not collide.
    assert await repos.processed_posts.has_post("other.substack.com", p1) is False


async def test_record_posts_empty_is_noop(repos) -> None:
    await repos.processed_posts.record_posts("x.substack.com", [])
    assert await repos.processed_posts.has_post("x.substack.com", "p") is False


async def test_config_defaults_then_update(repos) -> None:
    # Missing config returns defaults.
    config = await repos.config.get()
    assert config["digest_channel_id"] == ""
    assert config["leaders_channel_id"] == ""
    assert config["admin_role_ids"] == []

    await repos.config.update(
        {"digest_channel_id": "999", "digest_hour_utc": 9, "admin_role_ids": ["r1", "r2"]}
    )
    updated = await repos.config.get()
    assert updated["digest_channel_id"] == "999"
    assert updated["digest_hour_utc"] == 9
    assert updated["admin_role_ids"] == ["r1", "r2"]
