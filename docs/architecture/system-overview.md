# System Overview

NoteApp V1 is a local-first personal knowledge base with encrypted sync,
desktop packaging, a browser-based UI, and AI-assisted knowledge workflows.

## Components

| Component | Path | Responsibility |
| --- | --- | --- |
| Web UI | `apps/frontend/noteapp-web` | React/Vite workspace UI, settings, sync center, file explorer, AI views |
| Desktop shell | `apps/desktop/noteapp-desktop` | Electron packaging, bundled frontend, local process launch, signed installers |
| Desktop service and CLI | `clients/desktop` | Vault filesystem access, crypto provider selection, sync orchestration, local bridge contracts |
| Vault core | `packages/vault-core` | Manifest/filemap/tombstone logic, CAS commit preparation, pull/apply recovery, SQLite local state |
| AI core | `packages/ai-core` | AI compile and ask helpers used by desktop workflows |
| Protocol package | `packages/protocol` | JSON schemas, OpenAPI contract, and checked-in golden protocol fixtures |
| Sync backend | `apps/backend/noteapp-server` | FastAPI sync API, account/session boundary, CAS commits, blob capabilities, JSON/SQLite/S3-compatible storage profiles |
| E2E and acceptance tests | `tests/e2e`, `docs/testing` | Release gates, smoke coverage, stability runs, compatibility and chaos plans |

## Primary Sync Flow

1. The web UI reads and writes through the local bridge at
   `http://127.0.0.1:3187`.
2. The bridge invokes the desktop CLI/service with the configured vault root,
   vault id, device id, bearer token, and sync server URL.
3. The desktop service reads local workspace state and delegates manifest,
   filemap, tombstone, commit, and recovery logic to `vault-core`.
4. The configured crypto provider encrypts blob payloads before they leave the
   device. Production paths require an `e2ee-v1` vault key.
5. The sync backend accepts CAS commits, stores encrypted blobs, serves
   manifests by revision, and exposes short-lived upload/download capabilities.
6. Other devices pull the latest manifest, download required encrypted blobs,
   decrypt locally, and apply the plan through the journaled pull/apply path.

## Persisted State

| Location | Contents |
| --- | --- |
| Vault root | User notes and attachments |
| `.noteapp/filemap.json` | Stable file identity, canonical paths, conflict records |
| `.noteapp/tombstone-ledger.jsonl` | Local delete history used for sync convergence |
| `.noteapp/state.sqlite3` | Local vault state, journals, search/wiki metadata |
| `.noteapp/sync-apply-plan.json` | Pull/apply replay plan during interrupted applies |
| `.noteapp/staging/` | Temporary decrypted payload staging |
| `.noteapp/conflict-orphans/` | Preserved local conflict bytes when a tracked path cannot remain canonical |
| Backend JSON/SQLite state | Vault heads, manifests, commits, devices, sessions, blob metadata |
| Backend blob store | Encrypted blob bytes only |

## Deferred Production Work

The backend README records the remaining production gaps: external IdP/MFA
hardening, managed database provider operations, object storage operations,
tombstone GC background workers, and distributed CAS locking/alerts. Those items
remain tracked in `docs/develop/v1.0.43-follow-up-todo.md`.
