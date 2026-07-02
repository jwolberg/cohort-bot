"""Async Firestore (Native mode) client initialization.

A single process-wide ``AsyncClient`` is reused across requests — Firestore
needs no connection pooling and this keeps cold starts cheap. When
``FIRESTORE_EMULATOR_HOST`` is set (tests, local dev) the client connects to the
emulator with anonymous credentials automatically.
"""

from __future__ import annotations

from functools import lru_cache

from google.cloud.firestore import AsyncClient

from app.config import get_settings


@lru_cache
def get_client() -> AsyncClient:
    """Return the shared async Firestore client."""
    settings = get_settings()
    kwargs: dict[str, str] = {"project": settings.gcp_project}
    if settings.firestore_database and settings.firestore_database != "(default)":
        kwargs["database"] = settings.firestore_database
    return AsyncClient(**kwargs)
