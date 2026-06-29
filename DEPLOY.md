# Production deploy notes (Dokploy)

This document covers the **Redis + worker** rollout that activates the durable
background-task queue and the access-token denylist (security fix 3.4). The code
and `docker-compose.yml` are already complete; what follows is the production
configuration that must exist in Dokploy.

## The one switch that activates everything: `QUEUE_BACKEND=arq`

The API and worker default to `QUEUE_BACKEND=inline`. With `inline`:

- Background tasks (CV analysis, emails, notifications) run **in-process,
  fire-and-forget** — lost on restart or exception. This is the original bug
  the queue was built to fix.
- The token denylist is **in-memory per process** — not shared across API
  replicas and wiped on every restart.

Both the Arq job pool **and** the Redis-backed denylist are created only when
`queue_backend == "arq"` (see `app/main.py` lifespan). So `arq` is the single
value that turns on the durable worker *and* the shared denylist.

## Services required in production

| Service  | Status | Notes |
|----------|--------|-------|
| db       | exists | Postgres |
| minio    | exists | Object storage (CVs) |
| Backend  | exists | FastAPI API |
| Frontend | exists | Next.js |
| **redis**   | **ADD** | Task broker + denylist store |
| **worker**  | **ADD** | Runs `arq app.worker.WorkerSettings`; no HTTP port |

## Environment variables

Set on **both** Backend and Worker (the worker reads the same DB/MinIO/Gemini/
SMTP config):

| Variable        | Value                                   | Where            |
|-----------------|-----------------------------------------|------------------|
| `REDIS_URL`     | `redis://[:password@]<redis-host>:6379/0` | Backend + Worker |
| `QUEUE_BACKEND` | `arq`                                   | Backend (+ Worker, harmless) |
| `TRUST_PROXY_HEADERS` | `true`                            | Backend |

> `TRUST_PROXY_HEADERS=true` makes per-IP rate limits (and audit IPs) key on the
> real client instead of the Next.js proxy. The frontend proxy forwards the real
> IP in `X-Real-Client-IP`; the backend honours it only with this flag on. Set it
> only because the backend is unreachable directly (internal network) — never on a
> directly-exposed API, where a client could set the header itself.

> The worker only strictly needs `REDIS_URL` — it is always the Arq consumer and
> ignores `QUEUE_BACKEND`. The API needs `QUEUE_BACKEND=arq` to route to Redis.

Optional (only when scaling the **API** to more than one replica):

| Variable                 | Value                          | Why |
|--------------------------|--------------------------------|-----|
| `RATE_LIMIT_STORAGE_URI` | `redis://<redis-host>:6379/1`  | Shared rate-limit counters across replicas (default `memory://` is per-process) |

`REDIS_URL` must include the password if the Redis instance has one (Dokploy
managed Redis sets one by default). Both `arq` and the denylist client parse the
password from the DSN.

## Rollout order (matters)

Flipping the API to `arq` while no worker is consuming would silently queue
emails and CV analyses that never run. Do it in this order:

1. **Create Redis.** Wait until healthy. Copy its internal connection URL
   (with password).
2. **Create the Worker.** Same build as Backend (same repo/Dockerfile), start
   command overridden to `arq app.worker.WorkerSettings`. Set the full backend
   env block + `REDIS_URL`. No domain, no exposed port. Confirm it boots and
   logs that it connected to Redis and registered the task functions.

   > The image `ENTRYPOINT` is `entrypoint.prod.sh`, which dispatches on args:
   > with no command it runs Alembic migrations then uvicorn (the API); with a
   > command (the worker's `arq …`) it `exec`s that command directly and skips
   > migrations. So the worker never runs migrations — the Backend deploy does.
3. **Update the Backend.** Add `QUEUE_BACKEND=arq` and `REDIS_URL`, redeploy.
   From now on the API enqueues to Redis and the worker (already running)
   executes the jobs.

## Migrations and the worker

The worker opens its own DB session for tasks that touch the database. It must
start against a schema that is already migrated to head. Ensure Alembic
migrations run (on the Backend deploy) **before** the worker starts processing,
otherwise the first jobs fail and burn their retries (`max_tries=4`).

## Smoke test after rollout

1. Worker logs show a connection to Redis and the registered functions on boot.
2. Submit a new application → its `match_score` is populated shortly after
   (worker ran `analyze_application`), not only when the profile is opened.
3. Trigger a re-analysis on an already-scored application → it is **skipped**
   unless `force=true` (R2 idempotency).
4. Log in, then change the password → the previous access token returns
   **401 Token revoked** on the next call (R3 denylist).

## Task catalog (executed by the worker)

Registered in `app/worker.py` via the shared `_REGISTRY` (same callables the
inline queue runs):

- `analyze_application` (CV-vs-vacancy match; idempotent, accepts `force`)
- `notify_stage_change`, `notify_rejection`
- `parse_candidate_cv`
- `send_verification_email`, `send_account_exists_email`
- `send_slot_offer_email`, `create_teams_meeting`,
  `send_interview_invitation`, `notify_slot_confirmed`
