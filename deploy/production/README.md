# NoteApp Production Deployment

This directory contains the deployable backend sync-server profile for
v1.0.43. It intentionally covers the server side only. Windows desktop signing
and installer publication remain an operations task in the signing environment.

## Scope

Deploys:

1. `apps/backend/noteapp-server` as a FastAPI service.
2. SQLite repository state on a persistent database volume.
3. S3/OSS-compatible encrypted blob storage through server-side capabilities.
4. Health, dependency diagnostics, metrics, tombstone GC worker, and CAS lock
   diagnostics.

Does not deploy:

1. Windows signed installers. Run the signed release lane separately.
2. A public browser-only sync app. The current web UI is packaged with the
   desktop shell and talks to the local bridge.
3. PostgreSQL multi-host active-active locking. The current production strategy
   is SQLite `BEGIN IMMEDIATE` with one shared writable database.

## Prerequisites

1. Docker Engine with Docker Compose v2.
2. HTTPS reverse proxy or load balancer in front of `127.0.0.1:8000`.
3. S3/OSS-compatible bucket with one-bucket read/write credentials.
4. Persistent storage for `/var/lib/noteapp-server/db`,
   `/var/lib/noteapp-server/runtime`, and `/var/backups/noteapp-server`.
5. Release commit that has passed the non-signing acceptance gate.

## Configure

From the repository root:

```bash
cp deploy/production/backend.env.example deploy/production/backend.env
```

Edit `deploy/production/backend.env` and replace every placeholder. Required
production boundaries:

1. `NOTEAPP_SERVER_ENV=production`
2. `NOTEAPP_SERVER_STORAGE=sqlite`
3. `NOTEAPP_SERVER_DATABASE_URL=sqlite:////var/lib/noteapp-server/db/noteapp-server.sqlite3`
4. `NOTEAPP_SERVER_DATA_DIR=/var/lib/noteapp-server/runtime`
5. `NOTEAPP_SERVER_BLOB_STORAGE=s3` or `oss` with HTTPS endpoint and operations
   policy values.
6. `NOTEAPP_SERVER_CAS_LOCK_STRATEGY=sqlite-immediate`
7. `NOTEAPP_SERVER_CORS_ORIGINS` set to the deployed app origin, or empty when
   only desktop clients call the sync server.

Keep `backend.env` in the deployment secret store or host-local protected
storage. It is ignored by Git.

## Start

From the repository root:

```bash
docker compose \
  --env-file deploy/production/backend.env \
  -f deploy/production/docker-compose.backend.yml \
  up -d --build
```

Set `NOTEAPP_BACKEND_ENV_FILE=backend.env.example` only for local compose
configuration dry-runs. Production should use `backend.env`.

The compose file binds the backend to `127.0.0.1:8000` by default. Put TLS,
request size limits, and access logs in the reverse proxy.

## Verify

Run the deployment checker:

```bash
python scripts/deploy/check_noteapp_server.py \
  --base-url http://127.0.0.1:8000 \
  --expect-repository sqlite \
  --expect-cas-strategy sqlite-immediate \
  --expect-blob-store s3
```

Also inspect:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/health/dependencies
curl -fsS http://127.0.0.1:8000/metrics
```

Expected deployment diagnostics:

1. Repository type is `sqlite` and schema version is at least `2`.
2. Blob store diagnostics are `ok`.
3. CAS strategy is `sqlite-immediate`.
4. Tombstone GC worker is enabled when configured.
5. Metrics include request counters, error rate, CAS counters, lock-wait
   counters, and object-storage failure counters.

## Backup

Create a SQLite backup from the running container:

```bash
docker compose \
  --env-file deploy/production/backend.env \
  -f deploy/production/docker-compose.backend.yml \
  exec noteapp-server \
  python scripts/backup_sqlite.py \
    --sqlite-db /var/lib/noteapp-server/db/noteapp-server.sqlite3 \
    --backup-dir /var/backups/noteapp-server
```

Object storage recovery relies on provider bucket versioning and lifecycle
retention. The bucket retention window must be at least
`NOTEAPP_SERVER_OBJECT_LIFECYCLE_RETENTION_DAYS`.

## Upgrade

1. Confirm `git status --short` is clean in the release workspace.
2. Run the non-signing gate:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ci\v1043-acceptance.ps1
```

3. Ask operations to run the signed Windows lane before public installer
   distribution:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\ci\v1043-acceptance.ps1 -ReleaseArtifacts
```

4. Back up SQLite.
5. Deploy the new backend image:

```bash
docker compose \
  --env-file deploy/production/backend.env \
  -f deploy/production/docker-compose.backend.yml \
  up -d --build
```

6. Run `scripts/deploy/check_noteapp_server.py`.
7. Watch `/metrics` and structured request logs for elevated `error_rate`,
   `object_storage_failures_total`, `commit_cas_rejections_total`,
   `state_write_conflicts_total`, and `cas_lock_wait_max_ms`.

## Rollback

Application rollback is a binary/image rollback. Destructive schema rollback is
not supported. If the new deployment is unhealthy:

1. Keep the SQLite database unless a tested restore is explicitly approved.
2. Roll back the container image tag.
3. Restart the service.
4. Run the deployment checker.
5. Restore SQLite from backup only during an approved recovery window.
