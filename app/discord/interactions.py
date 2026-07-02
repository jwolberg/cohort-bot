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

_dispatcher: CommandDispatcher | None = None
_followup_handler: FollowupHandler | None = None


def set_command_dispatcher(dispatcher: CommandDispatcher) -> None:
    """Register the coroutine that turns a command interaction into a response."""
    global _dispatcher
    _dispatcher = dispatcher


def set_followup_handler(handler: FollowupHandler) -> None:
    """Register the coroutine that runs deferred slow-command work (U7)."""
    global _followup_handler
    _followup_handler = handler


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
