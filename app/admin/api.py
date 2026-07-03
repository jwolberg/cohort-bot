"""Admin JSON API + static panel.

The API and Discord ``/track`` are two front-ends over the **same** Firestore
repositories (parity — ARCHITECTURE §9). ``/admin/*`` is fronted by Identity-
Aware Proxy in production; this code trusts the IAP-verified identity header and
falls back to a shared admin bearer token for local dev. The static page is
served openly (IAP still gates it at the edge); only ``/admin/api/*`` enforces
auth in-app.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.config import Settings, get_settings
from app.store.repositories import Repositories, get_repositories
from app.substack.client import slug_for
from app.tasks.queue import TaskEnqueuer

router = APIRouter(prefix="/admin")

_STATIC_DIR = Path(__file__).parent / "static"

# IAP signs its assertion with these rotating keys (distinct from OAuth certs).
IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"

# Config keys the panel may edit.
_EDITABLE_CONFIG = ("digest_channel_id", "digest_hour_utc", "admin_role_ids")


def verify_iap_jwt(assertion: str, audience: str) -> dict[str, Any]:
    """Verify IAP's signed ``X-Goog-IAP-JWT-Assertion`` (separated for tests)."""
    request = google_requests.Request()
    return id_token.verify_token(
        assertion, request, audience=audience, certs_url=IAP_CERTS_URL
    )


def require_admin(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """Authorize an admin request via a *verified* IAP assertion or a bearer token.

    The unsigned ``X-Goog-Authenticated-User-Email`` header is never trusted on
    its own — only IAP's cryptographically signed assertion (when an
    ``IAP_AUDIENCE`` is configured) or the shared admin bearer token authorize.
    """
    assertion = request.headers.get("X-Goog-IAP-JWT-Assertion")
    if assertion and settings.iap_audience:
        try:
            verify_iap_jwt(assertion, settings.iap_audience)
            return
        except Exception as exc:  # noqa: BLE001 - any verification failure is a reject
            raise HTTPException(status_code=403, detail="invalid IAP assertion") from exc
    auth = request.headers.get("Authorization", "")
    if settings.admin_token and auth == f"Bearer {settings.admin_token}":
        return
    raise HTTPException(status_code=401, detail="admin authorization required")


def get_repos() -> Repositories:
    return get_repositories()


def get_enqueuer() -> TaskEnqueuer:
    return TaskEnqueuer(get_settings())


@router.get("/api/users", dependencies=[Depends(require_admin)])
async def list_users(repos: Repositories = Depends(get_repos)) -> dict[str, Any]:
    users = await repos.tracked_users.list_enabled()
    return {"users": [{"username": u["username"], "added_by": u.get("added_by", "")} for u in users]}


@router.post("/api/users", dependencies=[Depends(require_admin)])
async def add_user(
    payload: dict[str, Any] = Body(...), repos: Repositories = Depends(get_repos)
) -> dict[str, Any]:
    username = (payload.get("username") or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    await repos.tracked_users.add(username, added_by="admin-panel")
    return {"status": "ok", "username": username}


@router.delete("/api/users/{username}", dependencies=[Depends(require_admin)])
async def remove_user(
    username: str, repos: Repositories = Depends(get_repos)
) -> dict[str, Any]:
    await repos.tracked_users.remove(username)
    return {"status": "ok", "username": username}


@router.get("/api/config", dependencies=[Depends(require_admin)])
async def get_config(repos: Repositories = Depends(get_repos)) -> dict[str, Any]:
    return await repos.config.get()


@router.put("/api/config", dependencies=[Depends(require_admin)])
async def put_config(
    payload: dict[str, Any] = Body(...), repos: Repositories = Depends(get_repos)
) -> dict[str, Any]:
    updates = {k: payload[k] for k in _EDITABLE_CONFIG if k in payload}
    if updates:
        await repos.config.update(updates)
    return await repos.config.get()


@router.post("/api/digest/test", dependencies=[Depends(require_admin)])
async def test_digest(enqueuer: TaskEnqueuer = Depends(get_enqueuer)) -> dict[str, Any]:
    task_name = await enqueuer.enqueue_digest_run({"source": "admin-test"})
    return {"status": "enqueued", "task": task_name}


def _normalize_feed_url(raw: str) -> str:
    """Normalize a pasted Substack feed *or* publication URL to its RSS feed URL.

    Accepts ``pragmaticengineer.substack.com``, ``https://…substack.com`` or the
    ``/feed`` URL; returns ``https://<host>/feed``. Empty/hostless input → "".
    """
    url = raw.strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}/feed"


@router.get("/api/publications", dependencies=[Depends(require_admin)])
async def list_publications(repos: Repositories = Depends(get_repos)) -> dict[str, Any]:
    pubs = await repos.tracked_publications.list_enabled()
    return {
        "publications": [
            {"slug": p["slug"], "feed_url": p.get("feed_url", ""), "title": p.get("title", "")}
            for p in pubs
        ]
    }


@router.post("/api/publications", dependencies=[Depends(require_admin)])
async def add_publication(
    payload: dict[str, Any] = Body(...), repos: Repositories = Depends(get_repos)
) -> dict[str, Any]:
    feed_url = _normalize_feed_url(payload.get("feed_url") or "")
    if not feed_url:
        raise HTTPException(status_code=400, detail="feed_url required")
    slug = slug_for(feed_url)
    if not slug:
        raise HTTPException(status_code=400, detail="could not derive a slug from feed_url")
    title = (payload.get("title") or "").strip()
    await repos.tracked_publications.add(slug, feed_url, title=title, added_by="admin-panel")
    return {"status": "ok", "slug": slug, "feed_url": feed_url}


@router.delete("/api/publications/{slug}", dependencies=[Depends(require_admin)])
async def remove_publication(
    slug: str, repos: Repositories = Depends(get_repos)
) -> dict[str, Any]:
    await repos.tracked_publications.remove(slug)
    return {"status": "ok", "slug": slug}


@router.get("/", include_in_schema=False)
async def admin_index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")
