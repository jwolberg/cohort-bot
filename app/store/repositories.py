"""Data access for the four Firestore collections (ARCHITECTURE §7).

Collections:
- ``tracked_users/{username}``     — who we follow, per-user cursor watermark
- ``processed_commits/{repo__sha}``— dedup key, ~90d TTL
- ``repo_cache/{owner__repo}``     — GitHub ETag + metadata cache
- ``config/singleton``             — digest channel/hour, admin role ids

Firestore document ids cannot contain ``/``, so ``owner/repo`` is encoded as
``owner__repo`` in ids (the logical key is preserved in the ``repo`` field).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from google.cloud.firestore import AsyncClient, SERVER_TIMESTAMP
from google.cloud.firestore_v1.base_query import FieldFilter

from app.store.firestore import get_client

# processed_commits entries expire ~90d after processing (TTL policy on
# `expire_at`, created by deploy/setup.sh).
PROCESSED_TTL_DAYS = 90

CONFIG_SINGLETON_ID = "singleton"

_DEFAULT_CONFIG: dict[str, Any] = {
    "digest_channel_id": "",
    "digest_hour_utc": 13,
    "admin_role_ids": [],
}


def _encode(repo: str) -> str:
    """Encode ``owner/repo`` for use in a Firestore document id."""
    return repo.replace("/", "__")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TrackedUsersRepo:
    """CRUD + cursor management for tracked GitHub users."""

    COLLECTION = "tracked_users"

    def __init__(self, client: AsyncClient) -> None:
        self._col = client.collection(self.COLLECTION)

    async def add(self, username: str, added_by: str) -> None:
        """Add a user, or re-enable an existing one. Idempotent."""
        doc = self._col.document(username)
        snapshot = await doc.get()
        if snapshot.exists:
            # Re-adding an existing user re-enables it; created_at/cursor stay.
            await doc.set({"enabled": True}, merge=True)
            return
        await doc.set(
            {
                "username": username,
                "enabled": True,
                "added_by": added_by,
                "created_at": SERVER_TIMESTAMP,
                "last_cursor": None,
            }
        )

    async def remove(self, username: str) -> None:
        """Remove a user entirely."""
        await self._col.document(username).delete()

    async def get(self, username: str) -> dict[str, Any] | None:
        snapshot = await self._col.document(username).get()
        return snapshot.to_dict() if snapshot.exists else None

    async def list_enabled(self) -> list[dict[str, Any]]:
        query = self._col.where(filter=FieldFilter("enabled", "==", True))
        return [doc.to_dict() async for doc in query.stream()]

    async def list_all(self) -> list[dict[str, Any]]:
        return [doc.to_dict() async for doc in self._col.stream()]

    async def set_cursor(self, username: str, cursor: datetime) -> None:
        await self._col.document(username).set(
            {"last_cursor": cursor}, merge=True
        )

    async def get_cursor(self, username: str) -> datetime | None:
        snapshot = await self._col.document(username).get()
        if not snapshot.exists:
            return None
        return snapshot.to_dict().get("last_cursor")


class ProcessedCommitsRepo:
    """Commit-SHA dedup keyed by ``owner/repo@sha``."""

    COLLECTION = "processed_commits"

    def __init__(self, client: AsyncClient) -> None:
        self._client = client
        self._col = client.collection(self.COLLECTION)

    @staticmethod
    def doc_id(repo: str, sha: str) -> str:
        return f"{_encode(repo)}@{sha}"

    async def has_sha(self, repo: str, sha: str) -> bool:
        snapshot = await self._col.document(self.doc_id(repo, sha)).get()
        return snapshot.exists

    async def record_shas(self, repo: str, shas: Iterable[str]) -> None:
        """Record processed SHAs for a repo in a single batch."""
        shas = list(shas)
        if not shas:
            return
        expire_at = _now() + timedelta(days=PROCESSED_TTL_DAYS)
        batch = self._client.batch()
        for sha in shas:
            batch.set(
                self._col.document(self.doc_id(repo, sha)),
                {
                    "repo": repo,
                    "sha": sha,
                    "processed_at": SERVER_TIMESTAMP,
                    "expire_at": expire_at,
                },
            )
        await batch.commit()


class RepoCacheRepo:
    """GitHub metadata + ETag cache keyed by ``owner/repo``."""

    COLLECTION = "repo_cache"

    def __init__(self, client: AsyncClient) -> None:
        self._col = client.collection(self.COLLECTION)

    async def get(self, repo: str) -> dict[str, Any] | None:
        snapshot = await self._col.document(_encode(repo)).get()
        return snapshot.to_dict() if snapshot.exists else None

    async def put(self, repo: str, data: dict[str, Any]) -> None:
        """Store metadata + ETag; stamps ``fetched_at`` server-side."""
        payload = {**data, "repo": repo, "fetched_at": SERVER_TIMESTAMP}
        await self._col.document(_encode(repo)).set(payload)


class ConfigRepo:
    """Singleton operational config."""

    COLLECTION = "config"

    def __init__(self, client: AsyncClient) -> None:
        self._doc = client.collection(self.COLLECTION).document(CONFIG_SINGLETON_ID)

    async def get(self) -> dict[str, Any]:
        snapshot = await self._doc.get()
        if not snapshot.exists:
            return dict(_DEFAULT_CONFIG)
        return {**_DEFAULT_CONFIG, **snapshot.to_dict()}

    async def update(self, fields: dict[str, Any]) -> None:
        await self._doc.set(fields, merge=True)


@dataclass(frozen=True)
class Repositories:
    """Bundle of all repositories over one client (shared by /track + admin)."""

    tracked_users: TrackedUsersRepo
    processed_commits: ProcessedCommitsRepo
    repo_cache: RepoCacheRepo
    config: ConfigRepo


def get_repositories(client: AsyncClient | None = None) -> Repositories:
    """Build the repository bundle over the shared (or given) client."""
    client = client or get_client()
    return Repositories(
        tracked_users=TrackedUsersRepo(client),
        processed_commits=ProcessedCommitsRepo(client),
        repo_cache=RepoCacheRepo(client),
        config=ConfigRepo(client),
    )
