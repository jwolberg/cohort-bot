# Implementation

## Scope Implemented
- **Requested scope:** Substack publication tracking (spec.md + BUILD_PLAN.md), tickets S1–S6, plus the S7 TTL.
- **Related phase:** Phases 1–5.
- **Related ticket(s):** P1-T1, P2-T1, P3-T1, P3-T2, P4-T1, P4-T2 (Complete); P5-T1 (In Progress — TTL done, PRD/ARCHITECTURE doc updates pending).

## Approach
- **High-level strategy:** Mirror each existing GitHub-source layer for publications, skipping the Claude summarizer (native feed excerpt only). One commit per ticket, tests green before each commit.
- **Key decisions:**
  - Cursor initialized to **add time** (`SERVER_TIMESTAMP`) on `add()` so a new feed never dumps its back catalog (A5).
  - `defusedxml` is the single net-new runtime dependency (A1); dates via `email.utils`, excerpts via `html.parser`.
  - Dedup key = feed `guid`, fallback `link`; entries missing a parseable `pubDate` or both keys are skipped.
  - Best-effort per feed: `NotFoundError`/`SubstackError` are caught in the pipeline and skipped so one bad feed never fails the run/command.
  - Reused the `digest-fanout` queue and the daily scheduler — no new queue/scheduler/secret/IAM (AC#10).
  - Admin POST normalizes any pasted host/publication/feed URL → `https://<host>/feed`.
- **Assumptions:** custom-domain feeds are assumed to live at `/feed` (Substack-style only, per spec Scope); `title` is optional on add and falls back to the slug in rendering (no add-time channel-title fetch, to keep the client surface small).

---

## Implementation Plan
1. **S1 store** — `TrackedPublicationsRepo` + `ProcessedPostsRepo`, threaded into `Repositories`.
2. **S2 client** — `app/substack/client.py` (`SubstackClient`, `PostRef`, parser, `slug_for`, `clean_excerpt`); add `defusedxml`.
3. **S3a pipeline** — `PublicationSection`, `compute_publication_section`, `substack_factory`; formatter `format_publication_section` + `format_substack`.
4. **S3b/S4 worker** — enqueue helper + path, `process_publication`, `run_fanout` fan-out, OIDC route + wiring.
5. **S5 command** — `SUBSTACK_COMMAND`, `on_demand_substack`, slow-path branch, provider, HELP_TEXT.
6. **S6 admin** — `/admin/api/publications` CRUD + Publications panel section.
7. **S7 deploy** — `processed_posts` TTL in `setup.sh`; impl-notes appended.

---

## Code Changes

### File: app/store/repositories.py
- Added `TrackedPublicationsRepo` (`tracked_publications`, doc id = slug; add/remove/get/list_enabled/list_all/get_cursor/set_cursor; cursor init = add time) and `ProcessedPostsRepo` (`processed_posts`, doc id `slug@post_id`, 90d `expire_at`; `has_post`/`record_posts`). Added both to the `Repositories` dataclass + `get_repositories()`.

### File: app/substack/__init__.py, app/substack/client.py (new)
- `SubstackClient` (async CM, injectable httpx client, typed errors, bounded concurrency), `PostRef`, `fetch_posts_since`, `clean_excerpt`, `slug_for`, RFC-822 date parsing, response-size cap, defusedxml parsing.

### File: app/digest/pipeline.py
- `PublicationSection`, `substack_factory`, `compute_publication_section`, `process_publication`, `run_fanout` publication fan-out, `on_demand_substack` + `_window_since`, wiring in `install_digest()`.

### File: app/digest/formatter.py
- `format_publication_section` (one 📰 native-excerpt embed) and `format_substack` (on-demand batched list + empty state).

### File: app/tasks/queue.py
- `SUBSTACK_PUBLICATION_PATH` + `enqueue_substack_publication` (reuses `digest-fanout`).

### File: app/discord/interactions.py
- OIDC-gated `/tasks/substack/publication` route + `set_publication_worker`.

### File: app/discord/commands.py, app/discord/handlers.py
- `SUBSTACK_COMMAND` (optional `window` with `1d`/`7d`/`30d`); `substack` in `SLOW_COMMANDS`; `run_followup` branch; `set_substack_provider`; `HELP_TEXT`.

### File: app/admin/api.py, app/admin/static/index.html
- `/admin/api/publications` GET/POST/DELETE (`_normalize_feed_url` + `slug_for`); Publications panel section (Alpine.js add/list/remove).

### File: deploy/setup.sh
- Enable Firestore TTL on `processed_posts.expire_at`.

### Files: tests/* (repositories, substack_client, digest_pipeline, commands, handlers, admin_api)
- New coverage for every layer.

---

## Acceptance Criteria Mapping
- **AC#1** (POST normalizes + stores doc, idempotent re-add): `app/admin/api.py:add_publication`, `repositories.py:TrackedPublicationsRepo.add` — `tests/test_admin_api.py::test_publications_crud_and_normalization`, `tests/test_repositories.py::test_publication_reAdd_is_idempotent_preserving_cursor`.
- **AC#2** (GET lists / DELETE removes / admin auth): `app/admin/api.py` — `test_admin_api.py::test_publications_crud_and_normalization`, `::test_publications_endpoint_requires_admin`.
- **AC#3** (panel add/list/remove): `app/admin/static/index.html` — `test_admin_api.py::test_static_panel_serves_required_elements`.
- **AC#4** (`fetch_posts_since` fields, since filter, newest-first, excerpt): `app/substack/client.py` — `tests/test_substack_client.py::test_fetch_maps_all_fields`, `::test_since_filters_strictly_newer`.
- **AC#5** (one task per publication; post-first then record+cursor): `pipeline.run_fanout`/`process_publication` — `test_digest_pipeline.py::test_fanout_enqueues_one_task_per_publication`, `::test_process_publication_posts_and_advances_cursor`.
- **AC#6** (idempotent no-op on no new posts): `test_digest_pipeline.py::test_process_publication_posts_and_advances_cursor` (re-run branch).
- **AC#7** (`/substack` window, no dedup): `pipeline.on_demand_substack` — `::test_on_demand_substack_lists_recent_posts`, `test_commands.py::test_substack_declares_optional_window_with_choices`.
- **AC#8** (bad feed skipped + logged): `compute_publication_section` — `::test_publication_section_skips_broken_feed`, client `::test_404_raises_not_found`/`::test_invalid_xml_raises_substack_error`.
- **AC#9** (`expire_at` + TTL policy): `ProcessedPostsRepo.record_posts` + `deploy/setup.sh` — `test_repositories.py::test_record_and_check_posts`.
- **AC#10** (no new env/secret/IAM/queue/scheduler): reused `digest-fanout` queue + daily scheduler; `defusedxml` is the only new dependency.

---

## Build Plan Mapping
- **P1-T1** — Complete — both repos + bundle + emulator tests.
- **P2-T1** — Complete — client + parser + respx tests; `defusedxml` added.
- **P3-T1** — Complete — section compute + formatter + factory + tests.
- **P3-T2** — Complete — worker + enqueue + fan-out + OIDC route + tests.
- **P4-T1** — Complete — `/substack` command + on-demand path + tests.
- **P4-T2** — Complete — admin CRUD + panel + tests.
- **P5-T1** — In Progress — `processed_posts` TTL added to `setup.sh`; **remaining:** update `docs/PRD.md` and `docs/ARCHITECTURE.md` (§5 commands, §7 collections) to document the Substack source.

---

## Validation
- **Tests:** full suite **139 passed** (`uv run pytest -q`). New tests: repositories (+8), substack client (+10), pipeline/formatter (+11), commands (+1), handlers (+2), admin (+3).
- **App boot:** `create_app()` initializes cleanly; `/tasks/substack/publication` responds 401 without OIDC (route present); `COMMANDS` includes `substack`; substack provider + publication worker are wired.
- **Lint:** no linter is configured in this repo (no ruff/flake8/black in `pyproject.toml`).
- **Manual e2e (not run here):** add a real public Substack feed via the panel, trigger a digest run, confirm a deduped per-publication message posts; run `/substack`; confirm a second run with no new posts is silent. (Requires live GCP/Discord — see spec Validation Plan.)
- **Visible user outcome:** admins can add/list/remove Substack publications in the panel; the daily digest posts one 📰 message per publication with new posts; `/substack [1d|7d|30d]` returns recent posts on demand.

---

## Open Issues
- **Docs:** PRD/ARCHITECTURE updates for the Substack source are still pending (P5-T1 remainder).
- **Custom-domain feeds** whose RSS is not at `/feed` are not supported (spec scope: Substack-style feeds only).
- **Publication title** is only populated if provided on add; otherwise rendering falls back to the slug. A best-effort channel-title fetch was deliberately deferred.
- **Heartbeat SLO:** `run_fanout` still emits the heartbeat only when the GitHub header posts (channel + users present). A publications-only deployment (no tracked users) would enqueue Substack tasks but emit no heartbeat — acceptable for v1 since GitHub is the primary source; revisit if Substack-only becomes a real config.
- **Manual e2e** against live GCP/Discord has not been executed in this environment.

---

## BUILD_PLAN Update
- **Current phase:** Phase 5 — Deploy & Docs.
- **Current ticket:** P5-T1 (In Progress).
- **Updated ticket status:** P1-T1…P4-T2 = Complete; P5-T1 = In Progress (TTL done; PRD/ARCHITECTURE pending).
- **Blockers:** None.
- **Recommended next ticket:** finish P5-T1 — document the Substack source in `docs/PRD.md` and `docs/ARCHITECTURE.md`, then close the feature.
