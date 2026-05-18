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
13. JSON-backed account sessions with access/refresh token rotation

State is stored locally under `.data/` by default:

```text
apps/backend/noteapp-server/.data/
```

Override it with:

```powershell
$env:NOTEAPP_SERVER_DATA_DIR='C:\tmp\noteapp-server-data'
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

## Test

From the repository root:

```powershell
python -m unittest apps\backend\noteapp-server\tests\test_sync_store.py
python tests\e2e\sync_server_smoke.py
```

## Auth Boundary

`POST /devices/register` and `POST /auth/login` create a local account session and return an access token, refresh token, and expiry timestamps. Vault-level APIs require `Authorization: Bearer <access_token>`; missing, invalid, expired, or revoked tokens are rejected before vault operations run.

`POST /auth/refresh` rotates both the access token and refresh token. `POST /auth/logout` revokes the current session. `DELETE /devices/{deviceId}` is limited to the current account and revokes the device session, refresh token, and outstanding blob capabilities for that device.

## Blob Boundary

`upload-init` and `download-init` return short-lived local capability URLs under:

```text
/_capabilities/blobs/{token}
```

This is not an object-storage integration. It is a local substitute that lets the existing `CapabilityBlobUploader` and `CapabilityBlobDownloader` exercise the same upload/download contract.

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

1. Account/session state is a JSON-backed dev profile; no durable database, password flow, external IdP, MFA, or account recovery
2. No real object storage
3. Tombstone GC is local-store only; no background worker, metrics, or production retention controls
4. No cross-process CAS lock beyond the single-process JSON store

The `V1043-M3-01` production backend architecture is frozen in:

```text
docs/develop/v1.0.43-production-backend-architecture.md
```

Future production work should treat this JSON store as a dev/test profile and move account, device, vault, revision, blob metadata, capability, tombstone GC, and `file_versions` state behind a durable repository layer.
