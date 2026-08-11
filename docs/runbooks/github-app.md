# Runbook — GitHub App (per-member private-repo digest)

Rollout for the per-member private-repo digest (`SPEC-GHAPP` / `ADR-0002`).
Members install a GitHub App on the repos they choose; the bot reads them with
short-lived installation tokens and posts an attributed section in the daily
digest. All `GITHUB_APP_*` config is empty by default, so this whole path is
**inert until you complete the steps below**.

Deploy target is project **`cohort-bot-1`** (always pass `--project cohort-bot-1`).

## [1] Register the GitHub App

GitHub → **Settings → Developer settings → GitHub Apps → New GitHub App** (under
the **owner's personal account**, per ADR-0002 §C1).

- **Homepage URL:** `<SERVICE_URL>` (any valid URL).
- **Webhook URL:** `<SERVICE_URL>/github/webhook`
- **Webhook secret:** generate a strong random string — you'll store it as
  `GITHUB_APP_WEBHOOK_SECRET`.
- **Setup URL:** `<SERVICE_URL>/github/setup` (check *"Redirect on update"* off is fine).
- **Permissions → Repository:** **Contents: Read-only**, **Metadata: Read-only**.
  Nothing else. No account, org, or write permissions.
- **Subscribe to events:** **Installation target**, **Installation**,
  **Installation repositories**.
- **Where can this app be installed:** **Any account** (public), but leave it
  **unlisted** — you share the link directly (ADR-0002 §C4).

After creating it, note the **App ID** and **App slug** (from the public URL
`github.com/apps/<slug>`), and **generate a private key** (downloads a `.pem`).

## [2] Store secrets + env

Sensitive values → Secret Manager; identifiers → plain Cloud Run env.

```bash
# From deploy/setup.sh — provision the two App secrets (opt-in flag):
ENABLE_GITHUB_APP=1 PROJECT_ID=cohort-bot-1 REGION=us-central1 \
  bash deploy/setup.sh

# Set their values (use printf %s to avoid a trailing newline):
gcloud secrets versions add GITHUB_APP_PRIVATE_KEY \
  --project cohort-bot-1 --data-file=/path/to/app-private-key.pem
printf %s 'YOUR_WEBHOOK_SECRET' | gcloud secrets versions add GITHUB_APP_WEBHOOK_SECRET \
  --project cohort-bot-1 --data-file=-
```

Mount the secrets and set the identifiers on the service (merges with existing env):

```bash
gcloud run services update digest-bot --project cohort-bot-1 --region us-central1 \
  --update-secrets "GITHUB_APP_PRIVATE_KEY=GITHUB_APP_PRIVATE_KEY:latest,GITHUB_APP_WEBHOOK_SECRET=GITHUB_APP_WEBHOOK_SECRET:latest" \
  --update-env-vars "GITHUB_APP_ID=<APP_ID>,GITHUB_APP_SLUG=<APP_SLUG>"
```

> The private key is a multi-line PEM. Storing the file directly with
> `--data-file=` preserves the newlines; do not echo it through a shell.

## [3] Deploy + verify webhook

Redeploy the image if you shipped code (`gcloud builds submit --config
deploy/cloudbuild.yaml`), then confirm GitHub can reach the webhook: in the App's
**Advanced** tab, GitHub shows recent deliveries — a `ping` should return `200`.
A `401` there means the `GITHUB_APP_WEBHOOK_SECRET` on the service doesn't match
the App's configured secret.

## [4] Onboard members

Share the install link in the cohort channel:

```
https://github.com/apps/<APP_SLUG>/installations/new
```

Each member installs and picks repos. Confirm in the admin panel → **Cohort
members (GitHub App)** that they appear with `installed` and a repo count, or:

```bash
curl -s "<SERVICE_URL>/admin/api/members" -H "Authorization: Bearer $ADMIN_TOKEN"
```

## [5] Verify the first digest

After the next scheduled run (or trigger a test digest), a `🧑‍💻 @<login>`
section should appear in the cohort channel for members with new commits, and a
`digest_installation_posted` log line should be present. `digest_heartbeat` now
carries `installations` / `installations_enqueued` counts.

## [6] Disable / rollback

- **One member:** they uninstall from GitHub (Settings → Applications) → the
  `installation.deleted` webhook soft-disables their installation, member record,
  and repos; nothing is scanned next run.
- **Whole feature:** clear the App env/secrets (or unset `GITHUB_APP_ID`) and
  redeploy — `_default_gh_app` returns `None`, so `process_installation` no-ops.
  The rest of the digest is unaffected.

## [7] Acceptance criteria (SPEC-GHAPP §12) — verified

The end-to-end path is covered by `tests/test_gh_app_e2e.py`: install webhook →
fan-out → attributed member section → uninstall stops scanning, with no member
token persisted. The remaining criteria (App-unset == prior behavior; one
member's failure doesn't break others) are covered in `tests/test_app_auth.py`
and `tests/test_digest_pipeline.py`.
