"""U10 tests: admin JSON API (emulator) + static panel smoke.

The API tests drive the app through an in-loop ASGI transport (not the sync
TestClient) so every request shares the test's event loop — the async Firestore
gRPC channel is loop-bound, which the sync client's per-request loops would
break. Production runs a single uvicorn loop, so this only affects tests.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport

from app.admin import api as api_module
from app.admin.api import get_enqueuer, get_repos
from app.config import get_settings
from app.main import create_app
from app.store.repositories import get_repositories

AUTH = {"Authorization": "Bearer test-admin-token"}

pytestmark = pytest.mark.asyncio


class FakeEnqueuer:
    def __init__(self):
        self.runs: list[dict] = []

    async def enqueue_digest_run(self, payload):
        self.runs.append(payload)
        return "queues/digest-fanout/tasks/1"


@pytest.fixture
def wired(firestore_client):
    repos = get_repositories(firestore_client)
    enqueuer = FakeEnqueuer()
    app = create_app()
    app.dependency_overrides[get_repos] = lambda: repos
    app.dependency_overrides[get_enqueuer] = lambda: enqueuer
    return app, repos, enqueuer


def _ac(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_unauthenticated_request_is_rejected(wired) -> None:
    app, _, _ = wired
    async with _ac(app) as ac:
        assert (await ac.get("/admin/api/users")).status_code == 401


async def test_unsigned_iap_email_header_no_longer_authorizes(wired) -> None:
    # The spoofable identity header alone must NOT grant admin — only a verified
    # IAP assertion or the bearer token does.
    app, _, _ = wired
    async with _ac(app) as ac:
        resp = await ac.get(
            "/admin/api/users",
            headers={"X-Goog-Authenticated-User-Email": "accounts.google.com:me@x.com"},
        )
    assert resp.status_code == 401


async def test_verified_iap_assertion_authorizes(wired, monkeypatch) -> None:
    app, _, _ = wired
    monkeypatch.setattr(api_module, "verify_iap_jwt", lambda assertion, aud: {"email": "me@x.com"})
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        iap_audience="/projects/123/apps/proj", admin_token=""
    )
    async with _ac(app) as ac:
        resp = await ac.get("/admin/api/users", headers={"X-Goog-IAP-JWT-Assertion": "signed-jwt"})
    assert resp.status_code == 200


async def test_invalid_iap_assertion_rejected(wired, monkeypatch) -> None:
    app, _, _ = wired
    def _boom(assertion, aud):
        raise ValueError("bad signature")

    monkeypatch.setattr(api_module, "verify_iap_jwt", _boom)
    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        iap_audience="/projects/123/apps/proj", admin_token=""
    )
    async with _ac(app) as ac:
        resp = await ac.get("/admin/api/users", headers={"X-Goog-IAP-JWT-Assertion": "forged"})
    assert resp.status_code == 403


async def test_users_crud_shares_store_with_track(wired) -> None:
    app, _, _ = wired
    async with _ac(app) as ac:
        assert (await ac.post("/admin/api/users", json={"username": "octocat"}, headers=AUTH)).status_code == 200
        listed = (await ac.get("/admin/api/users", headers=AUTH)).json()
        assert listed["users"][0]["username"] == "octocat"
        # A user added without a group defaults to the cohort list.
        assert listed["users"][0]["group"] == "cohort"
        assert (await ac.delete("/admin/api/users/octocat", headers=AUTH)).status_code == 200
        assert (await ac.get("/admin/api/users", headers=AUTH)).json()["users"] == []


async def test_users_are_partitioned_by_group(wired) -> None:
    app, _, _ = wired
    async with _ac(app) as ac:
        await ac.post("/admin/api/users", json={"username": "octocat", "group": "cohort"}, headers=AUTH)
        await ac.post("/admin/api/users", json={"username": "karpathy", "group": "leaders"}, headers=AUTH)
        users = (await ac.get("/admin/api/users", headers=AUTH)).json()["users"]
    by_group = {u["username"]: u["group"] for u in users}
    assert by_group == {"octocat": "cohort", "karpathy": "leaders"}


async def test_add_user_rejects_unknown_group(wired) -> None:
    app, _, _ = wired
    async with _ac(app) as ac:
        resp = await ac.post(
            "/admin/api/users", json={"username": "octocat", "group": "bogus"}, headers=AUTH
        )
    assert resp.status_code == 400


async def test_re_add_moves_user_between_groups(wired) -> None:
    app, _, _ = wired
    async with _ac(app) as ac:
        await ac.post("/admin/api/users", json={"username": "octocat", "group": "cohort"}, headers=AUTH)
        await ac.post("/admin/api/users", json={"username": "octocat", "group": "leaders"}, headers=AUTH)
        users = (await ac.get("/admin/api/users", headers=AUTH)).json()["users"]
    assert len(users) == 1
    assert users[0]["group"] == "leaders"


async def test_duplicate_add_is_idempotent(wired) -> None:
    app, _, _ = wired
    async with _ac(app) as ac:
        await ac.post("/admin/api/users", json={"username": "octocat"}, headers=AUTH)
        await ac.post("/admin/api/users", json={"username": "octocat"}, headers=AUTH)
        users = (await ac.get("/admin/api/users", headers=AUTH)).json()["users"]
    assert len(users) == 1


async def test_config_get_and_put(wired) -> None:
    app, _, _ = wired
    async with _ac(app) as ac:
        resp = await ac.put(
            "/admin/api/config",
            json={
                "digest_channel_id": "999",
                "leaders_channel_id": "888",
                "digest_hour_utc": 9,
                "admin_role_ids": ["r1"],
            },
            headers=AUTH,
        )
        assert resp.status_code == 200
        got = (await ac.get("/admin/api/config", headers=AUTH)).json()
    assert got["digest_channel_id"] == "999"
    assert got["leaders_channel_id"] == "888"
    assert got["digest_hour_utc"] == 9
    assert got["admin_role_ids"] == ["r1"]


async def test_publications_crud_and_normalization(wired) -> None:
    app, repos, _ = wired
    async with _ac(app) as ac:
        # A bare host is normalized to the /feed URL; slug is the host.
        resp = await ac.post(
            "/admin/api/publications", json={"feed_url": "pragmaticengineer.substack.com"}, headers=AUTH
        )
        assert resp.status_code == 200
        assert resp.json()["slug"] == "pragmaticengineer.substack.com"
        assert resp.json()["feed_url"] == "https://pragmaticengineer.substack.com/feed"

        listed = (await ac.get("/admin/api/publications", headers=AUTH)).json()["publications"]
        assert listed[0]["slug"] == "pragmaticengineer.substack.com"

        assert (
            await ac.delete("/admin/api/publications/pragmaticengineer.substack.com", headers=AUTH)
        ).status_code == 200
        assert (await ac.get("/admin/api/publications", headers=AUTH)).json()["publications"] == []


async def test_add_publication_requires_feed_url(wired) -> None:
    app, _, _ = wired
    async with _ac(app) as ac:
        resp = await ac.post("/admin/api/publications", json={"feed_url": ""}, headers=AUTH)
    assert resp.status_code == 400


async def test_publications_endpoint_requires_admin(wired) -> None:
    app, _, _ = wired
    async with _ac(app) as ac:
        assert (await ac.get("/admin/api/publications")).status_code == 401


async def test_digest_test_enqueues_run(wired) -> None:
    app, _, enqueuer = wired
    async with _ac(app) as ac:
        resp = await ac.post("/admin/api/digest/test", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["status"] == "enqueued"
    assert len(enqueuer.runs) == 1


async def test_static_panel_serves_required_elements() -> None:
    async with _ac(create_app()) as ac:
        resp = await ac.get("/admin/")
    assert resp.status_code == 200
    body = resp.text
    assert "x-data" in body
    assert "/admin/api" in body
    assert "Substack publications" in body  # publications section present
