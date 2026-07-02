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


def test_app_wires_healthz_route() -> None:
    app = create_app()
    paths = {route.path for route in app.routes}
    assert "/healthz" in paths
