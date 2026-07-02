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


async def test_config_defaults_then_update(repos) -> None:
    # Missing config returns defaults.
    config = await repos.config.get()
    assert config["digest_channel_id"] == ""
    assert config["admin_role_ids"] == []

    await repos.config.update(
        {"digest_channel_id": "999", "digest_hour_utc": 9, "admin_role_ids": ["r1", "r2"]}
    )
    updated = await repos.config.get()
    assert updated["digest_channel_id"] == "999"
    assert updated["digest_hour_utc"] == 9
    assert updated["admin_role_ids"] == ["r1", "r2"]
