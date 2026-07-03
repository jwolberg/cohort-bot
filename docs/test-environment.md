# Test / Deploy Environment Reference

Non-secret identifiers for the first production deploy + test setup. **No secret
values live here** — tokens/keys are in Secret Manager (project `cohort-bot-1`)
and the macOS keychain. Safe to commit (all IDs below are public identifiers).

_Last updated: 2026-07-02._

## GCP

| Item | Value |
|---|---|
| Project ID | `cohort-bot-1` |
| Project number | `587338514666` |
| Region | `us-central1` |
| Cloud Run service | `digest-bot` |
| Service account | `digest-bot-sa@cohort-bot-1.iam.gserviceaccount.com` |
| Artifact Registry repo | `digest-bot` (us-central1) |
| Firestore | `(default)`, Native mode, us-central1 |
| Billing account | `0142FA-BE81E1-A43239` |

## Deploy status (2026-07-02 — LIVE)

- **Service URL:** `https://digest-bot-xrowcvf5wq-uc.a.run.app` (revision `digest-bot-00002-bex`, 100% traffic).
- **Public ingress:** `allUsers` → `roles/run.invoker` (added manually — org policy blocked the deploy-time `--allow-unauthenticated`). Security enforced in-app.
- **Interactions Endpoint URL:** set on `TV1-dev` → `…/interactions`. Discord PING→PONG **validated** (HTTP 200), so Ed25519 verification works end to end.
- **Slash commands:** 6 registered to guild `1238157008428077177` (instant).
- **Scheduler:** `daily-digest` → `/tasks/digest/run` (OIDC), 13:00 UTC. `digest_heartbeat` log metric created.
- **`/healthz` caveat:** Google's frontend shadows the literal `/healthz` path on `*.run.app` (returns Google's 404 before reaching the app). Cosmetic — the route is registered (`app/main.py:21`) and all real endpoints work. **Follow-up:** rename health route to `/health` so the DEPLOY.md health check works.

## Discord application (the digest bot's identity)

| Item | Value |
|---|---|
| Bot username | `TV1-dev` |
| Application ID | `1222953770963570831` |
| Public Key | `a190517e96b30283ffa5bcc932c386c156abb11e44ead9a0cfedf215188fb19b` (public — verifies interaction signatures) |
| Bot Token | macOS keychain item `cohortbot-JAY_TEST_DISCORD_TOKEN` → Secret Manager `DISCORD_TOKEN` |

Token/app/public-key verified consistent (`GET /users/@me` returns id
`1222953770963570831`).

Invite URL (a **server admin** opens it to add the bot; only needs App ID):

```
https://discord.com/oauth2/authorize?client_id=1222953770963570831&scope=bot+applications.commands&permissions=19456
```

`permissions=19456` = View Channel + Send Messages + Embed Links.

## Test servers (guilds) & channels

From the provided `discord.com/channels/<GUILD>/<CHANNEL>` URLs:

| Role | Guild (server) ID | Channel ID |
|---|---|---|
| Primary (invite target / default digest) | `1238157008428077177` | `1238157008872542208` |
| Secondary | `1516303266323890237` | `1516303267330785312` |

- **Guild ID** → slash-command registration: `register_commands --guild <GUILD_ID>` (instant).
- **Channel ID** → Firestore `config.digest_channel_id` (where the daily digest posts; DEPLOY.md Step 9).

> The server also runs other bots (hikari, etc.). That's fine — each bot is its
> own application; installing `TV1-dev` doesn't affect them. (Do **not** point
> another running bot's application at this service's Interactions Endpoint URL —
> it would divert that bot's gateway interactions.)

## Secret Manager status (project `cohort-bot-1`)

| Secret | Status |
|---|---|
| `DISCORD_APP_ID` | ✅ real (`1222953770963570831`) |
| `DISCORD_PUBLIC_KEY` | ✅ real |
| `DISCORD_TOKEN` | ✅ real (`TV1-dev`, from keychain) |
| `ANTHROPIC_API_KEY` | ✅ real (v2, from keychain `cohortbot-ANTHROPIC_API`, validated 200 against `/v1/models`). Earlier v1 was a bad 218-char value. |
| `GITHUB_TOKEN` | ✅ real (v2, from keychain `cohortbot-GITHUB_API`, authenticates as `jwolberg`, 5000/hr). v1 was a placeholder; a `cohortbot-GITHUB` attempt was 401/invalid. |
| `ADMIN_TOKEN` | ✅ real (keychain `cohortbot-ADMIN_TOKEN`), mapped; admin API validated (401 without / 200 with). |

To replace a placeholder later (no code change; needs a new revision to pick up):

```bash
printf %s '<real value>' | gcloud secrets versions add <SECRET> --data-file=- --project cohort-bot-1
# then redeploy (Step 3) or roll a new revision so :latest is remounted
```

## Keychain items (local, macOS `security`)

| Service name | Holds |
|---|---|
| `cohortbot-JAY_TEST_DISCORD_TOKEN` | Discord bot token (TV1-dev) |
| `cohortbot-ANTHROPIC_KEY` | Anthropic key (⚠️ suspect — see above) |
