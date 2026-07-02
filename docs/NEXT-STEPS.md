# Next Steps

Pick-up notes for the next working session. Current state and what to do next,
roughly in priority order.

_Last updated: 2026-07-02 (end of MVP build)._

---

## Where we are

- **MVP is code-complete.** All 12 plan units (U1–U12) built, **84 tests
  passing**, pushed to `main` at `github.com/jwolberg/cohort-bot`.
- **Reviewed & hardened.** An adversarial code review found 5 issues; 4 fixed
  (IAP JWT verification, OIDC caller-identity check, best-effort digest enqueue,
  inclusive commit cursor) and 1 documented as an accepted tradeoff. See
  `docs/implementation-notes.md` → "Code review".
- **Deploy runbook ready** in `DEPLOY.md`; `deploy/` scaffolding validated
  locally (`bash -n`, YAML parse, `uv build`).
- **Not yet deployed.** No live GCP project, Discord app, or API keys have been
  wired — that's the immediate next task.

The plan (`docs/plans/2026-07-02-001-...`) is marked `status: completed`.

---

## 1. First production deploy (immediate)

Follow **[DEPLOY.md](../DEPLOY.md)** end to end. Before starting, gather:

- [ ] A GCP project with billing enabled; `gcloud` authenticated as owner.
- [ ] A Discord application + bot: **Application ID, Bot Token, Public Key**, and a **test guild**.
- [ ] A **GitHub PAT** and an **Anthropic API key**.

Watch these easy-to-miss steps (each has burned us in the runbook design):

- [ ] **Step 4 — set `SERVICE_URL` + `TASK_INVOKER_SA_EMAIL` after the first
      deploy.** Without `SERVICE_URL`, every `/tasks/*` call fails closed (it's
      the OIDC audience). This is the #1 thing that will look "mysteriously
      broken" if skipped.
- [ ] **Firestore Native mode is irreversible** — `setup.sh` creates it; confirm
      the project has no existing Datastore-mode database.
- [ ] **Bootstrap `config.digest_channel_id`** (Step 9) or the digest has nowhere
      to post and no heartbeat fires.
- [ ] **Register slash commands against the test guild first** (instant), verify,
      then register globally.

**Definition of done for the deploy:** run the DEPLOY.md verification checklist —
`/help`, `/track`, `/repo` all work in Discord, the admin panel loads, a manual
test digest posts, and a `digest_heartbeat` log appears after the first scheduled
run.

---

## 2. Post-deploy validation & monitoring

- [ ] Create the **Cloud Monitoring alert policy** (metric absence on
      `logging/user/digest_heartbeat`, ~26h). `setup.sh` only creates the log
      metric; the alert is still a manual Console step (DEPLOY.md Step 10).
- [ ] Watch the **first real scheduled digest** (default 13:00 UTC) — confirm the
      header + per-user messages render like the PRD example and dedup holds on a
      second manual run.
- [ ] Decide on **`--min-instances=1`** if cold starts threaten the 3s ACK on
      fast commands (monitor first; only add if observed).

---

## 3. Known follow-ups & small cleanups

Surfaced during the build — none block the MVP, but worth a pass:

- [ ] **Wire or remove `config.digest_hour_utc`.** It's in the Firestore schema
      and editable via the admin panel, but the actual schedule is the Cloud
      Scheduler cron — the field is currently informational. Either drive the
      scheduler from it or drop it to avoid confusion.
- [ ] **Wire or remove `DEFAULT_DIGEST_CHANNEL_ID`.** Defined in `config.py` as a
      bootstrap value but nothing seeds the Firestore `config` singleton from it.
      Either seed on startup or remove; today the channel must be set via the
      admin panel.
- [ ] **Capture Anthropic token usage** in the summarizer and add it to the
      digest heartbeat log. U12's ideal "LLM tokens" field is currently absent
      (`summarizer.claude` discards `response.usage`).
- [ ] **Script the alert policy** in `setup.sh` (`gcloud alpha monitoring
      policies create` with an absence condition) so the whole SLO setup is
      reproducible.
- [ ] **Add CI** — a GitHub Actions workflow that runs `uv run pytest` on PRs
      (the emulator fixture needs the `cloud-firestore-emulator` component + a
      pre-3.12 Python for gcloud; see `tests/conftest.py`).
- [ ] **Add a linter** — no ruff/formatter config exists yet; adopt `ruff` for
      lint + format to match the "run lint before commit" workflow.

---

## 4. Reliability item to revisit (documented tradeoff)

`digest/pipeline.py::process_user` posts to Discord **before** recording SHAs /
advancing the cursor, to guarantee no lost reports on downtime. The rare cost is
an at-least-once **message** dup if the instance crashes in the post→record
window. Commit-level dedup keeps duplicate *reporting* under the PRD's <1%.

If duplicate messages are ever observed in practice, the fix is a transactional
outbox or a Discord-side idempotency key — out of MVP scope, tracked here.

---

## 5. Deferred features (from the plan's roadmap)

Explicitly out of MVP scope, in rough priority:

- **Weekly digest** — trends + milestones over 7 days.
- **Repository trends** — commit velocity, contributor activity, inactive repos.
- **GitHub issue / PR summaries** — opened/closed issues, PRs, reviews.
- **AI release notes** from commit history.
- **GraphQL batching** for GitHub — once REST rate limits bind at 500+ users.
- **GitHub App auth** — replaces the PAT for higher/again-scaling rate limits.

Out of scope for this product's identity: Substack/newsletter intelligence,
gateway-only Discord features (message listening, presence, voice).

---

## Dev environment quick reference

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
cp .env.example .env         # fill in real values for local runs
uv run pytest                # emulator-backed + mocked; ~7s
uv run uvicorn app.main:app --reload --port 8080
```

Integration tests auto-start the Firestore emulator (needs
`gcloud components install cloud-firestore-emulator`). See `README.md` and
`docs/implementation-notes.md` for the emulator/py3.12 gotcha and other build
decisions.
