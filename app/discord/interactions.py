"""Discord ``/interactions`` endpoint: verify, then dispatch.

Signature verification runs on the *raw* request body before any JSON parsing;
unverified requests get 401. Verified requests are answered:
- PING (type 1)                → PONG
- APPLICATION_COMMAND (type 2) → command dispatcher (registered by U7)

The command dispatcher is injected via ``set_command_dispatcher`` so this module
stays decoupled from handler wiring (and is testable with a stub).
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.discord import responses
from app.discord.verify import verify_signature
from app.logging import get_logger
from app.tasks.auth import require_oidc

logger = get_logger(__name__)

router = APIRouter()

# Discord interaction request types.
PING = 1
APPLICATION_COMMAND = 2

CommandDispatcher = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
FollowupHandler = Callable[[dict[str, Any]], Awaitable[None]]
DigestRunner = Callable[[], Awaitable[None]]
DigestUserWorker = Callable[[str], Awaitable[None]]
PublicationWorker = Callable[[str], Awaitable[None]]

_dispatcher: CommandDispatcher | None = None
_followup_handler: FollowupHandler | None = None
_digest_runner: DigestRunner | None = None
_digest_user_worker: DigestUserWorker | None = None
_publication_worker: PublicationWorker | None = None


def set_command_dispatcher(dispatcher: CommandDispatcher) -> None:
    """Register the coroutine that turns a command interaction into a response."""
    global _dispatcher
    _dispatcher = dispatcher


def set_followup_handler(handler: FollowupHandler) -> None:
    """Register the coroutine that runs deferred slow-command work (U7)."""
    global _followup_handler
    _followup_handler = handler


def set_digest_handlers(runner: DigestRunner, user_worker: DigestUserWorker) -> None:
    """Register the digest fan-out runner and per-user worker (U9)."""
    global _digest_runner, _digest_user_worker
    _digest_runner = runner
    _digest_user_worker = user_worker


def set_publication_worker(worker: PublicationWorker) -> None:
    """Register the per-publication Substack worker (S4)."""
    global _publication_worker
    _publication_worker = worker


def get_public_key() -> str:
    """Discord app public key (overridable in tests)."""
    return get_settings().discord_public_key


@router.post("/interactions")
async def interactions(
    request: Request,
    public_key: str = Depends(get_public_key),
) -> JSONResponse:
    body = await request.body()
    signature = request.headers.get("X-Signature-Ed25519", "")
    timestamp = request.headers.get("X-Signature-Timestamp", "")

    if not verify_signature(public_key, signature, timestamp, body):
        # Body is never parsed for unverified requests.
        raise HTTPException(status_code=401, detail="invalid request signature")

    interaction = json.loads(body)
    itype = interaction.get("type")

    if itype == PING:
        return JSONResponse(responses.pong())

    if itype == APPLICATION_COMMAND:
        if _dispatcher is None:
            logger.warning("no_command_dispatcher_registered")
            return JSONResponse(
                responses.message(
                    "Command handling is not available yet.", ephemeral=True
                )
            )
        return JSONResponse(await _dispatcher(interaction))

    raise HTTPException(status_code=400, detail=f"unsupported interaction type {itype}")


@router.post("/tasks/followup", dependencies=[Depends(require_oidc)])
async def tasks_followup(request: Request) -> JSONResponse:
    """Run deferred slow-command work enqueued by the dispatcher (U7/U8).

    Protected by OIDC — only Cloud Tasks (as the service account) may call it.
    """
    payload = await request.json()
    if _followup_handler is None:
        logger.warning("no_followup_handler_registered")
        raise HTTPException(status_code=503, detail="follow-up handler not ready")
    await _followup_handler(payload)
    return JSONResponse({"status": "ok"})


@router.post("/tasks/digest/run", dependencies=[Depends(require_oidc)])
async def tasks_digest_run() -> JSONResponse:
    """Cloud Scheduler entry point: fan out the daily digest (OIDC-gated)."""
    if _digest_runner is None:
        raise HTTPException(status_code=503, detail="digest runner not ready")
    await _digest_runner()
    return JSONResponse({"status": "ok"})


@router.post("/tasks/digest/user", dependencies=[Depends(require_oidc)])
async def tasks_digest_user(request: Request) -> JSONResponse:
    """Per-user digest worker enqueued by the fan-out (OIDC-gated)."""
    if _digest_user_worker is None:
        raise HTTPException(status_code=503, detail="digest worker not ready")
    payload = await request.json()
    username = payload.get("username")
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    await _digest_user_worker(username)
    return JSONResponse({"status": "ok"})


@router.post("/tasks/substack/publication", dependencies=[Depends(require_oidc)])
async def tasks_substack_publication(request: Request) -> JSONResponse:
    """Per-publication Substack worker enqueued by the fan-out (OIDC-gated)."""
    if _publication_worker is None:
        raise HTTPException(status_code=503, detail="publication worker not ready")
    payload = await request.json()
    slug = payload.get("slug")
    if not slug:
        raise HTTPException(status_code=400, detail="slug required")
    await _publication_worker(slug)
    return JSONResponse({"status": "ok"})
