"""U8 tests: Cloud Tasks enqueue + OIDC-protected worker endpoints."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.cloud import tasks_v2

from app.config import get_settings
from app.discord import interactions as interactions_module
from app.main import create_app
from app.tasks import auth as auth_module
from app.tasks.queue import EnqueueError, TaskEnqueuer


class FakeTasksClient:
    def __init__(self, *, raise_on_create: bool = False):
        self.created_request: dict | None = None
        self._raise = raise_on_create

    def queue_path(self, project: str, location: str, queue: str) -> str:
        return f"projects/{project}/locations/{location}/queues/{queue}"

    async def create_task(self, request):
        if self._raise:
            raise RuntimeError("cloud tasks unavailable")
        self.created_request = request
        return SimpleNamespace(name=request["parent"] + "/tasks/abc")


# --- Enqueue ---


@pytest.mark.asyncio
async def test_enqueue_followup_builds_correct_task() -> None:
    settings = get_settings()
    client = FakeTasksClient()
    enq = TaskEnqueuer(settings, client=client)

    name = await enq.enqueue_followup({"interaction_token": "tok", "command": "repo"})

    req = client.created_request
    assert req["parent"].endswith(f"/queues/{settings.followups_queue}")
    http = req["task"]["http_request"]
    assert http["url"] == "https://digest-bot-test.a.run.app/tasks/followup"
    assert http["http_method"] == tasks_v2.HttpMethod.POST
    assert http["oidc_token"]["audience"] == settings.effective_oidc_audience
    assert http["oidc_token"]["service_account_email"] == settings.task_invoker_sa_email
    body = json.loads(http["body"])
    assert body["command"] == "repo"
    assert name.endswith("/tasks/abc")


@pytest.mark.asyncio
async def test_enqueue_digest_user_uses_fanout_queue() -> None:
    settings = get_settings()
    client = FakeTasksClient()
    enq = TaskEnqueuer(settings, client=client)
    await enq.enqueue_digest_user({"username": "octocat"})
    req = client.created_request
    assert req["parent"].endswith(f"/queues/{settings.digest_fanout_queue}")
    assert req["task"]["http_request"]["url"].endswith("/tasks/digest/user")


@pytest.mark.asyncio
async def test_enqueue_failure_raises_typed_error() -> None:
    enq = TaskEnqueuer(get_settings(), client=FakeTasksClient(raise_on_create=True))
    with pytest.raises(EnqueueError):
        await enq.enqueue_followup({"x": 1})


# --- OIDC-protected /tasks/followup ---


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_followup_missing_token_returns_401(client: TestClient) -> None:
    resp = client.post("/tasks/followup", json={"command": "repo"})
    assert resp.status_code == 401


def test_followup_invalid_token_returns_403(client: TestClient, monkeypatch) -> None:
    def _reject(token, audience):
        raise ValueError("bad token")

    monkeypatch.setattr(auth_module, "verify_oidc_token", _reject)
    resp = client.post(
        "/tasks/followup",
        json={"command": "repo"},
        headers={"Authorization": "Bearer garbage"},
    )
    assert resp.status_code == 403


SA_EMAIL = "digest-bot-sa@cohort-bot-test.iam.gserviceaccount.com"


def test_followup_valid_token_dispatches_to_handler(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        auth_module, "verify_oidc_token", lambda token, audience: {"email": SA_EMAIL}
    )
    seen: dict = {}

    async def handler(payload: dict) -> None:
        seen["payload"] = payload

    interactions_module.set_followup_handler(handler)
    try:
        resp = client.post(
            "/tasks/followup",
            json={"command": "repo", "interaction_token": "tok"},
            headers={"Authorization": "Bearer good"},
        )
        assert resp.status_code == 200
        assert seen["payload"]["command"] == "repo"
    finally:
        interactions_module.set_followup_handler(None)  # type: ignore[arg-type]


def test_followup_wrong_caller_identity_returns_403(client: TestClient, monkeypatch) -> None:
    # Valid signature/audience but the token is from an unrelated account.
    monkeypatch.setattr(
        auth_module, "verify_oidc_token", lambda token, audience: {"email": "attacker@gmail.com"}
    )
    resp = client.post(
        "/tasks/followup",
        json={"command": "repo"},
        headers={"Authorization": "Bearer valid-but-wrong-account"},
    )
    assert resp.status_code == 403
