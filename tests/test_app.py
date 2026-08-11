"""U1 smoke tests: health check, config validation, app wiring."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def test_healthz_returns_ok(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_settings_load_from_env() -> None:
    settings = Settings(_env_file=None)
    assert settings.gcp_project == "cohort-bot-test"
    assert settings.summarizer_model == "claude-haiku-4-5"
    # Optional OIDC audience falls back to the service URL.
    assert settings.effective_oidc_audience == settings.service_url


def test_missing_required_setting_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)
    # The error names the missing field so startup failures are actionable.
    assert "anthropic_api_key" in str(exc_info.value).lower()


def test_github_app_settings_default_empty() -> None:
    """The GitHub App fields are optional; unset, they default to empty so the
    member private-repo path stays inert (mirrors the github_token_private
    fallback). The app must still boot with none configured."""
    settings = Settings(_env_file=None)
    assert settings.github_app_id == ""
    assert settings.github_app_private_key == ""
    assert settings.github_app_webhook_secret == ""
    assert settings.github_app_slug == ""


def test_github_app_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """When GITHUB_APP_* are set, they populate the settings (member path enabled)."""
    monkeypatch.setenv("GITHUB_APP_ID", "123456")
    monkeypatch.setenv(
        "GITHUB_APP_PRIVATE_KEY",
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
    )
    monkeypatch.setenv("GITHUB_APP_WEBHOOK_SECRET", "whsec-xyz")
    monkeypatch.setenv("GITHUB_APP_SLUG", "cohort-digest")

    settings = Settings(_env_file=None)

    assert settings.github_app_id == "123456"
    assert settings.github_app_private_key.startswith("-----BEGIN RSA PRIVATE KEY-----")
    assert settings.github_app_webhook_secret == "whsec-xyz"
    assert settings.github_app_slug == "cohort-digest"


def test_app_wires_healthz_route() -> None:
    app = create_app()
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/healthz" in paths
