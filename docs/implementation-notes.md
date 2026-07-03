# Implementation Notes

Running log of decisions, deviations, and tradeoffs made while executing the
[GitHub Digest Discord Bot plan](plans/2026-07-02-001-feat-github-digest-discord-bot-plan.md).
Dated, tied to the implementation unit (U-ID) being worked.

## 2026-07-03 — Digest signal filtering (chores/docs/fixes excluded)

- **Change:** the daily digest was too long, so `compute_section`
  (`app/digest/pipeline.py`) now drops **low-signal commits** — chores,
  documentation updates, and bug fixes — before grouping/summarizing. Applies to
  both the scheduled per-user digest and the on-demand `/digest` view (shared
  code path).
- **Classifier (`_is_low_signal`):** primary detection is the
  conventional-commit type prefix (`chore`/`docs`/`fix` + aliases
  `doc`/`bugfix`/`hotfix`); a small start-of-subject keyword fallback catches
  non-conventional messages (`Fix …`, `Update README`, `Bump …`, `typo`). Only
  the first subject line is inspected, and keywords match only at the start so
  feature commits that merely mention these words are kept
  (e.g. "Improve docs discoverability", "fixture: seed data").
- **Cursor semantics (deliberate):** the cursor still advances past the **whole
  fetched batch** (including filtered commits) so noise isn't re-scanned each
  run. Filtered SHAs are **not** recorded in `processed_commits` (they're never
  reported), which is harmless because the cursor already guards re-fetching.
- **Tradeoffs / assumptions:** (a) scope limited to the three requested
  categories — `ci`/`build`/`style`/`test` are *not* filtered; extend
  `_LOW_SIGNAL_TYPES`/`_LOW_SIGNAL_PREFIXES` to change that. (b) A subject that
  *leads* with a fix keyword but also adds a feature (e.g. "Fixes #12 add dark
  mode") is dropped — accepted, message-led intent wins. (c) If *all* of a
  user's commits are low-signal, the section is omitted and the cursor is not
  advanced (matches existing "no new activity" behavior).
- **Pre-existing failure (not mine):** `test_on_demand_builds_batched_embeds`
  fails on `main`/HEAD too — its event is hardcoded to `2026-07-02` while
  `today` is `2026-07-03`, so the commit falls outside the "today" window. Left
  as-is (out of scope); flagging for a follow-up to make the test date-relative.

## 2026-07-02 — First production deploy (pass 1)

### Deploy: infra provisioning against `cohort-bot-1`
- **Target project:** created a **new dedicated project `cohort-bot-1`** (user's
  choice) rather than reusing the shell's then-current `global-pulse-8`, which
  already hosts another app + a live Firestore Native DB. Billing linked to the
  existing account `0142FA-BE81E1-A43239`.
- **Scope:** ran `deploy/setup.sh` **pass 1 only** (no `SERVICE_URL`) — infra
  now exists (APIs, `digest-bot-sa` + least-priv IAM, Artifact Registry
  `digest-bot`, Firestore Native @ us-central1, TTL policy, Cloud Tasks queues
  `digest-fanout`/`interaction-followups`, 5 empty Secret Manager secrets).
  Stopped before deploy/Scheduler because the 5 credentials aren't in hand yet.
- **Admin auth decision:** will use the `ADMIN_TOKEN` bearer fallback (no IAP/LB)
  for this MVP — leave `IAP_AUDIENCE` unset.
- **Deviation — `setup.sh` hardening:** the first run silently redirected the
  Cloud Tasks step to a **different project (`airbnb-1001`)** — a concurrent
  gcloud process flipped the shared `gcloud config set project` value mid-run.
  Changed `setup.sh` to pin the project **per-process** via
  `export CLOUDSDK_CORE_PROJECT` (env var, takes precedence over and is immune to
  the shared mutable config) instead of `gcloud config set project`. No resources
  were created in the wrong project (the misdirected call failed on a disabled
  API). Re-ran clean.

### Deploy: build & first deploy (Step 3)
- **Deviation — `cloudbuild.yaml` image tag:** the config tagged the image with
  the built-in `${SHORT_SHA}`, which is **only populated for trigger-based
  builds**. DEPLOY.md instructs a manual `gcloud builds submit`, where
  `SHORT_SHA` is empty → invalid image ref (`…/digest-bot:`) → build fails.
  Replaced with a `_TAG` user substitution (default `manual`), passed explicitly
  at submit time (git short sha). Fixes every manual deploy.
- **Placeholder secrets (to unblock Discord-path testing):**
  - `GITHUB_TOKEN` = `PLACEHOLDER_replace_with_real_github_pat` — the field is
    required (app won't boot without *a* value) but unvalidated, so a placeholder
    boots the service. GitHub-backed commands 401 until a real PAT is set. User
    hadn't provided a PAT ("all the test server info" covered Discord/GCP, not
    GitHub).
  - `ANTHROPIC_API_KEY` v1 is **suspect** (218 chars, no `sk-ant-` prefix) —
    kept for boot; summarizer will fail until re-stored. Flagged to user.
- **Test env documented** in `docs/test-environment.md` (project, Discord app
  `TV1-dev`/`1222953770963570831`, two test guilds/channels, secret status).
- Admin auth: `ADMIN_TOKEN` bearer (no IAP) per earlier decision — still to be
  set post-deploy.
- **Deployed & validated (LIVE):** build+deploy succeeded (revision 00002).
  - **Public ingress:** the org blocked deploy-time `--allow-unauthenticated`;
    user granted `allUsers` `run.invoker` manually (Option A). Requests then
    reached the app.
  - **`/healthz` GFE shadow:** Google's frontend intercepts the literal
    `/healthz` path on `*.run.app` and returns its own 404 before the container;
    every other route works (`/`→app-JSON-404, `/interactions`→405, `/admin/`,
    `/docs`, `/openapi.json`→200). Route *is* registered. Cost ~most of the
    deploy debugging. **Follow-up: rename the health route to `/health`.**
  - **Step 4/5 done:** `SERVICE_URL`+`TASK_INVOKER_SA_EMAIL` set; Scheduler
    `daily-digest` + `digest_heartbeat` metric created.
  - **Discord wired:** `TV1-dev` already in guild `1238157008428077177`; 6
    commands registered to it; Interactions Endpoint URL set via API → PING→PONG
    HTTP 200 (Ed25519 verified end to end).
  - **Still needed for full function:** real `GITHUB_TOKEN` (placeholder now),
    fix suspect `ANTHROPIC_API_KEY`, set `ADMIN_TOKEN` + bootstrap
    `config.digest_channel_id` (Step 9), monitoring alert policy (Step 10).
- **Post-deploy runtime fixes (during testing):**
  - All 5 real secrets loaded & validated (Discord ✓, `GITHUB_TOKEN` v2 auth as
    `jwolberg`/5000, `ANTHROPIC_API_KEY` v2 valid, `ADMIN_TOKEN` set). Secrets
    pinned to explicit versions to force revision remounts.
  - 5 GitHub users tracked via admin API; `the-real-adammork` corrected from a
    404 typo.
  - `config.digest_channel_id` = `1238157008872542208` (automated digest target).
    On-demand `/digest`/`/repo`/`/user` reply in the invoking channel via
    `edit_original_response` — no channel config needed.
  - **Deviation — `setup.sh` missing `actAs`:** slow commands enqueue a Cloud
    Task with an OIDC token minted for `digest-bot-sa`; creating it needs
    `iam.serviceAccounts.actAs` on that SA. `setup.sh` granted only project-level
    roles, so `create_task` returned `PERMISSION_DENIED` and every deferred
    command ("didn't respond in time"). Fixed `setup.sh` to grant the SA
    `roles/iam.serviceAccountUser` on itself; live grant applied separately.

### Bug fix — empty digests (GitHub Events API drift)
- **Symptom:** `/digest today` returned "No activity to report" even for users
  who pushed within the window.
- **Root cause:** GitHub's `/users/{u}/events/public` **no longer inlines the
  `commits[]` array** in PushEvent payloads — it now returns only the
  `before`/`head` SHA range (+`repository_id`). `fetch_user_commits_since` read
  `payload.commits`, always got `[]`, so no commits were ever surfaced. Confirmed
  the stripped payload across auth schemes/API versions (not an auth issue).
- **Fix:** `app/github/client.py` — new `_commits_from_push()` hydrates each
  push via `GET /repos/{repo}/compare/{before}...{head}`; new-branch pushes
  (`before` all-zeros) fall back to the single head commit; unresolvable ranges
  (deleted/renamed repo) are skipped best-effort. Push time is kept as each
  commit's timestamp to preserve window/cursor semantics.
- **Tests:** updated `test_github_client.py` (compare mocks) + added new-branch
  and 404-skip cases; updated `test_digest_pipeline.py::_push_event` to emit
  before/head and register compare mocks. Full suite 86 passed. Deployed.
- **Scope note:** Events API is **public-only** — private-repo pushes remain
  invisible by design (user confirmed public repos are the target).

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

### Code review (post-build, adversarial) — outcomes
Ran a code-reviewer pass over the auth/idempotency-critical files. Five findings
confirmed; four fixed, one accepted as a documented tradeoff.
- **[Critical, fixed] IAP header spoofing** (`admin/api.py`): `require_admin` no
  longer trusts the unsigned `X-Goog-Authenticated-User-Email` header. It now
  verifies the signed `X-Goog-IAP-JWT-Assertion` against Google's IAP public
  keys for the configured `IAP_AUDIENCE`; bearer token remains the local-dev
  fallback.
- **[High, fixed] OIDC caller identity** (`tasks/auth.py`): `require_oidc` now
  also checks the token's `email` claim equals `task_invoker_sa_email` — a
  valid-audience token from an unrelated Google account no longer passes.
- **[High, NOT changed — documented tradeoff] duplicate report on crash between
  post and record** (`digest/pipeline.py process_user`): posting before
  recording SHAs/cursor is deliberate (the plan prioritizes "recover gracefully
  after downtime" and no-loss). Recording before posting would instead *lose* a
  report if the post then failed — strictly worse. A crash in the post→record
  window yields an at-least-once **message** dup (rare); commit-level dedup via
  `processed_commits` still bounds duplicate *reporting* to <1% (the PRD metric).
  True exactly-once would need a transactional outbox / Discord idempotency key —
  out of MVP scope.
- **[Medium, fixed] duplicate header on partial-failure retry** (`run_fanout`):
  per-user enqueue is now best-effort (catch `EnqueueError`, log, continue) so
  one enqueue failure can't 500 the job and make Cloud Scheduler retry (which
  would re-post the header).
- **[Medium, fixed] same-second events silently dropped** (`github/client.py`):
  the cursor lower bound is now inclusive (`created < since` to stop paging), so
  a distinct later push sharing the cursor's 1-second timestamp is re-included;
  SHA dedup filters ones already reported.
- Ruled out by the reviewer (no change): `_encode` doc-id collisions (GitHub
  owners can't contain `_`), `audience=""` OIDC bypass (google-auth enforces
  empty audience → fails closed), Ed25519 verify (correct, constant-time).

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
