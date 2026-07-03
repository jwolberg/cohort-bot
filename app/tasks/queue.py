"""Cloud Tasks enqueue helpers.

Enqueues HTTP tasks that call back into this Cloud Run service, authenticated
with an OIDC token minted for the service account (ARCHITECTURE §3, §9). Two
queues: ``interaction-followups`` (deferred slow slash-commands) and
``digest-fanout`` (per-user digest work).
"""

from __future__ import annotations

import json
from typing import Any

from google.cloud import tasks_v2

from app.config import Settings, get_settings
from app.logging import get_logger

logger = get_logger(__name__)

# Route paths the tasks target on this service.
FOLLOWUP_PATH = "/tasks/followup"
DIGEST_USER_PATH = "/tasks/digest/user"
DIGEST_RUN_PATH = "/tasks/digest/run"
SUBSTACK_PUBLICATION_PATH = "/tasks/substack/publication"


class EnqueueError(Exception):
    """Raised when a task could not be enqueued."""


class TaskEnqueuer:
    """Builds and enqueues OIDC-authenticated Cloud Tasks."""

    def __init__(self, settings: Settings | None = None, *, client: Any | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = tasks_v2.CloudTasksAsyncClient()
        return self._client

    def _target_url(self, path: str) -> str:
        return self._settings.service_url.rstrip("/") + path

    def _build_task(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        audience = self._settings.effective_oidc_audience
        return {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": self._target_url(path),
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps(payload).encode(),
                "oidc_token": {
                    "service_account_email": self._settings.task_invoker_sa_email,
                    "audience": audience,
                },
            }
        }

    async def enqueue(self, queue: str, path: str, payload: dict[str, Any]) -> str:
        """Create a task in ``queue`` targeting ``path``; returns the task name."""
        client = self._get_client()
        parent = client.queue_path(
            self._settings.gcp_project, self._settings.gcp_location, queue
        )
        task = self._build_task(path, payload)
        try:
            response = await client.create_task(request={"parent": parent, "task": task})
        except Exception as exc:  # noqa: BLE001 - surface a typed error to the caller
            logger.error("task_enqueue_failed", extra={"queue": queue, "path": path})
            raise EnqueueError(f"failed to enqueue task to {queue}") from exc
        logger.info("task_enqueued", extra={"queue": queue, "path": path})
        return response.name

    async def enqueue_followup(self, payload: dict[str, Any]) -> str:
        return await self.enqueue(self._settings.followups_queue, FOLLOWUP_PATH, payload)

    async def enqueue_digest_user(self, payload: dict[str, Any]) -> str:
        return await self.enqueue(
            self._settings.digest_fanout_queue, DIGEST_USER_PATH, payload
        )

    async def enqueue_digest_run(self, payload: dict[str, Any] | None = None) -> str:
        """Trigger a full digest run (used by the admin 'test digest' button)."""
        return await self.enqueue(
            self._settings.digest_fanout_queue, DIGEST_RUN_PATH, payload or {}
        )

    async def enqueue_substack_publication(self, payload: dict[str, Any]) -> str:
        """Enqueue one publication's Substack check (reuses the digest-fanout queue)."""
        return await self.enqueue(
            self._settings.digest_fanout_queue, SUBSTACK_PUBLICATION_PATH, payload
        )
