"""Shared test fixtures.

Provides:
- test environment variables so ``app.config`` / app import succeeds,
- a session-scoped **Firestore emulator** and async client (U2+),
- a per-test reset of emulator data.

The emulator is started via ``gcloud emulators firestore start``. Install it
once with ``gcloud components install cloud-firestore-emulator``.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time

import httpx
import pytest

TEST_PROJECT = "cohort-bot-test"

# Required settings must exist before `app` is imported at collection time.
_ENV_DEFAULTS = {
    "DISCORD_PUBLIC_KEY": "0" * 64,
    "DISCORD_TOKEN": "test-discord-token",
    "DISCORD_APP_ID": "123456789012345678",
    "GITHUB_TOKEN": "test-github-token",
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "GCP_PROJECT": TEST_PROJECT,
    "SERVICE_URL": "https://digest-bot-test.a.run.app",
    "TASK_INVOKER_SA_EMAIL": "digest-bot-sa@cohort-bot-test.iam.gserviceaccount.com",
    "ADMIN_TOKEN": "test-admin-token",
}
for _key, _value in _ENV_DEFAULTS.items():
    os.environ.setdefault(_key, _value)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        return sock.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"Firestore emulator did not start on {host}:{port}")


@pytest.fixture(scope="session")
def firestore_emulator():
    """Start a Firestore emulator for the test session.

    Yields the ``host:port`` and sets ``FIRESTORE_EMULATOR_HOST`` so the
    google-cloud-firestore client connects to it.
    """
    if os.environ.get("FIRESTORE_EMULATOR_HOST"):
        # Caller manages an external emulator; use it as-is.
        yield os.environ["FIRESTORE_EMULATOR_HOST"]
        return

    port = _free_port()
    host_port = f"localhost:{port}"
    proc = subprocess.Popen(
        [
            "gcloud",
            "emulators",
            "firestore",
            "start",
            f"--host-port={host_port}",
            f"--project={TEST_PROJECT}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port("localhost", port)
        os.environ["FIRESTORE_EMULATOR_HOST"] = host_port
        yield host_port
    finally:
        os.environ.pop("FIRESTORE_EMULATOR_HOST", None)
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
async def firestore_client(firestore_emulator):
    """An async Firestore client bound to the emulator, cleared per test."""
    from app.store.firestore import get_client

    _reset_emulator(firestore_emulator)
    get_client.cache_clear()
    client = get_client()
    yield client
    get_client.cache_clear()


def _reset_emulator(host_port: str) -> None:
    """Delete all documents in the emulator between tests."""
    url = (
        f"http://{host_port}/emulator/v1/projects/{TEST_PROJECT}"
        f"/databases/(default)/documents"
    )
    try:
        httpx.delete(url, timeout=10.0)
    except httpx.HTTPError:
        pass
