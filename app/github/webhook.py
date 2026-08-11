"""GitHub App webhook receiver + install landing (SPEC-GHAPP §8.2).

A member installs the App on the repos they choose; GitHub posts installation
events here. We verify ``X-Hub-Signature-256`` (HMAC-SHA256 over the *raw* body)
before trusting any payload — mirroring the raw-body-first rule the Discord
interactions endpoint uses — then sync members / installations / tracked repos.

Event handling is a pure ``process_webhook_event`` function so it can be unit
tested against the emulator without going through HTTP/signature machinery.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import Settings, get_settings
from app.logging import get_logger
from app.store.repositories import Repositories, get_repositories

logger = get_logger(__name__)

router = APIRouter(prefix="/github")


def verify_webhook_signature(secret: str, body: bytes, signature_header: str) -> bool:
    """Constant-time check of GitHub's ``X-Hub-Signature-256`` (HMAC-SHA256).

    Returns False (reject) when the secret or header is absent/malformed, so an
    unconfigured webhook secret fails closed rather than accepting anything.
    """
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _repo_names(items: list[dict[str, Any]] | None) -> list[str]:
    return [r["full_name"] for r in (items or []) if r.get("full_name")]


# Actions that (re)activate an installation and (re)add its repos.
_ACTIVATE_ACTIONS = frozenset({"created", "unsuspend", "new_permissions_accepted"})


async def process_webhook_event(
    repos: Repositories, event: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Apply one *verified* webhook event to the store; return a small summary.

    The digest fan-out iterates enabled installations, so ``suspend`` only
    disables the installation (repos untouched, ready to resume on ``unsuspend``);
    ``deleted`` (uninstall) is the permanent path that also soft-disables the
    member's repos and the member record.
    """
    action = payload.get("action", "")
    installation = payload.get("installation") or {}
    inst_id = installation.get("id")
    account = installation.get("account") or {}
    login = account.get("login", "")

    if event == "installation":
        if inst_id is None or not login:
            return {"ignored": "missing installation/account"}
        if action in _ACTIVATE_ACTIONS:
            await repos.installations.upsert(
                inst_id,
                account_login=login,
                account_type=account.get("type", ""),
                repository_selection=installation.get("repository_selection", ""),
            )
            await repos.members.upsert(login, installation_id=inst_id)
            added = _repo_names(payload.get("repositories"))
            for name in added:
                await repos.tracked_repos.add(
                    name,
                    added_by=f"app:{login}",
                    source="app",
                    installation_id=inst_id,
                    member_login=login,
                )
            return {"action": action, "installation_id": str(inst_id), "repos_added": len(added)}
        if action == "suspend":
            await repos.installations.disable(inst_id)
            return {"action": action, "installation_id": str(inst_id)}
        if action == "deleted":
            await repos.tracked_repos.disable_for_installation(inst_id)
            await repos.installations.disable(inst_id)
            await repos.members.disable(login)
            return {"action": action, "installation_id": str(inst_id)}
        return {"ignored": f"installation:{action}"}

    if event == "installation_repositories":
        if inst_id is None or not login:
            return {"ignored": "missing installation/account"}
        added = _repo_names(payload.get("repositories_added"))
        removed = _repo_names(payload.get("repositories_removed"))
        for name in added:
            await repos.tracked_repos.add(
                name,
                added_by=f"app:{login}",
                source="app",
                installation_id=inst_id,
                member_login=login,
            )
        for name in removed:
            await repos.tracked_repos.disable(name)
        await repos.members.upsert(login, installation_id=inst_id)
        return {"action": action, "repos_added": len(added), "repos_removed": len(removed)}

    return {"ignored": f"event:{event}"}


def get_repos() -> Repositories:
    return get_repositories()


@router.post("/webhook")
async def github_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
    repos: Repositories = Depends(get_repos),
) -> JSONResponse:
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not verify_webhook_signature(settings.github_app_webhook_secret, body, signature):
        # Never parse the body of an unverified request.
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    event = request.headers.get("X-GitHub-Event", "")
    payload = json.loads(body)
    result = await process_webhook_event(repos, event, payload)
    logger.info(
        "github_webhook",
        extra={"event": event, **{k: v for k, v in result.items() if isinstance(v, (str, int))}},
    )
    return JSONResponse({"status": "ok", **result})


_SETUP_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Installed</title></head>
<body style="font-family: system-ui, sans-serif; max-width: 40rem; margin: 4rem auto; padding: 0 1rem">
<h1>✅ Installed</h1>
<p>The cohort digest bot now has read-only access to the repositories you selected.
Your new commits will appear in the next daily digest.</p>
<p>You can change or revoke access anytime in GitHub → Settings → Applications.</p>
</body></html>"""


@router.get("/setup")
async def github_setup() -> HTMLResponse:
    """Landing page GitHub redirects to after install.

    The webhook is the source of truth for repos; no identity binding happens
    here (decision SPEC-GHAPP §3.2).
    """
    return HTMLResponse(_SETUP_HTML)
