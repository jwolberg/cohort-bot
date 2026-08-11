"""Tests for the GitHub App webhook + setup router (#7).

Signature helpers and the pure event processor are tested directly; the endpoint
is driven through an in-loop ASGI transport (like the admin tests) so the async
Firestore channel stays on the test's event loop.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport

from app.config import get_settings
from app.github.webhook import (
    get_repos,
    process_webhook_event,
    verify_webhook_signature,
)
from app.main import create_app
from app.store.repositories import get_repositories

# asyncio_mode = "auto" (pyproject) marks async tests automatically; the sync
# signature tests below must stay unmarked, so no module-level asyncio mark.

SECRET = "whsec-test"


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --- signature verification (pure) ---


def test_verify_signature_accepts_valid() -> None:
    body = b'{"hello":"world"}'
    assert verify_webhook_signature(SECRET, body, _sign(SECRET, body)) is True


def test_verify_signature_rejects_tampered_body() -> None:
    good = _sign(SECRET, b"original")
    assert verify_webhook_signature(SECRET, b"tampered", good) is False


def test_verify_signature_rejects_missing_or_malformed() -> None:
    body = b"{}"
    assert verify_webhook_signature(SECRET, body, "") is False
    assert verify_webhook_signature(SECRET, body, "md5=deadbeef") is False
    # No configured secret must fail closed, not accept everything.
    assert verify_webhook_signature("", body, _sign("", body)) is False


# --- event processor (emulator) ---


def _installation_payload(action, inst_id=42, login="alice", repos=None, selection="selected"):
    return {
        "action": action,
        "installation": {
            "id": inst_id,
            "account": {"login": login, "type": "User"},
            "repository_selection": selection,
        },
        "repositories": [{"full_name": r} for r in (repos or [])],
    }


async def test_installation_created_registers_member_installation_and_repos(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    payload = _installation_payload("created", repos=["alice/one", "alice/two"])

    result = await process_webhook_event(repos, "installation", payload)

    assert result["repos_added"] == 2
    assert [m["github_login"] for m in await repos.members.list_enabled()] == ["alice"]
    assert [i["installation_id"] for i in await repos.installations.list_enabled()] == ["42"]
    tracked = {r["repo"] for r in await repos.tracked_repos.list_enabled_for_installation(42)}
    assert tracked == {"alice/one", "alice/two"}
    assert (await repos.tracked_repos.get("alice/one"))["source"] == "app"


async def test_installation_repositories_added_and_removed(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await process_webhook_event(repos, "installation", _installation_payload("created", repos=["alice/one"]))

    payload = {
        "action": "added",
        "installation": {"id": 42, "account": {"login": "alice", "type": "User"}},
        "repositories_added": [{"full_name": "alice/two"}],
        "repositories_removed": [{"full_name": "alice/one"}],
    }
    await process_webhook_event(repos, "installation_repositories", payload)

    enabled = {r["repo"] for r in await repos.tracked_repos.list_enabled_for_installation(42)}
    assert enabled == {"alice/two"}  # one added, one removed (soft-disabled)
    assert (await repos.tracked_repos.get("alice/one"))["enabled"] is False


async def test_installation_deleted_disables_everything(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await process_webhook_event(
        repos, "installation", _installation_payload("created", repos=["alice/one", "alice/two"])
    )

    await process_webhook_event(repos, "installation", _installation_payload("deleted"))

    assert await repos.installations.list_enabled() == []
    assert await repos.members.list_enabled() == []
    assert await repos.tracked_repos.list_enabled_for_installation(42) == []


async def test_installation_suspend_disables_installation_but_keeps_repos(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await process_webhook_event(
        repos, "installation", _installation_payload("created", repos=["alice/one"])
    )

    await process_webhook_event(repos, "installation", _installation_payload("suspend"))

    # Installation is skipped by fan-out, but repos stay enabled so unsuspend resumes.
    assert await repos.installations.list_enabled() == []
    assert {r["repo"] for r in await repos.tracked_repos.list_enabled_for_installation(42)} == {"alice/one"}


async def test_unknown_event_is_ignored(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    result = await process_webhook_event(repos, "push", {"action": "whatever"})
    assert "ignored" in result


# --- endpoint (ASGI, in-loop) ---


@pytest.fixture
def wired(firestore_client):
    repos = get_repositories(firestore_client)
    app = create_app()
    app.dependency_overrides[get_repos] = lambda: repos
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        github_app_webhook_secret=SECRET
    )
    return app, repos


async def _post(app, path, *, body: bytes, headers):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, content=body, headers=headers)


async def test_endpoint_rejects_bad_signature(wired) -> None:
    app, repos = wired
    body = json.dumps(_installation_payload("created", repos=["x/y"])).encode()
    resp = await _post(
        app,
        "/github/webhook",
        body=body,
        headers={"X-GitHub-Event": "installation", "X-Hub-Signature-256": "sha256=bad"},
    )
    assert resp.status_code == 401
    # Body was never processed.
    assert await repos.members.list_enabled() == []


async def test_endpoint_accepts_valid_signature(wired) -> None:
    app, repos = wired
    body = json.dumps(_installation_payload("created", repos=["x/y"])).encode()
    resp = await _post(
        app,
        "/github/webhook",
        body=body,
        headers={"X-GitHub-Event": "installation", "X-Hub-Signature-256": _sign(SECRET, body)},
    )
    assert resp.status_code == 200
    assert resp.json()["repos_added"] == 1
    assert [m["github_login"] for m in await repos.members.list_enabled()] == ["alice"]


async def test_setup_page_renders() -> None:
    app = create_app()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/github/setup")
    assert resp.status_code == 200
    assert "Installed" in resp.text
