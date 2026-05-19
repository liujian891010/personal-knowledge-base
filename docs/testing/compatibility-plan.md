# Compatibility Plan

Compatibility is validated through repeatable release gates rather than broad
platform claims. v1.0.43 focuses on the desktop/web/backend path.

## Dimensions

| Dimension | Current Coverage | Regression Signal |
| --- | --- | --- |
| Frontend runtime | TypeScript lint, Vite production build, entry-policy smoke, dist smoke | Build failure, route entry drift, broken SPA fallback |
| Local bridge contract | Frontend sync snapshot smoke and fixture validators | Missing endpoint, invalid snapshot/action/settings/workspace payload |
| Desktop Python service | `clients.desktop.tests.*` and desktop sync smoke | CLI contract drift, local state regression, recovery failure |
| Vault-core domain logic | `packages/vault-core/tests/*` | Manifest/filemap/tombstone/CAS/recovery invariant regression |
| Backend JSON profile | Backend unit tests and JSON smoke | Local development sync regression |
| Backend SQLite profile | SQLite smoke and production smoke | Migration, repository, session, capability, CAS, or backup regression |
| Blob storage profile | Production smoke plus backend unit coverage | Capability expiry/revocation or encrypted blob storage failure |
| Desktop packaging input | `desktop:build-frontend` and signed artifact lane | Missing frontend bundle or unsigned/invalid installer |

## Supported Release Claims

1. Windows desktop public distribution requires the signed artifact lane.
2. macOS packaging scripts exist, but Windows signing remains the public release
   blocker tracked by POST-RC-01.
3. The mobile client is outside the v1.0.43 acceptance scope.
4. JSON and local filesystem storage remain dev/test profiles.
5. SQLite plus filesystem or S3-compatible blob storage are the hardened backend
   profiles under current production-smoke coverage.

## Adding a Compatibility Claim

Before adding a new supported OS, runtime, storage backend, or browser claim:

1. Add the environment to this table.
2. Add an automated smoke or an explicit manual RC checklist item.
3. Record the exact command and environment in the RC report.
4. Ensure the claim does not depend on private endpoints or developer-local
   credentials.
