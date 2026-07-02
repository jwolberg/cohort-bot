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

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.config import Settings, get_settings
from app.store.repositories import Repositories, get_repositories
from app.tasks.queue import TaskEnqueuer

router = APIRouter(prefix="/admin")

_STATIC_DIR = Path(__file__).parent / "static"

# Config keys the panel may edit.
_EDITABLE_CONFIG = ("digest_channel_id", "digest_hour_utc", "admin_role_ids")


def require_admin(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """Authorize an admin request via IAP identity or the fallback bearer token."""
    # IAP sets this header on every request it forwards; its presence means the
    # caller passed Google sign-in + the IAP allow-list at the edge.
    if request.headers.get("X-Goog-Authenticated-User-Email"):
        return
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


@router.get("/", include_in_schema=False)
async def admin_index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")
