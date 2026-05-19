# Module Boundaries

## Ownership

| Module | Owns | Does Not Own |
| --- | --- | --- |
| `apps/frontend/noteapp-web` | React views, adapters for snapshot JSON, browser-side settings/UI state | Direct vault filesystem access, encryption, sync protocol mutation |
| `apps/frontend/noteapp-web/scripts/sync-shell-bridge.mjs` | Loopback HTTP bridge, fixture refresh, CLI dispatch from browser actions | Long-lived sync state or protocol business rules |
| `apps/desktop/noteapp-desktop` | Electron shell, packaging, platform signing, bundled resource layout | Sync protocol rules or backend storage behavior |
| `clients/desktop` | Desktop CLI/service orchestration, workspace reads/writes, crypto provider selection, local recovery commands | Backend persistence and schema ownership |
| `packages/vault-core` | Deterministic sync domain logic, local vault state, commit/pull journals, manifest/filemap/tombstone convergence | HTTP server routing, UI presentation, platform packaging |
| `packages/ai-core` | AI compile/ask helpers and deterministic degraded-mode behavior | Sync state mutation and provider credential storage |
| `packages/protocol` | JSON schemas, OpenAPI contract, protocol golden fixtures | Runtime implementation logic |
| `apps/backend/noteapp-server` | API auth, CAS commit acceptance, manifests, file versions, blob capabilities, repository/object storage profiles | Plaintext content, desktop vault key material, UI-specific state |
| `tests/e2e` and `docs/testing` | Release gates, smoke orchestration, compatibility/chaos acceptance rules | Product runtime logic |

## Cross-Module Rules

1. Frontend views consume typed adapters and bridge endpoints. They do not infer
   vault internals from filesystem paths.
2. The local bridge may call desktop CLI commands, but it should not duplicate
   vault-core sync decisions.
3. Desktop service code owns all local filesystem mutation and must preserve
   journaled recovery boundaries around commit and pull/apply work.
4. Vault-core functions should stay deterministic and testable without Electron,
   Vite, or FastAPI process state.
5. Backend code validates auth, CAS, capabilities, and persistence. It never
   requires plaintext note bytes.
6. Protocol schema or fixture changes must be accompanied by parser/adapter test
   updates in the affected frontend, desktop, backend, or vault-core module.
7. Generated runtime artifacts, release output, secrets, and user vault data do
   not belong in the repository.

## Release-Critical Contracts

| Contract | Verification |
| --- | --- |
| Bridge snapshot and action payloads | `frontend:e2e-sync-snapshot`, frontend fixture validators |
| Desktop sync orchestration | `desktop-core:unit`, `desktop:e2e-sync`, stability run |
| Backend sync API | `backend:unit`, `backend:json-smoke`, `backend:sqlite-smoke`, `backend:production-smoke` |
| Protocol invariants | `vault-core:unit`, checked-in protocol fixtures, OpenAPI review |
| Packaging inputs | `desktop:build-frontend`, `desktop:dist-win-signed` when signing credentials exist |
