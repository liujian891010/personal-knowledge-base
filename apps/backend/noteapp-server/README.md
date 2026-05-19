# noteapp-server

Python FastAPI sync server for the NoteApp V1 sync loop.

## Current Scope

This is a local-development AG04 MVP. It implements the frozen sync API shape needed by the existing desktop client:

1. Device registration, account bootstrap, and device revocation
2. Vault head lookup
3. Manifest fetch by revision
4. CAS commit
5. Commit intent recovery lookup
6. Ack reporting
7. Blob check
8. Blob upload/download capability URLs
9. Resumable blob upload sessions for encrypted chunks
10. Ranged blob download capabilities for encrypted bytes
11. Vault device list / heartbeat state
12. Tombstone GC eligibility and audit logs for the local JSON store
13. Repository-backed account sessions with access/refresh token rotation
14. SQLite repository profile with schema migration and JSON-state import
15. Object-storage-backed encrypted blob persistence with local filesystem and S3/OSS-compatible profiles
16. Operational readiness endpoints, structured request logs, runtime metrics, and SQLite backup/restore scripts

State is stored locally as JSON under `.data/` by default:

```text
apps/backend/noteapp-server/.data/
```

Override it with:

```powershell
$env:NOTEAPP_SERVER_DATA_DIR='C:\tmp\noteapp-server-data'
```

Copy `.env.example` as the deployment baseline for database, object storage, token secret, CORS, and log-level settings.

Enable the SQLite repository profile with:

```powershell
$env:NOTEAPP_SERVER_STORAGE='sqlite'
$env:NOTEAPP_SERVER_SQLITE_PATH='C:\tmp\noteapp-server-data\noteapp-server.sqlite3'
```

`NOTEAPP_SERVER_DATABASE_URL=sqlite:///C:/tmp/noteapp-server-data/noteapp-server.sqlite3` is also accepted. The SQLite profile stores the canonical sync state plus projected metadata tables for users, devices, sessions, vaults, manifests, commits, blobs, capabilities, tombstone GC runs, and file versions.

Import an existing JSON state file into SQLite with:

```powershell
python apps\backend\noteapp-server\scripts\import_json_state_to_sqlite.py --json-state apps\backend\noteapp-server\.data\sync-state.json --sqlite-db apps\backend\noteapp-server\.data\noteapp-server.sqlite3
```

Blob payloads are stored under the local `.data/blobs` filesystem object store by default. Use a separate filesystem object-store root with:

```powershell
$env:NOTEAPP_SERVER_BLOB_STORAGE='filesystem'
$env:NOTEAPP_SERVER_OBJECT_STORAGE_DIR='C:\tmp\noteapp-object-store'
```

Use an S3/OSS-compatible bucket through the server-controlled streaming proxy with:

```powershell
$env:NOTEAPP_SERVER_BLOB_STORAGE='s3'
$env:NOTEAPP_SERVER_OBJECT_ENDPOINT='https://s3.example.com'
$env:NOTEAPP_SERVER_OBJECT_BUCKET='noteapp-vault-blobs'
$env:NOTEAPP_SERVER_OBJECT_REGION='us-east-1'
$env:NOTEAPP_SERVER_OBJECT_ACCESS_KEY_ID='...'
$env:NOTEAPP_SERVER_OBJECT_SECRET_ACCESS_KEY='...'
```

## Run Locally

```powershell
cd apps\backend\noteapp-server
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8000
```

Health check:

```text
http://127.0.0.1:8000/health
```

Dependency diagnostics:

```text
http://127.0.0.1:8000/health/dependencies
```

Runtime metrics:

```text
http://127.0.0.1:8000/metrics
```

## Test

From the repository root:

```powershell
python -m unittest apps\backend\noteapp-server\tests\test_sync_store.py
python tests\e2e\sync_server_smoke.py
python tests\e2e\sync_server_production_smoke.py
```

## Operations

Set `NOTEAPP_SERVER_LOG_LEVEL=INFO` to emit one structured JSON request log per HTTP request. Logs include `request_id`, `user_id`, `device_id`, `vault_id`, `error_code`, and `duration_ms`. Incoming `X-Request-Id` is propagated to the response; otherwise the server generates one.

`GET /health` stays intentionally small for load balancers. `GET /health/dependencies` checks process state, repository health, object storage health, and SQLite migration versions. Filesystem blob storage performs a short read/write/delete probe; S3/OSS-compatible storage reports configuration without a destructive bucket probe.

`GET /metrics` returns JSON counters for requests, error rate, commit conflicts, blob upload failures, capability expiry/revocation events, and tombstone GC deletion count.

Create a consistent SQLite backup with:

```powershell
python apps\backend\noteapp-server\scripts\backup_sqlite.py --sqlite-db apps\backend\noteapp-server\.data\noteapp-server.sqlite3 --backup-dir backups\noteapp-server
```

Restore into a fresh destination with:

```powershell
python apps\backend\noteapp-server\scripts\restore_sqlite.py --backup-path backups\noteapp-server\noteapp-server-YYYYMMDDTHHMMSSZ.sqlite3 --sqlite-db apps\backend\noteapp-server\.data\noteapp-server-restored.sqlite3
```

Use `--force` only during a planned recovery drill, because it replaces the destination database. Object storage backups should use bucket versioning plus lifecycle retention; destructive schema rollbacks are not supported, so migrations must remain forward compatible.

## Auth Boundary

`POST /auth/register` creates a password-backed account, stores only a salted
PBKDF2-SHA256 password hash, creates the first device session, and returns an
access token, refresh token, and expiry timestamps.

`POST /auth/login` verifies the password for password-backed accounts and creates
a new device session. For development/bootstrap compatibility, login without a
password still follows the legacy local device registration path.

`POST /devices/register` remains the local development and bootstrap device path.
It refuses password-backed accounts with `password_required`, so a password
account cannot be extended by silently registering another device.

Vault-level APIs require `Authorization: Bearer <access_token>`; missing,
invalid, expired, or revoked tokens are rejected before vault operations run.

`POST /auth/refresh` rotates both the access token and refresh token. `POST /auth/logout` revokes the current session. `DELETE /devices/{deviceId}` is limited to the current account and revokes the device session, refresh token, and outstanding blob capabilities for that device.

The production account policy and deferred external IdP/MFA/recovery decisions
are tracked in `docs/develop/v1.0.43-production-auth-boundary.md`.

## Blob Boundary

`upload-init` and `download-init` return short-lived server-controlled capability URLs under:

```text
/_capabilities/blobs/{token}
```

The capability endpoints validate server-side capability state before streaming encrypted bytes into or out of the configured blob store. They do not expose long-lived public object URLs or require clients to construct object keys.

`resumable-upload-init` and `resumable-upload-complete` add encrypted chunk upload recovery. Chunks are uploaded to:

```text
/_capabilities/blobs/resumable-upload/{session_id}
```

Chunk metadata is passed with `X-Noteapp-Chunk-Id`, `X-Noteapp-Chunk-Offset`, and `X-Noteapp-Chunk-Size`. The server only marks a blob as uploaded after all chunks are present and the joined encrypted payload matches `encrypted_size` and `encrypted_sha256`.

`resumable-download-init` issues ranged download capability URLs under:

```text
/_capabilities/blobs/resumable-download/{token}
```

The client requests each authorized range with `X-Noteapp-Range-Offset` and `X-Noteapp-Range-Size`. Whole-blob `download-init` remains available for legacy clients.

## Production Gaps

This MVP is intentionally not production-ready:

1. External IdP, built-in MFA, and self-service account recovery remain policy-defined/deferred; password auth and session rotation are implemented
2. SQLite is a local-file repository profile; managed DB migration rollout and hosted backup scheduling are still pending
3. S3/OSS mode uses a server streaming proxy; production credential rotation and bucket lifecycle automation are still pending
4. Tombstone GC has no background worker or production retention controls
5. CAS locking is implemented for the SQLite repository profile; distributed multi-node locking and operational alerts are still pending

The `V1043-M3-01` production backend architecture is frozen in:

```text
docs/develop/v1.0.43-production-backend-architecture.md
```

Future production work should keep JSON/local filesystem as dev/test profiles and harden the SQLite/S3-compatible repository path with operational migrations, monitoring, backup, distributed locking strategy, and recovery.
