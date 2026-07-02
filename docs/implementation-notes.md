# Implementation Notes

Running log of decisions, deviations, and tradeoffs made while executing the
[GitHub Digest Discord Bot plan](plans/2026-07-02-001-feat-github-digest-discord-bot-plan.md).
Dated, tied to the implementation unit (U-ID) being worked.

## 2026-07-02

### Session setup
- Executing the full MVP (U1–U12) serially on branch
  `feat/github-digest-discord-bot`, committing per unit.
- **Environment constraint:** no live GCP project, Discord app, or API
  credentials here. Building all code + emulator/mock test suite (U1–U10, U12)
  and deploy scaffolding (U11); live deploy / Discord registration / real
  interaction-endpoint smoke test are left for the user to run with real creds.
  This matches the plan's deferred live-validation posture.
- Package manager: **uv** (present locally); Python 3.12.
- Installed the `cloud-firestore-emulator` gcloud component for U2/U9/U10
  integration tests.

### U1 — Project scaffolding & configuration
- **Config source:** `app/config.py` reads settings from **env vars only**
  (pydantic-settings). Decision: rather than call the Secret Manager API at
  runtime, secrets are injected as env vars by Cloud Run from Secret Manager at
  deploy time (wired in U11's `setup.sh`). Simpler, one less runtime dependency,
  and testable. Deviation from a literal reading of the plan's "loads from …
  Secret Manager" — same outcome, cleaner mechanism. Noted for U11.
- **Operational config** (digest channel/hour, admin role ids) lives in the
  Firestore `config` singleton (ARCHITECTURE §7), not env — it is
  runtime-editable via the admin panel (U10). `config.py` only holds infra
  values + an optional `DEFAULT_DIGEST_CHANNEL_ID` bootstrap.
- **Router wiring:** `create_app()` attaches the interactions/admin routers via
  guarded `try/except ImportError` so the app boots at any point during the
  build (before U3/U10 exist). Routers are added by their owning units.
- **Test env:** `tests/conftest.py` sets required env defaults at import time so
  `app` imports cleanly during collection, and provides a session-scoped
  Firestore emulator + async client fixture (used from U2 onward).
