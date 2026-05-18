# e2e

End-to-end checks for the sync loop.

## Sync Server Smoke

`sync_server_smoke.py` starts the FastAPI sync server on a temporary local port and runs:

1. device registration
2. blob upload capability
3. blob upload
4. CAS commit
5. head lookup
6. manifest fetch
7. blob download capability
8. blob download
9. authenticated device revocation

Run from the repository root:

```powershell
python tests\e2e\sync_server_smoke.py
```

## Desktop Sync Smoke

`desktop_sync_smoke.py` starts the local FastAPI sync server and drives the real desktop service/runtime against it with two temporary vault roots:

1. register two desktop devices
2. initialize both local vaults
3. submit a detected local note from vault A
4. pull and apply that note into vault B
5. edit and submit the same note from vault B
6. pull and apply the update back into vault A

Run from the repository root with `PYTHONPATH` pointing at `packages/vault-core/src`:

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'
python tests\e2e\desktop_sync_smoke.py
```

## Desktop Sync Stability

`desktop_sync_stability.py` starts the local FastAPI sync server with the SQLite repository profile by default and runs:

1. single-file continuous commit and pull verification, defaulting to 1000 iterations
2. long-offline old-device delete regression, ensuring a stale clean device does not recreate a tombstoned file and removes the canonical file after reconnect

Run the full release stability gate:

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'
python tests\e2e\desktop_sync_stability.py --iterations 1000
```

Run a short local check:

```powershell
$env:PYTHONPATH='packages/vault-core/src;.'
python tests\e2e\desktop_sync_stability.py --iterations 5 --progress-every 1
```

## Sync Server Production Smoke

`sync_server_production_smoke.py` starts the FastAPI sync server with the SQLite repository profile and runs the production-backend regression gates:

1. unauthorized vault requests are rejected
2. dependency health reports SQLite migrations
3. invalid manifests return a contract error
4. concurrent CAS commits produce exactly one success and one conflict
5. device revocation invalidates protected APIs and stale blob capabilities
6. an S3/OSS-compatible object storage outage returns a diagnosable 503

Run from the repository root:

```powershell
python tests\e2e\sync_server_production_smoke.py
```

## Frontend Sync Snapshot Smoke

`frontend_sync_snapshot_smoke.py` starts the local FastAPI sync server, initializes a temporary desktop vault, then runs the frontend `sync:snapshot` and `settings:snapshot` bridge scripts to generate live sync-shell and settings JSON snapshots.

Run from the repository root:

```powershell
python tests\e2e\frontend_sync_snapshot_smoke.py
```

## Frontend Dist Smoke

`frontend_dist_smoke.py` builds the Vite app, serves `dist/`, verifies SPA fallback, and checks that the bundled sync page text is present.

Run from the repository root:

```powershell
python tests\e2e\frontend_dist_smoke.py
```

Optional port override:

```powershell
$env:NOTEAPP_SMOKE_PORT='8091'
python tests\e2e\sync_server_smoke.py
```

Prerequisites:

```powershell
cd apps\backend\noteapp-server
pip install -r requirements.txt
```
