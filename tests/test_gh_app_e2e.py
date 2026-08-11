"""End-to-end: GitHub App install → fan-out → attributed member digest (#10).

Ties the webhook intake to the digest pipeline with GitHub fully mocked, checking
the SPEC-GHAPP §12 acceptance criteria: a member self-installs, their private-repo
commits appear in the next digest attributed to their GitHub login via a
short-lived installation token (no shared PAT), and uninstalling stops all
scanning. No member token is ever persisted.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import get_settings
from app.digest.pipeline import DigestPipeline
from app.github.app_auth import GitHubAppAuth
from app.github.webhook import process_webhook_event
from app.store.repositories import get_repositories

pytestmark = pytest.mark.asyncio

BASE = "https://api.github.com"


def _pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


_PEM = _pem()


class FakeRest:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    async def post_channel_message(self, channel_id, *, embeds=None, content=None):
        self.posts.append({"channel": channel_id, "embeds": embeds})
        return {"id": "msg"}


class FakeSummarizer:
    async def summarize(self, *, repo_description, commit_messages, commit_count):
        return f"Summary of {commit_count} commits."


class FakeEnqueuer:
    def __init__(self) -> None:
        self.digest_installations: list[dict] = []

    async def enqueue_digest_user(self, payload):
        return "t"

    async def enqueue_substack_publication(self, payload):
        return "t"

    async def enqueue_digest_repo(self, payload):
        return "t"

    async def enqueue_digest_installation(self, payload):
        self.digest_installations.append(payload)
        return "t"


def _repo_sha(repo: str) -> str:
    # A real commit SHA is hex (no slash); doc_id encodes the repo but not the SHA.
    return "commit" + repo.split("/")[-1]


def _mock_repo(repo: str) -> None:
    respx.get(f"{BASE}/repos/{repo}/commits").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "sha": _repo_sha(repo),
                    "html_url": f"https://github.com/{repo}/commit/x",
                    "commit": {
                        "message": "feat: ship it",
                        "author": {"name": "Alice", "date": "2026-08-10T10:00:00Z"},
                        "committer": {"date": "2026-08-10T10:00:00Z"},
                    },
                }
            ],
        )
    )
    respx.get(f"{BASE}/repos/{repo}").mock(
        return_value=httpx.Response(
            200,
            json={
                "description": "d", "language": "Python",
                "stargazers_count": 0, "forks_count": 0, "default_branch": "main",
            },
        )
    )


@respx.mock
async def test_install_to_digest_end_to_end(firestore_client) -> None:
    repos = get_repositories(firestore_client)
    await repos.config.update({"digest_channel_id": "cohort-chan"})

    # 1) Member installs the App on two private repos → webhook intake.
    created = {
        "action": "created",
        "installation": {
            "id": 42,
            "account": {"login": "alice", "type": "User"},
            "repository_selection": "selected",
        },
        "repositories": [{"full_name": "alice/secret"}, {"full_name": "alice/thesis"}],
    }
    await process_webhook_event(repos, "installation", created)

    assert [m["github_login"] for m in await repos.members.list_enabled()] == ["alice"]
    assert (await repos.tracked_repos.get("alice/secret"))["source"] == "app"

    # 2) Daily fan-out enqueues exactly one task for the installation.
    enqueuer = FakeEnqueuer()
    rest = FakeRest()
    auth = GitHubAppAuth("123", _PEM)
    pipeline = DigestPipeline(
        repos, enqueuer, get_settings(), rest, FakeSummarizer(), gh_app_factory=lambda: auth
    )
    await pipeline.run_fanout()
    assert enqueuer.digest_installations == [{"installation_id": "42"}]

    # 3) The worker mints a scoped installation token and posts ONE attributed section.
    respx.post(f"{BASE}/app/installations/42/access_tokens").mock(
        return_value=httpx.Response(
            201, json={"token": "ghs_scoped", "expires_at": "2999-01-01T00:00:00Z"}
        )
    )
    _mock_repo("alice/secret")
    _mock_repo("alice/thesis")

    posted = await pipeline.process_installation("42")

    assert posted is True
    assert len(rest.posts) == 1
    embed = rest.posts[0]["embeds"][0]
    assert embed["title"] == "🧑‍💻 alice"  # attributed to the member's login
    assert rest.posts[0]["channel"] == "cohort-chan"
    assert await repos.processed_commits.has_sha("alice/secret", _repo_sha("alice/secret")) is True

    # No member/installation token is ever persisted — only ids + login.
    inst = await repos.installations.get(42)
    assert "token" not in inst and inst["account_login"] == "alice"

    # 4) Member uninstalls → nothing is scanned on the next run.
    await process_webhook_event(
        repos,
        "installation",
        {"action": "deleted", "installation": {"id": 42, "account": {"login": "alice"}}},
    )
    assert await repos.installations.list_enabled() == []
    assert await repos.members.list_enabled() == []
    assert await repos.tracked_repos.list_enabled_for_installation("42") == []
