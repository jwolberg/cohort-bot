"""U12 tests: structured logging + digest SLO heartbeat."""

from __future__ import annotations

import json
import logging

import pytest
import respx

from app.config import get_settings
from app.digest.pipeline import HEARTBEAT_EVENT, DigestPipeline
from app.logging import JsonFormatter, log_event
from app.store.repositories import get_repositories
from tests.test_digest_pipeline import FakeEnqueuer, FakeRest, FakeSummarizer


def test_json_formatter_emits_severity_message_and_extra_fields() -> None:
    record = logging.LogRecord(
        name="app.test", level=logging.INFO, pathname="", lineno=0,
        msg="digest_heartbeat", args=(), exc_info=None,
    )
    record.users = 3
    record.channel = "chan"
    line = JsonFormatter().format(record)
    payload = json.loads(line)
    assert payload["severity"] == "INFO"
    assert payload["message"] == "digest_heartbeat"
    assert payload["users"] == 3
    assert payload["channel"] == "chan"


def test_log_event_attaches_structured_fields(caplog) -> None:
    logger = logging.getLogger("app.test.event")
    with caplog.at_level(logging.INFO, logger="app.test.event"):
        log_event(logger, "digest_run", users=5, commits=42)
    record = next(r for r in caplog.records if r.message == "digest_run")
    assert record.users == 5
    assert record.commits == 42


def _pipeline(repos, rest):
    return DigestPipeline(repos, FakeEnqueuer(), get_settings(), rest, FakeSummarizer())


@respx.mock
@pytest.mark.asyncio
async def test_successful_digest_emits_heartbeat(firestore_client, caplog) -> None:
    repos = get_repositories(firestore_client)
    await repos.config.update({"digest_channel_id": "chan"})
    await repos.tracked_users.add("jay", added_by="a")
    pipeline = _pipeline(repos, FakeRest())
    with caplog.at_level(logging.INFO):
        await pipeline.run_fanout()
    beats = [r for r in caplog.records if r.message == HEARTBEAT_EVENT]
    assert len(beats) == 1
    assert beats[0].users == 1
    assert hasattr(beats[0], "duration_ms")


@respx.mock
@pytest.mark.asyncio
async def test_failed_post_emits_no_heartbeat(firestore_client, caplog) -> None:
    repos = get_repositories(firestore_client)
    await repos.config.update({"digest_channel_id": "chan"})
    await repos.tracked_users.add("jay", added_by="a")
    pipeline = _pipeline(repos, FakeRest(fail=True))
    with caplog.at_level(logging.INFO):
        with pytest.raises(RuntimeError):
            await pipeline.run_fanout()
    assert [r for r in caplog.records if r.message == HEARTBEAT_EVENT] == []


@respx.mock
@pytest.mark.asyncio
async def test_missing_channel_warns_without_heartbeat(firestore_client, caplog) -> None:
    repos = get_repositories(firestore_client)
    await repos.tracked_users.add("jay", added_by="a")  # no digest_channel_id
    pipeline = _pipeline(repos, FakeRest())
    with caplog.at_level(logging.INFO):
        await pipeline.run_fanout()
    assert [r for r in caplog.records if r.message == HEARTBEAT_EVENT] == []
    assert any(r.message == "digest_not_posted" for r in caplog.records)
