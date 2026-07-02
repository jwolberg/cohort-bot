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

logger = get_logger(__name__)

router = APIRouter()

# Discord interaction request types.
PING = 1
APPLICATION_COMMAND = 2

CommandDispatcher = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

_dispatcher: CommandDispatcher | None = None


def set_command_dispatcher(dispatcher: CommandDispatcher) -> None:
    """Register the coroutine that turns a command interaction into a response."""
    global _dispatcher
    _dispatcher = dispatcher


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
