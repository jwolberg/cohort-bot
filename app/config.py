"""Application settings.

Settings are read from environment variables. Locally these come from a
``.env`` file; in deployed environments Cloud Run injects them from Secret
Manager (see ``deploy/setup.sh``). Runtime-editable operational config (digest
channel, hour, admin roles) lives in the Firestore ``config`` singleton, not
here — this module holds only infrastructure/bootstrapping values.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Infrastructure configuration.

    Required fields have no default, so a missing value raises a clear
    ``ValidationError`` at startup rather than failing deep in a request.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Required secrets / identifiers ---
    discord_public_key: str = Field(..., alias="DISCORD_PUBLIC_KEY")
    discord_token: str = Field(..., alias="DISCORD_TOKEN")
    discord_app_id: str = Field(..., alias="DISCORD_APP_ID")
    github_token: str = Field(..., alias="GITHUB_TOKEN")
    anthropic_api_key: str = Field(..., alias="ANTHROPIC_API_KEY")
    gcp_project: str = Field(..., alias="GCP_PROJECT")

    # --- Optional infrastructure (sensible defaults) ---
    gcp_location: str = Field("us-central1", alias="GCP_LOCATION")
    firestore_database: str = Field("(default)", alias="FIRESTORE_DATABASE")

    digest_fanout_queue: str = Field("digest-fanout", alias="DIGEST_FANOUT_QUEUE")
    followups_queue: str = Field("interaction-followups", alias="FOLLOWUPS_QUEUE")

    # Public base URL of this Cloud Run service; task targets are built from it
    # and it is the default OIDC audience for inbound task/scheduler auth.
    service_url: str = Field("", alias="SERVICE_URL")
    # Service account used to mint OIDC tokens for enqueued tasks.
    task_invoker_sa_email: str = Field("", alias="TASK_INVOKER_SA_EMAIL")
    # Audience inbound OIDC tokens must carry; defaults to service_url.
    oidc_audience: str = Field("", alias="OIDC_AUDIENCE")

    # Summarizer model (Haiku default; Sonnet for richer digests).
    summarizer_model: str = Field("claude-haiku-4-5", alias="SUMMARIZER_MODEL")

    # IAP JWT audience for /admin/* (Cloud Run:
    # /projects/PROJECT_NUMBER/apps/PROJECT_ID). When set, the signed IAP
    # assertion is verified; the unsigned identity header is never trusted alone.
    iap_audience: str = Field("", alias="IAP_AUDIENCE")
    # Fallback admin auth when IAP is not in front of /admin/* (local dev).
    admin_token: str = Field("", alias="ADMIN_TOKEN")

    # Optional bootstrap value for the Firestore config singleton.
    default_digest_channel_id: str = Field("", alias="DEFAULT_DIGEST_CHANNEL_ID")

    log_level: str = Field("INFO", alias="LOG_LEVEL")

    @property
    def effective_oidc_audience(self) -> str:
        """Audience to require on inbound OIDC tokens."""
        return self.oidc_audience or self.service_url


@lru_cache
def get_settings() -> Settings:
    """Return process-wide settings, constructed once."""
    return Settings()
