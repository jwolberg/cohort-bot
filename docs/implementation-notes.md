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

### U11 — Deployment scaffolding
- **Verification done locally:** `setup.sh` passes `bash -n`; `cloudbuild.yaml`
  is valid YAML; `uv build` produces a wheel (validates the Dockerfile's
  `pip install .`). Full `docker build`, `setup.sh` execution, and the Cloud Run
  deploy + Discord PING smoke test **could not run here** (no Docker daemon, no
  GCP project) — left for the user with real credentials, per the session's
  stated constraint.
- **Firestore Native** is created explicitly (irreversible); `setup.sh` guards
  every resource for idempotent re-runs.
- **Two-pass deploy:** `setup.sh` (infra) → Cloud Build (deploy, learn URL) →
  `setup.sh` again with `SERVICE_URL` to create the Scheduler job (needs the
  live URL as OIDC audience). README documents the full sequence.

### U10 — Admin panel
- **Auth:** `require_admin` authorizes if the IAP identity header
  (`X-Goog-Authenticated-User-Email`) is present (IAP sets it only on forwarded
  requests) OR a valid `ADMIN_TOKEN` bearer is supplied (local-dev fallback).
  Follow-up/hardening: verify the `X-Goog-IAP-JWT-Assertion` signature rather
  than trusting the email header, in case the service is ever reachable without
  IAP in front.
- **Parity:** admin API and Discord `/track` share the same `Repositories`.
- **Test gotcha:** Starlette's sync `TestClient` uses a fresh event loop per
  request, which breaks the loop-bound async Firestore gRPC channel across
  multi-request tests. Admin API tests drive the app via in-loop
  `httpx.ASGITransport` so all requests share the pytest-asyncio loop.
  Production (single uvicorn loop) is unaffected.

### U9 — Daily digest pipeline
- **Resolved §14 open decision (message shape):** scheduled digest uses
  **per-user fan-out messages** — `/tasks/digest/run` posts one batched header
  ("📊 GitHub Daily Digest — <date>") then enqueues one `/tasks/digest/user`
  task per enabled user; each user task posts its own section message. Rationale:
  each user's work becomes an independently retryable, idempotent Cloud Task with
  **no fan-in/join coordination** — directly serving the ">99% success" and
  "recover gracefully" goals with far less complexity than a batched single
  message (which would need a Firestore join + last-writer-assembles race). The
  **on-demand `/digest` command** does use batched, paginated embeds (matches the
  PRD example header + per-user layout in one interactive response).
- **Idempotency:** per-user task records `processed_commits` SHAs and advances
  `last_cursor` **only after** a successful post. Post failure → Cloud Tasks
  retries; cursor unchanged and SHAs unrecorded, so the retry recomputes and
  reposts. Tradeoff: post-succeeds-but-record-fails yields an at-least-once
  message dup (rare); commit-level dedup still holds via `processed_commits`.
- **Cursor advance:** to the newest commit timestamp seen this run (exclusive
  cursor), so the next run skips them even before SHA dedup.
- **On-demand does not dedup or advance cursors** — it's a read-only window view
  (`compute_section(dedup=False)`), so `/digest today` shows the day's activity
  regardless of what the scheduled digest already reported.

### U5 — GitHub REST client
- **Resolved deferred question (events vs commits):** per-user commit fetch uses
  the **public Events API** (`/users/{u}/events/public`) — one request per user,
  newest-first, early-stop once past the cursor. PushEvent payloads give
  sha/message/author. Cheaper than enumerating repos; matches ARCHITECTURE §8.
  Limitation: Events API caps at ~300 recent events, fine for a daily digest.
- **Cursor boundary is exclusive** (strictly after `last_cursor`); combined with
  `processed_commits` SHA dedup this makes re-runs safe.
- **ETag caching** only on repo metadata (`fetch_repo`); recent commits and
  contributors are small and fetched fresh. `304` returns the cached RepoInfo
  and does not re-write the cache.
- **Branch ahead/behind** is best-effort (compare endpoint), enriched only for
  the first N branches to bound calls — "when available" per PRD.

### U2 — Firestore Native data layer
- **Doc-id encoding:** Firestore ids can't contain `/`, so `owner/repo` is
  encoded as `owner__repo` in `processed_commits` (`owner__repo@sha`) and
  `repo_cache` ids — matches the `{repo__sha}`/`{owner__repo}` path hints in
  ARCHITECTURE §7. The logical key is preserved in the `repo` field.
- **`remove` = hard delete** (not soft-disable). The `enabled` flag is retained
  for future soft-disable and drives `list_enabled`, but `/track remove` removes
  the doc. Satisfies "excluded from list-enabled".
- **TTL:** `processed_commits` docs store an `expire_at = processed_at + 90d`
  field; the actual TTL policy on `expire_at` is created in U11 `setup.sh`.
- **Emulator gotcha (important):** the local gcloud SDK (428) launcher does
  `import imp`, removed in Python 3.12 — so the emulator crashes when spawned
  under the 3.12 test venv. Fixed in `conftest.py` by discovering a pre-3.12
  interpreter and passing it as `CLOUDSDK_PYTHON` to the emulator subprocess.
  The fixture now captures the emulator log to `tests/.firestore-emulator.log`
  and prints it on startup failure. Emulator boots in ~4s once warm.

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
