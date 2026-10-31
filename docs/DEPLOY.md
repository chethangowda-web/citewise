# Deploying Citewise

> **Status:** these steps use only what is verified locally (the production Compose file, the nginx build, health checks). The app has **not** been deployed to a cloud host yet, so treat the host-specific parts as a starting point and expect to adjust.

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

   Caddy sets `X-Forwarded-For` itself, but nginx in this stack overwrites it with the address it sees (Caddy's), so every visitor would share one rate-limit bucket. If you add a TLS proxy in front, either terminate TLS in the nginx container instead, or change `default.conf` to trust the upstream proxy's header with `real_ip_header` and `set_real_ip_from`.

## Option B: a container platform (Railway, Render, Fly)

Deploy `backend` and `frontend` as two services from their Dockerfiles, plus a Postgres with the `pgvector` extension (`CREATE EXTENSION vector` is run by the app on startup, so the database user needs permission to create it; most managed Postgres offerings with pgvector support allow this).

- The `frontend` image proxies to the hostname `backend` on port 8000. Name the backend service `backend` (or edit `frontend/nginx/default.conf`).
- Set the same environment variables as above on the backend, including `DATABASE_URL`, and `TRUSTED_PROXY=true` only if the platform's edge overwrites `X-Forwarded-For`.
- Expose only the frontend service publicly.

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
