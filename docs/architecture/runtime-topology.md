# Runtime Topology

## Local Desktop Development

| Runtime | Default | Notes |
| --- | --- | --- |
| Frontend dev server | `http://127.0.0.1:3000/` | `npm run dev` from `apps/frontend/noteapp-web` |
| Local sync bridge | `http://127.0.0.1:3187/` | `npm run sync:bridge`; rejects non-loopback hosts unless explicitly allowed |
| Backend sync server | `http://127.0.0.1:8000/` | `uvicorn main:app` from `apps/backend/noteapp-server` |
| Electron shell | local Electron process | `npm run dev` from `apps/desktop/noteapp-desktop` |
| Vault root | user configured | Contains notes plus `.noteapp/` local runtime state |

The browser UI never reads the vault filesystem directly. It calls the local
sync bridge, which dispatches CLI/service commands and refreshes JSON snapshots
under the frontend fixture contract.

## Packaged Desktop Runtime

The Electron package includes:

1. `main.mjs`, `preload.cjs`, and desktop package scripts.
2. The built frontend bundle from `apps/frontend/noteapp-web/dist`.
3. The frontend bridge scripts required by the shell.
4. `clients/desktop`, `packages/vault-core/src`, and `packages/ai-core/src`.

`clients/mobile` is not bundled for v1.0.43.

## Backend Runtime Profiles

| Profile | Storage | Use |
| --- | --- | --- |
| JSON | `.data/sync-state.json` | Local development and simple smoke runs |
| SQLite | `NOTEAPP_SERVER_SQLITE_PATH` or `NOTEAPP_SERVER_DATABASE_URL` | Repository-backed dev/test and production-smoke profile |
| Filesystem blob store | `.data/blobs` or `NOTEAPP_SERVER_OBJECT_STORAGE_DIR` | Local encrypted blob persistence |
| S3-compatible blob store | `NOTEAPP_SERVER_OBJECT_*` | Production-oriented encrypted blob storage via server-controlled streaming |

The backend exposes `GET /health`, `GET /health/dependencies`, and
`GET /metrics` for readiness, dependency checks, and operational counters.

## Security Boundaries

1. Browser-to-bridge traffic is loopback by default and constrained by CORS.
2. Vault APIs require `Authorization: Bearer <access_token>`.
3. Blob upload/download URLs are short-lived server-side capabilities.
4. The backend stores encrypted blob bytes and does not receive plaintext note
   content.
5. Code signing credentials are provided through environment variables only and
   must never be committed.

## Key Environment Variables

| Variable | Owner | Purpose |
| --- | --- | --- |
| `VITE_NOTEAPP_SYNC_BRIDGE_URL` | Frontend | Override local bridge URL |
| `NOTEAPP_SYNC_BASE_URL` | Desktop service | Sync backend base URL |
| `NOTEAPP_VAULT_ROOT` | Desktop service | Local vault path |
| `NOTEAPP_VAULT_ID` | Desktop service | Vault identity |
| `NOTEAPP_DEVICE_ID` | Desktop service | Device identity |
| `NOTEAPP_BEARER_TOKEN` | Desktop service | Backend API token |
| `NOTEAPP_SERVER_STORAGE` | Backend | Select JSON or SQLite repository profile |
| `NOTEAPP_SERVER_BLOB_STORAGE` | Backend | Select filesystem or S3-compatible blob storage |
| `CSC_LINK` / `CSC_KEY_PASSWORD` | Desktop release | Windows Authenticode signing inputs |
