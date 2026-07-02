# Production Deployment Guide

End-to-end runbook for deploying the GitHub Activity Digest Discord bot to
Google Cloud Run. Follow the steps in order — several later steps depend on
values produced by earlier ones.

> **Time estimate:** ~45–60 min for a first deploy (most of it is IAP + Discord
> portal setup, which are one-time).

---

## Architecture at a glance

```
Discord ──POST /interactions (Ed25519)──▶ ┌────────────────────────┐
Cloud Scheduler ──POST /tasks/digest/run (OIDC)──▶ Cloud Run: digest-bot │──▶ Firestore (Native)
Cloud Tasks ──POST /tasks/* (OIDC)──────▶ │  FastAPI / Uvicorn      │──▶ Secret Manager
Admin browser ──GET /admin/* (IAP)──────▶ └───────────┬────────────┘──▶ GitHub REST / Anthropic / Discord REST
```

Security is enforced **in-app**, so the service runs with public ingress
(`--allow-unauthenticated`):

| Surface | Protection |
|---|---|
| `/interactions` | Ed25519 signature (Discord public key) |
| `/tasks/*`, `/tasks/digest/*` | Google OIDC token (verified signature + audience + caller = `digest-bot-sa`) |
| `/admin/*` | Identity-Aware Proxy (or `ADMIN_TOKEN` bearer fallback) |
| `/healthz` | public (liveness) |

---

## Prerequisites

**Accounts & external resources**

- A GCP project with **billing enabled**, and `gcloud` authenticated as an owner/editor.
- A **Discord application** (Developer Portal) with a bot user — you need its
  Application ID, Bot Token, and Public Key. Create a **test guild** for dev.
- A **GitHub Personal Access Token** (classic or fine-grained, `public_repo` /
  read scope is enough for public activity).
- An **Anthropic API key**.

**Local tools**

- `gcloud` (Cloud SDK) — authenticated (`gcloud auth login`, `gcloud auth application-default login`).
- Python 3.12 + [`uv`](https://github.com/astral-sh/uv) (only needed to run
  `scripts/register_commands.py`).
- This repo checked out.

**Credentials → secrets map**

| Value | Where to get it | Secret Manager name |
|---|---|---|
| Discord public key | Discord Portal → General Information | `DISCORD_PUBLIC_KEY` |
| Discord bot token | Discord Portal → Bot | `DISCORD_TOKEN` |
| Discord application ID | Discord Portal → General Information | `DISCORD_APP_ID` |
| GitHub PAT | github.com → Settings → Developer settings | `GITHUB_TOKEN` |
| Anthropic API key | console.anthropic.com | `ANTHROPIC_API_KEY` |

---

## Step 0 — Set shell variables

```bash
export PROJECT_ID="your-gcp-project"
export REGION="us-central1"
export SERVICE="digest-bot"
export SA_EMAIL="digest-bot-sa@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud config set project "$PROJECT_ID"
```

---

## Step 1 — Provision infrastructure (pass 1)

Idempotent; safe to re-run.

```bash
PROJECT_ID="$PROJECT_ID" REGION="$REGION" bash deploy/setup.sh
```

This creates:
- APIs enabled; service account `digest-bot-sa` with least-privilege IAM
  (`datastore.user`, `cloudtasks.enqueuer`, `secretmanager.secretAccessor`, `run.invoker`).
- Artifact Registry repo `digest-bot`.
- **Firestore in Native mode** — ⚠️ this choice is **irreversible** for the project.
- TTL policy on `processed_commits.expire_at` (~90-day auto-expiry).
- Cloud Tasks queues `digest-fanout` and `interaction-followups`.
- **Empty** Secret Manager secrets (values set in the next step).
- The `digest_heartbeat` log-based metric.

It stops before the Scheduler step because that needs the deployed URL (Step 5).

---

## Step 2 — Set secret values

```bash
printf %s 'YOUR_DISCORD_PUBLIC_KEY' | gcloud secrets versions add DISCORD_PUBLIC_KEY --data-file=-
printf %s 'YOUR_DISCORD_BOT_TOKEN'  | gcloud secrets versions add DISCORD_TOKEN      --data-file=-
printf %s 'YOUR_DISCORD_APP_ID'     | gcloud secrets versions add DISCORD_APP_ID     --data-file=-
printf %s 'YOUR_GITHUB_PAT'         | gcloud secrets versions add GITHUB_TOKEN       --data-file=-
printf %s 'sk-ant-YOUR_KEY'         | gcloud secrets versions add ANTHROPIC_API_KEY  --data-file=-
```

> Use `printf %s` (not `echo`) to avoid a trailing newline in the secret.

---

## Step 3 — Build & deploy the image

```bash
gcloud builds submit --config deploy/cloudbuild.yaml \
  --substitutions=_REGION="$REGION"
```

This builds the container, pushes to Artifact Registry, and deploys to Cloud Run
with the service account and the 5 secrets mapped in as env vars.

Grab the service URL:

```bash
export SERVICE_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" \
  --format='value(status.url)')"
echo "$SERVICE_URL"
```

Sanity check the app is up:

```bash
curl -s "$SERVICE_URL/healthz"   # -> {"status":"ok"}
```

---

## Step 4 — Set deploy-derived runtime env (REQUIRED)

`SERVICE_URL` and `TASK_INVOKER_SA_EMAIL` aren't known until after the first
deploy, but the app needs them at runtime — `SERVICE_URL` is used to build Cloud
Task target URLs **and** as the OIDC audience the worker endpoints verify against.
Without it, every `/tasks/*` call fails closed.

```bash
gcloud run services update "$SERVICE" --region "$REGION" \
  --update-env-vars "SERVICE_URL=${SERVICE_URL},TASK_INVOKER_SA_EMAIL=${SA_EMAIL}"
```

(`--update-env-vars` merges, so this preserves the env set by Cloud Build. The
`IAP_AUDIENCE` var is added in Step 6.)

---

## Step 5 — Finish infrastructure (pass 2)

Re-run `setup.sh` with the URL to create the Cloud Scheduler `daily-digest` job
(OIDC → `/tasks/digest/run`). The digest fires daily at `DIGEST_HOUR_UTC`
(default 13:00 UTC — override with the env var).

```bash
PROJECT_ID="$PROJECT_ID" REGION="$REGION" SERVICE_URL="$SERVICE_URL" \
  DIGEST_HOUR_UTC=13 bash deploy/setup.sh
```

> The daily trigger time is controlled by this **Cloud Scheduler cron**, not by
> the Firestore `config.digest_hour_utc` field (which is schema-only). To change
> the time later, update the scheduler job.

---

## Step 6 — Put IAP in front of `/admin/*`

The admin panel is the only browser-facing surface. Front `/admin/*` with
Identity-Aware Proxy so only allow-listed Google accounts reach it.

Because Discord and the schedulers must reach `/interactions` and `/tasks/*`
**without** IAP, put IAP on an **external HTTPS load balancer** with a serverless
NEG and **path-based routing**: route `/admin/*` to an IAP-enabled backend and
everything else to a non-IAP backend pointing at the same Cloud Run service.
(See Google's "Enabling IAP for Cloud Run" guide.)

Then:

```bash
# Grant yourself access through IAP
gcloud iap web add-iam-policy-binding \
  --resource-type=backend-services --service=<ADMIN_BACKEND_SERVICE> \
  --member="user:you@example.com" --role="roles/iap.httpsResourceAccessor"
```

Get the IAP JWT audience for that backend and set it on the service so the app
verifies the signed assertion:

```bash
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
BACKEND_ID="$(gcloud compute backend-services describe <ADMIN_BACKEND_SERVICE> \
  --global --format='value(id)')"
export IAP_AUDIENCE="/projects/${PROJECT_NUMBER}/global/backendServices/${BACKEND_ID}"

gcloud run services update "$SERVICE" --region "$REGION" \
  --update-env-vars "IAP_AUDIENCE=${IAP_AUDIENCE}"
```

> **Simpler alternative (no IAP):** skip the load balancer, set an `ADMIN_TOKEN`
> secret, and authenticate admin API calls with `Authorization: Bearer <token>`.
> Weaker (a shared token, and the admin page is then reachable by URL), but fine
> for a private MVP. If you do this, leave `IAP_AUDIENCE` unset.

---

## Step 7 — Register slash commands

Guild registration is instant (use it for the first verification); global
registration propagates in ~1h.

```bash
uv run python -m scripts.register_commands --guild <TEST_GUILD_ID>
# later, for production:
uv run python -m scripts.register_commands
```

This reads `DISCORD_APP_ID` + `DISCORD_TOKEN` from your local env/`.env`, so set
those locally (they don't need to match anything server-side beyond being the
same app).

---

## Step 8 — Point Discord at the interactions endpoint

In the Discord Developer Portal → your app → **General Information** →
**Interactions Endpoint URL**, set:

```
<SERVICE_URL>/interactions
```

Discord immediately sends a signed `PING`; the app must answer `PONG`. If it
saves without error, signature verification is working end to end. If it errors,
see Troubleshooting.

---

## Step 9 — Bootstrap runtime config (digest channel + admins)

The digest channel, admin role IDs, etc. live in the Firestore `config`
singleton — set them via the admin API (through IAP in the browser, or with the
bearer token):

```bash
# Example with the ADMIN_TOKEN fallback:
curl -X PUT "$SERVICE_URL/admin/api/config" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"digest_channel_id":"<CHANNEL_ID>","digest_hour_utc":13,"admin_role_ids":["<ROLE_ID>"]}'
```

- `digest_channel_id` — the channel the daily digest posts to. **Required** or
  the digest has nowhere to post (and no SLO heartbeat fires).
- `admin_role_ids` — Discord role IDs allowed to run `/track add|remove`.

The bot must be a member of the target guild with permission to post in that
channel.

---

## Step 10 — Monitoring alert (digest SLO)

`setup.sh` created the `digest_heartbeat` log metric. Create an alert policy that
**pages when the metric reports no data over ~26h** — that means the daily
digest failed to post (the PRD SLO is >99% of days).

Console → Monitoring → Alerting → Create Policy → condition type **Metric
absence** on `logging/user/digest_heartbeat`, duration 26h. (Or
`gcloud alpha monitoring policies create` with an absence condition.)

---

## Verification checklist

```bash
curl -s "$SERVICE_URL/healthz"                 # {"status":"ok"}
```

- [ ] Discord Portal accepted the Interactions Endpoint URL (PING → PONG).
- [ ] `/help` and `/track list` respond in the test guild (fast path).
- [ ] `/repo owner/repo` responds after a "thinking…" defer (slow path → Cloud Tasks → follow-up).
- [ ] `/track add <user>` by an admin persists; a non-admin gets an ephemeral denial.
- [ ] Admin panel loads at `<SERVICE_URL>/admin/` and lists/edits users.
- [ ] Trigger a test digest (admin panel "Run test digest" or
      `POST /admin/api/digest/test`) → header + per-user messages appear in the channel.
- [ ] After a real scheduled run, a `digest_heartbeat` entry appears in Cloud Logging.

---

## Redeploy & rollback

**Redeploy** (new code): re-run Step 3. `--update-env-vars`/`--update-secrets`
preserve the runtime env you set in Steps 4 & 6.

**Rollback** to a previous revision:

```bash
gcloud run revisions list --service "$SERVICE" --region "$REGION"
gcloud run services update-traffic "$SERVICE" --region "$REGION" \
  --to-revisions <REVISION>=100
```

Slash-command schema changes require re-running `scripts/register_commands.py`.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Discord rejects the Interactions Endpoint URL | Wrong `DISCORD_PUBLIC_KEY` secret, or the service isn't public. Confirm `curl $SERVICE_URL/healthz` works and the deploy used `--allow-unauthenticated`. |
| Slash commands "The application did not respond" | Cold start missed the 3s ACK. Fast commands should be well under; if persistent, set Cloud Run `--min-instances=1`. |
| `/repo` defers forever (never edits) | Follow-up worker failing. Check `/tasks/followup` logs; verify `SERVICE_URL` + `TASK_INVOKER_SA_EMAIL` env are set (Step 4) and the `interaction-followups` queue exists. |
| `/tasks/*` return 403 | OIDC audience mismatch (`SERVICE_URL` not set or scheduler/queue audience differs) or caller email ≠ `digest-bot-sa`. |
| No daily digest, no heartbeat | `config.digest_channel_id` unset (Step 9), or the Scheduler job didn't fire. Check the `daily-digest` job and the channel post permissions. |
| Admin API returns 401 | No verified IAP assertion and no valid `ADMIN_TOKEN` bearer. Behind IAP, confirm `IAP_AUDIENCE` matches the backend service. |
| Duplicate digest header | A partial Scheduler retry — mitigated (best-effort enqueue) but a header can still dup if the header post itself is retried. Cosmetic. |

---

## Configuration reference

**Runtime env vars** (secrets via Secret Manager; the rest via `--update-env-vars`):

| Var | Source | Purpose |
|---|---|---|
| `DISCORD_PUBLIC_KEY` / `DISCORD_TOKEN` / `DISCORD_APP_ID` | secret | Discord auth + REST |
| `GITHUB_TOKEN` | secret | GitHub REST (5k req/hr) |
| `ANTHROPIC_API_KEY` | secret | Claude summarizer |
| `GCP_PROJECT` / `GCP_LOCATION` | Cloud Build | project + region |
| `SERVICE_URL` | Step 4 | Cloud Task targets + OIDC audience |
| `TASK_INVOKER_SA_EMAIL` | Step 4 | mints task OIDC tokens; inbound caller check |
| `IAP_AUDIENCE` | Step 6 | verify signed IAP assertion for `/admin/*` |
| `ADMIN_TOKEN` | optional secret | admin bearer fallback when no IAP |
| `SUMMARIZER_MODEL` | optional | default `claude-haiku-4-5` |
| `DIGEST_FANOUT_QUEUE` / `FOLLOWUPS_QUEUE` | optional | queue names (defaults match `setup.sh`) |

**Firestore `config` singleton** (set via admin API, not env):
`digest_channel_id`, `digest_hour_utc` (informational — schedule is the Scheduler
cron), `admin_role_ids`.

---

## Cost profile

At MVP scale everything but Anthropic sits in free tiers (Cloud Run scale-to-zero,
Firestore free quota, 3 free Scheduler jobs, 1M free Cloud Tasks ops/month). The
dominant cost is Claude tokens — Haiku keeps a daily run over hundreds of users
at cents/day. See `docs/ARCHITECTURE.md` §12.
