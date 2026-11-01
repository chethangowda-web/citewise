# Deploying Citewise

> **Status:** these steps use only what is verified locally (the production Compose file, the nginx build, health checks). The app has **not** been deployed to a cloud host yet, so treat the host-specific parts as a starting point and expect to adjust.

## Before you deploy: five minutes that matter

1. **Start from the production template.** `cp .env.production.example .env` (on the server, or paste it into your host's variable settings) and fill in every blank. Generate secrets with `openssl rand -hex 32`.
2. **Run the preflight:** `python scripts/preflight.py`. It prints one line per finding and exits non-zero on anything unsafe: accounts off, a weak or missing `JWT_SECRET`, the default database password, an insecure cookie, a `localhost` public address, no named administrator, a secret about to be committed. Warnings (open sign-up, no daily token limit, no SMTP) do not block you, but read them.
3. **Set `ADMIN_EMAIL` to your own address.** With sign-up open, whoever registers first on a fresh server would otherwise become the admin. With `ADMIN_EMAIL` set, only an account registered with exactly that address is made admin. Register it first thing, then (if you want) set `ALLOW_SIGNUP=false` once your people have accounts.
4. **Set daily token limits** (`GLOBAL_DAILY_TOKEN_LIMIT` below your provider's daily quota, `USER_DAILY_TOKEN_LIMIT` per person). A public demo on a free tier will otherwise be emptied by the first busy afternoon.
5. **Configure SMTP**, or accept that nobody receives password-reset emails and the link only appears in the server log.

After the first start: sign in as the admin, upload your policies, ask a question, and run `scripts/backup.sh` once so you know it works on the server.

## What gets deployed

Three containers from `docker-compose.yml`: `db` (Postgres + pgvector), `backend` (FastAPI) and `frontend` (nginx serving the React build and proxying `/api`). In the production file **only nginx is published**; the database and backend are reachable only on the internal network.

```bash
docker compose -f docker-compose.yml up -d --build
```

Do **not** use the auto-loaded `docker-compose.override.yml` in production. It publishes the backend and database for local development, and publishing the backend would let clients spoof `X-Forwarded-For` around nginx and dodge the rate limiter.

## Option A: a single VM (recommended)

Any small VM with Docker works (2 GB RAM is plenty).

1. Install Docker, then clone the repo.
2. `cp .env.example .env` and set:
   - `GEMINI_API_KEY` (embeddings) and `GROQ_API_KEY` if `LLM_PROVIDER=groq`
   - `POSTGRES_PASSWORD` to a long random value (**before the first start**; the password is baked into the data volume)
   - `AUTH_ENABLED=true` and `JWT_SECRET` (a long random value) for a public deployment, so uploads, deletes and history belong to signed-in users; set `ALLOW_SIGNUP=false` once your people have accounts
   - `COOKIE_SECURE=true` once HTTPS is in place, and `PUBLIC_URL=https://your.domain` so password-reset links point at the right place
   - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` so reset emails are really sent (without them the link is only written to the log)
   - `ADMIN_API_KEY` to a long random value if you want the audit ledger (with auth on it also lets you remove shared documents)
   - `RATE_LIMIT_PER_MINUTE` (see cost control below)
   - `WEB_PORT=8080` (or another port if you put a TLS proxy in front on 80/443)
3. `docker compose -f docker-compose.yml up -d --build`
4. `docker compose -f docker-compose.yml run --rm ingest` to load the policy documents.
5. Put HTTPS in front. The simplest option is Caddy on the same host, which obtains certificates automatically:

   ```caddyfile
   policies.example.com {
       reverse_proxy localhost:8080
   }
   ```

   Caddy sets `X-Forwarded-For` itself, but nginx in this stack overwrites it with the address it sees (Caddy's), so every visitor would share one rate-limit bucket. If you add a TLS proxy in front, either terminate TLS in the nginx container instead, or set `REAL_IP_DIRECTIVES` on the frontend container to trust the proxy's header (see Option B).

## Option B: a container platform (Railway, Render, Fly)

Deploy `backend` and `frontend` as two services from their Dockerfiles, plus a Postgres with the `pgvector` extension (`CREATE EXTENSION vector` is run by the app on startup, so the database user needs permission to create it; most managed Postgres offerings with pgvector support allow this).

- The `frontend` image proxies to `http://backend:8000` by default. Point it elsewhere with `BACKEND_URL` (for example `https://citewise-api.onrender.com` on Render's free plans, where services have no private network) and set `BACKEND_HOST_HEADER` to that host so the platform routes the request. The config is the template `frontend/nginx/default.conf.template`.
- `render.yaml` in the repository root sets all of this up on Render's free plans: services sleep when idle, so the first visit waits about a minute, and the free database expires after 30 days.
- Behind a platform's HTTPS proxy every visitor would otherwise share one rate-limit bucket. Set `REAL_IP_DIRECTIVES` on the frontend, for example `set_real_ip_from 0.0.0.0/0; real_ip_header X-Real-IP;`, but only when nothing except the platform's proxy can reach the frontend.
- Set the same environment variables as above on the backend, including `DATABASE_URL`, and `TRUSTED_PROXY=true` only if the platform's edge overwrites `X-Forwarded-For`.
- Expose only the frontend service publicly.

## Backups

Everything worth keeping (accounts, documents and their passages, conversations, the audit ledger) is in the Postgres database, so that is what to back up.

```bash
scripts/backup.sh                       # backups/citewise-YYYYMMDD-HHMMSS.dump, newest 14 kept (KEEP=30 to change)
scripts/restore.sh FILE.dump --verify   # prove a backup restores: loads it into a throwaway database and shows what is in it
scripts/restore.sh FILE.dump --replace --yes   # REPLACE the live database with it (stops the backend, restores, restarts it)
scripts/test_backup.sh                  # self-test of all of the above, touching nothing live
```

- **Schedule it.** cron: `15 3 * * * cd /path/to/policy-rag && scripts/backup.sh`. On Windows, a daily Task Scheduler task running `bash scripts/backup.sh` in the project folder.
- **A backup you have not restored is a hope, not a backup.** Run `--verify` on a recent file now and then, and after any upgrade.
- **Copy them off this machine.** A backup next to the database it protects does not survive losing the disk. The files hold password hashes and every conversation, so encrypt them wherever they go; the script creates them readable by you only.
- `--replace` loses everything written since the backup. Take a fresh backup first if anything in the database matters.
- Documents are stored as their extracted text and embeddings, not as the original files, so a restore brings back exactly what the assistant could search, but not the original PDFs.

## Database migrations

The backend applies pending migrations when it starts, so deploying a new version needs no manual database step. Two things to know:

- Take a backup first (`scripts/backup.sh`, above); a migration that fails halfway on a big table is the one case where you will want it.
- Run only one version of the app against a database at a time during an upgrade; the startup lock makes simultaneous starts safe, but an old server running against a new schema is not.

## Hardening checklist

- [ ] `POSTGRES_PASSWORD` changed from the default `rag`
- [ ] Only port 80/443 published; database and backend not reachable from outside
- [ ] HTTPS enabled
- [ ] `AUTH_ENABLED=true` with a strong `JWT_SECRET` (otherwise anyone can upload and delete shared documents)
- [ ] `COOKIE_SECURE=true` and HTTPS in front, so the session cookie is never sent over plain HTTP
- [ ] SMTP configured (a reset link written to the log is a credential: anyone who can read the log can take over an account)
- [ ] `ADMIN_API_KEY` set (or left empty to disable the admin routes entirely)
- [ ] `.env` is not committed and `.env.example` contains no real keys
- [ ] Backups for the `pgdata` volume (the audit log and documents live there)
- [ ] Alerting on 5xx and on the `llm_unavailable` log line (it carries the provider's real error, such as a quota hit)

## Cost control for a public demo

Every answered question spends LLM tokens (roughly 1,000 per answer in testing), and free-tier daily quotas are small (Groq's free tier allowed 200,000 tokens per day; Gemini's ran out after a handful of requests).

- Keep `RATE_LIMIT_PER_MINUTE` low (5 to 10) for a public deployment.
- Blocked and refused requests cost nothing, because they never reach the model.
- Watch spend in the **Audit** tab (tokens and estimated cost) or `GET /api/admin/stats`.
- `API_KEY` cannot protect a browser UI, since the key would have to ship to the browser. For a private demo, put HTTP basic auth in front of nginx instead.

## Rollback and upgrades

- The schema upgrades itself on startup with forward-only `ADD COLUMN IF NOT EXISTS` statements. Take a database backup before upgrading.
- To roll back, redeploy the previous image tag; newer columns are ignored by older code.
