"""OIDC verification for task/scheduler-invoked endpoints.

Cloud Tasks and Cloud Scheduler call ``/tasks/*`` with an OIDC bearer token
minted for the service account. We verify the token's signature and audience
(ARCHITECTURE §9); unauthenticated callers are rejected. This is the only thing
standing between the public internet and the worker endpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from app.config import Settings, get_settings
from app.logging import get_logger

logger = get_logger(__name__)


def verify_oidc_token(token: str, audience: str) -> dict[str, Any]:
    """Verify a Google-signed OIDC token for the expected audience.

    Raises ``ValueError`` on any verification failure (bad signature, wrong
    audience, expired). Separated out so tests can stub it.
    """
    request = google_requests.Request()
    return id_token.verify_oauth2_token(token, request, audience=audience)


async def require_oidc(
    request: Request, settings: Settings = Depends(get_settings)
) -> dict[str, Any]:
    """FastAPI dependency: require a valid OIDC bearer token.

    401 if the token is missing/malformed, 403 if present but invalid.
    """
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = header[len("Bearer ") :].strip()
    try:
        return verify_oidc_token(token, settings.effective_oidc_audience)
    except Exception as exc:  # noqa: BLE001 - any failure is a rejected caller
        logger.warning("oidc_verification_failed")
        raise HTTPException(status_code=403, detail="invalid OIDC token") from exc
